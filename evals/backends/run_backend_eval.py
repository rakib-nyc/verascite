"""Measure a reading backend against the recorded proposition samples.

Why this exists. Every published figure for the model layer was produced by a
reader driven by hand, one batch at a time. Shipping a configurable backend
changes who does the reading, and a number measured with one reader says
nothing about another. So the backend layer does not ship with borrowed
numbers: it ships with whatever this harness measures for the backend actually
used, and the two are reported separately.

The samples are the ones already in evals/proposition/ -- the same passages,
the same propositions, the same gold labels -- so a backend's score here is
directly comparable to the recorded run rather than to a fresh corpus.

Usage:

    python evals/backends/run_backend_eval.py \
        --model local --model-name MODEL --out results-MODEL.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from verascite.readers import build_reader, parse_model_params  # noqa: E402
from verascite.verify_proposition import (  # noqa: E402
    PropositionQuestion,
    ask_and_verify,
)

SAMPLES = ROOT / "evals" / "proposition"


def load(name: str, prop_key: str) -> list[dict]:
    rows = json.loads((SAMPLES / name).read_text())
    out = []
    for index, row in enumerate(rows, start=1):
        out.append({
            "n": index,
            "subset": name.replace("_sample.json", "").replace(".json", ""),
            "cite": row["cite"],
            "proposition": row[prop_key],
            "signal": row.get("signal"),
            "passage": row["passage"],
            "gold": row["gold"],
        })
    return out


def is_a_flag(verdict: str, confidence: str) -> bool:
    """The scoring rule used in the recorded run, reproduced unchanged.

    A flag is CONTRADICTED at any confidence, or NOT_SUPPORTED at high
    confidence. Everything else routes to a human and is not a finding, so
    counting it as one would score a check the tool does not actually make.
    """
    return verdict == "CONTRADICTED" or (verdict == "NOT_SUPPORTED" and confidence == "high")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="local")
    parser.add_argument("--model-name", required=True)
    parser.add_argument("--model-base-url", default="")
    parser.add_argument("--model-param", action="append", default=[])
    parser.add_argument("--model-max-tokens", type=int, default=8192)
    parser.add_argument("--model-timeout", type=int, default=600)
    parser.add_argument("--limit", type=int, default=0, help="stop after N items")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    reader = build_reader(
        args.model,
        model=args.model_name,
        base_url=args.model_base_url,
        extra_params=parse_model_params(args.model_param),
        max_tokens=args.model_max_tokens,
        timeout_s=args.model_timeout,
    )

    items = load("dahl_sample.json", "prop") + load("sample.json", "proposition")
    if args.limit:
        items = items[: args.limit]

    records = []
    started = time.time()
    for item in items:
        answer = ask_and_verify(
            PropositionQuestion(
                citation=item["cite"],
                proposition=item["proposition"],
                signal=item["signal"],
                opinion_text=item["passage"],
            ),
            reader,
        )
        flagged = is_a_flag(answer.verdict, answer.confidence) and not answer.rejection
        records.append({
            **{k: item[k] for k in ("n", "subset", "cite", "gold")},
            "verdict": answer.verdict,
            "confidence": answer.confidence,
            "span_verified": answer.span_verified,
            "rejection": answer.rejection,
            "flagged": flagged,
        })
        print(
            f"[{len(records):>2}/{len(items)}] {item['subset']:<5} "
            f"gold={item['gold']:<28} -> {answer.verdict:<20} {answer.confidence:<6} "
            f"flag={flagged} {'REJECTED' if answer.rejection else ''}",
            flush=True,
        )

    summary = score(records)
    summary["elapsed_s"] = round(time.time() - started, 1)
    summary["backend"] = reader.describe()
    summary["reader_stats"] = reader.stats.to_dict()
    args.out.write_text(json.dumps({"summary": summary, "records": records}, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


def score(records: list[dict]) -> dict:
    """Precision and recall per subset and overall.

    A gold label of "clean" is a negative; anything else is a positive. There
    is no partial credit: the tool either produced a finding a lawyer would act
    on or it did not.
    """
    out = {}
    subsets = sorted({r["subset"] for r in records}) + ["all"]
    for subset in subsets:
        rows = records if subset == "all" else [r for r in records if r["subset"] == subset]
        tp = sum(1 for r in rows if r["flagged"] and r["gold"] != "clean")
        fp = sum(1 for r in rows if r["flagged"] and r["gold"] == "clean")
        fn = sum(1 for r in rows if not r["flagged"] and r["gold"] != "clean")
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision and recall
            else (0.0 if precision is not None and recall is not None else None)
        )
        out[subset] = {
            "n": len(rows), "tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision, 3) if precision is not None else None,
            "recall": round(recall, 3) if recall is not None else None,
            "f1": round(f1, 3) if f1 is not None else None,
            "rejected_by_interlock": sum(1 for r in rows if r["rejection"]),
        }
    return out


if __name__ == "__main__":
    raise SystemExit(main())
