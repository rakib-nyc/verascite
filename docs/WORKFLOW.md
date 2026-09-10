# Checking citations from an AI-assisted research session

For students, independent researchers, self-represented litigants, and anyone
doing legal research with an AI assistant and without a paid research
subscription.

---

## Why this is worth doing even when the assistant seems careful

The failure is not usually the one people expect. Of the citation defects in the
benchmark corpus this project measures against:

| What went wrong | Share |
|---|---:|
| A **real case**, cited for something it does not say | **41%** |
| The case name does not match the citation | 20% |
| The pincite points at the wrong page | 17% |
| The quotation is not in the opinion | 13% |
| The case does not exist | **10%** |

**Only a tenth are invented cases.** The rest are real, published, findable
decisions that were cited wrongly — and every one of them survives the check a
careful person performs by eye. You look the citation up, you find a real case,
and you are still wrong.

The same pattern shows in real sanctions: courts have recorded roughly twice as
many *misrepresented* and *falsely quoted* authorities as fabricated ones.

An assistant that invents `88 Jurisprudentia 100` is easy to catch. One that
cites a real Fourth Circuit case for a proposition it does not contain is not.

---

## The short version

```bash
# once
python3 -m venv verascite-env && source verascite-env/bin/activate
pip install "verascite[all] @ git+https://github.com/rakib-nyc/verascite.git"

# every time
verascite my-memo.docx --out ./check --plain
```

Read `check/report.md`. If you are not a lawyer, read `check/plain-english.md`
instead — same findings, no vocabulary to look up.

---

## The workflow

### 1. Draft wherever you like

Any assistant, any editor. This does not care where the text came from and does
not need to know.

### 2. Save it as a file the tool can read

`.docx`, `.pdf`, `.md` or `.txt`. If your research is sitting in a chat window,
paste it into a text file. Keep the citations exactly as written — spacing,
punctuation and the year all carry information the check uses.

### 3. Run the check

```bash
verascite research-memo.md --out ./check --plain
```

Add `--offline` to run local checks only, with **no citation string leaving your
machine**. You will get less: existence cannot be confirmed without consulting a
source. What you keep is reporter validity and the structural checks.

### 4. Read the results in the right order

Four results, and the difference between the middle two is the entire point.

| Result | What it means | What to do |
|---|---|---|
| **Contradicted** | A source was retrieved and it disagrees with your document | Fix it. The evidence is attached — read it rather than the label. |
| **Not found** | Absent from the sources consulted | **This is not a finding that it is fake.** Check it yourself, somewhere else. |
| **Needs review** | Something wants a human read | Read the passage. |
| **Confirmed** | Matched on every dimension checked | Nothing — but see the limits below. |

### 5. Read the coverage line before the counts

Every report opens with how much of your document the sources could actually
speak to. Roughly **one citation in ten** in a real brief is absent from the free
archives — recent decisions, unpublished dispositions, state trial courts, and
anything carried only by a paid service.

That line is why "no findings" does not mean "no problems". It means no problems
*in what could be examined*.

---

## The part that matters most if you have no subscription

Other tools, given a citation their corpus does not contain, will tell you it is
hallucinated. Published rates for that error run from **25% to 66%**.

This one has never done it: **zero false accusations across 345 unreachable
citations**, measured across three independent samples. One of those samples was
149 citations that courts had adjudicated to be fabricated — and even there, it
declined to say so, because a vendor-only identifier that was invented is
indistinguishable from one naming a real unreported decision when no free source
holds either.

If you have a paid subscription, a wrong "this is fake" costs you two minutes.
**If you do not, it costs you a real citation you delete from your brief.** That
is the error this tool is built to never make, and it is the reason it is worth
using specifically when you cannot afford to double-check.

---

## Checking from inside the assistant

If your assistant supports MCP, it can check its own citations before you ever
see them:

```bash
python -m verascite.mcp_server
```

Two tools: `verify_citations` and `extract_citations`. Every response ships the
reporting rules with it, so a model cannot turn "absent from the archives" into
"this case does not exist" while summarising.

---

## What this does not do

- **It cannot tell you whether a case is still good law.** It is not a citator.
  It does not detect overruled, reversed, vacated, abrogated or superseded
  decisions. This is the single largest gap and there is no free tool that
  closes it.
- **It cannot tell you whether a case helps your argument.**
- It covers United States authority only.
- Statutory currency is not checked: answers describe the text currently in
  force.
- **It is not legal advice, it certifies nothing, and responsibility for what
  you file stays with you.** No tool can take that on, and any tool claiming to
  should be distrusted.

---

## If something is reported as not found

In order of usefulness:

1. **Search the free archives directly** — CourtListener and the Caselaw Access
   Project. Coverage differs from what this tool consulted.
2. **Check a public law library.** Many offer free database access on site, and
   county law librarians answer questions from the public.
3. **Ask the assistant for the case's docket number and court**, then look for
   the docket rather than the citation. A fabricated case has no docket.
4. **If it cannot be found anywhere, do not cite it.** Not because the tool said
   so — it did not — but because you cannot read a case you cannot find, and
   citing something you have not read is the actual problem underneath all of
   this.
