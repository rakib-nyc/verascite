# VeraScite for Word

Check every legal citation in a Word document against free public archives, and
leave a comment in the margin on the ones a source contradicts — without the
document ever leaving your machine.

## Install (sideload)

The add-in is not in the Office store. The task pane is served over HTTPS from
GitHub Pages; there is no server of ours anywhere in the path.

### macOS

```bash
mkdir -p ~/Library/Containers/com.microsoft.Word/Data/Documents/wef
curl -o ~/Library/Containers/com.microsoft.Word/Data/Documents/wef/verascite.xml \
  https://raw.githubusercontent.com/rakib-nyc/verascite/main/word-addin/manifest.xml
```

Restart Word. The button appears on the **Home** tab as **Check citations**.

### Windows

1. Make a folder, e.g. `C:\verascite`, and put `manifest.xml` in it.
2. Share the folder (right-click → Properties → Sharing → Share), and copy the
   network path.
3. In Word: **File → Options → Trust Center → Trust Center Settings → Trusted
   Add-in Catalogs**. Paste the path, click **Add catalog**, tick **Show in
   Menu**, click OK, restart Word.
4. **Home → Add-ins → My Add-ins → Shared Folder → VeraScite**.

### Word on the web

**Home → Add-ins → More Add-ins → My Add-ins → Upload My Add-in**, and choose
`manifest.xml`.

## What it does

**Check citations in this document** reads the document text, pulls out every
reporter citation, and reports what is actually printed at each one.

**Add a comment on each finding** writes a Word comment anchored to the
citation. It writes on findings **only**:

| | Comment written? | Why |
|---|---|---|
| Different case at this citation | **yes** | A source was retrieved and it names a different case |
| No such reporter series | **yes** | The reporter is not one of the 1,342 recognised in American law |
| Year does not match | **yes** | The name matches, the year does not |
| Reported | no | Nothing to say |
| Not in this archive | **no** | Absence is not a finding |
| No free source carries this | **no** | A gap in public access, not a defect |

That last pair is the point. About **one citation in ten** in a real brief is
missing from the free archives and is perfectly sound. A margin comment saying
otherwise is an accusation the evidence does not support, so none is written.

## What leaves your machine

**Only the citation strings** — `550 U.S. 544`, and nothing else. Never the
document, never a sentence of it, never the client's name. Lookups go from the
task pane straight to the free CourtListener API. Nothing is stored anywhere,
by anyone, and there is no account.

## What it cannot tell you

- Whether the case says what you claim it says. That is the **largest**
  category of citation defect and it is invisible here.
- Whether a quotation is real, or a pincite right.
- Whether an authority is still good law.
- Anything about statutes, regulations, or non-US authority.

For quotations, pincites, statutory subsections and proposition support, run
[the full tool](https://github.com/rakib-nyc/verascite) over the `.docx`. It
also writes an annotated copy with comments, plus a dated verification record.

## Requirements and limits

- Word 2016 or later on Windows or macOS, or Word on the web. Comments need
  WordApi 1.4 or later; on an older build the check still runs and the comment
  button reports that it could not anchor them.
- Only the common reporter form — volume, reporter, page — is recognised.
  Short forms (`550 U.S. at 555`), statutes and regulations are not.
- Capped at 60 citations per run and paced between lookups. CourtListener is a
  non-profit serving this free; please use it gently.

**Not legal advice. Certifies nothing. Responsibility for the filing stays with
the person filing it.**
