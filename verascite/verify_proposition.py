"""Stage 5: does the cited opinion support the proposition it is cited for?

This is the only component where a model makes a substantive call, and it
exists because the benchmark says it must. Content misrepresentation is 42% of
the hallucinations in LePhantomCite and the deterministic layer detects 2% of
them. No amount of string comparison reaches it: deciding whether an opinion
supports an assertion requires reading the opinion.

That makes this the most dangerous component in the system, because a verifier
that can hallucinate its own evidence is worse than no verifier. Three
constraints contain it:

**Grounded input only.** The model sees the proposition verbatim from the
document and the retrieved opinion text. It is told, in the prompt, that it may
not answer from background legal knowledge. It gets no web access and no
citation lookup.

**The verbatim-span interlock.** Every supporting finding must quote the span
of the opinion that supports it, and that span is checked programmatically
against the retrieved text. If it does not appear, the answer is discarded and
the dimension reports NOT_CHECKABLE. This makes the verifier's own output
falsifiable by string comparison rather than trusted.

**Conservative defaults.** Anything the text does not settle is NOT_SUPPORTED
at low confidence, which reports as REVIEW for a human -- not as a FAIL. A
verifier that cries wolf on defensible citations gets switched off, and then it
protects nobody.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from .clients.opinions import OpinionText
from .models import LedgerEntry
from .names import name_similarity
from .pinscope import first_page_of, passage_for_pincite
from .verdicts import CheckResult, Verdict

PROPOSITION_SOURCE = "model_grounded_reading"

#: Introductory signals and the strength of support each one claims.
SIGNAL_STRENGTH = {
    None: "the source directly states this proposition",
    "": "the source directly states this proposition",
    "see": "the proposition follows from the source by inference",
    "see also": "the source offers additional support",
    "accord": "another source states the same thing",
    "e.g.,": "the source is one example among several",
    "see, e.g.,": "the source is one example supporting the proposition",
    "cf.": "the source supports the proposition only by analogy",
    "compare": "the comparison itself makes the point",
    "but see": "the source contradicts the proposition",
    "contra": "the source directly contradicts the proposition",
    "but cf.": "the source is in analogous tension with the proposition",
    "see generally": "the source provides background",
}

VERDICTS = ("SUPPORTED", "PARTIALLY_SUPPORTED", "NOT_SUPPORTED", "CONTRADICTED")
CONFIDENCES = ("high", "medium", "low")

#: The model never sees more than this much opinion text at once.
MAX_OPINION_CHARS = 60_000


@dataclass
class PropositionQuestion:
    """Everything the model is allowed to see."""

    citation: str
    proposition: str
    signal: Optional[str]
    opinion_text: str
    opinion_descriptor: str = "opinion"

    def claimed_strength(self) -> str:
        return SIGNAL_STRENGTH.get(
            (self.signal or "").lower().strip(), "the source supports this proposition"
        )


@dataclass
class PropositionAnswer:
    verdict: str
    confidence: str
    supporting_span: Optional[str] = None
    reasoning: str = ""
    signal_mismatch: Optional[str] = None
    #: Set by the interlock, not by the model.
    span_verified: bool = False
    rejection: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in (None, "", False)}


def build_prompt(question: PropositionQuestion) -> str:
    """The grounded prompt. Deliberately narrow."""
    opinion = question.opinion_text[:MAX_OPINION_CHARS]
    return f"""\
You are checking whether a cited opinion supports a proposition asserted in a
legal brief. Answer ONLY from the opinion text provided below.

You must not use background legal knowledge. You must not rely on recognising
the case. If the provided text does not settle the question, say so — that is
a correct and useful answer, and guessing is not.

THE PROPOSITION AS THE BRIEF STATES IT (verbatim):
{question.proposition}

THE CITATION: {question.citation}
THE SIGNAL USED: {question.signal or "(none)"}
WHAT THAT SIGNAL CLAIMS: {question.claimed_strength()}

THE OPINION TEXT ({question.opinion_descriptor}):
--- BEGIN OPINION ---
{opinion}
--- END OPINION ---

Answer with a single JSON object and nothing else:

{{
  "verdict": "SUPPORTED" | "PARTIALLY_SUPPORTED" | "NOT_SUPPORTED" | "CONTRADICTED",
  "confidence": "high" | "medium" | "low",
  "supporting_span": "<a span copied EXACTLY from the opinion text above, or null>",
  "reasoning": "<two sentences maximum>",
  "signal_mismatch": "<describe any mismatch between the signal used and the
                       strength of support the opinion actually gives, or null>"
}}

Rules:
- "supporting_span" must be copied character for character from the opinion
  text above. It is checked automatically. If it does not appear there
  verbatim, your entire answer is discarded. Do not paraphrase it, do not
  join separated passages with an ellipsis, do not correct its punctuation.
- If you cannot find such a span, return null and use NOT_SUPPORTED with
  confidence "low", saying in the reasoning what is missing.
- Judge support at the strength the signal claims. An opinion that supports
  the proposition only by analogy, cited with no signal, is a mismatch.
"""


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def parse_answer(raw: str) -> PropositionAnswer:
    """Parse the model's reply. A malformed reply is a rejection, not a verdict."""
    match = _JSON_BLOCK.search(raw or "")
    if not match:
        return PropositionAnswer(
            verdict="NOT_SUPPORTED", confidence="low",
            rejection="the reply contained no JSON object",
        )
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError as exc:
        return PropositionAnswer(
            verdict="NOT_SUPPORTED", confidence="low",
            rejection=f"the reply was not valid JSON: {exc}",
        )

    verdict = str(data.get("verdict", "")).upper().strip()
    if verdict not in VERDICTS:
        return PropositionAnswer(
            verdict="NOT_SUPPORTED", confidence="low",
            rejection=f"unrecognised verdict {verdict!r}",
        )
    confidence = str(data.get("confidence", "low")).lower().strip()
    if confidence not in CONFIDENCES:
        confidence = "low"

    span = data.get("supporting_span")
    return PropositionAnswer(
        verdict=verdict,
        confidence=confidence,
        supporting_span=span if isinstance(span, str) and span.strip() else None,
        reasoning=str(data.get("reasoning") or "")[:400],
        signal_mismatch=(
            str(data["signal_mismatch"])[:300]
            if data.get("signal_mismatch") not in (None, "", "null")
            else None
        ),
    )


def _flatten(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def enforce_span_interlock(answer: PropositionAnswer, opinion_text: str) -> PropositionAnswer:
    """The anti-hallucination interlock.

    A finding of support must quote the opinion, and the quote must really be
    in the opinion. Whitespace is normalised, because line breaks are an
    artefact of retrieval; nothing else is. An ellipsis is rejected outright --
    joining two separated passages can manufacture support that the opinion
    does not give.

    Only whitespace is forgiven deliberately. Casefolding is allowed because
    it cannot change which words are present.
    """
    if answer.rejection:
        return answer

    positive = answer.verdict in ("SUPPORTED", "PARTIALLY_SUPPORTED", "CONTRADICTED")
    if not positive:
        answer.span_verified = answer.supporting_span is None
        return answer

    if not answer.supporting_span:
        answer.rejection = (
            f"a verdict of {answer.verdict} requires a supporting span and none was given"
        )
        return answer

    if re.search(r"\.\s*\.\s*\.|…", answer.supporting_span):
        answer.rejection = (
            "the supporting span contains an ellipsis; separated passages may not "
            "be joined, because doing so can manufacture support the opinion does "
            "not give"
        )
        return answer

    if _flatten(answer.supporting_span) not in _flatten(opinion_text):
        answer.rejection = (
            "the supporting span does not appear in the opinion text that was "
            "provided; the answer is discarded"
        )
        return answer

    answer.span_verified = True
    return answer


def ask_and_verify(
    question: PropositionQuestion, ask: Callable[[str], str]
) -> PropositionAnswer:
    """Put the question to a model and run the interlock over its answer."""
    try:
        raw = ask(build_prompt(question))
    except Exception as exc:  # pragma: no cover - defensive
        return PropositionAnswer(
            verdict="NOT_SUPPORTED", confidence="low",
            rejection=f"the model call failed: {exc}",
        )
    return enforce_span_interlock(parse_answer(raw), question.opinion_text)


#: A citation is resolved by volume/reporter/page. When the page is wrong or
#: the reporter is misread, that lands on a real opinion that is the wrong
#: case. Reading a proposition against the wrong opinion is worse than not
#: reading it: the model correctly reports that the text does not support the
#: claim, and the output is a confident accusation about a citation that is
#: fine. The case-name check has already compared the name the citation gives
#: against the name of what came back, so its verdict decides this.
_IDENTITY_BLOCKING = (Verdict.FAIL, Verdict.AMBIGUOUS)

#: Below this the reconstructed page is a fragment -- a running head, a page
#: that is mostly a table -- and reading it would be worse than reading the
#: whole opinion.
_MIN_PAGE_CHARS = 400


def _identity_agrees(entry: LedgerEntry) -> bool:
    """Whether the retrieved opinion is the case the citation actually names."""
    name_check = entry.checks.get("case_name") if entry.checks else None
    if name_check is not None:
        return name_check.verdict not in _IDENTITY_BLOCKING
    claimed = (entry.citation.case_name or "").strip()
    resolution = entry.resolved_to
    if not claimed or resolution is None:
        return True  # nothing to compare: leave the existing behaviour alone
    candidates = [
        getattr(resolution, attr, None)
        for attr in ("case_name", "case_name_short", "case_name_full")
    ]
    names = [c for c in candidates if c]
    if not names:
        return True
    return max(name_similarity(claimed, n) for n in names) >= 0.34


def _reading_scope(
    entry: LedgerEntry, source: "OpinionText", page_texts: Optional[dict]
) -> tuple[str, str]:
    """The text to read, scoped to the cited page when one is available.

    A proposition is asserted about a page, not about a case. Handing the model
    the whole opinion answers a different question -- whether the case says
    this anywhere -- and an opinion of any length usually says something close
    to the claim somewhere other than where it was cited. Scoping to the page
    the citation names is what makes the answer about the citation.

    Falls back to the full text whenever the page cannot be isolated, because
    reading the whole opinion is worse than reading the right page but far
    better than reading nothing.
    """
    if page_texts:
        window = passage_for_pincite(page_texts, entry.citation.pin_cite)
        if len(window) >= _MIN_PAGE_CHARS:
            page = first_page_of(entry.citation.pin_cite)
            return window, f"{source.descriptor}, at page {page}"
    return source.text, source.descriptor


def verify_proposition(
    entry: LedgerEntry,
    opinions: list[OpinionText],
    ask: Optional[Callable[[str], str]] = None,
    page_texts: Optional[dict] = None,
) -> None:
    """Set the ``proposition`` dimension on one ledger entry."""
    proposition = (entry.citation.sentence or "").strip()

    if ask is None:
        entry.try_set_check(
            "proposition",
            CheckResult(
                Verdict.NOT_CHECKABLE,
                reason=(
                    "proposition support was not checked: this run had no model "
                    "available for grounded reading"
                ),
            ),
        )
        return

    if not proposition or len(proposition) < 40:
        entry.try_set_check(
            "proposition",
            CheckResult(
                Verdict.NA,
                reason="no assertion of substance accompanies this citation",
            ),
        )
        return

    holding = [o for o in opinions if o.is_court_holding and not o.is_syllabus]
    source = holding[0] if holding else (opinions[0] if opinions else None)
    if source is None:
        entry.try_set_check(
            "proposition",
            CheckResult(
                Verdict.NOT_CHECKABLE,
                reason=(
                    "no opinion text was retrieved, so whether the authority "
                    "supports this proposition could not be read"
                ),
                sources_consulted=[PROPOSITION_SOURCE],
            ),
        )
        return

    if not _identity_agrees(entry):
        entry.try_set_check(
            "proposition",
            CheckResult(
                Verdict.NOT_CHECKABLE,
                reason=(
                    "the text retrieved for this citation is a different "
                    "case from the one the citation names. Reading it would "
                    "test the proposition against the wrong opinion, so it "
                    "was not read."
                ),
                sources_consulted=[PROPOSITION_SOURCE],
            ),
        )
        return

    opinion_text, descriptor = _reading_scope(entry, source, page_texts)

    answer = ask_and_verify(
        PropositionQuestion(
            citation=entry.citation.raw_text,
            proposition=proposition,
            signal=entry.citation.signal,
            opinion_text=opinion_text,
            opinion_descriptor=descriptor,
        ),
        ask,
    )
    entry.try_set_check("proposition", _to_check(answer, source))


def _to_check(answer: PropositionAnswer, source: OpinionText) -> CheckResult:
    detail = {"model_answer": answer.to_dict(), "opinion": source.descriptor}

    if answer.rejection:
        return CheckResult(
            Verdict.NOT_CHECKABLE,
            reason=(
                f"the grounded reading was discarded: {answer.rejection}. No "
                "conclusion is drawn from a discarded answer."
            ),
            sources_consulted=[PROPOSITION_SOURCE],
            detail=detail,
        )

    if answer.verdict == "CONTRADICTED":
        return CheckResult(
            Verdict.FAIL,
            evidence=(
                f"the cited {source.descriptor} contradicts this proposition. "
                f"{answer.reasoning} The opinion says: "
                f"{' '.join((answer.supporting_span or '').split())!r}"
            ),
            confidence=answer.confidence,
            sources_consulted=[PROPOSITION_SOURCE],
            detail=detail,
        )

    if answer.verdict == "SUPPORTED" and answer.confidence == "high":
        if answer.signal_mismatch:
            return CheckResult(
                Verdict.NOT_CHECKABLE,
                reason=(
                    f"the opinion supports this proposition, but not at the "
                    f"strength the citation claims: {answer.signal_mismatch}"
                ),
                confidence=answer.confidence,
                sources_consulted=[PROPOSITION_SOURCE],
                detail=detail,
            )
        return CheckResult(
            Verdict.PASS,
            evidence=(
                f"supported by the cited {source.descriptor}: "
                f"{' '.join((answer.supporting_span or '').split())!r}"
            ),
            confidence=answer.confidence,
            sources_consulted=[PROPOSITION_SOURCE],
            detail=detail,
        )

    # Everything else -- partial support, low-confidence support, an
    # unsupported reading -- is a human's call, not a finding against the
    # brief. Reporting these as FAIL is how a verifier starts crying wolf.
    return CheckResult(
        Verdict.NOT_CHECKABLE,
        reason=(
            f"the grounded reading returned {answer.verdict} at {answer.confidence} "
            f"confidence. {answer.reasoning} Read the cited passage and decide."
        ),
        confidence=answer.confidence,
        sources_consulted=[PROPOSITION_SOURCE],
        detail=detail,
    )
