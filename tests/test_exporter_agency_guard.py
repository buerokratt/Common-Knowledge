"""A17 — assert N=1, rather than starve the second agency in silence.

No network: `requests.get` is stubbed throughout.

The failure this guards is the quietest one in the plan. G1 fans out from
`list_agencies`; F2 takes a whole-run lock. With two agencies the second POST
finds the lock held, reports `busy` and exits 0 — every hour, forever, with
nothing logged as an error, because a concurrent run is by design not a
failure. The second agency is simply never exported and no signal says so.
"""

import logging
from typing import Any

import pytest
import requests

from exporter.api.config import Settings
from exporter.services.agency_guard import (
    AgencyCountUnavailableError,
    MultipleAgenciesError,
    check_single_agency_at_startup,
    count_agencies,
    require_single_agency,
)

GUARD = "exporter.services.agency_guard.requests.get"


def _settings() -> Settings:
    return Settings(content_work_dir="/var/lib/content-external")  # pyright: ignore[reportCallIssue]


class _Response:
    """The slice of requests.Response this module actually uses."""

    def __init__(
        self, payload: object = None, *, status: int = 200, body_is_json: bool = True
    ) -> None:
        self._payload = payload
        self._status = status
        self._body_is_json = body_is_json

    def raise_for_status(self) -> None:
        if self._status >= 400:
            raise requests.HTTPError(str(self._status))

    def json(self) -> object:
        if not self._body_is_json:
            raise ValueError("not json")
        return self._payload


def _stub(monkeypatch: pytest.MonkeyPatch, response: object) -> dict[str, Any]:
    """Install a stub and capture the call, so the request shape is testable."""
    captured: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: object) -> object:
        captured["url"] = url
        captured.update(kwargs)
        return response

    monkeypatch.setattr(GUARD, fake_get)
    return captured


def _raiser(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    def fake_get(_url: str, **_kwargs: object) -> object:
        raise error

    monkeypatch.setattr(GUARD, fake_get)


def _rows(total: int) -> list[dict[str, object]]:
    """What list_agencies returns for page_size=1: one row plus the window
    total, or nothing at all when there are no agencies."""
    if total == 0:
        return []
    return [{"base_id": "agency-1", "name": "Agency", "total": total}]


# --------------------------------------------------------------------------
# The request itself
# --------------------------------------------------------------------------


def test_it_reads_resql_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    """list_agencies has no Ruuter-internal wrapper, and adding one here
    would pre-empt G1's "reuse, do not add one"."""
    captured = _stub(monkeypatch, _Response(_rows(1)))
    count_agencies(_settings())

    assert captured["url"] == "http://resql-ckb:8090/ckb/agency/list_agencies"


def test_it_asks_for_a_single_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """The query reports the unpaginated total through a window function, so
    one row is enough and the whole table never crosses the wire."""
    captured = _stub(monkeypatch, _Response(_rows(1)))
    count_agencies(_settings())

    assert captured["params"]["page_size"] == 1
    assert captured["params"]["page"] == 1


def test_the_sorting_value_is_in_the_declared_enum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_agencies.sql's declaration allowlists six values. Ruuter's own
    all.yml passes 'updated_at desc', which is not among them — harmless
    there, not worth copying."""
    captured = _stub(monkeypatch, _Response(_rows(1)))
    count_agencies(_settings())

    allowed = {
        "name desc",
        "name asc",
        "sector desc",
        "sector asc",
        "updatedAt asc",
        "updatedAt desc",
    }
    assert captured["params"]["sorting"] in allowed


def test_a_timeout_is_always_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    """This runs in the startup path; an untimed request would hang boot."""
    captured = _stub(monkeypatch, _Response(_rows(1)))
    count_agencies(_settings())

    assert captured["timeout"] > 0


# --------------------------------------------------------------------------
# count_agencies
# --------------------------------------------------------------------------


@pytest.mark.parametrize("total", [1, 2, 7])
def test_the_reported_total_is_returned(
    monkeypatch: pytest.MonkeyPatch, total: int
) -> None:
    _stub(monkeypatch, _Response(_rows(total)))
    assert count_agencies(_settings()) == total


def test_an_empty_result_is_zero_agencies(monkeypatch: pytest.MonkeyPatch) -> None:
    """A CKB with no agency yet. Nothing can starve, so it is not a
    violation — this service will simply have nothing to export."""
    _stub(monkeypatch, _Response([]))
    assert count_agencies(_settings()) == 0


def test_a_missing_total_is_unavailable_not_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The most important negative case in this file.

    Falling back to len(rows) would be the obvious thing to write and would
    silently disable the whole check: page_size is 1, so the length is 1
    whether there is one agency or fifty.
    """
    _stub(monkeypatch, _Response([{"base_id": "agency-1"}]))
    with pytest.raises(AgencyCountUnavailableError, match="total"):
        count_agencies(_settings())


@pytest.mark.parametrize("row", [{"total": "many"}, {"total": None}])
def test_a_non_numeric_total_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, row: dict[str, object]
) -> None:
    _stub(monkeypatch, _Response([row]))
    with pytest.raises(AgencyCountUnavailableError):
        count_agencies(_settings())


def test_a_connection_error_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _raiser(monkeypatch, requests.ConnectionError("resql is not up"))
    with pytest.raises(AgencyCountUnavailableError, match="could not reach Resql"):
        count_agencies(_settings())


def test_a_timeout_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _raiser(monkeypatch, requests.Timeout("too slow"))
    with pytest.raises(AgencyCountUnavailableError):
        count_agencies(_settings())


def test_an_http_error_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, _Response(status=500))
    with pytest.raises(AgencyCountUnavailableError):
        count_agencies(_settings())


def test_a_non_json_body_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, _Response(body_is_json=False))
    with pytest.raises(AgencyCountUnavailableError, match="non-JSON"):
        count_agencies(_settings())


@pytest.mark.parametrize("payload", ["a string", 42, None])
def test_a_non_list_payload_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    _stub(monkeypatch, _Response(payload))
    with pytest.raises(AgencyCountUnavailableError, match="list of agencies"):
        count_agencies(_settings())


def test_a_ruuter_style_envelope_is_unwrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resql returns rows directly; the envelope is accepted in case this
    ever moves behind an internal endpoint."""
    _stub(monkeypatch, _Response({"response": _rows(1)}))
    assert count_agencies(_settings()) == 1


# --------------------------------------------------------------------------
# check_single_agency_at_startup — boot-tolerant
# --------------------------------------------------------------------------


@pytest.mark.parametrize("total", [0, 1])
def test_startup_passes_for_at_most_one_agency(
    monkeypatch: pytest.MonkeyPatch, total: int
) -> None:
    _stub(monkeypatch, _Response(_rows(total)))
    assert check_single_agency_at_startup(_settings()) == total


def test_startup_refuses_for_more_than_one_agency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub(monkeypatch, _Response(_rows(2)))
    with pytest.raises(MultipleAgenciesError) as excinfo:
        check_single_agency_at_startup(_settings())

    message = str(excinfo.value)
    assert "2 agencies" in message
    # The message has to explain the mechanism, because "only one agency is
    # supported" invites someone to try it anyway and see nothing break.
    assert "busy" in message
    assert "never be exported" in message


def test_startup_warns_and_continues_when_resql_is_unreachable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Load-bearing. A hard assert here would couple this container's boot to
    Resql being up, which neither compose (no depends_on) nor Kubernetes
    guarantees — so a transient Resql restart would become a
    CrashLoopBackOff, and a service that will not boot is worse than one that
    boots and warns."""
    _raiser(monkeypatch, requests.ConnectionError("resql is not up yet"))

    with caplog.at_level(logging.WARNING, logger="exporter.services.agency_guard"):
        assert check_single_agency_at_startup(_settings()) is None

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "could not confirm" in message
    # An operator reading it should know the check is not simply skipped.
    assert "every export" in message


def test_startup_does_not_tolerate_a_definite_wrong_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tolerance is for "I could not find out", never for "I found out and it
    is wrong"."""
    _stub(monkeypatch, _Response(_rows(3)))
    with pytest.raises(MultipleAgenciesError):
        check_single_agency_at_startup(_settings())


def test_startup_records_the_passing_count(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _stub(monkeypatch, _Response(_rows(1)))
    with caplog.at_level(logging.INFO, logger="exporter.services.agency_guard"):
        check_single_agency_at_startup(_settings())
    assert any("agency count check passed" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------
# require_single_agency — authoritative, for Stage F
# --------------------------------------------------------------------------


@pytest.mark.parametrize("total", [0, 1])
def test_run_start_passes_for_at_most_one_agency(
    monkeypatch: pytest.MonkeyPatch, total: int
) -> None:
    _stub(monkeypatch, _Response(_rows(total)))
    assert require_single_agency(_settings()) == total


def test_run_start_fails_for_more_than_one_agency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub(monkeypatch, _Response(_rows(2)))
    with pytest.raises(MultipleAgenciesError):
        require_single_agency(_settings())


def test_run_start_does_not_tolerate_an_unreachable_resql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The asymmetry with startup, asserted. At run start the export needs
    the agency list regardless, so there is nothing to tolerate — and a
    failed run is visible, which silent starvation is not."""
    _raiser(monkeypatch, requests.ConnectionError("resql is down"))
    with pytest.raises(AgencyCountUnavailableError):
        require_single_agency(_settings())
