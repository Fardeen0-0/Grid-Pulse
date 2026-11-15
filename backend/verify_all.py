"""End-to-end sponsor checks. Exit 0 only when every service responds for real."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from solders.keypair import Keypair  # noqa: E402

from backend.config import settings  # noqa: E402
from backend.gemini_bill import analyze_sample  # noqa: E402
from backend.mongo import close as close_mongo  # noqa: E402
from backend.mongo import get_user, latest_bill, save_bill, setup as setup_mongo  # noqa: E402
from backend.sample_bill import generate_sample_bill  # noqa: E402
from backend.solana_payout import transfer_sol, treasury_public_key  # noqa: E402
from backend.tiger import tiger  # noqa: E402


def _say(message: str) -> None:
    print(message, flush=True)


async def verify_tiger() -> None:
    _say("[1/4] Tiger Data hypertable + continuous aggregate")
    await asyncio.to_thread(tiger.setup_schema)
    moment = datetime.now(timezone.utc) - timedelta(minutes=2)
    rows = []
    for index in range(6):
        rows.append(
            (
                moment + timedelta(seconds=index * 5),
                "verify_home",
                1200 + index * 15,
                455.0,
                True,
            )
        )
    await asyncio.to_thread(tiger.insert_readings, rows)
    await asyncio.to_thread(tiger.refresh_recent_cagg)
    aggregated = await asyncio.to_thread(tiger.aggregated_for_home, "verify_home")
    if not aggregated:
        # Real-time aggregation should see the fresh bucket even before the
        # materialization job runs. A wider refresh covers that minute if needed.
        try:
            await asyncio.to_thread(
                tiger.execute,
                """
                CALL refresh_continuous_aggregate(
                    'telemetry_1min_cagg',
                    NOW() - INTERVAL '2 hours',
                    NOW()
                );
                """,
            )
        except Exception as exc:
            _say(f"      refresh note: {exc}")
        aggregated = await asyncio.to_thread(tiger.aggregated_for_home, "verify_home")
    if not aggregated:
        raise RuntimeError("telemetry_1min_cagg returned no aggregated rows")
    sample = aggregated[0]
    if sample.get("avg_watts") is None:
        raise RuntimeError(f"aggregate row missing avg_watts: {sample}")
    _say(
        f"      bucket={sample['bucket']} home={sample['home_id']} "
        f"avg_watts={float(sample['avg_watts']):.1f} "
        f"avg_carbon={float(sample['avg_carbon']):.1f}"
    )


async def verify_mongo() -> None:
    _say("[2/4] MongoDB Atlas users + parsed_bills")
    await setup_mongo()
    marker = {
        "home_id": "verify_user",
        "display_name": "Verifier",
        "solana_wallet": str(Keypair().pubkey()),
        "total_co2_saved_grams": 1.5,
        "total_sol_earned": 0.0,
        "streak_days": 1,
        "source": "verify_all",
    }
    from backend.mongo import database

    await database().users.update_one({"home_id": "verify_user"}, {"$set": marker}, upsert=True)
    found = await get_user("verify_user")
    if found is None or found.get("display_name") != "Verifier":
        raise RuntimeError("users collection readback failed")
    bill = await save_bill(
        {
            "home_id": "verify_user",
            "source": "verify_all",
            "model": "pending-gemini",
            "analysis": {
                "utility_name": "probe",
                "kwh_rate_usd": 0.1,
                "monthly_kwh": 100,
                "peak_window": "4 PM - 9 PM",
                "tips": [],
            },
        }
    )
    read_back = await latest_bill("verify_user")
    if read_back is None or read_back.get("id") != bill.get("id"):
        raise RuntimeError("parsed_bills collection readback failed")
    _say(f"      user={found['home_id']} bill={bill['id']}")


async def verify_gemini() -> None:
    _say("[3/4] Gemini Vision sample bill")
    path = generate_sample_bill()
    _say(f"      image={path}")
    document = await analyze_sample(settings.demo_home_id)
    analysis = document.get("analysis") or {}
    tips = analysis.get("tips") or []
    required = ("utility_name", "kwh_rate_usd", "monthly_kwh", "peak_window")
    missing = [key for key in required if not analysis.get(key)]
    if missing or len(tips) != 3:
        raise RuntimeError(f"structured bill JSON was incomplete: {analysis}")
    _say(
        f"      model={document.get('model')} utility={analysis['utility_name']} "
        f"rate={analysis['kwh_rate_usd']} kwh={analysis['monthly_kwh']} "
        f"peak={analysis['peak_window']} tips={len(tips)}"
    )


async def verify_solana() -> None:
    _say("[4/4] Solana Devnet micro-transfer")
    treasury = treasury_public_key()
    destination = str(Keypair().pubkey())
    _say(f"      treasury={treasury}")
    _say(f"      destination={destination}")
    receipt = await transfer_sol(destination, settings.payout_lamports)
    signature = str(receipt["signature"])
    if not signature:
        raise RuntimeError("transfer returned an empty signature")
    _say(f"      signature={signature}")
    _say(f"      solscan={receipt['solscan_url']}")


async def main() -> None:
    try:
        await verify_tiger()
        await verify_mongo()
        await verify_gemini()
        await verify_solana()
    finally:
        await asyncio.to_thread(tiger.close)
        close_mongo()
    _say("All sponsor checks passed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print(f"VERIFY FAILED: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
