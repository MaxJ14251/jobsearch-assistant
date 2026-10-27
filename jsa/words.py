"""Counts that read as English: "1 match", "2 matches", never "match(es)".
Used by the CLI, doctor, the daily summary and the dashboard alike."""


def plural(n: int, one: str, many: str | None = None) -> str:
    """"1 match", "2 matches". `many` defaults to `one` + "s"."""
    return f"{n} {one if n == 1 else (many or one + 's')}"
