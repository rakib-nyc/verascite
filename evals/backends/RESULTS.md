# Backend choice is not a detail

**2026-09-09.** Measured with `run_backend_eval.py` against the recorded
proposition samples in `evals/proposition/` — the same passages, the same
propositions, the same gold labels, so the figures are directly comparable to
the reader used in the original evaluation.

## Why this had to be measured before v0.2.0 shipped

Every published figure for the model layer was produced by one reader, driven a
batch at a time. v0.2.0 makes the layer configurable, which means the reader is
now whatever the user attaches. **A number measured with one reader says nothing
about another**, and shipping the old figure next to a new backend flag would
have implied otherwise.

So the backend layer does not ship with borrowed numbers. This is what one
small local model actually does.

## Setup

A general-purpose 4-billion-parameter instruction model with a reasoning mode,
served over a loopback chat-completions endpoint, temperature 0, 8,192-token
reply budget. Deliberately a modest model on ordinary hardware, because that is
what `--model local` is for: the lawyer who cannot send privileged text
anywhere.

Scoring rule reproduced unchanged from the recorded run: a flag is
`CONTRADICTED` at any confidence, or `NOT_SUPPORTED` at high confidence.
Anything else routes to a human and is not a finding.

## Result — n=35 of 38 (the run was still going; figures are for what completed)

| subset | n | TP | FP | FN | precision | recall |
|---|---:|---:|---:|---:|---:|---:|
| holdings | 20 | 0 | 1 | 12 | 0% | **0%** |
| brief PDFs | 15 | 1 | 0 | 5 | 100% | 16.7% |
| **all** | **35** | **1** | **1** | **17** | **50%** | **5.6%** |

For comparison, on the same samples the reader used in the original evaluation
measured **88.5% precision and 82.7% recall**.

**Recall collapses from 82.7% to 5.6%.** This is not a tuning problem and no
prompt change rescues it. The model does not decline to answer — it answers, and
its answers are almost all `NOT_SUPPORTED` at `low` confidence:

| verdict | count |
|---|---:|
| `NOT_SUPPORTED` | 33 |
| `CONTRADICTED` | 1 |
| `SUPPORTED` | 1 |

| confidence | count |
|---|---:|
| `low` | 31 |
| `high` | 4 |

Thirty-one of thirty-five readings arrive at low confidence, which by design
becomes `NOT_CHECKABLE` and reaches the reader as "a human should look at this."
The layer is running. It is just not deciding anything.

## The part worth keeping

**Under a weak reader the tool degrades toward silence, not toward false
accusation.** One false positive in 35. The conservative defaults that look
excessive with a capable reader are exactly what contains an incapable one:

- Only `CONTRADICTED`, or `NOT_SUPPORTED` at high confidence, can become a
  finding. Low-confidence output — almost everything this model produced —
  cannot.
- **The verbatim span interlock rejected 7 of 35 readings.** One of those seven
  would otherwise have been a flag, and its gold label was *clean*. The
  interlock caught a false accusation that a weak model was about to make.

That is the design working under adversarial conditions rather than favourable
ones, and it is the reason the model layer could be made configurable at all: a
user who attaches a poor backend gets less signal, not wrong signal.

## What this means for anyone using `--model`

1. **Measure your backend.** This harness is the one that produced the numbers
   above; point it at yours.
2. **Do not read the 88.5% / 82.7% figures as a property of the tool.** They are
   a property of that reader on that sample.
3. A small local model is currently better understood as a **privacy floor than
   a capability floor**: it keeps privileged text on the machine, and on this
   evidence it will find very little.

## Honest limits

- n=35, of which 18 carry a defect label. The confidence intervals are wide and
  the per-subset figures are wider still.
- One model, one quantisation, one runtime. Nothing here generalises to local
  models as a class, and a larger local model was not tested.
- The run was capped by wall-clock, not by a stopping rule: readings took
  roughly two minutes each on this hardware, which is itself a finding worth
  reporting to anyone planning to read a hundred opinions.
- The reply budget had to be raised to 8,192 because the model spends most of
  it on an internal scratchpad. A backend that supports suppressing that will
  be faster and may score differently.
