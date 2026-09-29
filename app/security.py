# Copyright 2026 Google LLC
# Licensed under the Apache License, Version 2.0
import os
import re
from typing import Tuple

# CISO Emergency Kill Switch
def is_kill_switch_active() -> bool:
    return os.getenv("CISO_KILL_SWITCH_ACTIVE", "false").lower() in ("true", "1", "yes")


def check_kill_switch():
    """CISO Emergency Kill Switch: Freezes agent operations if active."""
    if is_kill_switch_active():
        raise RuntimeError(
            "CISO Emergency Kill Switch Active: Agent operations temporarily suspended by Security Operations."
        )


# In-Memory PII Scrubber (Presidio Pattern Equivalent)
PII_PATTERNS = [
    (r"\b\d{3}-\d{2}-\d{4}\b", "[SSN-REDACTED]"),
    (r"\b(?:\d[ -]*?){13,16}\b", "[CREDIT-CARD-REDACTED]"),
    (r"\b\+?[\d\s\-()]{10,20}\b", "[PHONE-REDACTED]"),
]


def scrub_pii(text: str) -> Tuple[str, bool]:
    """Inspects and scrubs sensitive PII (SSN, Credit Cards, Phones) from prompt context."""
    scrubbed = text
    modified = False
    for pattern, replacement in PII_PATTERNS:
        new_text, count = re.subn(pattern, replacement, scrubbed)
        if count > 0:
            scrubbed = new_text
            modified = True
    return scrubbed, modified
