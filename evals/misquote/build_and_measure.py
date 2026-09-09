"""P5: does misquotation detection work on publisher text?

**The question, and why it was open.** Misquotation is 42 labels in the
benchmark corpus at 0% recall, and V8h established why: a *correct* quotation
matches its source at a median of 0.929 rather than 1.00, because both the
brief and the archive are OCR output. The injected errors are one- and
two-word swaps, which are smaller than the disagreement OCR already produces
between a correct quotation and its source. Precision peaked at 17.2% at every
threshold, so the check was measured and deliberately not shipped.

That rejection was always a statement about the *corpus*, not about the check.
The natural hypothesis is that publisher-quality text dissolves the problem.
It could not be tested on the benchmark: only one misquote-labelled citation
there resolved to a non-OCR opinion.

**What this harness does.** It tests the hypothesis on text that is not OCR.
The cache holds opinion texts carrying the archive's own ``extracted_by_ocr``
flag, so the non-OCR subset can be separated exactly rather than guessed at.
Passages are drawn from those opinions as the correct quotations, and the same
one- and two-word alterations the benchmark injects are applied to produce the
misquotes. The matcher is the shipped one, unmodified.

**What would settle it.** If the two distributions separate on clean text --
correct quotations at or near 1.00, altered ones well below -- then a
threshold exists that is precise at the true base rate, and the check can ship
gated on ``extracted_by_ocr == False``, a flag the codebase already tracks per
opinion. If they do not separate, the rejection stands and is now grounded in
evidence rather than in an untested assumption.

**The trap in the obvious design, and what is done about it.** Drawing a
passage out of an opinion and matching it back against that same opinion
guarantees a ratio of 1.00. It is the identical string. A measurement built
that way would report a beautiful separation and would be measuring nothing
except that string comparison works.

What the benchmark measured was different and harder: a *brief* quoting an
opinion, where the brief was OCR and the archive was OCR, and the two
disagreed at a median of 0.929 before anyone made a mistake. The question P5
actually asks is what that disagreement becomes when the archive text is
clean.

So the correct quotations here are generated in three grades, reported
separately and never pooled:

``verbatim``
    The passage copied exactly. This is an **upper bound and it is circular by
    construction** -- it models a brief that pasted perfectly from a clean
    source. It is reported because it establishes the ceiling, and it is
    labelled so nobody mistakes the ceiling for the finding.

``conventional``
    The passage as a careful lawyer would actually quote it: a bracketed case
    alteration at the start, an internal citation dropped, a bracketed
    substitution. These are *correct* quotations under any convention, and a
    check that flags them is unusable regardless of what it catches.

``noisy``
    The passage with transcription noise -- the character-level slips a real
    document acquires. This is the closest analogue to what the benchmark
    measured, minus the archive-side OCR that clean text removes.

The finding, if there is one, lives in ``conventional`` and ``noisy``. A
threshold that separates misquotes only from ``verbatim`` has not been shown
to work on anything a lawyer would file.

Nothing here is reported unless it was measured. The base-rate calculation in
particular is not a projection: it applies the measured per-class rates to the
defect frequency observed in the benchmark corpus, and says so.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from verascite.clients.opinions import OpinionText  # noqa: E402
from verascite.config import (  # noqa: E402
    QUOTE_MATERIAL_ALTERATION_FLOOR,
    QUOTE_MIN_CHARS,
    QUOTE_NEAR_MISS_FLOOR,
)
from verascite.verify_quote import _best_window, normalize_quote  # noqa: E402

#: One- and two-word substitutions of the kind the benchmark injects: a word
#: swapped for a near-synonym that leaves the sentence grammatical and changes
#: what it says. Drawn from vocabulary that actually recurs in opinions, so the
#: alteration lands on a real word rather than on a rare one.
SUBSTITUTIONS = {
    "must": "may", "may": "must", "shall": "should", "should": "shall",
    "not": "never", "never": "not", "cannot": "need not",
    "required": "permitted", "permitted": "required",
    "affirmed": "reversed", "reversed": "affirmed",
    "granted": "denied", "denied": "granted",
    "plaintiff": "defendant", "defendant": "plaintiff",
    "always": "sometimes", "sometimes": "always",
    "all": "some", "some": "all", "any": "every", "every": "any",
    "before": "after", "after": "before",
    "more": "less", "less": "more", "greater": "lesser",
    "sufficient": "insufficient", "insufficient": "sufficient",
    "reasonable": "unreasonable", "unreasonable": "reasonable",
    "valid": "invalid", "lawful": "unlawful", "proper": "improper",
    "does": "did", "is": "was", "are": "were", "has": "had",
    "holds": "held", "finds": "found", "concludes": "concluded",
    "error": "harm", "harmless": "harmful",
}

#: Quoting conventions that are correct and that change the characters on the
#: page. A check that cannot absorb these flags careful lawyers, which is the
#: fastest way for a verifier to be switched off.
def quote_conventionally(passage: str, rng: random.Random) -> str:
    """Rewrite a passage the way a brief would legitimately quote it."""
    tokens = passage.split()
    if len(tokens) < 8:
        return passage
    out = list(tokens)
    # A bracketed case alteration on the opening word, the commonest of all.
    if out[0][:1].isupper():
        out[0] = f"[{out[0][0].lower()}]{out[0][1:]}"
    else:
        out[0] = f"[{out[0][0].upper()}]{out[0][1:]}"
    # A bracketed substitution mid-passage, as when a pronoun is made explicit.
    index = rng.randrange(2, len(out) - 2)
    out[index] = f"[{out[index]}]"
    return " ".join(out)


#: Character-level slips a real document acquires: a transposition, a dropped
#: letter, a doubled one. Rate is per word, applied independently.
def add_noise(passage: str, rng: random.Random, rate: float) -> str:
    """Introduce transcription noise at a per-word rate."""
    out = []
    for word in passage.split():
        if len(word) > 3 and rng.random() < rate:
            position = rng.randrange(1, len(word) - 1)
            choice = rng.randrange(3)
            if choice == 0:      # transpose
                word = word[:position] + word[position + 1] + word[position] + word[position + 2:]
            elif choice == 1:    # drop
                word = word[:position] + word[position + 1:]
            else:                # double
                word = word[:position] + word[position] + word[position:]
        out.append(word)
    return " ".join(out)


#: A sentence shorter than this is not a quotation anyone checks; the shipped
#: matcher declines below QUOTE_MIN_CHARS and this respects the same floor.
MIN_PASSAGE = max(QUOTE_MIN_CHARS, 80)
MAX_PASSAGE = 400

_SENTENCE = re.compile(r"(?<=[.!?])\s+")
#: Star-pagination markers and reporter furniture are not part of the prose.
_FURNITURE = re.compile(r"\*+\d+|\[\d+\]|\bId\.\b|\bsupra\b", re.IGNORECASE)


def load_opinions(cache: Path, ocr: bool) -> list[OpinionText]:
    """Cached opinion texts, split by the archive's own OCR flag.

    The flag is the archive's, not ours. That matters: the split has to be
    something the shipped code can reproduce at run time to gate a check on
    it, and ``extracted_by_ocr`` is already carried on every OpinionText.
    """
    out = []
    for path in sorted((cache / "opinions").rglob("*.json")):
        try:
            payload = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        for record in payload if isinstance(payload, list) else [payload]:
            if not isinstance(record, dict) or not record.get("text"):
                continue
            is_ocr = str(record.get("extracted_by_ocr")).lower() == "true"
            if is_ocr is not ocr:
                continue
            if not record.get("is_court_holding", True):
                continue
            out.append(record)
    return out


def passages(record: dict, rng: random.Random, count: int) -> list[str]:
    """Quotable sentences from one opinion.

    Sentences carrying pagination markers or citation furniture are skipped:
    they are not what a brief quotes, and including them would measure the
    matcher against text no one would ever cite.
    """
    text = record["text"]
    found = []
    for sentence in _SENTENCE.split(text):
        sentence = " ".join(sentence.split())
        if not (MIN_PASSAGE <= len(sentence) <= MAX_PASSAGE):
            continue
        if _FURNITURE.search(sentence):
            continue
        if sum(c.isalpha() for c in sentence) < 0.7 * len(sentence):
            continue
        found.append(sentence)
    rng.shuffle(found)
    return found[:count]


def alter(passage: str, words: int, rng: random.Random) -> tuple[str, list[str]]:
    """Swap one or two words for near-synonyms. Returns the text and the swaps.

    Returns the original unchanged when the passage carries no substitutable
    word, and the caller drops it. Inventing a substitution the vocabulary does
    not contain would make the injected error unlike the ones being modelled.
    """
    tokens = passage.split()
    positions = [
        i for i, t in enumerate(tokens)
        if re.sub(r"\W", "", t).lower() in SUBSTITUTIONS
    ]
    if len(positions) < words:
        return passage, []
    chosen = rng.sample(positions, words)
    swaps = []
    for index in chosen:
        token = tokens[index]
        bare = re.sub(r"\W", "", token).lower()
        replacement = SUBSTITUTIONS[bare]
        if token[:1].isupper():
            replacement = replacement.capitalize()
        tokens[index] = token.replace(re.sub(r"\W", "", token), replacement, 1)
        swaps.append(f"{bare}->{replacement}")
    return " ".join(tokens), swaps


def match_ratio(quote: str, record: dict) -> float:
    """The shipped matcher's similarity for one quotation against one opinion."""
    haystack = normalize_quote(record["text"])
    needle = normalize_quote(quote)
    if not needle or len(needle) < QUOTE_MIN_CHARS:
        return -1.0
    ratio, _, _ = _best_window(haystack, needle)
    return ratio


def build(cache: Path, ocr: bool, per_opinion: int, limit: int, seed: int,
          noise_rate: float) -> list[dict]:
    """Generate the graded correct classes and the misquotes from one cache."""
    rng = random.Random(seed)
    records = load_opinions(cache, ocr=ocr)
    rng.shuffle(records)
    rows: list[dict] = []
    for record in records:
        if limit and len(rows) >= limit:
            break
        for passage in passages(record, rng, per_opinion):
            variants = [
                ("correct", "verbatim", 0, passage),
                ("correct", "conventional", 0, quote_conventionally(passage, rng)),
                ("correct", "noisy", 0, add_noise(passage, rng, noise_rate)),
            ]
            for words in (1, 2):
                altered, swaps = alter(passage, words, rng)
                if swaps:
                    # A misquote a lawyer files is also quoted conventionally
                    # and also carries transcription noise. Testing the altered
                    # text in isolation would compare a clean misquote against
                    # a noisy correct quotation, which is not the comparison
                    # the check has to make.
                    variants.append(("misquote", "conventional", words,
                                     quote_conventionally(altered, rng)))
                    variants.append(("misquote", "noisy", words,
                                     add_noise(altered, rng, noise_rate)))
                    variants.append(("misquote", "verbatim", words, altered))
            for kind, grade, words, text in variants:
                ratio = match_ratio(text, record)
                if ratio < 0:
                    continue
                rows.append({
                    "opinion_id": record.get("opinion_id"),
                    "ocr": ocr,
                    "kind": kind,
                    "grade": grade,
                    "words_changed": words,
                    "ratio": round(ratio, 4),
                    "length": len(text),
                })
    return rows


def median(values: list[float]) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def sweep(rows: list[dict], base_rate_positives: int, base_rate_negatives: int,
          grade: str) -> dict:
    """Threshold sweep, reported at the observed rate and at the true base rate.

    A balanced sample flatters a check for a rare defect. The second half of
    each row is the number that decides whether the check can ship: the
    measured per-class rates applied to the frequency the defect actually has.
    """
    correct = [r["ratio"] for r in rows
               if r["kind"] == "correct" and r["grade"] == grade]
    misquote = [r["ratio"] for r in rows
                if r["kind"] == "misquote" and r["grade"] == grade]
    table = []
    for threshold in (0.99, 0.98, 0.97, 0.95, 0.93, 0.90, 0.85, 0.80, 0.75, 0.70):
        caught = sum(1 for r in misquote if r <= threshold)
        flagged_clean = sum(1 for r in correct if r <= threshold)
        recall = caught / len(misquote) if misquote else 0.0
        false_positive_rate = flagged_clean / len(correct) if correct else 0.0
        observed_precision = (
            caught / (caught + flagged_clean) if caught + flagged_clean else None
        )
        # The same rates, applied to the real frequency of the defect.
        expected_tp = recall * base_rate_positives
        expected_fp = false_positive_rate * base_rate_negatives
        base_rate_precision = (
            expected_tp / (expected_tp + expected_fp) if expected_tp + expected_fp else None
        )
        table.append({
            "threshold": threshold,
            "misquotes_caught": caught,
            "of_misquotes": len(misquote),
            "clean_flagged": flagged_clean,
            "of_clean": len(correct),
            "recall": round(recall, 4),
            "false_positive_rate": round(false_positive_rate, 4),
            "precision_on_this_sample": round(observed_precision, 4) if observed_precision else None,
            "precision_at_true_base_rate": round(base_rate_precision, 4) if base_rate_precision else None,
        })
    return {
        "grade": grade,
        "circular": grade == "verbatim",
        "n_correct": len(correct),
        "n_misquote": len(misquote),
        "median_correct": round(median(correct), 4),
        "median_misquote": round(median(misquote), 4),
        "min_correct": round(min(correct), 4) if correct else None,
        "perfect_matches_among_correct": sum(1 for r in correct if r >= 0.9999),
        "base_rate_assumption": {
            "positives": base_rate_positives,
            "negatives": base_rate_negatives,
            "note": (
                "the misquote labels and the sound quotations in the benchmark "
                "corpus. Applied to the measured per-class rates above; this is "
                "an application of measured rates, not a separate measurement"
            ),
        },
        "sweep": table,
        "shipped_thresholds": {
            "quote_material_alteration_floor": QUOTE_MATERIAL_ALTERATION_FLOOR,
            "quote_near_miss_floor": QUOTE_NEAR_MISS_FLOOR,
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path,
                        default=Path.home() / "Documents/verascite/.verascite-cache")
    parser.add_argument("--per-opinion", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0, help="stop after N rows")
    parser.add_argument("--seed", type=int, default=20260909)
    parser.add_argument("--noise-rate", type=float, default=0.02,
                        help="per-word transcription-noise rate for the noisy grade")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    result = {"noise_rate": args.noise_rate, "seed": args.seed}
    for label, ocr in (("non_ocr", False), ("ocr", True)):
        rows = build(args.cache, ocr, args.per_opinion, args.limit, args.seed,
                     args.noise_rate)
        if not rows:
            result[label] = {"error": "no opinions of this kind in the cache"}
            continue
        result[label] = {
            "rows": rows,
            "grades": {
                grade: sweep(rows, 42, 233, grade)
                for grade in ("verbatim", "conventional", "noisy")
            },
        }
        print(f"{label}: {len(rows)} rows", flush=True)

    args.out.write_text(json.dumps(result, indent=2))
    for label in ("non_ocr", "ocr"):
        block = result.get(label, {})
        for grade, data in (block.get("grades") or {}).items():
            note = "  [CIRCULAR -- upper bound only]" if data["circular"] else ""
            print(f"\n=== {label} / {grade}{note} ===")
            print(f"correct median {data['median_correct']} (min {data['min_correct']}, "
                  f"{data['perfect_matches_among_correct']}/{data['n_correct']} exact) "
                  f"| misquote median {data['median_misquote']}")
            print(f"{'thr':>5} {'recall':>7} {'fp rate':>8} {'prec(sample)':>13} {'prec(base rate)':>16}")
            for row in data["sweep"]:
                print(f"{row['threshold']:>5} {row['recall']:>7.3f} "
                      f"{row['false_positive_rate']:>8.3f} "
                      f"{str(row['precision_on_this_sample']):>13} "
                      f"{str(row['precision_at_true_base_rate']):>16}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
