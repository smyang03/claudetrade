"""Helpers for safe logging of URL-bearing exceptions."""

from __future__ import annotations

import os
import re


def mask_secrets(text: object) -> str:
    value = str(text or "")
    telegram_token = os.getenv("TELEGRAM_TOKEN", "")
    if telegram_token:
        value = value.replace(telegram_token, "***TELEGRAM_TOKEN***")
    value = re.sub(r"/bot[0-9]{5,}:[A-Za-z0-9_-]+", "/bot***TELEGRAM_TOKEN***", value)
    value = re.sub(r"(token=)[A-Za-z0-9_.:-]+", r"\1***", value, flags=re.IGNORECASE)
    value = re.sub(r"(crtfc_key=)[A-Za-z0-9_.:-]+", r"\1***", value, flags=re.IGNORECASE)
    return value
