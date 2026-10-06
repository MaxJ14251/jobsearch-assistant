"""Local review dashboard.

Binds to 127.0.0.1 only. This database holds a real job search — names of
companies applied to, dates, rejection notes — and the generated documents
carry full contact details. None of it should be reachable from the network.

Server-rendered with Jinja2 and no build step: it runs on the dependencies
already in requirements.txt. Approve and reject go through `jsa.approvals`, the
same path the CLI uses, so the human-approval guarantee is identical here.
Tailoring goes through `jsa.drafting`, the same path `jsa tailor` uses.

Two guards beyond the loopback bind, because a loopback server is still
reachable from the browser that visits other sites:

- Host header check. A hostile page can point its own domain at 127.0.0.1
  (DNS rebinding) and then read responses as same-origin. Its requests still
  carry its own hostname, so anything but a loopback name is refused.
- A per-process token on every form. Any page can make the browser POST to
  127.0.0.1; it cannot read the token, so it cannot forge an approval.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, RedirectResponse)
from jinja2 import DictLoader, Environment

from .. import approvals, db, review
from ..prep import INTERVIEW_ROUNDS

HOST = "127.0.0.1"          # never 0.0.0.0
MAX_PICKED = 500            # cards one opened map bubble may ask for
# Stages where the job page shows the apply-by-hand checklist (plan 22).
APPLY_BY_HAND_STAGES = ("saved", "drafting", "ready")
PORT = 8765

DOCX_TYPE = ("application/vnd.openxmlformats-officedocument."
             "wordprocessingml.document")

# The page templates, one file each under templates/ (plan 24). Loaded once,
# into the same names the pages extend and import ("base", "card", ...).
TEMPLATE_DIR = Path(__file__).with_name("templates")
TEMPLATES = {p.stem: p.read_text(encoding="utf-8")
             for p in sorted(TEMPLATE_DIR.glob("*.html"))}
BASE, CARD, MATCHES, JOB = (TEMPLATES[k] for k in ("base", "card", "matches", "job"))
PREP, PIPELINE, REVIEW, PREVIEW = (TEMPLATES[k] for k in ("prep", "pipeline", "review",
                                                         "preview"))
ADD, FIND, IMPORT, IMPORTED = (TEMPLATES[k] for k in ("add", "find", "import", "imported"))
TURBO, SETUP = TEMPLATES["turbo"], TEMPLATES["setup"]


env = Environment(
    loader=DictLoader(TEMPLATES),
    # Always on. select_autoescape(["html"]) keys on the template NAME, and
    # these are named "base", "job"... so it was silently off, and a job
    # description from a third-party board rendered as live HTML.
    autoescape=True,
)
env.filters["localtime"] = db.local_time


def safe_url(url: Any) -> str:
    """A link from stored data, only when it is http(s). A feed is third-party
    input, and a `javascript:` URL in an href runs on the dashboard's own
    origin when clicked (review R-09); anything else becomes a dead link."""
    text = str(url or "").strip()
    return text if re.match(r"(?i)https?://", text) else ""


env.filters["safe_url"] = safe_url
env.filters["localdate"] = lambda stamp: str(db.local_date(stamp) or stamp or "")


# How far the slider goes. Below five miles a radius is a postcode, not a
# commute; above three hundred it is a move, and `jsa.config.parse_radius`
# says the same thing to a profile.
RADIUS_MIN, RADIUS_MAX = 5, 300


def _radius(typed: str, anywhere: str) -> float | None:
    """Miles, or None for the whole country.

    Empty means nationwide, which is what n18's links and the "Show them
    anyway" link say. A value outside the slider's range is clamped rather
    than refused: it came from a URL somebody typed, and the honest response
    to "within 5000 miles" is the largest radius there is.
    """
    if anywhere:
        return None
    text = (typed or "").strip()
    if not text:
        return None
    try:
        miles = float(text)
    except ValueError:
        return None
    return min(float(RADIUS_MAX), max(float(RADIUS_MIN), miles))


def _map_note(view) -> str:
    """What the map is not showing, in words. Nothing is dropped in silence."""
    places_shown = len(view.bubbles)
    if view.kind == "local":
        bits = [f"{view.shown} posting(s) in {places_shown} place(s) on this map"]
        if view.off_map:
            bits.append(f"{view.off_map} further out than it reaches")
    else:
        bits = [f"{view.shown} posting(s) in {places_shown} place(s) "
                "across the lower 48"]
        if view.off_map:
            bits.append(f"{view.off_map} in Alaska, Hawaii or Puerto Rico, "
                        "which this map does not draw")
    if view.remote:
        bits.append(f"{view.remote} remote, with no distance to draw")
    if view.unplaced:
        bits.append(f"{view.unplaced} naming a place this could not find")
    return "; ".join(bits) + "."


def _origin(typed: str, profile: dict[str, Any] | None = None):
    """(Place, what to show in the box, a problem to report).

    Typed text wins; otherwise the profile's own origin, so the box is
    pre-filled with where the operator already said they are. The profile is
    passed in rather than loaded here: the app takes a profile_loader, and
    reading the real one behind its back is how a test passes while the
    feature is broken for anybody with a different profile.
    """
    from .. import places

    typed = (typed or "").strip()
    if typed:
        found = places.origin(typed)
        if found is None:
            return None, typed, (
                f"{typed!r} is not a ZIP code or a US town this recognises, "
                "so the radius is off. Try a ZIP, or \"City, ST\".")
        return found, typed, ""
    try:
        from ..config import Preferences

        found = Preferences.from_profile(profile or {}).home()
    except Exception:  # noqa: BLE001 - a missing profile is not an error here
        found = None
    return found, str(found) if found else "", ""


def _profile_radius(profile: dict[str, Any] | None) -> float:
    """How far the operator said they would go, or the shipped default."""
    from ..config import DEFAULT_RADIUS_MILES, Preferences

    try:
        return Preferences.from_profile(profile or {}).radius_miles
    except Exception:  # noqa: BLE001 - a bad profile is not a reason to 500
        return DEFAULT_RADIUS_MILES


# How many cards one employer may take on the Matches page, so a board that
# posts four hundred roles cannot fill it.
PER_COMPANY = 3


def _per_company(rows, cap: int):
    """The first `cap` rows per company, order kept."""
    seen: dict[str, int] = {}
    out = []
    for row in rows:
        company = row.get("company") or ""
        if seen.get(company, 0) < cap:
            out.append(row)
            seen[company] = seen.get(company, 0) + 1
    return out


def _copies(con, rows) -> dict[str, list[tuple[str, str]]]:
    """Every copy's (location, remote) for the folded cards among `rows`.

    A card on the Matches page can stand for several postings of one req in
    different cities. Its own row is whichever scored best from the
    profile's home, which is not necessarily the one near the place somebody
    just typed into the box.
    """
    keys = sorted({r["dedup_key"] for r in rows
                   if r.get("dedup_key") and (r.get("variant_count") or 1) > 1})
    out: dict[str, list[tuple[str, str]]] = {}
    for start in range(0, len(keys), 500):          # SQLite's parameter limit
        chunk = keys[start:start + 500]
        for row in con.execute(
                "SELECT j.dedup_key, j.location, j.remote FROM jobs j "
                "WHERE j.archived_at IS NULL AND j.closed_at IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.job_id = j.id) "
                f"AND j.dedup_key IN ({','.join('?' * len(chunk))})", chunk):
            out.setdefault(row["dedup_key"], []).append(
                (row["location"] or "", row["remote"] or ""))
    return out


def _by_distance(rows, origin, radius: float | None, copies=None):
    """Filter to what is within the radius. Returns (rows, hidden, unplaced).

    A remote posting always passes: it has no distance, and hiding it behind
    a radius would be hiding the jobs that are open to everybody. A posting
    whose location cannot be placed is hidden when a radius is set -- but
    counted separately and named on the page, because "unknown" is not
    "far away".

    A folded card is judged by its NEAREST copy (`copies`, from _copies):
    "Deployed Engineer" with copies in Atlanta and Boston is near somebody
    in Boston even when the card's own row is the Atlanta one. A remote copy
    makes the whole card remote.

    The comparison is on the exact distance and only the DISPLAY is rounded.
    It used to compare the rounded number, so a job 25.4 miles away passed a
    25-mile radius while the map (which compares exactly) drew it outside
    the circle -- the one disagreement n19 said could not happen.
    """
    from .. import places

    for row in rows:
        row["miles"] = row["exact_miles"] = None
        row["via_copy"] = False
        row["any_remote"] = (row.get("remote") or "") == "remote"
        spots = [(row.get("location") or "", False)]
        for location, remote in (copies or {}).get(row.get("dedup_key"), []):
            row["any_remote"] = row["any_remote"] or remote == "remote"
            spots.append((location, True))
        if origin is None:
            continue
        best = None
        for location, is_copy in spots:
            distance = places.nearest(origin, location)
            if distance is not None and (best is None or distance < best[0]):
                best = (distance, is_copy and location != spots[0][0])
        if best is not None:
            row["exact_miles"], row["via_copy"] = best
            row["miles"] = round(best[0])
    if radius is None or origin is None:
        return rows, 0, 0

    kept, hidden, unplaced = [], 0, 0
    for row in rows:
        if row["any_remote"] or (
                row["exact_miles"] is not None and row["exact_miles"] <= radius):
            kept.append(row)
            continue
        hidden += 1
        if row["exact_miles"] is None:
            unplaced += 1
    return kept, hidden, unplaced


# The four colours on the Matches map (n21). A card with no application is
# "new"; a live application is grouped by how far along it is. Closed ones
# (approvals.CLOSED) are never on this page. The pill on a card still says
# the real stage -- "offer", "phone screen" -- the group is only its colour.
STATUS_GROUP = {
    "saved": "saved", "drafting": "saved", "ready": "saved",
    "applied": "applied",
    "phone_screen": "interview", "technical": "interview",
    "onsite": "interview", "offer": "interview",
}


def _annual(row: dict[str, Any]) -> tuple[int | None, int | None]:
    """The posting's pay as yearly dollars, or (None, None) when it states none.

    Hourly figures are multiplied out the same way jsa.salary does, so the
    dashboard's pay ranges compare like with like.
    """
    from ..salary import HOURS_PER_YEAR

    low, high = row.get("salary_min"), row.get("salary_max")
    if low is None and high is None:
        return None, None
    # Dollars compare with dollars; a figure in another currency is not
    # averaged into a USD median (n22).
    if (row.get("salary_currency") or "USD").upper() != "USD":
        return None, None
    low = low if low is not None else high
    high = high if high is not None else low
    if row.get("salary_period") == "hour":
        return low * HOURS_PER_YEAR, high * HOURS_PER_YEAR
    return low, high


def _pipeline_rows(con: sqlite3.Connection, where: list[str],
                   params: dict[str, Any]) -> list[dict[str, Any]]:
    """Live applications, shaped like v_new_matches cards, under the page's
    own filters. Not capped per company: these are the operator's own."""
    closed = ",".join(f"'{s}'" for s in sorted(approvals.CLOSED))
    rows = con.execute(
        "SELECT m.id AS job_id, c.name AS company, m.title, m.location, "
        "       m.remote, m.match_score, m.match_reasons, m.url, m.track, "
        "       m.discovered_at, 1 AS variant_count, NULL AS dedup_key, "
        "       m.degree_required, m.clearance_required, m.years_required, "
        "       m.tech_stack, m.enrichment_note, m.enriched_at, "
        "       m.salary_min, m.salary_max, m.salary_period, m.salary_currency, "
        "       a.status AS stage "
        "  FROM applications a JOIN jobs m ON m.id = a.job_id "
        "  LEFT JOIN companies c ON c.id = m.company_id "
        f" WHERE a.archived_at IS NULL AND a.status NOT IN ({closed}) "
        f"   AND {' AND '.join(where)}", params).fetchall()
    out = []
    for row in rows:
        card = _decode(row)
        card["status"] = STATUS_GROUP.get(card["stage"], "saved")
        card["key"] = f"app:{card['job_id']}"
        out.append(card)
    return out


def _card_data(rows) -> dict[str, dict[str, Any]]:
    """What the page's script needs to know about each listed card, keyed the
    way the map's points are. Presentation only: the list itself is already
    decided."""
    out = {}
    for row in rows:
        low, high = _annual(row)
        out[row["key"]] = {
            "company": row.get("company") or "",
            "status": row["status"],
            "miles": row.get("exact_miles"),
            "remote": bool(row.get("any_remote")),
            "pay": [low, high] if low is not None else None,
            "skills": [str(s) for s in row.get("stack") or []][:12],
            "found": row.get("discovered_at") or "",
            "score": row.get("match_score") or 0,
        }
    return out


def _pending_count(con: sqlite3.Connection) -> int:
    return con.execute(
        "SELECT COUNT(*) FROM approvals WHERE decision='pending'").fetchone()[0]


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    d["reasons"] = json.loads(d.get("match_reasons") or "[]")
    d["stack"] = json.loads(d.get("tech_stack") or "[]")
    return d


def _json_list(value: str | None) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


# Hostnames a request may name. See the module docstring.
ALLOWED_HOSTS = ("127.0.0.1", "localhost", "[::1]")

# The same rule v_new_matches applies, for the map's own query (ADR 0015:
# the map agrees with its filter): a group of copies is gone once any copy
# has an application or was passed.
NOT_TAKEN = ("COALESCE(m.dedup_key, 'job:' || m.id) NOT IN ("
             "SELECT COALESCE(t.dedup_key, 'job:' || t.id) FROM jobs t "
             "WHERE t.passed_at IS NOT NULL "
             "OR EXISTS (SELECT 1 FROM applications a WHERE a.job_id = t.id))")

# The only upload is a resume (ADR 0022); real ones are well under 1 MB.
MAX_UPLOAD_BYTES = 5_000_000
# Every other form. The largest is a pasted posting, capped at
# intake.MAX_PASTED_CHARS (120,000 characters); URL encoding can triple
# that. Measured 2026-10-02 on 1,750 stored postings: the longest was
# 25,165 bytes URL-encoded, the 99th percentile 11,591.
MAX_FORM_BYTES = 1_000_000


def multipart_field(body: bytes, content_type: str, name: str) -> str:
    """One small text field from a multipart body ("" if absent or malformed).

    The guard needs the token before the route runs, and FastAPI's own form
    parsing would consume the body.
    """
    from python_multipart.multipart import MultipartParser, parse_options_header

    _, options = parse_options_header(content_type)
    boundary = options.get(b"boundary")
    if not boundary:
        return ""
    state: dict[str, Any] = {"field": b"", "value": b"", "name": None,
                             "data": [], "found": None}

    def on_header_field(data, start, end):
        state["field"] += data[start:end]

    def on_header_value(data, start, end):
        state["value"] += data[start:end]

    def on_header_end():
        if state["field"].lower() == b"content-disposition":
            _, opts = parse_options_header(state["value"])
            state["name"] = opts.get(b"name")
        state["field"], state["value"] = b"", b""

    def on_part_begin():
        state["name"], state["data"] = None, []

    def on_part_data(data, start, end):
        if state["name"] == name.encode():
            state["data"].append(data[start:end])

    def on_part_end():
        if state["name"] == name.encode() and state["found"] is None:
            state["found"] = b"".join(state["data"])

    try:
        parser = MultipartParser(boundary, {
            "on_part_begin": on_part_begin, "on_part_data": on_part_data,
            "on_part_end": on_part_end, "on_header_field": on_header_field,
            "on_header_value": on_header_value, "on_header_end": on_header_end})
        parser.write(body)
        parser.finalize()
    except Exception:  # noqa: BLE001 - a malformed body simply has no token
        return ""
    return (state["found"] or b"").decode("ascii", "ignore")


def host_allowed(host_header: str) -> bool:
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        name, _, rest = host.partition("]")
        name += "]"
    else:
        name, _, port = host.partition(":")
        rest = ":" + port if port else ""
    if rest and not (rest.startswith(":") and rest[1:].isdigit()):
        return False
    return name in ALLOWED_HOSTS


def create_app(db_path: Path | None = None, output_dir: Path | None = None,
               profile_loader: Callable[[], dict[str, Any]] | None = None) -> FastAPI:
    """The three arguments exist for tests. Served, the app uses the
    configured tracker, output/ and profile."""
    # No /docs, /redoc or /openapi.json: Swagger's page loads a script from a
    # CDN into this origin, and nothing here is an API for others (review R-13).
    app = FastAPI(title="Job Search Review", docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.csrf_token = secrets.token_urlsafe(32)
    from ..turbo import Worker
    # Started by serve(); tests drive it with drain().
    app.state.worker = Worker(db_path, profile_loader)
    from ..setup import Discovery
    app.state.discovery = Discovery()       # the setup page's first search

    def connect() -> sqlite3.Connection:
        return db.connect(db_path) if db_path is not None else db.connect()

    def out_dir() -> Path:
        from .. import config
        return Path(output_dir) if output_dir is not None else config.OUTPUT_DIR

    def profile() -> dict[str, Any] | None:
        try:
            if profile_loader is not None:
                return profile_loader()
            from ..config import load_profile
            return load_profile()
        except Exception:  # noqa: BLE001 - a missing profile degrades the page
            return None

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if not host_allowed(request.headers.get("host", "")):
            return PlainTextResponse("Unrecognised host.", status_code=400)
        if request.method == "POST":
            kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
            # Every POST is sized BEFORE it is read: this guard holds the
            # whole body in memory. Browsers always send Content-Length for a
            # form, and never a chunked one. A chunked body's length is known
            # only after reading it (h11 frames by Transfer-Encoding even when
            # a Content-Length is sent too), so it is refused outright (review
            # R-16). The length check after reading is a second line.
            length = request.headers.get("content-length", "")
            if not length.isdigit() or "transfer-encoding" in request.headers:
                return PlainTextResponse("Length required.", status_code=411)
            limit = MAX_UPLOAD_BYTES if kind == "multipart/form-data" else MAX_FORM_BYTES
            if int(length) > limit:
                return PlainTextResponse(
                    "That file is too large. A resume is well under "
                    f"{MAX_UPLOAD_BYTES // 1_000_000} MB."
                    if kind == "multipart/form-data" else
                    "That form is too large to be a real one.", status_code=413)
            # The raw body, not request.form(): parsing the form here consumes
            # it, and the route then receives nothing.
            raw = await request.body()
            if len(raw) > limit:  # the header said otherwise
                return PlainTextResponse("That form is too large.", status_code=413)
            if kind == "multipart/form-data":
                sent = multipart_field(raw, request.headers["content-type"], "csrf")
            elif kind in ("application/x-www-form-urlencoded", ""):
                sent = parse_qs(raw.decode("utf-8", "replace")).get("csrf", [""])[0]
            else:
                # No other format may slip past the token check.
                sent = ""
            # Bytes, not str: compare_digest raises on a non-ASCII str, which
            # turned a junk token into a 500 (review R-12).
            if not secrets.compare_digest(sent.encode("utf-8", "replace"),
                                          app.state.csrf_token.encode()):
                return PlainTextResponse(
                    "This form has expired. Reload the page and try again.",
                    status_code=403)
        response = await call_next(request)
        # Not framed by another site's page, so its buttons can't be clicked
        # through an overlay (review R-14).
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
        return response

    def daily_banner(con) -> dict[str, Any] | None:
        """The latest `jsa daily` run nobody has dismissed yet (plan 18)."""
        from .. import daily
        try:
            row = daily.latest(con, unseen=True)
        except sqlite3.OperationalError:      # an older tracker: no table yet
            return None
        if row is None:
            return None
        return {"id": row["id"], "started_at": row["started_at"], "ok": bool(row["ok"]),
                "s": json.loads(row["summary_json"] or "{}")}

    def render(name: str, page: str, **ctx) -> HTMLResponse:
        con = connect()
        try:
            ctx.setdefault("pending_count", _pending_count(con))
            if page in ("matches", "pipeline") and name != "preview":
                ctx.setdefault("daily", daily_banner(con))
                ctx.setdefault("page_path", "/" if page == "matches" else "/pipeline")
        finally:
            con.close()
        title = ctx.pop("title", page.title())
        html = env.get_template(name).render(
            page=page, title=title, csrf=app.state.csrf_token, **ctx)
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    def not_found(what: str) -> HTMLResponse:
        return HTMLResponse(f"<p>No such {what}.</p>", status_code=404)

    def document_status(con, document_id: int) -> tuple[str, str | None]:
        row = con.execute(
            "SELECT decision, feedback FROM approvals WHERE subject_type = 'document' "
            "AND subject_id = ? ORDER BY id DESC LIMIT 1", (document_id,)).fetchone()
        return (row["decision"], row["feedback"]) if row else ("not queued", None)

    def job_row(job_id) -> dict[str, Any]:
        con = connect()
        try:
            row = con.execute(
                "SELECT j.id, j.title, j.location, c.name AS company FROM jobs j "
                "LEFT JOIN companies c ON c.id = j.company_id WHERE j.id = ?",
                (job_id,)).fetchone()
        finally:
            con.close()
        return dict(row) if row else {}

    def inspect_document(row, prof) -> dict[str, Any]:
        """Everything the pages show about one document. Never raises."""
        info: dict[str, Any] = {"servable": False, "paragraphs": [],
                                "compare": None, "problem": None,
                                "letter": None, "kind": row["kind"],
                                "note": row["note"],
                                "job_id": row["job_id"]}
        path = review.safe_document_path(row["path"], out_dir())
        if path is None:
            info["problem"] = "The file for this draft is missing from output/."
            return info
        info["servable"] = True
        try:
            info["paragraphs"] = review.readable(path)
        except Exception:  # noqa: BLE001 - a corrupt file is reported, not raised
            info["problem"] = "This file could not be read as a Word document."
            return info
        if prof is None:
            info["problem"] = ("Your profile could not be loaded, so this draft "
                               "cannot be checked against it.")
            return info
        if row["kind"] == "cover_letter":
            # Prose has no source bullet to sit beside; what it has is a list
            # of words nothing in the profile supports. ADR 0007.
            from .. import letter
            text = chr(10).join(p["text"] for p in info["paragraphs"])
            info["letter"] = {"problems": letter.check(text, prof,
                                                       job_row(row["job_id"]))}
            return info
        info["compare"] = review.compare(
            info["paragraphs"], _json_list(row["bullet_ids"]), prof)
        return info

    def match_filters(near: str, track: str, q: str, degree: str,
                      remote: str) -> tuple[list[str], dict[str, Any]]:
        """The SQL conditions behind the filter form. Matches and Turbo use
        the same ones, so the deck and the list agree."""
        from ..cli import load_regions, region_clause
        where, params = ["1=1"], {}
        if near and near in load_regions():
            where.append(f"({region_clause(load_regions(), near)})")
        if track in ("engineering", "sales"):
            # No longer on the form (Plan 7), still honoured so old links work.
            where.append("m.track = :track")
            params["track"] = track
        q = q.strip()
        if q:
            # One condition in the shared list, so the cards, the map's dots
            # and your own applications all filter by it (ADR 0015). The
            # function is registered by match_connect.
            where.append("title_matches(m.title, :q) = 1")
            params["q"] = q
        if degree == "yes":
            where.append("m.degree_required = 1")
        elif degree == "no":
            where.append("(m.degree_required = 0 OR m.degree_required IS NULL)")
        if remote == "remote":
            where.append("m.remote = 'remote'")
        return where, params

    def match_place(bare: bool, home: str, radius: str, anywhere: str):
        """(origin, home_text, home_problem, wanted radius, profile radius)."""
        origin, home_text, home_problem = _origin(home, profile())
        prefs_radius = _profile_radius(profile())
        # A bare visit is not a request for the whole country: it is
        # somebody opening their own dashboard, and their profile already
        # says how far they would go. Any query string is obeyed literally,
        # so every link on the page keeps meaning what it says.
        if bare and origin is not None:
            wanted = prefs_radius
        else:
            wanted = _radius(radius, anywhere)
        return origin, home_text, home_problem, wanted, prefs_radius

    def match_connect(q: str) -> sqlite3.Connection:
        con = connect()
        if q:
            from ..scoring import title_matches
            con.create_function("title_matches", 2, title_matches,
                                deterministic=True)
        return con

    def new_rows(con: sqlite3.Connection, where: list[str],
                 params: dict[str, Any]) -> list[dict[str, Any]]:
        """Every new card for these filters, best first, before the radius."""
        listed = ("SELECT m.* FROM v_new_matches m WHERE "
                  f"{' AND '.join(where)} ORDER BY m.match_score DESC")
        rows = [_decode(r) for r in con.execute(listed, params).fetchall()]
        for row in rows:
            row["status"] = "new"
            row["key"] = row.get("dedup_key") or f"job:{row['job_id']}"
        return rows

    def learn_state(con, prof) -> dict[str, Any] | None:
        from .. import learning
        try:
            model = learning.build(con)
        except sqlite3.OperationalError:
            return None
        return {"signals": model.signals, "active": model.active,
                "enabled": learning.enabled(prof), "minimum": learning.MIN_SIGNALS}

    def learned(con, rows):
        """The swipe-learned nudge (plan 19, ADR 0029): a reorder in memory,
        stored scores untouched; off or under the threshold, the same list."""
        from .. import learning
        try:
            return learning.rank(con, rows, profile())
        except sqlite3.OperationalError:      # an older tracker
            return rows

    def match_deck(rows, copies, origin, wanted):
        """The radius, then the three-per-company cap: what Matches lists and
        Turbo deals, in the same order. Returns (rows, hidden, unplaced).

        The cap is applied AFTER the radius: applied first (it used to be, in
        SQL), a company's three slots went to its best cards anywhere, the
        radius then hid them, and the one near you had already been capped
        out. Measured in n20 at 40 miles: Austin showed 2 nearby cards and
        now shows 6, Seattle 10 and now 15."""
        rows, hidden, unplaced = _by_distance(rows, origin, wanted, copies)
        return _per_company(rows, PER_COMPANY), hidden, unplaced

    def back_to_pipeline(msg: str, bad: bool) -> RedirectResponse:
        return RedirectResponse(
            "/pipeline?" + urlencode({"msg": msg, "bad": int(bad)}),
            status_code=303)

    def back_to_job(job_id: int, msg: str, bad: bool) -> RedirectResponse:
        query = urlencode({"msg": msg, "bad": int(bad)})
        return RedirectResponse(f"/job/{job_id}?{query}", status_code=303)

    # What the route modules share: these helpers close over this app's
    # tracker, output folder and profile (plan 24).
    from .context import Ctx
    ctx = Ctx(
        back_to_job=back_to_job,
        back_to_pipeline=back_to_pipeline,
        connect=connect,
        daily_banner=daily_banner,
        document_status=document_status,
        inspect_document=inspect_document,
        job_row=job_row,
        learn_state=learn_state,
        learned=learned,
        match_connect=match_connect,
        match_deck=match_deck,
        match_filters=match_filters,
        match_place=match_place,
        new_rows=new_rows,
        not_found=not_found,
        out_dir=out_dir,
        profile=profile,
        render=render,
    )
    from . import (
        routes_matches,
        routes_turbo,
        routes_setup,
        routes_misc,
        routes_job,
        routes_documents,
        routes_add,
        routes_pipeline,
        routes_review,
    )
    routes_matches.register(app, ctx)
    routes_turbo.register(app, ctx)
    routes_setup.register(app, ctx)
    routes_misc.register(app, ctx)
    routes_job.register(app, ctx)
    routes_documents.register(app, ctx)
    routes_add.register(app, ctx)
    routes_pipeline.register(app, ctx)
    routes_review.register(app, ctx)
    return app


LOOPBACK = ("127.0.0.1", "localhost", "::1")
ALLOW_PUBLIC_BIND_ENV = "JSA_ALLOW_PUBLIC_BIND"


def serve(host: str = HOST, port: int = PORT) -> None:
    """Start the dashboard. Loopback only, unless explicitly overridden.

    A container is the one legitimate exception: the process must bind
    0.0.0.0 *inside* the container for the port mapping to work at all, and
    safety comes from the host side binding to 127.0.0.1. That case sets
    JSA_ALLOW_PUBLIC_BIND=1 (see docker-compose.yml) so the override is a
    deliberate, greppable decision rather than a silently weakened guard.
    """
    import os

    import uvicorn

    if host not in LOOPBACK:
        if os.environ.get(ALLOW_PUBLIC_BIND_ENV) != "1":
            raise ValueError(
                f"refusing to bind to {host!r}: this database holds a real job "
                "search and must stay on the loopback interface. If you are "
                f"running in a container, set {ALLOW_PUBLIC_BIND_ENV}=1 and "
                "publish the port as 127.0.0.1:8765:8765 so it is still "
                "unreachable from the network."
            )
        print(
            f"WARNING: binding to {host!r} because {ALLOW_PUBLIC_BIND_ENV}=1. "
            "Anything that can reach this port can read your job search."
        )
    # db.upgrade runs from the commands that WRITE, and this one mostly
    # reads, so a view changed since the last discovery run used to be
    # served as it was: n21's pay columns came back empty on the author's
    # tracker, and the page said 1 of 63 cards stated pay when 844 of 1,512
    # open postings do. Upgrade once, here, before the first page.
    db.upgrade()
    print(f"review dashboard: http://{host}:{port}  (ctrl-c to stop)")
    import threading

    from .. import basemap
    threading.Thread(target=basemap.warm, name="basemap-warm", daemon=True).start()
    from .. import ledger
    ledger.install()                       # count every model call (plan 26)
    app = create_app()
    app.state.worker.start()               # Turbo's drafting queue (plan 16)
    uvicorn.run(app, host=host, port=port, log_level="warning")
