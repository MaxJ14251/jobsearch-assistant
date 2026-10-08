"""What a company's postings ask for in education (plan 32, ADR 0032).

Built from the postings in the tracker, which are the ones that matched the
person's search, not the employer's whole board; the wording says so. It is
what postings ASK for, never who gets hired (`degree.CAVEAT`). Reads only.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

from . import db
from .degree import CAVEAT, LABELS, LEVELS

# Below this many postings a share means little: counts only (plan 13's rule).
MIN_FOR_SHARES = 10
# Levels a person without a bachelor's can apply to as written.
OPEN_LEVELS = ("none", "associate", "bachelors_or_equiv")


@dataclass
class Profile:
    company_id: int
    company: str
    n: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    certs: list[tuple[str, int]] = field(default_factory=list)   # top 5
    certs_any: int = 0                    # postings naming at least one
    unreadable: int = 0                   # too short to read (posting.thin)
    as_of: str | None = None
    source: str = "tracker"               # or "board" (plan 34's snapshot)

    @property
    def too_few(self) -> bool:
        return self.n < MIN_FOR_SHARES

    def share(self, *levels: str) -> float | None:
        if self.too_few or not self.n:
            return None
        return sum(self.counts.get(lv, 0) for lv in levels) / self.n

    @property
    def open_share(self) -> float | None:
        return self.share(*OPEN_LEVELS)

    @property
    def certs_share(self) -> float | None:
        return None if self.too_few or not self.n else self.certs_any / self.n

    def line(self) -> str:
        """One sentence, e.g. "SpaceX, 412 postings: 58% bachelor's required, ..."."""
        scope = ("open postings in your tracker" if self.source == "tracker"
                 else "open postings on its job board")
        head = f"{self.company}, {self.n} {scope}"
        short = (f" {self.unreadable} more too short to read." if self.unreadable else "")
        if not self.n:
            return f"{self.company}: no open postings with enough text to read.{short}"
        parts = []
        for level in reversed(LEVELS):
            count = self.counts.get(level, 0)
            if not count:
                continue
            amount = f"{count}" if self.too_few else _pct(count, self.n)
            parts.append(f"{amount} {LABELS[level]}")
        if self.certs_any:
            parts.append((f"{self.certs_any}" if self.too_few else
                          _pct(self.certs_any, self.n)) + " name a certification")
        tail = " (too few to compare)" if self.too_few else ""
        when = f" As of {db.local_date(self.as_of)}." if self.as_of else ""
        return f"{head}: " + ", ".join(parts) + f"{tail}.{short}{when}"


def _pct(count: int, n: int) -> str:
    share = round(100 * count / n)
    return "<1%" if count and share == 0 else f"{share}%"


def _profile(company_id: int, company: str, rows, as_of) -> Profile:
    """`rows`: degree_level, certs_named and description. A posting too short
    to read says nothing about degrees, so it is counted apart, not as "no
    degree mentioned" (Snap's postings carry no text at all)."""
    from .posting import prose_words, THIN_WORDS
    profile = Profile(company_id=company_id, company=company, as_of=as_of)
    counts: Counter = Counter()
    certs: Counter = Counter()
    for row in rows:
        if row["degree_level"] is None:
            continue
        if prose_words(row["description"]) < THIN_WORDS:
            profile.unreadable += 1
            continue
        profile.n += 1
        counts[row["degree_level"]] += 1
        named = json.loads(row["certs_named"] or "[]")
        certs.update(named)
        profile.certs_any += bool(named)
    profile.counts = dict(counts)
    profile.certs = certs.most_common(5)
    return profile


_OPEN = "j.closed_at IS NULL AND j.archived_at IS NULL"


def company_profile(con: sqlite3.Connection, company_id: int) -> Profile | None:
    company = con.execute("SELECT name FROM companies WHERE id = ?", (company_id,)).fetchone()
    if company is None:
        return None
    rows = con.execute(f"SELECT j.degree_level, j.certs_named, j.description FROM jobs j "
                       f"WHERE j.company_id = ? AND {_OPEN}", (company_id,)).fetchall()
    as_of = con.execute("SELECT MAX(last_polled_at) AS at FROM sources WHERE company_id = ?",
                        (company_id,)).fetchone()["at"]
    return _profile(company_id, company["name"], rows, as_of)


def all_profiles(con: sqlite3.Connection, minimum: int = MIN_FOR_SHARES,
                 sort: str = "open") -> list[Profile]:
    """Every company with at least `minimum` open postings, sorted by the
    share open to people without a bachelor's ("open") or naming a
    certification ("certs"), highest first."""
    ids = [r["company_id"] for r in con.execute(
        f"SELECT j.company_id, COUNT(*) AS n FROM jobs j WHERE {_OPEN} "
        "AND j.degree_level IS NOT NULL GROUP BY j.company_id HAVING n >= ?", (minimum,))]
    profiles = [p for p in (company_profile(con, i) for i in ids)
                if p is not None and p.n >= minimum]
    key = (lambda p: p.certs_share or 0) if sort == "certs" else (lambda p: p.open_share or 0)
    profiles.sort(key=lambda p: (-key(p), -p.n, p.company))
    return profiles


__all__ = ["CAVEAT", "MIN_FOR_SHARES", "OPEN_LEVELS", "Profile", "all_profiles",
           "company_profile"]
