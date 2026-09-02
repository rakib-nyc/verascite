# Contributing

Thank you for considering it. A few things about this codebase are unusual and
worth knowing before you change anything.

## The one rule that overrides everything

**Absence of evidence is never evidence of fabrication.**

`UNVERIFIED` means the sources consulted do not contain an authority.
`FLAGGED` means a retrieved source contradicts it. These are different claims
and the code keeps them structurally apart:

- There is no `FABRICATED` verdict anywhere, deliberately.
- `CheckResult(Verdict.FAIL)` raises without `evidence`.
- `CheckResult(Verdict.NOT_FOUND)` raises without `sources_consulted`.
- `tests/test_p1_separation.py` proves over *every* combination of verdicts
  that `FLAGGED` appears if and only if some dimension is `FAIL`.

A change that lets absence produce a flag will fail those tests. If you find
yourself wanting to weaken them, please open an issue first — telling a lawyer
a real case is fabricated is the failure this whole project is organised
around avoiding.

## Verdicts are monotonic

A check may go from `PENDING` to anything, and may be **downgraded**. A later
stage may never **upgrade** one. `LedgerEntry.set_check` raises otherwise, and
an explicit `override=` reason is recorded in the ledger when you really mean
it.

This exists because three separate bugs were a later stage overwriting an
earlier stage's decision, each making a citation look better than the evidence
supported. `tests/test_stage_order.py` runs the stages in the wrong order and
asserts the verdicts do not move.

## Thresholds are pinned

Every tunable number lives in `verascite/config.py`, is recorded into each
ledger, and is locked by a regression corpus with hand-reasoned expected
verdicts (`tests/fixtures/corpus.py`).

If you change one:

1. Say why in a protocol file under `evals/lephantomcite/`.
2. Re-run the regression corpus and update the expectations deliberately, not
   reflexively — each expectation is a statement about what the tool *should*
   say.
3. Raise `THRESHOLDS_VERSION`.

A change that silently moves a verdict is the thing this guards against.

## Running the tests

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q                       # 217 tests, no network, no token
python -m pytest -q evals/                # offline determinism
COURTLISTENER_API_TOKEN=... python -m pytest -q -m live
```

The unit suite is credential-independent **by construction**: `tests/conftest.py`
strips API tokens from the environment and blocks outbound sockets. A test that
reaches for the network fails by name. Please do not weaken that — a live token
in the environment silently masked a real assertion once already.

## The document is the attack surface

A citation checker is pointed at files from opposing counsel, from the
internet, and from clients. `verascite/safety.py` holds the guards, and every
one of them was written after running the payload against the real code path:

- **XML entity expansion** — a 618-byte `.docx` expanded to 1,000,000
  characters through five levels of internal entities. DTDs are refused; no
  word processor writes one.
- **Compression bombs** — a 398 KB `.docx` carrying 419 MB was read entirely
  into memory. Sizes are checked against the archive directory first.
- **Output injection** — a citation containing a newline and a `#` wrote its
  own heading into the report; control characters produced a `.docx` Word
  refuses to open. All untrusted text goes through `flatten_for_output`.

`tests/test_adversarial.py` keeps those payloads. `tests/test_properties.py`
looks for what nobody thought to write down — it found an inverted citation
span on degenerate input that every example-based test had missed.

Please add a payload rather than only a fix when you find one.

## Respect the API limits

Everything here runs on Free Law Project's infrastructure, maintained by a small
nonprofit. There are three limits and the documentation describes one:

| Limit | Value | Documented |
|---|---|---|
| Citations per minute (lookup) | 60 | yes |
| Requests per minute | 5 | no |
| **Requests per day** | **125** | no |

The client meters itself against all three, caches opinion text indefinitely,
expires negative lookups after 7 days, and honours the wait a 429 states.
**Never treat a 429 as an empty result** — a swallowed throttle drops a
sub-opinion from the search, and a quotation living in it then looks absent
from the case. That is how a correct quotation becomes a reported misquote.

If you are doing bulk work, use `--cache-only`, and consider
[membership](https://donate.free.law/forms/membership).

## What good work looks like here

- **Measure before building.** Treatment triage was designed, measured, and
  then not built, because 17% of citing opinions contain negative-treatment
  words with no way to tell which are about the cited case. That measurement is
  in the README and it is a result.
- **Report what did not work.** `evals/lephantomcite/V4_PROTOCOL.md` has an
  addendum listing three changes that moved the benchmark by nothing and were
  kept anyway. Reporting only what worked would misrepresent the search.
- **Prefer a stated limitation to a confident guess.** `NOT_CHECKABLE` with a
  reason is a good outcome. Most of this codebase is the machinery for knowing
  which one you are in.

## Where things are

| Path | What |
|---|---|
| `verascite/verdicts.py` | verdict vocabulary, rollup, the P1 interlocks |
| `verascite/extract.py` | eyecite plus the shadow scan for invented reporters |
| `verascite/verify_*.py` | one module per dimension |
| `verascite/clients/` | CourtListener, rate limiting, caching |
| `references/` | scope, limitations, verdict schema |
| `evals/lephantomcite/` | benchmark harness and the pre-registered protocols |
| `docs/` | scaling analysis, upstream rate-limit report |
