"""Tiger Data / TimescaleDB hypertable and continuous aggregate access."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from backend.config import settings

log = logging.getLogger("gridpulse.tiger")


def _already(exc: Exception) -> bool:
    message = str(exc).lower()
    return "already" in message or "duplicate" in message


class TigerDB:
    """psycopg pool. Callers use asyncio.to_thread so the event loop stays free."""

    def __init__(self) -> None:
        self._pool: ConnectionPool | None = None
        self._writer: psycopg.Connection | None = None
        self._lock = threading.Lock()
        self._writer_lock = threading.Lock()

    def _ensure_pool(self) -> ConnectionPool:
        if self._pool is not None:
            return self._pool
        with self._lock:
            if self._pool is None:
                pool = ConnectionPool(
                    conninfo=settings.tiger_url,
                    min_size=2,
                    max_size=6,
                    timeout=12,
                    max_lifetime=600,
                    max_idle=120,
                    reconnect_timeout=5,
                    kwargs={
                        "autocommit": True,
                        "row_factory": dict_row,
                        "connect_timeout": 10,
                        "options": "-c statement_timeout=8000",
                    },
                    check=ConnectionPool.check_connection,
                    open=False,
                )
                pool.open(wait=True, timeout=20)
                self._pool = pool
            return self._pool

    def _writer_conn(self) -> psycopg.Connection:
        """Inserts use their own connection so a slow read cannot stall the 1 Hz writer."""
        if self._writer is not None and not self._writer.closed:
            return self._writer
        self._writer = psycopg.connect(
            settings.tiger_url,
            autocommit=True,
            row_factory=dict_row,
            connect_timeout=10,
        )
        return self._writer

    def _drop_writer(self) -> None:
        if self._writer is not None and not self._writer.closed:
            self._writer.close()
        self._writer = None

    def close(self) -> None:
        with self._writer_lock:
            self._drop_writer()
        with self._lock:
            if self._pool is not None:
                self._pool.close()
                self._pool = None

    def fetch(self, sql: str, params: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        last_exc: Exception | None = None
        for _attempt in range(2):
            try:
                with self._ensure_pool().connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(sql, params)
                        if cur.description is None:
                            return []
                        return list(cur.fetchall())
            except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
                last_exc = exc
                log.warning("tiger query failed, reconnecting: %s", exc)
        assert last_exc is not None
        raise last_exc

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        self.fetch(sql, params)

    def executemany(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        if not rows:
            return
        last_exc: Exception | None = None
        for _attempt in range(2):
            try:
                with self._writer_lock:
                    conn = self._writer_conn()
                    with conn.cursor() as cur:
                        cur.executemany(sql, rows)
                return
            except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
                last_exc = exc
                log.warning("tiger insert failed, reconnecting writer: %s", exc)
                with self._writer_lock:
                    self._drop_writer()
        assert last_exc is not None
        raise last_exc

    def setup_schema(self) -> None:
        """Create the hypertable, compression policy, and 1-minute continuous aggregate."""
        try:
            self.execute("CREATE EXTENSION IF NOT EXISTS timescaledb;")
        except Exception as exc:
            log.info("timescaledb extension note: %s", exc)

        self.execute(
            """
            CREATE TABLE IF NOT EXISTS energy_telemetry (
                time TIMESTAMPTZ NOT NULL,
                home_id TEXT NOT NULL,
                watts DOUBLE PRECISION NOT NULL,
                grid_carbon_gco2_kwh DOUBLE PRECISION NOT NULL,
                is_peak_event BOOLEAN DEFAULT FALSE
            );
            """
        )
        self.execute(
            "SELECT create_hypertable('energy_telemetry', 'time', if_not_exists => TRUE);"
        )
        self.execute(
            """
            CREATE INDEX IF NOT EXISTS energy_telemetry_home_time_idx
            ON energy_telemetry (home_id, time DESC);
            """
        )

        try:
            self.execute(
                """
                ALTER TABLE energy_telemetry SET (
                    timescaledb.compress = true,
                    timescaledb.compress_segmentby = 'home_id',
                    timescaledb.compress_orderby = 'time DESC'
                );
                """
            )
        except Exception as exc:
            if not _already(exc):
                self.execute(
                    "ALTER TABLE energy_telemetry SET (timescaledb.compress = true);"
                )

        try:
            self.execute(
                """
                SELECT add_compression_policy(
                    'energy_telemetry',
                    INTERVAL '7 days',
                    if_not_exists => TRUE
                );
                """
            )
        except Exception as exc:
            if not _already(exc):
                try:
                    self.execute(
                        """
                        SELECT add_compression_policy(
                            'energy_telemetry',
                            compress_after => INTERVAL '7 days',
                            if_not_exists => TRUE
                        );
                        """
                    )
                except Exception as inner:
                    if not _already(inner):
                        raise

        existing = self.fetch(
            """
            SELECT view_name
            FROM timescaledb_information.continuous_aggregates
            WHERE view_name = 'telemetry_1min_cagg'
            """
        )
        if not existing:
            self.execute(
                """
                CREATE MATERIALIZED VIEW telemetry_1min_cagg
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket('1 minute', time) AS bucket,
                    home_id,
                    AVG(watts) AS avg_watts,
                    AVG(grid_carbon_gco2_kwh) AS avg_carbon,
                    SUM(watts) / 60000.0 AS kwh_used
                FROM energy_telemetry
                GROUP BY bucket, home_id
                WITH NO DATA;
                """
            )

        try:
            self.execute(
                """
                ALTER MATERIALIZED VIEW telemetry_1min_cagg
                SET (timescaledb.materialized_only = false);
                """
            )
        except Exception as exc:
            if not _already(exc):
                log.warning("could not enable real-time aggregation: %s", exc)

        try:
            self.execute(
                """
                SELECT add_continuous_aggregate_policy(
                    'telemetry_1min_cagg',
                    start_offset => INTERVAL '1 day',
                    end_offset => INTERVAL '1 minute',
                    schedule_interval => INTERVAL '1 minute',
                    if_not_exists => TRUE
                );
                """
            )
        except Exception as exc:
            if not _already(exc):
                raise

        hypertables = self.fetch(
            """
            SELECT hypertable_name
            FROM timescaledb_information.hypertables
            WHERE hypertable_name = 'energy_telemetry'
            """
        )
        if not hypertables:
            raise RuntimeError("energy_telemetry was not created as a hypertable")

    def insert_readings(self, rows: list[tuple[Any, ...]]) -> None:
        self.executemany(
            """
            INSERT INTO energy_telemetry
                (time, home_id, watts, grid_carbon_gco2_kwh, is_peak_event)
            VALUES (%s, %s, %s, %s, %s)
            """,
            rows,
        )

    def refresh_recent_cagg(self) -> None:
        try:
            self.execute(
                """
                CALL refresh_continuous_aggregate(
                    'telemetry_1min_cagg',
                    NOW() - INTERVAL '1 day',
                    NOW() - INTERVAL '1 minute'
                );
                """
            )
        except Exception as exc:
            log.warning("continuous aggregate refresh skipped: %s", exc)

    def recent_raw(self, home_id: str, seconds: int = 180) -> list[dict[str, Any]]:
        return self.fetch(
            """
            SELECT time, home_id, watts, grid_carbon_gco2_kwh, is_peak_event
            FROM energy_telemetry
            WHERE home_id = %s AND time > NOW() - (%s * INTERVAL '1 second')
            ORDER BY time ASC
            """,
            (home_id, seconds),
        )

    def recent_cagg(self, home_id: str, minutes: int = 30) -> list[dict[str, Any]]:
        return self.fetch(
            """
            SELECT bucket, home_id, avg_watts, avg_carbon, kwh_used
            FROM telemetry_1min_cagg
            WHERE home_id = %s AND bucket > NOW() - (%s * INTERVAL '1 minute')
            ORDER BY bucket ASC
            """,
            (home_id, minutes),
        )

    def latest_by_home(self) -> list[dict[str, Any]]:
        return self.fetch(
            """
            SELECT DISTINCT ON (home_id)
                home_id, time, watts, grid_carbon_gco2_kwh, is_peak_event
            FROM energy_telemetry
            WHERE time > NOW() - INTERVAL '5 minutes'
            ORDER BY home_id, time DESC
            """
        )

    def co2_avoided_grams(self, home_id: str, baseline_watts: float) -> float:
        rows = self.fetch(
            """
            SELECT COALESCE(SUM(
                GREATEST(0, %s - watts) / 1000.0
                * (1.0 / 3600.0)
                * grid_carbon_gco2_kwh
            ), 0) AS grams
            FROM energy_telemetry
            WHERE home_id = %s
              AND is_peak_event = TRUE
              AND watts < %s
            """,
            (baseline_watts, home_id, baseline_watts),
        )
        if not rows:
            return 0.0
        return float(rows[0]["grams"] or 0)

    def aggregated_for_home(self, home_id: str) -> list[dict[str, Any]]:
        return self.fetch(
            """
            SELECT bucket, home_id, avg_watts, avg_carbon, kwh_used
            FROM telemetry_1min_cagg
            WHERE home_id = %s
            ORDER BY bucket DESC
            LIMIT 10
            """,
            (home_id,),
        )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def isoformat(value: Any) -> Any:
    if isinstance(value, datetime):
        return as_utc(value).isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    return value


tiger = TigerDB()
