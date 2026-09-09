"""Reading a statutory or regulatory citation into the parts a lookup needs.

Until now every statutory citation in a document was `OUT_OF_SCOPE` -- seen,
counted, and then set aside. That is a real gap rather than a cosmetic one. A
fabricated subsection of a real statute is as damaging in a brief as a
fabricated case, and it is harder to spot by eye: the title is right, the
section is right, and only the parenthetical after them was invented.

**What this module does and does not decide.** It parses. It converts the
citation as written into a title, a section, and the path of subsection
designators the citation claims. It renders no verdict; ``verify_statute``
does that, and the separation is deliberate, because the parse is the part
that must be conservative. A parse that guesses wrong sends a lookup after the
wrong provision, and a lookup that lands on the wrong provision produces a
confident finding against a citation that was fine.

**Where the parse comes from.** ``eyecite`` supplies most of it, and the
awkward parts of its output are handled here rather than worked around at
every call site:

- A C.F.R. title arrives in ``chapter``, not ``title``. The same field name
  means different things for different codes.
- A subsection arrives as an unparsed string in ``pin_cite`` -- ``'(b)(3)(A)'``
  -- which is exactly the fabrication target and therefore exactly the thing
  that must be turned into a path rather than compared as text.
- A section *range* arrives as one string, ``'1983-1985'``, and a section
  *list* silently loses everything after the first section. Both are refused
  here rather than checked, because checking the first of several sections and
  reporting a result for the whole citation is worse than declining: it tells
  the reader the citation was examined when most of it was not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .models import CiteKind, RawCitation

#: The codes this module can take apart. A citation to anything else is parsed
#: as far as its code and then declines, so the report can say which authority
#: type it was rather than only that it was unrecognised.
USC = "usc"
CFR = "cfr"
PUBLIC_LAW = "public_law"
STATUTES_AT_LARGE = "statutes_at_large"

#: Reporter abbreviations, folded to a code. Written out rather than
#: normalised by rule because the variants are few and a rule that turned
#: "U.S." into "U.S.C." would be a catastrophe rather than a bug.
_CODE_BY_REPORTER = {
    "u.s.c.": USC, "usc": USC, "u.s.c.a.": USC, "u.s.c.s.": USC,
    "c.f.r.": CFR, "cfr": CFR,
    "pub. l.": PUBLIC_LAW, "pub.l.": PUBLIC_LAW, "pub. l. no.": PUBLIC_LAW,
    "p.l.": PUBLIC_LAW,
    "stat.": STATUTES_AT_LARGE,
}

#: One subsection designator: (a), (12), (iv), (A). Anything else -- a range, a
#: comma, prose -- ends the path, and the remainder is recorded as unparsed so
#: the caller can decline rather than check half a citation.
_DESIGNATOR = re.compile(r"\(\s*([A-Za-z0-9]{1,6})\s*\)")

#: A pincite that is not a subsection path at all.
_NOT_A_PATH = re.compile(r"et\s+seq|note|app|&|,|--|\bto\b|–|—", re.IGNORECASE)

#: A section naming more than one provision. eyecite hands ranges over intact
#: and drops list members, so both are detected from the section string.
_SECTION_IS_PLURAL = re.compile(r"[-–—,;]|\bto\b|et\s+seq", re.IGNORECASE)

#: A CFR section is part.section: 1910.132 -> part 1910, section 1910.132.
_CFR_SECTION = re.compile(r"^(\d+[A-Za-z]?)\.(.+)$")

#: A public law is congress-number: 116-136.
_PUBLIC_LAW = re.compile(r"^(\d{1,3})\s*[-–]\s*(\d{1,4})$")


@dataclass
class StatuteRef:
    """A statutory citation broken into the parts a source lookup needs."""

    code: str
    raw_text: str
    #: US Code / CFR title number, as written.
    title: Optional[str] = None
    #: Section as written: "1983", or "1910.132" for a regulation.
    section: Optional[str] = None
    #: CFR part, derived from the section. None for anything else.
    part: Optional[str] = None
    #: Subsection designators in order: "(b)(3)(A)" -> ["b", "3", "A"].
    subsection: list[str] = field(default_factory=list)
    #: Congress and law number for a public law.
    congress: Optional[str] = None
    law_number: Optional[str] = None
    #: Volume and page for Statutes at Large.
    volume: Optional[str] = None
    page: Optional[str] = None
    #: Year the citation itself claims, when it gives one.
    year: Optional[str] = None
    #: Why this citation cannot be checked as a single provision, if it cannot.
    declined: Optional[str] = None

    @property
    def checkable(self) -> bool:
        return self.declined is None

    def describe(self) -> str:
        """How the provision is named in a report."""
        if self.code == USC:
            base = f"{self.title} U.S.C. § {self.section}"
        elif self.code == CFR:
            base = f"{self.title} C.F.R. § {self.section}"
        elif self.code == PUBLIC_LAW:
            return f"Public Law {self.congress}-{self.law_number}"
        elif self.code == STATUTES_AT_LARGE:
            return f"{self.volume} Stat. {self.page}"
        else:
            return self.raw_text
        return base + "".join(f"({d})" for d in self.subsection)

    def to_dict(self) -> dict:
        return {
            k: v for k, v in self.__dict__.items() if v not in (None, [], "")
        }


def parse_subsection(pin_cite: Optional[str]) -> tuple[list[str], Optional[str]]:
    """Turn a pincite into a subsection path, or explain why it is not one.

    Returns ``(path, declined_reason)``. A pincite that names a range, a list,
    a note, or ``et seq.`` is not a single provision, and this returns an empty
    path with a reason rather than the first designator it can find. Taking the
    first would check ``(b)`` and report the answer for ``(b)(3), (c)`` -- a
    result about a provision the document did not cite.
    """
    text = (pin_cite or "").strip()
    if not text:
        return [], None
    if _NOT_A_PATH.search(text):
        return [], (
            f"the citation names more than one provision or a note ({text!r}), "
            "so it was not checked as a single subsection"
        )
    path = _DESIGNATOR.findall(text)
    if not path:
        return [], (
            f"the pincite {text!r} is not a subsection designator, so no "
            "subsection was checked"
        )
    # Anything left over after removing the designators means the pincite was
    # a designator plus something else, and the something else may change what
    # is being claimed.
    remainder = _DESIGNATOR.sub("", text).strip(" .")
    if remainder:
        return [], (
            f"the pincite {text!r} carries text beyond its subsection "
            "designators, so it was not checked as a single subsection"
        )
    return path, None


def parse_statute(cite: RawCitation) -> Optional[StatuteRef]:
    """Read a statutory citation into its parts, or None if it is not one."""
    if cite.kind is not CiteKind.LAW:
        return None
    reporter = (cite.reporter or "").strip().lower()
    code = _CODE_BY_REPORTER.get(reporter)
    if code is None:
        # Reached by state codes and by compilations reporters-db knows but
        # this module has no source for. Recorded as a parse failure so the
        # report can name the authority type rather than say nothing.
        return StatuteRef(
            code="other", raw_text=cite.raw_text,
            declined=(
                f"{cite.reporter or 'this authority type'} is not among the "
                "codes this tool can verify"
            ),
        )

    if code == STATUTES_AT_LARGE:
        return StatuteRef(code=code, raw_text=cite.raw_text,
                          volume=cite.volume, page=cite.page, year=cite.year)

    if code == PUBLIC_LAW:
        # eyecite puts "116-136" in title, not in section.
        match = _PUBLIC_LAW.match((cite.title or "").strip())
        if not match:
            return StatuteRef(
                code=code, raw_text=cite.raw_text,
                declined="the public law number could not be read from the citation",
            )
        return StatuteRef(code=code, raw_text=cite.raw_text, year=cite.year,
                          congress=match.group(1), law_number=match.group(2))

    section = (cite.section or "").strip()
    if not section:
        return StatuteRef(code=code, raw_text=cite.raw_text, title=cite.title,
                          declined="the citation names no section")
    if _SECTION_IS_PLURAL.search(section):
        return StatuteRef(
            code=code, raw_text=cite.raw_text, title=cite.title, section=section,
            declined=(
                f"the citation names a range or list of sections ({section!r}). "
                "Checking the first alone would report a result for provisions "
                "that were never looked at"
            ),
        )

    subsection, declined = parse_subsection(cite.pin_cite)
    ref = StatuteRef(code=code, raw_text=cite.raw_text, title=cite.title,
                     section=section, subsection=subsection, year=cite.year,
                     declined=declined)

    if code == CFR:
        # A C.F.R. title arrives in `chapter`; extract.py maps eyecite's groups
        # onto RawCitation without knowing that, so recover it here from the
        # citation text rather than trusting the field.
        if not ref.title:
            leading = re.match(r"\s*(\d+)\s", cite.raw_text)
            ref.title = leading.group(1) if leading else None
        match = _CFR_SECTION.match(section)
        ref.part = match.group(1) if match else None
        if ref.part is None:
            ref.declined = ref.declined or (
                f"the regulation number {section!r} is not in part.section form, "
                "so the part it belongs to could not be identified"
            )
    return ref


#: Forms a brief uses constantly and eyecite does not recognise, because it
#: requires the section symbol. "42 USC 1983" and "29 CFR 1910.132" produce no
#: citation at all, which means they are invisible rather than unchecked -- the
#: worse of the two failures, since nothing in the report says they were there.
_BARE_CODE = re.compile(
    r"""(?<![\w.§])
        (?P<title>\d{1,2})\s+
        (?P<code>U\.?\s?S\.?\s?C\.?(?:\s?A\.?|\s?S\.?)?|C\.?\s?F\.?\s?R\.?)
        \s+
        (?P<section>\d[\w.\-]*)
        """,
    re.VERBOSE,
)


def insert_section_symbols(text: str) -> tuple[str, int]:
    """Rewrite "42 USC 1983" as "42 U.S.C. § 1983". Returns text and a count.

    Offsets shift, so this is *not* run over a document whose spans must stay
    true to the source. It exists for callers that want a normalised copy --
    the count is what the report uses to say how many citations were only
    visible after normalising.
    """
    count = 0

    def replace(match: re.Match) -> str:
        nonlocal count
        count += 1
        raw = match.group("code").replace(" ", "").replace(".", "").upper()
        code = "C.F.R." if raw.startswith("CFR") else (
            ".".join(raw) + "." if raw == "USC" else "U.S.C."
        )
        if raw == "USC":
            code = "U.S.C."
        return f"{match.group('title')} {code} § {match.group('section')}"

    return _BARE_CODE.sub(replace, text), count
