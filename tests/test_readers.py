"""The pluggable reading backends.

These tests exist because the reading stage is the one place a model's output
can reach a verdict, and the backend is the seam where a mistake would be
invisible: a reader that silently returns rubbish still produces a report. The
guards worth testing here are the ones that fail *closed* -- a misconfigured
backend must refuse to start, a failing one must produce no verdict, and a
'local' backend must be unable to send anything off the machine.
"""

import json
import pytest

from verascite import readers
from verascite.readers import (
    CostRates,
    Reader,
    ReaderError,
    ReaderStats,
    build_reader,
    parse_model_params,
    strip_reasoning,
)


# --- configuration must fail before any work is done -------------------------

def test_none_backend_builds_no_reader():
    assert build_reader("none") is None
    assert build_reader("") is None


def test_unknown_backend_is_refused():
    with pytest.raises(ReaderError, match="unknown reading backend"):
        build_reader("wishful")


def test_api_backend_requires_a_base_url():
    with pytest.raises(ReaderError, match="needs --model-base-url"):
        build_reader("api", model="m")


def test_api_backend_requires_a_token(monkeypatch):
    monkeypatch.delenv(readers.DEFAULT_TOKEN_ENV, raising=False)
    with pytest.raises(ReaderError, match="needs a token"):
        build_reader("api", model="m", base_url="https://example.invalid/v1")


def test_backends_require_a_model_name():
    with pytest.raises(ReaderError, match="needs --model-name"):
        build_reader("local")


def test_command_backend_requires_a_command():
    with pytest.raises(ReaderError, match="needs --model-command"):
        build_reader("command")


# --- the local guarantee is structural, not documentary ----------------------

@pytest.mark.parametrize("url", [
    "https://api.example.com/v1",
    "http://192.168.1.9:8080/v1",
    "http://evil.example/v1",
])
def test_local_backend_refuses_a_non_loopback_endpoint(url):
    """'local' promises nothing leaves the machine. Enforce it, don't say it."""
    with pytest.raises(ReaderError, match="loopback"):
        build_reader("local", model="m", base_url=url)


@pytest.mark.parametrize("url", [
    "http://localhost:11434/v1",
    "http://127.0.0.1:8080/v1",
    "http://[::1]:8080/v1",
])
def test_local_backend_accepts_loopback(url):
    assert build_reader("local", model="m", base_url=url) is not None


def test_local_reader_reports_that_nothing_leaves_the_machine():
    reader = build_reader("local", model="m")
    assert reader.describe()["leaves_this_machine"] is False


def test_api_reader_reports_that_text_leaves_the_machine(monkeypatch):
    monkeypatch.setenv(readers.DEFAULT_TOKEN_ENV, "t")
    reader = build_reader("api", model="m", base_url="https://example.invalid/v1")
    assert reader.describe()["leaves_this_machine"] is True


def test_a_token_never_appears_in_the_provenance(monkeypatch):
    monkeypatch.setenv(readers.DEFAULT_TOKEN_ENV, "sk-secret-value")
    reader = build_reader("api", model="m", base_url="https://example.invalid/v1")
    assert "sk-secret-value" not in json.dumps(reader.describe())


# --- reply handling -----------------------------------------------------------

def test_strip_reasoning_removes_a_scratchpad():
    assert strip_reasoning('<think>maybe {"a":9}</think>{"b":1}') == '{"b":1}'


def test_strip_reasoning_removes_an_unterminated_scratchpad():
    """A model that ran out of budget mid-thought emits no opening tag."""
    assert strip_reasoning('rambling on and on</think>{"b":1}') == '{"b":1}'


def test_strip_reasoning_leaves_a_plain_reply_alone():
    assert strip_reasoning('{"b":1}') == '{"b":1}'


def test_strip_reasoning_survives_an_empty_reply():
    assert strip_reasoning("") == ""


def test_a_scratchpad_cannot_smuggle_a_verdict_past_the_parser():
    """The scratchpad often contains a draft answer. It must not be the answer."""
    from verascite.verify_proposition import parse_answer
    reply = strip_reasoning(
        '<think>I could say {"verdict":"CONTRADICTED","confidence":"high"}</think>'
        '{"verdict":"SUPPORTED","confidence":"low"}'
    )
    assert parse_answer(reply).verdict == "SUPPORTED"


# --- parameters ---------------------------------------------------------------

def test_model_params_parse_json_values():
    assert parse_model_params(["think=false", "n=3", "s=hi"]) == {
        "think": False, "n": 3, "s": "hi",
    }


def test_model_params_nest_on_dots():
    assert parse_model_params(["a.b.c=1"]) == {"a": {"b": {"c": 1}}}


def test_model_params_reject_a_missing_value():
    with pytest.raises(ReaderError, match="key=value"):
        parse_model_params(["think"])


def test_model_params_are_sent_in_the_request_body(monkeypatch):
    seen = {}

    def fake_post(url, payload, headers, timeout):
        seen.update(payload)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(readers, "_post_json", fake_post)
    reader = build_reader("local", model="m", extra_params={"think": False})
    reader("prompt")
    assert seen["think"] is False
    assert seen["temperature"] == 0, "a verifier must not sample"


# --- failure is never a verdict ----------------------------------------------

def test_a_failing_backend_raises_rather_than_answering(monkeypatch):
    def fake_post(url, payload, headers, timeout):
        raise OSError("connection reset")

    monkeypatch.setattr(readers, "_post_json", fake_post)
    monkeypatch.setattr(readers.time, "sleep", lambda _: None)
    reader = build_reader("local", model="m")
    with pytest.raises(ReaderError):
        reader("prompt")
    assert reader.stats.failures == 1


def test_a_backend_failure_becomes_not_checkable_not_a_finding(monkeypatch):
    """The invariant: an API failure is recorded, never converted to a verdict."""
    from verascite.verify_proposition import PropositionQuestion, ask_and_verify

    monkeypatch.setattr(readers, "_post_json",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    monkeypatch.setattr(readers.time, "sleep", lambda _: None)
    reader = build_reader("local", model="m")
    answer = ask_and_verify(
        PropositionQuestion(citation="c", proposition="p", signal=None,
                            opinion_text="text"),
        reader,
    )
    assert answer.rejection
    assert answer.span_verified is False


def test_an_empty_reply_at_the_token_limit_says_so(monkeypatch):
    """A model that spent its budget thinking must not look like a refusal."""
    monkeypatch.setattr(readers, "_post_json", lambda *a, **k: {
        "choices": [{"message": {"content": "<think>"}, "finish_reason": "length"}],
        "usage": {"completion_tokens": 64},
    })
    monkeypatch.setattr(readers.time, "sleep", lambda _: None)
    reader = build_reader("local", model="m", max_tokens=64)
    with pytest.raises(ReaderError, match="token reply limit"):
        reader("prompt")


def test_a_non_retryable_status_is_not_retried(monkeypatch):
    import urllib.error
    calls = []

    def fake_post(url, payload, headers, timeout):
        calls.append(1)
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(readers, "_post_json", fake_post)
    reader = build_reader("local", model="m")
    with pytest.raises(ReaderError, match="HTTP 401"):
        reader("prompt")
    assert len(calls) == 1, "an authentication failure will not fix itself"


def test_a_retryable_status_is_retried(monkeypatch):
    import urllib.error
    calls = []

    def fake_post(url, payload, headers, timeout):
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.HTTPError(url, 503, "busy", {}, None)
        return {"choices": [{"message": {"content": "done"}}]}

    monkeypatch.setattr(readers, "_post_json", fake_post)
    monkeypatch.setattr(readers.time, "sleep", lambda _: None)
    reader = build_reader("local", model="m")
    assert reader("prompt") == "done"
    assert len(calls) == 3


# --- reply shapes -------------------------------------------------------------

def test_a_list_shaped_content_is_joined(monkeypatch):
    monkeypatch.setattr(readers, "_post_json", lambda *a, **k: {
        "choices": [{"message": {"content": [
            {"type": "text", "text": "a"}, {"type": "text", "text": "b"},
        ]}}]
    })
    assert build_reader("local", model="m")("p") == "ab"


def test_no_choices_is_an_error_not_an_empty_answer(monkeypatch):
    monkeypatch.setattr(readers, "_post_json", lambda *a, **k: {"choices": []})
    monkeypatch.setattr(readers.time, "sleep", lambda _: None)
    with pytest.raises(ReaderError, match="no choices"):
        build_reader("local", model="m")("p")


# --- accounting ---------------------------------------------------------------

def test_stats_report_no_cost_without_user_supplied_rates():
    stats = ReaderStats()
    stats.record("prompt", "reply", 1.0, {"prompt_tokens": 10, "completion_tokens": 5})
    assert "estimated_cost" not in stats.to_dict(CostRates())


def test_stats_report_cost_when_rates_are_given():
    stats = ReaderStats()
    stats.record("p", "r", 1.0, {"prompt_tokens": 1000, "completion_tokens": 2000})
    out = stats.to_dict(CostRates(input_per_1k=0.5, output_per_1k=1.5, currency="USD"))
    assert out["estimated_cost"] == "3.5000 USD"


def test_stats_say_so_when_a_backend_reports_no_tokens():
    stats = ReaderStats()
    stats.record("p", "r", 1.0, None)
    assert stats.to_dict()["tokens"] == "not reported by this backend"


def test_stats_track_the_slowest_read():
    stats = ReaderStats()
    stats.record("p", "r", 1.0, None)
    stats.record("p", "r", 9.0, None)
    assert stats.to_dict()["slowest_read_s"] == 9.0


# --- the subprocess backend ---------------------------------------------------

def test_command_backend_passes_the_prompt_and_returns_stdout():
    reader = build_reader("command", command="cat")
    assert reader("hello prompt") == "hello prompt"


def test_command_backend_failure_raises():
    reader = build_reader("command", command="false")
    with pytest.raises(ReaderError, match="exited"):
        reader("p")
