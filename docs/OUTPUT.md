# Output reference

VeraScite writes two artifacts: `report.md` (for a human) and `ledger.json` (for a machine).

## Per-check verdicts

| Verdict | Meaning |
|---|---|
| `PASS` | Affirmatively confirmed against a retrieved source |
| `FAIL` | Affirmatively contradicted by a retrieved source. Always carries evidence. |
| `NOT_FOUND` | Not present in any source consulted. **Not a fabrication finding.** Always names the sources. |
| `NOT_CHECKABLE` | A source was retrieved but lacks the data this check needs |
| `AMBIGUOUS` | The citation matches several different cases |
| `OUT_OF_SCOPE` | An authority type this pipeline does not cover |
| `N/A` | The dimension does not apply to this citation |
| `PENDING` | Not yet checked |

## Document-level rollup

| Result | Meaning |
|---|---|
| `FLAGGED` | At least one affirmative contradiction. Do not file without fixing. |
| `UNVERIFIED` | Absent from consulted sources. **Not a fabrication finding.** |
| `REVIEW` | Something needs a human read |
| `PENDING` | Not yet checked |
| `VERIFIED` | Every applicable dimension passed |

Severity order is `FLAGGED → UNVERIFIED → REVIEW → PENDING → VERIFIED`. A citation showing
several results takes the most severe.

## Dimensions checked

| Dimension | Question |
|---|---|
| `reporter_valid` | Is this a reporter series that has ever existed? |
| `existence` | Does an authority exist at this volume/reporter/page? |
| `case_name` | Does the name in the document match the retrieved record? |
| `court` | Does the court match? |
| `year` | Does the year match? |
| `precedential_status` | Published, unpublished, or a denial of certiorari? |
| `quote` | Does the quoted language appear in the opinion? |
| `quote_attribution` | Is quoted language from the opinion of the court, or a separate writing? |
| `pincite` | Is the quoted language on the page the citation names? |
| `proposition` | Does the authority support the proposition it is cited for? *(model layer)* |
| `treatment` | Routed to manual review — **VeraScite is not a citator.** |

## Not checked

- Whether an authority is still good law. VeraScite does not detect overruled, reversed,
  vacated, abrogated, or superseded decisions.
- Statutes, regulations, legislative history, and secondary sources.
- Non-US authority.
