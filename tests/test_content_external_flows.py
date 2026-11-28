"""Static checks: content-external reads CKB only through Ruuter-internal.

Two team rules, enforced here rather than by review:

1. No service calls Resql directly. content-external's three reads go through
   DSL/Ruuter.internal/ckb/GET/content-external/*.yml, and nothing under
   content-external/exporter/ names Resql's address.
2. No hardcoded endpoints. No host or port literal under
   content-external/exporter/; endpoints come from constants.ini.

The flow checks guard the Ruuter behaviours that fail silently: a param
missing from `allowlist: params` is dropped without an error, a Resql
400/500 comes back as HTTP 200 unless the status is checked, and a response
over 256 KB is lost unless the step sets `limit:`.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from exporter.services.agency_guard import STARTUP_TIMEOUT_SECONDS
from exporter.services.ckb_ruuter import (
    AGENCIES_PATH,
    DOCUMENTS_FLOW_LIMIT_KB,
    DOCUMENTS_PATH,
    EXPORT_FLOW_TIMEOUT_SECONDS,
    FLOW_PARAMS,
    MAX_EXPORT_PAGE_SIZE,
    MIN_QUERY_TIMEOUT_SECONDS,
    WATERMARK_PATH,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FLOWS_DIR = REPO_ROOT / "DSL" / "Ruuter.internal" / "ckb" / "GET"
RESQL_DIR = REPO_ROOT / "DSL" / "Resql" / "ckb" / "GET"
CONSTANTS = REPO_ROOT / "constants.ini"
EXPORTER = REPO_ROOT / "content-external" / "exporter"

# Each Python path, the Resql query its flow wraps, and the SMALLEST read
# timeout the Python can give it (seconds) — the flow's step timeout must be
# below it. For the export flows that is the lowest CKB_QUERY_TIMEOUT startup
# accepts, not its default, so no permitted setting can undercut Ruuter.
FLOWS: dict[str, tuple[str, float]] = {
    AGENCIES_PATH: ("agency/list_agencies", STARTUP_TIMEOUT_SECONDS),
    WATERMARK_PATH: (
        "agency/get_agency_export_watermark",
        MIN_QUERY_TIMEOUT_SECONDS,
    ),
    DOCUMENTS_PATH: (
        "source_file/list_agency_export_documents",
        MIN_QUERY_TIMEOUT_SECONDS,
    ),
}

# Measured: a 1,000-row page is ~0.81 MB (RUNBOOK.md, 2026-10-21).
_BYTES_PER_ROW = 810

# The flow's camelCase param -> Resql's snake_case one.
_SNAKE = {
    "page": "page",
    "pageSize": "page_size",
    "sorting": "sorting",
    "agencyBaseId": "agency_base_id",
    "afterBaseId": "after_base_id",
}


def _flow(path: str) -> dict[str, Any]:
    return yaml.safe_load((FLOWS_DIR / f"{path.lstrip('/')}.yml").read_text("utf-8"))


def _resql_step(flow: dict[str, Any]) -> dict[str, Any]:
    calls = [
        step
        for name, step in flow.items()
        if name != "declaration" and step.get("call") == "http.get"
    ]
    assert len(calls) == 1, "each flow makes exactly one Resql call"
    return calls[0]


def _resql_params(query: str) -> set[str]:
    header = (RESQL_DIR / f"{query}.sql").read_text("utf-8").split("*/", 1)[0]
    declaration = yaml.safe_load(header.split("/*", 1)[1])["declaration"]
    return {field["field"] for field in declaration["allowlist"]["query"]}


def _constants() -> set[str]:
    return {
        line.split("=", 1)[0]
        for line in CONSTANTS.read_text("utf-8").splitlines()
        if "=" in line
    }


@pytest.mark.parametrize("path", FLOWS)
def test_the_flow_allowlists_exactly_the_params_the_python_sends(path: str) -> None:
    """`params:`, not `query:`: Ruuter reads only params/body/headers, so a
    `query:` allowlist filters nothing. And a param the Python sends but the
    flow does not list is dropped without an error."""
    allowlist = _flow(path)["declaration"]["allowlist"]
    assert "query" not in allowlist
    assert {field["field"] for field in allowlist["params"]} == set(FLOW_PARAMS[path])


@pytest.mark.parametrize("path", FLOWS)
def test_the_flow_passes_resql_exactly_its_declared_params(path: str) -> None:
    query, _ = FLOWS[path]
    step = _resql_step(_flow(path))
    assert step["args"]["url"] == f"[#CKB_RESQL]/{query}"
    assert set(step["args"]["query"]) == _resql_params(query)
    assert set(step["args"]["query"]) == {_SNAKE[p] for p in FLOW_PARAMS[path]}


@pytest.mark.parametrize("path", FLOWS)
def test_every_param_is_required_before_the_call(path: str) -> None:
    """A null value in a `query:` map is an NPE inside Ruuter, which surfaces
    as a bare 500 the Python would not retry. A 400 names the param."""
    flow = _flow(path)
    conditions = " ".join(
        branch["condition"] for branch in flow["checkParams"]["switch"]
    )
    for param in FLOW_PARAMS[path]:
        assert f"!{param}}}" in conditions
    returns = {
        name: step for name, step in flow.items() if name.startswith("returnMissing")
    }
    assert len(returns) == len(FLOW_PARAMS[path])
    assert all(step["status"] == 400 for step in returns.values())


@pytest.mark.parametrize("path", FLOWS)
def test_a_resql_error_status_becomes_a_502(path: str) -> None:
    """CKB's httpCodesAllowList lets a Resql 400/401/403/500 through as a
    normal result, so `error:` alone would hand the caller HTTP 200 with the
    reason phrase. The flow checks the status itself."""
    flow = _flow(path)
    step = _resql_step(flow)
    assert step["error"] == "assignUpstreamError"
    assert step["next"] == "checkStatus"
    condition = flow["checkStatus"]["switch"][0]["condition"]
    assert "200 <= res.response.statusCodeValue" in condition
    assert "res.response.statusCodeValue < 300" in condition
    assert flow["checkStatus"]["switch"][0]["next"] == "returnSuccess"
    assert flow["checkStatus"]["next"] == "assignUpstreamError"
    assert flow["returnUpstreamError"]["status"] == 502
    assert flow["returnSuccess"]["return"] == "${res.response.body}"


@pytest.mark.parametrize("path", FLOWS)
def test_ruuter_answers_before_the_python_gives_up(path: str) -> None:
    """The step timeout (ms) must be below the Python's read timeout, so a
    slow query surfaces as Ruuter's 500 — never retried — rather than as a
    Python timeout racing a still-running Resql step."""
    _, python_timeout = FLOWS[path]
    assert _resql_step(_flow(path))["timeout"] < python_timeout * 1000


@pytest.mark.parametrize("path", [WATERMARK_PATH, DOCUMENTS_PATH])
def test_the_export_flows_timeout_is_the_one_the_python_is_bounded_by(
    path: str,
) -> None:
    """CKB_QUERY_TIMEOUT's floor is derived from EXPORT_FLOW_TIMEOUT_SECONDS;
    if the YAML changes without the constant, the floor protects nothing."""
    step_ms = _resql_step(_flow(path))["timeout"]
    assert step_ms == EXPORT_FLOW_TIMEOUT_SECONDS * 1000


def test_the_largest_permitted_page_fits_the_flows_limit_twice_over() -> None:
    """EXPORT_PAGE_SIZE's ceiling against the flow's real `limit:`, with 2x
    headroom for rows longer than the measured average."""
    limit_kb = _resql_step(_flow(DOCUMENTS_PATH))["limit"]
    assert limit_kb == DOCUMENTS_FLOW_LIMIT_KB
    assert MAX_EXPORT_PAGE_SIZE * _BYTES_PER_ROW * 2 <= limit_kb * 1024


def test_the_document_page_fits_ruuters_response_buffer() -> None:
    """Ruuter buffers 256 KB of a response by default; a 1,000-row page is
    ~0.81 MB. Without `limit:` (KB) every page fails with
    DataBufferLimitException and the flow answers 500 (verified against a
    local ruuter-internal, 2026-11-28). Logging off, or each page writes
    ~0.8 MB of log."""
    step = _resql_step(_flow(DOCUMENTS_PATH))
    assert step["limit"] >= 4096
    assert step["logging"] == {"request": False, "response": False}


@pytest.mark.parametrize("path", FLOWS)
def test_every_placeholder_is_defined(path: str) -> None:
    text = (FLOWS_DIR / f"{path.lstrip('/')}.yml").read_text("utf-8")
    assert set(re.findall(r"\[#(\w+)\]", text)) <= _constants()


def test_constants_ini_defines_the_exporters_endpoints() -> None:
    assert {"CKB_RUUTER_INTERNAL", "CKB_VAULT", "CKB_RESQL"} <= _constants()


# --- the architecture rules, over the exporter's own source ------------------

_SOURCES = sorted(EXPORTER.rglob("*.py"))

# http(s):// followed by a host. The https-only checks name the scheme alone.
_HOST_LITERAL = re.compile(r"https?://[A-Za-z0-9.\-]+")


def test_the_exporter_sources_are_found() -> None:
    assert len(_SOURCES) > 20


@pytest.mark.parametrize("source", _SOURCES, ids=lambda p: p.name)
def test_no_service_calls_resql_directly(source: Path) -> None:
    text = source.read_text("utf-8")
    for forbidden in ("ckb_resql", "CKB_RESQL", "resql-ckb", ":8090"):
        assert forbidden not in text, f"{source.name} names Resql's address"


@pytest.mark.parametrize("source", _SOURCES, ids=lambda p: p.name)
def test_no_endpoint_is_hardcoded(source: Path) -> None:
    """Endpoints come from constants.ini (or the environment), never code."""
    found = _HOST_LITERAL.findall(source.read_text("utf-8"))
    assert found == [], f"{source.name} hardcodes {found}"
