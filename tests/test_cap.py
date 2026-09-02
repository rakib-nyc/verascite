"""Caselaw Access Project client: resolving a page to the case that holds it."""

from verascite.clients.cap import CapCase, CapClient


def _case(**kwargs) -> CapCase:
    base = dict(
        cite="552 F.3d 305", name="In re Hydrogen Peroxide Antitrust Litigation",
        name_abbreviation="In re Hydrogen Peroxide", decision_date="2008-12-30",
        court="Court of Appeals", court_abbreviation="3d Cir.",
        first_page="305", last_page="327", file_name="0305-01",
        reporter_slug="f3d", volume="552",
    )
    base.update(kwargs)
    return CapCase(**base)


def test_page_bounds_may_arrive_as_strings():
    """CAP records the bounds as strings in some volumes, integers in others."""
    assert _case().covers_page(309) is True
    assert _case().covers_page(400) is False
    assert _case(first_page=305, last_page=327).covers_page(309) is True
    assert _case(last_page=None).covers_page(309) is None


def test_containment_is_refused_for_a_full_citation():
    """A page always falls inside some case, so containment must be gated.

    Applied to a full citation, containment would return a case for a citation
    that does not exist, and offer it as evidence that the case is real -- the
    exact inversion P1 forbids. It is allowed only where the caller knows the
    page is a pincite: an unresolved short form.
    """
    records = [
        {
            "first_page": "305", "last_page": "327", "file_name": "0305-01",
            "name_abbreviation": "In re Hydrogen Peroxide", "citations": [],
            "decision_date": "2008-12-30",
        }
    ]
    client = CapClient(cache_dir=None)
    client.volume_cases = lambda slug, volume: records
    client.reporter_slug = lambda name: "f3d"

    assert client.find("552", "F.3d", "309") is None

    found = client.find("552", "F.3d", "309", page_is_pincite=True)
    assert found is not None
    assert found.name_abbreviation == "In re Hydrogen Peroxide"


def test_the_first_page_still_wins_when_it_matches():
    """Containment never displaces an exact match on the page a case begins on."""
    records = [
        {"first_page": "300", "last_page": "340", "file_name": "0300-01",
         "name_abbreviation": "Earlier Case", "citations": [],
         "decision_date": "2008-01-01"},
        {"first_page": "309", "last_page": "350", "file_name": "0309-01",
         "name_abbreviation": "Case Beginning At 309", "citations": [],
         "decision_date": "2008-06-01"},
    ]
    client = CapClient(cache_dir=None)
    client.volume_cases = lambda slug, volume: records
    client.reporter_slug = lambda name: "f3d"

    for pincite in (False, True):
        found = client.find("552", "F.3d", "309", page_is_pincite=pincite)
        assert found is not None
        assert found.name_abbreviation == "Case Beginning At 309"


def test_parallel_citations_skip_vendor_neutral_identifiers():
    """LEXIS and WL numbers cannot reach text: no free archive holds them."""
    from verascite.verify_existence import _parallel_citations

    cluster = {
        "citations": [
            {"volume": "581", "reporter": "U.S.", "page": "1"},
            {"volume": "2017", "reporter": "U.S. LEXIS", "page": "2185"},
            {"volume": "137", "reporter": "S. Ct.", "page": "1039"},
            {"volume": None, "reporter": "F.3d", "page": "12"},
        ]
    }
    assert _parallel_citations(cluster) == ["581 U.S. 1", "137 S. Ct. 1039"]
