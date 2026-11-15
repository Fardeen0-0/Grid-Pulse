"""Generate a readable mock electric bill for the Gemini Vision demo."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "assets" / "sample_bill.png"
PUBLIC_PATH = ROOT / "frontend" / "public" / "sample_bill.png"

FONT_CANDIDATES = [
    Path(r"C:\Windows\Fonts\arialbd.ttf"),
    Path(r"C:\Windows\Fonts\arial.ttf"),
    Path(r"C:\Windows\Fonts\segoeui.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"),
]


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    preferred = []
    for path in FONT_CANDIDATES:
        name = path.name.lower()
        is_bold = "bd" in name or "bold" in name
        if bold == is_bold:
            preferred.append(path)
    for path in preferred + FONT_CANDIDATES:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default(size=size)


def generate_sample_bill(path: Path | None = None) -> Path:
    """Write assets/sample_bill.png and return its path."""
    target = path or DEFAULT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)

    width, height = 1200, 1560
    image = Image.new("RGB", (width, height), "#f6f3ea")
    draw = ImageDraw.Draw(image)
    title = _font(54, bold=True)
    heading = _font(32, bold=True)
    body = _font(28, bold=False)
    small = _font(22, bold=False)
    big = _font(40, bold=True)

    draw.rectangle([0, 0, width, 210], fill="#0f3d32")
    draw.text((56, 42), "PIONEER VALLEY ELECTRIC", fill="#f4fff8", font=title)
    draw.text((56, 118), "Residential Service Statement", fill="#b7e6d4", font=heading)
    draw.text((56, 162), "Member-owned utility  ·  Amherst, Massachusetts", fill="#d5efe4", font=small)

    draw.rounded_rectangle([48, 250, 1152, 430], radius=18, fill="#ffffff", outline="#d9d3c5", width=2)
    draw.text((80, 278), "Account", fill="#5d6b63", font=small)
    draw.text((80, 314), "GP-DEMO-01042", fill="#14241c", font=big)
    draw.text((620, 278), "Service address", fill="#5d6b63", font=small)
    draw.text((620, 322), "14 Elm Walk", fill="#14241c", font=heading)
    draw.text((620, 366), "Amherst, MA 01002", fill="#14241c", font=body)

    facts = [
        ("Billing period", "October 1, 2026 - October 31, 2026"),
        ("Utility name", "Pioneer Valley Electric"),
        ("Energy rate", "$0.1842 per kWh"),
        ("Monthly usage", "742 kWh"),
        ("Peak window", "4:00 PM - 9:00 PM weekdays"),
        ("Amount due", "$136.68"),
    ]
    y = 470
    for label, value in facts:
        draw.rounded_rectangle([48, y, 1152, y + 92], radius=14, fill="#ffffff", outline="#e4dece", width=2)
        draw.text((80, y + 14), label, fill="#5d6b63", font=small)
        draw.text((80, y + 44), value, fill="#10241b", font=heading)
        y += 108

    draw.text((56, y + 10), "TOP APPLIANCES THIS MONTH", fill="#0f3d32", font=heading)
    y += 64
    draw.rounded_rectangle([48, y, 1152, y + 250], radius=16, fill="#ffffff", outline="#d9d3c5", width=2)
    rows = [
        ("Level 2 EV charger", "310 kWh", "Mostly 4 PM - 9 PM"),
        ("Central air conditioner", "180 kWh", "Mostly 4 PM - 9 PM"),
        ("Electric water heater", "95 kWh", "Mostly 5 PM - 8 PM"),
    ]
    row_y = y + 24
    for name, usage, when in rows:
        draw.text((80, row_y), name, fill="#14241c", font=body)
        draw.text((620, row_y), usage, fill="#0f3d32", font=body)
        draw.text((820, row_y), when, fill="#3d5148", font=small)
        row_y += 70

    footer_y = height - 90
    draw.text(
        (56, footer_y),
        "Peak demand 4:00 PM - 9:00 PM is when the regional grid is dirtiest.",
        fill="#3d5148",
        font=small,
    )
    image.save(target, format="PNG")
    _publish(target)
    return target


def _publish(source: Path) -> None:
    """Copy the bill into Next's public folder so the page does not proxy the PNG."""
    PUBLIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = source.read_bytes()
    if not PUBLIC_PATH.exists() or PUBLIC_PATH.read_bytes() != data:
        PUBLIC_PATH.write_bytes(data)


def ensure_sample_bill() -> Path:
    if not DEFAULT_PATH.exists():
        return generate_sample_bill(DEFAULT_PATH)
    _publish(DEFAULT_PATH)
    return DEFAULT_PATH


if __name__ == "__main__":
    print(generate_sample_bill())
