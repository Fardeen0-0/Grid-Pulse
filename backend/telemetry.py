"""One-second household telemetry and the dirty-grid demo state machine."""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.config import HOMES, settings
from backend.mongo import get_user, latest_receipt, save_receipt
from backend.solana_payout import transfer_sol
from backend.tiger import isoformat, tiger

log = logging.getLogger("gridpulse.telemetry")

_payout_lock = asyncio.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GridState:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.carbon = settings.clean_carbon
        self.demo_watts = settings.baseline_watts
        self.peak_active = False
        self.load_reduced = False
        self.spike_started_at: datetime | None = None
        self.reduced_at: datetime | None = None
        self.pending_event: dict[str, Any] | None = None
        self.last_receipt: dict[str, Any] | None = None

    def snapshot(self) -> dict[str, Any]:
        drop = 0.0
        if self.load_reduced:
            drop = (settings.baseline_watts - settings.reduced_watts) / settings.baseline_watts * 100
        return {
            "watts": self.demo_watts,
            "grid_carbon_gco2_kwh": self.carbon,
            "peak_active": self.peak_active,
            "load_reduced": self.load_reduced,
            "baseline_watts": settings.baseline_watts,
            "reduced_watts": settings.reduced_watts,
            "drop_pct": drop,
            "spike_started_at": self.spike_started_at.isoformat() if self.spike_started_at else None,
        }


state = GridState()


def _wave(home_id: str, moment: datetime, amplitude: float) -> float:
    phase = sum(ord(char) for char in home_id) / 40.0
    return math.sin(moment.timestamp() / 6.5 + phase) * amplitude


def build_rows(moment: datetime, carbon: float, demo_watts: float, peak: bool) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for home in HOMES:
        home_id = str(home["home_id"])
        if home_id == settings.demo_home_id:
            watts = demo_watts + _wave(home_id, moment, 18.0)
        else:
            watts = float(home["baseline_watts"]) + _wave(home_id, moment, 35.0)
        carbon_sample = carbon + _wave(home_id, moment, 1.5)
        rows.append((moment, home_id, max(40.0, watts), max(1.0, carbon_sample), peak))
    return rows


async def write_rows(rows: list[tuple[Any, ...]]) -> None:
    await asyncio.to_thread(tiger.insert_readings, rows)


async def backfill(seconds: int = 90) -> None:
    async with state.lock:
        carbon = state.carbon
        demo_watts = state.demo_watts
        peak = state.peak_active
    moment = _now()
    rows: list[tuple[Any, ...]] = []
    for age in range(seconds, 0, -1):
        rows.extend(build_rows(moment - timedelta(seconds=age), carbon, demo_watts, peak))
    await write_rows(rows)
    await asyncio.to_thread(tiger.refresh_recent_cagg)


async def insert_once() -> None:
    async with state.lock:
        rows = build_rows(_now(), state.carbon, state.demo_watts, state.peak_active)
    await write_rows(rows)


async def run_telemetry(stop: asyncio.Event) -> None:
    try:
        await backfill()
        log.info("backfilled %s homes of telemetry", len(HOMES))
    except Exception:
        log.exception("telemetry backfill failed")
    failures = 0
    while not stop.is_set():
        started = asyncio.get_event_loop().time()
        try:
            await insert_once()
            failures = 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failures += 1
            if failures == 1 or failures % 15 == 0:
                log.warning("telemetry insert failed: %s", exc)
        elapsed = asyncio.get_event_loop().time() - started
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0.05, 1.0 - elapsed))
            return
        except asyncio.TimeoutError:
            continue


def _public_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    public: list[dict[str, Any]] = []
    for row in rows:
        public.append({key: isoformat(value) for key, value in row.items()})
    return public


def _names() -> dict[str, str]:
    return {str(home["home_id"]): str(home["display_name"]) for home in HOMES}


# The 1-minute rollup and the CO2 sum do not change every second. Caching them
# keeps the raw-reading query, which drives the clock, off the slow path.
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {
    "cagg_at": 0.0,
    "cagg": [],
    "co2_at": 0.0,
    "co2": 0.0,
    "refreshing": False,
}


def _refresh_heavy(home_id: str) -> None:
    try:
        rows = tiger.recent_cagg(home_id, 30)
        grams = tiger.co2_avoided_grams(home_id, settings.baseline_watts)
    except Exception:
        log.warning("slow telemetry aggregate failed; the live clock keeps the last series")
        rows = None
        grams = None
    with _cache_lock:
        if rows is not None:
            _cache["cagg"] = rows
            _cache["cagg_at"] = time.monotonic()
        if grams is not None:
            _cache["co2"] = grams
            _cache["co2_at"] = time.monotonic()
        _cache["refreshing"] = False


def _schedule_heavy(home_id: str) -> tuple[list[dict[str, Any]], float]:
    now = time.monotonic()
    with _cache_lock:
        cagg = list(_cache["cagg"])
        grams = float(_cache["co2"])
        stale = now - float(_cache["cagg_at"]) > 15 or now - float(_cache["co2_at"]) > 10
        if stale and not _cache["refreshing"]:
            _cache["refreshing"] = True
            asyncio.get_running_loop().create_task(asyncio.to_thread(_refresh_heavy, home_id))
    return cagg, grams


async def live_payload() -> dict[str, Any]:
    home_id = settings.demo_home_id
    raw, latest = await asyncio.gather(
        asyncio.to_thread(tiger.recent_raw, home_id, 180),
        asyncio.to_thread(tiger.latest_by_home),
    )
    cagg, avoided = _schedule_heavy(home_id)
    async with state.lock:
        snap = state.snapshot()
        receipt = state.last_receipt
    names = _names()
    peers = []
    for row in latest:
        peer_id = str(row["home_id"])
        peers.append(
            {
                "home_id": peer_id,
                "display_name": names.get(peer_id, peer_id),
                "watts": row["watts"],
                "grid_carbon_gco2_kwh": row["grid_carbon_gco2_kwh"],
                "time": isoformat(row["time"]),
            }
        )
    if receipt is None:
        try:
            receipt = await latest_receipt(home_id)
        except Exception:
            receipt = None
        if receipt is not None:
            async with state.lock:
                state.last_receipt = receipt
    latest_stamp = max((row["time"] for row in raw), default=None)
    return {
        "home_id": home_id,
        "server_time": _now().isoformat(),
        "latest_timestamp": isoformat(latest_stamp) if latest_stamp is not None else None,
        "co2_avoided_grams": avoided,
        "raw_points": _public_rows(raw),
        "cagg": _public_rows(cagg),
        "peers": peers,
        "latest_receipt": receipt,
        **snap,
    }


async def trigger_spike() -> dict[str, Any]:
    async with state.lock:
        state.peak_active = True
        state.carbon = settings.dirty_carbon
        state.demo_watts = settings.baseline_watts
        state.load_reduced = False
        state.pending_event = None
        state.spike_started_at = _now()
        rows = build_rows(state.spike_started_at, state.carbon, state.demo_watts, True)
        snap = state.snapshot()
    await write_rows(rows)
    snap["server_time"] = _now().isoformat()
    snap["message"] = "Regional carbon jumped to 680 gCO2/kWh. Peak alert is active."
    return snap


async def reduce_load() -> dict[str, Any]:
    """Drop the demo home to 850 W, score the avoided CO2, and pay 0.001 SOL."""
    async with _payout_lock:
        return await _reduce_load_once()


async def _reduce_load_once() -> dict[str, Any]:
    write_now = False
    rows: list[tuple[Any, ...]] = []
    async with state.lock:
        if state.load_reduced and state.last_receipt is not None:
            return state.last_receipt
        now = _now()
        if state.spike_started_at is None or not state.peak_active:
            state.peak_active = True
            state.carbon = settings.dirty_carbon
            state.spike_started_at = now - timedelta(seconds=60)
        if not state.load_reduced:
            elapsed = max((now - state.spike_started_at).total_seconds(), 60.0)
            watts_reduced = settings.baseline_watts - settings.reduced_watts
            grams = (watts_reduced / 1000.0) * (elapsed / 3600.0) * state.carbon
            state.demo_watts = settings.reduced_watts
            state.load_reduced = True
            state.reduced_at = now
            state.pending_event = {
                "window_seconds": elapsed,
                "co2_avoided_grams": grams,
                "drop_pct": watts_reduced / settings.baseline_watts * 100.0,
                "carbon_gco2_kwh": state.carbon,
            }
            rows = build_rows(now, state.carbon, state.demo_watts, True)
            write_now = True
        pending = dict(state.pending_event or {})
    if write_now:
        await write_rows(rows)
    try:
        user = await get_user(settings.demo_home_id)
        if user is None or not user.get("solana_wallet"):
            raise RuntimeError("demo user has no Solana wallet")
        payout = await transfer_sol(str(user["solana_wallet"]), settings.payout_lamports)
    except Exception:
        async with state.lock:
            state.demo_watts = settings.baseline_watts
            state.load_reduced = False
            state.reduced_at = None
            state.pending_event = None
        raise
    receipt = {
        "home_id": settings.demo_home_id,
        "peak_event": True,
        "baseline_watts": settings.baseline_watts,
        "reduced_watts": settings.reduced_watts,
        "drop_pct": float(pending["drop_pct"]),
        "carbon_gco2_kwh": float(pending["carbon_gco2_kwh"]),
        "window_seconds": float(pending["window_seconds"]),
        "co2_avoided_grams": float(pending["co2_avoided_grams"]),
        "sol_amount": float(payout["sol_amount"]),
        "signature": payout["signature"],
        "solscan_url": payout["solscan_url"],
        "from_wallet": payout["from_wallet"],
        "to_wallet": payout["to_wallet"],
        "cluster": "devnet",
    }
    try:
        stored = await save_receipt(receipt)
    except Exception:
        log.exception("payout landed on Devnet but the Atlas receipt was not stored")
        stored = receipt
    async with state.lock:
        state.last_receipt = stored
        state.pending_event = None
    return stored
