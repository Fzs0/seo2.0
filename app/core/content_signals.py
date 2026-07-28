"""Shared, language-aware content structure signals."""
from __future__ import annotations

import re
from collections.abc import Iterable


_FAQ_SIGNAL = re.compile(
    r"""
    \bfaq\b
    |frequently\s+asked(?:\s+questions)?
    |common\s+questions
    |常见问题
    |preguntas\s+frecuentes
    |perguntas\s+frequentes
    |questions\s+fr[ée]quentes
    |domande\s+frequenti
    |h(?:ä|ae)ufig(?:e|\s+gestellte)\s+fragen
    |fragen\s*(?:und|&)\s*antworten
    """,
    re.IGNORECASE | re.VERBOSE,
)


def has_faq_signal(value: str, configured_patterns: Iterable[str] = ()) -> bool:
    """Return whether content contains a recognized FAQ heading or marker."""
    if _FAQ_SIGNAL.search(value):
        return True
    folded = value.casefold()
    return any(
        str(pattern).casefold() in folded
        for pattern in configured_patterns
        if str(pattern).strip()
    )


__all__ = ["has_faq_signal"]
