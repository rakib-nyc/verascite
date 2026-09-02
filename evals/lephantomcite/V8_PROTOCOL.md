# V8 — the model layer, measured rather than projected

Written after the change set, against results already recorded. The two code
changes it evaluates were committed in `e0dce44`; the readings below were made
against that build.

## What changed

1. **The proposition window measures prose, not characters.** It previously
   extended backward only when the citation's sentence was short. A citation
   sentence is not short and asserts nothing; neither does a string cite. The
   window now measures the sentence with citation text removed.

2. **A proposition is never read against a case the citation does not name.**
   Citations resolve by volume/reporter/page, so a wrong page lands on a real
   but unrelated opinion. The case-name check has already compared the names,
   so its verdict gates the read.

## Why this was worth measuring separately

Every earlier figure for the model layer came from n=18 and n=20. The
projection built on them was ~51% recall / ~64% F1. This run replaces the
projection with a measurement over both halves of the corpus.

## Method

Every citation in the eval split with (a) a gold label, (b) retrievable CAP
text, and (c) a proposition of at least 40 characters was read. Readings were
made against the retrieved passage alone. Gold labels were not consulted until
after each batch was recorded, and each batch was scored before the next was
displayed.

## Result — the two halves are not the same problem

| subset | n | TP | FP | FN | precision | recall | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| holdings (Dahl) | 90 | 74 | 4 | 3 | 94.9% | 96.1% | 95.5% |
| brief PDFs | 59 | 8 | 6 | 6 | 57.1% | 57.1% | 57.1% |

The holdings subset states a holding in prose and asks whether it is the
holding. The brief subset embeds the citation in advocacy, where the
proposition is what a party asserts, the passage is one page of a long
opinion, and the honest answer is often "this passage neither supports nor
contradicts it." **The gap between 95.5% and 57.1% is the real finding here**,
and it is not a tuning problem: the earlier n=20 sample was drawn entirely
from the easy half, which is why the projection was optimistic.

## Measured hybrid, full corpus

Deterministic v12 (`--no-quotes`) plus the model layer over everything it
could read. Nothing below is projected.

| | precision | recall | F1 |
|---|---:|---:|---:|
| deterministic alone (v12) | 81.8% | 23.1% | 36.0% |
| **+ model layer (measured)** | **85.7%** | **46.7%** | **60.5%** |
| strongest published baseline | 76.1% | 62.8% | 68.8% |

Precision is 9.6 points ahead. Recall is 16.1 points behind, and F1 with it.

## The identity guard is a free win

On the brief subset the guard blocked 14 reads. All 14 were clean citations.
Recall cost: zero. Reading a proposition against the wrong opinion produces a
truthful report that the text does not support the claim, and a sound citation
is flagged — the guard removes exactly that failure and nothing else.

## What still bounds recall

| bound | size | note |
|---|---:|---|
| content misrepresentation with no CAP text | 40 of 131 | not a reading failure; the opinion could not be retrieved |
| brief-subset reading | 6 FN of 14 | the hard half, measured above |
| misquotation | 42 gold, 4.8% recall | both sides are OCR; rejected in V7 for precision cost |

Closing the first row is a retrieval problem, not a model problem, and it is
the largest single block left.

## Correction to an earlier figure

A benchmark run made during this session reported 52.7% precision and was
briefly read as a regression. It was run without `--no-quotes`. The committed
baseline produces the same number under the same flag, so no regression
occurred; the headline configuration remains quotes-off, as V7 established.

---

## V8b — resolving a short form to the case that contains its page

Declared before the change, measured after.

### The defect

28 of the 36 content misrepresentations that could not be read were short
forms — `Hydrogen Peroxide, 552 F.3d at 309`, `Orthodox, 426 U.S. at 709` —
whose antecedent is not in the excerpt, because the excerpt begins after the
full citation. Their page addresses somewhere *inside* the case, so a lookup
by first page cannot match, and the harness gave up on them.

A second defect sat underneath: CAP records `first_page` as a string in some
volumes and an integer in others, and the fallback compared the raw value
against `int(page)`. For every string volume that comparison was silently
false, so even correct first-page lookups failed.

### The change

`CapClient.find` accepts `page_is_pincite`. When set, and only then, a case
may match because its page range contains the page.

**The gate is the point.** A page number falls inside *some* case in any
volume. Containment applied to a full citation would return a case for a
citation that does not exist, and offer it as evidence that the case is real
— the exact inversion P1 forbids. It is allowed only where the caller already
knows the page is a pincite.

Verified by hand against three:

| citation | resolves to | page range |
|---|---|---|
| `552 F.3d at 309` | In re Hydrogen Peroxide Antitrust Litig. | 305–327 |
| `426 U.S. at 709` | Serbian E. Orthodox Diocese v. Milivojevich | 696–735 |
| `676 F.3d at 1075` | Eurand, Inc. v. Mylan Pharms. (Cyclobenzaprine) | 1063–1088 |

Each matches the party name the brief gave, which is the check that the
resolution is right and not merely plausible.

### Effect

| | before | after |
|---|---:|---:|
| content misrepresentations readable | 89 of 131 | **117 of 131** |
| deterministic precision | 81.8% | 79.8% |
| deterministic recall | 23.1% | 24.0% |
| deterministic F1 | 36.0% | **37.0%** |

Two points of precision for one of recall and 28 more citations the model
layer can reach. Taken because recall is the binding constraint and the model
layer is where it comes from; recorded here so it can be reversed if the
precision cost matters more elsewhere.

---

## V8c — the model layer, measured across every citation it can read

235 citations read: 90 holdings, 59 brief, 86 reachable only after V8b.

| set | n | TP | FP | FN | precision | recall |
|---|---:|---:|---:|---:|---:|---:|
| holdings (Dahl) | 90 | 74 | 4 | 3 | 94.9% | 96.1% |
| brief PDFs | 59 | 8 | 6 | 6 | 57.1% | 57.1% |
| reachable after V8b | 86 | 28 | 5 | 12 | 84.8% | 70.0% |
| **all** | **235** | **110** | **15** | **21** | **88.0%** | **84.0%** |

### Measured hybrid, full corpus

| | precision | recall | F1 |
|---|---:|---:|---:|
| session start (v4) | 46.2% | 19.2% | 27.2% |
| deterministic only (v12) | 81.8% | 23.1% | 36.0% |
| **deterministic + model layer** | **84.2%** | **58.3%** | **68.9%** |
| strongest published baseline | 76.1% | 62.8% | 68.8% |

Precision is 8.1 points ahead. Recall is 4.5 behind. **F1 is 68.9 against
68.8, which is a tie, not a win** — a tenth of a point on 321 labels is one
citation, and no conclusion should be drawn from it. The defensible claim is
narrower and still worth making: the same F1 reached with a very different
error profile, trading recall for precision, on a task where a false
accusation against a sound citation is the expensive error.

## V8d — scoping the read to the cited page

### Diagnosis

Of the 21 misses, 12 had a pincite whose page could be reconstructed. Reading
that page instead of a keyword-matched window changes what the passage says:

| citation | what page N actually holds |
|---|---|
| `Moore, 137 S. Ct. at 1045` | Ginsburg's opening and the facts of the crime — not the three-criteria test the brief attributes to it |
| `Free Enterprise, 561 U.S. at 484-85` | Roberts on separation of powers — not the holding that Board members are officers |
| `Boyle, 556 U.S. at 947` | that an association-in-fact **must** have three structural features — the opposite of "does not require an ascertainable structure" |
| `Rosales, 19 F.3d at 765-66` | the appellant arguing the testimony was wrongly **admitted** — the brief cites it for inadmissibility |
| `Herschler, 591 F.2d at 701` | inventorship and § 120 priority; the quoted sentence is not on the page |

A keyword search over a whole opinion finds language near the claim somewhere,
because an opinion of any length contains near-miss language. Scoping to the
page is what makes the answer about the citation rather than about the case.

### Honesty about what this is

**Those 12 were re-read knowing they were labelled misrepresentations. That is
a mechanism diagnosis, not a measurement, and it is not evidence of a recall
gain.** What the table above establishes is independent of the labels — page
1045 of *Moore* is Ginsburg's opening whatever the gold file says. The recall
effect requires a fresh blind run over citations whose labels have not been
seen, which this session did not do.

The behaviour is implemented and tested: `_reading_scope` prefers the cited
page and its neighbours, falls back to the whole opinion when the page cannot
be isolated or reconstructs to less than 400 characters. The fallback matters
— reading a running head is worse than reading the whole opinion.

---

## V8e — REJECTED: inferring a wrong pincite from absence on the cited page

`_reading_scope` makes a new check look available: read the cited page, and if
the proposition is not on it, call the pincite wrong. 46 of 53 wrong-pincite
labels are currently missed, so the headroom is large. It was tested and it
must not be built.

### Method

29 wrong-pincite citations had a reconstructable cited page. 22 of them were
mixed with 22 randomly drawn citations that carry a pincite and no pincite
label, shuffled, and judged without labels: *is the content the claim asserts
on this page?*

### Result on the balanced sample

| | value |
|---|---:|
| recall | 77.3% |
| specificity | 77.3% |
| F1 | 77.3% |

### Why that number is a trap

The sample was balanced 22/22. The corpus is not: **29 wrong pincites against
562 citations that carry a pincite and are fine.** Carrying the measured
22.7% false-positive rate to that population:

| | flagged |
|---|---:|
| true wrong pincites found | 22 |
| sound citations falsely flagged | **128** |
| **precision** | **14.9%** |

Six false accusations for every finding. The balanced sample said 77%; the
corpus says 15%. Any check evaluated on a balanced sample of a rare event is
reporting a number that does not survive contact with real documents.

### The reason, which is P1

Every false positive was the same shape: the proposition was not visibly on
the page, so the pincite was called wrong. **The proposition not being on the
page is not evidence that the pincite is wrong.** It is evidence that a
reconstructed page, a keyword window, and an OCR'd reporter did not show it —
absence of evidence, promoted to a finding. That is the failure P1 exists to
prevent, arriving through a new door.

### What would be admissible

A wrong pincite may be reported only on **positive evidence**: the cited
content located on a *different* page. That is what the existing quote-based
pincite check does, and why it holds 7 detections against 9 false positives
rather than 22 against 128. Extending pincite detection means extending what
can be positively located, never relaxing what counts as located.

---

## V8f — a quotation found everywhere has not been located

Two of the nine pincite false positives fired on quotations the matcher placed
on 5 and 13 different pages. A phrase that recurs on thirteen pages has not
been located; which page it "is on" is undefined, and reporting the pincite as
wrong on that basis states a finding the evidence does not support. The check
now declines to decide above two candidate pages (two allows a passage that
spans a page break).

| | before | after |
|---|---:|---:|
| precision | 79.8% | **81.5%** |
| recall | 24.0% | 24.0% |
| false positives | 19 | **17** |

No recall cost.

## V8g — some "false positives" are real errors the benchmark does not label

LePhantomCite labels *injected* hallucinations. Errors already present in the
source briefs carry no label, so detecting one scores as a false positive.

Three of the remaining deterministic false positives were checked against CAP,
which is a different source from the CourtListener lookup that produced them:

| citation as filed | what the reporter actually holds | verdict |
|---|---|---|
| `Vela v. City of Houston, 216 F.3d 659` | *Vela* is at **276** F.3d 659 | wrong volume — real error |
| `United States v. Braswell, 687 F.3d 671` | *Petrofac v. DynMcDermott* | real error |
| `Spinella v. Pearce, 873 F.2d 701` | *Jalil v. Avdel Corp.* | real error |

Two sources agree in each case. These are correct detections.

Adjusting only for what was verified: **TP 75 → 78, FP 17 → 14, precision
81.5% → 84.8%.** Three more could not be corroborated because CAP does not
hold the volume; they are left counted against us.

**The headline figures in this file are not adjusted.** They stay as the
benchmark scores them, because the published baseline was scored the same
way and the same mechanism understates both. The right reading is that
measured precision is a **floor**, for us and for the baseline alike.

---

## V8h — REJECTED again, now with the distribution: misquote detection

Misquotation is 42 gold labels at 0% recall with quotes off, the second
largest block. V7 rejected quote checking on outcome; this measures the cause.

### Method

278 quotations were aligned against the best-matching window of the retrieved
opinion (anchored on the quote's least common words). 22 carried a misquote
label, 233 carried none.

### The distributions overlap

| | n | median | mean |
|---|---:|---:|---:|
| injected misquotes | 22 | 0.719 | 0.657 |
| clean quotations | 233 | **0.929** | 0.794 |

**A correct quotation matches its source at 0.93, not 1.00.** Both sides are
OCR: the briefs through olmOCR, CAP through scanned reporters. That noise floor
is the whole problem — the medians do separate, but the tails do not.

| flag at ratio ≤ | misquotes caught | clean flagged | precision | recall |
|---:|---:|---:|---:|---:|
| 0.99 | 21/22 | 150/233 | 12.3% | 95.5% |
| 0.94 | 20/22 | 125/233 | 13.8% | 90.9% |
| 0.90 | 19/22 | 107/233 | 15.1% | 86.4% |
| **0.85** | 17/22 | 82/233 | **17.2%** | 77.3% |
| 0.80 | 15/22 | 77/233 | 16.3% | 68.2% |
| 0.70 | 9/22 | 58/233 | 13.4% | 40.9% |

**There is no threshold. Precision peaks at 17.2%** — five false accusations
for every misquote found. The benchmark's injected errors are one- and two-word
synonym swaps, which are smaller than the disagreement OCR already produces
between a correct quotation and its source.

### Whole-corpus confirmation

Turning quotes on across the corpus, with every fix from this session in place:

| | precision | recall | F1 |
|---|---:|---:|---:|
| quotes off | 81.5% | 24.0% | 37.1% |
| quotes on | 50.3% | 30.8% | 38.2% |

21 more detections for 78 more false positives. Carried into the hybrid, quotes
on costs about 4 points of F1 and 20 of precision. Quotes stay off.

### What this does not say

It does not say quotation checking is useless. It says it cannot be done
**against OCR'd reporter text with these error sizes**. Against a publisher's
digital text the noise floor collapses and the same check becomes viable. The
blocker is the source, not the method — which is why this is recorded as a
corpus finding rather than a design conclusion.

---

## V8i — reaching text through parallel reporters

CAP holds U.S. but not S. Ct. `137 S. Ct. 1045` was therefore unreachable
under its own reporter and reachable as `581 U.S. 1`. The CourtListener
cluster already lists every reporter the decision is printed in; `Resolution`
now carries them, skipping LEXIS and WL identifiers because no free archive
holds those.

| | before | after |
|---|---:|---:|
| content misrepresentations with retrievable text | 117 of 131 | **125 of 131** |

Two remain unreachable, down from 42 at the start of the session.

**The pincite must not travel with it.** Page 1045 of S. Ct. is not page 1045
of U.S., so a pincite compared against another reporter's pagination is
guaranteed to disagree and the disagreement means nothing. Wiring the parallel
lookup without that guard cost 14 false positives immediately. A short form
carries no reporter of its own — `Id. at 2586` inherits it — so the antecedent
chain is walked to find the reporter actually being cited.

## V8j — a pincite range names every page in it

`Idaho Power Co., 312 F.3d at 459-61` was flagged because the quotation sits on
461. 461 is a page the citation names. Reading only the first number of a range
reports a cited page as uncited — a false accusation against a correct pincite.
`459-61` abbreviates 461, not 61, and an implausibly long span is not a range.

## Where this turn ended

| | precision | recall | F1 |
|---|---:|---:|---:|
| deterministic only | 83.3% | 24.0% | 37.3% |
| **deterministic + model layer** | **86.4%** | **59.2%** | **70.2%** |
| strongest published baseline | 76.1% | 62.8% | 68.8% |

Ahead on precision by 10.3 points and on F1 by 1.4. **1.4 F1 points is roughly
four citations out of 321** — ahead, but not by a margin that should be
described as decisive. Recall remains 3.6 points behind.

Adjusting only for the three unlabeled true positives verified in V8g,
precision is 87.7%.

### What is now known to be unavailable

| block | gold | status |
|---|---:|---|
| misquotation | 42 | rejected — OCR floor exceeds the signal (V8h), precision peaks at 17.2% |
| wrong pincite by absence | 46 | rejected — 14.9% precision at the true base rate (V8e) |
| case name, thin short forms | 4 | rejected — 40% precision, 4 detections for 6 false alarms |
| case name, retrieval failures | 13 | not resolvable: the record was never retrieved |
| content misrepresentation | 6 | 125 of 131 read; the rest lack retrievable text |

Roughly 105 of the 131 remaining misses sit behind measured rejections rather
than unbuilt work. Further recall on this corpus needs a better text source —
publisher-quality text in place of OCR — not a better checker.
