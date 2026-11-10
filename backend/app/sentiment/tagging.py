"""Region tagging: which currencies/economies is a piece of text about?

Data-driven: edit REGION_KEYWORDS (or add a region) and everything follows.
Each keyword is matched as a whole word. Keywords written with any upper-case
letter and at most 5 characters (``US``, ``Fed``, ``BoE``, ``EUR``) are matched
case-sensitively so that "us" or "fed up" do not tag the United States;
everything else is case-insensitive.

``strong`` keywords tag the region wherever they appear; ``weak`` keywords only
count in the title (they are too ambiguous to trust inside a long summary).
``currency`` terms are the names that can be the grammatical subject of a
price move ("the yen slides"); they are also strong tags.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

REGION_KEYWORDS: dict[str, dict[str, list[str]]] = {
    "US": {
        "currency": ["dollar", "greenback", "USD", "DXY", "dollar index", "U.S. dollar", "US dollar"],
        "strong": [
            "Fed", "FOMC", "Federal Reserve", "Powell", "Warsh", "Hassett", "Waller", "Bowman", "Jefferson",
            "Miran", "Goolsbee", "Kashkari", "Bostic", "Logan", "Musalem", "Hammack", "Schmid", "Barkin",
            "Fed's Williams", "Fed's Daly", "Fed's Cook", "Fed's Barr", "Fed's Collins", "Fed's Paulson",
            "U.S.", "US", "United States", "Treasury yields", "Treasuries", "nonfarm", "non-farm", "payrolls",
            "NFP", "fed funds", "ISM", "Wall Street", "jobless claims", "initial claims", "PCE", "Treasury Secretary",
            "Bessent",
        ],
        "weak": ["America", "American", "Washington", "Treasury"],
    },
    "EZ": {
        "currency": ["euro", "EUR", "single currency"],
        "strong": [
            "ECB", "European Central Bank", "Lagarde", "Schnabel", "de Guindos", "Nagel", "Villeroy", "Lane",
            "Cipollone", "Rehn", "Kazaks", "Holzmann", "Knot", "Simkus", "Makhlouf", "Escriva", "Panetta",
            "eurozone", "euro zone", "euro area", "euro-area", "Bundesbank", "bund", "bunds", "HICP",
            "Eurostat", "ZEW", "Ifo",
        ],
        "weak": ["Germany", "German", "France", "French", "Italy", "Italian", "Spain", "Spanish", "Europe",
                 "European", "EU", "DAX"],
    },
    "JP": {
        "currency": ["yen", "JPY"],
        "strong": ["BoJ", "BOJ", "Bank of Japan", "Ueda", "Himino", "Uchida", "Japan", "Japanese", "JGB",
                   "JGBs", "Tankan", "Takaichi", "Katayama", "Mimura"],
        "weak": ["Tokyo", "Nikkei"],
    },
    "UK": {
        "currency": ["pound", "sterling", "GBP"],
        "strong": ["BoE", "BOE", "Bank of England", "Bailey", "Lombardelli", "Breeden", "Dhingra", "Ramsden",
                   "Huw Pill", "BoE's Pill", "BOE's Pill", "BoE's Mann", "BoE's Greene", "BoE's Taylor",
                   "UK", "U.K.", "Britain", "British", "gilt", "gilts", "Reeves", "ONS", "MPC Member"],
        "weak": ["London", "FTSE", "Chancellor"],
    },
    "AU": {
        "currency": ["Aussie", "AUD", "Australian dollar", "Aussie dollar"],
        "strong": ["RBA", "Reserve Bank of Australia", "Bullock", "Hauser", "Australia", "Australian", "Chalmers"],
        "weak": ["Sydney", "ASX"],
    },
    "CA": {
        "currency": ["loonie", "CAD", "Canadian dollar"],
        "strong": ["BoC", "BOC", "Bank of Canada", "Macklem", "Canada", "Canadian", "Statistics Canada", "StatCan"],
        "weak": ["Ottawa", "TSX", "Toronto"],
    },
    "NZ": {
        "currency": ["kiwi", "NZD", "New Zealand dollar", "NZ dollar", "kiwi dollar"],
        "strong": ["RBNZ", "Reserve Bank of New Zealand", "Breman", "New Zealand", "NZ", "OCR", "Stats NZ"],
        "weak": ["Wellington", "Auckland"],
    },
    "CN": {
        "currency": ["yuan", "renminbi", "RMB", "CNY", "CNH"],
        "strong": ["PBoC", "PBOC", "People's Bank of China", "China", "Chinese", "Beijing", "Caixin", "Pan Gongsheng"],
        "weak": ["Shanghai", "Hong Kong", "Hang Seng"],
    },
    "CH": {
        "currency": ["franc", "CHF", "Swissie", "Swiss franc"],
        "strong": ["SNB", "Swiss National Bank", "Schlegel", "Tschudin", "Swiss", "Switzerland"],
        "weak": ["Zurich"],
    },
}

# Phrases that contain a region keyword but must NOT tag it ("Hong Kong dollar" is not USD).
MASK_PHRASES: list[str] = [
    "Hong Kong dollar", "Singapore dollar", "Taiwan dollar", "dollar store", "Dollar General", "Dollar Tree",
    "billion dollar", "million dollar", "trillion dollar", "pound of", "pounds of", "New York Fed survey of",
    "Swiss Re", "Swiss cheese", "Bank of America", "American Express", "American Airlines", "Lane closure",
    "euro 2028", "Euros", "eurovision", "US Open", "U.S. Open", "British Airways", "British Gas",
    "Air Canada", "Japan Airlines", "China Evergrande",
]

_CCY_CODES = {"USD": "US", "EUR": "EZ", "JPY": "JP", "GBP": "UK", "AUD": "AU", "CAD": "CA", "NZD": "NZ",
              "CNY": "CN", "CNH": "CN", "CHF": "CH"}
PAIR_RE = re.compile(r"\b(" + "|".join(_CCY_CODES) + r")\s?/?\s?(" + "|".join(_CCY_CODES) + r")\b")


# "Dollar-Yen edges higher", "euro/dollar slips": named pairs behave like EUR/USD.
_PAIR_NAMES = {"dollar": "US", "euro": "EZ", "yen": "JP", "pound": "UK", "sterling": "UK", "aussie": "AU",
               "loonie": "CA", "kiwi": "NZ", "yuan": "CN", "franc": "CH", "swissie": "CH"}
NAME_PAIR_RE = re.compile(r"(?i)\b(" + "|".join(_PAIR_NAMES) + r")\s?[-/]\s?(" + "|".join(_PAIR_NAMES) + r")\b")


@dataclass(frozen=True)
class Mention:
    region: str
    start: int
    end: int
    text: str
    tier: str  # currency | strong | weak | pair
    quote_region: str = ""  # for pairs: the quote currency's region


def _kw_regex(kw: str) -> str:
    body = re.escape(kw).replace(r"\ ", r"\s+")
    case_sensitive = len(kw) <= 5 and kw != kw.lower() or kw in ("Lane", "Mann", "Ifo", "Swiss", "Europe")
    if not case_sensitive:
        body = f"(?i:{body})"
    return rf"(?<![A-Za-z0-9]){body}(?:'s|’s)?(?![A-Za-z0-9])"


@lru_cache(maxsize=1)
def _compiled() -> tuple[list[tuple[str, str, re.Pattern[str]]], re.Pattern[str]]:
    out: list[tuple[str, str, re.Pattern[str]]] = []
    for region, tiers in REGION_KEYWORDS.items():
        for tier, kws in tiers.items():
            if kws:
                alt = "|".join(_kw_regex(k) for k in sorted(kws, key=len, reverse=True))
                out.append((region, tier, re.compile(alt)))
    mask = re.compile(
        "|".join(_kw_regex(p) for p in sorted(MASK_PHRASES, key=len, reverse=True))
        + r"|(?i:\b(?:multi-)?(?:hundred|thousand|million|billion|trillion|\d+)-dollars?\b)|(?i:\btop dollar\b)"
    )
    return out, mask


def find_mentions(text: str) -> list[Mention]:
    """All region mentions in ``text`` with character spans, longest match wins on overlap."""
    patterns, mask = _compiled()
    masked = [m.span() for m in mask.finditer(text)]
    found: list[Mention] = []
    for m in PAIR_RE.finditer(text):
        base, quote = _CCY_CODES[m.group(1)], _CCY_CODES[m.group(2)]
        if base != quote:
            found.append(Mention(base, m.start(), m.end(), m.group(0), "pair", quote))
    for m in NAME_PAIR_RE.finditer(text):
        base, quote = _PAIR_NAMES[m.group(1).lower()], _PAIR_NAMES[m.group(2).lower()]
        if base != quote:
            found.append(Mention(base, m.start(), m.end(), m.group(0), "pair", quote))
    for region, tier, rx in patterns:
        for m in rx.finditer(text):
            found.append(Mention(region, m.start(), m.end(), m.group(0), tier))
    # drop masked matches, then matches strictly inside a longer match ("dollar" in "Australian dollar")
    found = [f for f in found if not any(s <= f.start and f.end <= e for s, e in masked)]
    found.sort(key=lambda f: (-(f.end - f.start), f.start))
    kept: list[Mention] = []
    for f in found:
        if any(k.start <= f.start and f.end <= k.end and (k.end - k.start) > (f.end - f.start) for k in kept):
            continue
        if f.tier != "pair" and any(k.tier == "pair" and k.start < f.end and f.start < k.end for k in kept):
            continue
        kept.append(f)
    kept.sort(key=lambda f: f.start)
    return kept


def tag_regions(title: str, summary: str = "") -> list[str]:
    """Region codes the item is about.

    The title decides. Only when the title names no region do we fall back to
    the summary (non-weak keywords), because long summaries mention many
    countries in passing.
    """
    regions: list[str] = []

    def add(code: str) -> None:
        if code and code not in regions:
            regions.append(code)

    for m in find_mentions(title):
        add(m.region)
        add(m.quote_region)
    if not regions:
        for m in find_mentions(summary):
            if m.tier != "weak":
                add(m.region)
                add(m.quote_region)
    return regions


# Macro / FX vocabulary used to drop neutral off-topic stories ("Toyota to sell minitrucks in US").
MACRO_RE = re.compile(
    r"(?i)\b(?:central bank|monetary|interest rates?|rate (?:hike|cut|rise|decision)|inflation|deflation|cpi|ppi|gdp|"
    r"econom(?:y|ic|ist|ists)|recession|unemployment|jobless|payrolls|jobs report|labou?r market|wages?|"
    r"retail sales|pmi|manufacturing|trade (?:balance|surplus|deficit|data|war|deal)|exports?|imports?|tariffs?|"
    r"budget|fiscal|deficit|debt|bonds?|yields?|treasur(?:y|ies)|gilts?|currenc(?:y|ies)|forex|fx|exchange rate|"
    r"stimulus|consumer (?:prices|spending|confidence|sentiment)|housing|mortgage|markets?|stocks?|oil prices?)\b"
)


def is_macro_relevant(title: str, summary: str = "") -> bool:
    """True when the text is about macro/FX matters (not just a company in that country)."""
    if MACRO_RE.search(title) or MACRO_RE.search(summary[:200]):
        return True
    return any(m.tier in ("currency", "pair") for m in find_mentions(title))
