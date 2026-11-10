"""Scoring engine: turns stored data into -100..+100 sentiment scores.

Public contract (used by app.pipeline and app.cli):

    run(session, as_of=None) -> int
    backfill(session, start, end, step_days=7) -> int

Layout:
    config.py        loads backend/config/scoring.yaml (all weights / thresholds)
    data.py          DataStore (loads every series once) + point-in-time Context
    signals.py       shared maths and formatting helpers
    components/      one module per component family (surprise, news, momentum,
                     risk, rates, positioning, trade, growth, commodity)
    engine.py        weights -> scores, coverage, persistence
    conclusions.py   template-based written conclusions

Methodology: docs/SCORING.md
"""
from __future__ import annotations

from .engine import backfill, run, score_horizon, score_region

__all__ = ["run", "backfill", "score_region", "score_horizon"]
