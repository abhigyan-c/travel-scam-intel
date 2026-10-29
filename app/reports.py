"""Small deterministic intake filters; no identity or allegation-truth assessment."""

import re
import unicodedata
from collections import Counter

VERSION = "reports-v1"
EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE = re.compile(r"(?<!\w)(?:\+\d[\d ().-]{7,}\d|\b\d{3}[-. ]\d{3}[-. ]\d{4}\b)(?!\w)")
LONG_NUMBER = re.compile(r"(?<!\d)(?:\d[ -]?){12,19}(?!\d)")
URL = re.compile(r"https?://\S+", re.I)


class ReportConflict(ValueError):
    pass


class InvalidReport(ValueError):
    pass


def redact_pii(value: str) -> tuple[str, list[str]]:
    value = unicodedata.normalize("NFKC", value)
    flags = []
    for pattern, flag in (
        (EMAIL, "email_redacted"),
        (PHONE, "phone_redacted"),
        (LONG_NUMBER, "long_number_redacted"),
    ):
        value, count = pattern.subn("[REDACTED]", value)
        if count:
            flags.append(flag)
    if re.search(r"\b(?:i will|i am going to)\s+(?:kill|hurt|attack)\b", value, re.I):
        flags.append("threat_language")
    return value, flags


def prepare_text(value: str) -> tuple[str, list[str]]:
    value = unicodedata.normalize("NFKC", value)
    if any(unicodedata.category(char) == "Cc" and char not in "\r\n\t" for char in value):
        raise InvalidReport("invalid_control_characters")
    narrative = URL.sub("", value)
    words = re.findall(r"[A-Za-z]+", narrative.lower())
    if len(words) < 4 or (len(words) >= 10 and max(Counter(words).values()) / len(words) > 0.8):
        raise InvalidReport("empty_or_repetitive_report")
    return redact_pii(value)
