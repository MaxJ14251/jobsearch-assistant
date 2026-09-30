"""Job board feed adapters.

Each adapter takes a companies.yaml entry and returns a FetchResult whose jobs
are normalized dicts:

    {external_id, title, department, location, remote, employment_type,
     url, description, description_hash, posted_at}

Only public endpoints the companies' own job boards already call. No auth, no
credentials, no scraping behind a login. Descriptive User-Agent, delay between
requests.
"""

from __future__ import annotations

import hashlib
import html
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from . import salary
from .config import POLITE_DELAY_S, REQUEST_TIMEOUT, user_agent

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]*\n[ \t]*")

# Some corporate sites (Cloudflare-fronted) reject a non-browser UA outright.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)

# Cap per-job detail requests so one huge board can't stall a run.
MAX_DETAIL_FETCHES = 200
DETAIL_DELAY_S = 0.3


def strip_html(raw: str | None) -> str:
    if not raw:
        return ""
    text = html.unescape(raw)
    text = re.sub(r"<(br|/p|/li|/div|/h[1-6])[^>]*>", "\n", text, flags=re.I)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    text = _WS_RE.sub("\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def norm_employment(value: str | None) -> str:
    """Map a board's employment-type label onto the tracker's enum.

    Boards are inconsistent: Ashby says 'FullTime', Lever 'Full-time', Rivian
    'FULL_TIME', Snap 'Regular'. Anything unrecognized becomes 'unknown'
    rather than blowing up an insert.
    """
    key = re.sub(r"[^a-z]", "", (value or "").lower())
    return {
        "fulltime": "full-time",
        "regular": "full-time",
        "permanent": "full-time",
        "parttime": "part-time",
        "contract": "contract",
        "contractor": "contract",
        "temporary": "contract",
        "temp": "contract",
        "intern": "internship",
        "internship": "internship",
    }.get(key, "unknown")


def classify_remote(location: str, description: str = "") -> str:
    blob = f"{location} {description[:600]}".lower()
    if "hybrid" in blob:
        return "hybrid"
    if "remote" in blob:
        return "remote"
    if location.strip():
        return "onsite"
    return "unknown"


@dataclass
class FetchResult:
    ok: bool
    jobs: list[dict[str, Any]]
    status: str  # 'ok' or a human-readable error


def _client(browser_ua: bool = False) -> httpx.Client:
    return httpx.Client(
        headers={
            "User-Agent": BROWSER_UA if browser_ua else user_agent(),
            "Accept": "application/json",
        },
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    )


def _get_json(url: str, browser_ua: bool = False) -> Any:
    with _client(browser_ua) as client:
        resp = client.get(url)
        resp.raise_for_status()
        time.sleep(POLITE_DELAY_S)
        return resp.json()


def _fail(exc: Exception) -> FetchResult:
    return FetchResult(False, [], f"{type(exc).__name__}: {exc}")


# --- Greenhouse -------------------------------------------------------------


def greenhouse_url(entry: dict[str, Any]) -> str:
    return (
        f"https://boards-api.greenhouse.io/v1/boards/{entry['board']}"
        f"/jobs?content=true&pay_transparency=true"
    )


def fetch_greenhouse(entry: dict[str, Any]) -> FetchResult:
    try:
        data = _get_json(greenhouse_url(entry))
    except Exception as exc:  # noqa: BLE001 — the status string is the product
        return _fail(exc)

    jobs = []
    for item in data.get("jobs") or []:
        desc = strip_html(item.get("content"))
        location = (item.get("location") or {}).get("name") or ""
        departments = item.get("departments") or []
        jobs.append(
            {
                "external_id": str(item.get("id")),
                "title": item.get("title") or "",
                "department": departments[0]["name"] if departments else None,
                "location": location,
                "remote": classify_remote(location, desc),
                "employment_type": "unknown",
                "url": item.get("absolute_url") or "",
                "description": desc,
                "description_hash": content_hash(desc),
                "posted_at": item.get("updated_at"),
                # The board's own pay data, when it publishes one (n22).
                "pay": salary.from_greenhouse(item.get("pay_input_ranges")),
            }
        )
    return FetchResult(True, jobs, "ok")


# --- Lever ------------------------------------------------------------------


def lever_url(entry: dict[str, Any]) -> str:
    return f"https://api.lever.co/v0/postings/{entry['board']}?mode=json"


def lever_text(item: dict[str, Any]) -> str:
    """A Lever posting's text, in the order its hosted page shows it.

    `descriptionPlain` is only the top of the page (it already contains the
    opening). The requirement and responsibility lists sit in `lists`, and pay
    and benefits in `additional`. Storing the description alone dropped a
    median 3,400 characters per posting, which is where the degree, years and
    clearance lines live. Measured 2026-09-28 over 1,450 postings on the five
    verified Lever boards: 99% carry `lists`, 98% `additional`.
    """
    desc = item.get("descriptionPlain") or strip_html(item.get("description"))
    extra = []
    for block in item.get("lists") or []:
        heading = strip_html(block.get("text"))
        items = strip_html(block.get("content"))
        extra.append("\n".join(p for p in (heading, items) if p))
    extra.append(item.get("additionalPlain") or strip_html(item.get("additional")))
    extra = [p.strip() for p in extra if p and p.strip()]
    if not extra:
        return desc  # byte-identical to what was stored before, same hash
    return "\n\n".join(p for p in (desc.strip(), *extra) if p)


def fetch_lever(entry: dict[str, Any]) -> FetchResult:
    try:
        data = _get_json(lever_url(entry))
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)

    jobs = []
    for item in data or []:
        cats = item.get("categories") or {}
        desc = lever_text(item)
        location = cats.get("location") or ""
        jobs.append(
            {
                "external_id": str(item.get("id")),
                "title": item.get("text") or "",
                "department": cats.get("team"),
                "location": location,
                "remote": classify_remote(location, desc),
                "employment_type": norm_employment(cats.get("commitment")),
                "url": item.get("hostedUrl") or "",
                "description": desc,
                "description_hash": content_hash(desc),
                "posted_at": item.get("createdAt"),
                "pay": salary.from_lever(item.get("salaryRange")),
            }
        )
    return FetchResult(True, jobs, "ok")


# --- Ashby ------------------------------------------------------------------


def ashby_url(entry: dict[str, Any]) -> str:
    return (f"https://api.ashbyhq.com/posting-api/job-board/{entry['board']}"
            "?includeCompensation=true")


def fetch_ashby(entry: dict[str, Any]) -> FetchResult:
    try:
        data = _get_json(ashby_url(entry))
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)

    jobs = []
    for item in data.get("jobs") or []:
        desc = item.get("descriptionPlain") or strip_html(item.get("descriptionHtml"))
        location = item.get("location") or ""
        remote = "remote" if item.get("isRemote") else classify_remote(location, desc)
        jobs.append(
            {
                "external_id": str(item.get("id")),
                "title": item.get("title") or "",
                "department": item.get("department") or item.get("team"),
                "location": location,
                "remote": remote,
                "employment_type": norm_employment(item.get("employmentType")),
                "url": item.get("jobUrl") or item.get("applyUrl") or "",
                "description": desc,
                "description_hash": content_hash(desc),
                "posted_at": item.get("publishedAt"),
                "pay": salary.from_ashby(item.get("compensation")),
            }
        )
    return FetchResult(True, jobs, "ok")


# --- Workday ----------------------------------------------------------------
# Workday's own job board calls a public "cxs" endpoint. The listing response
# carries almost nothing useful (locationsText is literally "2 Locations"), so
# the real location and description need one detail request per job.


def workday_url(entry: dict[str, Any]) -> str:
    t, wd, site = entry["tenant"], entry.get("wd", "wd1"), entry["site"]
    return f"https://{t}.{wd}.myworkdayjobs.com/wday/cxs/{t}/{site}/jobs"


def fetch_workday(entry: dict[str, Any]) -> FetchResult:
    base = workday_url(entry)
    detail_base = base[: -len("/jobs")]
    jobs: list[dict[str, Any]] = []

    try:
        with _client() as client:
            headers = {"Content-Type": "application/json"}
            # Workday reports `total` only on the FIRST page (later pages send
            # 0), and paging past the end silently wraps back to page 1. So
            # latch the total once and stop on it — trusting the running value
            # ends the loop after two pages, and ignoring it loops forever.
            limit = 20
            offset, total, page = 0, None, 0
            postings: list[dict[str, Any]] = []
            while page < 20:  # 20 * 20 = 400 listings, plenty for one company
                resp = client.post(
                    base,
                    json={
                        "appliedFacets": {},
                        "limit": limit,
                        "offset": offset,
                        # Boards with thousands of reqs (Petco ~2k, Johnson
                        # Controls ~2.6k) can't be sampled meaningfully within
                        # the detail-fetch cap, so those entries set `search`
                        # to narrow server-side instead of taking whatever the
                        # first 200 rows happen to be.
                        "searchText": entry.get("search") or "",
                    },
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                batch = data.get("jobPostings") or []
                if total is None:
                    total = data.get("total") or len(batch)
                postings.extend(batch)
                offset += len(batch)
                page += 1
                if not batch or len(batch) < limit or offset >= total:
                    break
                time.sleep(POLITE_DELAY_S)
            postings = postings[:total or len(postings)]

            for posting in postings[:MAX_DETAIL_FETCHES]:
                path = posting.get("externalPath") or ""
                info: dict[str, Any] = {}
                if path:
                    try:
                        info = workday_detail(client, detail_base, path)
                    except Exception:  # noqa: BLE001 — degrade, don't abort
                        info = {}
                    time.sleep(DETAIL_DELAY_S)
                jobs.append(workday_job(info, detail_base, path, posting))
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)

    return FetchResult(True, jobs, "ok")


def workday_detail(client: httpx.Client, detail_base: str, path: str) -> dict[str, Any]:
    """One posting's jobPostingInfo, or {} when Workday does not return it."""
    d = client.get(detail_base + path)
    if d.status_code != 200:
        return {}
    return (d.json() or {}).get("jobPostingInfo") or {}


def workday_job(info: dict[str, Any], detail_base: str, path: str,
                posting: dict[str, Any] | None = None) -> dict[str, Any]:
    """The tracker's shape for one Workday posting. Shared with `jsa add`."""
    posting = posting or {}
    desc = strip_html(info.get("jobDescription"))
    location = info.get("location") or posting.get("locationsText") or ""
    extra = info.get("additionalLocations") or []
    if extra:
        location = "; ".join([location, *extra])
    return {
        "external_id": str(
            info.get("jobReqId")
            or (posting.get("bulletFields") or [path])[0]
        ),
        "title": info.get("title") or posting.get("title") or "",
        "department": None,
        "location": location,
        "remote": classify_remote(location, desc),
        "employment_type": norm_employment(info.get("timeType")),
        "url": info.get("externalUrl") or (detail_base + path),
        "description": desc,
        "description_hash": content_hash(desc),
        "posted_at": info.get("startDate"),
    }


# --- Workable ---------------------------------------------------------------


def workable_url(entry: dict[str, Any]) -> str:
    return (
        f"https://apply.workable.com/api/v1/widget/accounts/{entry['board']}"
        "?details=true"
    )


def fetch_workable(entry: dict[str, Any]) -> FetchResult:
    try:
        data = _get_json(workable_url(entry))
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)

    jobs = []
    for item in data.get("jobs") or []:
        desc = strip_html(item.get("description"))
        parts = [item.get("city"), item.get("state"), item.get("country")]
        location = ", ".join(p for p in parts if p)
        remote = "remote" if item.get("telecommuting") else classify_remote(location, desc)
        jobs.append(
            {
                "external_id": str(item.get("shortcode")),
                "title": item.get("title") or "",
                "department": item.get("department"),
                "location": location,
                "remote": remote,
                "employment_type": norm_employment(item.get("employment_type")),
                "url": item.get("url") or item.get("shortlink") or "",
                "description": desc,
                "description_hash": content_hash(desc),
                "posted_at": item.get("published_on"),
            }
        )
    return FetchResult(True, jobs, "ok")


# --- Company-specific boards ------------------------------------------------
# Two employers run their own job APIs. Each is a handful of lines, and
# both post many roles, so they earn a custom adapter.


def fetch_snap(entry: dict[str, Any]) -> FetchResult:
    """Snap's careers site queries an Elasticsearch-shaped public endpoint.

    NOTE: this feed carries no job description, so keyword scoring contributes
    nothing for Snap and its scores will read lower than a comparable
    Greenhouse role. The description has to be fetched from the job URL at
    tailoring time.
    """
    try:
        data = _get_json("https://careers.snap.com/api/jobs")
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)

    jobs = []
    for hit in data.get("body") or []:
        src = hit.get("_source") or {}
        offices = src.get("offices") or []
        location = "; ".join(
            o.get("location") for o in offices if o.get("location")
        ) or (src.get("primary_location") or "")
        jobs.append(
            {
                "external_id": str(src.get("id") or hit.get("_id")),
                "title": src.get("title") or "",
                "department": src.get("departments") or src.get("role"),
                "location": location,
                "remote": classify_remote(location),
                "employment_type": norm_employment(src.get("employment_type")),
                "url": src.get("absolute_url") or "",
                "description": "",
                "description_hash": "",
                "posted_at": None,
            }
        )
    return FetchResult(True, jobs, "ok")


def fetch_rivian(entry: dict[str, Any]) -> FetchResult:
    """Rivian fronts its iCIMS board with a paginated JSON API.

    Needs a browser User-Agent; the API rejects other clients.
    """
    jobs = []
    try:
        with _client(browser_ua=True) as client:
            page = 1
            while page <= 15:  # 15 * 10 = 150 listings
                resp = client.get(f"https://careers.rivian.com/api/jobs?page={page}")
                resp.raise_for_status()
                data = resp.json()
                batch = data.get("jobs") or []
                if not batch:
                    break
                for wrapper in batch:
                    d = wrapper.get("data") or {}
                    desc = strip_html(d.get("description"))
                    location = d.get("full_location") or d.get("location_name") or ""
                    jobs.append(
                        {
                            "external_id": str(d.get("req_id") or d.get("slug")),
                            "title": d.get("title") or "",
                            "department": (d.get("categories") or [{}])[0].get("name"),
                            "location": location,
                            "remote": classify_remote(location, desc),
                            "employment_type": norm_employment(d.get("employment_type")),
                            "url": d.get("apply_url") or "",
                            "description": desc,
                            "description_hash": content_hash(desc),
                            "posted_at": d.get("posted_date"),
                        }
                    )
                if len(jobs) >= (data.get("totalCount") or 0):
                    break
                page += 1
                time.sleep(POLITE_DELAY_S)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)

    return FetchResult(True, jobs, "ok")


CUSTOM: dict[str, Callable[[dict[str, Any]], FetchResult]] = {
    "snap": fetch_snap,
    "rivian": fetch_rivian,
}


def fetch_custom(entry: dict[str, Any]) -> FetchResult:
    handler = CUSTOM.get(entry.get("handler") or "")
    if not handler:
        return FetchResult(False, [], f"no handler named {entry.get('handler')!r}")
    return handler(entry)


# --- RSS --------------------------------------------------------------------


def fetch_rss(entry: dict[str, Any]) -> FetchResult:
    import feedparser

    url = entry.get("url") or ""
    try:
        with _client() as client:
            resp = client.get(url, headers={"Accept": "application/rss+xml"})
            resp.raise_for_status()
            time.sleep(POLITE_DELAY_S)
        parsed = feedparser.parse(resp.content)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)

    if parsed.get("bozo") and not parsed.get("entries"):
        return FetchResult(False, [], f"unparseable feed: {parsed.get('bozo_exception')}")

    jobs = []
    for item in parsed.entries:
        desc = strip_html(item.get("summary") or item.get("description"))
        title = item.get("title") or ""
        location = ""
        for tag in item.get("tags") or []:
            if tag.get("term"):
                location = tag["term"]
                break
        jobs.append(
            {
                "external_id": item.get("id") or item.get("link") or title,
                "title": title,
                "department": None,
                "location": location,
                "remote": classify_remote(location or "remote", desc),
                "employment_type": "unknown",
                "url": item.get("link") or "",
                "description": desc,
                "description_hash": content_hash(desc),
                "posted_at": item.get("published") or item.get("updated"),
            }
        )
    return FetchResult(True, jobs, "ok")


# --- The Muse ---------------------------------------------------------------
# The one source that is not a single employer. Every other feed here is one
# company's own board, which is why the shipped list covers the places those
# companies hire and nowhere else: measured 2026-09-26 over 1,147 stored
# postings, 25 states had NO on-site posting at all.
#
# The Muse aggregates many employers and filters by city, so the query is
# built from the operator's own `locations` rather than from a board token.
# Measured the same day, sampling 80 postings per city: Boise returned 6 in
# Boise and 74 remote from a pool of ~6,700; Columbus 47 in Columbus of 80.
# Boise's count from the shipped feeds was zero.
#
# Terms (https://www.themuse.com/developers/api/v2/terms, read 2026-09-26):
# registration is required for any use beyond testing (2.2), content shown
# must link back to themuse.com (3.4) -- the stored URL is their posting
# page, so that holds -- cloning the content wholesale is forbidden (3.3g),
# which is why this fetches only the operator's own cities and stops at
# MAX_PAGES, and rate limits must be respected (4.2): 500 requests/hour
# without a key, 3,600 with one.

MUSE_BASE = "https://www.themuse.com/api/public/jobs"
# Per city per run. 20 postings a page, so 5 pages is 100 of the freshest for
# that city -- enough to keep a tracker fed daily, nowhere near a clone.
MUSE_MAX_PAGES = 5


def themuse_url(entry: dict[str, Any]) -> str:
    return MUSE_BASE


def _muse_job(item: dict[str, Any]) -> dict[str, Any] | None:
    names = [l.get("name", "") for l in item.get("locations") or []]
    location = "; ".join(n for n in names if n)
    desc = strip_html(item.get("contents"))
    url = ((item.get("refs") or {}).get("landing_page") or "").strip()
    if not url or not item.get("id"):
        return None
    remote = "remote" if any("flexible" in n.lower() or "remote" in n.lower()
                             for n in names) else classify_remote(location, desc)
    # The Muse's own level names, mapped to the schema's vocabulary. The
    # enrichment pass may refine this later from the posting text.
    levels = [str(l.get("name", "")).lower() for l in item.get("levels") or []]
    seniority = "unknown"
    for label, value in (("internship", "intern"), ("entry", "entry"),
                         ("mid", "mid"), ("senior", "senior"),
                         ("management", "senior"), ("executive", "principal")):
        if any(label in lv for lv in levels):
            seniority = value
            break
    return {
        "external_id": str(item.get("id")),
        # The employer, not the job site. Every other feed IS one employer, so
        # the company came from companies.yaml -- which filed a SpaceX job
        # under "The Muse" on the first real run, and made every aggregator
        # posting look like one company's.
        "employer": ((item.get("company") or {}).get("name") or "").strip(),
        "title": item.get("name") or "",
        "department": (item.get("categories") or [{}])[0].get("name"),
        "location": location,
        "remote": remote,
        "employment_type": "unknown",
        "seniority": seniority,
        "url": url,
        "description": desc,
        "description_hash": content_hash(desc),
        "posted_at": item.get("publication_date"),
    }


def fetch_themuse(entry: dict[str, Any]) -> FetchResult:
    """Postings near the operator's own locations, from many employers.

    `locations` is filled in by discovery from the profile; an entry with
    none is a configuration mistake, not an empty result, and says so.
    """
    locations = [str(l).strip() for l in (entry.get("locations") or []) if str(l).strip()]
    if not locations:
        return FetchResult(
            False, [], "no locations: fill in job_search_preferences.locations")
    key = (entry.get("api_key") or "").strip()
    if not key:
        # Not a failure: the operator has not registered yet, and the terms
        # ask them to. Everything else in the run still works.
        return FetchResult(True, [], "skipped (no MUSE_API_KEY; register free "
                                     "at themuse.com/developers/api/v2/apps)")

    jobs: dict[str, dict[str, Any]] = {}
    try:
        with _client() as client:
            for city in locations:
                for page in range(1, MUSE_MAX_PAGES + 1):
                    params = {"page": page, "location": city, "api_key": key}
                    resp = client.get(MUSE_BASE, params=params)
                    if resp.status_code == 403:
                        return FetchResult(False, list(jobs.values()),
                                           "rate limited by The Muse (403)")
                    resp.raise_for_status()
                    data = resp.json() or {}
                    results = data.get("results") or []
                    for item in results:
                        job = _muse_job(item)
                        if job:
                            # The same posting comes back for several of the
                            # operator's cities. One row, not one per city.
                            jobs[job["external_id"]] = job
                    if len(results) < 20 or page >= (data.get("page_count") or 1):
                        break
                    time.sleep(POLITE_DELAY_S)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc)
    return FetchResult(True, list(jobs.values()), "ok")


# --- dispatch ---------------------------------------------------------------

FETCHERS: dict[str, Callable[[dict[str, Any]], FetchResult]] = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workday": fetch_workday,
    "workable": fetch_workable,
    "custom": fetch_custom,
    "rss": fetch_rss,
    "themuse": fetch_themuse,
}

URL_BUILDERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "greenhouse": greenhouse_url,
    "lever": lever_url,
    "ashby": ashby_url,
    "workday": workday_url,
    "workable": workable_url,
    "themuse": themuse_url,
}

REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "greenhouse": ("board",),
    "lever": ("board",),
    "ashby": ("board",),
    "workable": ("board",),
    "workday": ("tenant", "site"),
    "custom": ("handler",),
    "rss": ("url",),
    "themuse": (),
}


def feed_url(entry: dict[str, Any]) -> str | None:
    """Resolve the endpoint recorded in the sources table."""
    kind = entry.get("kind") or ""
    builder = URL_BUILDERS.get(kind)
    if builder:
        try:
            return builder(entry)
        except KeyError:
            return entry.get("careers_url")
    return entry.get("url") or entry.get("careers_url")


def fetch(entry: dict[str, Any]) -> FetchResult:
    kind = entry.get("kind") or ""
    fetcher = FETCHERS.get(kind)
    if not fetcher:
        return FetchResult(False, [], f"no adapter for kind={kind!r}")
    missing = [f for f in REQUIRED_FIELDS.get(kind, ()) if not entry.get(f)]
    if missing:
        return FetchResult(False, [], f"missing config field(s): {', '.join(missing)}")
    return fetcher(entry)
