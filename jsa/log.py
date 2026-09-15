"""Structured logging.

Quiet by default: the CLI's own output is the interface, and a wall of INFO
lines buries it. `--verbose` turns on the detail that matters when something
goes wrong — which feed was slow, which model answered, how many tokens a run
actually cost.

Every LLM call already records model, tokens, latency, attempts and whether it
fell back (see `jsa.llm.Usage`). This is where that becomes visible.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

LOGGER = logging.getLogger("jsa")
_CONFIGURED = False


def configure(verbose: bool = False) -> logging.Logger:
    """Set up logging once. Honours JSA_LOG_LEVEL for finer control."""
    global _CONFIGURED
    level = os.environ.get("JSA_LOG_LEVEL")
    if level:
        resolved = getattr(logging, level.upper(), logging.INFO)
    else:
        resolved = logging.DEBUG if verbose else logging.WARNING

    if not _CONFIGURED:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                              datefmt="%H:%M:%S")
        )
        LOGGER.addHandler(handler)
        _CONFIGURED = True
    LOGGER.setLevel(resolved)
    return LOGGER


@dataclass
class RunMetrics:
    """Totals for one command, printed as a single summary line."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    fallbacks: int = 0
    retries: int = 0
    failures: int = 0
    models: dict[str, int] = field(default_factory=dict)
    started: float = field(default_factory=time.time)

    def record(self, usage) -> None:
        self.calls += 1
        self.prompt_tokens += getattr(usage, "prompt_tokens", 0)
        self.completion_tokens += getattr(usage, "completion_tokens", 0)
        self.fallbacks += int(bool(getattr(usage, "fell_back", False)))
        self.retries += max(0, getattr(usage, "attempts", 1) - 1)
        model = getattr(usage, "model", "") or "unknown"
        self.models[model] = self.models.get(model, 0) + 1
        LOGGER.debug(
            "llm call model=%s in=%s out=%s %.2fs attempts=%s fell_back=%s",
            model, getattr(usage, "prompt_tokens", 0),
            getattr(usage, "completion_tokens", 0),
            getattr(usage, "latency_s", 0.0),
            getattr(usage, "attempts", 1),
            getattr(usage, "fell_back", False),
        )

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def summary(self) -> str:
        if not self.calls:
            return ""
        elapsed = time.time() - self.started
        models = ", ".join(f"{m.split('/')[-1]}×{n}"
                           for m, n in sorted(self.models.items(),
                                              key=lambda kv: -kv[1]))
        parts = [
            f"{self.calls} LLM call(s)",
            f"{self.total_tokens:,} tokens "
            f"({self.prompt_tokens:,} in / {self.completion_tokens:,} out)",
            f"{elapsed:.1f}s",
        ]
        if self.retries:
            parts.append(f"{self.retries} retry/retries")
        if self.fallbacks:
            parts.append(f"{self.fallbacks} fell back to another model")
        if self.failures:
            parts.append(f"{self.failures} failed")
        return "  ".join(parts) + (f"  [{models}]" if models else "")


@contextmanager
def timed(label: str):
    """Debug-level timing for one step."""
    start = time.time()
    LOGGER.debug("%s: start", label)
    try:
        yield
    finally:
        LOGGER.debug("%s: %.2fs", label, time.time() - start)
