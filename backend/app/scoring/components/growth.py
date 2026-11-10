"""Growth differential (long horizon): real GDP y/y against peers, both in
level terms and relative to each economy's own 5-year norm."""
from __future__ import annotations

import numpy as np

from ...regions import Region
from ..data import Context
from ..signals import blend, fmt_pp, fmt_sigma, squash
from . import ComponentResult, missing, register


@register("growth_differential")
def growth_differential(ctx: Context, region: Region, params: dict) -> ComponentResult:
    mix = params.get("mix", {})
    n_hist = int(params.get("own_history_obs", 20))

    def own_z(r: Region) -> float | None:
        ts = ctx.region_ts(r.code, "GDP_YOY")
        if ts is None or len(ts) < 8:
            return None
        hist = ts.v[-n_hist:]
        sd = float(np.std(hist, ddof=1))
        return None if sd <= 1e-12 else float((ts.last - float(np.mean(hist))) / sd)

    lvl = ctx.relative("level:GDP_YOY", region, lambda r: ctx.latest(r.code, "GDP_YOY"))
    own = ctx.relative(f"ownz:GDP_YOY:{n_hist}", region, own_z)
    signal = blend([
        (squash(lvl.diff, float(params.get("level_scale", 1.5))) if lvl else None, float(mix.get("level", 0.6))),
        (squash(own.diff, float(params.get("own_scale", 1.0))) if own else None, float(mix.get("vs_own_history", 0.4))),
    ])
    if signal is None:
        return missing("No GDP data for this region (or too few peers to compare with).")
    bits, sentences = [], []
    if lvl:
        bits.append(f"GDP y/y {lvl.own:+.1f}%, {fmt_pp(lvl.diff, 1)} vs peers")
        sentences.append(f"Real GDP is growing {lvl.own:+.1f}% y/y, {abs(lvl.diff):.1f}pp "
                         f"{'faster' if lvl.diff >= 0 else 'slower'} than the peer average")
    if own:
        bits.append(f"vs own 5y norm {fmt_sigma(own.own)}, rel. to peers {fmt_sigma(own.diff)}")
        sentences.append(f"growth is {fmt_sigma(own.own)} versus its own 5-year norm "
                         f"({'better' if own.diff >= 0 else 'worse'} than peers on that measure)")
    verdict = "growth outperformance" if signal > 0.1 else "growth underperformance" if signal < -0.1 else "growth in line with peers"
    return ComponentResult(signal, "; ".join(bits), f"{'; '.join(sentences)}: {verdict}.")
