"""The free federal statutory sources, and the line between the three states.

The whole point of this client is that ``ABSENT`` and ``UNVERIFIED`` are
different things. Confusing them produces the exact failure this project exists
to prevent: a confident report that a real provision does not exist. Most of
these tests exist to hold that line, and the sharpest one is the eCFR
full-text 404, which means two incompatible things at once.
"""

import json

import pytest
import requests

from verascite.clients.statutes import (
    ABSENT,
    ECFR_EPOCH,
    EXISTS,
    UNVERIFIED,
    StatuteClient,
    _cfr_designators,
    _path_present,
    _release_point,
)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.headers = headers or {}
        self.content = text.encode()

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Serves canned responses in order, recording what was asked for."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None, allow_redirects=True, headers=None):
        self.calls.append({"url": url, "params": params,
                           "allow_redirects": allow_redirects})
        if not self.responses:
            raise AssertionError(f"unexpected request to {url}")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def client(*responses, **kw):
    return StatuteClient(session=FakeSession(*responses), **kw)


# --- the link service ---------------------------------------------------------

def test_a_redirect_means_the_provision_exists():
    result = client(FakeResponse(302, headers={"Location": "https://x/doc.pdf"})
                    ).usc_section("42", "1983")
    assert result.state == EXISTS
    assert result.sources_consulted == ["govinfo_link_service"]


def test_a_400_is_an_affirmative_report_of_absence():
    result = client(FakeResponse(400)).usc_section("42", "999999")
    assert result.state == ABSENT
    assert "affirmative" in result.detail


def test_redirects_are_not_followed():
    """The status is the answer; following it replaces a signal with noise."""
    session = FakeSession(FakeResponse(302, headers={"Location": "x"}))
    StatuteClient(session=session).usc_section("42", "1983")
    assert session.calls[0]["allow_redirects"] is False


@pytest.mark.parametrize("status", [403, 429, 500, 502, 503])
def test_any_other_status_is_unverified(status):
    result = client(FakeResponse(status)).usc_section("42", "1983")
    assert result.state == UNVERIFIED


def test_a_connection_failure_is_unverified_not_absent():
    result = client(requests.ConnectionError("down")).usc_section("42", "1983")
    assert result.state == UNVERIFIED
    assert "unreachable" in result.detail


def test_a_timeout_is_unverified():
    result = client(requests.Timeout("slow")).public_law("116", "136")
    assert result.state == UNVERIFIED


# --- the regulation existence oracle -----------------------------------------

def test_versions_with_results_means_the_section_exists():
    payload = {"content_versions": [{"date": "2017-01-01"}, {"date": "2019-06-01"}]}
    result = client(FakeResponse(200, payload)).cfr_section("29", "1910", "1910.132")
    assert result.state == EXISTS
    assert result.as_of == "2019-06-01"


def test_versions_with_an_empty_result_is_absence():
    """A successful answer carrying nothing is a report, not a failure."""
    payload = {"content_versions": [], "meta": {"result_count": "0"}}
    # The empty answer is authoritative, so no fallback request is made.
    result = client(FakeResponse(200, payload)).cfr_section("29", "1910", "1910.99999")
    assert result.state == ABSENT


def test_a_non_200_from_versions_falls_back_and_stays_unverified_if_that_fails():
    result = client(FakeResponse(503), FakeResponse(503)
                    ).cfr_section("29", "1910", "1910.132")
    assert result.state == UNVERIFIED


def test_an_unreadable_body_is_unverified():
    result = client(FakeResponse(200, None, text="<html>"), FakeResponse(503)
                    ).cfr_section("29", "1910", "1910.132")
    assert result.state == UNVERIFIED


# --- the trap ------------------------------------------------------------------

def test_a_full_text_404_never_becomes_absence():
    """eCFR returns the same 404 for a fake section and an out-of-range date.

    Reading it as non-existence would report every genuine pre-2017 regulatory
    citation as absent. This is the single most important test in the file.
    """
    result = client(FakeResponse(404)).cfr_subsection("29", "1910", "1910.132", ["d"])
    assert result.state == UNVERIFIED
    assert result.state != ABSENT
    assert "outside its recorded history" in result.detail


def test_a_regulation_paragraph_that_is_missing_is_still_not_absence():
    """Flat text can hide a designator, so absence here proves nothing."""
    xml = "<DIV8><P>(a) Application.</P><P>(b) Something else.</P></DIV8>"
    result = client(FakeResponse(200, text=xml)
                    ).cfr_subsection("29", "1910", "1910.132", ["z", "9"])
    assert result.state == UNVERIFIED
    assert "not evidence the paragraph does not exist" in result.detail


def test_a_located_regulation_paragraph_is_reported_at_reduced_confidence():
    xml = "<DIV8><P>(a) Application.</P><P>(d)(1) Selection of equipment.</P></DIV8>"
    result = client(FakeResponse(200, text=xml)
                    ).cfr_subsection("29", "1910", "1910.132", ["d", "1"])
    assert result.state == EXISTS
    assert result.confidence == "medium", "flat text cannot support high confidence"


# --- US Code subsections, the structural source -------------------------------

USLM = '''<?xml version="1.0"?>
<uscDoc><meta><docPublicationName>Online@119-103</docPublicationName></meta>
<section identifier="/us/usc/t42/s1983"><content><p>Every person who...</p></content></section>
<section identifier="/us/usc/t42/s12112">
  <subsection identifier="/us/usc/t42/s12112/b">
    <paragraph identifier="/us/usc/t42/s12112/b/3">
      <subparagraph identifier="/us/usc/t42/s12112/b/3/A"/>
    </paragraph>
  </subsection>
</section></uscDoc>'''


@pytest.fixture
def with_title(tmp_path):
    directory = tmp_path / "uscode-titles"
    directory.mkdir(parents=True)
    (directory / "usc42@119-103.xml").write_text(USLM)
    return StatuteClient(cache_dir=tmp_path, session=FakeSession())


def test_a_real_subsection_is_found(with_title):
    result = with_title.usc_subsection("42", "12112", ["b", "3", "A"])
    assert result.state == EXISTS
    assert result.as_of == "Online@119-103"


def test_a_fabricated_subsection_of_a_real_section_is_absent(with_title):
    """The check this module exists for. Section 1983 has no subsections."""
    result = with_title.usc_subsection("42", "1983", ["a", "2"])
    assert result.state == ABSENT
    assert "119-103" in result.detail


def test_a_subsection_of_a_missing_section_is_not_reported_as_absent(with_title):
    """One defect must not be counted twice."""
    result = with_title.usc_subsection("42", "999999", ["a"])
    assert result.state == UNVERIFIED


def test_without_the_structural_text_the_answer_is_unverified(tmp_path):
    result = StatuteClient(cache_dir=tmp_path, session=FakeSession()
                           ).usc_subsection("42", "1983", ["a"])
    assert result.state == UNVERIFIED
    assert "not available locally" in result.detail


def test_a_title_is_never_downloaded_implicitly(tmp_path):
    """An 18MB transfer must be asked for, never assumed."""
    session = FakeSession()  # any request would raise
    StatuteClient(cache_dir=tmp_path, session=session).usc_subsection("42", "1983", ["a"])
    assert session.calls == []


def test_the_release_point_is_read_from_the_artefact():
    assert _release_point(USLM) == "Online@119-103"


def test_an_artefact_without_a_release_point_reports_none():
    assert _release_point("<uscDoc/>") == ""


# --- helpers -------------------------------------------------------------------

def test_designators_are_read_through_markup():
    assert _cfr_designators("<P>(a) text</P><P>(b)(1) more</P>") == ["a", "b", "1"]


def test_a_path_must_appear_in_order():
    assert _path_present(["a", "b", "1"], ["b", "1"])
    assert not _path_present(["1", "b"], ["b", "1"])


def test_an_empty_subsection_request_is_unverified():
    assert client().usc_subsection("42", "1983", []).state == UNVERIFIED


# --- every result names its sources -------------------------------------------

def test_every_result_names_what_was_consulted():
    for result in (
        client(FakeResponse(302, headers={"Location": "x"})).usc_section("42", "1983"),
        client(FakeResponse(400)).usc_section("42", "999999"),
        client(requests.ConnectionError("x")).statute_at_large("134", "281"),
        client(FakeResponse(200, {"content_versions": []})).cfr_section("29", "1910", "1910.1"),
    ):
        assert result.sources_consulted, f"no sources named on {result.state}"


def test_no_credential_is_ever_sent(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVINFO_API_KEY", "should-never-be-used")
    session = FakeSession(FakeResponse(302, headers={"Location": "x"}))
    StatuteClient(session=session).usc_section("42", "1983")
    blob = json.dumps(session.calls) + json.dumps(dict(session.headers))
    assert "should-never-be-used" not in blob
    assert "api_key" not in blob.lower()
