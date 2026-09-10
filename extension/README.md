# VeraScite browser extension

Check the legal citations on any page against free public archives — an AI chat,
a draft, a brief, an opinion. Nothing is installed on a server, nothing is sent
until you ask, and a citation the archive does not hold is never reported as
fabricated.

## Install (unpacked, ~30 seconds)

1. Open `chrome://extensions` in Chrome, Edge, Brave or Arc.
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked** and choose this `extension/` folder.
4. Pin the extension so its icon is visible.

To use it: open a page with citations, click the icon, and click
**Check citations on this page**.

## What it does, and when

**Counting is free and local.** Opening the popup extracts citations with a
regex and a bundled copy of every reporter abbreviation American law
recognises. No request is made, so you can see how many citations a page
contains without anything leaving the machine.

**Checking is deliberate.** Only when you click does it look anything up, and
only the citation strings go — never the page, never your draft, never your
question. Lookups run in the extension's own service worker rather than under
the page's origin, so the site you are reading cannot observe them.

## The four results

| | Colour | Meaning |
|---|---|---|
| **No such reporter series** | red | The reporter is not among the 1,342 series recognised in American law. This is a **contradiction**, not an absence — the one thing the extension states affirmatively. Runs locally, needs no request. |
| **Reported** | green | The archive holds a case at this citation, and names it. Compare that name against the one on the page. |
| **Not in this archive** | grey | Absent from the source consulted. **Not a finding.** |
| **No free source carries this** | grey | A vendor identifier. No request is made; no free archive holds these. |

### Why absence is grey

About **one citation in ten** in a real brief is missing from the free archives
— recent decisions, unpublished dispositions, state trial court orders, and
anything carried only by a paid service. Published false-flag rates for that
case run from **25% to 66%**: tools routinely tell people a sound citation is
hallucinated.

A browser extension's natural affordances push hard in that direction. A red
underline reads as *wrong*; a warning triangle reads as *fake*. So exactly one
result gets a warning colour, and it is the only one the extension can actually
prove. Everything else is neutral and says in words why it is not an accusation.

That restraint is measured, not stylistic: the reporter check alone produced
**zero false positives on 996 sound citations**, and the underlying tool has
made **zero false accusations across 345 unreachable citations** in three
independent samples.

## What it cannot tell you

- Whether a case says what the page claims it says. That is the **largest**
  category of citation defect and it is invisible here.
- Whether a quotation is real, or whether a page number is right.
- Whether an authority is still good law.

For those, run [the full tool](https://github.com/rakib-nyc/verascite) on the
document. It is free, open source, and runs on your own machine.

## Known limitations

- **Single-page applications may erase the inline marks.** On a site that
  re-renders its own DOM, highlights can disappear on the next update. The panel
  is attached outside the page's root and survives; the marks are a convenience.
- Only the common reporter form — volume, reporter, page — is recognised.
  Statutes, regulations and short-form citations are not.
- United States authority only.
- Lookups are paced and capped at 40 citations per page. CourtListener is a
  non-profit serving this free; please use it gently.

**Not legal advice. Certifies nothing. Responsibility for what you file stays
with you.**
