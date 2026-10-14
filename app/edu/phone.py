"""Phone numbers are the only student identifier. One canonical form: +<digits>.
Mirrors the SQL function reg_normalize_phone (migrations/009) — keep in sync."""
import re
from typing import Optional

_NON_DIGIT = re.compile(r"\D")


def normalize_phone(raw: Optional[str]) -> Optional[str]:
    digits = _NON_DIGIT.sub("", raw or "")
    if not digits:
        return None
    if len(digits) == 9 and digits[0] in "62":  # bare Cameroon number
        return "+237" + digits
    if digits.startswith("00"):
        digits = digits[2:]
    if not 8 <= len(digits) <= 15:
        return None
    return "+" + digits


def mask_phone(phone: Optional[str]) -> str:
    """+237 6xx xx 12 34 -> +237•••••1234, for watermarks and teacher views."""
    p = phone or ""
    return (p[:4] + "•" * max(0, len(p) - 8) + p[-4:]) if len(p) > 8 else p
