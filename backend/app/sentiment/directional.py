"""Rule engine that applies the lexicon (lexicon.py) to one news item.

Pure Python, no downloads. Returns per-region currency-directional scores, a
global risk tone and a readable trace of every rule that fired.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from . import lexicon as lx
from .base import ScoredText, TextItem
from .tagging import Mention, find_mentions, tag_regions

_TOKEN_RE = re.compile(r"[A-Za-z&][A-Za-z&'’/-]*[A-Za-z]|[A-Za-z]|[+-]?\d+(?:[.,]\d+)?%?")
_SENT_RE = re.compile(r"(?<![A-Z])[.!?]\s+|\s+\|\s+")
_CLAUSE_RE = re.compile(
    r"[;:]\s+|,\s+|\s+[–—-]\s+|\s+(?:while|but|as|after|amid|despite|although|though|because|following|"
    r"before|ahead of|even as|and then)\s+",
    re.I,
)
_MAX_SUBJECT_WORDS = max(len(k.split()) for k in lx.SUBJECTS)
_POLICY = [(re.compile(p, re.I), w, label) for p, w, label in lx.POLICY_PATTERNS]
_STANDALONE = [(re.compile(p, re.I), w, label) for p, w, label in lx.STANDALONE_PATTERNS if w]
_RISK = [(re.compile(p, re.I), w, label, ctx) for p, w, label, ctx in lx.RISK_PATTERNS]
_SURPRISE = [(re.compile(p, re.I), kind) for p, kind in lx.SURPRISE_PATTERNS]
_RISK_ALL = {**{s: 1 for s in lx.RISK_SUBJECTS}, **{s: -1 for s in lx.RISK_INVERTED}}
_MAX_RISK_WORDS = max(len(k.split()) for k in _RISK_ALL)


@dataclass
class _Tok:
    text: str
    start: int
    end: int


@dataclass
class _DataHit:
    """Last data subject seen in a sentence, so a trailing ", more than expected" can amend it."""

    region: str
    polarity: int
    verb_dir: int
    label: str
    idx: int  # index into the contributions list


def _tokens(text: str) -> list[_Tok]:
    out = []
    for m in _TOKEN_RE.finditer(text):
        t = m.group(0).lower().replace("’", "'")
        if t.endswith("'s"):
            t = t[:-2]
        out.append(_Tok(t, m.start(), m.end()))
    return out


def _split(text: str, rx: re.Pattern[str]) -> list[tuple[int, str]]:
    """Split keeping each piece's character offset in ``text``."""
    pieces, pos = [], 0
    for m in rx.finditer(text):
        if m.start() > pos:
            pieces.append((pos, text[pos : m.start()]))
        pos = m.end()
    if pos < len(text):
        pieces.append((pos, text[pos:]))
    return pieces


def _phrases(tokens: list[_Tok], table: dict[str, int], max_words: int) -> list[tuple[int, int, str, int]]:
    """Longest-first phrase matches: (first token idx, last token idx, phrase, value)."""
    found, i = [], 0
    while i < len(tokens):
        for n in range(min(max_words, len(tokens) - i), 0, -1):
            phrase = " ".join(t.text for t in tokens[i : i + n])
            if phrase in table:
                found.append((i, i + n - 1, phrase, table[phrase]))
                i += n - 1
                break
        i += 1
    return found


def _direction(
    tokens: list[_Tok], first: int, last: int, up: set[str], down: set[str], after: int, before: int,
    blocked: set[int],
) -> tuple[int, str]:
    """Nearest direction word around tokens[first..last]. Returns (sign, word); sign 0 = none."""
    best: tuple[int, int, str] | None = None  # (distance, sign, word)
    for j in list(range(last + 1, min(len(tokens), last + 1 + after))) + list(range(max(0, first - before), first)):
        if j in blocked:
            continue
        w = tokens[j].text
        sign = 1 if w in up else -1 if w in down else 0
        if not sign:
            continue
        dist = (j - last) if j > last else (first - j) + 0.5  # ties favour the word after
        if any(tokens[k].text in lx.NEGATORS for k in range(max(0, j - 2), j)):
            sign = -sign
            w = "not " + w
        if best is None or dist < best[0]:
            best = (dist, sign, w)
    return (best[1], best[2]) if best else (0, "")


def _tok_index(tokens: list[_Tok], char_pos: int) -> int:
    for i, t in enumerate(tokens):
        if t.end > char_pos:
            return i
    return len(tokens)


class DirectionalScorer:
    """Applies the rule families; see lexicon.py for the vocabulary."""

    def score(self, item: TextItem) -> ScoredText:
        title = (item.title or "").replace("vs.", "vs").replace("’", "'").replace("‘", "'")
        summary = (item.summary or "").replace("vs.", "vs").replace("’", "'").replace("‘", "'")
        if lx.ROUNDUP_RE.search(title):
            summary = ""
        explicit = tag_regions(title, summary)
        regions = list(explicit)
        default = item.default_region
        if default and default not in regions and (item.force_default or not regions):
            regions.insert(0, default)
        primary = regions[0] if regions else None

        sums: dict[str, float] = {}
        risk_sum, risk_hits = 0.0, 0
        hits: list[str] = []
        market_context = bool(lx.MARKET_CONTEXT_RE.search(f"{title} {summary}"))
        for text, mult, cap, where in ((title, lx.TITLE_MULT, None, "title"), (summary, lx.SUMMARY_MULT, lx.SUMMARY_CAP, "summary")):
            if not text:
                continue
            contribs, r_sum, r_n = self._score_text(text, primary, hits, where, market_context)
            part: dict[str, float] = {}
            for region, value in contribs:
                part[region] = part.get(region, 0.0) + value
            for region, value in part.items():
                if cap is not None:
                    value = max(-cap, min(cap, value))
                sums[region] = sums.get(region, 0.0) + value * mult
            risk_sum += (max(-cap, min(cap, r_sum)) if cap is not None else r_sum) * mult
            risk_hits += r_n

        # A feed's home region is only a fallback context: without an explicit mention
        # keep it only when a rule fired (or the feed is that central bank's own).
        if default and default not in explicit and not item.force_default and not sums.get(default):
            regions = [r for r in regions if r != default]

        out = {r: round(math.tanh(sums.get(r, 0.0) / lx.SQUASH), 4) for r in regions}
        risk = round(math.tanh(risk_sum / lx.SQUASH), 4) if risk_hits else None
        return ScoredText(tone=0.0, regions=out, risk=risk, hits=hits)

    # ------------------------------------------------------------------ internals
    def _score_text(
        self, text: str, primary: str | None, hits: list[str], where: str, market_context: bool = True
    ) -> tuple[list[tuple[str, float]], float, int]:
        contribs: list[tuple[str, float]] = []
        risk_sum, risk_n = 0.0, 0
        for s_off, sentence in _split(text, _SENT_RE):
            s_mentions = find_mentions(sentence)
            sentence_region = s_mentions[0].region if s_mentions else primary
            last_data: _DataHit | None = None
            for c_off, clause in _split(sentence, _CLAUSE_RE):
                mentions = [m for m in s_mentions if c_off <= m.start < c_off + len(clause)]
                mentions = [Mention(m.region, m.start - c_off, m.end - c_off, m.text, m.tier, m.quote_region) for m in mentions]
                tokens = _tokens(clause)
                if not tokens:
                    continue

                def region_at(char_pos: int) -> str | None:
                    before = [m for m in mentions if m.start <= char_pos]
                    if before:
                        return before[-1].region
                    return mentions[0].region if mentions else sentence_region

                blocked: set[int] = set()
                # --- currency price action -------------------------------------
                ccy = [m for m in mentions if m.tier in ("currency", "pair")]
                last_dir = 0
                prev: Mention | None = None
                for k, m in enumerate(ccy):
                    first = _tok_index(tokens, m.start)
                    last = max(first, _tok_index(tokens, m.end - 1))
                    versus = any(tokens[j].text in lx.VERSUS_WORDS for j in range(max(0, first - 3), first))
                    if versus and last_dir and m.tier == "currency":
                        contribs.append((m.region, -last_dir * lx.W_CURRENCY))
                        hits.append(f"{where}: {m.text} is the counter-currency -> {m.region} {-last_dir:+d}")
                        continue
                    nxt = _tok_index(tokens, ccy[k + 1].start) if k + 1 < len(ccy) else len(tokens)
                    window_block = set(range(nxt, len(tokens)))
                    lead = range(max(last + 2, nxt - 2), nxt)  # adjectives of the NEXT currency ("a weaker dollar")
                    if k + 1 < len(ccy) and not any(tokens[j].text in lx.VERSUS_WORDS for j in lead):
                        window_block.update(lead)
                    sign, word = _direction(tokens, first, last, lx.CURRENCY_UP, lx.CURRENCY_DOWN, 6, 2, window_block)
                    if not sign:
                        prev = m
                        continue
                    last_dir = sign
                    if versus and prev is not None and m.tier == "currency":
                        # "US dollar vs Swiss franc holds gains": the move belongs to the first currency
                        contribs.append((prev.region, sign * lx.W_CURRENCY))
                        contribs.append((m.region, -sign * lx.W_CURRENCY))
                        hits.append(f"{where}: pair '{prev.text} vs {m.text} {word}' -> {prev.region} {sign:+d}, {m.region} {-sign:+d}")
                        prev = m
                        continue
                    prev = m
                    contribs.append((m.region, sign * lx.W_CURRENCY))
                    hits.append(f"{where}: currency move '{m.text} {word}' -> {m.region} {sign:+d}")
                    if m.tier == "pair" and m.quote_region:
                        contribs.append((m.quote_region, -sign * lx.W_CURRENCY))
                        hits.append(f"{where}: quote side of {m.text} -> {m.quote_region} {-sign:+d}")

                # --- policy language ---------------------------------------------
                policy_text = lx.NON_POLICY_RATE_RE.sub(lambda mm: "_" * len(mm.group(0)), clause)
                found: list[tuple[int, int, float, str]] = []
                for rx, w, label in _POLICY:
                    for mm in rx.finditer(policy_text):
                        found.append((mm.start(), mm.end(), w, label))
                found.sort(key=lambda f: (-abs(f[2]), -(f[1] - f[0])))
                kept: list[tuple[int, int, float, str]] = []
                for f in found:
                    if not any(f[0] < k[1] and k[0] < f[1] for k in kept):
                        kept.append(f)
                if kept:
                    keep = bool(lx.KEEP_RE.search(policy_text))
                    spans = [(k[0], k[1]) for k in kept]

                    def outside(mm: re.Match[str]) -> bool:
                        return not any(mm.start() < e and st < mm.end() for st, e in spans)

                    any_flips = sum(1 for mm in lx.FLIP_ANY_RE.finditer(policy_text) if outside(mm))
                    total, negated = 0.0, False
                    for st, _, w, label in kept:
                        if label in lx.ADJ_LABELS:
                            flip = bool(lx.ADJ_NEG_RE.search(policy_text[:st]))
                        elif keep:
                            flip = False
                        else:
                            before = sum(1 for mm in lx.FLIP_BEFORE_RE.finditer(policy_text[:st]) if outside(mm))
                            flip = (before + any_flips) % 2 == 1
                        negated = negated or flip
                        total += w * (-0.7 if flip else 1.0)
                    total = max(-1.0, min(1.0, total)) * lx.W_POLICY
                    region = region_at(min(k[0] for k in kept))
                    if region:
                        contribs.append((region, total))
                        labels = ", ".join(k[3] for k in kept)
                        hits.append(f"{where}: policy [{labels}]{' (negated)' if negated else ''} -> {region} {total:+.2f}")
                    for k in kept:
                        blocked.update(i for i, t in enumerate(tokens) if t.start < k[1] and k[0] < t.end)

                # --- standalone region terms -------------------------------------
                for rx, w, label in _STANDALONE:
                    mm = rx.search(clause)
                    if mm:
                        neg = bool(re.search(r"\b(?:avoid\w*|avert\w*|dodg\w+|escap\w+|no|not|without|exits?|out of|emerges? from)\b", clause[: mm.end()], re.I))
                        region = region_at(mm.start())
                        if region:
                            value = w * lx.W_STANDALONE * 2 * (-0.6 if neg else 1.0)
                            contribs.append((region, value))
                            hits.append(f"{where}: {label}{' (negated)' if neg else ''} -> {region} {value:+.2f}")

                # --- data subject x direction / surprise -------------------------
                surprise: str | None = None
                for rx, kind in _SURPRISE:
                    if rx.search(clause):
                        surprise = kind
                        break
                num = lx.NUMERIC_VS_RE.search(clause)
                num_sign: int | None = None
                if num:
                    a = float(num.group("a").replace(",", "."))
                    e = float(num.group("e").replace(",", "."))
                    num_sign = (a > e) - (a < e)

                subjects = _phrases(tokens, lx.SUBJECTS, _MAX_SUBJECT_WORDS)
                subject_tokens = {i for f, l, _, _ in subjects for i in range(f, l + 1)}
                for first, last, phrase, polarity in subjects:
                    if first in blocked:
                        continue
                    verb_dir, word = _direction(
                        tokens, first, last, lx.UP_WORDS, lx.DOWN_WORDS, 8, 3, blocked | subject_tokens
                    )
                    sign, weight, why = verb_dir, lx.W_DATA, word
                    if num_sign is not None:
                        sign, weight, why = num_sign, lx.W_SURPRISE, f"{num.group('a')} vs {num.group('e')} expected"
                    elif surprise == "+":
                        sign, weight, why = 1, lx.W_SURPRISE, "above expectations"
                    elif surprise == "-":
                        sign, weight, why = -1, lx.W_SURPRISE, "below expectations"
                    elif surprise == "verb" and verb_dir:
                        weight, why = lx.W_SURPRISE, f"{word}, more than expected"
                    elif surprise == "anti" and verb_dir:
                        sign, weight, why = -verb_dir, lx.W_SURPRISE, f"{word}, but less than expected"
                    region = region_at(tokens[first].start)
                    if not region:
                        continue
                    if not sign and num_sign is None:
                        continue
                    value = polarity * sign * weight
                    contribs.append((region, value))
                    hits.append(f"{where}: data '{phrase}' {why}{' [inverted subject]' if polarity < 0 else ''} -> {region} {value:+.2f}")
                    last_data = _DataHit(region, polarity, verb_dir, phrase, len(contribs) - 1)

                # a trailing clause that only carries the surprise amends the previous data hit
                if not subjects and last_data is not None and (surprise or num_sign is not None):
                    sign = (
                        num_sign if num_sign is not None
                        else 1 if surprise == "+"
                        else -1 if surprise == "-"
                        else last_data.verb_dir if surprise == "verb"
                        else -last_data.verb_dir
                    )
                    value = last_data.polarity * sign * lx.W_SURPRISE
                    contribs[last_data.idx] = (last_data.region, value)
                    hits.append(f"{where}: surprise amends '{last_data.label}' -> {last_data.region} {value:+.2f}")

                # --- global risk tone --------------------------------------------
                for rx, w, label, needs_context in _RISK:
                    if rx.search(clause) and (market_context or not needs_context):
                        neg = bool(re.search(r"\b(?:no|not|avoid\w*|avert\w*|eas(?:e|es|ed|ing)|fad(?:e|es|ed|ing)|end(?:s|ed)?|reced\w+|calm\w*|cool\w*|shrug\w*)\b", clause, re.I))
                        value = w * (-0.6 if neg else 1.0)
                        risk_sum += value
                        risk_n += 1
                        hits.append(f"{where}: risk '{label}'{' (negated)' if neg else ''} -> GLOBAL {value:+.2f}")
                for first, last, phrase, polarity in _phrases(tokens, _RISK_ALL, _MAX_RISK_WORDS):
                    sign, word = _direction(tokens, first, last, lx.CURRENCY_UP, lx.CURRENCY_DOWN, 5, 2, set())
                    if sign:
                        value = polarity * sign * 0.7
                        risk_sum += value
                        risk_n += 1
                        hits.append(f"{where}: risk '{phrase} {word}' -> GLOBAL {value:+.2f}")
        return contribs, risk_sum, risk_n
