"""Prompt-injection detection for untrusted document text."""

import re

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore (all )?(previous|prior) instructions", re.I),
    re.compile(r"reveal (the )?(system prompt|api key|secret)", re.I),
    re.compile(r"disregard (the )?(developer|system) message", re.I),
    re.compile(r"execute (this )?(command|shell)", re.I),
    re.compile(r"you are now", re.I),
)


def detect_prompt_injection(text: str) -> list[str]:
    return [pattern.pattern for pattern in _PATTERNS if pattern.search(text or "")]


def sanitize_untrusted(text: str) -> tuple[str, list[str]]:
    attempts = detect_prompt_injection(text)
    if not attempts:
        return text, []
    lines: list[str] = []
    for line in (text or "").splitlines():
        if detect_prompt_injection(line):
            lines.append("[document instruction removed]")
        else:
            lines.append(line)
    return "\n".join(lines), attempts
