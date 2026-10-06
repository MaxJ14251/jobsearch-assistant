"""Structured logging.

Quiet by default: the CLI's own output is the interface, and a wall of INFO
lines buries it. `--verbose` turns on the detail that matters when something
goes wrong — which feed was slow, which model answered, how many tokens a run
actually cost.

Every model call's counts go to the model-call ledger (`jsa.ledger`, plan
26), which `jsa usage` reads; this module only shapes log output.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from contextlib import contextmanager

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
@contextmanager
def timed(label: str):
    """Debug-level timing for one step."""
    start = time.time()
    LOGGER.debug("%s: start", label)
    try:
        yield
    finally:
        LOGGER.debug("%s: %.2fs", label, time.time() - start)
