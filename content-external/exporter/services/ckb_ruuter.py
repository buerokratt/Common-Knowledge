"""CKB reads through Ruuter-internal: the only way this service reads CKB's DB.

No service calls Resql directly. Every read goes through a named flow under
DSL/Ruuter.internal/ckb/GET/content-external/, at CKB_RUUTER_INTERNAL from
constants.ini. The flows wrap three read-only Resql queries and nothing else.

WHAT A FLOW ANSWERS, AND WHAT THIS MODULE MAKES OF IT.

  200 {"response": [rows]}   the rows
  200 {"response": "<text>"} an error, never data. A flow that lost its status
                             check would hand back Ruuter's reason phrase
                             ("Bad Request") this way, because CKB's
                             httpCodesAllowList lets a Resql 400/500 through
                             as a normal result
  400                        a required param did not arrive: the flow's
                             allowlist and FLOW_PARAMS below have drifted.
                             Permanent
  502                        Resql answered with an error. Retried
  500, null body             Ruuter aborted the flow: Resql could not be
                             reached, or the step hit its `timeout:`. Never
                             retried, because the query may still be running
                             in Postgres (backoff.is_retryable_ruuter_read)

Errors never carry the URL or a response body (F13): `describe` names the
operation, and the status code or exception type says what went wrong.
"""

from collections.abc import Mapping
from typing import Any

import requests

from exporter.services.backoff import (
    BackoffPolicy,
    is_retryable_ruuter_read,
    retry_with_backoff,
)

__all__ = [
    "AGENCIES_PATH",
    "DOCUMENTS_FLOW_LIMIT_KB",
    "DOCUMENTS_PATH",
    "EXPORT_FLOW_TIMEOUT_SECONDS",
    "FLOW_PARAMS",
    "MAX_EXPORT_PAGE_SIZE",
    "MIN_QUERY_TIMEOUT_SECONDS",
    "WATERMARK_PATH",
    "CkbReadError",
    "get_rows",
]

# Paths under CKB_RUUTER_INTERNAL, which already ends in the project layer
# (/ckb), exactly as cleaning and scrapper append theirs.
AGENCIES_PATH = "/content-external/agencies"
WATERMARK_PATH = "/content-external/export-watermark"
DOCUMENTS_PATH = "/content-external/export-documents"

# The params each flow allowlists. Ruuter drops an unlisted param silently,
# so tests/test_content_external_flows.py asserts each flow's allowlist equals
# this, and the callers send exactly these names.
FLOW_PARAMS: Mapping[str, tuple[str, ...]] = {
    AGENCIES_PATH: ("page", "pageSize", "sorting"),
    WATERMARK_PATH: ("agencyBaseId",),
    DOCUMENTS_PATH: ("agencyBaseId", "afterBaseId", "pageSize"),
}


# The flows' fixed Ruuter step settings, mirrored here so the Python settings
# that depend on them can be bounded at startup. Ruuter DSL cannot read an
# environment variable, so these values live in the YAML;
# tests/test_content_external_flows.py asserts the two stay equal.
#
# `timeout:` on the export-watermark and export-documents steps, in seconds.
EXPORT_FLOW_TIMEOUT_SECONDS = 25.0
# `limit:` on the export-documents step, in KB (Ruuter's default is 256).
DOCUMENTS_FLOW_LIMIT_KB = 8192

# CKB_QUERY_TIMEOUT must exceed the flows' step timeout, so Ruuter always
# answers first (with a 500 that is never retried). Below it, the exporter
# gives up on a query that is still running in Postgres, and the next run
# stacks a second copy on it.
MIN_QUERY_TIMEOUT_SECONDS = EXPORT_FLOW_TIMEOUT_SECONDS + 5.0

# EXPORT_PAGE_SIZE ceiling. A 1,000-row page measured ~0.81 MB (RUNBOOK.md),
# so 5,000 rows is ~4 MB: half of DOCUMENTS_FLOW_LIMIT_KB, leaving headroom
# for longer URLs and titles. A page over the limit fails every run with 500.
MAX_EXPORT_PAGE_SIZE = 5_000

# What a Ruuter 404 or 500 from these flows usually means, named in the error
# because the status alone does not say. Ruuter answers 500 with an empty
# body when the Resql step fails, when a page outgrows the step's `limit:`,
# and when the flow is not deployed at all. Kept short: worker/utils.py cuts
# an error line at 300 characters, and the path comes first.
_RUUTER_FAILURE_HINTS = {
    404: "are the content-external flows deployed in ruuter-internal?",
    500: (
        "Resql down or timed out, page over the flow's limit, or "
        "content-external flows not deployed; see ruuter-internal's log"
    ),
}


class CkbReadError(RuntimeError):
    """A CKB read through Ruuter-internal gave no usable rows."""


def _what_failed(exc: requests.RequestException) -> str:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        hint = _RUUTER_FAILURE_HINTS.get(status)
        return f"HTTP {status}: {hint}" if hint else f"HTTP {status}"
    return type(exc).__name__


def get_rows(
    base_url: str,
    path: str,
    params: Mapping[str, str | int],
    *,
    timeout: float,
    policy: BackoffPolicy,
    describe: str,
) -> list[Any]:
    """GET one Ruuter-internal read flow and return its rows.

    `params` must name exactly the flow's allowlisted params (FLOW_PARAMS), so
    a typo here is a ValueError before anything is sent rather than a param
    Ruuter silently drops.
    """
    expected = FLOW_PARAMS.get(path)
    if expected is None or set(params) != set(expected):
        raise ValueError(
            f"{describe}: params {sorted(params)} do not match the "
            f"allowlist of {path} ({sorted(expected or ())})"
        )
    url = f"{base_url.rstrip('/')}{path}"

    def get() -> requests.Response:
        response = requests.get(url, params=dict(params), timeout=timeout)
        response.raise_for_status()
        return response

    try:
        response = retry_with_backoff(
            get,
            describe=describe,
            policy=policy,
            is_retryable=is_retryable_ruuter_read,
        )
    except requests.RequestException as exc:
        raise CkbReadError(
            f"{describe}: Ruuter-internal read failed ({_what_failed(exc)})"
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise CkbReadError(f"{describe}: Ruuter returned a non-JSON body") from exc

    if isinstance(payload, dict) and "response" in payload:
        payload = payload["response"]
    if isinstance(payload, str):
        # Ruuter's reason phrase standing in for an error status. Never data.
        raise CkbReadError(
            f"{describe}: Ruuter returned an error message instead of rows"
        )
    if not isinstance(payload, list):
        raise CkbReadError(f"{describe}: Ruuter did not return a list")
    return payload
