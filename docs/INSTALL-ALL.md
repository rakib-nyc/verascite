# Every way to use VeraScite, step by step

Five ways in. Pick the one that matches where you already work.

| | Install? | Runs where | Checks |
|---|---|---|---|
| [Web page](#1-web-page--nothing-to-install) | none | your browser | existence, case name, year, reporter |
| [Browser extension](#2-browser-extension) | sideload | any web page | the same, in place |
| [Word add-in](#3-word-add-in) | sideload | Word | the same, plus comments in the margin |
| [Command line](#4-command-line) | Python | your machine | **everything** — quotes, pincites, statutes, proposition support |
| [MCP server](#5-mcp-server-for-ai-assistants) | Python | your machine | everything, callable by an AI assistant |

> **AI can make mistakes. For experimental and research use only.**
> Not legal advice. Certifies nothing. Responsibility for anything you file stays
> with you.

---

## About the API token — read this once

**Everything here works without a token.** The CourtListener search endpoint
answers anonymous requests, so nothing below requires an account.

What a token buys is **rate limit**. Anonymous traffic is throttled first and
hardest; a long brief can crawl without one. If you check citations regularly,
get one — it is free and takes a minute.

### Getting one

1. Make a free account at [courtlistener.com/sign-in](https://www.courtlistener.com/sign-in/)
2. Open [courtlistener.com/profile/api-token](https://www.courtlistener.com/profile/api-token/)
3. Copy the token — 40 characters

You can revoke it on that same page at any time.

### Where each surface keeps it

| Surface | Stored in | Sent to |
|---|---|---|
| Web page | this browser's `localStorage` | courtlistener.com only |
| Extension | `chrome.storage.local` — this profile, **never synced** | courtlistener.com only |
| Word add-in | the task pane's `localStorage` | courtlistener.com only |
| Command line | `COURTLISTENER_API_TOKEN`, or `--token` | courtlistener.com only |
| MCP server | `COURTLISTENER_API_TOKEN` | courtlistener.com only |

**There is no account and no server of ours anywhere in this project.** Your
token is never transmitted to us, because there is nowhere for it to go. The
test suite asserts that no surface ships a credential, and the command-line
tool asserts that the token never reaches a report, a ledger, a cache key or a
log.

---

## 1. Web page — nothing to install

**[rakib-nyc.github.io/verascite/check.html](https://rakib-nyc.github.io/verascite/check.html)**

1. Open the page
2. Paste citations, or a paragraph containing them
3. Press **Look up citations**

**Optional token:** open *Use your own CourtListener token*, paste it, press
**Save**. It is checked against CourtListener before being stored, so a typo
surfaces immediately.

---

## 2. Browser extension

Chrome, Edge, Brave, or Arc.

1. Download the repository:
   ```bash
   git clone https://github.com/rakib-nyc/verascite.git
   ```
   Or download the ZIP from GitHub and unzip it.
2. Open `chrome://extensions`
3. Turn on **Developer mode** (top right)
4. Click **Load unpacked** and choose the `extension/` folder
5. Pin the extension so its icon is visible

**To use it:** open any page with citations, click the icon, click **Check
citations on this page**.

**Optional token:** click the icon → *Use your own CourtListener token*, or
right-click the icon → **Options**. Stored in `chrome.storage.local` on this
machine, never synced to a Google account.

**To update after a `git pull`:** return to `chrome://extensions` and click the
reload arrow on the VeraScite card.

---

## 3. Word add-in

### macOS

```bash
mkdir -p ~/Library/Containers/com.microsoft.Word/Data/Documents/wef
curl -o ~/Library/Containers/com.microsoft.Word/Data/Documents/wef/verascite.xml \
  https://raw.githubusercontent.com/rakib-nyc/verascite/main/word-addin/manifest.xml
```

Restart Word. The button appears on the **Home** tab as **Check citations**.

### Windows

1. Put `word-addin/manifest.xml` in a folder, e.g. `C:\verascite`
2. Right-click the folder → **Properties → Sharing → Share**, and copy the
   network path
3. Word → **File → Options → Trust Center → Trust Center Settings → Trusted
   Add-in Catalogs**. Paste the path, **Add catalog**, tick **Show in Menu**,
   OK, restart Word
4. **Home → Add-ins → My Add-ins → Shared Folder → VeraScite**

### Word on the web

**Home → Add-ins → More Add-ins → My Add-ins → Upload My Add-in**, choose
`manifest.xml`.

**To use it:** open a document, **Home → Check citations**, then **Check
citations in this document**. **Add a comment on each finding** writes Word
comments — on findings only, never on a citation that is merely absent from an
archive.

**Optional token:** in the task pane, open *Use your own CourtListener token*.

---

## 4. Command line

The only surface that checks quotations, pincites, statutory subsections, and
whether an authority supports the proposition it is cited for.

```bash
python3 -m venv verascite-env
source verascite-env/bin/activate          # Windows: verascite-env\Scripts\activate
pip install "verascite[all] @ git+https://github.com/rakib-nyc/verascite.git"
verascite --help
```

Python 3.10 or later. Then:

```bash
verascite brief.docx --out ./audit                 # standard run
verascite brief.docx --out ./audit --plain         # add a plain-language report
verascite brief.pdf  --out ./audit --offline       # nothing leaves the machine
verascite ./matter/  --batch --out ./audit         # a whole folder
```

**Setting your token:**

```bash
# for this shell only
export COURTLISTENER_API_TOKEN=your-token-here

# permanently (macOS/Linux, zsh)
echo 'export COURTLISTENER_API_TOKEN=your-token-here' >> ~/.zshrc && source ~/.zshrc

# or per run, without storing it
verascite brief.docx --out ./audit --token your-token-here
```

Windows PowerShell:

```powershell
setx COURTLISTENER_API_TOKEN "your-token-here"
```

Without a token, existence is reported `NOT_CHECKABLE` rather than guessed —
absence of a lookup is never treated as evidence about a citation.

Full guide including troubleshooting: [`docs/INSTALL.md`](INSTALL.md).

---

## 5. MCP server, for AI assistants

Lets an assistant check its own citations before you ever see them.

```bash
pip install "verascite[all] @ git+https://github.com/rakib-nyc/verascite.git"
python -m verascite.mcp_server
```

Add it to your assistant's MCP configuration:

```json
{
  "mcpServers": {
    "verascite": {
      "command": "python",
      "args": ["-m", "verascite.mcp_server"],
      "env": { "COURTLISTENER_API_TOKEN": "your-token-here" }
    }
  }
}
```

Leave `env` out entirely to run anonymous. Two tools are exposed:
`verify_citations` and `extract_citations`. Every response carries the rules for
reporting the result, because a model paraphrases whatever it is handed and
"absent from the archive" must never become "this case does not exist".

---

## Which surface checks what

|  | Web | Extension | Word | CLI | MCP |
|---|---|---|---|---|---|
| Reporter series exists | ✓ | ✓ | ✓ | ✓ | ✓ |
| Case exists at that citation | ✓ | ✓ | ✓ | ✓ | ✓ |
| Case name matches | ✓ | ✓ | ✓ | ✓ | ✓ |
| Year matches | ✓ | ✓ | ✓ | ✓ | ✓ |
| Quotation is real | | | | ✓ | ✓ |
| Pincite is right | | | | ✓ | ✓ |
| Statutes and subsections | | | | ✓ | ✓ |
| Supports the proposition | | | | ✓ (`--model`) | |
| Verification record | | | | ✓ | |
| Comments in the document | | in page | ✓ Word | ✓ `.docx` | |

**None of them** can tell you whether an authority is still good law. Nothing
free can.

---

## The rule every surface obeys

A citation the sources do not contain is reported as **absent**, never as
fabricated. About **one citation in ten** in a real brief is missing from the
free archives and is perfectly sound — recent decisions, unpublished
dispositions, state trial courts, and anything carried only by a paid service.

Measured: **zero false accusations across 345 unreachable citations**, in three
independent samples. Published rates for other systems on the same task run from
25% to 66%. [Protocol](../evals/abstention/PROTOCOL.md).
