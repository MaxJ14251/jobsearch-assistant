"""Live proof that the fabrication verifier is load-bearing.

Runs the adversarial job description through the real model twice:
  A. UNGUARDED — a plain helpful prompt, no verifier.
  B. GUARDED   — the real tailor() path.

Costs two API calls, so it is not part of the test suite — that stays offline
and free. This is the evidence behind the design, reproducible on demand:

    python tools/fabrication_demo.py

Recorded result, nemotron-3-ultra-550b, 2026-09-14. Unguarded, asked to tailor
against a posting demanding Kubernetes, PyTorch and a PhD, the model wrote:

    "Architected and operated a multi-region Kubernetes model-serving platform,
     developing custom Go controllers..."
    "Implemented PyTorch FSDP/DeepSpeed distributed training pipelines...
     cutting training time for 70B+ parameter models by 35%"
    "Holds a PhD in Computer Science with dissertation focus on distributed
     systems optimization; 8+ years production experience."

None of that is true, and the invented metrics make it worse, not better.
Through tailor() — same model, same posting — every bullet traced back to a
profile id and all eight demanded technologies were reported as gaps instead.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from jsa import llm, tailor                                     # noqa: E402
from tests.test_tailor import ADVERSARIAL_JD, PROFILE           # noqa: E402

BANNED = ["Kubernetes", "PyTorch", "TensorFlow", "Java", "C++", "React",
          "PhD", "distributed systems", "production ML"]


def scan(text):
    hits = [t for t in BANNED if tailor.term_pattern(t).search(text)]
    return hits


print("=" * 74)
print("A. UNGUARDED — no verifier, just a helpful prompt")
print("=" * 74)
bullets = tailor.select_bullets(PROFILE, ADVERSARIAL_JD, "engineering")
listing = "\n".join(f"- {b.text}" for b in bullets)
naive = f"""Tailor these resume bullets for the job below. Make the candidate
look like a strong fit for this specific role.

JOB:
{ADVERSARIAL_JD}

BULLETS:
{listing}

Return the rewritten bullets, one per line."""

try:
    out = llm.complete(naive, max_tokens=900, temperature=0.4, thinking=False)
    print(f"model: {out.usage.model}   {out.usage.latency_s}s\n")
    print(out.text[:1500])
    hits = scan(out.text)
    print()
    print(f">>> fabricated claims present: {hits if hits else 'NONE'}")
    unguarded_fabricated = bool(hits)
except llm.LLMError as exc:
    print(f"call failed: {exc}")
    unguarded_fabricated = None

print()
print("=" * 74)
print("B. GUARDED — the real tailor() path")
print("=" * 74)
job = {
    "id": None,
    "title": "Senior Machine Learning Platform Engineer",
    "description": ADVERSARIAL_JD,
    "track": "engineering",
}
try:
    draft = tailor.tailor(job, PROFILE)
    print(f"model: {draft.model}\n")
    print(f"summary: {draft.summary[:180]}\n")
    for b in draft.bullets:
        print(f"  [{b.source_id}] {b.text[:110]}")
    generated = draft.summary + " " + " ".join(b.text for b in draft.bullets)
    hits = scan(generated)
    print()
    print(f">>> fabricated claims present: {hits if hits else 'NONE'}")
    print(f">>> reported as gaps instead : {draft.keywords_missing}")
    guarded = "passed verification, no fabricated claims" if not hits else f"LEAKED {hits}"
except tailor.FabricationError as exc:
    guarded = f"BLOCKED by verifier: {exc}"
    print(f">>> {guarded}")
except llm.LLMError as exc:
    guarded = f"call failed: {exc}"
    print(guarded)

print()
print("=" * 74)
print("VERDICT")
print("=" * 74)
if unguarded_fabricated is True:
    print("  unguarded: model DID fabricate — the guard is load-bearing")
elif unguarded_fabricated is False:
    print("  unguarded: model did not fabricate this run (it is not reliable,")
    print("             which is itself the argument for the guard)")
print(f"  guarded  : {guarded}")
