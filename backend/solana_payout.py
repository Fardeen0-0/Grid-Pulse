"""Solana Devnet treasury: key management, airdrop, and 0.001 SOL payouts."""

from __future__ import annotations

import logging

import base58
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solana.rpc.models import TxOpts
from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import Message
from solders.pubkey import Pubkey
from solders.signature import Signature
from solders.system_program import TransferParams, transfer
from solders.transaction import Transaction

from backend.config import persist_solana_key, settings

log = logging.getLogger("gridpulse.solana")

LAMPORTS_PER_SOL = 1_000_000_000


def solscan_url(signature: str) -> str:
    return f"https://solscan.io/tx/{signature}?cluster=devnet"


def _keypair_from_secret(secret: str) -> Keypair:
    text = secret.strip()
    if text.startswith("["):
        raw = bytes(int(part) for part in text.strip("[]").split(",") if part.strip())
        return Keypair.from_bytes(raw)
    try:
        return Keypair.from_base58_string(text)
    except Exception:
        raw = base58.b58decode(text)
    if len(raw) == 64:
        return Keypair.from_bytes(raw)
    if len(raw) == 32:
        return Keypair.from_seed(raw)
    raise RuntimeError("SOLANA_PRIVATE_KEY is not a 32 or 64 byte keypair")


def load_or_create_keypair() -> Keypair:
    settings.reload_solana_key()
    if settings.solana_private_key:
        return _keypair_from_secret(settings.solana_private_key)
    keypair = Keypair()
    secret = base58.b58encode(bytes(keypair)).decode("ascii")
    persist_solana_key(secret)
    settings.solana_private_key = secret
    log.info("generated Devnet treasury %s", keypair.pubkey())
    return keypair


def treasury_public_key() -> str:
    return str(load_or_create_keypair().pubkey())


def _client() -> AsyncClient:
    return AsyncClient(settings.solana_rpc_url, commitment=Confirmed, timeout=40)


async def _balance(client: AsyncClient, keypair: Keypair) -> int:
    response = await client.get_balance(keypair.pubkey(), commitment=Confirmed)
    return int(response.value)


async def _sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


def _rpc_detail(exc: Exception) -> str:
    cause = exc.__cause__ or exc
    response = getattr(cause, "response", None)
    if response is not None:
        text = getattr(response, "text", "") or ""
        return f"HTTP {getattr(response, 'status_code', '?')} {text[:240]}"
    return str(cause)[:240]


async def ensure_funded(keypair: Keypair, minimum_lamports: int) -> None:
    client = _client()
    try:
        current = await _balance(client, keypair)
        if current >= minimum_lamports:
            return
        log.info("treasury balance %s lamports, requesting Devnet airdrop", current)
        last_exc: Exception | None = None
        for amount in (1_000_000_000, 500_000_000):
            try:
                response = await client.request_airdrop(keypair.pubkey(), amount)
                await client.confirm_transaction(response.value, commitment=Confirmed)
                current = await _balance(client, keypair)
                if current >= minimum_lamports:
                    log.info("airdrop confirmed, balance %s lamports", current)
                    return
            except Exception as exc:
                last_exc = exc
                detail = _rpc_detail(exc)
                log.warning("airdrop attempt failed: %s", detail)
                if "410" in detail or "faucet is closed" in detail.lower():
                    break
                await _sleep(2)
        current = await _balance(client, keypair)
        if current >= minimum_lamports:
            return
        detail = _rpc_detail(last_exc) if last_exc else "no faucet"
        raise RuntimeError(
            "Devnet airdrop did not fund treasury "
            f"{keypair.pubkey()} ({current} lamports). {detail} "
            "Send devnet SOL to that address from https://faucet.solana.com and retry."
        )
    finally:
        await client.close()


async def transfer_sol(destination: str, lamports: int | None = None) -> dict[str, object]:
    """Send a confirmed Devnet transfer and return the signature plus Solscan URL."""
    amount = settings.payout_lamports if lamports is None else lamports
    keypair = load_or_create_keypair()
    await ensure_funded(keypair, amount + 20_000)
    destination_key = Pubkey.from_string(destination)
    client = _client()
    last_exc: Exception | None = None
    try:
        for _attempt in range(3):
            try:
                blockhash_resp = await client.get_latest_blockhash(commitment=Confirmed)
                blockhash: Hash = blockhash_resp.value.blockhash
                last_valid = blockhash_resp.value.last_valid_block_height
                instruction = transfer(
                    TransferParams(
                        from_pubkey=keypair.pubkey(),
                        to_pubkey=destination_key,
                        lamports=amount,
                    )
                )
                message = Message.new_with_blockhash([instruction], keypair.pubkey(), blockhash)
                transaction = Transaction.new_unsigned(message)
                transaction.sign([keypair], blockhash)
                sent = await client.send_raw_transaction(
                    bytes(transaction),
                    opts=TxOpts(
                        skip_confirmation=False,
                        skip_preflight=False,
                        preflight_commitment=Confirmed,
                        last_valid_block_height=last_valid,
                    ),
                )
                signature: Signature = sent.value
                statuses = await client.get_signature_statuses([signature])
                status = statuses.value[0] if statuses.value else None
                if status is not None and status.err:
                    raise RuntimeError(f"transaction failed: {status.err}")
                signature_text = str(signature)
                return {
                    "signature": signature_text,
                    "solscan_url": solscan_url(signature_text),
                    "lamports": amount,
                    "sol_amount": amount / LAMPORTS_PER_SOL,
                    "from_wallet": str(keypair.pubkey()),
                    "to_wallet": destination,
                    "cluster": "devnet",
                }
            except Exception as exc:
                last_exc = exc
                log.warning("devnet transfer attempt failed: %s", exc)
                await _sleep(1.5)
    finally:
        await client.close()
    raise RuntimeError(f"Devnet transfer failed: {last_exc}")
