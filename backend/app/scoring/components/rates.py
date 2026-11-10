"""Interest-rate components: nominal yield differentials (medium), real rate
differentials and policy trajectory (long). All measured RELATIVE to peers."""
from __future__ import annotations

from ...regions import Region
from ..data import Context, Relative
from ..signals import blend, fmt_days, fmt_pp, squash
from . import ComponentResult, missing, register


def _level(ctx: Context, region: Region, key: str) -> Relative | None:
    return ctx.relative(f"level:{key}", region, lambda r: ctx.latest(r.code, key))


def _change(ctx: Context, region: Region, key: str, days: int) -> Relative | None:
    def fn(r: Region) -> float | None:
        ts = ctx.region_ts(r.code, key)
        return None if ts is None else ts.chg(days)

    return ctx.relative(f"chg:{key}:{days}", region, fn)


@register("yield_differential")
def yield_differential(ctx: Context, region: Region, params: dict) -> ComponentResult:
    days = int(params.get("change_days", 63))
    lvl_scale, chg_scale = float(params.get("level_scale", 1.5)), float(params.get("change_scale", 0.4))
    mix = params.get("mix", {})
    l2, c2 = _level(ctx, region, "YIELD_2Y"), _change(ctx, region, "YIELD_2Y", days)
    l10, c10 = _level(ctx, region, "YIELD_10Y"), _change(ctx, region, "YIELD_10Y", days)
    signal = blend([
        (squash(l2.diff, lvl_scale) if l2 else None, float(mix.get("level_2y", 0.45))),
        (squash(c2.diff, chg_scale) if c2 else None, float(mix.get("change_2y", 0.35))),
        (squash(l10.diff, lvl_scale) if l10 else None, float(mix.get("level_10y", 0.10))),
        (squash(c10.diff, chg_scale) if c10 else None, float(mix.get("change_10y", 0.10))),
    ])
    if signal is None:
        return missing("No 2Y or 10Y yield data for this region (or too few peers to compare with).")
    bits = []
    for tag, lvl, chg in (("2Y", l2, c2), ("10Y", l10, c10)):
        if lvl:
            bit = f"{tag} {fmt_pp(lvl.diff)} vs peers"
            if chg:
                bit += f", {fmt_days(days)} chg {fmt_pp(chg.diff)}"
            bits.append(bit)
        elif chg:
            bits.append(f"{tag} spread {fmt_days(days)} chg {fmt_pp(chg.diff)}")
    raw = "; ".join(bits)
    main_l, main_c, tag = (l2, c2, "2-year") if (l2 or c2) else (l10, c10, "10-year")
    sentences = []
    if main_l:
        sentences.append(f"The {tag} yield ({main_l.own:.2f}%) is {abs(main_l.diff):.2f}pp "
                         f"{'above' if main_l.diff >= 0 else 'below'} the peer average")
    if main_c:
        sentences.append(f"the spread has {'widened in its favour' if main_c.diff > 0 else 'moved against it'} "
                         f"by {abs(main_c.diff):.2f}pp over {fmt_days(days)}")
    verdict = "a carry and rate-expectations advantage" if signal > 0.1 else \
        "a rate disadvantage" if signal < -0.1 else "no clear rate advantage"
    return ComponentResult(signal, raw, f"{' and '.join(sentences)}: {verdict}.")


@register("real_rate_differential")
def real_rate_differential(ctx: Context, region: Region, params: dict) -> ComponentResult:
    scale = float(params.get("scale", 1.5))
    mix = params.get("mix", {})

    def real(key: str):
        def fn(r: Region) -> float | None:
            nominal, infl = ctx.latest(r.code, key), ctx.inflation(r.code)
            return None if nominal is None or infl is None else nominal - infl[0]

        return ctx.relative(f"real:{key}", region, fn)

    pol, y10 = real("POLICY_RATE"), real("YIELD_10Y")
    signal = blend([
        (squash(pol.diff, scale) if pol else None, float(mix.get("policy", 0.5))),
        (squash(y10.diff, scale) if y10 else None, float(mix.get("yield_10y", 0.5))),
    ])
    if signal is None:
        return missing("Needs a policy rate or 10Y yield plus an inflation series (and peers to compare with).")
    bits = []
    if pol:
        bits.append(f"real policy {pol.own:+.2f}%, {fmt_pp(pol.diff)} vs peers")
    if y10:
        bits.append(f"real 10Y {y10.own:+.2f}%, {fmt_pp(y10.diff)} vs peers")
    infl = ctx.inflation(region.code)
    verdict = "which attracts long-term capital" if signal > 0.1 else \
        "which erodes the currency's long-term appeal" if signal < -0.1 else "roughly in line with peers"
    lead = pol or y10
    assert lead is not None
    detail = (f"Inflation-adjusted rates ({'policy rate' if pol else '10Y yield'} minus "
              f"{infl[1] if infl else 'inflation'}: {lead.own:+.2f}%) are {abs(lead.diff):.2f}pp "
              f"{'above' if lead.diff >= 0 else 'below'} the peer average, {verdict}.")
    return ComponentResult(signal, "; ".join(bits), detail)


@register("policy_trajectory")
def policy_trajectory(ctx: Context, region: Region, params: dict) -> ComponentResult:
    days = int(params.get("change_days", 365))
    mix = params.get("mix", {})
    targets = ctx.general.get("inflation_target", {})
    chg = _change(ctx, region, "POLICY_RATE", days)

    def gap_fn(r: Region) -> float | None:
        infl = ctx.inflation(r.code)
        return None if infl is None else infl[0] - float(targets.get(r.code, targets.get("default", 2.0)))

    gap = ctx.relative("inflation_gap", region, gap_fn)
    signal = blend([
        (squash(chg.diff, float(params.get("change_scale", 1.0))) if chg else None, float(mix.get("rate_change", 0.6))),
        (squash(gap.diff, float(params.get("gap_scale", 1.0))) if gap else None, float(mix.get("inflation_gap", 0.4))),
    ])
    if signal is None:
        return missing("No policy-rate history or inflation data to judge the policy path.")
    bits, sentences = [], []
    if chg:
        bits.append(f"policy rate {fmt_days(days)} chg {fmt_pp(chg.own)}, {fmt_pp(chg.diff)} vs peers")
        sentences.append(f"The policy rate has moved {fmt_pp(chg.own)} over {fmt_days(days)}, "
                         f"{abs(chg.diff):.2f}pp {'more hawkish' if chg.diff >= 0 else 'more dovish'} than peers")
    if gap:
        bits.append(f"inflation gap {fmt_pp(gap.own)}, {fmt_pp(gap.diff)} vs peers")
        sentences.append(f"inflation is {fmt_pp(gap.own)} from target ({fmt_pp(gap.diff)} relative to peers), "
                         f"implying a {'hawkish' if gap.diff > 0.1 else 'dovish' if gap.diff < -0.1 else 'neutral'} bias")
    verdict = "relatively hawkish regime" if signal > 0.1 else "relatively dovish regime" if signal < -0.1 else "policy regime in line with peers"
    return ComponentResult(signal, "; ".join(bits), f"{'; '.join(sentences)}: {verdict}.")
