# ADR 0010 — Outreach fails closed, and why the cover-letter guard does not fit

Status: accepted
Date: 2026-09-20
Extends: ADR 0007 (the added-words guard for prose with no source bullet).
Supersedes the claim in `jsa/outreach.py`'s own docstring that outreach used
"the same verifier as tailoring". It did not.

## Context

`jsa/outreach.py` had existed for weeks, carried 536 lines of green tests, and
had produced zero messages. Running it against three real postings and real
model calls found four things wrong at once:

1. **`verify_message` did not verify.** It was a banned-term list plus three
   literal degree phrases. Measured: a draft inventing an employer, a team of
   twelve, six years' tenure, a $4M revenue figure and a professional
   certification passed it without objection — while the module docstring
   promised "the same verifier as tailoring", and gave the right reason for
   needing one: a referral ask is read by a person who may check.
2. **Nothing signed the message.** The prompt ends "Sign off without a name —
   the sender's name is added afterwards." Nothing added it. Every draft ended
   "Thanks," and stopped.
3. **The model never saw the posting.** It was given a title, a company and a
   tech stack, then asked to "be concrete about why this role specifically".
4. **The track was hardcoded.** `select_bullets` was always told
   "engineering", so a posting the tracker had already classified as sales
   fell back to the wrong kind. Latent rather than active, because
   `role_kind(title, track)` lets the title decide first.

1, 2 and 4 are fixed. 3 is fixed — the posting is now shown, capped, and
explicitly framed as describing the role and never the sender. This ADR is
about what happened next.

## What was measured

The word-level check from ADR 0007 was brought over: the message's vocabulary
must come from the profile, the posting's title/company/location, the
recipient's record, the tech stack, or an allowlist of connectives. Then nine
real drafts — three postings across three channels:

| | linkedin_connect | linkedin_dm | email |
|---|---|---|---|
| AI Engineer, Enablement | refused | refused | refused |
| Premium Support Engineer | refused | refused | refused |
| Commercial Account Executive | **drafted** | refused | refused |

One of nine. Three rounds of fixes moved it from 0/9 to 2/9 to 1/9; the
variation is noise, and the number is about one in nine.

The words it refused over: `reliable`, `platform`, `developer`, `debugging`,
`output`, `content`, `value`, `driven`, `shipping`, `implementations`.

## The finding

**Both of these are true: the drafts are ones a person would send, and the
check cannot certify them.**

`unsupported_words` is a bag-of-words test with no notion of *who a word is
about*. That is fine for a cover letter, which is almost entirely about the
candidate, so any word the profile does not support is a claim the candidate
cannot make. An outreach message is not shaped like that. It is substantially
about the recipient, their company and the role, so a large share of its
vocabulary legitimately belongs to someone else — and the check has no way to
separate "their platform is reliable" from "I am reliable".

The first draft ever produced illustrates it exactly. A human reads it as a
good message; the check rejects it for one word, `detection`, because the
profile says "AI-identified highlights" and the model wrote "highlight
detection". That is a synonym, not an invention — and a rule that cannot see
the difference is the rule we have.

A secondary cause was found and fixed: the retry named only the latest round's
offending words, so each rewrite swapped one unsupported word for a different
one and never converged. Accumulating the list across attempts is what moved
anything at all.

## Decision

**Outreach fails closed, and the README says so plainly.**

- The strict check stays. The alternative is shipping the weak one, and the
  weak one passed an invented employer and a $4M figure.
- The status table no longer says outreach is Done. It says it refuses more
  often than it drafts, with the measured number.
- The refusal message explains itself as what it is — "your profile does not
  have the material for this message" — and names the words, so the operator
  can either add a bullet that genuinely covers the ground or write that
  message themselves.
- No tuning of the allowlist to reach a pass rate. Words were added when they
  were connectives or the vocabulary of the request itself (`referral` was
  being rejected on a referral ask); no word naming a skill, tool, outcome or
  quality was added, which is ADR 0007's rule.

## Known causes of false refusal

- **Acronym plurals.** `tailor._stem` only strips a suffix when the word is
  longer than five characters, so "LLMs" never reduces to "LLM". Fixed for
  outreach's own check rather than in a stemmer three modules share.
- **Synonyms of the operator's own words.** "detection" for "identified".
  Unfixed, and the main cause of the one-in-nine rate.

## What would actually fix it

Not more allowlist. The check needs to know the subject of a claim — to run
only over the sentences that say "I". That is a real piece of work and it
should be measured against these nine before and after, not argued about.

## Consequences

- An operator using outreach today should expect to write the message
  themselves and read the refusal as a list of claims they would have had to
  stand behind. That is a smaller promise than "draft-only outreach, Done",
  and it is the true one.
- Nothing about sending changed. It still cannot: the import-graph test is
  unchanged and still passes.
- Contacts are third parties who did not ask to be in anybody's database. The
  trial used invented contacts in a sandbox tracker outside the repository,
  and the real tracker was never written to.
