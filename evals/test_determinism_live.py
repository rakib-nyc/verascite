"""Eval: determinism through the *fetch* path, not just the cache.

The offline determinism test proves the deterministic layer is stable. It does
not prove the network path is, because a warm cache skips exactly the code
where the swallowed 429 lived -- three runs off one cache prove the cache is
deterministic and nothing more.

This runs the full pipeline three times with the disk cache bypassed, so every
request is really made, and requires the verdicts to be identical. It needs a
token and real network, so it is skipped by default:

    COURTLISTENER_API_TOKEN=... python -m pytest evals/ -q -m live

Expect roughly a minute per run: the docket lookups are throttled at 5/min.

Measured 2026-08-30 on tests/fixtures/sample_brief.md -- 3 cold runs, verdicts
identical, 62s each.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("COURTLISTENER_API_TOKEN"),
        reason="needs COURTLISTENER_API_TOKEN and live network",
    ),
]

#: Fields that legitimately differ run to run and say nothing about verdicts.
VOLATILE = {
    "created_at", "updated_at", "retrieved_at", "run_at", "oldest_evidence",
    "requests", "cache_hits", "cache_misses", "expired_from_cache",
    "throttle_waits", "fetched_now", "opinion_versions_fetched", "failed",
}


def _strip(value):
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def _cold_run(out: Path) -> dict:
    subprocess.run(
        [sys.executable, "verascite/run_audit.py",
         "tests/fixtures/sample_brief.md", "--out", str(out), "--no-cache", "--quiet"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    return json.loads((out / "ledger.json").read_text())


def test_verdicts_are_identical_across_cold_live_runs(tmp_path):
    ledgers = [_cold_run(tmp_path / f"run{i}") for i in range(3)]
    assert all(led["run_meta"]["existence_lookup"]["cache_hits"] == 0 for led in ledgers), (
        "a run was served from cache; this eval must exercise the fetch path"
    )

    shapes = [json.dumps(_strip(led), sort_keys=True) for led in ledgers]
    if len(set(shapes)) != 1:
        first, second = json.loads(shapes[0]), json.loads(shapes[1])
        for a, b in zip(first["entries"], second["entries"]):
            if a != b:
                pytest.fail(
                    f"cold runs diverged on {a['citation_id']}: "
                    f"{a['overall']} vs {b['overall']}"
                )
        pytest.fail("cold runs diverged outside the entry list")


def test_no_sub_opinion_is_silently_dropped_between_runs(tmp_path):
    """The failure mode that made the swallowed 429 invisible."""
    counts = []
    for i in range(2):
        led = _cold_run(tmp_path / f"drop{i}")
        counts.append(sum(len(e["sources_consulted"]) for e in led["entries"]))
    assert counts[0] == counts[1], (
        f"the same document consulted a different number of sources across runs: {counts}"
    )
