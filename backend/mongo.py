"""MongoDB Atlas document store for profiles, bills, and payout receipts."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from solders.keypair import Keypair

from backend.config import HOMES, mongo_uri_candidates, settings

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def clean(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if doc is None:
        return None
    out: dict[str, Any] = {}
    for key, value in doc.items():
        if key == "_id":
            out["id"] = str(value)
            continue
        if isinstance(value, datetime):
            stamped = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
            out[key] = stamped.astimezone(timezone.utc).isoformat()
        elif isinstance(value, dict):
            nested = clean(value)
            out[key] = nested if nested is not None else value
        else:
            out[key] = value
    return out


def database() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("MongoDB is not connected")
    return _db


def auth_failure(exc: Exception) -> bool:
    text = str(exc).lower()
    return "bad auth" in text or "authentication failed" in text or "code: 8000" in text


async def setup(attempts: int = 3, timeout_ms: int = 12000) -> None:
    global _client, _db
    settings.reload_mongo()
    if _db is not None:
        return
    last_exc: Exception | None = None
    for attempt in range(attempts):
        for uri in mongo_uri_candidates(settings.mongo_uri_raw):
            client = AsyncIOMotorClient(
                uri,
                serverSelectionTimeoutMS=timeout_ms,
                tz_aware=True,
                tzinfo=timezone.utc,
            )
            try:
                await client.admin.command("ping")
            except Exception as exc:
                last_exc = exc
                client.close()
                continue
            _client = client
            _db = client[settings.mongo_db]
            break
        if _db is not None:
            break
        message = str(last_exc or "")
        if "SSL" not in message and "EOF" not in message:
            break
        await asyncio.sleep(1.5)
    if _db is None:
        raise RuntimeError(f"MongoDB Atlas connection failed: {last_exc}") from last_exc

    db = database()
    await db.users.create_index("home_id", unique=True)
    await db.parsed_bills.create_index([("home_id", 1), ("created_at", -1)])
    await db.payout_receipts.create_index([("home_id", 1), ("created_at", -1)])

    for home in HOMES:
        profile = {
            "home_id": home["home_id"],
            "display_name": home["display_name"],
            "solana_wallet": str(Keypair().pubkey()),
            "total_co2_saved_grams": float(home["total_co2_saved_grams"]),
            "total_sol_earned": float(home["total_sol_earned"]),
            "streak_days": int(home["streak_days"]),
            "created_at": _now(),
        }
        await db.users.update_one(
            {"home_id": profile["home_id"]},
            {"$setOnInsert": profile},
            upsert=True,
        )

    demo = await db.users.find_one({"home_id": settings.demo_home_id})
    if demo is not None and not demo.get("solana_wallet"):
        await db.users.update_one(
            {"home_id": settings.demo_home_id},
            {"$set": {"solana_wallet": str(Keypair().pubkey())}},
        )


def close() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
    _client = None
    _db = None


async def get_user(home_id: str) -> dict[str, Any] | None:
    doc = await database().users.find_one({"home_id": home_id})
    return clean(doc)


async def leaderboard() -> list[dict[str, Any]]:
    cursor = database().users.find({}).sort("total_co2_saved_grams", -1)
    docs = await cursor.to_list(length=20)
    return [item for item in (clean(doc) for doc in docs) if item is not None]


async def save_bill(document: dict[str, Any]) -> dict[str, Any]:
    payload = dict(document)
    payload.setdefault("created_at", _now())
    result = await database().parsed_bills.insert_one(payload)
    payload["_id"] = result.inserted_id
    cleaned = clean(payload)
    assert cleaned is not None
    return cleaned


async def latest_bill(home_id: str) -> dict[str, Any] | None:
    doc = await database().parsed_bills.find_one(
        {"home_id": home_id},
        sort=[("created_at", -1)],
    )
    return clean(doc)


async def save_receipt(document: dict[str, Any]) -> dict[str, Any]:
    payload = dict(document)
    payload.setdefault("created_at", _now())
    result = await database().payout_receipts.insert_one(payload)
    await database().users.update_one(
        {"home_id": payload["home_id"]},
        {
            "$inc": {
                "total_co2_saved_grams": float(payload["co2_avoided_grams"]),
                "total_sol_earned": float(payload["sol_amount"]),
            }
        },
    )
    await database().users.update_one(
        {"home_id": payload["home_id"], "streak_days": {"$lt": 1}},
        {"$set": {"streak_days": 1}},
    )
    payload["_id"] = result.inserted_id
    cleaned = clean(payload)
    assert cleaned is not None
    return cleaned


async def latest_receipt(home_id: str) -> dict[str, Any] | None:
    doc = await database().payout_receipts.find_one(
        {"home_id": home_id},
        sort=[("created_at", -1)],
    )
    return clean(doc)
