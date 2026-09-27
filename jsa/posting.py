"""How much of a job posting a model is shown, decided in one place.

Four modules used to slice the description themselves, at three different
lengths, none of which had ever been reconciled: tailor and prep at 4,000
characters, enrich at 6,000, outreach at 2,000. A resume was tailored from
less of the posting than the extraction pass had read, and nothing said so.

MEASURED 2026-09-27 over 1,266 postings in the author's tracker. Median
length 6,092, mean 6,187, longest 23,227 -- 92% are longer than 4,000 and
52% are longer than 6,000. Where the parts that decide a draft sit, as a
share of the postings that contain them at all:

    what the model is shown          4,000     6,000    12,000
    a requirements heading             90%       98%      100%
    a degree line                      79%       98%      100%
    a years-of-experience line         93%      100%      100%
    a clearance line                   84%       96%      100%
    a work-authorisation line          28%       84%      100%

One in five postings that state a degree requirement stated it where the
tailoring model could not see it. Four in five stated work authorisation
there, which is the thing ADR 0001 exists to get right.

WHY 12,000 AND NOT MORE. Raising the cap costs tokens only on the postings
that are actually long, and most are not:

    cap      still cut    mean window
    4,000    92.0%        3,860 chars (~965 tokens)
    8,000    14.7%        6,018
    10,000    2.2%        6,154
    12,000    0.3%        6,174 (~1,543 tokens)
    24,000    0.0%        6,187

The curve is flat past 10,000: going from 12,000 to no limit at all buys
four postings and 13 characters of average prompt. 12,000 leaves 99.7% of
postings complete for about 580 extra tokens per call.

WHAT WAS REJECTED, on the same numbers:

- *Dropping boilerplate to make room.* Only 6% of the first 4,000 characters
  is EEO text, benefits and "about us" -- that material lives at the END of a
  posting, which is the part already being cut. Stripping it lifted the
  work-authorisation figure from 28% to 41% and everything else by a point or
  two, for a rule that can silently delete a requirement worded like a
  benefit. Not worth it.
- *A head plus a tail, to catch the sponsorship line at the bottom.* Measured
  worse than simply reading more: 3,000 + 1,500 sees 63% of degree lines
  against today's 79%, because it cuts the middle, and the middle is where
  requirements live.

So the window is a plain prefix: a contiguous slice from the start, never
reordered, never rewritten, never a line removed from the middle. Whatever
reaches a model is text that appears in the posting, in the posting's order,
and `visible()` is the only thing that decides where it stops.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# What a drafting or extraction call reads. See the module docstring for the
# measurement behind the number.
MAX_DESCRIPTION_CHARS = 12000

# Outreach is a different job: the model is answering "why this role", not
# writing against requirements, and ADR 0010 rejects anything it borrows about
# the sender anyway. A smaller budget, through the same function.
OUTREACH_CHARS = 2000

_BREAK = re.compile(r"\n\s*\n")


@dataclass(frozen=True)
class Window:
    """What the model was shown, and what it was not."""

    text: str
    total: int
    budget: int
    # The first few words of what was cut, so the operator can find the
    # boundary in the posting instead of counting characters.
    resumes: str = ""

    @property
    def complete(self) -> bool:
        return len(self.text) >= self.total

    @property
    def dropped(self) -> int:
        return max(0, self.total - len(self.text))


def visible(description: str | None, budget: int = MAX_DESCRIPTION_CHARS) -> Window:
    """The part of a posting a model may read.

    Cuts at a blank line when there is one near the end of the budget, so the
    window stops between sections rather than mid-sentence. It never cuts
    EARLIER than it has to by more than a tenth of the budget: a tidy boundary
    is worth a few hundred characters, not a requirement.
    """
    text = description or ""
    if len(text) <= budget:
        return Window(text, len(text), budget)
    cut = budget
    breaks = [m.start() for m in _BREAK.finditer(text, 0, budget)]
    if breaks and breaks[-1] >= budget * 0.9:
        cut = breaks[-1]
    resumes = " ".join(text[cut:].split()[:8])[:60].strip()
    return Window(text[:cut], len(text), budget, resumes)


def note(window: Window) -> str:
    """What to tell the operator, in words they can check against the posting.

    Silent truncation is how a draft ends up ignoring a requirement stated in
    the part the model never saw. Quoting the first few words of what was cut
    means the operator can find the boundary in the posting itself rather than
    counting characters to it.
    """
    if window.complete:
        return ""
    said = (f"posting is {window.total:,} chars; the model read the first "
            f"{len(window.text):,} and not the last {window.dropped:,}")
    if window.resumes:
        said += f', which begin "{window.resumes}..."'
    return said
