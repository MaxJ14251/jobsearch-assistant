# ADR 0032 — What postings ask for, not who gets hired

Status: accepted
Date: 2026-10-08
Relates to: ADR 0016 (how much of a posting a model reads), the thin-posting
rule (ADR 0005 section 11), plan 13's small-group rule. Implements plan 32;
plans 33 and 34 build on it.

## Context

The owner asked what share of each company's hires have no degree
(certifications only), a bachelor's, or a master's. **That data isn't
public**: hiring outcomes by education are private, federal EEO-1 reports
carry no education, and the nearest commercial source (LinkedIn Talent
Insights) is paid, and LinkedIn is not read (ADR 0013, 0020).

What can be read honestly is what each company's **postings ask for**. That
is not who gets hired: the Burning Glass Institute and Harvard Business
School found that removing a degree requirement often changes actual hiring
little. Every surface that shows these numbers says so.

Enrichment already stored `jobs.degree_required` (0/1/NULL) from a model
call, on about half the postings, with "or equivalent experience" defined as
false. It says nothing about master's degrees or certifications.

## Decision

1. **`jsa/degree.py`, no model.** Ordered phrase rules read each posting into
   one of six levels, plus the certifications it names and the sentence it
   was read from (at most 160 characters):

   | level | meaning |
   |---|---|
   | `none` | no degree asked of the candidate |
   | `associate` | an associate's, and nothing higher |
   | `bachelors_or_equiv` | a degree, but equivalent experience accepted, or only preferred |
   | `bachelors` | a bachelor's, required |
   | `masters_preferred` | a bachelor's required, a graduate degree preferred or offered |
   | `masters` | a graduate degree required (master's, MBA, PhD, MD, JD) |

   The plan named five levels; `associate` was added because folding 13
   associate's-degree postings into "no degree mentioned" would be false.
   "Or equivalent experience" anywhere in the posting opens the requirement,
   including SpaceX's separate clause "OR 2+ years of professional experience
   … in lieu of a degree". A sentence about the employer's people ("our
   engineers hold PhDs") is ignored, and so is "high school diploma or
   equivalent". Certifications come from `jsa/resources/degree.yaml`,
   editable like `coach.yaml`.
2. **Stored on `jobs`:** `degree_level`, `certs_named`, `degree_evidence`.
   Read when a posting is stored and again only when its text changes, by
   `jsa fill`, by `jsa rescore`, and once for every stored posting when a
   tracker is upgraded. `degree_required` stays, so the existing Matches
   filter works as before.
3. **A company profile** (`jsa/companies.py`): shares by level and the
   certifications named, over the company's open postings **in the
   tracker**, which are the ones that matched the person's search, not the
   employer's whole board; the wording says so. Under 10 postings it gives
   counts, "too few to compare". A posting too short to read (under the
   thin-posting threshold) is counted apart, not as "no degree mentioned":
   Snap's 16 postings carry no text at all.
4. **Where it shows:** the job page ("This posting asks for", its sentence,
   "This company"); a Companies page, sortable by the share open to
   equivalent experience or naming a certification; a Matches option,
   Degree: "Open to equivalent experience" (`degree=open`: no degree named,
   an associate's, or experience accepted); `jsa companies [--sort
   open|certs]`. The caveat is on each.

## Measured, 2026-10-08

On the owner's tracker (1,807 stored postings), against the model's
`degree_required` on the 474 enriched postings that have one:

- Raw agreement on "a bachelor's or more is required": 202 of 474 (42.6%).
- In 258 of the disagreements the posting offers experience instead of the
  degree and the model answered "required", against its own prompt, which
  defines that case as false. 64 of them are SpaceX's "in lieu of a degree"
  clause. In 13 more the model answered "required" for a posting with no
  degree word at all.
- Where the model followed its own definition: **202 of 203 (99.5%)**. The
  plan's bar was 90%.
- A spot check of 30 random open postings, read by the implementer against
  the text: 28 of 30 right at first; the two misses (a medical degree, a law
  degree) led to the professional-degree rule, and all 30 are right now.
- SpaceX by hand: 10 random postings, all 10 read correctly.
- **Still owed:** the plan's 50 postings hand-labelled by the owner.

Open postings by level after this: 709 no degree mentioned, 477 open to
equivalent experience, 254 bachelor's required, 161 bachelor's required with
a graduate degree preferred, 38 graduate degree required, 6 associate's.

## Consequences

- The model's `degree_required` is wrong on the measured 258 postings, so
  Matches' Degree: "Not required" hides postings that accept experience.
  "Open to equivalent experience" is the reliable filter now. Fixing the
  enrichment prompt or retiring the field is left to a later plan.
- The profile is only as wide as the tracker. Plan 34 adds a snapshot of a
  company's whole public board.
- No claim about who any company hires is made anywhere.

## Rejected

- **Hiring data by education.** It doesn't exist publicly.
- **A model for the classification.** The measurement above shows the model
  answering against its own instructions on this exact question; the rules
  are checkable sentence by sentence.
