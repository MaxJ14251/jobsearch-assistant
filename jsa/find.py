"""Find a LinkedIn or Indeed listing on the employer's own board (Plan 21).

The operator types what the listing says (company, title, city), and this
looks for the same job where the tool IS allowed to look: the tracker, the
employer's configured board, and, for an employer with no configured board,
a few guessed public boards. LinkedIn and Indeed are never requested; their
terms forbid automated reading (ADR 0013, 0020). A listing link may be given,
and is kept as text only.

Three answers:

- **same**: the employer's posting, with its tracker number when stored.
  Only the tracker or a configured board can say this, and only with the
  same normalized title AND the same city (the `db.find_duplicate` rule: a
  wrong merge hides a job).
- **possible**: shown for the operator to check, with the reason. A title
  that overlaps enough, a city that differs or wasn't given, and anything
  from a guessed board.
- **not found**: apply on the listing site (the job page's checklist helps).

A guessed board can never say "same": a board token is not derivable from a
company name, and a wrong one can be someone else's board (companies.yaml's
header). Greenhouse names its board, so a guess there must carry the typed
company's name; Lever and Ashby don't, so a hit there is labelled for the
operator to confirm. Nothing here writes companies.yaml; a confirmed board
prints a ready-to-paste entry and `jsa verify`. See
docs/decisions/0030-finding-a-listing-on-the-employers-board.md.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from . import sources
from .config import load_sources
from .db import _norm
from .scoring import dedup_key

# Every host this module may send a request to: the boards' public APIs.
ALLOWED_HOSTS = frozenset({
    "boards-api.greenhouse.io", "api.lever.co", "api.ashbyhq.com",
    "apply.workable.com",
})
_WORKDAY_API = re.compile(r"^[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com$")
REFUSED_HOSTS = ("linkedin.com", "indeed.com", "glassdoor.com")

# Kinds with a public board this can check live. Workday is searched with
# the typed title server-side, so its detail requests stay few.
LIVE_KINDS = ("greenhouse", "lever", "ashby", "workable", "workday")
GUESS_KINDS = ("greenhouse", "lever", "ashby")
MAX_GUESSES = 3        # token guesses per kind
MAX_GUESS_REQUESTS = 9  # over all guesses, Greenhouse's name check included

# Words a company name carries that its board token often drops: "Perplexity
# AI" is `perplexity`, "Mitek Systems" is `mitek` (measured 2026-10-04 on
# companies.yaml: these four typed names missed their own rows).
TRAILING_WORDS = frozenset({
    "inc", "llc", "ltd", "corp", "corporation", "co", "company", "ai", "labs",
    "technologies", "technology", "systems", "brands", "health", "group",
    "the",
})

# Below this share of shared title words, two titles are different jobs.
# Jaccard on normalized words: "Support Engineer" vs "Senior Support
# Engineer" is 0.67 (shown); "Support Engineer" vs "Sales Engineer" is 0.33.
TITLE_OVERLAP = 0.6


class FindError(ValueError):
    """The search could not run. The message says what to type instead."""


class HostNotAllowed(RuntimeError):
    """A request to a host outside ALLOWED_HOSTS. A bug, never a fallback."""


@dataclass
class Candidate:
    name: str
    slug: str
    why: str
    company_id: int | None = None
    entries: list[dict[str, Any]] = field(default_factory=list)  # yaml boards
    # Matched only because the name starts the same ("Scale" for "Scale
    # AI"): another company is as likely, so never "same job" (review R-05).
    prefix: bool = False


@dataclass
class Hit:
    kind: str                 # "same" | "possible"
    where: str                # "tracker" | "board" | "guess"
    company: str
    title: str
    location: str
    url: str
    reason: str
    job_id: int | None = None
    status: str = ""          # saved / applied / passed, when it is in the tracker
    add_url: str = ""         # a link `jsa add` reads, when it is not stored yet


@dataclass
class Board:
    """A guessed public board that answered."""
    kind: str
    token: str
    name: str = ""            # Greenhouse only: the board's own company name
    confirm: str = ""         # what the operator must check
    yaml: str = ""            # a ready-to-paste companies.yaml entry


@dataclass
class Report:
    company: str
    title: str
    city: str
    link: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)
    boards: list[Board] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    requested: list[str] = field(default_factory=list)   # every URL asked for

    @property
    def verdict(self) -> str:
        kinds = {h.kind for h in self.hits}
        return "same" if "same" in kinds else "possible" if kinds else "not_found"

    @property
    def same(self) -> list[Hit]:
        return [h for h in self.hits if h.kind == "same"]

    @property
    def possible(self) -> list[Hit]:
        return [h for h in self.hits if h.kind == "possible"]


# --- names ------------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _core(slug: str) -> str:
    """The slug without trailing company words: perplexity-ai -> perplexity."""
    words = [w for w in slug.split("-") if w]
    while len(words) > 1 and words[-1] in TRAILING_WORDS:
        words.pop()
    while len(words) > 1 and words[0] == "the":
        words.pop(0)
    return "-".join(words)


def _prefix(a: str, b: str) -> bool:
    """One slug's words start the other's: mitek / mitek-systems-inc."""
    wa, wb = a.split("-"), b.split("-")
    short, long_ = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    return len("".join(short)) >= 3 and long_[:len(short)] == short


def company_candidates(con: sqlite3.Connection, name: str,
                       entries: list[dict[str, Any]] | None = None) -> list[Candidate]:
    """Every company the typed name could be, best first, each with why.

    From the tracker's companies and companies.yaml. Exact slug, then the
    slug without trailing words ("Perplexity AI" -> perplexity), then a
    word prefix. More than one is returned as a choice, never picked.
    """
    typed = _slug(name)
    if not typed:
        return []
    if entries is None:
        try:
            entries = load_sources()
        except Exception:  # noqa: BLE001 - no config just means no boards
            entries = []
    pool: dict[str, Candidate] = {}
    for row in con.execute("SELECT id, name, slug FROM companies"):
        pool[row["slug"]] = Candidate(row["name"], row["slug"], "", int(row["id"]))
    for entry in entries:
        slug = entry.get("slug") or _slug(entry.get("company") or "")
        if not slug:
            continue
        cand = pool.setdefault(slug, Candidate(entry.get("company") or slug, slug, ""))
        if entry.get("kind") in LIVE_KINDS and entry.get("enabled") is not False:
            cand.entries.append(entry)

    ranked: list[tuple[int, Candidate]] = []
    for cand in pool.values():
        names = {cand.slug, _slug(cand.name)}
        if typed in names:
            rank, why = 0, "same name"
        elif _core(typed) in {_core(n) for n in names}:
            rank, why = 1, "same name without " + _dropped(typed, cand)
        elif any(_prefix(_core(typed), _core(n)) for n in names):
            rank, why = 2, "name starts the same"
        else:
            continue
        cand.why, cand.prefix = why, rank == 2
        ranked.append((rank, cand))
    ranked.sort(key=lambda rc: (rc[0], not rc[1].entries, rc[1].name.lower()))
    return [c for _, c in ranked]


def _dropped(typed: str, cand: Candidate) -> str:
    extra: list[str] = []
    for slug in (typed, cand.slug, _slug(cand.name)):
        core = set(_core(slug).split("-"))
        extra += [w for w in slug.split("-") if w and w not in core and w not in extra]
    return "'" + " ".join(extra) + "'" if extra else "its ending"


# --- titles and places --------------------------------------------------------


def title_key(title: str, location: str | None = None) -> str:
    """The title as `dedup_key` folds it: place qualifiers dropped."""
    return dedup_key(0, title, location).split(":", 1)[1]


def overlap(a: str, b: str) -> float:
    wa, wb = set(a.split()), set(b.split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _place(location: Any) -> tuple[str, str]:
    """The first place's (city, state): "Austin, TX; Remote" -> ("austin", "tx")."""
    first = str(location or "").split(";")[0].split("(")[0]
    parts = [_norm(p) for p in first.split(",")]
    return parts[0], (parts[1].split() or [""])[0] if len(parts) > 1 else ""


def same_place(a: Any, b: Any) -> bool:
    """Same city, and the same state when both name one: Portland, OR is not
    Portland, ME (review R-06). A missing state on either side is allowed."""
    (city_a, state_a), (city_b, state_b) = _place(a), _place(b)
    if not city_a or city_a != city_b:
        return False
    return not (state_a and state_b) or state_a == state_b


def judge(title: str, city: str, found_title: str, found_location: str,
          *, guessed: bool = False) -> tuple[str, str] | None:
    """("same" | "possible", why), or None when it is a different job."""
    typed, theirs = title_key(title, city), title_key(found_title, found_location)
    if typed == theirs:
        if not city:
            return "possible", "same title; no city given to compare"
        if not same_place(city, found_location):
            return "possible", f"same title, but in {found_location or 'no stated place'}"
        if guessed:
            return "possible", "same title and city, on a guessed board"
        return "same", "same title and city"
    share = overlap(typed, theirs)
    if share >= TITLE_OVERLAP:
        return "possible", f"similar title ({share:.0%} of words shared)"
    return None


# --- requests -----------------------------------------------------------------


def allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ALLOWED_HOSTS or bool(_WORKDAY_API.match(host))


def _check(report: Report, url: str) -> None:
    if not allowed(url):
        raise HostNotAllowed(f"refusing to request {urlparse(url).hostname!r}")
    report.requested.append(url)


def _board_url(entry: dict[str, Any]) -> str:
    builder = sources.URL_BUILDERS.get(entry.get("kind") or "")
    return builder(entry) if builder else ""


def _guarded(report: Report):
    """Every request the fetchers make, redirects and Workday's detail pages
    included, passes the allowlist; the first URL alone was checked before."""
    def hook(request) -> None:
        url = str(request.url)
        if not allowed(url):
            raise HostNotAllowed(f"refusing to request {request.url.host!r}")
        if not report.requested or report.requested[-1] != url:
            report.requested.append(url)
    return hook


def _fetch(report: Report, entry: dict[str, Any]) -> sources.FetchResult:
    url = _board_url(entry)
    _check(report, url)
    token = sources.REQUEST_GUARD.set(_guarded(report))
    try:
        return sources.fetch(entry)
    finally:
        sources.REQUEST_GUARD.reset(token)


def _greenhouse_name(report: Report, token: str) -> str | None:
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}"
    _check(report, url)
    guard = sources.REQUEST_GUARD.set(_guarded(report))
    try:
        data = sources._get_json(url)
    except Exception:  # noqa: BLE001 - no board by that token
        return None
    finally:
        sources.REQUEST_GUARD.reset(guard)
    return str((data or {}).get("name") or "")


def add_link(kind: str, token: str, job: dict[str, Any]) -> str:
    """A link `jsa add` reads for this posting, or "" when it has none."""
    jid = str(job.get("external_id") or "")
    if kind == "greenhouse" and jid.isdigit():
        return f"https://boards.greenhouse.io/{token}/jobs/{jid}"
    if kind == "lever" and jid:
        return f"https://jobs.lever.co/{token}/{jid}"
    if kind == "ashby" and jid:
        return f"https://jobs.ashbyhq.com/{token}/{jid}"
    if kind == "workday":
        return str(job.get("url") or "")
    return ""


# --- the search -----------------------------------------------------------------


def _refuse_links(**fields: str) -> None:
    for label, value in fields.items():
        if re.search(r"https?://|www\.|\.com/", value or "", re.I):
            raise FindError(
                f"The {label} looks like a link. This never opens LinkedIn or "
                "Indeed: type the company, the job title and the city as the "
                "listing shows them.")


def _held_back(verdict: tuple[str, str] | None, prefix: bool) -> tuple[str, str] | None:
    """A company matched only by how its name starts is never "same job"."""
    if verdict and prefix and verdict[0] == "same":
        return "possible", (f"{verdict[1]}, but at a company whose name only "
                            "starts the same; check it is this employer")
    return verdict


def _tracker(con: sqlite3.Connection, report: Report, cands: list[Candidate]) -> None:
    ids = [c.company_id for c in cands if c.company_id is not None]
    if not ids:
        return
    rows = con.execute(
        "SELECT j.id, j.title, j.location, j.url, j.passed_at, j.company_id, "
        "c.name AS company, a.status FROM jobs j JOIN companies c ON c.id = j.company_id "
        "LEFT JOIN applications a ON a.job_id = j.id "
        f"WHERE j.company_id IN ({','.join('?' * len(ids))}) "
        "AND j.closed_at IS NULL AND j.archived_at IS NULL", ids).fetchall()
    prefix = {c.company_id for c in cands if c.prefix}
    for row in rows:
        verdict = _held_back(judge(report.title, report.city, row["title"],
                                   row["location"] or ""), row["company_id"] in prefix)
        if verdict is None:
            continue
        status = row["status"] or ("passed" if row["passed_at"] else "")
        report.hits.append(Hit(verdict[0], "tracker", row["company"], row["title"],
                               row["location"] or "", row["url"] or "", verdict[1],
                               job_id=int(row["id"]), status=status or ""))


def _stored(con: sqlite3.Connection, entry: dict[str, Any], job: dict[str, Any]) -> int | None:
    row = con.execute(
        "SELECT j.id FROM jobs j JOIN sources s ON s.id = j.source_id "
        "WHERE s.name = ? AND j.external_id = ?",
        (f"{entry.get('slug')}-{entry.get('kind')}", str(job.get("external_id")))).fetchone()
    return int(row["id"]) if row else None


def _live(con: sqlite3.Connection, report: Report, cands: list[Candidate]) -> None:
    """Each configured board, once, for postings newer than the last discovery."""
    seen = {h.job_id for h in report.hits}
    for cand in cands:
        for entry in cand.entries:
            entry = dict(entry)
            if entry.get("kind") == "workday":
                entry["search"] = report.title      # narrow server-side
            result = _fetch(report, entry)
            if not result.ok:
                report.notes.append(f"{cand.name}'s {entry['kind'].title()} board "
                                    f"did not answer: {result.status}")
                continue
            for job in result.jobs:
                verdict = _held_back(judge(report.title, report.city,
                                           job.get("title") or "",
                                           job.get("location") or ""), cand.prefix)
                if verdict is None:
                    continue
                job_id = _stored(con, entry, job)
                if job_id is not None and job_id in seen:
                    continue
                report.hits.append(Hit(
                    verdict[0], "board", cand.name, job.get("title") or "",
                    job.get("location") or "", job.get("url") or "",
                    verdict[1] + (" (live on the board)" if job_id is None else ""),
                    job_id=job_id,
                    add_url="" if job_id else add_link(entry["kind"], entry.get("board")
                                                       or "", job)))


def _tokens(name: str) -> list[str]:
    typed = _slug(name)
    out: list[str] = []
    for token in (_core(typed), typed, typed.replace("-", ""), _core(typed).replace("-", "")):
        if token and token not in out:
            out.append(token)
    return out[:MAX_GUESSES]


def _article(word: str) -> str:
    return ("An " if word[:1].lower() in "aeiou" else "A ") + word


def yaml_entry(company: str, kind: str, token: str) -> str:
    return (f"  - company: {company}\n    slug: {_slug(company)}\n"
            f"    kind: {kind}\n    board: {token}\n    verified: false\n")


def _guess(report: Report) -> None:
    """Guessed boards for an employer with none configured. Possible at best."""
    budget = MAX_GUESS_REQUESTS
    want = _core(_slug(report.company))
    for token in _tokens(report.company):
        for kind in GUESS_KINDS:
            if budget <= 0:
                report.notes.append("Stopped guessing boards after "
                                    f"{MAX_GUESS_REQUESTS} requests.")
                return
            board = Board(kind, token)
            if kind == "greenhouse":
                budget -= 1
                name = _greenhouse_name(report, token)
                if name is None:
                    continue
                if _core(_slug(name)) != want:
                    continue        # someone else's board: never look at its jobs
                board.name = name
                board.confirm = (f"Greenhouse names this board {name!r}; check "
                                 "it is this employer.")
                if budget <= 0:
                    continue
            else:
                board.confirm = (f"{_article(kind.title())} board named {token!r} "
                                 "exists; confirm it is this employer before trusting it.")
            budget -= 1
            result = _fetch(report, {"kind": kind, "board": token})
            if not result.ok:
                continue
            if not result.jobs:
                # Measured 2026-10-05: Ashby answers 200 with no postings for
                # a board nobody uses, so an empty board is no evidence.
                report.notes.append(f"{_article(kind.title())} board named {token!r} "
                                    "exists but lists no jobs; not suggested.")
                continue
            board.yaml = yaml_entry(report.company, kind, token)
            report.boards.append(board)
            for job in result.jobs:
                verdict = judge(report.title, report.city, job.get("title") or "",
                                job.get("location") or "", guessed=True)
                if verdict is None:
                    continue
                report.hits.append(Hit(
                    "possible", "guess", board.name or token, job.get("title") or "",
                    job.get("location") or "", job.get("url") or "",
                    f"{verdict[1]}. {board.confirm}",
                    add_url=add_link(kind, token, job)))


def find(con: sqlite3.Connection, company: str, title: str, city: str = "", *,
         link: str = "", live: bool = True,
         entries: list[dict[str, Any]] | None = None) -> Report:
    """Look for the listing's job on the employer's own board. Reads only."""
    company, title, city = (company or "").strip(), (title or "").strip(), (city or "").strip()
    if not company or not title:
        raise FindError("Type the company and the job title as the listing shows them.")
    _refuse_links(company=company, title=title, city=city)
    report = Report(company, title, city, link=(link or "").strip())
    if report.link and any(h in (urlparse(report.link).hostname or "").lower()
                           for h in REFUSED_HOSTS):
        report.notes.append("The listing link is kept as text; it is never opened.")

    report.candidates = company_candidates(con, company, entries)
    _tracker(con, report, report.candidates)
    if live:
        if any(c.entries for c in report.candidates):
            _live(con, report, report.candidates)
        else:
            _guess(report)
    order, typed = {"same": 0, "possible": 1}, title_key(title, city)
    report.hits.sort(key=lambda h: (order[h.kind], -overlap(typed, title_key(h.title, h.location)),
                                    h.where != "tracker", h.title))
    return report
