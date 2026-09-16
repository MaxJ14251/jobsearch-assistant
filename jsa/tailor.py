"""Resume and cover-letter tailoring.

The engine selects bullets from the master profile and rewords them against a
job description. It never writes new claims — and that distinction is enforced
here rather than requested in a prompt, because the models available on this
tier will not honour it reliably.

Three guarantees, each with a function that fails closed:

* `scrub_prompt`  — identity values never reach the API.
* `verify_draft`  — every output bullet traces to a profile bullet id, and its
                    wording stays close enough to the source that it makes no
                    new factual claim.
* do-not-claim    — nothing from `ats_keywords.aspirational_do_not_claim`
                    appears in generated text.

Why the paranoia is warranted: asked to tailor against a posting demanding
Kubernetes, a model that wants to be helpful writes a Kubernetes bullet. It
reads well, it fits the job, and it is false. The verifier is the only thing
standing between that and a document with the candidate's name on it.
See `tests/test_tailor.py::TestFabricationResistance`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import llm

# A reworded bullet must retain this share of its source's meaningful words.
# Below it, the text has drifted far enough to be asserting something new.
MIN_SOURCE_OVERLAP = 0.45

# Below this, a bullet shares almost nothing with the source it cites. That is
# not a rewrite that wandered -- it is a different claim wearing a real id, and
# it raises rather than reverting.
#
# The boundary is measured, not guessed. Overlap against the cited source:
#     0.00  "Operated multi-region Kubernetes clusters serving PyTorch models"
#           against a video-pipeline bullet -- wholesale invention
#     0.27  a real drifted rewrite observed in production
#     0.73  a good rewrite observed in production
# 0.20 sits in the gap between invention and imprecision.
FABRICATION_FLOOR = 0.20

# How much of a posting the model is shown. 88% of the tracker's descriptions
# are longer than this (median 5,668 chars, longest 10,661), so for most jobs
# the back half is invisible to tailoring -- requirements stated late in a
# posting cannot influence the draft. The number was previously inline and
# unnamed; enrich.py independently uses 6000 for the same job, and the two have
# never been reconciled. Raising it is a token-budget decision that needs its
# own evidence, so for now it is named, measured, and surfaced by `jsa tailor`
# rather than silently applied.
MAX_DESCRIPTION_CHARS = 4000

# Words carried by every bullet; they say nothing about whether meaning survived.
_STOP = {
    "a", "an", "and", "the", "to", "of", "in", "on", "for", "with", "from",
    "by", "at", "as", "into", "that", "this", "it", "its", "is", "are", "was",
    "were", "be", "been", "using", "used", "use", "via", "across", "through",
    "while", "including", "such", "also", "more", "than", "then", "their",
}


class FabricationError(RuntimeError):
    """Generated text made a claim the profile does not support."""


class UndecidedPreferenceError(RuntimeError):
    """A preference the draft depends on has never been answered.

    Goal 01 specified this guard and it was never built, so tailoring would
    happily proceed on a null work_authorization and let the model decide what
    to say about it. Null is not "no constraint" -- it is "nobody has said",
    and a model asked to write around an unanswered question invents an answer.

    See docs/decisions/0001-compensation-and-work-authorization.md.
    """


class IdentityLeakError(RuntimeError):
    """An outbound prompt contained personal identifying information."""


@dataclass(frozen=True)
class SourceBullet:
    id: str
    text: str
    tags: tuple[str, ...]
    family: str
    strength: int
    origin: str          # "experience" | "project"
    parent: str          # company or project name


@dataclass
class DraftBullet:
    source_id: str
    text: str


@dataclass
class TailoredDraft:
    job_id: int | None = None
    summary_id: str = ""
    summary: str = ""
    bullets: list[DraftBullet] = field(default_factory=list)
    keywords_matched: list[str] = field(default_factory=list)
    keywords_missing: list[str] = field(default_factory=list)
    model: str = ""
    # Hash of the exact prompt that produced this draft. Recorded so a document
    # can be traced back to its input without storing the input itself.
    prompt_hash: str = ""
    # Bullet ids whose rewrite drifted too far and were reverted to the profile
    # text verbatim. Surfaced by `jsa tailor`; never silent.
    reverted: list[str] = field(default_factory=list)


# --- profile access ---------------------------------------------------------


def collect_bullets(profile: dict[str, Any]) -> dict[str, SourceBullet]:
    """Every reusable bullet in the profile, keyed by id."""
    out: dict[str, SourceBullet] = {}
    for exp in profile.get("experience") or []:
        for b in exp.get("bullets") or []:
            out[b["id"]] = SourceBullet(
                id=b["id"], text=" ".join(b["text"].split()),
                tags=tuple(b.get("tags") or []), family=exp.get("family", ""),
                strength=int(b.get("strength", 2)), origin="experience",
                # The ENTRY id, not the company. Two roles at one employer
                # (Sales Representative and Installer, both at Riverton) collided on
                # the company name, and the renderer printed one role's bullets
                # under both.
                parent=exp.get("id") or exp.get("company", ""),
            )
    for proj in profile.get("projects") or []:
        for b in proj.get("bullets") or []:
            out[b["id"]] = SourceBullet(
                id=b["id"], text=" ".join(b["text"].split()),
                tags=tuple(b.get("tags") or []), family=proj.get("family", ""),
                strength=int(b.get("strength", 2)), origin="project",
                parent=proj.get("id") or proj.get("name", ""),
            )
    return out


def do_not_claim(profile: dict[str, Any]) -> list[str]:
    return list(
        (profile.get("ats_keywords") or {}).get("aspirational_do_not_claim") or []
    )


def identity_values(profile: dict[str, Any]) -> list[str]:
    """Strings that must never appear in an outbound prompt."""
    ident = profile.get("identity") or {}
    loc = ident.get("location") or {}
    links = profile.get("links") or {}
    candidates = [
        ident.get("full_name"), ident.get("preferred_name"),
        ident.get("email"), ident.get("phone"),
        loc.get("street"), loc.get("postal_code"),
        links.get("linkedin"), links.get("github"),
        links.get("portfolio"), links.get("website"),
    ]
    out = []
    for value in candidates:
        if not isinstance(value, str):
            continue
        value = value.strip()
        # A first name alone is too short to match on safely; a full name is not.
        if len(value) >= 5:
            out.append(value)
    return out


# --- guarantee 1: identity never leaves the machine -------------------------


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def scrub_prompt(prompt: str, profile: dict[str, Any]) -> str:
    """Return `prompt` unchanged, or raise if it carries identity.

    Deliberately raises rather than silently redacting: a leak means the
    caller built the prompt wrong, and quietly patching it would hide the bug
    while the next caller reintroduces it.
    """
    haystack = prompt.lower()
    for value in identity_values(profile):
        if value.lower() in haystack:
            raise IdentityLeakError(
                f"prompt contains identity value {value[:18]!r}; "
                "personal details are merged into the document locally, "
                "never sent to the API"
            )
    # Phone numbers survive reformatting, so compare digits too.
    phone = (profile.get("identity") or {}).get("phone") or ""
    phone_digits = _digits(phone)
    if len(phone_digits) >= 10 and phone_digits[-10:] in _digits(prompt):
        raise IdentityLeakError("prompt contains the phone number")
    return prompt


# --- guarantee 2: every claim traces to the profile -------------------------


def _words(text: str) -> set[str]:
    return {
        w for w in re.findall(r"[a-z0-9+#.]+", (text or "").lower())
        if w not in _STOP and len(w) > 2
    }


def term_pattern(term: str) -> re.Pattern[str]:
    r"""A boundary-safe matcher for a technology name.

    `\bC\+\+\b` never matches: a word boundary cannot sit between '+' and a
    space, so the trailing `\b` fails on "C++ and Java". The same bug hides
    C#, F# and .NET. Lookarounds that treat +, # and . as part of the token
    handle all of them.
    """
    return re.compile(
        rf"(?<![\w+#.]){re.escape(term)}(?![\w+#])", re.IGNORECASE
    )


def source_overlap(reworded: str, source: str) -> float:
    """Share of the source's meaningful words still present after rewording."""
    src = _words(source)
    if not src:
        return 0.0
    return len(src & _words(reworded)) / len(src)


def verify_draft(draft: TailoredDraft, profile: dict[str, Any]) -> TailoredDraft:
    """Reject anything untraceable or unfaithful.

    Three failure modes, and they are NOT the same thing:

      unknown source id   fabrication. Raises.
      banned term         fabrication. Raises.
      drift below the
      overlap threshold   the rewrite wandered into a claim the source does not
                          support. A safe answer exists -- the source text,
                          unchanged -- so the bullet REVERTS to it and the id is
                          recorded on draft.reverted.

    Reverting is not a weakened guard: the drifted sentence never reaches the
    document either way. It changes the consequence from "the whole draft dies"
    to "that one bullet is the original", which is the difference between a
    resume with five tailored bullets and no resume at all. `jsa tailor` prints
    what reverted, so the trade is visible rather than silent.
    """
    sources = collect_bullets(profile)
    banned = [t for t in do_not_claim(profile) if t]

    kept: list[DraftBullet] = []
    reverted: list[str] = []
    for bullet in draft.bullets:
        source = sources.get(bullet.source_id)
        if source is None:
            raise FabricationError(
                f"bullet cites unknown source id {bullet.source_id!r} — "
                "generated text must map to a master_profile.yaml bullet"
            )
        overlap = source_overlap(bullet.text, source.text)
        if overlap < FABRICATION_FLOOR:
            raise FabricationError(
                f"bullet {bullet.source_id!r} shares almost nothing with the "
                f"source it cites ({overlap:.0%} word overlap): "
                f"{bullet.text[:90]!r}"
            )
        if overlap < MIN_SOURCE_OVERLAP:
            kept.append(DraftBullet(source_id=bullet.source_id, text=source.text))
            reverted.append(bullet.source_id)
        else:
            kept.append(bullet)
    draft.bullets = kept
    draft.reverted = reverted

    generated = " ".join([draft.summary] + [b.text for b in draft.bullets]).lower()
    for term in banned:
        if term_pattern(term).search(generated):
            raise FabricationError(
                f"generated text claims {term!r}, which is on the "
                "aspirational_do_not_claim list"
            )
    return draft


# --- keyword gap ------------------------------------------------------------


def keyword_gap(
    description: str, profile: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """(matched, missing) — what the posting wants versus what can be claimed.

    `missing` is the honest gap report. It must never be trimmed to flatter the
    document; it is what tells the candidate where they actually stand.
    """
    blob = (description or "").lower()
    have = [k for k in ((profile.get("ats_keywords") or {}).get("have") or [])]
    matched = [k for k in have if term_pattern(k).search(blob)]
    missing = [t for t in do_not_claim(profile) if term_pattern(t).search(blob)]
    return matched, missing


# --- selection --------------------------------------------------------------


def pick_summary(profile: dict[str, Any], track: str, description: str) -> dict:
    """Choose the summary variant that fits the role."""
    summaries = {s["family"]: s for s in profile.get("summaries") or []}
    blob = (description or "").lower()
    if track == "sales" or re.search(
        r"customer[- ]facing|client[- ]facing|account executive|sales", blob
    ):
        return summaries.get("customer_facing_technical") or summaries["general"]
    if re.search(r"\bllm\b|generative ai|agentic|prompt", blob):
        return summaries.get("ai_engineering") or summaries["general"]
    return summaries.get("general") or next(iter(summaries.values()))


# A tag's worth is how rare it is. Measured against the 514 postings in the
# tracker: "systems" appears in 86.6% of them, "hardware" in 46.5% -- they say
# almost nothing about whether a bullet fits. "claude" appears in 3.5%,
# "commissioning" in 4.3%.
#
# This mattered concretely: a fire-alarm installation bullet tied for top score
# on a Rocket Lab ROBOTICS posting, on hits for "systems" ("space systems at
# Rocket Lab") and "hardware" ("build real hardware") -- both from the
# company's own blurb rather than its requirements.
#
# A common tag still counts for something, hence the floor; it just cannot
# outweigh a rare one.
MIN_TAG_WEIGHT = 0.15


def tag_weights(
    con, tags: set[str] | None = None, corpus: list[str] | None = None
) -> dict[str, float]:
    """Rarity weight per tag, from the postings actually in the tracker.

    Returns {} when there is no corpus to learn from -- a fresh clone has an
    empty jobs table, and inventing weights from nothing would be worse than
    weighting every tag equally.
    """
    if corpus is None:
        if con is None:
            return {}
        try:
            corpus = [
                (row[0] or "").lower() for row in con.execute(
                    "SELECT description FROM jobs WHERE description IS NOT NULL")
            ]
        except Exception:  # noqa: BLE001 -- a missing table is not fatal here
            return {}
    total = len(corpus)
    if total < 20:                    # too little to distinguish rare from common
        return {}
    weights = {}
    for tag in sorted(tags or ()):
        pattern = re.compile(rf"\b{re.escape(tag.replace('-', ' ').lower())}")
        seen = sum(1 for text in corpus if pattern.search(text))
        weights[tag] = max(MIN_TAG_WEIGHT, 1.0 - (seen / total))
    return weights


# A bullet scoring far below the best one is padding, not evidence. Six of ten
# bullets were previously chosen every time regardless of fit, which is not
# selection.
RELEVANCE_FLOOR = 0.75

# A ratio alone is brittle: on the Replit posting the top bullet scored 7.85
# and the next 5.87, so a 0.75 floor landed at 5.89 and cut the second-best
# bullet by 0.02, leaving a one-bullet resume. A floor decides what is padding;
# it must not decide that a resume has nothing to say.
MIN_BULLETS = 4

# Whether a bullet comes from the right kind of work outweighs any single
# shared word. A fire-alarm bullet genuinely shares "troubleshooting" with a
# robotics posting; it is still not software engineering evidence.
FAMILY_BONUS = 3.0


def select_bullets(
    profile: dict[str, Any], description: str, track: str, limit: int = 6,
    weights: dict[str, float] | None = None,
) -> list[SourceBullet]:
    """Rank profile bullets against the posting. Deterministic, no model call.

    `weights` come from tag_weights() and discount tags that appear in almost
    every posting. Without them every tag counts equally, which is how a
    fire-alarm bullet tied for top score on a robotics role.
    """
    blob = (description or "").lower()
    preferred = (
        {"sales", "technical_field"} if track == "sales"
        else {"ai_engineering", "data_ml"}
    )
    weights = weights or {}
    scored: list[tuple[float, int, int, str, SourceBullet]] = []
    for bullet in collect_bullets(profile).values():
        hit_value = sum(
            weights.get(tag, 1.0)
            for tag in bullet.tags
            if re.search(rf"\b{re.escape(tag.replace('-', ' ').lower())}", blob)
        )
        # Strength is deliberately NOT in the score. As (4 - strength) it added
        # +3 to almost every bullet, compressing a 7.09-to-4.96 range in which
        # no relevance floor could separate anything -- the constant swamped
        # the signal. It is a tiebreaker, and it is used as one below.
        score = hit_value * 2.0
        relevant = bullet.family in preferred
        score += FAMILY_BONUS if relevant else 0.0
        # Ties used to break on id, so "b_inst_codes" beat "b_vid_goal" purely
        # on the alphabet. Break on what the tie is actually about: whether the
        # bullet is from a relevant family, then how strong it is. The id
        # remains last, only so the order is deterministic.
        scored.append((score, 0 if relevant else 1, bullet.strength,
                       bullet.id, bullet))
    scored.sort(key=lambda row: (-row[0], row[1], row[2], row[3]))

    if not scored:
        return []
    best = scored[0][0]
    keep = [row for row in scored[:limit] if row[0] >= best * RELEVANCE_FLOOR]
    if len(keep) < MIN_BULLETS:
        keep = scored[:min(MIN_BULLETS, limit, len(scored))]
    return [row[4] for row in keep]


# --- generation -------------------------------------------------------------

# The balance here is deliberate and was measured. An earlier version carried
# one instruction to rewrite and three prohibitions; the model read it, decided
# the safest move was to change nothing, and returned bullets verbatim -- 3 of 6
# byte-identical, median source overlap 1.00 across 18 bullets. The guards were
# never the problem. verify_draft already rejects invention at the door, so the
# prompt does not need to say it three times, and saying it three times is what
# suppressed the rewriting.
SYSTEM = (
    "You rewrite resume bullets so they speak directly to one specific job. "
    "Change emphasis, ordering and language. Every fact you write must already "
    "be present in the bullet you were given -- you are re-presenting evidence, "
    "never adding it. An automated verifier rejects any bullet that introduces "
    "something new, so invention costs you the whole draft and gains nothing."
)

PROMPT = """Rewrite each bullet so a hiring manager for THIS role sees the
connection immediately. Do not return a bullet unchanged.

HOW TO REWRITE
- Lead with whatever part of the bullet this role cares about most.
- Borrow the posting's own vocabulary where it genuinely describes the same
  work you were given. Where it does not, keep your own words.
- Completed work reads in the past tense. Ongoing work reads in the present.
- One sentence each. Tighten rather than pad.

THE CONSTRAINT
Every fact must already exist in the source bullet. If this role wants
something the bullet does not contain, leave it out -- the gap is reported
separately and honestly, and is not your problem to solve.

- Return JSON: {{"summary": "...", "bullets": [{{"id": "<same id>", "text": "..."}}]}}

ROLE: {title}
WHAT THE ROLE INVOLVES:
{description}

SUMMARY TO ADAPT:
{summary}

BULLETS (keep each id):
{bullets}"""


# Preferences a draft cannot honestly be written without. compensation floor
# is NOT here: a floor is about which jobs to pursue, not about what a resume
# says, and the operator may legitimately have none.
REQUIRED_PREFERENCES = ("work_authorization",)


def require_decided_preferences(profile: dict[str, Any]) -> None:
    """Refuse to draft while a required preference is still null.

    Fails loudly naming the field, rather than guessing a value.
    """
    prefs = profile.get("job_search_preferences") or {}
    missing = [
        name for name in REQUIRED_PREFERENCES
        if prefs.get(name) is None
        or (isinstance(prefs.get(name), str) and not prefs[name].strip())
    ]
    if missing:
        raise UndecidedPreferenceError(
            "master_profile.yaml: job_search_preferences."
            + ", ".join(missing)
            + " is still null. Fill it in -- this goes into generated documents "
              "as a statement of fact, and nothing here will guess it for you."
        )


def tailor(
    job: dict[str, Any],
    profile: dict[str, Any],
    *,
    models: list[str] | None = None,
    weights: dict[str, float] | None = None,
) -> TailoredDraft:
    """Produce a verified draft. Raises rather than returning unsafe output."""
    require_decided_preferences(profile)   # before any work, before any network
    description = job.get("description") or ""
    track = job.get("track") or "engineering"
    summary = pick_summary(profile, track, description)
    chosen = select_bullets(profile, description, track, weights=weights)
    matched, missing = keyword_gap(description, profile)

    listing = "\n".join(f'- [{b.id}] {b.text}' for b in chosen)
    prompt = PROMPT.format(
        title=job.get("title") or "the role",
        description=description[:MAX_DESCRIPTION_CHARS],
        summary=" ".join(summary["text"].split()),
        bullets=listing,
    )
    scrub_prompt(prompt, profile)          # fails closed before any network call

    data, usage = llm.complete_json(
        prompt, system=SYSTEM, models=models, max_tokens=1200,
        temperature=0.3, thinking=False, attempts=2,
    )

    by_id = {b.id: b for b in chosen}
    bullets = []
    for item in (data.get("bullets") or []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        bid, text = item.get("id"), item.get("text")
        if isinstance(bid, str) and isinstance(text, str) and text.strip():
            bullets.append(DraftBullet(source_id=bid, text=" ".join(text.split())))
    # Anything the model dropped falls back to its source text verbatim, which
    # is always safe — the original claim, unchanged.
    seen = {b.source_id for b in bullets}
    for b in chosen:
        if b.id not in seen:
            bullets.append(DraftBullet(source_id=b.id, text=b.text))

    draft = TailoredDraft(
        job_id=job.get("id"),
        summary_id=summary["id"],
        summary=" ".join(str(data.get("summary") or summary["text"]).split())
        if isinstance(data, dict) else summary["text"],
        bullets=bullets,
        keywords_matched=matched,
        keywords_missing=missing,
        model=usage.model,
        prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
    )
    return verify_draft(draft, profile)
