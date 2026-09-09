# Installation

Complete instructions, including Windows, offline installs, and troubleshooting.

> VeraScite is experimental research software with no warranty. It does not
> certify filings and does not replace review by a licensed attorney.

---

## Requirements

| | |
|---|---|
| Python | 3.10 or newer |
| Disk | ~200 MB (mostly the caselaw reference databases) |
| Network | Optional — `--offline` runs entirely locally |
| Credentials | None required |

Check your Python:

```bash
python3 --version
```

If it prints below `3.10`, install a newer one from
[python.org/downloads](https://www.python.org/downloads/).

---

## macOS and Linux

```bash
# 1. Create an isolated environment (recommended)
python3 -m venv verascite-env
source verascite-env/bin/activate

# 2. Install
pip install "verascite[all] @ git+https://github.com/rakib-nyc/verascite.git"

# 3. Confirm
verascite --help
```

## Windows (PowerShell)

```powershell
py -3 -m venv verascite-env
verascite-env\Scripts\Activate.ps1

pip install "verascite[all] @ git+https://github.com/rakib-nyc/verascite.git"

verascite --help
```

If PowerShell blocks the activate script, run once:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## From a local clone (for development)

```bash
git clone https://github.com/rakib-nyc/verascite.git
cd verascite
pip install -e ".[all,dev]"
pytest -q          # 431 tests, no network, no credentials needed
```

---

## What the extras install

| Command | Adds |
|---|---|
| `pip install "... @ git+https://..."` | Markdown and plain text input |
| `pip install "verascite[pdf] @ ..."` | `.pdf` input |
| `pip install "verascite[docx] @ ..."` | `.docx` input and annotated output |
| `pip install "verascite[all] @ ..."` | Both of the above |
| `pip install "verascite[dev] @ ..."` | Test suite |

---

## The optional CourtListener token

VeraScite works with no credentials against the public-domain
[Caselaw Access Project](https://case.law/). A free
[CourtListener](https://www.courtlistener.com/) token adds coverage of recent
decisions that the archive does not yet hold.

1. Create a free account at [courtlistener.com/sign-in](https://www.courtlistener.com/sign-in/)
2. Copy the token from [courtlistener.com/profile/api](https://www.courtlistener.com/profile/api/)
3. Export it:

```bash
export COURTLISTENER_API_TOKEN="your-token-here"          # macOS / Linux
```

```powershell
$env:COURTLISTENER_API_TOKEN = "your-token-here"          # Windows
```

To persist it, add the line to `~/.zshrc`, `~/.bashrc`, or your PowerShell
profile. You can also pass it per-run with `--token`.

**The token never reaches reports, ledgers, cache keys, or logs.** This is
enforced by `tests/test_no_credential_leak.py`, and the unit suite blocks
network access at the socket layer so no test can reach the network or use a
live credential.

### Please respect the rate limits

CourtListener is a free public service run by a non-profit. VeraScite limits
itself to 60 citations/minute, 5 requests/minute, and 125 requests/day, and
caches everything to disk. Re-running against the same brief costs nothing.
Please do not remove the limiter.

---

## Running without any network

```bash
verascite brief.pdf --out ./audit --offline
```

No citation string leaves the machine. Existence is reported `NOT_CHECKABLE`
rather than guessed — absence of a lookup is not evidence about the citation.

---

## Troubleshooting

**`verascite: command not found`**
The environment is not active, or the install went to a different Python.
Re-run `source verascite-env/bin/activate`, or invoke it directly:

```bash
python3 -m verascite.run_audit brief.pdf --out ./audit
```

**`ModuleNotFoundError: No module named 'pdfplumber'`**
PDF support is an extra. Reinstall with `[all]` or `[pdf]`.

**`error: externally-managed-environment`**
Your system Python refuses global installs. Use a virtual environment as shown
above — that is the fix, not `--break-system-packages`.

**`note: COURTLISTENER_API_TOKEN is not set`**
Informational. VeraScite continues with local checks and the public archive.
Existence is reported `NOT_CHECKABLE` rather than guessed.

**A citation came back `UNVERIFIED` and I expected it to be found**
That is the tool working correctly. `UNVERIFIED` means the sources consulted do
not contain it — routine for recent decisions, unpublished dispositions, state
trial orders, and vendor-only identifiers. **It is not a finding of
fabrication.** Adding a CourtListener token widens coverage.

**The first run is slow**
Reference databases and opinion text are being fetched and cached. Later runs
against the same authorities are much faster.
