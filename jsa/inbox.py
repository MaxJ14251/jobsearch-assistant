"""Read application replies from the operator's mailbox, read-only (ADR 0021).

Rejections, interview requests and receipts arrive by email and get buried.
This finds the ones about applications in the tracker and SUGGESTS a stage
change for each. A person confirms or dismisses every one; fetching never
touches `applications`.

What makes it safe, and what tests/test_inbox.py holds it to:

- **Read-only.** The folder is opened with `readonly=True` (IMAP EXAMINE),
  and every fetch is a `BODY.PEEK`, so nothing is even marked read. This is
  the only module that imports imaplib, and it never calls STORE, COPY,
  MOVE, EXPUNGE or APPEND. Nothing here can send: there is no SMTP anywhere.
- **No model.** Matching and classification are fixed rules, run here. No
  mail text is sent anywhere, and none is stored: a stored reply keeps its
  subject, date, sender domain and the NAME of the rule that matched.
- **Suggest, never decide.** `confirm()` calls approvals.set_stage(), the
  same path as the dashboard's stage form, which records the human.

Settings live in .env (never committed): JSA_IMAP_USER, and a Gmail APP
PASSWORD in JSA_IMAP_APP_PASSWORD -- a separate, revocable code, never the
account password.
"""

from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape
from typing import Any

from . import approvals

HOST_DEFAULT = "imap.gmail.com"
FOLDER_DEFAULT = "[Gmail]/All Mail"   # archived and filtered mail included
TIMEOUT_S = 30
TEXT_CAP = 20_000                     # bytes of a message's text read, at most

# Applicant-tracking systems that send on an employer's behalf. A message from
# one of these may name the employer only in its body.
HIRING_SENDERS = (
    "greenhouse.io", "greenhouse-mail.io", "lever.co", "ashbyhq.com",
    "myworkday.com", "workday.com", "icims.com", "smartrecruiters.com",
    "jobvite.com",
)

# Stages in which a reply is worth looking for: applied, and the live ones
# after it. Not saved/drafting/ready (nothing sent yet), not closed.
LOOK_IN = ("applied", "phone_screen", "technical", "onsite", "offer")
NEXT_AFTER_INTERVIEW = {"applied": "phone_screen", "phone_screen": "technical",
                        "technical": "onsite"}

# Ordered: the first that matches wins. Rejection first, because rejections
# usually also say "thank you for applying" and often mention interviews.
RULES: list[tuple[str, str, re.Pattern[str]]] = [
    (kind, name, re.compile(pattern, re.I)) for kind, name, pattern in (
        ("rejection", "not-moving-forward",
         r"not (?:be )?moving forward|move forward with other candidates"
         r"|moving forward with other candidates|decided not to (?:proceed|move)"
         r"|regret to inform|position has (?:been|now been) filled"
         # "not selected" only as a statement: a receipt says "IF you are
         # not selected for this position, keep an eye on our jobs page"
         # (found on the first real fetch, 2026-10-02).
         r"|will not be (?:moving|proceeding)|(?:have|has|had|were|was) not (?:been )?selected"
         r"|pursue other candidates|unable to offer you"),
        ("offer", "offer", r"pleased to offer|offer letter|extend (?:you )?an offer"),
        ("interview", "invitation",
         r"your availability|schedule (?:a|an|your|some) (?:time|call|chat|interview|conversation)"
         r"|invite you to (?:an? )?(?:interview|call|chat|conversation)"
         r"|phone screen|next steps? in (?:the|our) (?:interview|hiring) process"
         r"|calendly\.com|goodtime\.io"),
        ("received", "receipt",
         r"received your application|thank you for applying|thanks for applying"
         r"|application (?:has been )?received"),
    )
]

_CORP_SUFFIX = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|company|plc|gmbh)\b\.?", re.I)
_TAGS = re.compile(r"<[^>]+>")


class InboxError(Exception):
    """Not configured, or the mailbox could not be read. Says what to do."""


@dataclass
class Report:
    searched: int = 0
    matched: int = 0
    stored: int = 0
    already: int = 0
    ambiguous: int = 0
    user: str = ""


# --- settings ---------------------------------------------------------------


def settings() -> dict[str, str] | None:
    """The IMAP settings from .env, or None when they are not set."""
    from .config import load_dotenv
    load_dotenv()
    user = os.environ.get("JSA_IMAP_USER", "").strip()
    password = os.environ.get("JSA_IMAP_APP_PASSWORD", "").strip()
    if not user or not password:
        return None
    return {"user": user, "password": password,
            "host": os.environ.get("JSA_IMAP_HOST", "").strip() or HOST_DEFAULT,
            "folder": os.environ.get("JSA_IMAP_FOLDER", "").strip() or FOLDER_DEFAULT}


def mask(address: str) -> str:
    """m…@gmail.com: enough to recognise, not enough to copy."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}…@{domain}" if domain else "…"


NOT_CONFIGURED = (
    "email is not set up. Put JSA_IMAP_USER and JSA_IMAP_APP_PASSWORD in .env "
    "(see .env.example): a Gmail app password from Google Account > Security > "
    "2-Step Verification > App passwords, never your account password.")


# --- the mailbox, read-only ---------------------------------------------------


def connect(cfg: dict[str, str]) -> imaplib.IMAP4:
    imap = imaplib.IMAP4_SSL(cfg["host"], 993, timeout=TIMEOUT_S)
    try:
        imap.login(cfg["user"], cfg["password"])
    except imaplib.IMAP4.error as exc:
        raise InboxError("the mailbox refused the login: check the app password "
                         "in .env (an app password, not your account password)") from exc
    folder = cfg["folder"]
    typ, _ = imap.select(_quote(folder), readonly=True)
    if typ != "OK":
        raise InboxError(f"could not open the folder {folder!r} read-only")
    return imap


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --- what to look for ---------------------------------------------------------


def candidates(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Live applications that have been sent, with the employer's name."""
    rows = con.execute(
        "SELECT a.id, a.job_id, a.status, a.applied_at, j.title, j.company_id, "
        "c.name AS company "
        "FROM applications a JOIN jobs j ON j.id = a.job_id "
        "JOIN companies c ON c.id = j.company_id "
        f"WHERE a.archived_at IS NULL AND a.applied_at IS NOT NULL "
        f"AND a.status IN ({','.join('?' * len(LOOK_IN))})", LOOK_IN).fetchall()
    out = []
    for r in rows:
        name = employer_name(r["company"], r["title"])
        if name:
            # The employer's other roles in the tracker. A reply that names one
            # of them, and not this one, is about a different application
            # (found on the first real fetch: a receipt for another role at an
            # employer with one application in the tracker was matched to it).
            others = [t for (t,) in con.execute(
                "SELECT DISTINCT title FROM jobs WHERE company_id = ? AND id <> ?",
                (r["company_id"], r["job_id"]))]
            out.append({**dict(r), "employer": name, "other_titles": others})
    return out


def _role(title: str) -> str:
    """A job title without its requisition code: emails leave "(R5856)" out."""
    return re.sub(r"\s*[\(\[][^)\]]*[\)\]]\s*$", "", title or "").strip()


def employer_name(company: str, title: str) -> str | None:
    """The name the employer's mail will carry.

    An aggregator is stored as the company ("(aggregator) Hacker News jobs");
    the employer is in the title, as in "Kyber (YC W23) Is Hiring a ...".
    """
    if (company or "").startswith("(aggregator)"):
        m = re.match(r"^\s*(.+?)\s*(?:\(|\bis hiring\b|[-–—:|])", title or "", re.I)
        return m.group(1).strip() if m else None
    return company


def _norm(text: str) -> str:
    text = _CORP_SUFFIX.sub(" ", (text or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _names(text: str, name: str) -> bool:
    """Whether `name` appears in `text` as whole words."""
    n = _norm(name)
    return bool(n) and re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])",
                                 _norm(text)) is not None


def gmail_query(apps: list[dict[str, Any]]) -> str:
    since = _since(apps)
    terms = [f"from:{d}" for d in HIRING_SENDERS]
    terms += ['"' + a["employer"].replace('"', "") + '"' for a in apps]
    return "{" + " ".join(terms) + "} after:" + since.strftime("%Y/%m/%d")


def _since(apps: list[dict[str, Any]]) -> datetime:
    earliest = min(_parse_time(a["applied_at"]) for a in apps)
    return earliest - timedelta(days=1)


def _parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def search(imap: imaplib.IMAP4, apps: list[dict[str, Any]]) -> list[bytes]:
    """Message UIDs worth reading. One Gmail search; per-term elsewhere."""
    if "X-GM-EXT-1" in (getattr(imap, "capabilities", ()) or ()):
        typ, data = imap.uid("SEARCH", "X-GM-RAW", _quote(gmail_query(apps)))
        return (data[0] or b"").split() if typ == "OK" else []
    since = _since(apps).strftime("%d-%b-%Y")
    found: list[bytes] = []
    for term in [("FROM", d) for d in HIRING_SENDERS] + \
                [("SUBJECT", a["employer"]) for a in apps] + \
                [("FROM", a["employer"]) for a in apps]:
        typ, data = imap.uid("SEARCH", "SINCE", since, term[0], _quote(term[1]))
        if typ == "OK":
            found += (data[0] or b"").split()
    return list(dict.fromkeys(found))


# --- reading one message ------------------------------------------------------


HEADER_SPEC = ("(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID "
               "CONTENT-TYPE CONTENT-TRANSFER-ENCODING MIME-VERSION)])")
TEXT_SPEC = f"(BODY.PEEK[TEXT]<0.{TEXT_CAP}>)"


def _fetch_part(imap: imaplib.IMAP4, uid: bytes, spec: str) -> bytes:
    typ, data = imap.uid("FETCH", uid, spec)
    if typ != "OK" or not data:
        return b""
    for item in data:
        if isinstance(item, tuple) and len(item) > 1:
            return item[1] or b""
    return b""


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(email.header.make_header(email.header.decode_header(value)))
    except Exception:  # noqa: BLE001 - a malformed header is still text
        return value


def headers(raw: bytes) -> dict[str, Any]:
    msg = email.message_from_bytes(raw)
    name, address = email.utils.parseaddr(_decode(msg.get("From")))
    try:
        received = email.utils.parsedate_to_datetime(msg.get("Date") or "")
        if received.tzinfo is None:
            received = received.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        received = None
    return {"message_id": (msg.get("Message-ID") or "").strip(),
            "from_name": name, "domain": address.rpartition("@")[2].lower(),
            "subject": _decode(msg.get("Subject")).strip(), "received": received}


def text_of(header_raw: bytes, body_raw: bytes) -> str:
    """Readable text from the headers plus the (capped) body. Lenient."""
    try:
        msg = email.message_from_bytes(header_raw + b"\r\n" + body_raw)
        parts = list(msg.walk()) if msg.is_multipart() else [msg]
        plain, html = [], []
        for part in parts:
            ctype = part.get_content_type()
            if ctype not in ("text/plain", "text/html"):
                continue
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", "replace")
            (plain if ctype == "text/plain" else html).append(text)
        if plain:
            return "\n".join(plain)
        if html:
            return unescape(_TAGS.sub(" ", "\n".join(html)))
    except Exception:  # noqa: BLE001 - a malformed message is still worth a look
        pass
    return body_raw.decode("utf-8", "replace")


# --- matching and classifying -------------------------------------------------


def is_hiring_sender(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in HIRING_SENDERS)


def match(head: dict[str, Any], text: str,
          apps: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, list[int]]:
    """(the application, []) or (None, [candidate ids]) when ambiguous.

    The employer must be named in the sender's name or domain, or the
    subject; or, from a hiring system only, in the body. A company named only
    in the body of some other mail is not enough: "Vast" is a word.
    """
    received = head["received"]
    hits = []
    for app in apps:
        if received is not None and received < _parse_time(app["applied_at"]) - timedelta(days=1):
            continue
        name = app["employer"]
        squashed = _norm(name).replace(" ", "")
        in_header = (_names(head["from_name"], name) or _names(head["subject"], name)
                     or (len(squashed) >= 4 and squashed in head["domain"].replace(".", "")))
        if in_header or (is_hiring_sender(head["domain"]) and _names(text, name)):
            hits.append(app)
    said = head["subject"] + " " + text
    if len(hits) == 1:
        app = hits[0]
        names_another = any(_names(said, _role(t)) for t in app.get("other_titles", ())
                            if len(_norm(_role(t))) >= 8)
        if names_another and not _names(said, _role(app["title"])):
            return None, [app["id"]]
        return app, []
    if not hits:
        return None, []
    titled = [a for a in hits if _names(said, _role(a["title"]))]
    if len(titled) == 1:
        return titled[0], []
    return None, [a["id"] for a in hits]


def classify(subject: str, text: str) -> tuple[str, str]:
    """(kind, rule name). The first rule that matches wins; else 'other'."""
    blob = f"{subject}\n{text}"
    for kind, name, pattern in RULES:
        if pattern.search(blob):
            return kind, name
    return "other", ""


def suggest(kind: str, status: str) -> str | None:
    if kind == "rejection":
        return "rejected"
    if kind == "offer":
        return "offer"
    if kind == "interview":
        return NEXT_AFTER_INTERVIEW.get(status)
    return None


# --- the run ------------------------------------------------------------------


def fetch(con: sqlite3.Connection, imap: imaplib.IMAP4 | None = None) -> Report:
    """Read the mailbox and store new suggestions as 'pending'.

    Never writes to `applications`. Logs out whatever happens.
    """
    apps = candidates(con)
    cfg = settings() if imap is None else None
    if imap is None and cfg is None:
        raise InboxError(NOT_CONFIGURED)
    report = Report(user=mask(cfg["user"]) if cfg else "")
    if not apps:
        return report
    imap = imap or connect(cfg)
    try:
        uids = search(imap, apps)
        report.searched = len(uids)
        known = {r[0] for r in con.execute("SELECT message_id FROM inbox_replies")}
        by_id = {a["id"]: a for a in apps}
        for uid in uids:
            raw_head = _fetch_part(imap, uid, HEADER_SPEC)
            head = headers(raw_head)
            if not head["message_id"]:
                continue
            if head["message_id"] in known:
                report.already += 1
                continue
            app, ambiguous = match(head, "", apps)
            if app is None and not ambiguous and not is_hiring_sender(head["domain"]):
                continue
            text = text_of(raw_head, _fetch_part(imap, uid, TEXT_SPEC))
            # Always again, with the text: the header found the employer, but
            # the body may name a different role there.
            app, ambiguous = match(head, text, apps)
            if app is None and not ambiguous:
                continue
            report.matched += 1
            kind, rule = classify(head["subject"], text)
            status = app["status"] if app else by_id[ambiguous[0]]["status"]
            con.execute(
                "INSERT OR IGNORE INTO inbox_replies (message_id, application_id, "
                "candidates, received_at, sender_domain, subject, kind, "
                "suggested_stage, rule) VALUES (?,?,?,?,?,?,?,?,?)",
                (head["message_id"], app["id"] if app else None,
                 json.dumps(ambiguous) if ambiguous else None,
                 head["received"].strftime("%Y-%m-%dT%H:%M:%SZ") if head["received"] else None,
                 head["domain"], head["subject"][:300], kind,
                 suggest(kind, status) if app else None, rule))
            known.add(head["message_id"])
            report.stored += 1
            report.ambiguous += bool(ambiguous)
        con.commit()
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001 - closing a dead connection
            pass
    return report


def pending(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT r.*, a.job_id, a.status, j.title, c.name AS company "
        "FROM inbox_replies r LEFT JOIN applications a ON a.id = r.application_id "
        "LEFT JOIN jobs j ON j.id = a.job_id LEFT JOIN companies c ON c.id = j.company_id "
        "WHERE r.state = 'pending' ORDER BY r.application_id, r.received_at").fetchall()


@dataclass
class Confirmed:
    job_id: int
    previous: str
    stage: str
    application_id: int
    kind: str


def confirm(con: sqlite3.Connection, reply_id: int, stage: str | None = None) -> Confirmed:
    """Move the application, as the person confirming."""
    row = con.execute(
        "SELECT r.*, a.job_id FROM inbox_replies r "
        "LEFT JOIN applications a ON a.id = r.application_id WHERE r.id = ?",
        (reply_id,)).fetchone()
    if row is None:
        raise InboxError(f"there is no reply {reply_id}")
    if row["state"] != "pending":
        raise InboxError(f"reply {reply_id} was already {row['state']}")
    if row["application_id"] is None:
        raise InboxError(f"reply {reply_id} could be about more than one application; "
                         "move the right one with `jsa status <job#> <stage>` and "
                         f"dismiss this with `jsa inbox dismiss {reply_id}`")
    stage = stage or row["suggested_stage"]
    if not stage:
        raise InboxError(f"reply {reply_id} suggests no stage; give one with --stage")
    day = (row["received_at"] or "")[:10] or "an unknown date"
    _app, previous = approvals.set_stage(
        con, row["job_id"], stage, note=f"from an email of {day}: {row['subject']}")
    con.execute("UPDATE inbox_replies SET state = 'confirmed' WHERE id = ?", (reply_id,))
    return Confirmed(row["job_id"], previous, stage, row["application_id"], row["kind"])


def dismiss(con: sqlite3.Connection, reply_id: int) -> None:
    cur = con.execute("UPDATE inbox_replies SET state = 'dismissed' "
                      "WHERE id = ? AND state = 'pending'", (reply_id,))
    if cur.rowcount != 1:
        raise InboxError(f"there is no pending reply {reply_id}")
