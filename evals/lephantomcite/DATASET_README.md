---
license: cc-by-4.0

tags:
  - legal
  - hallucination
  - citation-verification
  - nlp
  - law
pretty_name: LePhamtomCite
---

# Legal Phantom Citation Benchmark

**LePhamtomCite** is a benchmarking dataset for evaluating AI systems on legal citation hallucination detection. 

---

## Dataset description

Legal citation hallucinations (fabricated or misrepresented case citations in court filings) are a growing problem as attorneys, judges, and pro se litigants increasingly rely on LLMs to draft legal documents. LePhamtomCite provides a structured benchmark for evaluating automated citation verification systems designed to .

The dataset contains 1,300 excerpts drawn from two sources:

- **1,000 excerpts** from real federal appellate briefs (filed 2012–2021, retrieved via CourtListener) with systematically injected hallucinations. Hallucinations are injected into 50% of segments (500 excerpts with hallucinations, 500 without), creating a balanced evaluation set.
- **300 entries** from the LLM-generated central holdings portion of [Dahl et al. (2024)](https://doi.org/10.1093/jla/laae003), manually verified against Westlaw.


---

## Hallucination taxonomy

The dataset covers five hallucination types derived from real court filings:

| Type | Description | Example |
|---|---|---|
| **Non-existent citation** | Reporter citation does not correspond to any real case | `133 S. Ct. 1017` → `446 Cal. Rptr. 4th 183` |
| **Case name mismatch** | Case name and reporter citation refer to two different real cases | `Cinel v. Connick, 15 F.3d 1338` → `Boone v. Vinson, 15 F.3d 1338` |
| **Incorrect pincite** | Correct case, but the cited page number is wrong | `830 F.3d at 514` → `830 F.3d at 511` |
| **Verbatim misquote** | Quote is largely correct but does not appear verbatim in the cited case | One or two words replaced with synonyms |
| **Content misrepresentation** | Case exists but does not support the cited proposition | Holding altered to change its legal meaning |

---

## Data fields

Each entry contains:

| Field | Type | Description |
|---|---|---|
| `text` | `string` | A short excerpt from a legal brief, containing one or more case citations |
| `hallucinations` | `dict` | A mapping from hallucinated text span → hallucination type label. Empty dict `{}` for non-hallucinated entries |

**Example entry:**

```json
{
  "text": "[...] but the Commissioners exercise District powers when they handle D.C. parolees. See, e.g., Fletcher v. Dist. of Columbia, 370 F.3d 1223, 1224 judgment vacated on reh'g on other grnds, 391 F.3d 250 (D.C. Cir. 2004) US Parole Commission members are 1983 persons when they act pursuant to the Eighth Amendment's prohibition on cruel and unusual punishment. [...]",
  "hallucinations": {
    "370 F.3d 1223, 1224": "incorrect_pincite",
    "US Parole Commission members are 1983 persons when they act pursuant to the Eighth Amendment's prohibition on cruel and unusual punishment.": "content_misrepresentation"
  }
}
```

---

## Hallucination type counts

| Hallucination Type | Train | Eval | Total |
|---|---|---|---|
| **Non-existent citation** | 126 | 32 | 158 |
| **Case name mismatch** | 126 | 63 | 189 |
| **Incorrect pincite** | 124 | 53 | 177 |
| **Verbatim misquote** | 125 | 42 | 167 |
| **Content misrepresentation** | 285 | 131 | 416 |
| **Total** | **786** | **321** | **1107** |

---

## Splits

The aux_train set is not used in the paper [LINK] and may be noiser. We inlcude it as a training resource for future projects. 

| Split | Examples |
|---|---|
| `eval` | 390 |
| `aux_train` | 910 |

---

## Source data

Appellate briefs were collected from 13 U.S. Courts of Appeals via the [CourtListener API](https://www.courtlistener.com/), restricted to filings between January 2012 and December 2021 to minimize AI-generated content. Briefs were converted from PDF using [olmOCR](https://arxiv.org/abs/2502.18443), segmented with a fine-tuned RoBERTa sentence boundary model, and grouped into semantically coherent segments using Llama-3.3-70B-Instruct.

---


## License

This dataset is released under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The source appellate briefs are public domain U.S. court documents. The LLM-generated holdings subset is derived from [Dahl et al. (2024)](https://doi.org/10.1093/jla/laae003) — please also cite that work if you use those entries.