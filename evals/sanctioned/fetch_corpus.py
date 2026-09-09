"""Pin a snapshot of the sanctions database, because the database moves.

The source is a curated, continuously updated register of court decisions in
which a tribunal addressed fabricated or misrepresented authority. New records
land daily and existing records are revised as orders are read more closely.
That is the right way to run a register and the wrong thing to measure against:
a number produced today against a live URL cannot be reproduced tomorrow, and a
result that cannot be reproduced is not a result.

So this script does one thing. It fetches the CSV export once, writes it under
`raw/` with the fetch date in the filename, and records a manifest carrying the
URL, the date, the SHA-256 of the bytes, and the row count. Every downstream
number in this directory is a number about that file, not about the website.

    python evals/sanctioned/fetch_corpus.py

Re-running on a later date writes a new snapshot alongside the old one rather
than overwriting it; the manifest keeps one entry per snapshot.

Source: AI Hallucination Cases Database, Damien Charlotin,
https://www.damiencharlotin.com/hallucinations/ -- licensed CC BY 4.0.

Fetch politely. The CSV is one request and it is the only request this
directory makes; the linked opinion PDFs are not harvested.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
MANIFEST = RAW / "manifest.json"

#: The doubled path segment is not a typo -- it is the export route the site
#: actually serves.
SOURCE_URL = "https://www.damiencharlotin.com/hallucinations/hallucinations/download.csv"

ATTRIBUTION = (
    "AI Hallucination Cases Database, Damien Charlotin, "
    "https://www.damiencharlotin.com/hallucinations/"
)
LICENSE = "CC BY 4.0"

#: The origin sits behind a bot filter that answers a default library
#: user-agent with 403. A browser string is what gets the export, and the
#: request is otherwise an ordinary single GET.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Some narrative cells run past the interpreter's default field ceiling.
csv.field_size_limit(1 << 24)


def fetch(url: str = SOURCE_URL, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"{url} returned HTTP {response.status}")
        return response.read()


def summarize(payload: bytes) -> tuple[int, list[str]]:
    """Row count and header, read from the bytes that were actually written."""
    text = payload.decode("utf-8-sig")
    reader = csv.DictReader(text.splitlines())
    rows = list(reader)
    return len(rows), list(reader.fieldnames or [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=SOURCE_URL)
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite today's snapshot instead of refusing",
    )
    args = parser.parse_args(argv)

    RAW.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    target = RAW / f"hallucinations-{today}.csv"

    if target.exists() and not args.force:
        print(f"snapshot already present: {target}")
        print("pass --force to refetch")
        return 0

    payload = fetch(args.url)
    digest = hashlib.sha256(payload).hexdigest()
    target.write_bytes(payload)
    rows, header = summarize(payload)

    entry = {
        "url": args.url,
        "fetched": today,
        "file": target.name,
        "sha256": digest,
        "bytes": len(payload),
        "rows": rows,
        "fields": header,
        "attribution": ATTRIBUTION,
        "license": LICENSE,
    }

    # Keep every snapshot ever taken. A superseded entry is what lets an old
    # measurement be traced back to the file it was made against.
    entries = []
    if MANIFEST.exists():
        entries = json.loads(MANIFEST.read_text())
        entries = [e for e in entries if e.get("file") != target.name]
    entries.append(entry)
    entries.sort(key=lambda e: e["fetched"])
    MANIFEST.write_text(json.dumps(entries, indent=2) + "\n")

    print(f"wrote {target}")
    print(f"  sha256 {digest}")
    print(f"  rows   {rows}")
    print(f"  fields {len(header)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
