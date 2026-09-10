"""The browser surfaces must reach the same verdict as the command line.

A citation attached to the wrong case is the second-largest defect class, and it
was under-reported in the browser: the page said "compare them yourself, this is
a prompt to look, not a finding" for `Roe v. Wade, 500 F.3d 210`, which resolves
to a real record naming *United States v. Corley*. A source was retrieved and it
contradicts the document. That is a finding, and the command line had always
called it one.

The fix was to port the matcher rather than reimplement it. These tests exist so
the two cannot drift apart again: the generated file has to still be derived
from the Python constants, and the pairs that decide a mismatch have to still
decide it the same way here.
"""

import json
import re
from pathlib import Path

import pytest

from verascite import names as N
from verascite.config import NAME_PASS_THRESHOLD, NAME_REVIEW_THRESHOLD

ROOT = Path(__file__).resolve().parent.parent
SURFACES = [ROOT / "docs" / "names.js", ROOT / "extension" / "names.js"]


@pytest.fixture(params=SURFACES, ids=lambda p: p.parent.name)
def js(request):
    if not request.param.exists():
        pytest.skip(f"{request.param} not present")
    return request.param.read_text()


def js_set(source, name):
    m = re.search(r"var " + name + r" = new Set\((\[.*?\])\);", source, re.S)
    assert m, f"{name} not found in the generated file"
    return set(json.loads(m.group(1)))


# --- the generated file must still come from the Python constants -------------

def test_generic_parties_are_in_sync(js):
    assert js_set(js, "GENERIC") == set(N.GENERIC_PARTIES)


def test_noise_tokens_are_in_sync(js):
    assert js_set(js, "NOISE") == set(N.NOISE_TOKENS)


def test_thresholds_are_in_sync(js):
    assert "var PASS = " + str(NAME_PASS_THRESHOLD) in js
    assert "var REVIEW = " + str(NAME_REVIEW_THRESHOLD) in js
    assert "var GENERIC_CEILING = " + str(N.GENERIC_ONLY_CEILING) in js


def test_the_file_says_it_is_generated(js):
    assert "generated from verascite/names.py" in js.lower()
    assert "do not hand-edit" in js.lower()


# --- the pairs that decide a finding ------------------------------------------

#: (asserted, actual, is a mismatch a reviewer should act on)
PAIRS = [
    ("Roe v. Wade", "United States v. Corley", True),      # the reported defect
    ("State v. Pune", "State v. Ing", True),
    ("People v. Smith", "People v. Rodriguez", True),
    ("Brown v. Board of Education", "Brown v. Board of Education", False),
    ("Twombly", "Bell Atlantic Corp. v. Twombly", False),
    ("Ashcroft v. Iqbal", "Iqbal", False),
    ("Ziglar v. Abbasi", "Zigler v. Abbasi", False),       # a misspelling is not a finding
    ("In re Doe", "In re Doe", False),
    ("Iko v. Shreve", "Iko v. Shreve", False),
]


@pytest.mark.parametrize("asserted,actual,is_mismatch", PAIRS)
def test_python_agrees_with_the_intended_verdict(asserted, actual, is_mismatch):
    """The reference the browser is ported from."""
    score = N.name_similarity(asserted, actual)
    if is_mismatch:
        assert score < NAME_REVIEW_THRESHOLD, f"{asserted!r} vs {actual!r} scored {score}"
    else:
        assert score >= NAME_PASS_THRESHOLD, f"{asserted!r} vs {actual!r} scored {score}"


def test_the_browser_surfaces_treat_a_mismatch_as_a_finding():
    """Not a suggestion. The wording is the whole point of the fix."""
    for surface in (ROOT / "docs" / "check.html", ROOT / "extension" / "content.js"):
        if not surface.exists():
            continue
        text = surface.read_text()
        assert "this is a finding, not a prompt" in text, surface
        assert "Different case at this citation" in text, surface
        # The softer wording must be gone.
        assert "a prompt to look, not a finding" not in text, surface


def test_the_browser_surfaces_still_never_call_absence_fabrication():
    for surface in (ROOT / "docs" / "check.html", ROOT / "extension" / "content.js"):
        if not surface.exists():
            continue
        text = surface.read_text()
        assert "does not mean the citation is fake" in text, surface
