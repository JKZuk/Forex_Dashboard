"""News and central-bank tone for the region's currency (recency-weighted)."""
from __future__ import annotations

import numpy as np

from ...regions import Region
from ..data import Context
from ..signals import squash
from . import ComponentResult, missing, register


def weighted_tone(ctx: Context, region_code: str, lookback: float, half_life: float,
                  cb_weight: float = 1.0) -> tuple[float, float, int, int] | None:
    """(weighted mean score, effective weight, n items, n central-bank items)."""
    got = ctx.news(region_code, lookback)
    if got is None:
        return None
    age, score, is_cb = got
    w = np.power(0.5, age / half_life) * np.where(is_cb, cb_weight, 1.0)
    total = float(w.sum())
    if total <= 0:
        return None
    return float((w * score).sum() / total), total, int(len(score)), int(is_cb.sum())


@register("news_sentiment")
def news_sentiment(ctx: Context, region: Region, params: dict) -> ComponentResult:
    lookback = float(params.get("lookback_days", 7))
    tone = weighted_tone(ctx, region.code, lookback, float(params.get("half_life_days", 2)),
                         float(params.get("central_bank_weight", 2.5)))
    if tone is None:
        return missing(f"No news items tagged for {region.name} in the last {int(lookback)} days.")
    mean, eff_w, n, n_cb = tone
    shrunk = mean * eff_w / (eff_w + float(params.get("shrink_items", 3.0)))
    signal = squash(shrunk, float(params.get("scale", 0.25)))
    raw = f"tone {mean:+.2f} from {n} items, {n_cb} central bank"
    word = "positive" if signal > 0.1 else "negative" if signal < -0.1 else "balanced"
    detail = (f"Recency-weighted tone of {n} news items over {int(lookback)} days is {word} for {region.currency} "
              f"({mean:+.2f} on a -1..+1 scale; central-bank communications count "
              f"{params.get('central_bank_weight', 2.5)}x).")
    return ComponentResult(signal, raw, detail)
