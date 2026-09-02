"""A fixed corpus with hand-checked expected verdicts.

Every threshold in verascite/config.py is a tunable number sitting inside a
layer the spec requires to be bit-identical across runs. Tuning one while
building a later milestone would silently rewrite verdicts an earlier one
rendered. This corpus pins them: each citation below has an expected verdict
that was reasoned about, not recorded, and any threshold change that moves one
fails the suite instead of passing quietly.
"""

from __future__ import annotations

from verascite.clients.opinions import OpinionText, PageMark

BRIEF = '''\
# BRIEF

Qualified immunity shields officials from liability. Harlow v. Fitzgerald, 457
U.S. 800, 818 (1982). The Court reaffirmed this in Pearson v. Callahan, 555
U.S. 223 (2009). Id. at 236.

A complaint must plead facts. The Court held that "a formulaic recitation of
the elements of a cause of action will not do." Bell Atlantic Corp. v.
Twombly, 550 U.S. 544, 555 (2007).

Defendants insist that "a formulaic recitation of the elements of a cause of
action will not suffice." Twombly, 550 U.S. at 555.

Plaintiff asserts that "the pleading standard demands evidentiary proof at the
very outset of litigation." Twombly, 550 U.S. 544 (2007).

The Court observed that "the transparent policy concern that drives the
decision is the interest in protecting antitrust defendants." Twombly, 550
U.S. 544 (2007).

Accord Bogus v. Nobody, 33 Umbrella 422, 425 (2020).

See Tinch v. Video Indus. Servs., Inc., 2019 WL 1396975 (E.D. Mich. 2019).

See also 42 U.S.C. Section 1983.
'''

TWOMBLY_MAJORITY = (
    "Justice Souter delivered the opinion of the Court. Federal Rule of Civil "
    "Procedure 8(a)(2) requires only a short and plain statement of the claim "
    "showing that the pleader is entitled to relief. While a complaint attacked "
    "by a Rule 12(b)(6) motion to dismiss does not need detailed factual "
    "allegations, a plaintiff's obligation to provide the grounds of his "
    "entitlement to relief requires more than labels and conclusions, and a "
    "formulaic recitation of the elements of a cause of action will not do, see "
    "Papasan v. Allain, 478 U.S. 265, 286 (1986). Factual allegations must be "
    "enough to raise a right to relief above the speculative level. "
    + "The Court has said as much before in a variety of settings. " * 30
    + "The judgment of the Court of Appeals is reversed, and the cause is "
    "remanded for further proceedings consistent with this opinion. It is so "
    "ordered."
)

TWOMBLY_DISSENT = (
    "Justice Stevens, with whom Justice Ginsburg joins, dissenting. In the first "
    "paragraph of its opinion the Court states the case. "
    + "The pleading rules were not adopted to be a screening device. " * 30
    + "The transparent policy concern that drives the decision is the interest "
    "in protecting antitrust defendants from the burdens of discovery. "
    + "Accordingly I respectfully dissent. The judgment should be affirmed."
)


def twombly_opinions() -> list[OpinionText]:
    """The cluster as the pipeline would see it: majority plus dissent."""
    return [
        OpinionText(
            opinion_id=9435068,
            text=TWOMBLY_MAJORITY,
            source_field="xml_harvard",
            type_code="020lead",
            type_label="majority opinion",
            is_court_holding=True,
            author="Souter",
            page_marks=[PageMark("555", 0, 1), PageMark("556", 400, 1), PageMark("557", 900, 1)],
            complete=True,
            completeness_reason="fixed corpus: treated as complete",
        ),
        OpinionText(
            opinion_id=9435069,
            text=TWOMBLY_DISSENT,
            source_field="xml_harvard",
            type_code="040dissent",
            type_label="dissent",
            is_court_holding=False,
            author="Stevens",
            page_marks=[PageMark("571", 0, 1)],
            complete=True,
            completeness_reason="fixed corpus: treated as complete",
        ),
    ]


#: normalized citation -> canned citation-lookup response
LOOKUPS = {
    "457 U.S. 800": {
        "status": 200,
        "clusters": [{"id": 110763, "case_name": "Harlow v. Fitzgerald",
                      "date_filed": "1982-06-24", "precedential_status": "Published",
                      "absolute_url": "/opinion/110763/harlow-v-fitzgerald/",
                      "court_id": "scotus"}],
    },
    "555 U.S. 223": {
        "status": 200,
        "clusters": [{"id": 145918, "case_name": "Pearson v. Callahan",
                      "date_filed": "2009-01-21", "precedential_status": "Published",
                      "absolute_url": "/opinion/145918/pearson-v-callahan/",
                      "court_id": "scotus"}],
    },
    "550 U.S. 544": {
        "status": 200,
        "clusters": [{"id": 145730, "case_name": "Bell Atlantic Corp. v. Twombly",
                      "date_filed": "2007-05-21", "precedential_status": "Published",
                      "absolute_url": "/opinion/145730/bell-atlantic-corp-v-twombly/",
                      "court_id": "scotus"}],
    },
}

#: citation_id -> (overall, {dimension: verdict}) -- the locked expectations.
#: Each is a statement about what the tool *should* say, checked by hand.
EXPECTED = {
    # Real, correctly cited, no quote attached.
    # No quotation attached, so the quote dimension is absent rather than
    # invented -- dimensions that do not apply are not reported.
    "cite_0000": ("VERIFIED", {"existence": "PASS", "case_name": "PASS",
                               "court": "PASS", "year": "PASS"}),
    "cite_0001": ("VERIFIED", {"existence": "PASS", "case_name": "PASS",
                               "court": "PASS", "year": "PASS"}),
    # "Id." inherits Pearson.
    "cite_0002": ("VERIFIED", {"existence": "PASS", "court": "N/A", "year": "N/A"}),
    # Quoted exactly as the majority wrote it.
    "cite_0003": ("VERIFIED", {"existence": "PASS", "quote": "PASS",
                               "quote_source": "PASS"}),
    # One word swapped: "do" -> "suffice". Must be an alteration, not an absence.
    "cite_0004": ("FLAGGED", {"existence": "PASS", "quote": "FAIL"}),
    # Language that is nowhere in the retrieved opinion. Reported for review,
    # not as a finding: measured on LePhantomCite, "this quotation is not in
    # the text" produced 45 false alarms, because a quotation that cannot be
    # located may be misquoted, or attributed to the wrong citation, or absent
    # from the text that was retrievable. See V7_PROTOCOL.md.
    "cite_0005": ("REVIEW", {"existence": "PASS", "quote": "NOT_CHECKABLE"}),
    # Real language, but from the dissent, cited as the Court's.
    "cite_0006": ("FLAGGED", {"existence": "PASS", "quote": "PASS",
                              "quote_source": "FAIL"}),
    # Invented reporter: reporters-db has no "Umbrella".
    "cite_0007": ("FLAGGED", {"reporter_valid": "FAIL", "existence": "NOT_CHECKABLE"}),
    # Westlaw identifier: a known coverage gap, never a fabrication finding.
    "cite_0008": ("UNVERIFIED", {"existence": "OUT_OF_SCOPE", "reporter_valid": "PASS"}),
    # Statute: routed away from the case-reporter database entirely.
    "cite_0009": ("UNVERIFIED", {"existence": "OUT_OF_SCOPE", "reporter_valid": "N/A"}),
}
