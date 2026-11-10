"""Sentiment engine interface.

An engine turns one headline (+ optional short summary) into:

* ``tone``    generic positive/negative tone, -1..+1 (NOT currency direction)
* ``regions`` {region code: score}, -1..+1 where +1 = bullish for THAT currency
* ``risk``    global risk tone, +1 = risk-on, -1 = risk-off, None = no risk signal

Engines are swappable through ``settings.sentiment_engine`` (see ``get_engine``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

GLOBAL_REGION = "GLOBAL"


@dataclass
class ScoredText:
    tone: float = 0.0
    regions: dict[str, float] = field(default_factory=dict)
    risk: float | None = None
    hits: list[str] = field(default_factory=list)  # human-readable rule trace

    @property
    def has_signal(self) -> bool:
        return self.risk is not None or any(abs(v) > 1e-9 for v in self.regions.values())


@dataclass(frozen=True)
class TextItem:
    title: str
    summary: str = ""
    default_region: str | None = None  # the feed's home region, if any
    force_default: bool = False  # always tag default_region (central-bank feeds)


class SentimentEngine(Protocol):
    name: str

    def score(self, item: TextItem) -> ScoredText: ...

    def score_many(self, items: Sequence[TextItem]) -> list[ScoredText]: ...
