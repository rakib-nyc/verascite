"""Run the audit across a directory of documents, with one consolidated report.

A command line is a developer's tool. The people every standing order is
addressed to work in Word and a document management system, and they do not
review one brief at a time -- they review what came out of a matter, or what an
associate produced this week.

**What this does not do.** It does not parallelise. The rate limits on the free
archives are the binding constraint, they are shared across every document in a
batch, and running four documents at once against a nonprofit's five requests
per minute would spend the budget four times as fast to finish no sooner. Runs
are sequential and the cache does the work: two briefs citing the same authority
cost one lookup between them.

**Failure is per document.** One unreadable file must not take down a run over
forty. A document that fails is recorded as failed, named in the summary, and
the batch continues -- because the alternative is a lawyer discovering at the
end that nothing was checked.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .ledger import Ledger
from .verdicts import Overall

#: Formats the ingest stage can read.
SUFFIXES = (".pdf", ".docx", ".md", ".txt")

#: Files a document management system leaves lying about. Auditing a Word lock
#: file produces a confusing error and no findings.
_IGNORE_PREFIXES = ("~$", ".")


def find_documents(root: Path, recursive: bool = True) -> list[Path]:
    """Every auditable document under ``root``, in a stable order."""
    root = Path(root)
    if root.is_file():
        return [root]
    walk = root.rglob("*") if recursive else root.glob("*")
    found = [
        path for path in walk
        if path.is_file()
        and path.suffix.lower() in SUFFIXES
        and not path.name.startswith(_IGNORE_PREFIXES)
    ]
    return sorted(found)


@dataclass
class DocumentOutcome:
    """What became of one document in the batch."""

    path: Path
    out_dir: Optional[Path] = None
    counts: dict = field(default_factory=dict)
    citations: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def flagged(self) -> int:
        return self.counts.get(Overall.FLAGGED.value, 0)

    @property
    def unverified(self) -> int:
        return self.counts.get(Overall.UNVERIFIED.value, 0)

    def to_dict(self) -> dict:
        out = {
            "document": str(self.path),
            "citations": self.citations,
            "counts": self.counts,
        }
        if self.out_dir:
            out["output"] = str(self.out_dir)
        if self.error:
            out["error"] = self.error
        return out


def run_batch(
    documents: Iterable[Path],
    out_root: Path,
    runner,
    quiet: bool = False,
) -> list[DocumentOutcome]:
    """Audit each document into its own directory under ``out_root``.

    ``runner`` takes (document, out_dir) and returns a Ledger. It is injected
    rather than imported so a caller can supply a configured reader, and so
    this module can be tested without standing up the whole pipeline.
    """
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    outcomes: list[DocumentOutcome] = []
    documents = list(documents)

    for index, document in enumerate(documents, start=1):
        # Named for the document, deduplicated by index, because two matters
        # routinely contain a file called "brief.docx".
        slug = f"{index:03d}-{document.stem[:60]}"
        target = out_root / slug
        if not quiet:
            print(f"[{index}/{len(documents)}] {document.name}", file=sys.stderr)
        outcome = DocumentOutcome(path=document, out_dir=target)
        try:
            ledger = runner(document, target)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            # One bad file must not end a run over forty.
            outcome.error = f"{type(exc).__name__}: {exc}"
            outcome.out_dir = None
        else:
            outcome.counts = ledger.counts()
            outcome.citations = len(ledger)
        outcomes.append(outcome)
    return outcomes


def render_summary(outcomes: list[DocumentOutcome], generated_at: str = "") -> str:
    """A consolidated report over the whole batch.

    Ordered most-severe first, like the single-document report, because the
    reason to run a batch is to find which documents need attention.
    """
    stamp = generated_at or datetime.now(timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")
    ok = [o for o in outcomes if o.ok]
    failed = [o for o in outcomes if not o.ok]
    total_flagged = sum(o.flagged for o in ok)
    total_unverified = sum(o.unverified for o in ok)

    lines = ["# Batch citation review", ""]
    lines.append(
        "> This is an evidence package covering several documents. It certifies "
        "nothing, it is not a citator, and **`UNVERIFIED` does not mean "
        "fabricated** -- it means the sources consulted do not contain the "
        "authority, which is routine for recent, unpublished, and vendor-only "
        "citations."
    )
    lines.append("")
    lines.append(f"Generated {stamp}")
    lines.append("")
    lines.append("| | |")
    lines.append("|---|---:|")
    lines.append(f"| Documents reviewed | {len(ok)} |")
    if failed:
        lines.append(f"| Documents that could not be read | {len(failed)} |")
    lines.append(f"| Citations examined | {sum(o.citations for o in ok)} |")
    lines.append(f"| Citations contradicted by a retrieved source | {total_flagged} |")
    lines.append(f"| Citations absent from the sources consulted | {total_unverified} |")
    lines.append("")

    if not ok:
        lines.append("No document was successfully reviewed.")
        lines.append("")
    else:
        lines.append("## Documents, most severe first")
        lines.append("")
        lines.append("| Document | Citations | Flagged | Unverified | Review | Verified |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for outcome in sorted(ok, key=lambda o: (-o.flagged, -o.unverified, str(o.path))):
            counts = outcome.counts
            lines.append(
                f"| `{outcome.path.name}` | {outcome.citations} "
                f"| {outcome.flagged} | {outcome.unverified} "
                f"| {counts.get(Overall.REVIEW.value, 0)} "
                f"| {counts.get(Overall.VERIFIED.value, 0)} |"
            )
        lines.append("")
        attention = [o for o in ok if o.flagged]
        if attention:
            lines.append(
                f"**{len(attention)} document(s) contain a citation contradicted by a "
                "retrieved source.** The evidence for each is in that document's own "
                "report."
            )
        else:
            lines.append(
                "No document contains a citation contradicted by a retrieved source. "
                "This is not a clearance: it means nothing consulted contradicted "
                "them."
            )
        lines.append("")

    if failed:
        lines.append("## Documents that could not be read")
        lines.append("")
        lines.append(
            "These were **not checked at all.** They are not clean; they are "
            "unexamined."
        )
        lines.append("")
        for outcome in failed:
            lines.append(f"- `{outcome.path}` — {outcome.error}")
        lines.append("")
    return "\n".join(lines)


def write_summary(outcomes: list[DocumentOutcome], out_root: Path) -> Path:
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "batch.json").write_text(
        json.dumps([o.to_dict() for o in outcomes], indent=2), encoding="utf-8")
    path = out_root / "batch-report.md"
    path.write_text(render_summary(outcomes), encoding="utf-8")
    return path
