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

from . import llm, posting

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

# How much of a posting the model is shown. Decided once, in jsa/posting.py,
# which carries the measurement behind the number; re-exported here because
# letter.py and the CLI have always imported it from tailor.
MAX_DESCRIPTION_CHARS = posting.MAX_DESCRIPTION_CHARS

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
    # Why each one reverted, keyed by bullet id ("summary" for the summary).
    revert_reasons: dict[str, str] = field(default_factory=dict)


# --- profile access ---------------------------------------------------------


def entry_key(entry: dict[str, Any], *fields: str) -> str:
    """A key that distinguishes two entries at the same employer.

    `id:` when the profile sets one, otherwise everything that identifies the
    entry joined together. Never just the company: that is what collapsed
    "Account Representative at Riverton" and "Service Technician at
    Riverton" into one bucket.
    """
    if entry.get("id"):
        return str(entry["id"])
    parts = [str(entry.get(f) or "").strip() for f in fields]
    return " :: ".join(p for p in parts if p)


def public_repo(project: dict[str, Any]) -> str:
    """The project's public link, or "" when it has none.

    One test for both the resume (which prints it) and the ranking (which
    prefers a project that has one on a tie), so the two cannot disagree.
    """
    repo = project.get("repo")
    if isinstance(repo, str) and repo.strip().startswith(("http://", "https://")):
        return repo.strip()
    return ""


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
                # collided on the company name and the renderer printed one
                # role's bullets under both headings -- a resume claiming the
                # installer's work as the sales rep's.
                #
                # Preferring the id fixed it for a profile that has ids. The
                # fallback still collapsed, and `id:` is not required: a
                # newcomer who was promoted without changing employer hit the
                # original bug with no warning. The fallback now carries the
                # title, which is what distinguishes the two roles.
                parent=entry_key(exp, "company", "title"),
            )
    for proj in profile.get("projects") or []:
        for b in proj.get("bullets") or []:
            out[b["id"]] = SourceBullet(
                id=b["id"], text=" ".join(b["text"].split()),
                tags=tuple(b.get("tags") or []), family=proj.get("family", ""),
                strength=int(b.get("strength", 2)), origin="project",
                parent=entry_key(proj, "name", "title"),
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
        w for w in (t.rstrip(".") for t in
                    re.findall(r"[a-z0-9+#.]+", (text or "").lower()))
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


# --- what a rewrite ADDED --------------------------------------------------------
# source_overlap measures recall: how much of the source survived. It never
# looked at what was appended. A rewrite that kept the whole bullet and bolted
# on "during high-priority support scenarios" scored HIGHER, not lower. Across
# every document generated before this check existed:
#
#     tense changes only ("Building" -> "Built")        added share 0.00-0.10
#     invented claims ("strict turnaround requirements",
#       "debugs live data-quality issues",
#       "high-priority support scenarios")               added share 0.20-0.43
#
# A word counts as supported if it appears -- after crude stemming -- in the
# source bullet, in that bullet's own tags (the operator's words about it), or
# in a sibling bullet of the same experience or project (true facts about the
# same work). Anything else is new.

INVENTION_CEILING = 0.15

# Crude stemming cannot see these.
_IRREGULAR = {
    "built": "build", "drove": "drive", "ran": "run", "led": "lead",
    "wrote": "write", "made": "make", "grew": "grow", "won": "win",
    "took": "take", "gave": "give", "sold": "sell", "taught": "teach",
}

# A leading verb in the past tense asserts that the work is finished.
_PAST_TENSE = frozenset(_IRREGULAR)


def _stem(word: str) -> str:
    word = word.rstrip(".")
    if word in _IRREGULAR:
        return _IRREGULAR[word]
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > 5 and word.endswith(suffix):
            word = word[: -len(suffix)]
            break
    return word.rstrip("e")


def _stems(text: str) -> set[str]:
    return {_stem(w) for w in _words(text)}


def added_share(rewrite: str, supported: set[str]) -> tuple[float, list[str]]:
    """Share of the rewrite's meaningful words that nothing supports."""
    words = _stems(rewrite)
    if not words:
        return 0.0, []
    new = sorted(words - supported)
    return len(new) / len(words), new


def _leading_word(text: str) -> str:
    match = re.match(r"\s*([A-Za-z]+)", text or "")
    return match.group(1).lower() if match else ""


def claims_completion(source: str, rewrite: str) -> bool:
    """True when ongoing or aspirational work is rewritten as finished.

    "Goal: reduce a multi-hour workflow" became "Reduced a multi-hour
    workflow". "Building a Python application" became "Built". For a project
    still in development, a past-tense lead verb is a claim that it shipped.
    Stemming cannot see this -- "reduce" and "reduced" share a stem -- so it is
    checked on its own.
    """
    src = _leading_word(source)
    if not (src == "goal" or src.endswith("ing")):
        return False
    lead = _leading_word(rewrite)
    return lead in _PAST_TENSE or (lead.endswith("ed") and lead != "need")


def _supported_stems(profile: dict[str, Any]) -> dict[tuple[str, str], set[str]]:
    """For each experience or project entry, every stem its bullets and tags use."""
    out: dict[tuple[str, str], set[str]] = {}
    for bullet in collect_bullets(profile).values():
        bucket = out.setdefault((bullet.origin, bullet.parent), set())
        bucket |= _stems(bullet.text)
        for tag in bullet.tags:
            bucket |= {_stem(part) for part in tag.lower().split("-")}
    return out


def _whole_profile_stems(profile: dict[str, Any]) -> set[str]:
    """What a summary may draw on: the operator's own words, anywhere."""
    stems: set[str] = set()
    for bucket in _supported_stems(profile).values():
        stems |= bucket
    for summary in profile.get("summaries") or []:
        stems |= _stems(str(summary.get("text", "")))
    for values in (profile.get("skills") or {}).values():
        for value in values or []:
            stems |= _stems(str(value))
    for entry in profile.get("experience") or []:
        stems |= _stems(f"{entry.get('title', '')} {entry.get('company', '')}")
    for entry in profile.get("projects") or []:
        stems |= _stems(str(entry.get("name", "")))
    return stems


def verify_draft(draft: TailoredDraft, profile: dict[str, Any]) -> TailoredDraft:
    """Reject anything untraceable or unfaithful.

    Raises -- the whole draft is refused:
      unknown source id        the bullet cites nothing real
      overlap < FABRICATION_FLOOR
                               it shares almost nothing with its source
      banned term              aspirational_do_not_claim, anywhere in the
                               text as generated -- checked before any revert

    Reverts -- that one bullet becomes the profile text, verbatim:
      overlap < MIN_SOURCE_OVERLAP     it dropped too much of the source
      added share > INVENTION_CEILING  it added claims nothing supports
      claims completion                ongoing work rewritten as finished

    The summary is held to the same added-share test against the whole
    profile, and reverts to the chosen summary variant if it fails.

    Reverting is not a weakened guard: the offending sentence never reaches the
    document either way. `jsa tailor` prints every revert with its reason.
    """
    sources = collect_bullets(profile)
    banned = [t for t in do_not_claim(profile) if t]
    supported = _supported_stems(profile)

    # 1. Structural failures first: they name the most fundamental defect.
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

    # 2. Banned terms, against what the model actually WROTE, before anything
    # is reverted. Otherwise a bullet claiming Kubernetes would simply revert
    # and the draft would pass quietly. An attempt to claim something the
    # operator has explicitly ruled out refuses the whole draft.
    generated = " ".join([draft.summary] + [b.text for b in draft.bullets]).lower()
    for term in banned:
        if term_pattern(term).search(generated):
            raise FabricationError(
                f"generated text claims {term!r}, which is on the "
                "aspirational_do_not_claim list"
            )

    # 3. Per-bullet reverts.
    kept: list[DraftBullet] = []
    reverted: list[str] = []
    reasons: dict[str, str] = {}
    for bullet in draft.bullets:
        source = sources[bullet.source_id]
        overlap = source_overlap(bullet.text, source.text)
        share, new = added_share(bullet.text, supported[(source.origin, source.parent)])
        reason = ""
        if overlap < MIN_SOURCE_OVERLAP:
            reason = f"dropped too much of the source ({overlap:.0%} kept)"
        elif share > INVENTION_CEILING:
            reason = f"added unsupported words: {', '.join(new[:6])}"
        elif claims_completion(source.text, bullet.text):
            reason = "rewrote ongoing work as finished"
        if reason:
            kept.append(DraftBullet(source_id=bullet.source_id, text=source.text))
            reverted.append(bullet.source_id)
            reasons[bullet.source_id] = reason
        else:
            kept.append(bullet)
    draft.bullets = kept
    draft.reverted = reverted
    draft.revert_reasons = reasons

    share, new = added_share(draft.summary, _whole_profile_stems(profile))
    if share > INVENTION_CEILING:
        original = next((s for s in profile.get("summaries") or []
                         if s.get("id") == draft.summary_id), None)
        if original is not None:
            draft.summary = " ".join(str(original.get("text", "")).split())
            reasons["summary"] = f"added unsupported words: {', '.join(new[:6])}"

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


def pick_summary(profile: dict[str, Any], track: str,
                 description: str) -> dict | None:
    """Choose the summary variant that fits the role.

    Falls back in order: the family the posting calls for, `general`, the
    first summary. None when the profile has no summaries; the resume then has
    no Summary section, and `jsa doctor` reports the gap. This used to index
    summaries["general"], a KeyError on any profile without one.
    """
    listed = [s for s in profile.get("summaries") or [] if isinstance(s, dict)]
    if not listed:
        return None
    summaries = {s.get("family"): s for s in listed}
    blob = (description or "").lower()
    wanted = None
    if track == "sales" or re.search(
        r"customer[- ]facing|client[- ]facing|account executive|sales", blob
    ):
        wanted = "customer_facing_technical"
    elif re.search(r"\bllm\b|generative ai|agentic|prompt", blob):
        wanted = "ai_engineering"
    return summaries.get(wanted) or summaries.get("general") or listed[0]


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


# --- role kind and tag vocabulary ----------------------------------------------
# See docs/decisions/0005-role-kind-and-tag-vocabulary.md.
#
# Which families a resume should favour depends on what the role IS, and the
# least noisy statement of that is the title. The binary track used to decide
# it, so a "Premium Support Engineer" (track=engineering) handed its +3.0 to AI
# project work and no customer-facing bullet could compete. Deriving the
# preference from the posting's own tag mass was measured and rejected: on a
# robotics posting, "systems" and "hardware" made fire-alarm work win.

# "support" alone matched "Life Support Systems", "Ground Support Equipment",
# "Fleet Support" and "Tooling Design and Support" -- 5 of 29 matches were
# hardware roles. It counts here only with customer context.
SUPPORT_TITLE = re.compile(
    r"\b(?:"
    r"(?:technical|premium|customer|product|it)\s+support"
    r"|support\s+(?:engineer|specialist|analyst)"
    r"|customer\s+success"
    r"|solutions?\s+(?:engineer|architect|consultant)"
    r"|forward[- ]deployed"
    r"|implementation\s+(?:engineer|specialist|consultant)"
    r"|onboarding"
    r"|technical\s+account"
    r")\b",
    re.IGNORECASE,
)

SALES_TITLE = re.compile(
    r"\b(?:sales|account\s+executive|business\s+development|account\s+manager)\b",
    re.IGNORECASE,
)

ROLE_FAMILY_BONUS: dict[str, dict[str, float]] = {
    "engineering": {"ai_engineering": FAMILY_BONUS, "data_ml": FAMILY_BONUS},
    # A support role at an AI company still values the AI work, so it keeps
    # half weight rather than none. The resume should show both.
    "support": {
        "sales": FAMILY_BONUS, "technical_field": FAMILY_BONUS,
        "ai_engineering": FAMILY_BONUS / 2, "data_ml": FAMILY_BONUS / 2,
    },
    "sales": {"sales": FAMILY_BONUS, "technical_field": FAMILY_BONUS},
}


def role_kind(title: str | None, track: str | None) -> str:
    """engineering, support or sales -- printed by `jsa tailor` so it can be disputed."""
    if track == "sales" or SALES_TITLE.search(title or ""):
        return "sales"
    if SUPPORT_TITLE.search(title or ""):
        return "support"
    return "engineering"


# Words that carry no meaning once split from their tag: "customer-facing" is
# about customers, not about facing.
FILLER_WORDS = frozenset({
    "facing", "adjacent", "native", "based", "driven", "focused", "oriented",
    "processing", "selling",
})

# A component is weaker evidence than the phrase the operator actually wrote.
COMPONENT_DISCOUNT = 0.6


def tag_components(tag: str) -> list[str]:
    """The meaningful words inside a hyphenated tag. Empty for plain tags."""
    if "-" not in tag:
        return []
    return [w for w in tag.lower().split("-") if w not in FILLER_WORDS and len(w) > 2]


def vocabulary(profile: dict[str, Any]) -> set[str]:
    """Every term tag_weights() needs: each tag, and each tag's components."""
    terms: set[str] = set()
    for bullet in collect_bullets(profile).values():
        for tag in bullet.tags:
            terms.add(tag)
            terms.update(tag_components(tag))
    return terms


def is_dead(tag: str, weights: dict[str, float]) -> bool:
    """True only when the tracker proves the phrase appears in no posting.

    With no weights -- a fresh clone, an empty tracker -- nothing is known to be
    dead, so nothing is reinterpreted. That is the safe default.
    """
    return tag in weights and weights[tag] >= 1.0


def _mentions(term: str, blob: str) -> bool:
    return re.search(r"\b" + re.escape(term.replace("-", " ").lower()), blob) is not None


def tag_value(tag: str, blob: str, weights: dict[str, float]) -> float:
    """How much evidence one tag contributes to this posting.

    The phrase first. Only if the phrase matches no posting at all does a
    hyphenated tag fall back to its components, discounted. Splitting every tag
    was measured and rejected: it let a fire-alarm bullet back into a robotics
    resume.
    """
    if _mentions(tag, blob):
        return weights.get(tag, 1.0)
    if not is_dead(tag, weights):
        return 0.0
    best = 0.0
    for word in tag_components(tag):
        if _mentions(word, blob):
            best = max(best, weights.get(word, 1.0) * COMPONENT_DISCOUNT)
    return best


def tag_report(profile: dict[str, Any], weights: dict[str, float],
               corpus_size: int) -> list[dict[str, Any]]:
    """Every tag, how many postings it reaches, and whether it is dead.

    Advisory. It never edits the profile -- the profile is the operator's own
    description of their work.
    """
    def reach(term: str) -> int:
        weight = weights.get(term)
        if weight is None:
            return 0
        return round((1.0 - weight) * corpus_size) if weight > MIN_TAG_WEIGHT else -1

    rows = []
    owners: dict[str, list[str]] = {}
    for bullet in collect_bullets(profile).values():
        for tag in bullet.tags:
            owners.setdefault(tag, []).append(bullet.id)
    for tag in sorted(owners):
        dead = is_dead(tag, weights)
        revived = [(w, reach(w)) for w in tag_components(tag)
                   if dead and weights.get(w, 1.0) < 1.0]
        rows.append({
            "tag": tag,
            "postings": reach(tag),
            "dead": dead,
            "revived_by": revived,
            "bullets": owners[tag],
        })
    return rows


def select_bullets(
    profile: dict[str, Any], description: str, track: str, limit: int = 6,
    weights: dict[str, float] | None = None, title: str = "",
    keep_work_history: bool = True, keep_project: bool = True,
) -> list[SourceBullet]:
    """Rank profile bullets against the posting. Deterministic, no model call.

    `weights` come from tag_weights() over vocabulary(profile), and discount
    terms that appear in almost every posting. `title` decides the role kind,
    which decides which families of work are favoured -- see ADR 0005.

    Safe by construction: this only chooses among the operator's own bullets.
    Nothing here produces text.
    """
    blob = (description or "").lower()
    bonus = ROLE_FAMILY_BONUS[role_kind(title, track)]
    weights = weights or {}
    # How finished each project is, as a tiebreaker: (not released, no public
    # repo), so a released project with a link sorts first. Keyed the way
    # collect_bullets sets SourceBullet.parent. Experience is (False, False).
    maturity = {
        entry_key(proj, "name", "title"):
            (proj.get("status") != "released", not public_repo(proj))
        for proj in profile.get("projects") or []
    }
    scored: list[tuple[float, float, tuple[bool, bool], int, str, SourceBullet]] = []
    for bullet in collect_bullets(profile).values():
        hit_value = sum(tag_value(tag, blob, weights) for tag in bullet.tags)
        # Strength is deliberately NOT in the score. As (4 - strength) it added
        # +3 to almost every bullet, compressing a 7.09-to-4.96 range in which
        # no relevance floor could separate anything -- the constant swamped
        # the signal. It is a tiebreaker, and it is used as one below.
        family = bonus.get(bullet.family, 0.0)
        score = hit_value * 2.0 + family
        # Ties used to break on id, so "b_inst_codes" beat "b_vid_goal" purely
        # on the alphabet. Break on what the tie is actually about: how much the
        # role favours this kind of work, then how finished the project is (a
        # released one with a public repo first: on Kyber's empty posting the
        # alphabet picked an in-development tool over the released app), then
        # how strong the bullet is. The id remains last, only so the order is
        # deterministic. ADR 0005 section 11.
        ready = (maturity.get(bullet.parent, (False, False))
                 if bullet.origin == "project" else (False, False))
        scored.append((score, -family, ready, bullet.strength, bullet.id, bullet))
    scored.sort(key=lambda row: (-row[0], *row[1:5]))

    if not scored:
        return []
    best = scored[0][0]
    keep = [row for row in scored[:limit] if row[0] >= best * RELEVANCE_FLOOR]
    if len(keep) < MIN_BULLETS:
        keep = scored[:min(MIN_BULLETS, limit, len(scored))]

    # A resume must never read as if its owner has never been employed. When
    # projects outscore every job -- a career changer applying to engineering
    # -- keep the best bullet of the most recent job, displacing the weakest
    # pick if the resume is full. It is still one of the operator's own
    # bullets, so this adds no claim.
    #
    # Which bullet: the entry's `work_history_bullet` if the operator named
    # one, else the best-scoring. On an off-field job the score is noise -- a
    # single incidental tag like "ownership" once chose a sales-cycle bullet
    # for an AI role over the promotion bullet that reads well anywhere.
    recent = most_recent_job(profile)
    if (keep_work_history and recent is not None
            and not any(row[-1].origin == "experience" for row in keep)):
        parent = entry_key(recent, "company", "title")
        own = [row for row in scored
               if row[-1].origin == "experience" and row[-1].parent == parent]
        named = recent.get("work_history_bullet")
        if named:
            own = [row for row in own if row[-1].id == named]
            if not own:
                raise ValueError(
                    f"work_history_bullet {named!r} on experience {parent!r} "
                    "is not one of that entry's bullets")
        if own:
            keep = keep[:limit - 1] + [own[0]]

    # The mirror case: when the jobs outscore every project, the resume had no
    # PROJECTS section at all. The operator rejected the Shield AI and Kyber
    # forward-deployed drafts for exactly that ("No projects section"), so keep
    # the best-scoring project bullet the same way. The two rules never both
    # fire: a pick with no experience is all projects, and vice versa.
    if keep_project and not any(row[-1].origin == "project" for row in keep):
        projects = [row for row in scored if row[-1].origin == "project"]
        if projects:
            keep = keep[:limit - 1] + [projects[0]]
    return [row[-1] for row in keep]


def most_recent_job(profile: dict[str, Any]) -> dict[str, Any] | None:
    """The current job, else the one that ended last. None if there are none."""
    jobs = [e for e in profile.get("experience") or [] if e.get("bullets")]
    if not jobs:
        return None
    return max(jobs, key=lambda e: (bool(e.get("current")),
                                    str(e.get("end") or ""),
                                    str(e.get("start") or "")))


# --- generation -------------------------------------------------------------

# This prompt has been tuned twice, in opposite directions, and both were
# measured.
#
# The first version carried three prohibitions and one instruction to rewrite.
# The model returned 16 of 18 bullets byte-identical.
#
# The second said "Do not return a bullet unchanged" and "borrow the posting's
# own vocabulary". The model then appended whatever the posting wanted --
# "high-priority support scenarios", "strict turnaround requirements",
# "motion-control software" -- and turned "Goal: reduce" into "Reduced". A
# check on what was ADDED, run over every document it wrote, reverted 13 of 19
# rewrites and 2 of 3 summaries.
#
# The verifier now catches additions whatever the prompt says, so the prompt's
# job is only to make an honest rewrite the easy path: re-present what is
# there, and stop.
SYSTEM = (
    "You tailor resume bullets for one specific job by choosing emphasis and "
    "wording. You re-present the evidence you are given; you never extend it. "
    "An automated check compares every word you write against the source and "
    "reverts any bullet that adds an outcome, skill, scenario or quality the "
    "source does not state."
)

PROMPT = """Tailor each bullet so a hiring manager for THIS role sees the
relevant part first.

HOW
- Work only with the bullet's own words: move clauses so the part this role
  cares about comes first, and cut words that do not matter to this role.
- You may shorten. You may not add. No new outcomes, scenarios, skills or
  personal qualities, even when the role asks for them -- those gaps are
  reported to the candidate separately.
- Keep the original tense. "Building ..." is still in progress and stays that
  way. A bullet that begins "Goal:" describes an aim, not a result.

For the summary: choose which of its points to lead with for this role. Add no
skill, trait or experience it does not already state.

Return JSON: {{"summary": "...", "bullets": [{{"id": "<same id>", "text": "..."}}]}}

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
    # No summary in the profile: none is written, and none may be invented.
    summary_text = " ".join(str(summary["text"]).split()) if summary else ""
    chosen = select_bullets(profile, description, track, weights=weights,
                            title=job.get("title") or "")
    matched, missing = keyword_gap(description, profile)

    listing = "\n".join(f'- [{b.id}] {b.text}' for b in chosen)
    prompt = PROMPT.format(
        title=job.get("title") or "the role",
        description=posting.visible(description).text,
        summary=summary_text or "(none: return an empty string)",
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
        if isinstance(bid, str):
            # The listing shows ids as "[b_vid_design]" and models sometimes copy
            # the brackets. Stripping them is safe: an invented id is still
            # unknown afterwards, and verify_draft still refuses it.
            bid = bid.strip().strip("[]").strip()
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
        summary_id=summary["id"] if summary else "",
        summary=(" ".join(str(data.get("summary") or summary_text).split())
                 if isinstance(data, dict) and summary else summary_text),
        bullets=bullets,
        keywords_matched=matched,
        keywords_missing=missing,
        model=usage.model,
        prompt_hash=hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
    )
    return verify_draft(draft, profile)
