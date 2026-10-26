"""Networking outreach drafting.

This module drafts messages. It does not send them, and it structurally cannot:
nothing here imports an SMTP library, an email client, or any HTTP method that
posts. `tests/test_outreach.py` asserts that by inspecting the module's own
imports, so the claim is auditable in seconds rather than trusted because a
docstring says so.

The human sends the message themselves, then runs `jsa outreach sent <id>`.

A referral ask that invents experience is worse than no referral ask, because
it is addressed to a person who may check. This file used to say that every
claim went "through the same verifier as tailoring"; it did not. verify_message
was a banned-term list and three degree phrases, and a draft inventing an
employer, a team of twelve, six years' tenure, a $4M figure and a certification
passed it without objection. It now runs the word-level check cover letters got
in ADR 0007, bounded by the profile, this posting and the recipient.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Literal

from . import approvals, db, llm
from . import posting as posting_text
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

WHAT THE POSTING SAYS (for context — describe the ROLE from this, never the
sender's experience; every claim about the sender must come from the facts
above):
{posting}

ABOUT THE RECIPIENT:
{recipient}

Be concrete about why this role specifically. No flattery about the recipient's
career. No claims beyond the facts above. Sign off without a name — the sender's
name is added afterwards."""


# --- sentences about the recipient kept apart (G8) --------------------------------
# It changes the prompt and the retry only: the check (checked_sentences, plan
# 38) already sorts each sentence and holds the sender's to the profile. Off,
# every prompt is byte-identical (tests/test_prompts_unchanged.py). On since
# 2026-10-26: 2 of 9 messages passed against 0 of 18 with the old prompt, and
# the owner found no claim about them in the passing ones (ADR 0010).
SUBJECT_AWARE = True

SYSTEM_SPLIT = SYSTEM + (
    " Sentences about the recipient and sentences about the sender are kept "
    "apart, never mixed in one sentence, and the sender's facts keep their own "
    "wording.")

PROMPT_SPLIT = """Draft a {purpose} message for {channel}.

HARD LIMIT: {limit} characters. Count them. Going over makes the message unusable.

ABOUT THE SENDER — use only these facts, in their own words, and add no others:
{bullets}

THE ROLE THEY ARE INTERESTED IN:
{role}

WHAT THE POSTING SAYS (context only: never copy its wording into the message):
{posting}

ABOUT THE RECIPIENT:
{recipient}

Two kinds of sentence, never mixed in one sentence:
- About the recipient, their team or company: it starts with their name, "You",
  "Your team" or the company's name; it uses only the recipient's title and
  company and the technologies named under THE ROLE; it never mentions the
  sender (no I, my, me, we); it never says what they don't do; no numbers.
- About the sender: it starts with "I" or "My" and says only what the facts
  above say, keeping their words: don't paraphrase them.
Never write "my work matches your..." or "like you": write two sentences. At
most two sentences about the recipient; the rest is about the sender and the
ask. No flattery. Sign off without a name — the sender's name is added
afterwards."""


def build(profile: dict[str, Any], job: Any, contact: Any, *, channel: str,
          purpose: str, split: bool | None = None) -> tuple[str, str]:
    """(prompt, system) for one message. `split` defaults to SUBJECT_AWARE;
    off, today's prompt byte for byte. Shared by draft() and the measurement
    tool, so what is measured is what production sends."""
    split = SUBJECT_AWARE if split is None else split
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
    # role_kind(title, track) lets the title decide and falls back to the
    # track. Hardcoding "engineering" meant the fallback was always wrong for
    # a posting the tracker had already classified as something else.
    track = (job["track"] if job and job["track"] else "engineering")
    bullets = select_bullets(profile, description, track, limit=3,
                             title=job["title"] if job else "",
                             # A three-line note is not a resume.
                             keep_work_history=False, keep_project=False)
    limit = CHANNEL_LIMITS.get(channel, 2000)
    prompt = (PROMPT_SPLIT if split else PROMPT).format(
        purpose=purpose.replace("_", " "), channel=channel.replace("_", " "),
        limit=limit,
        bullets="\n".join(f"- {b.text}" for b in bullets),
        role=role,
        recipient=f"{contact['name']}, {contact['title'] or 'unknown role'}, "
                  f"at {contact['company_name'] or 'unknown company'}. "
                  f"Address them as {salutation(contact['name'])}.",
        posting=posting_text.visible(description, MAX_POSTING_CHARS).text
                or "(not available)",
    )
    return prompt, (SYSTEM_SPLIT if split else SYSTEM)


def retry_prompt(prompt: str, found: list[dict[str, Any]], unsupported: list[str],
                 rules: list[str], split: bool) -> str:
    """Name the words, and which sentence they were in, rather than re-rolling
    blind, exactly as a cover letter does. Every real draft of the first run
    tripped on ordinary words like "quick" and "worth"; the ones worth failing
    over are claims like "stakeholders" and "accustomed", and this tells the
    model which it used."""
    # Which sentence, too (plan 38): "zendesk" is fine about them and not
    # about you, so the word alone doesn't say what to fix.
    where = "; ".join(
        f"sentence {s['index']} (\"{s['text'][:60]}\"): {', '.join(s['refused'][:6])}"
        for s in found if s["refused"])
    retry = (
        prompt + "\n\nYour previous draft was rejected for using words "
        "that nothing in the facts above supports. Do not use any of "
        "these words: " + ", ".join(unsupported)
        + (". Where they were: " + where if where else "")
        + (". Also: " + "; ".join(rules[:3]) if rules else "")
        + ".\nRewrite it, saying only what the facts above say.")
    if split:
        retry += (" Keep sentences about the recipient (starting with their name, "
                  "You, Your team or the company) apart from sentences about the "
                  "sender (starting with I or My), and keep the facts' own words.")
    return retry


# Telling a model to count characters does not work. Measured on ten real
# linkedin_connect drafts against the 300-character limit, with "HARD LIMIT:
# 300 characters. Count them." already prominent in the prompt: min 432,
# median 473, max 556, and 10 of 10 over. That is not drift -- the model
# cannot count, so it guesses, and it guesses long.
#
# What works is measuring for it. This is the correction a person would make:
# "that is 473 characters, cut it to under 300."
SHORTEN_ATTEMPTS = 3

# Same shape as letter.ATTEMPTS: when a draft uses a word nothing supports,
# name the words and ask again rather than failing the message. One honest
# word out of place should cost a rewrite, not the outreach.
VERIFY_ATTEMPTS = 3

SHORTEN = """Your draft is {actual} characters. The limit is {limit}.

Cut it to under {limit} characters. Remove whole sentences rather than trimming
words -- a message that ends mid-thought is worse than a shorter one. Keep the
specific reason for reaching out; drop the context around it.

Output the shortened message body only.

DRAFT TO SHORTEN:
{body}"""


# Conversational words a message needs and a cover letter does not. Same rule
# as letter.CONNECTIVES (ADR 0007): nothing here may name a skill, a tool, an
# outcome or a quality. "quick" and "worth" are how people write; "diagnosing",
# "stakeholders" and "accustomed" are claims, and stay out deliberately so that
# a draft using them is rewritten rather than waved through.
#
# The last four lines are the vocabulary of the request itself: "referral" was
# being rejected on a referral ask. Note there are no comments inside the
# string below -- a "#" line in there is split into words and silently
# allowlists every word of the comment, which is how "purpose", "message" and
# "claim" briefly became supported vocabulary.
OUTREACH_CONNECTIVES = frozenset("""
hi hello there quick quickly brief briefly worth appreciate grateful
open happy glad willing free busy sorry sure
reach reaching out back down up over along
let lets letting ask asking asked answer answering
share sharing sharing send sends sent note noting
context background focus focused focusing
earlier later soon now today tomorrow week
clearly directly simply just really actually maybe perhaps
helpful helpfully exactly describe describes describing
emphasise emphasises emphasize emphasizes mention mentions mentioned
read reads reading seen saw
if so then than that this these those it its
mind minds wonder wondering curious
chat chatting call calling meet meeting
question questions thought thoughts
approach approaches side sides
require requires required
referral referrals refer refers referring referred
introduction introductions introduce introducing
connect connects connecting connection network networking
informational follow following up thank thanks
""".split())


# What the model is shown of the posting. Smaller than a drafting call's
# budget on purpose -- it is here to answer "why this role", not to be quoted,
# and the word check rejects anything it borrows about the sender -- but it
# goes through the same function, in jsa/posting.py.
MAX_POSTING_CHARS = posting_text.OUTREACH_CHARS


def salutation(name: str) -> str:
    """The name you would actually write after "Hi".

    The model was handed the contact's full name and guessed. Given a record
    reading "Dr. Alice Hernandez Ruiz" it may write any part of it.
    """
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    parts = [p for p in parts if p.rstrip(".").lower() not in
             {"mr", "mrs", "ms", "dr", "prof"}]
    return parts[0] if parts else "there"


def sign(body: str, profile: dict[str, Any]) -> str:
    """Append the sender's name, which the prompt promises and did not get."""
    ident = profile.get("identity") or {}
    name = (ident.get("preferred_name") or ident.get("full_name") or "").strip()
    if not name or body.rstrip().endswith(name):
        return body
    return body.rstrip() + "\n" + name


class OutreachError(RuntimeError):
    """A draft could not be produced safely."""


@dataclass
class Draft:
    contact_id: int
    job_id: int | None
    channel: str
    purpose: str
    body: str
    # A warning when the job's posting has too little text to say anything
    # true about the role (Plan 5). Only with a job; not stored.
    thin_note: str = ""

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


def _names(profile: dict[str, Any], contact: Any) -> list[str]:
    """The sender's names (they sign the message) and the recipient's (you
    address them): allowed in any sentence."""
    ident = profile.get("identity") or {}
    names = [str(ident[k]) for k in ("preferred_name", "full_name") if ident.get(k)]
    if contact is not None and _has(contact, "name") and contact["name"]:
        names.append(str(contact["name"]))
    return names


def strict_vocabulary(profile: dict[str, Any], job: dict[str, Any] | None,
                      contact: Any = None) -> set[str]:
    """Every stem a sentence about the sender (or an unclear one) may use:
    the profile, the job's title, company and location, the connectives,
    and the two people's names. Nothing about the recipient's job or the
    posting's stack (plan 38)."""
    from . import letter

    stems = letter.allowed_stems(profile, job or {})
    stems |= {letter._stem(w) for w in OUTREACH_CONNECTIVES}
    for value in _names(profile, contact):
        stems |= {letter._stem(w) for w in letter._words(value)}
    return stems


def _about_them(job: dict[str, Any] | None, contact: Any) -> str:
    """What a sentence about the recipient, their team or company may also
    say: the recipient's title and company, and the posting's tech stack
    (the enrichment read it off the posting)."""
    extra: list[str] = []
    if job:
        raw = job["tech_stack"] if _has(job, "tech_stack") else None
        if raw:
            import json
            try:
                extra.extend(str(x) for x in (json.loads(raw) or []))
            except (ValueError, TypeError):
                pass
    if contact is not None:
        for key in ("title", "company_name"):
            if _has(contact, key) and contact[key]:
                extra.append(str(contact[key]))
    return " ".join(extra)


def allowed_vocabulary(profile: dict[str, Any], job: dict[str, Any] | None,
                       contact: Any = None) -> set[str]:
    """Every word stem any sentence of an outreach message may use: the
    strict vocabulary, plus what only sentences about the recipient may say.

    The posting's prose is deliberately NOT included, exactly as in a cover
    letter: allowing it would let the model quote the employer's description
    of the job back as a description of the candidate (ADR 0007, 0010).
    """
    from . import letter

    return strict_vocabulary(profile, job, contact) | {
        letter._stem(w) for w in letter._words(_about_them(job, contact))}


def checked_sentences(body: str, profile: dict[str, Any], job: dict[str, Any] | None,
                      contact: Any = None) -> list[dict[str, Any]]:
    """The message sentence by sentence (plan 38, ADR 0010).

    A sentence about the recipient, their team or company (it starts with
    their name, "you", "your", or the recipient's or job's company) may also
    use the recipient's title and company and the posting's stack, but no
    first person, nothing standing in for the sender, no negation, and no
    number those don't contain. Every other sentence is about the sender, or
    unclear, and is held to the strict vocabulary.
    """
    from . import letter, subject

    strict = strict_vocabulary(profile, job, contact)
    source = _about_them(job, contact)
    about = strict | {letter._stem(w) for w in letter._words(source)}
    others = [str(contact[k]) for k in ("name", "company_name")
              if contact is not None and _has(contact, k) and contact[k]]
    if contact is not None and _has(contact, "name") and contact["name"]:
        others.append(salutation(str(contact["name"])))      # "Hi Dana, your team…"
    ident = profile.get("identity") or {}
    person = [str(ident.get(k) or "") for k in ("full_name", "preferred_name")]
    out = []
    for i, s in enumerate(subject.sort(body, (job or {}).get("company") if job else None,
                                       person, attitude_frame=False, others=others), 1):
        words = letter._words(s.body)
        problems: list[str] = []
        if s.kind == "employer":
            refused = sorted({w for w in words if not _supported(w, about)})
            if subject.negated(s.body):
                problems.append(f"sentence {i} says what they don't do or have")
            stray = [n for n in subject.numbers(s.body) if n not in subject.numbers(source)]
            if stray:
                problems.append(f"sentence {i}: the number {stray[0]} is not theirs to state")
        else:
            refused = sorted({w for w in words if not _supported(w, strict)})
        out.append({"index": i, "text": s.text,
                    "kind": "recipient" if s.kind == "employer" else "strict",
                    "refused": refused, "problems": problems})
    return out


def _has(row: Any, key: str) -> bool:
    try:
        return row[key] is not None or True
    except (IndexError, KeyError, TypeError):
        return False


def unsupported_words(body: str, profile: dict[str, Any],
                      job: dict[str, Any] | None,
                      contact: Any = None) -> list[str]:
    """The message's own words that nothing supports. Empty means it may go.

    Sentence by sentence (plan 38): the stack and the recipient's title and
    company only in sentences about the recipient. With no job there is no
    posting and no stack, but the message is still held to the profile."""
    return sorted({w for s in checked_sentences(body, profile, job, contact)
                   for w in s["refused"]})


def rule_problems(body: str, profile: dict[str, Any], job: dict[str, Any] | None,
                  contact: Any = None) -> list[str]:
    """What a sentence about the recipient may not do: deny, or count."""
    return [p for s in checked_sentences(body, profile, job, contact) for p in s["problems"]]


def _supported(word: str, allowed: set[str]) -> bool:
    """Is this word's stem allowed, allowing for a plural the stemmer misses?

    tailor._stem only strips a suffix when the word is longer than five
    characters, which keeps "less" from becoming "le" -- and means "LLMs"
    never reduces to "LLM". Measured: that alone rejected three real drafts
    whose only offence was writing an acronym in the plural. Fixed here, for
    outreach's own check, rather than in a stemmer three other modules share.
    """
    from . import letter

    stem = letter._stem(word)
    if stem in allowed:
        return True
    return bool(word.endswith("s") and letter._stem(word[:-1]) in allowed)


def verify_message(body: str, profile: dict[str, Any],
                   job: dict[str, Any] | None = None,
                   contact: Any = None) -> str:
    """No banned claims, and nothing the profile cannot support.

    The banned-term and degree-phrase rules alone are not verification, and
    for a long time this function was only those. Measured: a draft inventing
    an employer, a team of twelve, six years' tenure, a $4M revenue figure and
    a professional certification passed without objection, while this module's
    own docstring promised "the same verifier as tailoring". A referral ask is
    read by a person who may check, which is exactly why the weaker rule was
    the wrong one to have here.

    The word-level check is the one cover letters got in ADR 0007, for the
    same reason: prose with no source bullet needs its vocabulary bounded.
    """
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
    # The same pair the written answers use (plan 38): the three phrases
    # above missed "I completed my CS degree".
    from .facts import _edu, claims_a_degree
    from .letter import _DEGREE
    from .prep import DegreeClaimError, assert_no_degree_claim
    try:
        assert_no_degree_claim(body)
    except DegreeClaimError as exc:
        raise FabricationError(f"outreach draft implies a degree ({exc})") from exc
    held = claims_a_degree(" ".join(str(_edu(profile).get("credential") or "").split()))
    if not held and _DEGREE.search(body):
        raise FabricationError("outreach draft mentions a degree, and your profile "
                               "has none that was conferred")

    new = unsupported_words(body, profile, job, contact)
    if new:
        raise FabricationError(
            "outreach draft uses words nothing supports — neither your "
            "profile, this posting, nor the recipient: "
            + ", ".join(new[:10])
        )
    rules = rule_problems(body, profile, job, contact)
    if rules:
        raise FabricationError("outreach draft says something about the recipient it "
                               "can't know: " + "; ".join(rules[:3]))
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


def attempts(prompt: str, system: str, profile: dict[str, Any], job_row: Any,
             contact: Any, *, limit: int, models: list[str] | None = None,
             tries: int = VERIFY_ATTEMPTS, split: bool | None = None
             ) -> tuple[str, list[str], list[str], list[str]]:
    """Draft, shorten to the limit, check, and retry naming what failed, up to
    `tries` drafts. Returns (last body, its unsupported words, its rule
    problems, every body drafted). Shared by draft() and the measurement."""
    split = SUBJECT_AWARE if split is None else split
    attempt_prompt = prompt
    body = ""
    unsupported: list[str] = []
    rules: list[str] = []
    bodies: list[str] = []
    for attempt in range(tries):
        result = llm.complete(
            attempt_prompt, system=system, models=models, max_tokens=600,
            temperature=0.4 if attempt == 0 else 0.2, thinking=False,
            purpose="outreach",
        )
        body = re.sub(r"\n{3,}", "\n\n", result.text.strip())

        # The model cannot count characters, so measure and hand the number
        # back. Bounded: if it still cannot get under the limit, that is an
        # honest failure. Truncating mid-sentence would be worse than none.
        for _ in range(SHORTEN_ATTEMPTS):
            if len(body) <= limit:
                break
            retry = llm.complete(
                SHORTEN.format(actual=len(body), limit=limit, body=body),
                system=system, models=models, max_tokens=600,
                temperature=0.3, thinking=False, purpose="outreach-shorten",
            )
            shorter = re.sub(r"\n{3,}", "\n\n", retry.text.strip())
            if not shorter:
                break
            body = shorter

        bodies.append(body)
        found = checked_sentences(body, profile, job_row, contact)
        unsupported = sorted({w for s in found for w in s["refused"]})
        rules = [p for s in found for p in s["problems"]]
        if not unsupported and not rules:
            break
        attempt_prompt = retry_prompt(prompt, found, unsupported, rules, split)
        scrub_prompt(attempt_prompt, profile)
    return body, unsupported, rules, bodies


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

    prompt, system = build(profile, job, contact, channel=channel, purpose=purpose)
    scrub_prompt(prompt, profile)

    job_row = dict(job) if job else None
    limit = CHANNEL_LIMITS.get(channel, 2000)
    body, unsupported, rules, _ = attempts(
        prompt, system, profile, job_row, contact, limit=limit, models=models,
        tries=VERIFY_ATTEMPTS)

    if rules and not unsupported:
        raise FabricationError(
            "The draft kept saying something about the recipient it can't know: "
            + "; ".join(rules[:3]) + ". Write this one yourself.")
    if unsupported:
        # This reads like a tool failure and usually is not one. Measured on
        # the first real run: the words the model kept reaching for on a
        # support posting were "diagnosing", "escalation", "communicating",
        # "adherence" — the vocabulary of support work, for a profile whose
        # bullets are field installation and LLM pipelines. The guard is
        # reporting that the material is not there, which is the thing the
        # operator needs to know before writing to a person who may check.
        raise FabricationError(
            f"Your profile does not have the material for this message. "
            f"After {VERIFY_ATTEMPTS} attempts the model still needed words "
            f"nothing supports — not your bullets, this posting, or the "
            f"recipient: " + ", ".join(unsupported[:10]) + ". "
            f"Either add a bullet that genuinely covers this ground, or "
            f"write this one yourself: a referral ask is read by a person "
            f"who may check."
        )

    # The prompt tells the model to sign off without a name because the name
    # is added here. Nothing added it, so every draft ended "Thanks," and
    # stopped, and the reader copied an unsigned message. Signed after the
    # word check: the name is ours, not the model's, and checking it would
    # reject the sender for being named.
    body = sign(body, profile)

    verify_message(body, profile, job=job_row, contact=contact)
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
    thin_note = (posting_text.thin(job["description"], job_id, for_what="outreach")
                 if job is not None else "")
    return Draft(contact_id, job_id, channel, purpose, body, thin_note)


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
