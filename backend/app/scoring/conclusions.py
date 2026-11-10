"""Template-based written conclusions (no LLM): a headline and a short paragraph
per region built from the three horizon scores and their component breakdowns."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..regions import Region
from .config import HORIZONS

if TYPE_CHECKING:  # pragma: no cover
    from .engine import ComponentRow, HorizonScore

HORIZON_NAME = {"short": "short-term", "medium": "medium-term", "long": "long-term"}


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _driver(row: "ComponentRow", horizon: str) -> str:
    label = row.label[0].lower() + row.label[1:] if row.label[:2].upper() != row.label[:2] else row.label
    label = label.split(" (")[0]
    reading = row.raw_display.split(";")[0].strip()
    inner = f": {reading}" if reading and reading != "n/a" else ""
    return f"{label} ({HORIZON_NAME[horizon]}{inner})"


def _divergence(s: dict[str, float], cfg: dict[str, Any], bull: float, bear: float) -> str:
    short, medium, long_ = s.get("short"), s.get("medium"), s.get("long")
    if short is None or long_ is None:
        return ""
    med = medium if medium is not None else 0.0
    if short >= bull and long_ <= bear:
        return "The short-term strength looks like a bounce inside a longer-term downtrend rather than a change of trend."
    if short <= bear and long_ >= bull:
        return "The short-term weakness looks like a pullback inside a longer-term uptrend."
    if short >= bull and med >= bull and long_ >= bull:
        return "All three horizons point the same way, which adds conviction to the bullish view."
    if short <= bear and med <= bear and long_ <= bear:
        return "All three horizons point the same way, which adds conviction to the bearish view."
    if medium is not None and medium >= bull and long_ <= bear:
        return "Medium-term drivers are supportive but the structural backdrop is negative, so rallies may fade."
    if medium is not None and medium <= bear and long_ >= bull:
        return "Medium-term drivers are a drag on an otherwise supportive structural backdrop."
    values = [v for v in (short, medium, long_) if v is not None]
    if max(values) - min(values) >= float(cfg.get("divergence_spread", 40)):
        return "The horizons disagree markedly, so conviction is lower than any single score suggests."
    if all(bear < v < bull for v in values):
        return "No horizon shows a strong directional signal."
    return ""


def build_conclusion(region: Region, result: dict[str, "HorizonScore"], cfg: dict[str, Any]) -> tuple[str, str]:
    ccfg = cfg.get("conclusions", {})
    labels_cfg = cfg.get("labels", {})
    bull, bear = float(labels_cfg.get("bullish", 20)), float(labels_cfg.get("bearish", -20))
    horizons = [h for h in HORIZONS if h in result]
    have = [h for h in horizons if result[h].coverage > 0]
    ccy = region.currency

    # ---- headline
    if not have:
        return f"{ccy}: no data to score", f"There is not enough data to score {region.name} ({ccy}) yet."
    names = [result[h].label.lower() for h in have]
    if len(have) == len(horizons) and len(set(names)) == 1:
        headline = f"{ccy}: {names[0]} across all horizons"
    else:
        headline = f"{ccy}: " + ", ".join(f"{result[h].label.lower()} {HORIZON_NAME[h]}" for h in have)

    # ---- sentence 1: the three scores
    parts = [f"{result[h].label.lower()} {HORIZON_NAME[h]} ({result[h].score:+.0f})" for h in have]
    sentences = [f"{region.name} ({ccy}) screens {_join(parts)}."]

    # ---- drivers
    min_pts = float(ccfg.get("driver_min_points", 3.0))
    n_side = int(ccfg.get("drivers_each_side", 2))
    ranked = sorted(((row, h) for h in have for row in result[h].components if row.signal is not None),
                    key=lambda rh: rh[0].contribution, reverse=True)
    pos = [_driver(r, h) for r, h in ranked if r.contribution >= min_pts][:n_side]
    neg = [_driver(r, h) for r, h in reversed(ranked) if r.contribution <= -min_pts][:n_side]
    if pos:
        sentences.append(f"The main support{'s are' if len(pos) > 1 else ' is'} {_join(pos)}.")
    if neg:
        sentences.append(f"The main drag{'s are' if len(neg) > 1 else ' is'} {_join(neg)}.")
    if not pos and not neg:
        sentences.append("No single driver stands out; the inputs largely offset each other.")

    # ---- divergence between horizons
    note = _divergence({h: result[h].score for h in have}, ccfg, bull, bear)
    if note:
        sentences.append(note)

    # ---- coverage warning
    low = float(ccfg.get("low_coverage", 0.6))
    missing = [HORIZON_NAME[h] for h in horizons if result[h].coverage <= 0]
    thin = [f"{HORIZON_NAME[h]} ({result[h].coverage * 100:.0f}% of inputs)" for h in have if result[h].coverage < low]
    if thin:
        sentences.append(f"Data coverage is thin for the {_join(thin)} score{'s' if len(thin) > 1 else ''}, "
                         f"so treat {'them' if len(thin) > 1 else 'it'} with caution.")
    if missing:
        sentences.append(f"There is no data for the {_join(missing)} view.")
    return headline, " ".join(sentences)
