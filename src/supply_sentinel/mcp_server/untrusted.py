"""Untrusted text handling (spec 03 section 6).

Supplier notes and EOL notice text come from outside the company. They are never returned raw:
`wrap_untrusted` scans the text and returns an `UntrustedText` that says whether it looks like
an injection attempt. Flagging only marks the text; it never removes or rewrites it.

Patterns use word boundaries and context so ordinary notes ("approved vendor list updated")
are not flagged. The list below is the single place to extend.
"""

import re

from pydantic import BaseModel

# (name, regex). All compiled case-insensitive.
PATTERNS: list[tuple[str, str]] = [  # fmt: skip
    (
        "instruction_override",
        r"\b(ignore|disregard|forget)\b[^.]{0,30}\b(previous|prior|above|earlier)\b",
    ),
    ("instruction_override", r"\bnew\s+instructions?\b"),
    ("instruction_override", r"\boverride\b[^.]{0,20}\b(rules?|instructions?|policy|limits?)\b"),
    ("role_claim", r"(^|\n)\s*(system|assistant|developer)\s*:"),
    ("role_claim", r"\byou\s+are\s+now\b"),
    ("role_claim", r"\bact\s+as\s+(the|an?)\s+(system|admin|administrator|planner)\b"),
    (
        "tool_coercion",
        r"\b(auto-?)?approve\s+(this|it|the|all|every|rec|recommendation|po)\b",
    ),
    ("tool_coercion", r"\bmark\s+(it\s+|this\s+)?(as\s+)?approved\b"),
    ("tool_coercion", r"\b(call|invoke|run|use)\s+(the\s+)?tool\b"),
    ("tool_coercion", r"\brecord_recommendation\b"),
    ("tool_coercion", r"\bset\s+(the\s+)?(quantity|qty|order\s+quantity)\s+(to|=)"),
    (
        "exfiltration",
        r"\b(send|forward|email|post|upload)\b[^.]{0,40}\bto\b[^.]{0,30}(@|https?://|www\.)",
    ),
    ("exfiltration", r"https?://"),
    ("hidden_text", r"[\u200b\u200c\u200d\u2060\ufeff\u202a-\u202e]"),
    ("hidden_text", r"[A-Za-z0-9+/]{60,}={0,2}"),
]
_COMPILED = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in PATTERNS]


class UntrustedText(BaseModel):
    source: str
    text: str
    injection_suspected: bool
    matched_patterns: list[str]


def scan(text: str) -> list[str]:
    """Names of the patterns that match, without duplicates, in list order."""
    found: list[str] = []
    for name, rx in _COMPILED:
        if name not in found and rx.search(text):
            found.append(name)
    return found


def wrap_untrusted(source: str, text: str) -> UntrustedText:
    matched = scan(text)
    return UntrustedText(
        source=source, text=text, injection_suspected=bool(matched), matched_patterns=matched
    )
