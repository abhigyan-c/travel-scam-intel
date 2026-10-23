import hashlib
import json
import re
from dataclasses import dataclass

import trafilatura
from langdetect import DetectorFactory, LangDetectException, detect_langs

from app.integrations.fetch import FetchError, canonicalize_url

DetectorFactory.seed = 0
PROCESSING_VERSION = "article-v1"


@dataclass(frozen=True)
class ProcessedText:
    text: str | None
    language: str | None
    outcome: str
    metadata: dict


def process_bytes(raw: bytes, *, content_type: str, url: str | None = None) -> ProcessedText:
    if len(raw) > 2_000_000:
        return ProcessedText(None, None, "too_large", {})
    media_type = content_type.split(";", 1)[0].strip().lower()
    metadata = {}
    if media_type == "text/plain":
        charset = re.search(r"charset=[\"']?([\w-]+)", content_type, re.I)
        try:
            text = raw.decode(charset.group(1) if charset else "utf-8")
        except (LookupError, UnicodeDecodeError):
            return ProcessedText(None, None, "invalid_encoding", {})
    elif media_type in {"text/html", "application/xhtml+xml"}:
        extracted = trafilatura.extract(
            raw,
            url=url,
            output_format="json",
            with_metadata=True,
            include_comments=False,
            date_extraction_params={"extensive_search": False},
        )
        if not extracted:
            return ProcessedText(None, None, "empty", {})
        data = json.loads(extracted)
        text = data.get("text") or ""
        metadata = {key: data[key] for key in ("title", "author", "date") if data.get(key)}
        if data.get("source"):
            try:
                metadata["canonical_url"] = canonicalize_url(data["source"])
            except FetchError:
                pass
        if re.search(
            r"access denied|just a moment|verify you are human|captcha",
            metadata.get("title", ""),
            re.I,
        ):
            return ProcessedText(None, None, "blocked", metadata)
    else:
        return ProcessedText(None, None, "unsupported_content_type", {})
    text = "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip()).strip()
    if not text:
        return ProcessedText(None, None, "empty", metadata)
    language = None
    if sum(char.isalpha() for char in text) >= 40:
        try:
            candidates = detect_langs(text[:20000])
            if candidates and candidates[0].prob >= 0.9:
                language = candidates[0].lang
        except LangDetectException:
            pass
    metadata["clean_text_hash"] = hashlib.sha256(text.encode()).hexdigest()
    outcome = (
        "ready" if language == "en" else "unknown_language" if language is None else "non_english"
    )
    return ProcessedText(text, language, outcome, metadata)
