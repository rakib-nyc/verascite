"""The report is the product. These test it as communication, not as data."""

from __future__ import annotations

import pytest

from verascite.report import DISCLOSURE, render_report
from verascite.verdicts import Overall, Verdict

from test_regression_corpus import build_ledger


@pytest.fixture(scope="module")
def report():
    return render_report(build_ledger(), generated_at="2026-08-30T00:00:00+00:00")


# --- P9: the mandatory disclosure ---------------------------------------------


def test_disclosure_appears_before_any_finding(report):
    assert DISCLOSURE.strip() in report
    assert report.index("evidence package") < report.index("## Findings")


def test_report_never_certifies(report):
    for phrase in ("certif", "not a citator", "not legal advice", "Rule 11"):
        assert phrase.lower() in report.lower()


def test_absence_is_distinguished_from_fabrication_in_plain_language(report):
    assert "`UNVERIFIED` does not mean fabricated" in report
    # And the word "fabricated" never appears as a verdict about a citation.
    assert "FABRICATED" not in report


# --- ordering and prominence --------------------------------------------------


def test_flagged_findings_precede_confirmed_ones(report):
    assert report.index("### FLAGGED") < report.index("## Confirmed citations")


def test_severity_sections_are_ordered_by_work_required(report):
    present = [
        report.index(f"### {name}")
        for name in ("FLAGGED", "UNVERIFIED", "REVIEW")
        if f"### {name}" in report
    ]
    # The corpus may not exercise every severity; order among those it does.
    assert len(present) >= 1
    assert present == sorted(present)


def test_successes_are_collapsed_not_narrated(report):
    """A report that leads with reassurance trains people to stop reading."""
    confirmed = report.split("## Confirmed citations")[1]
    assert "| Check | Verdict |" not in confirmed


def test_what_was_not_checked_is_stated(report):
    section = report.split("**What was not checked**")[1].split("##")[0]
    assert "citator" in section
    assert "good law" in section
    assert "proposition" in section


def test_every_finding_names_a_next_action(report):
    findings = report.split("## Findings")[1].split("## Confirmed citations")[0]
    blocks = [b for b in findings.split("#### ")[1:]]
    assert blocks
    for block in blocks:
        assert "**What to do:**" in block, block[:120]


# --- the specific findings ----------------------------------------------------


def test_altered_quote_leads_with_the_substitution_not_the_score(report):
    """A reader acts on "do -> suffice"; they cannot calibrate "94%"."""
    line = next(l for l in report.splitlines() if "altered quotation" in l)
    assert line.index("brief says") < line.index("similarity")


def test_invented_reporter_is_reported_without_burying_it(report):
    block = report.split("33 Umbrella 422")[1].split("####")[0]
    assert "`FAIL`" in block
    # The consequential NOT_CHECKABLE dimensions are summarised, not tabulated.
    assert block.count("NOT_CHECKABLE") == 0
    # The collapse must name the check that caused it, so a reader can see
    # why those dimensions are missing rather than guess.
    assert "Not checkable while Reporter is unresolved" in block


def test_suppression_is_derived_and_preserved_in_the_ledger():
    """Collapsed rows survive in full in the ledger (P3), never inferred."""
    from test_regression_corpus import build_ledger

    ledger = build_ledger()
    entry = ledger.get("cite_0007")
    suppressed = {n: c for n, c in entry.checks.items() if c.suppressed_by}
    assert suppressed, "the cascade was not recorded"
    for name, check in suppressed.items():
        assert check.suppressed_by == "reporter_valid"
        # Every suppressed row is still present with its own reason.
        assert check.reason


def test_dissent_attribution_finding_is_explained(report):
    block = report.split("Quotation attribution")[1][:400]
    assert "dissent" in block


def test_quote_accounting_is_visible(report):
    section = report.split("**Quotations**")[1].split("##")[0]
    assert "found in the document" in section
    assert "compared against retrieved opinion text" in section


def test_threshold_version_is_recorded_for_reproducibility(report):
    assert "threshold set version" in report
    assert '"quote_material_alteration_floor": 0.75' in report


# --- structure ----------------------------------------------------------------


def test_report_is_stable_across_renders():
    ledger = build_ledger()
    first = render_report(ledger, generated_at="2026-08-30T00:00:00+00:00")
    second = render_report(ledger, generated_at="2026-08-30T00:00:00+00:00")
    assert first == second


def test_tables_survive_pipes_in_untrusted_text():
    """A pipe in document text would otherwise break the table row it sits in."""
    from verascite.ledger import Ledger
    from verascite.models import CiteKind, LedgerEntry, Location, RawCitation
    from verascite.verdicts import CheckResult

    ledger = Ledger(document_path="t")
    entry = LedgerEntry(
        citation_id="cite_0000",
        citation=RawCitation(
            kind=CiteKind.FULL_CASE, raw_text="A v. B, 1 F.2d 1",
            location=Location(0, 16),
        ),
    )
    entry.set_check("quote", CheckResult(Verdict.FAIL, evidence="a | b | c"))
    ledger.add(entry)
    rendered = render_report(ledger, generated_at="x")
    row = next(l for l in rendered.splitlines() if l.startswith("| Quotation"))
    # Pipes are removed rather than escaped: an escaped pipe still renders as a
    # pipe in the cell, and the text is not ours to reproduce faithfully.
    assert row.count("|") == 4
    assert "a b c" in row


def test_empty_document_still_renders_the_disclosure():
    from verascite.ledger import Ledger

    rendered = render_report(Ledger(document_path="empty.md"), generated_at="x")
    assert "evidence package" in rendered
    assert "No citation required attention." in rendered
