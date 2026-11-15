"""Gemini Vision utility-bill extraction with structured output."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel

from backend.config import settings
from backend.mongo import save_bill
from backend.sample_bill import ensure_sample_bill

log = logging.getLogger("gridpulse.gemini")

PRIMARY_MODEL = "gemini-3.8-flash"
FALLBACK_MODEL = "gemini-2.5-flash"
# 3.8 is the required model and 2.5 is the required fallback. Newer flash
# models are only used when both of those return a model or capacity error.
EXTRA_MODELS = (
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.1-flash-lite",
    "gemini-3.7-flash",
)

PROMPT = """
Read this residential electric utility bill image and return only the structured fields.
Use the numbers and names printed on the bill.
utility_name: the utility company name.
kwh_rate_usd: the energy rate in US dollars per kWh as a number.
monthly_kwh: the monthly energy usage in kWh as a number.
peak_window: the peak-price or peak-demand hours.
tips: exactly 3 load-shifting actions that move the listed appliances out of the peak window.
Each tip needs appliance, action, monthly_co2_saved_grams, and monthly_savings_usd.
Estimate savings from the printed rate and usage. Do not leave any field blank.
""".strip()


class ShiftTip(BaseModel):
    appliance: str
    action: str
    monthly_co2_saved_grams: float
    monthly_savings_usd: float


class BillAnalysis(BaseModel):
    utility_name: str
    kwh_rate_usd: float
    monthly_kwh: float
    peak_window: str
    tips: list[ShiftTip]


def _parse_response(response: Any) -> BillAnalysis:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, BillAnalysis):
        data = parsed
    elif isinstance(parsed, dict):
        data = BillAnalysis.model_validate(parsed)
    else:
        text = getattr(response, "text", None) or ""
        if not text:
            raise RuntimeError("Gemini returned an empty response")
        data = BillAnalysis.model_validate_json(text)
    if data.kwh_rate_usd <= 0 or data.monthly_kwh <= 0:
        raise RuntimeError("bill rate and monthly kWh must be positive")
    if len(data.tips) != 3:
        raise RuntimeError(f"expected 3 tips, got {len(data.tips)}")
    return data


def _retryable(exc: Exception) -> bool:
    text = str(exc).upper()
    return any(
        token in text
        for token in (
            "503",
            "429",
            "504",
            "UNAVAILABLE",
            "RESOURCE_EXHAUSTED",
            "DEADLINE_EXCEEDED",
            "TIMEOUT",
            "TIMED OUT",
        )
    )


def _content_config(*, thinking: bool) -> types.GenerateContentConfig:
    config = types.GenerateContentConfig(
        temperature=0.2,
        response_mime_type="application/json",
        response_schema=BillAnalysis,
    )
    if thinking:
        config.thinking_config = types.ThinkingConfig(thinking_budget=0)
    return config


def _call_model(client: genai.Client, model: str, image_bytes: bytes, mime_type: str) -> BillAnalysis:
    contents = [
        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
        PROMPT,
    ]
    try:
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=_content_config(thinking=True),
        )
    except Exception as exc:
        # Some flash models reject a thinking budget. Retry that call once
        # without it; capacity and auth errors should move on to the next model.
        if "400" not in str(exc) and "INVALID_ARGUMENT" not in str(exc).upper():
            raise
        response = client.models.generate_content(
            model=model,
            contents=contents,
            config=_content_config(thinking=False),
        )
    return _parse_response(response)


def _generate_sync(image_bytes: bytes, mime_type: str) -> tuple[BillAnalysis, str]:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is empty")
    client = genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(timeout=15_000),
    )
    # One short try of the required models, then a flash model that is actually serving.
    # A 60s timeout on an overloaded model makes the dashboard proxy return 500.
    schedule = (PRIMARY_MODEL, FALLBACK_MODEL, *EXTRA_MODELS)
    errors: list[str] = []
    for index, model in enumerate(schedule):
        try:
            return _call_model(client, model, image_bytes, mime_type), model
        except Exception as exc:
            errors.append(f"{model}: {exc}".replace("\n", " ")[:280])
            if index == len(schedule) - 1:
                break
            if _retryable(exc):
                time.sleep(0.4)
    raise RuntimeError("Gemini analysis failed: " + " | ".join(errors))


async def analyze_image(
    image_bytes: bytes,
    mime_type: str,
    home_id: str,
    source: str,
) -> dict[str, Any]:
    if not image_bytes:
        raise RuntimeError("bill image is empty")
    analysis, model = await asyncio.to_thread(_generate_sync, image_bytes, mime_type)
    document = {
        "home_id": home_id,
        "source": source,
        "model": model,
        "analysis": analysis.model_dump(),
    }
    try:
        return await asyncio.wait_for(save_bill(document), timeout=12)
    except Exception:
        log.exception("parsed bill was not stored")
        return document


async def analyze_sample(home_id: str) -> dict[str, Any]:
    path = ensure_sample_bill()
    return await analyze_image(path.read_bytes(), "image/png", home_id, "sample")
