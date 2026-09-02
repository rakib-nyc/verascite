"""Tunable thresholds, pinned in one place.

The deterministic layer must be bit-identical across runs (spec 8.2), which
makes every free numeric in it a hazard: tuning a threshold in a later
milestone would silently flip verdicts rendered by an earlier one. So the
numerics live here, carry a version, and are recorded into every ledger. A
regression test locks the verdicts they produce against a fixed corpus, so a
change to any of them fails loudly rather than quietly rewriting history.

Raising THRESHOLDS_VERSION is the deliberate act of accepting that.
"""

from __future__ import annotations

THRESHOLDS_VERSION = "2"

# --- quote matching -----------------------------------------------------------
# Calibrated against the real text of Bell Atlantic Corp. v. Twombly:
#   exact quote                1.00
#   one-word synonym swap      0.94
#   two-word swap              0.79
#   wholly invented language   0.28

#: At or above this, a mismatch is an alteration of text that is really there.
QUOTE_MATERIAL_ALTERATION_FLOOR = 0.75
#: Below this, the passage is simply not in the opinion.
QUOTE_NEAR_MISS_FLOOR = 0.60
#: Shorter quotes are too common to attribute to one authority reliably.
QUOTE_MIN_CHARS = 24
#: Maximum characters between a quotation and the citation that vouches for it.
QUOTE_MAX_ATTRIBUTION_GAP = 140

# --- case-name matching -------------------------------------------------------
#: Token-set overlap at or above this is the same case. Calibrated for the
#: canonical matcher in verascite/names.py, which folds the party-name and
#: T10 abbreviations and expands acronyms -- same-case pairs score at or near
#: 1.00 under it, so the boundary sits lower than the literal matcher needed.
#: See evals/lephantomcite/V5_PROTOCOL.md for the sweep.
NAME_PASS_THRESHOLD = 0.60
#: Below this is a different case; between the two is a human's call.
NAME_REVIEW_THRESHOLD = 0.34

# --- rate limits --------------------------------------------------------------
#: Measured 2026-08-31 against a default-tier account.
#:
#: There are TWO independent limits and the documentation describes only one.
#:
#: 1. The documented one: 60 valid *citations* per minute on citation-lookup.
#: 2. An undocumented one: 5 *requests* per minute. This applies to
#:    citation-lookup as well as to opinions, clusters, and dockets. Ten
#:    single-citation lookups -- well under 60 citations/min -- are throttled
#:    at the fifth request.
#:
#: The practical consequence is that the documented 60 citations/min is only
#: reachable by batching heavily: 60 citations in one request takes a second,
#: while 60 citations sent one at a time takes twelve minutes.
#:
#: Whether the 5/min budget is shared across endpoints or applied per-endpoint
#: was not established. The client assumes shared, which is the conservative
#: reading: if the limits are in fact separate, this is merely gentler than
#: required, and it is not our place to probe a nonprofit's infrastructure
#: until it breaks to find out.
REQUESTS_PER_MINUTE = 5
CITATIONS_PER_MINUTE = 60

#: Backwards-compatible alias.
OPINION_REQUESTS_PER_MINUTE = REQUESTS_PER_MINUTE

# --- cache lifetimes ----------------------------------------------------------
#: A 200 records a fact about a decided case and never changes, so opinion
#: text and successful lookups cache indefinitely (spec 4.5).
#:
#: A 404 does not. CourtListener ingests continuously, so "not in the database"
#: is a statement about a moment in time. Cached forever it decays into a false
#: UNVERIFIED -- the tool would keep reporting a case as unfindable for months
#: after it was added. Negative and ambiguous results therefore expire.
NEGATIVE_RESULT_TTL_DAYS = 7
#: Beyond this, a report says how old its evidence is.
CACHE_AGE_WARNING_DAYS = 30

# --- completeness -------------------------------------------------------------
#: A quote absent from an opinion is only a misquote if the text is whole.
#: Fraction of the opinion tail searched for a disposition marker. Generous,
#: because footnotes and appendices routinely follow the disposition and can
#: occupy a large share of the end of the text.
DISPOSITION_TAIL_FRACTION = 0.30
#: Star-pagination runs shorter than this prove nothing about completeness.
MIN_PAGE_MARKS_FOR_CONTIGUITY = 3
#: Opinions shorter than this are treated as fragments, not full text.
MIN_COMPLETE_OPINION_CHARS = 1500


def snapshot() -> dict:
    """Threshold values recorded into each ledger for auditability (P3)."""
    return {
        "version": THRESHOLDS_VERSION,
        "quote_material_alteration_floor": QUOTE_MATERIAL_ALTERATION_FLOOR,
        "quote_near_miss_floor": QUOTE_NEAR_MISS_FLOOR,
        "quote_min_chars": QUOTE_MIN_CHARS,
        "quote_max_attribution_gap": QUOTE_MAX_ATTRIBUTION_GAP,
        "name_pass_threshold": NAME_PASS_THRESHOLD,
        "name_review_threshold": NAME_REVIEW_THRESHOLD,
        "min_complete_opinion_chars": MIN_COMPLETE_OPINION_CHARS,
        "requests_per_minute": REQUESTS_PER_MINUTE,
        "citations_per_minute": CITATIONS_PER_MINUTE,
        "negative_result_ttl_days": NEGATIVE_RESULT_TTL_DAYS,
    }
