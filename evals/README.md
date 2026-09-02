# Evaluation

## Automated

```bash
python -m pytest evals/ -q              # offline determinism and offset integrity
COURTLISTENER_API_TOKEN=... python -m pytest evals/ -q -m live   # cold-cache live determinism
```

`test_determinism.py` runs each fixture three times offline and requires
byte-identical ledgers. `test_determinism_live.py` does the same with the cache
bypassed so every request is really made — a warm cache proves the cache is
deterministic and nothing else, and the throttle bug that silently dropped
sub-opinions lived precisely in the path a warm run skips.

## Trigger testing (spec 8.4)

`trigger_prompts.json` holds prompts a skill must fire on and prompts it must
not. This is a manual protocol until it is wired to a harness: run each prompt
in a clean session with the skill installed and record whether it fired.

Undertriggering is the failure that matters. A skill that only fires on the
word "verify" protects nobody, because the realistic prompt is *"here's my
motion to dismiss, does this look right?"* — which is why most entries in the
list carry no trigger words at all, with `words_absent` recording which.

The `behavioural_checks` are separate from triggering and matter as much: they
test whether the model obeys the two prohibitions in `SKILL.md` — no assertions
about citations from its own knowledge, and never describing `UNVERIFIED` as
fabricated.

## Still to build (spec 8.3)

- **LePhantomCite** harness — 1,300 excerpts, 4,499 citations, 1,107
  hallucinated. The strongest published baseline is 68.8% F1; the tool has to
  beat that to earn its context window.
- **Real sanctioned filings** from the Charlotin database, where a court
  identified specific fabrications and the sanctions order is ground truth.
- **Clean control briefs** — pre-2022 appellate briefs, which should produce
  near-zero flags. Expect a small honest floor: genuine human citation typos
  exist in pre-LLM briefs.
- **Coverage-gap set** — recent decisions, state trial courts, WL-only cites.
  All should return `UNVERIFIED`; **none** should return `FLAGGED`. This is the
  trust metric, and it belongs in the headline results, not a footnote.
