"""Pay ranges, read out of posting text. See docs/decisions/0006-*.

Deterministic on purpose. The enrichment model reads the first 6,000
characters of a posting and runs after scoring; pay ranges sit near the END
(Rocket Lab's is past character 9,000) and must be known AT scoring. A model
also costs a call per posting. So: a pattern, gated by context.

Measured on the 514-posting tracker (2026-09-17): 286 mention a dollar
figure, 234 of them a "$X - $Y" range. The rest are a mix of funding rounds
("raised our $1.5B Series F"), benefits and stipends -- and, read in full, 13
single stated wages ("$30.00/hour", "Zone 1: $98,700 USD"). So:

- A range counts with pay wording within 250 characters before it ("Pay
  Range", "salary", "base", "OTE", "USD"...) and no funding or perk wording
  right next to it.
- A single figure needs stronger evidence -- the wording immediately before
  it, or a unit like "/hour" right after -- and is read only when a posting
  has no usable range.
- The size of the figure decides the period, and must be plausible for it.
  An hourly-sized figure must also say "hour" nearby.
- Several ranges -- SpaceX posts "Level I" and "Level II", others post one per
  city -- combine to the lowest minimum and the highest maximum. Scoring ranks
  on the minimum, so a senior level cannot inflate an entry-level match.
- Annual ranges win over hourly ones when a posting gives both (one does:
  "$33.65 - $38.70 per hour ... annualized to $100,000 - $115,000 per year").

Equity, bonus and "competitive" are not pay and are not stored. A NULL
salary_min with a NULL salary_text means no pay figure was found. Amounts are
whole dollars in the posting's own period; salary_text keeps the exact words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HOURS_PER_YEAR = 2080

_NUMBER = r"(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)"
_RANGE = re.compile(
    r"\$\s?" + _NUMBER + r"\s?([kK])?"
    + r"(?:\s?(?:USD|usd))?\s*(?:-|–|—|to)\s*(?:USD\s*)?\$?\s?"
    + _NUMBER + r"\s?([kK])?"
)

# Wording that says the number is pay. Checked in the text just before the
# range, which is where "Pay Range:" sits even when a level name intervenes.
_CUE = re.compile(
    r"\b(pay|salary|salaries|compensation|base|wage|wages|ote|on-target|"
    r"earnings|hourly|annual|annually|annualized|per year|usd)\b",
    re.I,
)
CUE_WINDOW = 250
LIST_GAP = 120

# Wording that says it is not. Checked close to the range only.
_VETO = re.compile(
    r"\b(raised|raise|series|funding|valuation|revenue|arr|stipend|stipends|"
    r"reimburse\w*|allowance|fertility|401|match|donat\w*|budget)\b",
    re.I,
)
VETO_WINDOW = 45

# A range LABELLED as more than base pay. Rocket Lab states two ranges:
# "Total Compensation (base and equity) $93,275–$132,025 USD Base Salary
# $83,200–$114,400 USD", and both were combined, stretching the maximum by the
# equity (found in n22: 23 postings). Only the label directly before the range
# counts, and only these words: "equity" alone is not enough, because "...is
# eligible for equity. The base salary range is $X - $Y" is common and that
# base figure must survive. OTE is still stored as stated (decision 3).
_TOTAL_LABEL = re.compile(
    r"\b(total\s+(?:target\s+)?(?:compensation|comp|cash|rewards?)|"
    r"(?:base|salary)\s*(?:and|\+|&|plus)\s*(?:equity|stock|bonus)|"
    r"including\s+(?:equity|stock|bonus))\b[^.$]{0,25}$",
    re.I,
)
LABEL_WINDOW = 70

HOURLY_WINDOW = 90
_HOURLY = re.compile(r"(per\s+hour|/\s?h(?:ou)?r\b|an\s+hour|hourly)", re.I)

# Other evidence an hourly-sized figure is hourly, when the posting never
# says "hour" (n25; the owner's decision, 2026-09-29). Measured on the
# tracker: 69 open postings -- SpaceX's "Level 1: $23.00 - $27.00", Vast's
# "Pay Range: California $38.36-$54.45 USD" -- were refused for that alone.
# - Cents written out: salaries are not quoted to the cent.
# - Wording that says THIS ROLE is paid by the hour: "a non-exempt position",
#   "eligible for overtime pay", a shift differential. Not "non-exempt" or
#   "overtime" anywhere: Vast's benefits paragraph ("vacation for non-exempt
#   staff") and "willingness to work overtime" appear in its salaried
#   postings too -- 39 and 18 of 1,311 with annual pay. These phrasings
#   appear in none of them.
# Size alone is still not enough.
_CENTS = re.compile(r"\$\s?\d{1,3}\.\d{2}\b")
_HOURLY_WORK = re.compile(
    r"\bnon-?exempt\s+(?:\w+\s+)?(?:position|role|job)\b"
    r"|\beligible\s+for\s+overtime\b|\bshift\s+differential|\bper\s+shift\b", re.I)

ANNUAL_BOUNDS = (20_000, 1_000_000)
HOURLY_BOUNDS = (10, 500)


@dataclass(frozen=True)
class Salary:
    minimum: int
    maximum: int
    period: str            # "year" or "hour"
    text: str              # the words it was read from, for a human to check
    currency: str = "USD"
    source: str = "text"   # "text": read from the description; "field": the board's own pay data

    def annual_minimum(self) -> int:
        return self.minimum * HOURS_PER_YEAR if self.period == "hour" else self.minimum

    def annual_maximum(self) -> int:
        return self.maximum * HOURS_PER_YEAR if self.period == "hour" else self.maximum

    def label(self) -> str:
        unit = "/hr" if self.period == "hour" else "/yr"
        if self.minimum == self.maximum:
            return f"${self.minimum:,}{unit}"
        return f"${self.minimum:,}–${self.maximum:,}{unit}"


def _amount(digits: str, k: str | None) -> int:
    # Whole dollars. Hourly cents are rounded; salary_text keeps the exact
    # figures for anyone who needs them.
    value = float(digits.replace(",", ""))
    return round(value * 1000 if k else value)


def _candidates(text: str):
    last_accepted_end = -10**9
    hourly_work = bool(_HOURLY_WORK.search(text))
    for m in _RANGE.finditer(text):
        # A list of ranges ("... for Maryland $84,500 - $144,000 for D.C.
        # $96,500 - $164,000 ...") carries its pay wording only at the top. A
        # range that follows an accepted one closely belongs to the same list.
        # Found in the hand-check: job 507's third city was dropped.
        continued = m.start() - last_accepted_end <= LIST_GAP
        low = _amount(m.group(1), m.group(2))
        high = _amount(m.group(3), m.group(4) or m.group(2))
        # "$150-$195k": the k written once applies to both ends.
        if m.group(4) and not m.group(2) and low < 1000:
            low *= 1000
        before = text[max(0, m.start() - CUE_WINDOW):m.start()]
        near = text[max(0, m.start() - VETO_WINDOW):min(len(text), m.end() + VETO_WINDOW)]
        after = text[m.end():m.end() + 40]
        if not (continued or _CUE.search(before) or _CUE.search(after)):
            continue
        if _VETO.search(near):
            continue
        if _TOTAL_LABEL.search(text[max(0, m.start() - LABEL_WINDOW):m.start()]):
            continue
        if high < low:
            continue
        # The size of the figures decides the period. An hourly-sized range
        # must also say "hour" nearby: "$30 - $40" alone could be anything.
        # The look-back is wide because "the good faith hourly rate estimate
        # for this role is Zone 1: $21.01" puts the word 70 characters away.
        if ANNUAL_BOUNDS[0] <= low and high <= ANNUAL_BOUNDS[1]:
            period = "year"
        elif HOURLY_BOUNDS[0] <= low and high <= HOURLY_BOUNDS[1]:
            if not (_HOURLY.search(after) or _HOURLY.search(
                    text[max(0, m.start() - HOURLY_WINDOW):m.start()])
                    or _CENTS.search(m.group(0)) or hourly_work):
                continue
            period = "hour"
        else:
            continue
        # A "range" spanning more than 4x is a parse of two unrelated figures.
        if high > low * 4:
            continue
        last_accepted_end = m.end()
        yield low, high, period, _snippet(text, m.start(), m.end())


# A single figure is pay only with stronger evidence than a range needs: the
# wording must sit right before it, or a pay unit right after it. The first
# design ignored single figures entirely, on a sample of eight that happened to
# be funding rounds. The full list had 13 postings stating pay that way:
# "$30.00/hour", "Zone 1: $98,700 USD", "On Target Earnings: $125,000".
_SINGLE = re.compile(r"\$\s?" + _NUMBER + r"\s?([kKmMbB])?(?![\d,])")
_SINGLE_CUE = re.compile(
    r"\b(pay|salary|compensation|wage|rate|earnings|ote|starting at|zone \d)\b"
    r"[^$.]{0,40}$",
    re.I,
)
_UNIT_AFTER = re.compile(
    r"^\s?(?:usd)?\s?(?:/\s?h(?:ou)?r\b|per\s+hour|an\s+hour|/\s?year|"
    r"per\s+year|annually|a\s+year)",
    re.I,
)
_SINGLE_VETO = re.compile(
    _VETO.pattern[:-3] + r"|bonus|commission|variable|sign|signing|quota|"
    r"equity|incentive|relocation|home office)\b",
    re.I,
)
SINGLE_CUE_WINDOW = 70
SINGLE_VETO_AFTER = 12


def _singles(text: str, taken: list[tuple[int, int]]):
    last_end, last_period = -10**9, None
    hourly_work = bool(_HOURLY_WORK.search(text))
    for m in _SINGLE.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        if m.group(2) and m.group(2).lower() in "mb":
            continue                      # "$125M raised", "$1.5B Series F"
        value = _amount(m.group(1), m.group(2))
        before = text[max(0, m.start() - SINGLE_CUE_WINDOW):m.start()]
        after = text[m.end():m.end() + 25]
        # Short after-window: "$52,500 per year), plus variable compensation"
        # must not veto the base figure before it.
        near = text[max(0, m.start() - VETO_WINDOW):m.end() + SINGLE_VETO_AFTER]
        # "Zone 1: $28.85 USD Applicable for: CA, CO, ... Zone 2: $26.93 USD"
        # is one list; the second figure is as far from "hourly" as the list
        # is long. Found in the full audit: four postings lost their Zone 2.
        continued = m.start() - last_end <= LIST_GAP
        if not (continued or _SINGLE_CUE.search(before) or _UNIT_AFTER.search(after)):
            continue
        if _SINGLE_VETO.search(near):
            continue
        if ANNUAL_BOUNDS[0] <= value <= ANNUAL_BOUNDS[1]:
            period = "year"
        elif HOURLY_BOUNDS[0] <= value <= HOURLY_BOUNDS[1]:
            hourly_nearby = (_HOURLY.search(after) or _HOURLY.search(
                text[max(0, m.start() - HOURLY_WINDOW):m.start()]))
            if not (hourly_nearby or (continued and last_period == "hour")
                    or _CENTS.search(m.group(0)) or hourly_work):
                continue
            period = "hour"
        else:
            continue
        last_end, last_period = m.end(), period
        yield value, value, period, _snippet(text, m.start(), m.end())


def _snippet(text: str, start: int, end: int) -> str:
    begin = max(0, start - 60)
    head = text[begin:start]
    if begin and " " in head:
        head = head.split(" ", 1)[1]      # begin on a whole word
    return " ".join((head + text[start:end + 20]).split())


def extract(text: str | None) -> Salary | None:
    """The posting's pay, or None when it states none we can trust.

    Ranges are preferred. Single figures are read only when no range is.
    """
    text = text or ""
    found = list(_candidates(text))
    if not found:
        taken = [(m.start(), m.end()) for m in _RANGE.finditer(text)]
        found = list(_singles(text, taken))
    if not found:
        return None
    annual = [c for c in found if c[1] and c[2] == "year"]
    chosen = annual or found
    period = chosen[0][2]
    chosen = [c for c in chosen if c[2] == period]
    text_out = chosen[0][3]
    distinct = {(c[0], c[1]) for c in chosen}
    if len(distinct) > 1:
        text_out += f" (+{len(distinct) - 1} more range(s) combined)"
    return Salary(
        minimum=min(c[0] for c in chosen),
        maximum=max(c[1] for c in chosen),
        period=period,
        text=text_out[:300],
    )


# --- Pay the boards publish as data (n22) ------------------------------------
# Ashby, Greenhouse and Lever return pay as structured fields when asked, and
# put it nowhere in the description text: measured 2026-09-28, 358 of 463
# Ashby postings said "salary" or "compensation" with no figure beside it.
# A field is the employer's declared range, so it wins over reading the text.
# The rules are the text parser's: base pay only, a year or an hour, plausible
# for its period, several ranges combined.

# Components and range titles that are not base pay. Matched on a whole word.
_NOT_BASE = re.compile(
    r"\b(equity|stock|bonus|commission|ote|on-target|sign[- ]?on|quota|"
    r"variable|incentive|relocation)\b", re.I)


def _plausible(low: int, high: int, period: str) -> bool:
    lo, hi = ANNUAL_BOUNDS if period == "year" else HOURLY_BOUNDS
    return lo <= low <= high <= hi


def _money(low: int, high: int, period: str, currency: str) -> str:
    unit = "/yr" if period == "year" else "/hr"
    if currency == "USD":
        fig = f"${low:,}" if low == high else f"${low:,}-${high:,}"
    else:
        fig = f"{low:,}" if low == high else f"{low:,}-{high:,}"
        fig += f" {currency}"
    return fig + unit


def _combine(parts: list[tuple[int, int, str, str]], board: str) -> Salary | None:
    """(low, high, period, currency) ranges -> one Salary, or None.

    USD ranges win over others (the floor and the ranking compare dollars);
    annual over hourly, as in the text parser; then the lowest minimum and the
    highest maximum (ADR 0006 decision 2).
    """
    parts = [p for p in parts if p[2] in ("year", "hour") and _plausible(*p[:3])]
    if not parts:
        return None
    usd = [p for p in parts if p[3] == "USD"]
    parts = usd or [p for p in parts if p[3] == parts[0][3]]
    parts = [p for p in parts if p[2] == "year"] or parts
    low, high = min(p[0] for p in parts), max(p[1] for p in parts)
    period, currency = parts[0][2], parts[0][3]
    text = f"{board} pay field: {_money(low, high, period, currency)}"
    distinct = {(p[0], p[1]) for p in parts}
    if len(distinct) > 1:
        text += f" ({len(distinct)} ranges combined)"
    return Salary(low, high, period, text, currency, "field")


def _pair(low, high) -> tuple[int, int] | None:
    low = low if low is not None else high
    high = high if high is not None else low
    if low is None:
        return None
    return int(round(float(low))), int(round(float(high)))


def from_ashby(compensation: dict | None) -> Salary | None:
    """Ashby's `compensation` (posting API, includeCompensation=true).

    Salary components only: EquityCashValue, Bonus and Commission sit in the
    same list. Interval is "1 YEAR" or "1 HOUR"; anything else is left unknown.
    """
    if not compensation:
        return None
    components = [c for tier in compensation.get("compensationTiers") or []
                  for c in tier.get("components") or []]
    components = components or compensation.get("summaryComponents") or []
    parts = []
    for c in components:
        if c.get("compensationType") != "Salary":
            continue
        period = {"1 YEAR": "year", "1 HOUR": "hour"}.get(
            str(c.get("interval") or "").upper())
        pair = _pair(c.get("minValue"), c.get("maxValue"))
        if period and pair:
            parts.append((*pair, period, str(c.get("currencyCode") or "USD").upper()))
    return _combine(parts, "Ashby")


def from_greenhouse(ranges: list | None) -> Salary | None:
    """Greenhouse's `pay_input_ranges` (job board API, pay_transparency=true).

    In cents, and with no interval field: the period is in the range's own
    title ("Hourly Pay Range (CA Only)") or, failing that, in the size of the
    figure -- and an hourly-sized figure that does not say "hour" is refused,
    exactly as ADR 0006 refuses it in text.
    """
    parts = []
    for r in ranges or []:
        title = str(r.get("title") or "")
        if _NOT_BASE.search(title):
            continue
        lo, hi = r.get("min_cents"), r.get("max_cents")
        pair = _pair(lo / 100 if lo is not None else None,
                     hi / 100 if hi is not None else None)
        if not pair:
            continue
        if re.search(r"\bhour", title, re.I):
            period = "hour"
        elif pair[1] >= ANNUAL_BOUNDS[0]:
            period = "year"
        else:
            continue
        parts.append((*pair, period, str(r.get("currency_type") or "USD").upper()))
    return _combine(parts, "Greenhouse")


def from_lever(salary_range: dict | None) -> Salary | None:
    """Lever's `salaryRange` {min, max, currency, interval}."""
    if not salary_range:
        return None
    period = {"per-year-salary": "year", "per-hour-wage": "hour"}.get(
        str(salary_range.get("interval") or ""))
    pair = _pair(salary_range.get("min"), salary_range.get("max"))
    if not period or not pair:
        return None
    return _combine([(*pair, period, str(salary_range.get("currency") or "USD").upper())],
                    "Lever")


def columns(salary: Salary | None) -> dict[str, object]:
    """The jobs-table columns for a result. None clears them all."""
    if salary is None:
        return {"salary_min": None, "salary_max": None, "salary_period": None,
                "salary_text": None, "salary_currency": None, "salary_source": None}
    return {"salary_min": salary.minimum, "salary_max": salary.maximum,
            "salary_period": salary.period, "salary_text": salary.text,
            "salary_currency": salary.currency, "salary_source": salary.source}


def from_row(row) -> Salary | None:
    """Stored pay, for COMPARING: the floor and the pay ranking.

    Only dollars compare with dollars. A figure in another currency is
    stored and shown, and is unknown here -- which the floor never rejects
    (ADR 0001 decision 4) and the ranking does not reward.
    """
    get = row.get if hasattr(row, "get") else (lambda k: row[k] if k in row.keys() else None)
    low, high = get("salary_min"), get("salary_max")
    if low is None or high is None:
        return None
    currency = (get("salary_currency") or "USD").upper()
    if currency != "USD":
        return None
    return Salary(int(low), int(high), get("salary_period") or "year",
                  get("salary_text") or "", currency, get("salary_source") or "text")
