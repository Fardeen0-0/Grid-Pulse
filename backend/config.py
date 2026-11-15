"""Runtime configuration loaded from the repository .env file."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def _clean(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip().strip('"').strip("'")


def mongo_uri_candidates(raw: str) -> list[str]:
    """Build connectable Atlas URIs.

    The checked-in URI wraps the password in angle brackets copied from the
    Atlas placeholder. Brackets are illegal in userinfo, so the first candidate
    strips that wrapper and percent-encodes the password. The second keeps the
    brackets and encodes them, in case they are actually part of the secret.
    """
    raw = raw.strip()
    if "://" not in raw or "@" not in raw:
        return [raw]
    scheme, rest = raw.split("://", 1)
    userinfo, hostpart = rest.rsplit("@", 1)
    if ":" not in userinfo:
        return [raw]
    user, password = userinfo.split(":", 1)
    passwords = [password]
    if password.startswith("<") and password.endswith(">") and len(password) > 2:
        passwords.insert(0, password[1:-1])
    uris: list[str] = []
    for pwd in passwords:
        host = hostpart
        if "?" not in host:
            host = host.rstrip("/") + "/?retryWrites=true&w=majority"
        uri = f"{scheme}://{quote(user, safe='')}:{quote(pwd, safe='')}@{host}"
        if uri not in uris:
            uris.append(uri)
    return uris


def persist_solana_key(secret: str) -> None:
    """Write a generated Devnet secret back to .env without logging it."""
    path = ROOT / ".env"
    line = f"SOLANA_PRIVATE_KEY={secret}"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if re.search(r"^SOLANA_PRIVATE_KEY=.*$", text, flags=re.M):
        text = re.sub(r"^SOLANA_PRIVATE_KEY=.*$", line, text, count=1, flags=re.M)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += line + "\n"
    path.write_text(text, encoding="utf-8")
    os.environ["SOLANA_PRIVATE_KEY"] = secret


class Settings:
    def __init__(self) -> None:
        self.root = ROOT
        self.gemini_api_key = _clean(os.getenv("GEMINI_API_KEY"))
        self.tiger_url = _clean(os.getenv("TIGER_POSTGRES_URL"))
        self.mongo_uri_raw = _clean(os.getenv("MONGODB_URI"))
        self.mongo_db = _clean(os.getenv("MONGODB_DB_NAME")) or "gridpulse"
        self.solana_rpc_url = _clean(os.getenv("SOLANA_RPC_URL")) or "https://api.devnet.solana.com"
        self.solana_private_key = _clean(os.getenv("SOLANA_PRIVATE_KEY"))
        self.public_domain = _clean(os.getenv("PUBLIC_DOMAIN")) or "gridpulse.xyz"
        self.demo_home_id = "home_demo_01"
        self.baseline_watts = 3400.0
        self.reduced_watts = 850.0
        self.clean_carbon = 210.0
        self.dirty_carbon = 680.0
        self.payout_lamports = 1_000_000
        self.payout_sol = 0.001

    def reload_solana_key(self) -> None:
        load_dotenv(self.root / ".env", override=True)
        self.solana_private_key = _clean(os.getenv("SOLANA_PRIVATE_KEY"))

    def reload_mongo(self) -> None:
        """Pick up a password edited in .env without restarting the process."""
        load_dotenv(self.root / ".env", override=True)
        self.mongo_uri_raw = _clean(os.getenv("MONGODB_URI"))
        self.mongo_db = _clean(os.getenv("MONGODB_DB_NAME")) or "gridpulse"


# Neighborhood catalog shared by Tiger telemetry and Mongo profiles.
HOMES: list[dict[str, object]] = [
    {
        "home_id": "home_demo_01",
        "display_name": "Avery Chen",
        "baseline_watts": 3400.0,
        "streak_days": 3,
        "total_co2_saved_grams": 420.0,
        "total_sol_earned": 0.0,
    },
    {
        "home_id": "home_peer_02",
        "display_name": "North Hall",
        "baseline_watts": 2100.0,
        "streak_days": 6,
        "total_co2_saved_grams": 310.0,
        "total_sol_earned": 0.002,
    },
    {
        "home_id": "home_peer_03",
        "display_name": "Birch House",
        "baseline_watts": 2760.0,
        "streak_days": 2,
        "total_co2_saved_grams": 180.0,
        "total_sol_earned": 0.001,
    },
    {
        "home_id": "home_peer_04",
        "display_name": "Campus Annex",
        "baseline_watts": 1880.0,
        "streak_days": 4,
        "total_co2_saved_grams": 260.0,
        "total_sol_earned": 0.0,
    },
    {
        "home_id": "home_peer_05",
        "display_name": "South Quad",
        "baseline_watts": 4050.0,
        "streak_days": 1,
        "total_co2_saved_grams": 90.0,
        "total_sol_earned": 0.0,
    },
]

settings = Settings()
