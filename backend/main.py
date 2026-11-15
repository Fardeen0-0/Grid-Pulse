"""GridPulse HTTP API."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from backend.config import settings
from backend.gemini_bill import analyze_image, analyze_sample
from backend.mongo import auth_failure, close as close_mongo
from backend.mongo import get_user, latest_receipt, leaderboard, setup as setup_mongo
from backend.sample_bill import ensure_sample_bill
from backend.solana_payout import ensure_funded, load_or_create_keypair
from backend.telemetry import live_payload, reduce_load, run_telemetry, trigger_spike
from backend.tiger import tiger

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("gridpulse")

_telemetry_stop = asyncio.Event()
_mongo_task: asyncio.Task[None] | None = None


async def _keep_mongo(stop: asyncio.Event) -> None:
    delay = 5
    while not stop.is_set():
        try:
            await setup_mongo(attempts=1, timeout_ms=8000)
            log.info("MongoDB Atlas connected")
            return
        except Exception as exc:
            if auth_failure(exc):
                log.error("MongoDB Atlas rejected the database user; will reload .env and retry")
            else:
                log.warning("MongoDB Atlas is unreachable (%s); retrying", exc.__class__.__name__)
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                delay = min(delay * 2, 30)


def _safe_detail(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    if settings.gemini_api_key and settings.gemini_api_key in text:
        text = text.replace(settings.gemini_api_key, "[key]")
    lowered = text.lower()
    if any(token in lowered for token in ("mongodb", "postgres://", "tsdb", "password", "private key")):
        return "A sponsor service request failed. Check the server logs."
    return text[:400]


async def _prepare_treasury() -> None:
    keypair = load_or_create_keypair()
    try:
        await ensure_funded(keypair, settings.payout_lamports + 20_000)
    except Exception:
        log.exception("Devnet treasury is not funded yet; the shift-load payout will retry")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _mongo_task
    log.info("preparing Tiger Data, MongoDB Atlas, and the Devnet treasury")
    await asyncio.to_thread(tiger.setup_schema)
    _telemetry_stop.clear()
    try:
        await setup_mongo(attempts=1, timeout_ms=8000)
        log.info("MongoDB Atlas connected")
    except Exception as exc:
        if auth_failure(exc):
            log.error("MongoDB Atlas rejected the database user; telemetry will start and the driver will keep retrying")
        else:
            log.warning("MongoDB Atlas is not reachable yet; telemetry will start and the driver will keep retrying")
        _mongo_task = asyncio.create_task(_keep_mongo(_telemetry_stop))
    await _prepare_treasury()
    task = asyncio.create_task(run_telemetry(_telemetry_stop))
    log.info("GridPulse API ready")
    try:
        yield
    finally:
        _telemetry_stop.set()
        task.cancel()
        if _mongo_task is not None:
            _mongo_task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        if _mongo_task is not None:
            with suppress(asyncio.CancelledError):
                await _mongo_task
        await asyncio.to_thread(tiger.close)
        close_mongo()


app = FastAPI(title="GridPulse", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "gridpulse", "health": "/api/health", "docs": "/docs"}


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {"ok": True, "service": "gridpulse", "domain": settings.public_domain}


@app.get("/api/telemetry/live")
async def telemetry_live() -> dict[str, object]:
    try:
        return await live_payload()
    except Exception as exc:
        log.exception("live telemetry failed")
        raise HTTPException(status_code=503, detail=_safe_detail(exc)) from exc


@app.get("/api/leaderboard")
async def get_leaderboard() -> dict[str, object]:
    try:
        return {"homes": await leaderboard()}
    except Exception as exc:
        log.exception("leaderboard failed")
        raise HTTPException(status_code=503, detail=_safe_detail(exc)) from exc


@app.get("/api/user/{home_id}")
async def user_profile(home_id: str) -> dict[str, object]:
    try:
        user = await get_user(home_id)
    except Exception as exc:
        log.exception("user lookup failed")
        raise HTTPException(status_code=503, detail=_safe_detail(exc)) from exc
    if user is None:
        raise HTTPException(status_code=404, detail="unknown home")
    receipt = await latest_receipt(home_id)
    return {"user": user, "latest_receipt": receipt}


@app.post("/api/gemini/analyze-sample")
async def gemini_sample() -> dict[str, object]:
    try:
        return await analyze_sample(settings.demo_home_id)
    except Exception as exc:
        log.exception("sample bill analysis failed")
        raise HTTPException(status_code=502, detail=_safe_detail(exc)) from exc


@app.post("/api/gemini/analyze-bill")
async def gemini_upload(file: UploadFile = File(...)) -> dict[str, object]:
    data = await file.read()
    mime = file.content_type or "image/png"
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in {"image/png", "image/jpeg", "image/webp"}:
        mime = "image/png"
    try:
        return await analyze_image(data, mime, settings.demo_home_id, "upload")
    except Exception as exc:
        log.exception("uploaded bill analysis failed")
        raise HTTPException(status_code=502, detail=_safe_detail(exc)) from exc


@app.get("/api/gemini/sample-bill")
async def sample_bill_image() -> FileResponse:
    path = ensure_sample_bill()
    return FileResponse(path, media_type="image/png")


@app.post("/api/demo/trigger-spike")
async def demo_trigger_spike() -> dict[str, object]:
    try:
        return await trigger_spike()
    except Exception as exc:
        log.exception("trigger spike failed")
        raise HTTPException(status_code=503, detail=_safe_detail(exc)) from exc


@app.post("/api/demo/reduce-load")
async def demo_reduce_load() -> dict[str, object]:
    try:
        return await reduce_load()
    except Exception as exc:
        log.exception("reduce load failed")
        raise HTTPException(status_code=502, detail=_safe_detail(exc)) from exc
