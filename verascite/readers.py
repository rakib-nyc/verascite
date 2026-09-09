"""Pluggable backends for the grounded reading in stage 5.

The deterministic core reaches 37.3% F1 on the benchmark corpus. With a model
reading the retrieved opinion it reaches roughly 70%. Until now the reading was
only reachable by embedding the library in a host that supplied its own reader,
so the installed command line shipped the weaker half. This module closes that
gap without loosening a single guard.

**What a backend is.** A reader is any callable that takes a prompt and returns
the model's reply as text. That is the whole contract, and it is deliberately
the narrowest one that can work: the prompt is built by
``verify_proposition.build_prompt`` and the reply is policed by the verbatim
span interlock, so a backend cannot widen what the model is allowed to decide.
It can only change who is asked.

**Why the backends are shaped this way.** Three exist:

``api``
    Any endpoint speaking the chat-completions JSON interface over HTTP. That
    interface is implemented by every hosted inference service worth using and
    by every local server, so one backend covers both and this project names no
    vendor and depends on no vendor's SDK.

``local``
    The same wire format pointed at a loopback address. It is a separate name
    rather than a flag because the guarantee it carries is different in kind:
    with ``local`` no citation, no proposition, and no opinion text leaves the
    machine. For a lawyer holding privileged material that is not a preference,
    and it should not be buried in a URL.

``command``
    A subprocess that reads the prompt on stdin and writes the reply on stdout.
    The escape hatch: it needs no HTTP server and no dependency, and it lets a
    user attach a reader this project has never heard of.

**What does not change.** The reading is advisory in exactly the way it was
before. Only ``CONTRADICTED`` with a span found verbatim in the retrieved text
can produce ``FAIL``; everything else lands on ``NOT_CHECKABLE`` for a human.
A backend that fails, times out, or returns nonsense produces no verdict at
all -- an API failure is not a finding (invariant 7).

**Determinism.** The deterministic layer is reproducible by construction and a
model is not. Two things follow, and both are enforced rather than documented:
no reader runs unless one is asked for, so the default output stays
reproducible; and every verdict a reader touched is recorded with the backend
and model that produced it, so a report never presents a model's reading as
though it were a deterministic result.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

#: Backend names the CLI accepts.
BACKENDS = ("none", "api", "local", "command")

#: Loopback default for ``local``. Chosen because it is the port the common
#: local servers listen on; override with --model-base-url for anything else.
DEFAULT_LOCAL_BASE_URL = "http://localhost:11434/v1"

#: Environment variable consulted for the bearer token when one is not named.
DEFAULT_TOKEN_ENV = "VERASCITE_MODEL_TOKEN"

#: A reading that has not returned by now is not going to be worth waiting for.
DEFAULT_TIMEOUT_S = 120

#: Reply budget. Generous because a model that spends its budget on an internal
#: scratchpad and never reaches the answer produces an empty reply, which is
#: indistinguishable from a refusal unless the budget was clearly the cause.
#: See --model-param for suppressing a scratchpad on backends that support it.
DEFAULT_MAX_TOKENS = 4096

#: Transient HTTP statuses worth one more attempt.
_RETRYABLE = {408, 409, 429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3


class ReaderError(RuntimeError):
    """A backend could not be built, or could not answer.

    Raised at construction for a misconfiguration the user must fix. Call
    failures are *not* raised past ``ask_and_verify``: they are caught there
    and become NOT_CHECKABLE, because a network error is not a verdict.
    """


@dataclass
class ReaderStats:
    """What a run cost, in the units that are actually knowable.

    Token counts are reported only when the backend reports them. Money is
    reported only when the user supplies rates, because this project does not
    ship a price table for anyone's service: a stale hardcoded price is worse
    than no price, and quoting one implies an endorsement.
    """

    calls: int = 0
    failures: int = 0
    prompt_chars: int = 0
    reply_chars: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tokens_reported: bool = False
    latency_s: float = 0.0
    slowest_s: float = 0.0

    def record(self, prompt: str, reply: str, elapsed: float, usage: Optional[dict]) -> None:
        self.calls += 1
        self.prompt_chars += len(prompt)
        self.reply_chars += len(reply or "")
        self.latency_s += elapsed
        self.slowest_s = max(self.slowest_s, elapsed)
        if usage:
            got_in = usage.get("prompt_tokens", usage.get("input_tokens"))
            got_out = usage.get("completion_tokens", usage.get("output_tokens"))
            if isinstance(got_in, int) or isinstance(got_out, int):
                self.tokens_reported = True
                self.input_tokens += int(got_in or 0)
                self.output_tokens += int(got_out or 0)

    def to_dict(self, rates: Optional["CostRates"] = None) -> dict:
        out = {
            "reads": self.calls,
            "failures": self.failures,
            "prompt_chars": self.prompt_chars,
            "reply_chars": self.reply_chars,
            "total_latency_s": round(self.latency_s, 2),
            "mean_latency_s": round(self.latency_s / self.calls, 2) if self.calls else 0.0,
            "slowest_read_s": round(self.slowest_s, 2),
        }
        if self.tokens_reported:
            out["input_tokens"] = self.input_tokens
            out["output_tokens"] = self.output_tokens
        else:
            out["tokens"] = "not reported by this backend"
        if rates and rates.usable and self.tokens_reported:
            out["estimated_cost"] = rates.estimate(self.input_tokens, self.output_tokens)
            out["estimated_cost_note"] = (
                "computed from the rates supplied on the command line, not from "
                "any price list shipped with this tool"
            )
        return out


@dataclass
class CostRates:
    """User-supplied per-1,000-token rates. Absent unless the user gives them."""

    input_per_1k: Optional[float] = None
    output_per_1k: Optional[float] = None
    currency: str = ""

    @property
    def usable(self) -> bool:
        return self.input_per_1k is not None or self.output_per_1k is not None

    def estimate(self, input_tokens: int, output_tokens: int) -> str:
        total = (input_tokens / 1000.0) * (self.input_per_1k or 0.0) + (
            output_tokens / 1000.0
        ) * (self.output_per_1k or 0.0)
        unit = f" {self.currency}" if self.currency else ""
        return f"{total:.4f}{unit}"


#: Reasoning-style models wrap their scratchpad in a tag before the answer.
#: That is a transport artefact, not part of the reply, and it is stripped here
#: rather than in the parser so the parser keeps testing one thing.
_THINK_BLOCK = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE)
#: A scratchpad that was closed but never opened -- the opening tag was
#: consumed by the chat template, which several backends do.
_ORPHAN_CLOSE = re.compile(r"^.*?</(?:think|thinking|reasoning)>", re.DOTALL | re.IGNORECASE)
#: A scratchpad that was opened and never closed -- the model ran out of reply
#: budget mid-thought. Everything after the tag is scratchpad, so there is no
#: answer here at all.
_ORPHAN_OPEN = re.compile(r"<(?:think|thinking|reasoning)>.*$", re.DOTALL | re.IGNORECASE)


def strip_reasoning(reply: str) -> str:
    """Remove a reasoning scratchpad from a reply, leaving only the answer.

    Handles the three shapes seen in the wild: a properly delimited block, a
    stray closing tag whose opener the chat template swallowed, and an opening
    tag with no close -- which means the reply budget ran out before the model
    reached an answer, and the correct result is nothing rather than a
    scratchpad passed off as a reply.

    Removing the scratchpad is not cosmetic. It routinely contains a draft
    answer the model then rejected, and the reply parser takes the first JSON
    object it sees.
    """
    if not reply:
        return ""
    cleaned = _THINK_BLOCK.sub("", reply)
    if re.search(r"</(?:think|thinking|reasoning)>", cleaned, re.IGNORECASE):
        cleaned = _ORPHAN_CLOSE.sub("", cleaned)
    cleaned = _ORPHAN_OPEN.sub("", cleaned)
    return cleaned.strip()


@dataclass
class Reader:
    """A configured grounded-reading backend.

    Callable, so it drops straight into the ``ask=`` parameter that
    ``verify_proposition`` already takes. Everything else on it is bookkeeping
    for the report.
    """

    backend: str
    model: str = ""
    endpoint: str = ""
    stats: ReaderStats = field(default_factory=ReaderStats)
    rates: CostRates = field(default_factory=CostRates)
    #: Seconds to wait between calls, from --model-rpm. 0 means no pacing.
    min_interval_s: float = 0.0
    timeout_s: int = DEFAULT_TIMEOUT_S
    _call: Optional[object] = None
    _last_call_at: float = 0.0

    def __call__(self, prompt: str) -> str:
        if self.min_interval_s:
            wait = self.min_interval_s - (time.monotonic() - self._last_call_at)
            if wait > 0:
                time.sleep(wait)
        started = time.monotonic()
        try:
            reply, usage = self._call(prompt)  # type: ignore[misc]
        except Exception:
            self.stats.failures += 1
            self._last_call_at = time.monotonic()
            raise
        self._last_call_at = time.monotonic()
        reply = strip_reasoning(reply)
        self.stats.record(prompt, reply, time.monotonic() - started, usage)
        return reply

    def describe(self) -> dict:
        """Provenance recorded into the ledger and printed in the report."""
        d = {
            "backend": self.backend,
            "model": self.model or "unspecified",
            "leaves_this_machine": self.backend not in ("local", "command"),
        }
        if self.endpoint and self.backend != "command":
            d["endpoint"] = self.endpoint
        return d


# --- the chat-completions HTTP backend ---------------------------------------


def _post_json(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _extract_reply(payload: dict) -> str:
    """Pull the assistant text out of a chat-completions response.

    Tolerant of the two shapes in circulation -- a plain string content and a
    list of typed content parts -- because both are served by endpoints
    claiming the same interface.
    """
    choices = payload.get("choices") or []
    if not choices:
        raise ReaderError("the endpoint returned no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text")
        ]
        return "".join(parts)
    if content is None and message.get("reasoning_content"):
        # The whole budget went to the scratchpad and no answer was emitted.
        return ""
    raise ReaderError("the endpoint returned a reply in an unrecognised shape")


def _hit_the_cap(payload: dict, usage: dict, max_tokens: int) -> bool:
    """Whether an empty reply is explained by the reply budget running out."""
    choices = payload.get("choices") or [{}]
    if (choices[0] or {}).get("finish_reason") == "length":
        return True
    produced = usage.get("completion_tokens", usage.get("output_tokens"))
    return isinstance(produced, int) and produced >= max_tokens


def _http_caller(base_url: str, model: str, token: Optional[str], timeout: int,
                 max_tokens: int, extra: Optional[dict] = None):
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def call(prompt: str) -> tuple[str, Optional[dict]]:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            # A verifier must not be creative. Zero temperature does not make
            # the reading reproducible -- nothing does, across model versions
            # and batching -- but it removes the sampling variance that is
            # ours to remove.
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        # Backend-specific keys, passed straight through. This project does not
        # keep a table of any backend's options -- such a table is wrong the
        # week it is written -- so the user supplies what their endpoint needs.
        # The common case is suppressing a reasoning scratchpad on a model that
        # would otherwise spend the whole reply budget on it.
        if extra:
            payload.update(extra)
        last: Optional[Exception] = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                data = _post_json(url, payload, headers, timeout)
                reply = _extract_reply(data)
                usage = data.get("usage") or {}
                if not strip_reasoning(reply) and _hit_the_cap(data, usage, max_tokens):
                    raise ReaderError(
                        f"the model reached the {max_tokens}-token reply limit "
                        "without producing an answer. Raise --model-max-tokens, "
                        "or suppress the model's reasoning scratchpad with "
                        "--model-param"
                    )
                return reply, usage
            except urllib.error.HTTPError as exc:
                last = ReaderError(f"HTTP {exc.code} from the reading endpoint")
                if exc.code not in _RETRYABLE:
                    raise last from exc
            except urllib.error.URLError as exc:
                last = ReaderError(f"could not reach the reading endpoint: {exc.reason}")
            except (json.JSONDecodeError, TimeoutError, OSError) as exc:
                last = ReaderError(f"the reading endpoint failed: {exc}")
            if attempt + 1 < _MAX_ATTEMPTS:
                time.sleep(2 ** attempt)
        raise last or ReaderError("the reading endpoint failed")

    return call


# --- the subprocess backend ---------------------------------------------------


def _command_caller(command: str, timeout: int):
    argv = shlex.split(command)
    if not argv:
        raise ReaderError("--model-command was empty")

    def call(prompt: str) -> tuple[str, Optional[dict]]:
        try:
            proc = subprocess.run(
                argv, input=prompt, capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ReaderError(f"the reading command timed out after {timeout}s") from exc
        except OSError as exc:
            raise ReaderError(f"could not run the reading command: {exc}") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip()[:200]
            raise ReaderError(
                f"the reading command exited {proc.returncode}"
                + (f": {detail}" if detail else "")
            )
        return proc.stdout, None

    return call


# --- construction -------------------------------------------------------------


def build_reader(
    backend: str,
    *,
    model: str = "",
    base_url: str = "",
    token_env: str = DEFAULT_TOKEN_ENV,
    token: Optional[str] = None,
    command: str = "",
    timeout_s: int = DEFAULT_TIMEOUT_S,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    extra_params: Optional[dict] = None,
    rpm: Optional[float] = None,
    rates: Optional[CostRates] = None,
) -> Optional[Reader]:
    """Build a reader, or return None for ``none``.

    Everything that can be wrong with the configuration is caught here, before
    a document is read, because discovering a missing model name after forty
    opinion fetches is a waste of somebody's rate limit.
    """
    backend = (backend or "none").strip().lower()
    if backend in ("", "none"):
        return None
    if backend not in BACKENDS:
        raise ReaderError(
            f"unknown reading backend {backend!r}; choose one of: "
            + ", ".join(b for b in BACKENDS if b != "none")
        )

    interval = 60.0 / rpm if rpm and rpm > 0 else 0.0
    reader = Reader(
        backend=backend,
        model=model,
        rates=rates or CostRates(),
        min_interval_s=interval,
        timeout_s=timeout_s,
    )

    if backend == "command":
        if not command:
            raise ReaderError("the 'command' backend needs --model-command")
        reader.endpoint = command
        reader._call = _command_caller(command, timeout_s)
        return reader

    if backend == "local":
        base_url = base_url or DEFAULT_LOCAL_BASE_URL
        if not _is_loopback(base_url):
            raise ReaderError(
                f"--model local requires a loopback endpoint and {base_url!r} is not "
                "one. The whole point of 'local' is the guarantee that nothing "
                "leaves the machine; use --model api for a remote endpoint."
            )
        resolved_token = token or os.environ.get(token_env) or None
    else:  # api
        if not base_url:
            raise ReaderError("the 'api' backend needs --model-base-url")
        resolved_token = token or os.environ.get(token_env) or None
        if not resolved_token:
            raise ReaderError(
                f"the 'api' backend needs a token: set ${token_env} or pass "
                "--model-token"
            )

    if not model:
        raise ReaderError(f"the {backend!r} backend needs --model-name")

    reader.endpoint = base_url
    reader._call = _http_caller(
        base_url, model, resolved_token, timeout_s, max_tokens, extra_params
    )
    return reader


def parse_model_params(pairs: list[str]) -> dict:
    """Turn repeated ``--model-param key=value`` into a JSON body fragment.

    A value that parses as JSON is used as JSON, so ``think=false`` sends a
    boolean and ``options={"num_ctx":8192}`` sends an object. Anything else is
    sent as a string. A dotted key nests: ``a.b=1`` becomes ``{"a": {"b": 1}}``.
    """
    out: dict = {}
    for pair in pairs or []:
        key, sep, raw = pair.partition("=")
        if not sep or not key.strip():
            raise ReaderError(f"--model-param expects key=value, got {pair!r}")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        target = out
        parts = [p for p in key.strip().split(".") if p]
        for part in parts[:-1]:
            target = target.setdefault(part, {})
            if not isinstance(target, dict):
                raise ReaderError(f"--model-param key {key!r} conflicts with an earlier one")
        target[parts[-1]] = value
    return out


def _is_loopback(url: str) -> bool:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").strip().lower()
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or host.endswith(".localhost")
