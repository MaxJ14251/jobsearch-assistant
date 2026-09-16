"""Networking outreach drafting.

This module drafts messages. It does not send them, and it structurally cannot:
nothing here imports an SMTP library, an email client, or any HTTP method that
posts. `tests/test_outreach.py` asserts that by inspecting the module's own
imports, so the claim is auditable in seconds rather than trusted because a
docstring says so.

The human sends the message themselves, then runs `jsa outreach sent <id>`.

Every claim about the candidate goes through the same verifier as tailoring —
a referral ask that invents experience is worse than no referral ask, because
it is addressed to a person who may check.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Literal

from . import approvals, db, llm
from .tailor import (
    FabricationError,
    collect_bullets,
    scrub_prompt,
    select_bullets,
    term_pattern,
)

Channel = Literal["linkedin_connect", "linkedin_dm", "email", "other"]
Purpose = Literal["referral_ask", "informational", "follow_up", "thank_you"]

# LinkedIn truncates a connection note at 300 characters. Counted, not estimated.
LINKEDIN_CONNECT_LIMIT = 300

CHANNEL_LIMITS: dict[str, int] = {
    "linkedin_connect": LINKEDIN_CONNECT_LIMIT,
    "linkedin_dm": 1200,
    "email": 2000,
    "other": 2000,
}

SYSTEM = (
    "You draft short, specific professional outreach. Plain language, no "
    "flattery, no invented detail about the recipient. Use only the facts "
    "given to you. Output the message body only."
)

PROMPT = """Draft a {purpose} message for {channel}.

HARD LIMIT: {limit} characters. Count them. Going over makes the message unusable.

ABOUT THE SENDER — use only these facts, do not add any others:
{bullets}

THE ROLE THEY ARE INTERESTED IN:
{role}

ABOUT THE RECIPIENT:
{recipient}

Be concrete about why this role specifically. No flattery about the recipient's
career. No claims beyond the facts above. Sign off without a name — the sender's
name is added afterwards."""


# Telling a model to count characters does not work. Measured on ten real
# linkedin_connect drafts against the 300-character limit, with "HARD LIMIT:
# 300 characters. Count them." already prominent in the prompt: min 432,
# median 473, max 556, and 10 of 10 over. That is not drift -- the model
# cannot count, so it guesses, and it guesses long.
#
# What works is measuring for it. This is the correction a person would make:
# "that is 473 characters, cut it to under 300."
SHORTEN_ATTEMPTS = 3

SHORTEN = """Your draft is {actual} characters. The limit is {limit}.

Cut it to under {limit} characters. Remove whole sentences rather than trimming
words -- a message that ends mid-thought is worse than a shorter one. Keep the
specific reason for reaching out; drop the context around it.

Output the shortened message body only.

DRAFT TO SHORTEN:
{body}"""


class OutreachError(RuntimeError):
    """A draft could not be produced safely."""


@dataclass
class Draft:
    contact_id: int
    job_id: int | None
    channel: str
    purpose: str
    body: str

    @property
    def length(self) -> int:
        return len(self.body)


def add_contact(
    con: sqlite3.Connection, *, name: str, company_id: int | None = None,
    title: str | None = None, linkedin_url: str | None = None,
    email: str | None = None, relationship: str = "cold",
    notes: str | None = None,
) -> int:
    cur = con.execute(
        "INSERT INTO contacts (company_id, name, title, linkedin_url, email, "
        "relationship, notes) VALUES (?,?,?,?,?,?,?)",
        (company_id, name, title, linkedin_url, email, relationship, notes),
    )
    return int(cur.lastrowid)


def verify_message(body: str, profile: dict[str, Any]) -> str:
    """No banned claims, and nothing the profile cannot support."""
    banned = (profile.get("ats_keywords") or {}).get(
        "aspirational_do_not_claim") or []
    for term in banned:
        if term_pattern(term).search(body):
            raise FabricationError(
                f"outreach draft claims {term!r}, which is on the "
                "aspirational_do_not_claim list"
            )
    lowered = body.lower()
    for phrase in ("my degree in", "graduated with", "i hold a degree"):
        if phrase in lowered:
            raise FabricationError(
                f"outreach draft implies a conferred degree ({phrase!r})"
            )
    return body


def enforce_limit(body: str, channel: str) -> str:
    limit = CHANNEL_LIMITS.get(channel, 2000)
    if len(body) > limit:
        raise OutreachError(
            f"{channel} messages are limited to {limit} characters "
            f"(the platform's limit, not this tool's); the draft is "
            f"{len(body)} after {SHORTEN_ATTEMPTS} attempts to shorten it. "
            "Run the command again -- measured across ten real drafts, "
            "8 of 10 land under the limit."
        )
    return body


def draft(
    con: sqlite3.Connection, profile: dict[str, Any], *, contact_id: int,
    channel: Channel = "linkedin_connect", purpose: Purpose = "referral_ask",
    job_id: int | None = None, models: list[str] | None = None,
) -> Draft:
    """Produce a verified draft and queue it for human approval."""
    contact = con.execute(
        "SELECT c.*, co.name AS company_name FROM contacts c "
        "LEFT JOIN companies co ON co.id = c.company_id WHERE c.id = ?",
        (contact_id,),
    ).fetchone()
    if contact is None:
        raise OutreachError(f"no contact with id {contact_id}")

    job = None
    if job_id is not None:
        job = con.execute(
            "SELECT j.*, c.name AS company FROM jobs j "
            "JOIN companies c ON c.id = j.company_id WHERE j.id = ?",
            (job_id,),
        ).fetchone()

    role = (
        f"{job['title']} at {job['company']}." if job
        else f"Roles at {contact['company_name'] or 'their company'}."
    )
    if job and job["tech_stack"]:
        import json
        stack = json.loads(job["tech_stack"])
        if stack:
            role += f" The posting centres on {', '.join(stack[:4])}."

    description = job["description"] if job else ""
    bullets = select_bullets(profile, description, "engineering", limit=3,
                             title=job["title"] if job else "")
    limit = CHANNEL_LIMITS.get(channel, 2000)

    prompt = PROMPT.format(
        purpose=purpose.replace("_", " "), channel=channel.replace("_", " "),
        limit=limit,
        bullets="\n".join(f"- {b.text}" for b in bullets),
        role=role,
        recipient=f"{contact['name']}, {contact['title'] or 'unknown role'}, "
                  f"at {contact['company_name'] or 'unknown company'}.",
    )
    scrub_prompt(prompt, profile)

    result = llm.complete(
        prompt, system=SYSTEM, models=models,
        max_tokens=600, temperature=0.4, thinking=False,
    )
    body = re.sub(r"\n{3,}", "\n\n", result.text.strip())

    # The model cannot count characters, so measure and hand the number back.
    # Bounded: if it still cannot get under the limit, that is an honest
    # failure. Truncating mid-sentence would produce a message worse than none.
    for _ in range(SHORTEN_ATTEMPTS):
        if len(body) <= limit:
            break
        retry = llm.complete(
            SHORTEN.format(actual=len(body), limit=limit, body=body),
            system=SYSTEM, models=models, max_tokens=600,
            temperature=0.3, thinking=False,
        )
        shorter = re.sub(r"\n{3,}", "\n\n", retry.text.strip())
        if not shorter:
            break
        body = shorter

    verify_message(body, profile)
    enforce_limit(body, channel)

    cur = con.execute(
        "INSERT INTO outreach (contact_id, job_id, channel, purpose, draft_body) "
        "VALUES (?,?,?,?,?)",
        (contact_id, job_id, channel, purpose, body),
    )
    outreach_id = int(cur.lastrowid)
    approvals.queue(
        con, "outreach", outreach_id,
        f"Send {purpose.replace('_', ' ')} to {contact['name']} "
        f"({channel.replace('_', ' ')})",
    )
    return Draft(contact_id, job_id, channel, purpose, body)


def mark_sent(con: sqlite3.Connection, outreach_id: int) -> None:
    """Record that the HUMAN sent it. This tool never transmits anything."""
    if not approvals.is_approved(con, "outreach", outreach_id):
        raise OutreachError(
            f"outreach {outreach_id} has not been approved by a human; "
            "approve it before recording it as sent"
        )
    con.execute(
        "UPDATE outreach SET status = 'sent', sent_at = ? WHERE id = ?",
        (db.utcnow(), outreach_id),
    )
