

def test_a_pincite_range_names_every_page_in_it():
    """"at 459-61" cites 459 through 461, so 461 is a cited page.

    Reading only the first number reports a page the citation actually names
    as a page it does not, which is a false accusation against a correct
    pincite.
    """
    from verascite.verify_pincite import _pincite_pages

    class _Cite:
        def __init__(self, pin):
            self.pin_cite, self.page = pin, None

    class _Entry:
        def __init__(self, pin):
            self.citation = _Cite(pin)

    assert _pincite_pages(_Entry("459-61")) == set(range(459, 462))
    assert _pincite_pages(_Entry("96-98")) == {96, 97, 98}
    assert _pincite_pages(_Entry("1075-1076")) == {1075, 1076}
    assert _pincite_pages(_Entry("555")) == {555}
    # "n.12" is a footnote number, not a second page.
    assert _pincite_pages(_Entry("852 n.12")) == {852}
    # A range that would span an implausible number of pages is not a range.
    assert 900 not in _pincite_pages(_Entry("100-900"))
