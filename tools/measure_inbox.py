"""How well the inbox reads replies: the stored ones, re-read now (G6).

Run by hand, never in CI. For every reply in the tracker it finds the same
message in the mailbox by its Message-ID, reads it the way `jsa inbox` does
(the folder opened read-only, every fetch a BODY.PEEK, so nothing is marked
read), and classifies it with today's rules, in memory. Email text is never
written anywhere and never printed: only kinds, rule names and counts.

    python tools/measure_inbox.py                       # stored vs today's rules
    python tools/measure_inbox.py --labels FILE.json    # and against your labels

The labels file is the download from the local labelling page
(logs/label-replies.html): {"answers": [{"id": reply id, "answer": kind}]}.
"""

from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jsa import config, inbox  # noqa: E402

KINDS = ("rejection", "interview", "offer", "received", "other")


def reread(con: sqlite3.Connection) -> list[dict]:
    """Each stored reply with its stored reading and today's, in memory."""
    cfg = inbox.settings()
    if cfg is None:
        raise SystemExit(inbox.NOT_CONFIGURED)
    rows = con.execute("SELECT id, message_id, kind, rule FROM inbox_replies "
                       "ORDER BY id").fetchall()
    out = []
    imap = inbox.connect(cfg)
    try:
        for row in rows:
            now_kind = now_rule = None
            typ, data = imap.uid("SEARCH", None, "HEADER", "Message-ID",
                                 inbox._quote(row["message_id"]))
            uids = (data[0] or b"").split() if typ == "OK" and data else []
            if uids:
                head_raw = inbox._fetch_part(imap, uids[-1], inbox.HEADER_SPEC)
                body_raw = inbox._fetch_part(imap, uids[-1], inbox.TEXT_SPEC)
                head = inbox.headers(head_raw)
                now_kind, now_rule = inbox.classify(head["subject"],
                                                    inbox.text_of(head_raw, body_raw))
            out.append({"id": row["id"], "stored": row["kind"], "stored_rule": row["rule"],
                        "now": now_kind, "now_rule": now_rule})
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001 - closing a dead connection
            pass
    return out


def confusion(pairs: list[tuple[str, str]]) -> str:
    """A table of label (rows) against reading (columns), counts only."""
    counts = collections.Counter(pairs)
    head = "label \\ read".ljust(14) + "".join(k[:9].rjust(10) for k in KINDS)
    lines = [head]
    for label in KINDS:
        lines.append(label.ljust(14) + "".join(str(counts[(label, k)]).rjust(10)
                                               for k in KINDS))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--labels", help="the labelling page's downloaded answers")
    args = parser.parse_args(argv)
    config.load_dotenv()
    con = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = reread(con)
    finally:
        con.close()
    found = [r for r in rows if r["now"] is not None]
    print(f"{len(rows)} stored replies, {len(found)} found again in the mailbox")
    changed = [r for r in found if (r["now"], r["now_rule"]) != (r["stored"], r["stored_rule"])]
    print(f"read differently by today's rules: {len(changed)}")
    for r in changed:
        print(f"  reply {r['id']}: {r['stored']} ({r['stored_rule'] or '-'}) -> "
              f"{r['now']} ({r['now_rule'] or '-'})")
    if args.labels:
        labels = {int(a["id"]): a["answer"] for a in
                  json.loads(Path(args.labels).read_text(encoding="utf-8"))["answers"]
                  if a.get("answer") and a["answer"] != "unsure"}
        for name, key in (("stored", "stored"), ("today's rules", "now")):
            pairs = [(labels[r["id"]], r[key]) for r in rows
                     if r["id"] in labels and r[key] is not None]
            right = sum(1 for a, b in pairs if a == b)
            print(f"\nagainst your labels, {name}: {right} of {len(pairs)} right")
            print(confusion(pairs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
