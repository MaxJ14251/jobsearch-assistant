"""The dashboard's source, for tests that check what it says.

`jsa/web.py` became the package `jsa/web/` (plan 24). A guard that read one
file would silently read only `__init__.py`, so every source check reads
the whole package: its Python and its templates.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def web_files() -> list[Path]:
    package = ROOT / "jsa" / "web"
    if package.is_dir():
        return sorted(p for p in package.rglob("*") if p.suffix in (".py", ".html"))
    return [ROOT / "jsa" / "web.py"]


def web_source() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in web_files())
