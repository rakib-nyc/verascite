"""The notice has to appear on every surface, not most of them.

A person can meet this tool through the command line, a report handed to them by
someone else, a verification record filed with a court, a plain-language summary,
a batch review, a language model calling the server, or a browser panel. The
notice is only worth anything if it is on all of them, so this asserts it on all
of them rather than trusting that each was remembered.
"""

import json
from pathlib import Path

import pytest

from verascite import DISCLAIMER
from verascite.run_audit import audit_document

ROOT = Path(__file__).resolve().parent.parent

BRIEF = (
    'A pleading must contain more than "labels and conclusions." Bell Atlantic '
    "Corp. v. Twombly, 550 U.S. 544, 555 (2007). See also Smith v. Fictional "
    "Reporter Co., 88 Jurisprudentia 100 (9th Cir. 2018)."
)


def test_the_wording_is_what_was_asked_for():
    assert DISCLAIMER == "AI can make mistakes. For experimental and research use only."


@pytest.fixture(scope="module")
def outputs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("disclaimer")
    document = tmp / "brief.md"
    document.write_text(BRIEF)
    audit_document(document, out_dir=tmp / "out", offline=True, annotate=False,
                   plain=True, quiet=True)
    return tmp / "out"


@pytest.mark.parametrize("artifact", ["report.md", "verification-record.md",
                                      "plain-english.md"])
def test_every_written_artifact_carries_it(outputs, artifact):
    path = outputs / artifact
    assert path.exists(), f"{artifact} was not written"
    assert DISCLAIMER in path.read_text(), artifact


def test_the_batch_summary_carries_it(tmp_path):
    from verascite.batch import DocumentOutcome, render_summary
    body = render_summary([DocumentOutcome(path=tmp_path / "a.md",
                                           counts={"VERIFIED": 1}, citations=1)])
    assert DISCLAIMER in body


def test_the_server_ships_it_with_every_result():
    from verascite.mcp_server import handle
    r = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "verify_citations",
                           "arguments": {"text": BRIEF, "offline": True}}})
    payload = json.loads(r["result"]["content"][0]["text"])
    assert payload["disclaimer"] == DISCLAIMER
    assert DISCLAIMER in payload["how_to_report_this"]


def test_the_server_ships_it_at_connection_time():
    from verascite.mcp_server import handle
    r = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert DISCLAIMER in r["result"]["instructions"]


#: The browser surfaces carry the same sentence with an HTML ampersand.
BROWSER = [
    ROOT / "docs" / "check.html",
    ROOT / "docs" / "word" / "taskpane.html",
    ROOT / "extension" / "popup.html",
    ROOT / "extension" / "options.html",
]


@pytest.mark.parametrize("surface", BROWSER, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_browser_surface_carries_it(surface):
    if not surface.exists():
        pytest.skip(f"{surface} not present")
    text = surface.read_text()
    assert "AI can make mistakes" in text, surface
    assert "experimental &amp; research use only" in text, surface


# --- the token is the user's own, everywhere ---------------------------------

@pytest.mark.parametrize("surface", BROWSER + [ROOT / "docs" / "token.js",
                                               ROOT / "extension" / "options.js",
                                               ROOT / "extension" / "background.js"],
                         ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_surface_ships_a_hardcoded_credential(surface):
    """Nothing may carry a token of ours. There is no 'ours' to carry."""
    if not surface.exists():
        pytest.skip(f"{surface} not present")
    text = surface.read_text()
    import re
    # A CourtListener token is 40 hex characters.
    assert not re.search(r"\b[0-9a-f]{40}\b", text), f"a 40-hex literal appears in {surface}"


def test_the_extension_keeps_the_token_local_not_synced():
    """storage.sync would push it through an account. storage.local does not."""
    for name in ("options.js", "background.js"):
        path = ROOT / "extension" / name
        if not path.exists():
            continue
        code = "\n".join(
            line for line in path.read_text().splitlines()
            if not line.lstrip().startswith(("*", "//", "/*"))
        )
        assert "storage.sync" not in code, f"{name} reads or writes storage.sync"
        if "chrome.storage" in code:
            assert "storage.local" in code, name
