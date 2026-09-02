"""The verbatim-span interlock.

M5's acceptance condition: the interlock is proven to reject fabricated
supporting spans, verified by deliberately corrupting model output. A verifier
that can hallucinate its own evidence is worse than no verifier, so these
tests attack it rather than demonstrate it.
"""

from __future__ import annotations

import json

import pytest

from verascite.clients.opinions import OpinionText
from verascite.extract import extract
from verascite.models import Document
from verascite.resolve import resolve
from verascite.verdicts import CheckResult, Overall, Verdict
from verascite.verify_proposition import (
    PropositionQuestion,
    ask_and_verify,
    build_prompt,
    enforce_span_interlock,
    parse_answer,
    verify_proposition,
)

OPINION = (
    "Justice Souter delivered the opinion of the Court. A plaintiff's obligation "
    "to provide the grounds of his entitlement to relief requires more than "
    "labels and conclusions, and a formulaic recitation of the elements of a "
    "cause of action will not do. Factual allegations must be enough to raise a "
    "right to relief above the speculative level. We do not require heightened "
    "fact pleading of specifics."
)


def opinion(text: str = OPINION) -> OpinionText:
    return OpinionText(
        opinion_id=1, text=text, source_field="xml_harvard",
        type_label="majority opinion", is_court_holding=True, author="Souter",
    )


def question(prop: str = "A complaint must plead more than labels and conclusions.",
             signal=None) -> PropositionQuestion:
    return PropositionQuestion(
        citation="Bell Atlantic Corp. v. Twombly, 550 U.S. 544 (2007)",
        proposition=prop, signal=signal, opinion_text=OPINION,
    )


def reply(**kwargs) -> str:
    payload = {
        "verdict": "SUPPORTED", "confidence": "high",
        "supporting_span": "more than labels and conclusions",
        "reasoning": "The opinion says exactly this.", "signal_mismatch": None,
    }
    payload.update(kwargs)
    return json.dumps(payload)


# --- the interlock, attacked --------------------------------------------------


def test_a_genuine_span_passes():
    answer = ask_and_verify(question(), lambda _: reply())
    assert answer.span_verified and not answer.rejection


def test_a_fabricated_span_is_rejected():
    """The whole point: the model cannot invent its own evidence."""
    answer = ask_and_verify(
        question(),
        lambda _: reply(supporting_span="the Court expressly adopted a heightened standard"),
    )
    assert not answer.span_verified
    assert "does not appear in the opinion text" in answer.rejection


def test_a_subtly_altered_span_is_rejected():
    """One word changed is still not what the opinion says."""
    answer = ask_and_verify(
        question(), lambda _: reply(supporting_span="more than labels and assertions")
    )
    assert answer.rejection


def test_an_ellipsis_span_is_rejected():
    """Joining separated passages can manufacture support that is not there."""
    answer = ask_and_verify(
        question(),
        lambda _: reply(supporting_span="a formulaic recitation ... will not do"),
    )
    assert "ellipsis" in answer.rejection


def test_line_breaks_in_the_span_are_forgiven():
    """Whitespace is an artefact of retrieval, not a difference in the words."""
    answer = ask_and_verify(
        question(), lambda _: reply(supporting_span="more than labels\n  and conclusions")
    )
    assert answer.span_verified and not answer.rejection


def test_a_supporting_verdict_without_a_span_is_rejected():
    answer = ask_and_verify(question(), lambda _: reply(supporting_span=None))
    assert "requires a supporting span" in answer.rejection


def test_a_contradicted_verdict_also_needs_a_real_span():
    """An accusation needs evidence just as much as a confirmation does."""
    answer = ask_and_verify(
        question(),
        lambda _: reply(verdict="CONTRADICTED", supporting_span="the Court disagreed"),
    )
    assert answer.rejection


@pytest.mark.parametrize(
    "raw",
    ["not json at all", "", "{}", '{"verdict": "MAYBE"}', '{"verdict": "SUPPORTED",'],
)
def test_malformed_replies_are_rejections_not_verdicts(raw):
    answer = ask_and_verify(question(), lambda _: raw)
    assert answer.rejection


def test_a_model_that_raises_is_a_rejection():
    def boom(_):
        raise RuntimeError("network died")

    answer = ask_and_verify(question(), boom)
    assert "the model call failed" in answer.rejection


# --- the prompt ---------------------------------------------------------------


def test_prompt_forbids_background_knowledge_and_contains_the_opinion():
    text = build_prompt(question())
    assert "must not use background legal knowledge" in text
    assert "must not rely on recognising the case" in text.replace("\n", " ")
    assert OPINION[:60] in text
    assert "checked automatically" in text


def test_prompt_states_what_the_signal_claims():
    assert "only by analogy" in build_prompt(question(signal="cf."))
    assert "directly states" in build_prompt(question(signal=None))


# --- the dimension ------------------------------------------------------------


def ledger_for(text: str):
    document = Document(text=text, source_path="t", source_format="txt",
                        page_map=[(0, len(text), 1)])
    citations, notes = extract(document)
    return resolve(document, citations, notes)


BRIEF = ("A complaint must plead more than labels and conclusions to survive "
         "dismissal. Bell Atlantic Corp. v. Twombly, 550 U.S. 544 (2007).")


def test_a_discarded_answer_never_becomes_a_verdict():
    """A rejected reading yields NOT_CHECKABLE, never PASS and never FAIL."""
    ledger = ledger_for(BRIEF)
    entry = next(iter(ledger))
    verify_proposition(entry, [opinion()],
                       lambda _: reply(supporting_span="invented language"))
    check = entry.checks["proposition"]
    assert check.verdict is Verdict.NOT_CHECKABLE
    assert "discarded" in check.reason
    assert entry.overall is not Overall.FLAGGED


def test_verified_support_passes_with_the_span_as_evidence():
    ledger = ledger_for(BRIEF)
    entry = next(iter(ledger))
    verify_proposition(entry, [opinion()], lambda _: reply())
    check = entry.checks["proposition"]
    assert check.verdict is Verdict.PASS
    assert "labels and conclusions" in check.evidence


def test_contradiction_is_the_only_path_to_fail():
    ledger = ledger_for(BRIEF)
    entry = next(iter(ledger))
    verify_proposition(
        entry, [opinion()],
        lambda _: reply(verdict="CONTRADICTED",
                        supporting_span="We do not require heightened fact pleading"),
    )
    assert entry.checks["proposition"].verdict is Verdict.FAIL
    assert entry.overall is Overall.FLAGGED


@pytest.mark.parametrize(
    "verdict,confidence",
    [("NOT_SUPPORTED", "low"), ("PARTIALLY_SUPPORTED", "medium"), ("SUPPORTED", "low")],
)
def test_uncertain_readings_are_review_not_failures(verdict, confidence):
    """Crying wolf on defensible citations is how a verifier gets switched off."""
    ledger = ledger_for(BRIEF)
    entry = next(iter(ledger))
    verify_proposition(
        entry, [opinion()],
        lambda _: reply(verdict=verdict, confidence=confidence, supporting_span=None),
    )
    check = entry.checks["proposition"]
    assert check.verdict is Verdict.NOT_CHECKABLE
    assert entry.overall is not Overall.FLAGGED


def test_signal_mismatch_downgrades_a_pass_to_review():
    ledger = ledger_for(BRIEF)
    entry = next(iter(ledger))
    verify_proposition(
        entry, [opinion()],
        lambda _: reply(signal_mismatch="the opinion supports this only by analogy"),
    )
    check = entry.checks["proposition"]
    assert check.verdict is Verdict.NOT_CHECKABLE
    assert "not at the strength the citation claims" in check.reason


def test_no_model_means_not_checked_rather_than_passed():
    ledger = ledger_for(BRIEF)
    entry = next(iter(ledger))
    verify_proposition(entry, [opinion()], ask=None)
    assert entry.checks["proposition"].verdict is Verdict.NOT_CHECKABLE
    assert "no model available" in entry.checks["proposition"].reason


def test_no_opinion_text_means_not_checked():
    ledger = ledger_for(BRIEF)
    entry = next(iter(ledger))
    verify_proposition(entry, [], lambda _: reply())
    assert entry.checks["proposition"].verdict is Verdict.NOT_CHECKABLE


def test_the_model_never_sees_more_than_the_opinion_and_the_proposition():
    """Grounded input only: no citation lookup results, no other authorities."""
    captured = {}

    def capture(prompt):
        captured["prompt"] = prompt
        return reply()

    ledger = ledger_for(BRIEF)
    verify_proposition(next(iter(ledger)), [opinion()], capture)
    prompt = captured["prompt"]
    assert "courtlistener" not in prompt.lower()
    assert "cluster" not in prompt.lower()


def test_proposition_cannot_upgrade_an_earlier_verdict():
    """M5 runs last and must not overwrite what earlier stages decided."""
    ledger = ledger_for(BRIEF)
    entry = next(iter(ledger))
    entry.set_check("proposition", CheckResult(Verdict.FAIL, evidence="contradicted"))
    verify_proposition(entry, [opinion()], lambda _: reply())
    assert entry.checks["proposition"].verdict is Verdict.FAIL


# --- proposition window: prose density, not raw length -----------------------


def test_citation_sentence_is_not_a_proposition():
    """A standalone citation sentence is long but asserts nothing.

    "See SEC v. Chenery Corp., 332 U.S. 194 (1947);" is 47 characters. A raw
    length test treats it as a proposition and the model is then asked whether
    an opinion supports a string of citation furniture.
    """
    from verascite.extract import _prose_chars

    assert _prose_chars("See *SEC v. Chenery Corp.*, 332 U.S. 194 (1947);") < 20
    assert _prose_chars(
        "See id. at 197-98; Nat'l Ass'n of Home Builders v. Defenders, "
        "551 U.S. 644 (2007);"
    ) < 40
    assert _prose_chars(
        "the district court correctly held that the Secretary's position "
        "violates the Equal Protection Clause by allowing disparate treatment."
    ) > 100


def test_proposition_window_reaches_past_a_citation_sentence():
    """The claim a citation sentence supports is the sentence before it."""
    from verascite.extract import _sentence_around

    text = (
        "The Secretary's position violates the Equal Protection Clause by "
        "allowing disparate treatment of beneficiaries. "
        "See SEC v. Chenery Corp., 332 U.S. 194 (1947);"
    )
    start = text.index("332 U.S.")
    window = _sentence_around(text, start, start + len("332 U.S. 194"))
    assert "Equal Protection Clause" in window


# --- identity guard ----------------------------------------------------------


def test_proposition_not_read_when_retrieval_landed_on_another_case():
    """A wrong-case read produces a confident accusation about a good cite.

    Resolution is by volume/reporter/page, so a wrong page lands on a real but
    unrelated opinion. The model then truthfully reports that the text does not
    support the proposition, and the citation is flagged for no reason.
    """
    from verascite.verdicts import CheckResult

    ledger = ledger_for(BRIEF)
    entry = next(iter(ledger))
    entry.try_set_check(
        "case_name",
        CheckResult(
            Verdict.FAIL,
            evidence="document cites 'Doe v. GTE Corp.'; record is 'Speer v. State'.",
            sources_consulted=["courtlistener"],
        ),
    )
    called = []

    verify_proposition(
        entry,
        [opinion("Wholly unrelated text about sentencing guidelines.")],
        lambda prompt: called.append(prompt) or reply(),
    )

    assert called == [], "the model must not be asked about the wrong case"
    assert entry.checks["proposition"].verdict is Verdict.NOT_CHECKABLE


def test_proposition_is_read_when_the_name_check_passed():
    from verascite.verdicts import CheckResult

    ledger = ledger_for(BRIEF)
    entry = next(iter(ledger))
    entry.try_set_check(
        "case_name", CheckResult(Verdict.PASS, sources_consulted=["courtlistener"])
    )
    called = []
    verify_proposition(
        entry,
        [opinion()],
        lambda prompt: called.append(prompt) or reply(),
    )
    assert len(called) == 1


# --- reading scope: the page the citation names ------------------------------


def test_reading_is_scoped_to_the_cited_page_when_available():
    """A proposition is asserted about a page, not about a case.

    Handing the model the whole opinion answers a different question -- whether
    the case says this anywhere -- and an opinion of any length usually says
    something close to the claim somewhere other than where it was cited.
    """
    from verascite.verdicts import CheckResult, Verdict
    from verascite.verify_proposition import verify_proposition

    ledger = ledger_for(BRIEF)
    entry = next(iter(ledger))
    entry.citation.pin_cite = "555"
    entry.try_set_check(
        "case_name", CheckResult(Verdict.PASS, sources_consulted=["courtlistener"])
    )
    pages = {
        "554": "A" * 500,
        "555": "the text that actually sits on the cited page " * 20,
        "556": "C" * 500,
    }
    seen = []
    verify_proposition(
        entry, [opinion()], lambda prompt: seen.append(prompt) or reply(),
        page_texts=pages,
    )
    assert len(seen) == 1
    assert "actually sits on the cited page" in seen[0]
    assert "Justice Souter" not in seen[0], "the full opinion must not be sent"


def test_a_fragmentary_page_falls_back_to_the_whole_opinion():
    """Reading a running head is worse than reading the whole opinion."""
    from verascite.verdicts import CheckResult, Verdict
    from verascite.verify_proposition import verify_proposition

    ledger = ledger_for(BRIEF)
    entry = next(iter(ledger))
    entry.citation.pin_cite = "555"
    entry.try_set_check(
        "case_name", CheckResult(Verdict.PASS, sources_consulted=["courtlistener"])
    )
    seen = []
    verify_proposition(
        entry, [opinion()], lambda prompt: seen.append(prompt) or reply(),
        page_texts={"555": "550 U. S. 544"},
    )
    assert "Justice Souter" in seen[0]


def test_no_page_text_reads_the_whole_opinion():
    from verascite.verdicts import CheckResult, Verdict
    from verascite.verify_proposition import verify_proposition

    ledger = ledger_for(BRIEF)
    entry = next(iter(ledger))
    entry.try_set_check(
        "case_name", CheckResult(Verdict.PASS, sources_consulted=["courtlistener"])
    )
    seen = []
    verify_proposition(entry, [opinion()], lambda p: seen.append(p) or reply())
    assert "Justice Souter" in seen[0]
