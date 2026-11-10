"""Transparent financial lexicon + rules for CURRENCY-DIRECTIONAL sentiment.

Generic tone and currency direction are different things: "Fed signals rate
cuts" reads positive but is bearish for the dollar. This module scores the
direction with four explainable rule families, applied clause by clause:

1. POLICY patterns   hawkish (+) / dovish (-) central-bank language.
2. SUBJECT x MOVE    an economic subject near a direction word. Each subject has
                     a polarity: +1 when "up" is currency-bullish (inflation,
                     GDP, jobs, yields), -1 for inverted subjects (unemployment,
                     jobless claims, deficit). Surprise phrases ("less than
                     expected", "0.3% vs 0.2% expected") override the verb.
3. CURRENCY moves    "dollar slides", "EUR/USD jumps", "pound falls against
                     the euro" (price action; lower weight than policy/data).
4. RISK tone         risk-on / risk-off words -> region "GLOBAL".

Negators and expectation "faders" ("no", "rules out", "bets fade", "in no
hurry to") flip a hit. Everything here is plain data: tune freely.
"""
from __future__ import annotations

import re

# --- weights of the rule families (relative evidence strength) -------------
W_POLICY = 1.0
W_DATA = 0.8
W_SURPRISE = 1.0  # data explicitly compared with expectations
W_CURRENCY = 0.55
W_STANDALONE = 0.5
TITLE_MULT = 1.5  # a hit in the headline counts more than one in the summary
SQUASH = 1.6  # score = tanh(sum / SQUASH)

# --- 1. policy patterns: (regex, signed strength, label) --------------------
_RATE = r"(?:interest[- ]rates?|rates?|borrowing costs|cash rate|bank rate|policy rate|key rate|OCR|fed funds(?: rate)?|repo rate|RRR|reserve requirement(?: ratio)?)"
_BP = r"(?:\d+\s?(?:bps?|basis[- ]points?)|quarter[- ]point|half[- ]point|jumbo|bumper|outsized|double)"
_UPV = r"(?:hik\w+|rais\w+|lift\w*|increas\w+|tighten\w*)"
_DNV = r"(?:cut(?:s|ting)?|lower\w*|slash\w*|reduc\w+|trim\w*|loosen\w*|eas(?:e|es|ed|ing))"

POLICY_PATTERNS: list[tuple[str, float, str]] = [
    # hawkish
    (rf"\b{_UPV}\s+(?:\w+\s+){{0,3}}?{_RATE}\b", 1.0, "raises rates"),
    (rf"\b{_RATE}\s+(?:was |were |is |are |be |been )?(?:hik\w+|rais\w+|lift\w+|increas\w+)\b", 1.0, "rate raised"),
    (r"\b(?:rate|interest[- ]rate)[- ](?:hikes?|rises?|increases?|lift-?off)\b", 1.0, "rate hike"),
    (rf"\b{_BP}[- ](?:rate )?(?:hike|increase|rise)s?\b", 1.0, "bp hike"),
    (rf"\b(?:hikes?|hiked|hiking|raises?|raised)\s+(?:by\s+)?{_BP}", 1.0, "hikes by bp"),
    (r"(?<!tax )(?<!price )(?<!tariff )(?<!fare )(?<!wage )(?<!pay )(?<!dividend )(?<!fee )(?<!duty )\bhikes?\b", 0.7, "hike"),
    (r"\b(?:to|will|would|could|should|may|might|must|likely) (?:hike|tighten)\b", 0.8, "to hike"),
    (r"\b(?:policy|monetary|further|more|additional|Fed|ECB|BoE|BOE|BoJ|BOJ|RBA|BoC|RBNZ|SNB|PBOC|central[- ]bank)\s+tightening\b|\btightening (?:bets|expectations|hopes|prospects)\b|\btighten(?:s|ed|ing)?\s+(?:monetary\s+)?policy\b|\btightening (?:cycle|bias|path)\b", 0.9, "tightening"),
    (r"\bhawkish\b|\bhawks?\b", 0.9, "hawkish"),
    (r"\bhigher[- ]for[- ]longer\b|\brestrictive for (?:longer|some time)\b|\bkeep (?:policy|rates) restrictive\b|\bremain restrictive\b", 0.8, "higher for longer"),
    (r"\b(?:quantitative tightening|balance[- ]sheet (?:runoff|reduction))\b", 0.4, "QT"),
    (r"\binflation (?:is |remains |still )?(?:too high|above target|well above|a (?:problem|concern|worry)|worrying)\b|\b(?:upside|upward) (?:risks?|pressures?) (?:to|on) inflation\b|\binflation(?:ary)? (?:risks?|pressures?) (?:are |remain )?(?:rising|building|mounting|elevated|persist\w*|intensif\w+|tilted to the upside)\b|\binflation expectations (?:are |remain )?(?:worrying|rising|unanchored|de-anchor\w+|too high|elevated)\b", 0.8, "inflation worry"),
    (r"\b(?:yen|franc|yuan)[- ]buying intervention\b|\bintervene[sd]? to (?:support|prop up|defend)\b", 0.7, "supportive FX intervention"),
    (r"\b(?:credit[- ]rating|rating|outlook)\s+(?:is\s+|was\s+)?upgrad\w+|\bupgrad\w+\s+(?:\w+\s+){0,3}?(?:credit[- ]rating|rating|outlook to positive)\b", 0.6, "rating upgrade"),
    # dovish
    (rf"\b{_DNV}\s+(?:\w+\s+){{0,3}}?{_RATE}\b", -1.0, "cuts rates"),
    (rf"\b{_RATE}\s+(?:was |were |is |are |be |been )?(?:cut|lowered|slashed|reduced|trimmed)\b", -1.0, "rate cut (passive)"),
    (r"\b(?:rate|interest[- ]rate)[- ](?:cuts?|reductions?|decreases?)\b", -1.0, "rate cut"),
    (rf"\b{_BP}[- ](?:rate )?(?:cut|reduction|decrease)s?\b", -1.0, "bp cut"),
    (rf"\bcuts?\s+(?:by\s+|of\s+)?{_BP}", -1.0, "cuts by bp"),
    (r"\b(?:policy|monetary|further|more|additional|Fed|ECB|BoE|BOE|BoJ|BOJ|RBA|BoC|RBNZ|SNB|PBOC|central[- ]bank)\s+(?:easing|loosening|accommodation)\b|\beasing (?:bets|expectations|hopes|prospects)\b|\beas(?:e|es|ed|ing)\s+(?:monetary\s+)?policy\b|\beasing (?:cycle|bias|path)\b|\blower rates\b", -0.9, "easing"),
    (r"\bdovish\b|\bdoves?\b", -0.9, "dovish"),
    (r"\b(?:to|will|would|could|should|may|might|must|likely) cut\b(?=\s*$|\s+(?:rates?|interest|policy|again|further|by|in|at|this|next|soon|sooner|before|if|when|more|faster|twice|once|aggressively|gradually))", -0.8, "to cut"),
    (r"\b(?:quantitative easing|QE|bond[- ]buying|asset purchases?(?! facility)|yield[- ]curve control)\b", -0.5, "QE"),
    (r"\b(?:monetary )?stimulus\b", -0.35, "stimulus"),
    (r"\bnegative (?:interest )?rates\b", -0.7, "negative rates"),
    (r"\b(?:credit[- ]rating|rating|outlook)\s+(?:is\s+|was\s+)?(?:downgrad\w+|cut)|\bdowngrad\w+\s+(?:\w+\s+){0,3}?(?:credit[- ]rating|rating|outlook)\b|\b(?:cuts?|lowers?)\s+(?:\w+\s+){0,3}?credit[- ]rating\b", -0.7, "rating downgrade"),
    (r"\b(?:sell|selling|sold|weaken)\s+(?:the\s+)?(?:franc|yen|yuan|currency)\b|\bintervene[sd]? to (?:weaken|curb|cap)\b|\b(?:currency|franc|yen) (?:is |remains )?(?:overvalued|highly valued|too strong)\b", -0.6, "weakening FX intervention"),
    (r"\b(?:deflation(?:ary)?|disinflation(?:ary)?)\b", -0.5, "deflation"),
]

# Negators / faders flip a policy hit ("no rush to cut" is hawkish).
# FLIP_BEFORE_RE only counts when it precedes the policy phrase in the clause
# ("pauses rate hikes"); FLIP_ANY_RE counts anywhere ("rate-cut bets fade").
FLIP_BEFORE_RE = re.compile(
    r"\b(?:no|not|n't|never|without|neither|nor|rules? out|ruling out|rejects?|rejected|dismiss\w*|"
    r"push(?:es|ed|ing)? back|pushback|no (?:rush|hurry|need|urgency)|in no (?:rush|hurry)|hold(?:s|ing)? off|"
    r"slower pace of|end of|ends?|ended|ending|halt\w*|paus\w+|stop\w*|done with|finished|opposes?|opposed|"
    r"resist\w*|wary of|cautio\w+ (?:on|about|over)|patien\w+|against|less|little|low|lower|reduc\w+|"
    r"skip\w*|avoid\w*|refrain\w*|hesita\w+|reluctan\w+)\b",
    re.I,
)
FLIP_ANY_RE = re.compile(
    r"\b(?:unlikely|ruled out|premature|too (?:early|soon)|delay\w*|postpon\w+|shelv\w+|fewer|less likely|"
    r"fad(?:e|es|ed|ing)|reced\w+|par(?:e|es|ed|ing)|unwind\w*|unwound|scal(?:e|es|ed|ing) back|dash\w*|"
    r"dim(?:s|med|ming)?|doubts?|in doubt|cloud\w*|cool(?:s|ed|ing)? (?:on|bets|expectations|hopes)|"
    r"off the table|paused|halted|stopped|evaporat\w+|slashed bets|trim(?:s|med|ming)? bets)\b",
    re.I,
)
# ...except double negatives that keep the sign ("refuses to rule out hikes").
KEEP_RE = re.compile(
    r"\b(?:no longer|not|n't|never|cannot|refus\w+ to|declin\w+ to|unwilling to|won't|wouldn't|doesn't|didn't|"
    r"hasn't|haven't|can't)\s+rul\w+(?:\s+\w+){0,2}?\s+out\b|\bnot if but when\b",
    re.I,
)
# Adjective-style hits are only flipped by a negation directly in front ("not hawkish").
ADJ_LABELS = {"hawkish", "dovish"}
ADJ_NEG_RE = re.compile(r"\b(?:not|n't|less|no longer|hardly|far from)\s+(?:so |as |very |that |particularly |overly |too )?$", re.I)

# --- 2. subjects and direction words ----------------------------------------
# polarity +1: a HIGHER reading is bullish for the currency; -1: inverted.
SUBJECTS: dict[str, int] = {
    # inflation
    "inflation": 1, "cpi": 1, "core cpi": 1, "ppi": 1, "pce": 1, "core pce": 1, "hicp": 1,
    "consumer prices": 1, "producer prices": 1, "import prices": 1, "price pressures": 1, "price growth": 1,
    "price index": 1, "wholesale prices": 1, "factory-gate prices": 1, "inflation expectations": 1,
    "wages": 1, "wage growth": 1, "pay growth": 1, "earnings growth": 1, "average earnings": 1,
    "hourly earnings": 1, "labor costs": 1, "labour costs": 1, "house prices": 1, "home prices": 1,
    # growth / activity
    "gdp": 1, "growth": 1, "economy": 1, "economic growth": 1, "economic activity": 1, 
    "retail sales": 1, "consumer spending": 1, "household spending": 1, "consumption": 1,
    "pmi": 1, "pmis": 1, "ism": 1, "manufacturing": 1, "factory activity": 1, "services activity": 1,
    "services sector": 1, "business activity": 1, "industrial production": 1, "industrial output": 1,
    "factory output": 1, "factory orders": 1, "durable goods": 1, "durable goods orders": 1, 
    "machinery orders": 1, "business confidence": 1, "consumer confidence": 1, "consumer sentiment": 1,
    "business sentiment": 1, "confidence": 1, "sentiment": 1, "tankan": 1, "ifo": 1, "zew": 1,
    "housing starts": 1, "home sales": 1, "building permits": 1, "business investment": 1,
    "productivity": 1, "credit growth": 1, 
    # labour (normal polarity)
    "employment": 1, "jobs": 1, "job growth": 1, "jobs growth": 1, "payrolls": 1, "nonfarm payrolls": 1,
    "non-farm payrolls": 1, "nfp": 1, "hiring": 1, "job openings": 1, "jolts": 1, "labor market": 1,
    "labour market": 1, "job market": 1, "jobs market": 1, "jobs report": 1, "employment change": 1,
    "private payrolls": 1, "adp": 1, "vacancies": 1,
    # external
    "exports": 1, "trade surplus": 1, "surplus": 1, "current account": 1, "current account surplus": 1,
    "trade balance": 1, "foreign reserves": 1,
    # yields
    "yields": 1, "yield": 1, "treasury yields": 1, "bond yields": 1, "gilt yields": 1, "jgb yields": 1,
    "bund yields": 1, "rate expectations": 1, "rate differential": 1,
    # inverted subjects: up = bearish
    "unemployment": -1, "unemployment rate": -1, "jobless rate": -1, "jobless claims": -1,
    "initial claims": -1, "continuing claims": -1, "unemployment claims": -1, "jobless": -1, "claims": -1,
    "claimant count": -1, "layoffs": -1, "job cuts": -1, "job losses": -1, "redundancies": -1,
    "deficit": -1, "trade deficit": -1, "budget deficit": -1, "fiscal deficit": -1, "current account deficit": -1,
    "trade gap": -1, "borrowing": -1, "government borrowing": -1, "public debt": -1, "debt burden": -1,
    "bankruptcies": -1, "insolvencies": -1, "defaults": -1, "delinquencies": -1, "recession risk": -1,
    "recession risks": -1, "recession fears": -1, "recession odds": -1, "slack": -1, "capital outflows": -1,
    "outflows": -1, "political risk": -1, "fiscal worries": -1, "fiscal concerns": -1, "fiscal fears": -1,
}

UP_WORDS = set("""
rise rises rose risen rising increase increases increased increasing jump jumps jumped jumping surge surges
surged surging climb climbs climbed climbing accelerate accelerates accelerated accelerating gain gains gained
gaining grow grows grew grown growing expand expands expanded expanding rebound rebounds rebounded rebounding
recover recovers recovered recovering recovery improve improves improved improving improvement soar soars
soared soaring spike spikes spiked spiking rally rallies rallied rallying strengthen strengthens strengthened
strengthening quicken quickens quickened quickening heat heats heating reaccelerate reaccelerates reaccelerated
hot hotter hottest high higher highest strong stronger strongest robust solid resilient firm firmer firmed
upbeat sticky stubborn persistent elevated brisk buoyant healthy tight tighter booming boom booms boomed
beat beats beating top tops topped topping exceed exceeds exceeded exceeding outpace outpaces outpaced
widen widens widened widening swell swells swelled balloon balloons ballooned up upward upside uptick
advance advances advanced advancing pickup bounce bounces bounced bouncing better stabilise stabilises stabilize
stabilizes adds added add creates created peak record-high faster overshoot overshoots hotter-than-expected
stronger-than-expected better-than-expected higher-than-expected above-forecast upswing revive revives revived
""".split())

DOWN_WORDS = set("""
fall falls fell fallen falling drop drops dropped dropping decline declines declined declining slump slumps
slumped slumping plunge plunges plunged plunging tumble tumbles tumbled tumbling sink sinks sank sunk sinking
slide slides slid sliding slip slips slipped slipping dip dips dipped dipping decrease decreases decreased
slow slows slowed slowing slowdown ease eases eased easing cool cools cooled cooling cooler moderate moderates
moderated moderating weaken weakens weakened weakening weak weaker weakest soft softer softest soften softens
softened softening contract contracts contracted contracting contraction shrink shrinks shrank shrunk shrinking
miss misses missed missing disappoint disappoints disappointed disappointing sluggish stall stalls stalled
stalling stagnate stagnates stagnated stagnant stagnation deteriorate deteriorates deteriorated deteriorating
worsen worsens worsened worsening worse low lower lowest down downward downside downturn downbeat subdued
tepid lackluster lacklustre anemic anaemic fragile falter falters faltered faltering fade fades faded fading
retreat retreats retreated retreating narrow narrows narrowed narrowing collapse collapses collapsed crater
craters cratered wane wanes waned waning tank tanks tanked sheds shed lost loses lose losses plummets plummet
plummeted undershoot undershoots flat flatlines flatlined slack slacken slackens gloomy grim bleak dismal
weaker-than-expected softer-than-expected lower-than-expected worse-than-expected below-forecast cooler-than-expected
struggle struggles struggled struggling stumble stumbles stumbled sag sags sagged halve halves halved
""".split())

# "rate" compounds that are NOT the policy rate (masked before policy matching).
NON_POLICY_RATE_RE = re.compile(
    r"\b(?:unemployment|jobless|inflation|mortgage|exchange|growth|savings?|tax|participation|vacancy|default|"
    r"annual|annualized|annualised|monthly|yearly|swap|fixed|variable|home loan|employment|birth|"
    r"delinquency|occupancy|freight|fx|currency|forex)[- ]rates?\b|\brates? of\b",
    re.I,
)

# Words that flip the next direction word ("fails to rebound", "no growth").
NEGATORS = set("no not n't never without fails failed fail failing unlikely barely hardly isn't doesn't didn't won't "
               "wasn't aren't cannot can't little zero lack lacks avoids avoid avoided averts averted dodges dodged".split())

# Phrases comparing the outcome with expectations. sign: +1 = above, -1 = below,
# "verb" = same direction as the verb ("more than expected"), "anti" = opposite.
_EXP = r"(?:expect(?:ed|ations)?|forecasts?|estimates?|consensus|predicted|anticipated|thought|feared|projected|economists'? \w+)"
SURPRISE_PATTERNS: list[tuple[str, str]] = [
    (rf"\b(?:more|faster|quicker)[- ]than[- ]{_EXP}", "verb"),
    (rf"\b(?:less|slower|smaller)[- ]than[- ]{_EXP}", "anti"),
    (rf"\b(?:higher|hotter|stronger|better|above|firmer|bigger|larger)[- ]than[- ]{_EXP}", "+"),
    (rf"\b(?:lower|cooler|softer|weaker|worse|below|smaller)[- ]than[- ]{_EXP}", "-"),
    (rf"\b(?:beat|beats|beating|tops?|topped|exceed\w*|above|overshoot\w*|surpass\w*|smash\w+)\s+(?:\w+\s+){{0,2}}?{_EXP}", "+"),
    (rf"\b(?:miss|misses|missed|missing|below|short of|undershoot\w*|lags?|lagged|trails?)\s+(?:\w+\s+){{0,2}}?{_EXP}", "-"),
    (r"\bunexpectedly\b|\bsurpris\w+\b|\bshock\b", "verb"),
]
# "US core CPI +0.3% vs +0.2% expected", "claims 194K vs 200K estimate"
NUMERIC_VS_RE = re.compile(
    r"(?P<a>[+-]?\d+(?:[.,]\d+)?)\s?(?:%|k|m|b|bn|mln|pp)?(?:\s?[mqy]/[mqy])?\s+(?:vs\.?|versus|v)\s+"
    r"(?P<e>[+-]?\d+(?:[.,]\d+)?)\s?(?:%|k|m|b|bn|mln|pp)?(?:\s?[mqy]/[mqy])?\s+"
    r"(?:expected|estimate[ds]?|est\.?|exp\.?|forecast|consensus|eyed)",
    re.I,
)

# --- 3. currency price action ------------------------------------------------
CURRENCY_UP = UP_WORDS - {"hot", "hotter", "hottest", "sticky", "stubborn", "persistent", "elevated", "tight",
                          "tighter", "adds", "added", "add", "creates", "created", "widen", "widens", "widened",
                          "widening", "better", "healthy", "faster", "up", "upside", "upward"} | {
    "appreciates", "appreciate", "appreciated", "appreciating", "bid", "outperforms", "outperform", "supported",
    "underpinned", "buoyed", "boosted", "lifted", "bullish", "highs", "peak", "firms"}
CURRENCY_DOWN = DOWN_WORDS - {"miss", "misses", "missed", "missing", "narrow", "narrows", "narrowed", "narrowing",
                              "cool", "cools", "cooled", "cooling", "cooler", "moderate", "moderates",
                              "sheds", "shed", "slack", "slow", "slows", "slowed", "slowing", "slowdown"} | {
    "depreciates", "depreciate", "depreciated", "depreciating", "offered", "underperforms", "underperform",
    "pressured", "weighed", "battered", "hammered", "dented", "sell-off", "selloff", "bearish", "dumped",
    "sold", "lows", "trough", "pressure", "weakness", "lags", "lagging", "lagged", "underperforming"}
VERSUS_WORDS = {"against", "vs", "vs.", "versus", "to", "per"}

# --- standalone (no subject needed) region-level terms ------------------------
STANDALONE_PATTERNS: list[tuple[str, float, str]] = [
    (r"\b(?:enters?|entered|slips? into|falls? into|in|into|heading for|tips? into|technical) (?:a )?recession\b", -0.8, "recession"),
    (r"\bstagflation\b", -0.5, "stagflation"),
    (r"\b(?:government )?shutdown\b", -0.4, "shutdown"),
    (r"\b(?:debt|fiscal|budget|banking|political|currency) crisis\b", -0.7, "crisis"),
    (r"\bcapital flight\b|\bsovereign default\b", -0.8, "capital flight"),
    (r"\bsafe[- ]haven (?:demand|flows?|bid|buying)\b", 0.0, "haven demand"),  # handled via risk tone
]

# --- 4. global risk tone ------------------------------------------------------
RISK_SUBJECTS = {"stocks", "equities", "wall street", "s&p 500", "s&p", "nasdaq", "dow", "markets",
                 "stock market", "stock markets", "global stocks", "world stocks", "nikkei", "ftse", "dax",
                 "stoxx", "stock futures", "risk appetite", "risk sentiment", "risk assets",
                 "tech stocks", "hang seng", "asx", "market sentiment", "asian shares",
                 "european shares", "asian stocks", "european stocks", "us stocks", "emerging markets"}
RISK_INVERTED = {"vix", "volatility", "credit spreads", "spreads", "fear gauge", "haven demand"}
# (regex, strength, label, needs_market_context). Geopolitical words only count
# when the text also talks about markets, otherwise any war story would be "risk-off".
RISK_PATTERNS: list[tuple[str, float, str, bool]] = [
    (r"\brisk[- ]off\b|\brisk aversion\b|\bflight to (?:safety|quality)\b|\bsafe[- ]haven (?:demand|flows?|bid|buying|rush)\b", -1.0, "risk-off", False),
    (r"\b(?:sell-?off|market rout|rout|meltdown|bloodbath|panic|turmoil|contagion|crash(?:es|ed)?|bear market|correction territory|market stress|jitters|capitulation)\b", -0.8, "market stress", True),
    (r"(?<!turf )(?<!price )(?<!bidding )(?<!culture )(?<!talent )\b(?:war(?! chest)|invasion|airstrikes?|missile strikes?|military escalation|escalat\w+|geopolitical (?:tensions?|risks?)|trade war|sanctions|banking crisis|financial crisis|debt crisis|default fears|blockade)\b", -0.6, "geopolitical / crisis", True),
    (r"\b(?:new|fresh|higher|more|steep|sweeping|threatens?|imposes?|slaps?|hikes?) tariffs?\b|\btariff (?:threats?|hikes?|escalation)\b", -0.5, "tariffs", True),
    (r"\brisk[- ]on\b|\brisk appetite (?:returns|improves|recovers|rises)\b|\brelief rally\b|\beverything rally\b", 1.0, "risk-on", False),
    (r"\b(?:record highs?|all-time highs?|bull market|melt-up|soft landing|goldilocks)\b", 0.6, "bullish markets", True),
    (r"\b(?:ceasefire|truce|peace deal|peace talks|de-?escalat\w+|trade deal|trade truce|tariff (?:relief|cuts?|pause|rollback|exemptions?)|deal reached|shutdown ends)\b", 0.6, "de-escalation", True),
]
MARKET_CONTEXT_RE = re.compile(
    r"\b(?:stocks?|equit(?:y|ies)|markets?|investors?|traders?|wall street|s&p|nasdaq|dow|nikkei|ftse|dax|stoxx|"
    r"yields?|bonds?|treasur(?:y|ies)|oil|crude|brent|gold|dollar|yen|euro|franc|currenc(?:y|ies)|forex|fx|"
    r"safe[- ]haven|risk appetite|risk sentiment|vix|volatility|futures|rall(?:y|ies|ied)|sell-?off|econom(?:y|ic))\b",
    re.I,
)
# Titles of roundups: their summaries are a list of unrelated headlines -> score the title only.
ROUNDUP_RE = re.compile(
    r"\b(?:news wrap|wrap:|week ahead|weekly|daily report|morning bid|preview|recap|round-?up|live updates|"
    r"what to watch|things to (?:know|watch)|calendar|newsletter|podcast|market talk|briefing|at a glance)\b",
    re.I,
)
SUMMARY_MULT = 0.6  # summary evidence counts less than the headline...
SUMMARY_CAP = 1.2  # ...and its total per region is capped before weighting

# --- generic tone words (lexicon engine's "tone"; not currency-directional) ---
TONE_POSITIVE = set("""
gain gains gained rise rises rose surge surges surged rally rallies rallied rebound rebounds recover recovers
recovery growth grow grows improve improves improved improvement strong stronger robust solid upbeat optimism
optimistic boost boosts boosted record beat beats positive profit profits upgrade upgraded confidence resilient
support supports boom relief progress deal agreement breakthrough success win wins healthy stabilise stabilize
ease eases eased calm upside better best high higher jump jumps jumped climbs climbed soar soars soared
""".split())
TONE_NEGATIVE = set("""
fall falls fell drop drops dropped slump slumps slumped plunge plunges plunged tumble tumbles tumbled sink sinks
loss losses lose loses lost decline declines declined weak weaker weakness slow slows slowdown recession crisis
fear fears worry worries worried concern concerns risk risks threat threatens warn warns warning warned cut cuts
miss misses missed disappoint disappoints disappointing negative downgrade downgraded default bankruptcy layoffs
jobless unemployment deficit inflation tariffs tariff war conflict turmoil panic crash sell-off selloff rout
slide slides slid pressure pressures uncertain uncertainty volatile volatility trouble troubled struggle
struggles hit hits hurt hurts damage worst worse low lower shrink shrinks contract contracts contraction stall
stalls sanctions strike shutdown collapse collapses fraud lawsuit sued probe scandal
""".split())
