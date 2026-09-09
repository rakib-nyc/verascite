"""Turn a register of decisions into a list of citations, or say why it cannot.

The snapshot records one row per decision. Its `Hallucination Items` column is
where the citation-level detail lives: segments joined by `||`, each of the form
`Type: Category | narrative`. The narrative is curator prose, not a citation
field, and most of it names no citation at all -- "the brief contained citations
to nonexistent cases" is a true statement about a filing and useless as ground
truth. This script extracts only the segments that name an authority in full,
and reports how much of the register that leaves behind.

    python evals/sanctioned/extract_citations.py

Writes `fabricated_citations.jsonl` and `negative_controls.jsonl`, and prints
the yield. CORPUS.md records the numbers; this file records the reasoning.

Three decisions shape the output:

**Only `Fabricated: Case Law`.** The register also tags misrepresentation,
false quotation, outdated authority, and fabricated exhibits. Those are real
findings about real filings, but the authority in them usually exists -- so a
tool that answers "does this case exist" is not being asked the same question,
and mixing them would make a recall figure meaningless.

**Only adjudicated records.** `Alleged` marks rows where fabrication was
asserted by a party and not resolved by the tribunal. An allegation is not
ground truth.

**Only full citations.** A narrative naming `Harris v. City of Houston (5th
Cir. 2022)` gives a case name and a year and no reporter. Nothing downstream can
look that up, so counting it would inflate the corpus with rows no checker can
be scored on. The requirement is a case name followed by volume, reporter, and
page.

Source: AI Hallucination Cases Database, Damien Charlotin,
https://www.damiencharlotin.com/hallucinations/ -- licensed CC BY 4.0.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"

csv.field_size_limit(1 << 24)

ITEM_SEPARATOR = "||"
TARGET_TYPE = "Fabricated: Case Law"

# --------------------------------------------------------------------------
# The citation core
# --------------------------------------------------------------------------
# A reporter is a run of short capitalised abbreviations, optionally carrying a
# series ordinal that may be glued to the abbreviation (`F.3d`, `Cal.App.5th`)
# or standing alone (`F. Supp. 2d`). Enumerating reporters by name was rejected:
# the register is international, and a fixed list silently drops every neutral
# citation -- `2020 BCCRT 949`, `2016 ZALAC 28`, `2012 ND 176` -- which is a
# large share of the non-US rows. A shape rule keeps them.
UNIT = r"(?:[A-Z][A-Za-z0-9.'’]*|\d(?:d|th|st|nd|rd))"
REPORTER = rf"{UNIT}(?:\s+{UNIT}){{0,4}}"

# Volume, reporter, page. The page allows seven digits so that docket-style
# identifiers survive; a four-digit volume covers the year that stands in for a
# volume in neutral and unreported citations.
CORE = re.compile(rf"\b(\d{{1,4}})\s+({REPORTER})\s+(\d{{1,7}})\b")

# The shape rule is deliberately loose, so a few things that are not reporters
# fit it. A docket number (`No. 05 Civ. 8501`), a Louisiana panel designation
# (`La. App. 4 Cir. 10/30/13`) and a date all read as volume-reporter-page. They
# are rejected here rather than by tightening the shape, which would cost the
# neutral citations the shape exists to keep.
MONTHS = {
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "jan.", "feb.", "mar.",
    "apr.", "jun.", "jul.", "aug.", "sept.", "sep.", "oct.", "nov.", "dec.",
}
NOT_A_REPORTER = {
    "case", "cases", "civ.", "civ", "cir.", "cir", "rule", "r.", "docket",
    "no.", "nos.", "app.", "¶",
}

#: A citation core preceded by a docket marker is a docket number.
DOCKET_MARKER = re.compile(r"\bNos?\.?\s*$")


def plausible_reporter(reporter: str) -> bool:
    lowered = reporter.lower()
    if lowered in NOT_A_REPORTER or lowered in MONTHS:
        return False
    # A single letter is a docket division, not a reporter.
    return len(reporter.strip(".")) > 1

# --------------------------------------------------------------------------
# The case name
# --------------------------------------------------------------------------
# Reading the name forward from the left is what produces prefix leakage: the
# earliest capital letter in `Presented Board of Regents v. Wilson, 365 So. 2d
# 213` is `Presented`, and a non-greedy forward match happily takes it. So the
# name is read backwards from the citation instead, keeping tokens for as long
# as they look like part of a party name and stopping at the first word of
# curator prose. `Plaintiff cited a non-existent case titled` stops at `titled`
# without needing to know that `titled` is a curator word.
QUOTES = "\"'“”‘’«»"

#: A quote character only ends curator prose when it *opens* something. The
#: same glyphs serve as apostrophes -- `Emp't`, `Nat’l`, `F. App'x` -- and
#: cutting on those decapitated party names into `& Hous. v. ...` and
#: `Bank v. Havens`. An opening quote is one that starts a word.
QUOTE_OPENERS = " \t([-—–"

#: Lowercase words that legitimately appear inside a party name.
LOWER_OK = {
    "v", "v.", "vs", "vs.", "of", "the", "and", "for", "in", "re", "ex",
    "rel", "rel.", "et", "al", "al.", "de", "del", "la", "le", "van", "von",
    "der", "den", "du", "da", "dos", "y", "on", "behalf", "a", "an", "&",
    "d/b/a", "f/k/a", "n/k/a", "dba",
}

#: Names that open with these are complete without a party connector.
LEAD_FORMS = (
    "in re", "in the matter of", "matter of", "ex parte", "estate of",
    "est. of", "application of", "petition of",
)

#: Capitalised words that begin a sentence of curator prose and never begin a
#: case name. The backward scan cannot stop on them by case alone, because a
#: capital letter is exactly what it is looking for.
PROSE_HEAD = {
    "listed", "presented", "cited", "citing", "quoted", "quoting", "included",
    "including", "identified", "attributed", "referenced", "invented",
    "submitted", "filed", "found", "alleged", "purported", "purportedly",
    "fabricated", "nonexistent", "non-existent", "fictitious", "fictional",
    "also", "and", "but", "however", "further", "additionally", "finally",
    "again", "namely", "e.g.", "i.e.", "viz.", "such", "another", "here",
    "these", "those", "this", "that", "they", "court", "counsel", "brief",
    "motion", "opposition", "petition", "respondent", "petitioner",
    "appellant", "appellee", "plaintiff", "plaintiffs", "defendant",
    "defendants", "applicant", "claimant", "example", "instance", "second",
    "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth",
    "tenth", "purporting", "styled", "titled", "labeled", "labelled", "named",
    "called", "supposedly", "apparently",
}

CONNECTOR_TOKENS = {"v", "v.", "vs", "vs.", "versus"}

# A citation is often preceded by material that belongs to the citation rather
# than to the name -- a docket number, or the parenthetical court-and-date block
# that Louisiana and a few other jurisdictions place mid-citation. Removing them
# before the backward scan is what keeps `State Farm Fire & Cas. Co. v. Kelly`
# whole across `, No. 20-cv-3389,`.
TRAILING_PAREN = re.compile(r"\s*\([^()]{0,70}\)\s*,?\s*$")
TRAILING_DOCKET = re.compile(r",?\s*Nos?\.\s*[^,]{1,45}\s*,?\s*$")

# An em dash or en dash glues a prose word to the first party name
# (`precedent—United States v. Jones`). Splitting on them costs nothing:
# neither appears inside a party name where a hyphen would not do.
DASHES = re.compile(r"[–—―]")

# Text between two citation cores that means "this is still the same citation":
# a pincite, a star page, or the run-up to a parallel reporter.
CONTINUATION = re.compile(r"^[\s,]*(?:at\s*\*?\s*)?(?:\d+(?:\s*[-–]\s*\d+)?[\s,]*)*$")

# --------------------------------------------------------------------------
# Real cases named in the order
# --------------------------------------------------------------------------
# Some narratives name the authority that actually sits at the citation the
# filing gave. Those are real cases, quoted by the tribunal, and they must not
# be counted as fabrications. The cue has to sit immediately before the name --
# anywhere else in the sentence and it is not governing this citation.
REAL_CUE = re.compile(
    r"(?:"
    r"corresponds? to|corresponding to|"
    r"(?:the\s+)?(?:apparent\s+)?correct(?:ed)?\s+(?:case|citation|decision|opinion)"
    r"(?:\s+at\s+that\s+citation)?(?:\s+in\s+the\s+same\s+reporter)?\s+is|"
    r"(?:the\s+)?correct\s+(?:case|citation|decision|opinion)\s+as|"
    r"actual\s+(?:case|citation|decision|opinion)(?:\s+at\s+that\s+citation)?\s+is|"
    r"authority\s+actually\s+is|"
    r"actually\s+refers\s+to|refers\s+instead\s+to|"
    r"the\s+real\s+[A-Z][\w.'’-]*\s+is|"
    r"(?:the\s+)?closest\s+real\s+case(?:\s+as)?"
    r")"
    r"\s*(?:an?\s+)?(?:different|unrelated|real)?\s*(?:opinion|case|decision)?\s*\(?\s*$",
    re.IGNORECASE,
)


def strip_citation_run_up(window: str) -> str:
    for _ in range(3):
        shortened = TRAILING_DOCKET.sub("", TRAILING_PAREN.sub("", window))
        if shortened == window:
            break
        window = shortened
    return window


def cut_at_opening_quote(window: str) -> str:
    """Drop everything up to the last quote that opens a quoted citation."""
    cut = -1
    for index, char in enumerate(window):
        if char in QUOTES and (index == 0 or window[index - 1] in QUOTE_OPENERS):
            cut = index
    return window[cut + 1:] if cut >= 0 else window


def read_name_backwards(window: str) -> str:
    """Recover the case name from the text preceding a citation core."""
    # A quotation mark is a hard boundary: whatever the curator quoted starts
    # after it, and the citation being read is inside the quotation.
    window = cut_at_opening_quote(window)
    window = DASHES.sub(" ", window)
    window = strip_citation_run_up(window.rstrip().rstrip(",").rstrip())

    kept: list[str] = []
    for token in reversed(window.split()):
        bare = token.strip(",;:()[]'’\"")
        if bare[:1].isupper() or bare.lower() in LOWER_OK:
            kept.append(token)
            if len(kept) > 16:
                break
        else:
            break
    kept.reverse()

    # Shed leading curator prose and the connectives that trail it.
    while kept:
        joined = " ".join(kept).lower()
        if any(joined.startswith(form) for form in LEAD_FORMS):
            break
        head = kept[0].strip(",;:()[]'’\"").lower()
        if head in PROSE_HEAD or kept[0][:1].islower():
            kept.pop(0)
            continue
        break

    return " ".join(kept).strip().strip(",").lstrip("(&").strip()


def is_full_name(name: str) -> bool:
    """A name is usable only if it identifies parties, not just a word."""
    if not name or len(name) < 5:
        return False
    if any(name.lower().startswith(form) for form in LEAD_FORMS):
        return True
    # The connector has to be its own token. Matching it inside a word made
    # `Galty B.V.` look like a party name because `B.V.` contains a `v.`, and
    # a connector in final position means the second party is missing.
    tokens = [token.strip(",;:()[]'’\"").lower() for token in name.split()]
    if not tokens or tokens[-1] in CONNECTOR_TOKENS:
        return False
    return any(token in CONNECTOR_TOKENS for token in tokens[:-1])


def citations_in(segment: str):
    """Yield (name, volume, reporter, page, names_a_real_case) per citation."""
    previous_end = None
    for match in CORE.finditer(segment):
        # A parallel reporter or a pincite is part of the citation already
        # emitted, not a second authority.
        if previous_end is not None and CONTINUATION.match(
            segment[previous_end:match.start()]
        ):
            previous_end = match.end()
            continue
        previous_end = match.end()

        window = segment[: match.start()]
        if not plausible_reporter(match.group(2)):
            continue
        if DOCKET_MARKER.search(window):
            continue
        name = read_name_backwards(window)
        if not is_full_name(name):
            continue

        # Where the name begins in the original text, so the cue can be tested
        # against what immediately precedes it.
        head = window.rfind(name)
        preamble = window[:head] if head != -1 else window
        yield name, match.group(1), match.group(2), match.group(3), bool(
            REAL_CUE.search(preamble)
        )


def load(snapshot: Path) -> list[dict]:
    with snapshot.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def latest_snapshot() -> Path:
    candidates = sorted(RAW.glob("hallucinations-*.csv"))
    if not candidates:
        raise SystemExit("no snapshot in raw/ -- run fetch_corpus.py first")
    return candidates[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=HERE)
    args = parser.parse_args(argv)

    snapshot = args.snapshot or latest_snapshot()
    rows = load(snapshot)

    counts = {
        "rows": len(rows),
        "rows_alleged": 0,
        "rows_with_fabricated_case_law": 0,
        "rows_dropped_alleged": 0,
        "segments": 0,
        "segments_with_citation": 0,
        "records_yielding": 0,
        "raw_citations": 0,
        "duplicates": 0,
    }

    fabricated: dict[str, dict] = {}
    controls: dict[str, dict] = {}
    yielding_records = set()

    for row in rows:
        segments = [
            part.strip()
            for part in row["Hallucination Items"].split(ITEM_SEPARATOR)
            if part.strip().startswith(TARGET_TYPE)
        ]
        if not segments:
            continue
        counts["rows_with_fabricated_case_law"] += 1

        # An allegation the tribunal did not resolve is not ground truth. This
        # is counted before anything is extracted so the drop is reported even
        # when the dropped rows would have yielded nothing anyway.
        if row.get("Alleged", "").strip().lower() == "yes":
            counts["rows_alleged"] += 1
            counts["rows_dropped_alleged"] += 1
            continue

        for segment in segments:
            counts["segments"] += 1
            narrative = segment.split("|", 1)[1].strip() if "|" in segment else segment
            found = list(citations_in(segment))
            if found:
                counts["segments_with_citation"] += 1
            for name, volume, reporter, page, names_real in found:
                counts["raw_citations"] += 1
                citation = f"{name}, {volume} {reporter} {page}"
                record = {
                    "citation": citation,
                    "case_name": name,
                    "volume": volume,
                    "reporter": reporter,
                    "page": page,
                    "source_record": row["Case Name"],
                    "court": row["Court"],
                    "date": row["Date"],
                    "jurisdiction": row["State(s)"],
                    "raw_item": narrative,
                }
                bucket = controls if names_real else fabricated
                key = re.sub(r"\s+", " ", citation).lower()
                if key in fabricated or key in controls:
                    counts["duplicates"] += 1
                    continue
                bucket[key] = record
                if not names_real:
                    yielding_records.add(row["Case Name"])

    counts["records_yielding"] = len(yielding_records)
    counts["unique_citations"] = len(fabricated)
    counts["negative_controls"] = len(controls)

    args.out.mkdir(parents=True, exist_ok=True)
    write_jsonl(args.out / "fabricated_citations.jsonl", fabricated.values())
    write_jsonl(args.out / "negative_controls.jsonl", controls.values())
    (args.out / "extraction_counts.json").write_text(
        json.dumps({"snapshot": snapshot.name, **counts}, indent=2) + "\n"
    )

    width = max(len(k) for k in counts)
    print(f"snapshot {snapshot.name}")
    for key, value in counts.items():
        print(f"  {key:<{width}}  {value}")
    return 0


def write_jsonl(path: Path, records) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    sys.exit(main())
