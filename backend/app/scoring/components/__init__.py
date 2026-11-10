"""Scoring components.

A component is a function

    compute(ctx: Context, region: Region, params: dict) -> ComponentResult

registered under a `kind` name. scoring.yaml decides which kinds are used in
which horizon, with what weight and parameters, so the same implementation can
serve several horizons (e.g. price momentum with different lookbacks).

To add a component: write the function in a module of this package, decorate
it with @register("my_kind"), import the module below, and reference the kind
from scoring.yaml.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ...regions import Region
from ..data import Context


@dataclass
class ComponentResult:
    signal: float | None  # -1..+1 (+ = bullish for the currency); None = no data
    raw: str = ""  # human-readable raw reading
    detail: str = ""  # one sentence: why bullish / bearish
    weight_mult: float = 1.0  # multiplies the configured weight for this region
    applicable: bool = True  # False = component does not exist for this region


NO_DATA = "No data available for this component."


def missing(detail: str = NO_DATA, weight_mult: float = 1.0) -> ComponentResult:
    return ComponentResult(None, "n/a", detail, weight_mult)


ComponentFn = Callable[[Context, Region, dict], ComponentResult]
REGISTRY: dict[str, ComponentFn] = {}


def register(kind: str) -> Callable[[ComponentFn], ComponentFn]:
    def deco(fn: ComponentFn) -> ComponentFn:
        REGISTRY[kind] = fn
        return fn

    return deco


from . import commodity, growth, momentum, news, positioning, rates, risk, surprise, trade  # noqa: E402,F401
