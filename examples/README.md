# Examples

Worked, runnable examples. Each is self-contained.

| Example | What it shows |
|---|---|
| [`01_check_a_brief.py`](01_check_a_brief.py) | Audit a document and act on the verdicts |
| [`02_ci_gate.py`](02_ci_gate.py) | Fail a build on contradictions only, never on absence |
| [`03_agent_tool.py`](03_agent_tool.py) | Expose VeraScite as a tool to an AI assistant |
| [`04_triage_report.py`](04_triage_report.py) | Turn a ledger into a reviewer's worklist |

Run any of them:

```bash
pip install -e ".[all,dev]"   # from a clone of the repo
python examples/01_check_a_brief.py ../tests/fixtures/sample_brief.md
```
