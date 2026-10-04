"""A small, explained nudge to the ranking from your own swipes (plan 19).

ADR 0029. Local and arithmetic: no model, nothing leaves the machine (a test
checks this module imports nothing from jsa.llm). Stored scores never change:
the nudge reorders the list in memory, and only within MAX_NUDGE of a
match's own score, so it moves near-ties and can't lift a weak match over a
strong one. Nothing is filtered out because of a swipe.

Signals: a SAVE is an application the person created; a PASS is a posting
group they passed in Turbo. Each posting group counts once. An unpassed job
stops counting, and `learning_reset_at` (the Reset button) ignores everything
before it. Nothing is deleted.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from . import db

# Below this many swipes (saves + passes) nothing changes: on 2026-10-03 the
# owner had 12 saves and 0 passes, and a "preference" learned from that would
# be noise.
MIN_SIGNALS = 30
# A feature counts only once it has been seen this often among the swipes.
MIN_FEATURE_N = 5
# How far one feature can move a match, and how far all of them together can.
# Match scores are 0-1 and a strong match sits around 0.8; 0.10 at most
# reorders near-ties and can't carry a 0.5 match past a 0.8 one.
FEATURE_WEIGHT = 0.10
MAX_PER_FEATURE = 0.05
MAX_NUDGE = 0.10
# Title words that say nothing about the kind of work.
_STOP = frozenset("""a an and the of for to in at on with i ii iii iv senior junior
                     sr jr lead staff principal associate remote hybrid us usa new
                     grad entry level""".split())
RESET_KEY = "learning_reset_at"


def title_words(title: str | None) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z+#.]*", (title or "").lower())
            if w not in _STOP and len(w) > 1}


def features(job: dict[str, Any]) -> set[tuple[str, str]]:
    """What a posting is, in the terms the nudge learns from."""
    from .tailor import role_kind

    out = {("role", role_kind(job.get("title"), job.get("track")))}
    out |= {("word", w) for w in title_words(job.get("title"))}
    if job.get("company"):
        out.add(("company", str(job["company"])))
    if job.get("source_kind"):
        out.add(("source", str(job["source_kind"])))
    out.add(("remote", "remote" if job.get("remote") == "remote" else "on-site"))
    stack = job.get("tech_stack")
    if isinstance(stack, str):
        try:
            stack = json.loads(stack or "[]")
        except ValueError:
            stack = []
    for item in stack or []:
        out.add(("stack", str(item)))
    return out


def label(feature: tuple[str, str]) -> str:
    kind, value = feature
    return {"role": f"'{value}' roles", "word": f"titles with '{value}'",
            "company": f"jobs at {value}", "source": f"jobs found on {value}",
            "remote": f"{value} jobs", "stack": f"jobs asking for {value}"}[kind]


# --- settings ---------------------------------------------------------------------


def reset_at(con: sqlite3.Connection) -> str | None:
    try:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (RESET_KEY,)).fetchone()
    except sqlite3.OperationalError:          # an older tracker: no table yet
        return None
    return row["value"] if row else None


def reset(con: sqlite3.Connection) -> None:
    """Ignore every swipe so far. Nothing is deleted."""
    con.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (RESET_KEY, db.utcnow()))


def enabled(profile: dict[str, Any] | None) -> bool:
    return ((profile or {}).get("ranking") or {}).get("learn_from_swipes", True) is not False


# --- the model -----------------------------------------------------------------------


@dataclass
class Model:
    saves: int = 0
    passes: int = 0
    counts: dict[tuple[str, str], list[int]] = field(default_factory=dict)  # [saves, passes]

    @property
    def signals(self) -> int:
        return self.saves + self.passes

    @property
    def active(self) -> bool:
        return self.signals >= MIN_SIGNALS

    def contribution(self, feature: tuple[str, str]) -> float:
        s, p = self.counts.get(feature, (0, 0))
        if s + p < MIN_FEATURE_N or not self.signals:
            return 0.0
        base = (self.saves + 1) / (self.signals + 2)
        rate = (s + 1) / (s + p + 2)
        return max(-MAX_PER_FEATURE, min(MAX_PER_FEATURE, FEATURE_WEIGHT * (rate - base)))


_JOB_SQL = ("SELECT j.id, j.title, j.track, j.remote, j.tech_stack, "
            "COALESCE(j.dedup_key, 'job:' || j.id) AS grp, c.name AS company, "
            "s.kind AS source_kind FROM jobs j LEFT JOIN companies c ON c.id = j.company_id "
            "LEFT JOIN sources s ON s.id = j.source_id ")


def build(con: sqlite3.Connection) -> Model:
    since = reset_at(con) or ""
    saved = con.execute(
        _JOB_SQL + "JOIN applications a ON a.job_id = j.id WHERE a.saved_at >= ?",
        (since,)).fetchall()
    passed = con.execute(
        _JOB_SQL + "WHERE j.passed_at IS NOT NULL AND j.passed_at >= ?", (since,)).fetchall()
    model = Model()
    seen: set[str] = set()
    raw: list[tuple[set[tuple[str, str]], bool]] = []
    for rows, was_saved in ((saved, True), (passed, False)):
        for r in rows:
            if r["grp"] in seen:
                continue
            seen.add(r["grp"])
            raw.append((features(dict(r)), was_saved))
            if was_saved:
                model.saves += 1
            else:
                model.passes += 1
    # Title words only once seen 3 times; everything else as it comes.
    words = Counter(f for feats, _ in raw for f in feats if f[0] == "word")
    for feats, was_saved in raw:
        for f in feats:
            if f[0] == "word" and words[f] < 3:
                continue
            pair = model.counts.setdefault(f, [0, 0])
            pair[0 if was_saved else 1] += 1
    return model


def nudge(job: dict[str, Any], model: Model) -> tuple[float, list[str]]:
    """(delta, reasons). Zero and no reasons below MIN_SIGNALS."""
    if not model.active:
        return 0.0, []
    parts = [(model.contribution(f), f) for f in features(job)]
    parts = [(c, f) for c, f in parts if c]
    total = max(-MAX_NUDGE, min(MAX_NUDGE, sum(c for c, _ in parts)))
    reasons = []
    for c, f in sorted(parts, key=lambda t: -abs(t[0]))[:2]:
        s, p = model.counts[f]
        verb = "saved" if c > 0 else "passed"
        n = s if c > 0 else p
        reasons.append(f"{c:+.2f}: you {verb} {n} of {s + p} {label(f)}")
    return round(total, 4), reasons


def rank(con: sqlite3.Connection, rows: list[dict[str, Any]],
         profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Reorder by score + nudge. Off, or under the threshold: the same list,
    untouched. Stored scores are never written."""
    if not rows or not enabled(profile):
        return rows
    model = build(con)
    if not model.active:
        return rows
    kinds = {r["id"]: r["kind"] for r in con.execute(
        "SELECT j.id, s.kind FROM jobs j LEFT JOIN sources s ON s.id = j.source_id "
        "WHERE j.id IN (%s)" % ",".join("?" * len(rows)), [r["job_id"] for r in rows])}
    for row in rows:
        delta, reasons = nudge({**row, "source_kind": kinds.get(row["job_id"])}, model)
        row["adjusted"] = (row.get("match_score") or 0) + delta
        if reasons:
            row.setdefault("reasons", [])
            row["reasons"] = list(row["reasons"]) + [f"learned from your swipes, {r}"
                                                     for r in reasons]
    return sorted(rows, key=lambda r: -r["adjusted"])
