"""US assumptions stay in the US pack (country pack split, phase 4).

Everything the app assumes about a country lives in jsa/countries. These
fail when a string outside it names the US again: a dollar sign, USD, "the
US", a ZIP, "City, ST", "Remote (US)", a US state's name. Docstrings and
comments may talk about the US; what the app says and matches may not.
"""

import io
import re
import tokenize
import unittest
from pathlib import Path

from jsa.countries import us

ROOT = Path(__file__).resolve().parents[1]
JSA = ROOT / "jsa"

_STATES = "|".join(re.escape(n) for n in us.SUBDIVISIONS.values())
PATTERNS = {
    "a dollar sign": re.compile(r"\$\s?\{?[\d,]|\\\$|^['\"]\$['\"]$"),
    "USD": re.compile(r"\bUSD\b"),
    "the US": re.compile(r"\b(the US|United States|U\.S\.)\b"),
    "a ZIP": re.compile(r"\bZIP\b|\\d\{5\}"),
    "City, ST": re.compile(r"City, ST\b"),
    "Remote (US)": re.compile(r"Remote \(US\)"),
    "the lower 48": re.compile(r"lower 48"),
    "a US state": re.compile(rf"\b({_STATES})\b"),
}

# Strings that name the US on purpose, each with its reason.
ALLOWED = {
    # Model prices in .env are the provider's, in dollars, whatever the country.
    ("jsa/ledger.py", "a dollar sign"),
    # USAJOBS is a US-only source (the US pack lists it in SOURCE_KINDS); its
    # pay is in dollars by definition.
    ("jsa/sources.py", "USD"),
}

TEMPLATE_PHRASES = ("Remote (US)", "City, ST", "ZIP", "in the US", "of the US",
                    "lower 48", "5-digit")


def _strings(path: Path):
    src = path.read_text(encoding="utf-8")
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1)):
            if tok.string.lstrip("rbfuRBFU").startswith(('"""', "'''")):
                continue                                  # a docstring
            yield tok.start[0], tok.string


class TestNoUSOutsideThePack(unittest.TestCase):
    def test_python_strings(self):
        found = []
        for path in sorted(JSA.rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith("jsa/countries/"):
                continue
            for line, text in _strings(path):
                for name, rx in PATTERNS.items():
                    if rx.search(text) and (rel, name) not in ALLOWED:
                        found.append(f"{rel}:{line} names {name}: {text[:70]}")
        self.assertEqual(found, [], "\n".join(found))

    def test_templates(self):
        found = []
        for path in sorted((JSA / "web" / "templates").glob("*.html")):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for phrase in TEMPLATE_PHRASES:
                    if phrase in line:
                        found.append(f"{path.name}:{n} says {phrase!r}")
        self.assertEqual(found, [], "\n".join(found))

    def test_the_guard_bites(self):
        """A planted US string is caught: the patterns are not dead."""
        for text, name in (('"$90,000"', "a dollar sign"), ('"outside the US"', "the US"),
                           ('"Remote (US)"', "Remote (US)"), ('"in Texas"', "a US state")):
            self.assertTrue(PATTERNS[name].search(text), text)

    def test_every_allowance_is_still_needed(self):
        for rel, name in ALLOWED:
            hits = [t for _, t in _strings(ROOT / rel) if PATTERNS[name].search(t)]
            self.assertTrue(hits, f"{rel} no longer names {name}: drop the allowance")


if __name__ == "__main__":
    unittest.main()
