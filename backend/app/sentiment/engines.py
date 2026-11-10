"""Concrete sentiment engines.

* ``LexiconEngine``  default; pure Python, instant, no downloads.
* ``FinBertEngine``  optional; ProsusAI/finbert via ``transformers`` running
  locally. FinBERT only supplies the generic tone - currency direction always
  comes from the transparent hawkish/dovish rules, because FinBERT reads
  "Fed cuts rates" as positive while it is bearish for the dollar.
"""
from __future__ import annotations

import logging
import re
from typing import Sequence

from . import lexicon as lx
from .base import ScoredText, TextItem
from .directional import DirectionalScorer

log = logging.getLogger("sentiment")
_WORD_RE = re.compile(r"[a-z][a-z-]+")


def lexicon_tone(text: str) -> float:
    """Generic tone in -1..+1 from simple positive/negative word counts."""
    words = _WORD_RE.findall(text.lower())
    pos = sum(w in lx.TONE_POSITIVE for w in words)
    neg = sum(w in lx.TONE_NEGATIVE for w in words)
    return round((pos - neg) / (pos + neg + 1.0), 4) if pos or neg else 0.0


class LexiconEngine:
    name = "lexicon"

    def __init__(self) -> None:
        self._directional = DirectionalScorer()

    def score(self, item: TextItem) -> ScoredText:
        scored = self._directional.score(item)
        scored.tone = lexicon_tone(f"{item.title}. {item.summary}")
        return scored

    def score_many(self, items: Sequence[TextItem]) -> list[ScoredText]:
        return [self.score(i) for i in items]


class FinBertEngine:
    """FinBERT tone + rule-based currency direction. Heavy deps are imported lazily."""

    name = "finbert"
    model_name = "ProsusAI/finbert"

    def __init__(self, batch_size: int = 16) -> None:
        # Raises ImportError when torch/transformers are missing -> get_engine falls back.
        from transformers import pipeline  # type: ignore[import-not-found]

        self._pipe = pipeline("text-classification", model=self.model_name, top_k=None, truncation=True, max_length=256)
        self._batch_size = batch_size
        self._directional = DirectionalScorer()

    def _tones(self, texts: list[str]) -> list[float]:
        tones: list[float] = []
        for i in range(0, len(texts), self._batch_size):
            for result in self._pipe(texts[i : i + self._batch_size]):
                probs = {r["label"].lower(): float(r["score"]) for r in result}
                tones.append(round(probs.get("positive", 0.0) - probs.get("negative", 0.0), 4))
        return tones

    def score(self, item: TextItem) -> ScoredText:
        return self.score_many([item])[0]

    def score_many(self, items: Sequence[TextItem]) -> list[ScoredText]:
        if not items:
            return []
        tones = self._tones([f"{i.title}. {i.summary}".strip()[:1000] for i in items])
        out = []
        for item, tone in zip(items, tones):
            scored = self._directional.score(item)
            scored.tone = tone
            out.append(scored)
        return out
