"""Local news sentiment: region tagging + currency-directional scoring.

    from app.sentiment import get_engine, TextItem
    engine = get_engine()                      # settings.sentiment_engine
    scored = engine.score(TextItem("Fed signals rate cuts"))
    scored.regions  -> {"US": -0.73}           # +1 = bullish for that currency
    scored.risk     -> None or -1..+1          # stored as region "GLOBAL"
    scored.tone     -> generic tone, -1..+1

Nothing leaves the machine: the default engine is a rule/lexicon model, the
optional FinBERT engine runs a local model (pip install -r requirements-finbert.txt).
"""
from __future__ import annotations

import logging

from .base import GLOBAL_REGION, ScoredText, SentimentEngine, TextItem
from .engines import FinBertEngine, LexiconEngine
from .tagging import REGION_KEYWORDS, find_mentions, is_macro_relevant, tag_regions

log = logging.getLogger("sentiment")
_cache: dict[str, SentimentEngine] = {}


def get_engine(name: str | None = None) -> SentimentEngine:
    """Engine by name ("lexicon" | "finbert"); unknown/unavailable -> lexicon with a warning."""
    if name is None:
        from ..config import settings

        name = settings.sentiment_engine
    name = (name or "lexicon").strip().lower()
    if name in _cache:
        return _cache[name]
    engine: SentimentEngine
    if name == "finbert":
        try:
            engine = FinBertEngine()
        except Exception as exc:  # noqa: BLE001 - missing deps, no model cache, no network...
            log.warning(
                "SENTIMENT_ENGINE=finbert but FinBERT is unavailable (%s: %s). Falling back to the lexicon "
                "engine. Install with: pip install -r backend/requirements-finbert.txt",
                type(exc).__name__, exc,
            )
            engine = LexiconEngine()
    else:
        if name != "lexicon":
            log.warning("Unknown sentiment engine %r, using lexicon.", name)
        engine = LexiconEngine()
    _cache[name] = engine
    return engine


__all__ = [
    "GLOBAL_REGION", "ScoredText", "SentimentEngine", "TextItem", "LexiconEngine", "FinBertEngine",
    "REGION_KEYWORDS", "find_mentions", "is_macro_relevant", "tag_regions", "get_engine",
]
