"""services/ckb_ruuter.py: content-external's one way to read CKB's DB.

No network: `requests.get` is stubbed throughout.
"""

from typing import Any

import pytest
import requests

from exporter.services import ckb_ruuter
from exporter.services.backoff import BackoffPolicy, is_retryable_ruuter_read
from exporter.services.ckb_ruuter import (
    AGENCIES_PATH,
    WATERMARK_PATH,
    CkbReadError,
    get_rows,
)

BASE = "http://user:hunter2@ruuter-internal.test:8089/ckb/"
INSTANT = BackoffPolicy(max_attempts=3, base_delay=0, max_delay=0)


def _response(status: int, body: bytes = b"[]") -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = body
    return response


def _stub(monkeypatch: pytest.MonkeyPatch, *answers: object) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    remaining = iter(answers)

    def fake_get(url: str, **kwargs: object) -> requests.Response:
        calls.append({"url": url, **kwargs})
        answer = next(remaining)
        if isinstance(answer, BaseException):
            raise answer
        assert isinstance(answer, requests.Response)
        return answer

    monkeypatch.setattr(ckb_ruuter.requests, "get", fake_get)
    return calls


def _read(**overrides: Any) -> list[Any]:  # noqa: ANN401
    kwargs: dict[str, Any] = {
        "base_url": BASE,
        "path": WATERMARK_PATH,
        "params": {"agencyBaseId": "agency-1"},
        "timeout": 9.0,
        "policy": INSTANT,
        "describe": "watermark",
    } | overrides
    return get_rows(**kwargs)


def test_the_url_joins_the_base_and_the_flow_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _stub(monkeypatch, _response(200, b'{"response": [{"a": 1}]}'))
    assert _read() == [{"a": 1}]
    assert calls == [
        {
            "url": BASE.rstrip("/") + "/content-external/export-watermark",
            "params": {"agencyBaseId": "agency-1"},
            "timeout": 9.0,
        }
    ]


@pytest.mark.parametrize(
    "path, params",
    [
        (WATERMARK_PATH, {"agency_base_id": "agency-1"}),
        (WATERMARK_PATH, {}),
        (AGENCIES_PATH, {"page": 1, "pageSize": 1}),
        ("/content-external/unknown", {"agencyBaseId": "agency-1"}),
    ],
)
def test_params_must_match_the_flows_allowlist(
    monkeypatch: pytest.MonkeyPatch, path: str, params: dict[str, Any]
) -> None:
    """Ruuter drops an unlisted param silently; refuse it here, before
    anything is sent."""
    calls = _stub(monkeypatch)
    with pytest.raises(ValueError, match="allowlist"):
        _read(path=path, params=params)
    assert calls == []


@pytest.mark.parametrize(
    "answer",
    [
        requests.ConnectionError(f"{BASE} refused"),
        _response(500, b""),
        _response(502, b'{"response": {"error": "resql_error"}}'),
        _response(300, b""),
        _response(200, b"not json"),
        _response(200, b'{"response": "Bad Request"}'),
        _response(200, b"null"),
    ],
    ids=["connection", "500", "502", "300", "not-json", "error-string", "null"],
)
def test_errors_never_carry_the_url(
    monkeypatch: pytest.MonkeyPatch, answer: object
) -> None:
    """F13: the base URL can carry userinfo; the message names the
    operation, never the address."""
    _stub(monkeypatch, *([answer] * INSTANT.max_attempts))
    with pytest.raises(CkbReadError) as excinfo:
        _read()
    message = str(excinfo.value)
    assert message.startswith("watermark: ")
    assert "hunter2" not in message
    assert "ruuter-internal.test" not in message


@pytest.mark.parametrize(
    ("status", "hint"),
    [(500, "flows not deployed"), (300, "flows deployed")],
)
def test_a_ruuter_500_or_300_names_its_likely_causes(
    monkeypatch: pytest.MonkeyPatch, status: int, hint: str
) -> None:
    """The status alone does not say whether Resql is down, a page outgrew
    the flow's limit, or the flow was never deployed (a Kubernetes rollout
    ahead of the upstream DSL merge). The message says where to look."""
    _stub(monkeypatch, *([_response(status, b"")] * INSTANT.max_attempts))
    with pytest.raises(CkbReadError, match=f"HTTP {status}: .*{hint}"):
        _read()


def test_ruuters_no_such_flow_300_fails_at_once_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ruuter answers a path with no flow behind it 300 with an empty body
    (seen against ruuter v2.2.1), which raise_for_status lets through. It
    must fail as "not deployed", on the first attempt, rather than as a
    body that is not JSON."""
    calls = _stub(monkeypatch, _response(300, b""))
    with pytest.raises(CkbReadError) as excinfo:
        _read()
    assert "HTTP 300" in str(excinfo.value)
    assert "flows deployed" in str(excinfo.value)
    assert "non-JSON" not in str(excinfo.value)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [201, 204, 302])
def test_any_status_but_200_is_an_error(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    _stub(monkeypatch, _response(status, b'{"response": []}'))
    with pytest.raises(CkbReadError, match=f"HTTP {status}"):
        _read()


@pytest.mark.parametrize("status", [500, 300])
def test_the_longest_error_fits_the_worker_error_line(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """worker/utils.py cuts an error line at 300 characters; the hint must
    survive intact with the longest flow path in front of it."""
    from exporter.worker.utils import _MAX_MESSAGE_CHARS

    _stub(monkeypatch, _response(status, b""))
    with pytest.raises(CkbReadError) as excinfo:
        _read(
            path=ckb_ruuter.DOCUMENTS_PATH,
            params={"agencyBaseId": "a", "afterBaseId": "b", "pageSize": 1},
            describe=f"CKB read {ckb_ruuter.DOCUMENTS_PATH}",
        )
    assert len(str(excinfo.value)) <= _MAX_MESSAGE_CHARS


def test_other_statuses_carry_no_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, _response(400, b""))
    with pytest.raises(CkbReadError) as excinfo:
        _read()
    assert str(excinfo.value).endswith("(HTTP 400)")


def _http_error(status: int) -> requests.HTTPError:
    return requests.HTTPError(str(status), response=_response(status))


@pytest.mark.parametrize(
    "error, retryable",
    [
        (requests.ConnectionError("refused"), True),
        (requests.ConnectTimeout("no answer"), True),
        (_http_error(502), True),
        (_http_error(503), True),
        (_http_error(504), True),
        (_http_error(429), True),
        # Ruuter's abort: a Resql step that timed out may still be running.
        (_http_error(500), False),
        (requests.ReadTimeout("slow"), False),
        (_http_error(400), False),
        (_http_error(404), False),
        # Ruuter's "no such flow": retrying cannot deploy it.
        (_http_error(300), False),
    ],
    ids=[
        "connection",
        "connect-timeout",
        "502",
        "503",
        "504",
        "429",
        "500",
        "read-timeout",
        "400",
        "404",
        "300",
    ],
)
def test_which_failures_are_retried(error: BaseException, retryable: bool) -> None:
    assert is_retryable_ruuter_read(error) is retryable
