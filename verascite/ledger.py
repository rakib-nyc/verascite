"""The citation ledger: persistent, checkpointed verdict state (P5).

Written to disk after every mutation so that a long run survives
interruption, context compaction, and crash. Resume reads the ledger and
skips entries that already hold a non-PENDING verdict.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .models import CiteKind, LedgerEntry, Location, RawCitation, Resolution
from .verdicts import CheckResult, Overall, SEVERITY_ORDER, Verdict

SCHEMA_VERSION = "1.0"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class Ledger:
    """Ordered collection of LedgerEntry, keyed by citation_id."""

    document_path: str = ""
    document_format: str = ""
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)
    entries: dict[str, LedgerEntry] = field(default_factory=dict)
    #: sources named in the run, for the report header (P3)
    sources_available: list[str] = field(default_factory=list)
    run_meta: dict = field(default_factory=dict)
    path: Optional[Path] = None

    # --- access ---------------------------------------------------------

    def __iter__(self) -> Iterator[LedgerEntry]:
        return iter(self.entries.values())

    def __len__(self) -> int:
        return len(self.entries)

    def add(self, entry: LedgerEntry) -> LedgerEntry:
        self.entries[entry.citation_id] = entry
        return entry

    def get(self, citation_id: str) -> Optional[LedgerEntry]:
        return self.entries.get(citation_id)

    def by_overall(self, overall: Overall) -> list[LedgerEntry]:
        return [e for e in self if e.overall is overall]

    def counts(self) -> dict[str, int]:
        out = {o.value: 0 for o in Overall}
        for entry in self:
            out[entry.overall.value] += 1
        return out

    def sorted_by_severity(self) -> list[LedgerEntry]:
        return sorted(
            self,
            key=lambda e: (SEVERITY_ORDER[e.overall], e.citation.location.char_start),
        )

    def distinct_authorities(self) -> dict[str, list[LedgerEntry]]:
        """Group entries by authority_key -- one lookup per distinct authority (spec 4.5)."""
        groups: dict[str, list[LedgerEntry]] = {}
        for entry in self:
            key = entry.authority_key
            if key:
                groups.setdefault(key, []).append(entry)
        return groups

    def pending(self, dimension: str) -> list[LedgerEntry]:
        """Entries whose named dimension has not been decided yet (resume support)."""
        return [
            e
            for e in self
            if e.checks.get(dimension, CheckResult(Verdict.PENDING)).verdict is Verdict.PENDING
        ]

    # --- persistence ----------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "document_path": self.document_path,
            "document_format": self.document_format,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "sources_available": list(self.sources_available),
            "run_meta": dict(self.run_meta),
            "counts": self.counts(),
            "entries": [e.to_dict() for e in self],
        }

    def checkpoint(self, path: Optional[Path] = None) -> Path:
        """Atomically write the ledger. Called after every citation (spec 4.5)."""
        target = Path(path or self.path or "ledger.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = utcnow()
        payload = json.dumps(self.to_dict(), indent=2, ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self.path = target
        return target

    @classmethod
    def load(cls, path: Path) -> "Ledger":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        led = cls(
            document_path=data.get("document_path", ""),
            document_format=data.get("document_format", ""),
            created_at=data.get("created_at", utcnow()),
            updated_at=data.get("updated_at", utcnow()),
            sources_available=list(data.get("sources_available", [])),
            run_meta=dict(data.get("run_meta", {})),
            path=Path(path),
        )
        for raw in data.get("entries", []):
            cite_data = dict(raw.get("citation", {}))
            loc = Location(**cite_data.pop("location", raw.get("location", {})))
            cite_data.pop("kind", None)
            citation = RawCitation(
                kind=CiteKind(raw["type"]),
                location=loc,
                **{k: v for k, v in cite_data.items() if k not in ("location", "kind")},
            )
            entry = LedgerEntry(
                citation_id=raw["citation_id"],
                citation=citation,
                checks={k: CheckResult.from_dict(v) for k, v in raw.get("checks", {}).items()},
                antecedent_id=raw.get("antecedent_id"),
                authority_key=raw.get("authority_key"),
                sources_consulted=list(raw.get("sources_consulted", [])),
                notes=list(raw.get("notes", [])),
            )
            if raw.get("resolved_to"):
                entry.resolved_to = Resolution(**raw["resolved_to"])
            led.add(entry)
        return led
