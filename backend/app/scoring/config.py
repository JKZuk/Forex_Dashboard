"""Load backend/config/scoring.yaml (re-read on every run so edits apply immediately)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from ..config import CONFIG_DIR

DEFAULT_PATH = CONFIG_DIR / "scoring.yaml"
HORIZONS: tuple[str, ...] = ("short", "medium", "long")


def load_config(source: str | Path | dict | None = None) -> dict[str, Any]:
    """Return the scoring config.

    `source` may be a dict (used as-is, handy in tests), a path, or None for
    $SCORING_CONFIG or the default backend/config/scoring.yaml.
    """
    if isinstance(source, dict):
        cfg = source
    else:
        path = Path(source or os.getenv("SCORING_CONFIG") or DEFAULT_PATH)
        with open(path, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
    for key in ("labels", "general", "horizons"):
        if key not in cfg:
            raise ValueError(f"scoring config is missing the '{key}' section")
    return cfg


def label_for(score: float, labels: dict[str, Any]) -> str:
    if score >= float(labels.get("strong_bullish", 50)):
        return "Strong Bullish"
    if score >= float(labels.get("bullish", 20)):
        return "Bullish"
    if score > float(labels.get("bearish", -20)):
        return "Neutral"
    if score > float(labels.get("strong_bearish", -50)):
        return "Bearish"
    return "Strong Bearish"
