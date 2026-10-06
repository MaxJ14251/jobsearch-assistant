"""The model-call ledger: what every model call cost, never what it said (plan 26).

`llm.complete` hands `record()` one row per HTTP attempt, success or failure,
so retries and fallbacks are counted too. A row holds the purpose (tailor,
letter, prep...), the model, the token counts the provider reported, the
attempt's latency and whether it worked. No prompt or reply text is ever
stored; a test holds the table to its columns.

Recording never fails a model call: `llm._record` swallows any error here.
Each row opens and closes its own short connection, so the dashboard's
request threads and enrichment's worker threads can all record at once.

`jsa usage` reads it back. Dollar figures appear only when the owner sets
prices in .env, and are labelled an estimate.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import db

COLUMNS = ("purpose", "model", "prompt_tokens", "completion_tokens", "total_tokens",
           "latency_s", "attempt", "fell_back", "ok", "error_kind")
# A day with more calls than this gets a line from `jsa doctor`. Turbo's daily
# limit is 20 jobs; a resume and a letter each take one to three calls, so a
# full Turbo day is about 120. Two hundred is past any planned day.
USAGE_WARN_CALLS = 200
PRICE_IN_ENV, PRICE_OUT_ENV = "JSA_PRICE_IN_PER_MTOK", "JSA_PRICE_OUT_PER_MTOK"
GROUPINGS = ("purpose", "model", "day")


def stamp(when: datetime | None = None) -> str:
    return (when or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(row: dict[str, Any], db_path: Path | None = None) -> None:
    """One attempt. Writes only to a tracker that already exists."""
    from . import config
    path = Path(db_path or config.DB_PATH)
    if not path.exists():
        return
    con = sqlite3.connect(path, timeout=5)
    try:
        con.execute(
            f"INSERT INTO model_calls (at, {', '.join(COLUMNS)}) "
            f"VALUES (?{', ?' * len(COLUMNS)})",
            (row.get("at") or stamp(), *(_value(row, c) for c in COLUMNS)))
        con.commit()
    finally:
        con.close()


def _value(row: dict[str, Any], column: str) -> Any:
    value = row.get(column)
    if column in ("fell_back", "ok"):
        return int(bool(value))
    if column in ("prompt_tokens", "completion_tokens", "total_tokens", "attempt"):
        return int(value or 0)
    if column == "latency_s":
        return float(value or 0)
    return value


def install(db_path: Path | None = None):
    """Point `llm.recorder` here; returns what it was, to put back."""
    from . import llm
    previous = llm.recorder
    llm.recorder = (lambda row: record(row, db_path)) if db_path else record
    return previous


@dataclass
class Spent:
    calls: int = 0
    failed: int = 0
    fell_back: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    group: str = ""

    def line(self) -> str:
        text = f"{self.calls} model call(s)"
        if self.tokens_in or self.tokens_out:
            text += f", {self.tokens_in:,} tokens in / {self.tokens_out:,} out"
        if self.failed:
            text += f", {self.failed} failed"
        if self.fell_back:
            text += f", {self.fell_back} on a fallback model"
        return text


def _connect(con: sqlite3.Connection | None):
    return con if con is not None else db.connect()


def _rows(con: sqlite3.Connection, since: str, purpose: str | None = None) -> list:
    try:
        sql = "SELECT * FROM model_calls WHERE at >= ?"
        args: list[Any] = [since]
        if purpose:
            sql += " AND purpose = ?"
            args.append(purpose)
        return con.execute(sql, args).fetchall()
    except sqlite3.OperationalError:          # an older tracker: no table yet
        return []


def _add(spent: Spent, row) -> Spent:
    spent.calls += 1
    spent.failed += 0 if row["ok"] else 1
    spent.fell_back += int(row["fell_back"])
    spent.tokens_in += row["prompt_tokens"]
    spent.tokens_out += row["completion_tokens"]
    return spent


def since(started: str, purpose: str | None = None,
          con: sqlite3.Connection | None = None) -> Spent:
    own = con is None
    con = _connect(con)
    try:
        spent = Spent()
        for row in _rows(con, started, purpose):
            _add(spent, row)
        return spent
    finally:
        if own:
            con.close()


def on_day(day: date, con: sqlite3.Connection | None = None) -> Spent:
    """One local calendar day."""
    own = con is None
    con = _connect(con)
    try:
        spent = Spent(group=day.isoformat())
        for row in _rows(con, stamp(datetime.combine(day, datetime.min.time())
                                    .astimezone(timezone.utc) - timedelta(days=1))):
            if db.local_date(row["at"]) == day:
                _add(spent, row)
        return spent
    finally:
        if own:
            con.close()


@dataclass
class Usage:
    rows: list[Spent] = field(default_factory=list)
    total: Spent = field(default_factory=Spent)
    no_token_counts: bool = False
    price_in: float | None = None
    price_out: float | None = None

    def cost(self, spent: Spent) -> float | None:
        if self.price_in is None or self.price_out is None:
            return None
        return (spent.tokens_in * self.price_in + spent.tokens_out * self.price_out) / 1e6


def _price(name: str) -> float | None:
    text = (os.environ.get(name) or "").strip().lstrip("$")
    try:
        return float(text) if text else None
    except ValueError:
        return None


def usage(con: sqlite3.Connection, days: int = 7, by: str = "purpose",
          today: date | None = None) -> Usage:
    """Totals over the last `days` local days, grouped by purpose, model or day."""
    if by not in GROUPINGS:
        raise ValueError(f"group by one of {', '.join(GROUPINGS)}")
    today = today or date.today()
    first = today - timedelta(days=days - 1)
    rows = [r for r in _rows(con, stamp(datetime.combine(first, datetime.min.time())
                                        .astimezone(timezone.utc) - timedelta(days=1)))
            if (db.local_date(r["at"]) or today) >= first]
    groups: dict[str, Spent] = {}
    out = Usage(price_in=_price(PRICE_IN_ENV), price_out=_price(PRICE_OUT_ENV))
    for r in rows:
        key = (r["purpose"] if by == "purpose" else r["model"] if by == "model"
               else str(db.local_date(r["at"])))
        _add(groups.setdefault(key, Spent(group=key)), r)
        _add(out.total, r)
    out.rows = sorted(groups.values(), key=lambda s: (s.group if by == "day" else -s.calls,
                                                      s.group))
    ok = [r for r in rows if r["ok"]]
    out.no_token_counts = bool(ok) and not any(r["total_tokens"] for r in ok)
    return out
