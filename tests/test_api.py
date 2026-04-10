"""
test_api.py — API contract and validation tests for the cleaning service.

Tests the FastAPI layer against the running container. Focuses on:
  - Service availability (/docs, /openapi.json)
  - Request validation (missing fields, path traversal, non-existent files)
  - Correct HTTP status codes and response shapes
  - /clean_source_async returns immediately (does not block)
  - OpenAPI schema reflects correct defaults
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import requests

from conftest import TEST_SCRAPPED_DIR


def _to_container(host_path: Path) -> str:
    """Translate a host-side path under TEST_SCRAPPED_DIR to the container-side /scrapped-data/... path."""
    rel = host_path.relative_to(TEST_SCRAPPED_DIR)
    return f"/scrapped-data/{rel}"


# ---------------------------------------------------------------------------
# Service availability
# ---------------------------------------------------------------------------

class TestServiceAvailability:
    def test_docs_endpoint_reachable(self, cleaning_url: str) -> None:
        r = requests.get(f"{cleaning_url}/docs", timeout=10)
        assert r.status_code == 200

    def test_openapi_schema_has_both_endpoints(self, cleaning_url: str) -> None:
        r = requests.get(f"{cleaning_url}/openapi.json", timeout=10)
        assert r.status_code == 200
        paths = r.json()["paths"]
        assert "/clean_file" in paths
        assert "/clean_source_async" in paths


# ---------------------------------------------------------------------------
# /clean_file — validation
# ---------------------------------------------------------------------------

class TestCleanFileValidation:
    def test_empty_body_returns_422(self, cleaning_url: str) -> None:
        r = requests.post(f"{cleaning_url}/clean_file", json={}, timeout=10)
        assert r.status_code == 422

    def test_partial_body_returns_422(self, cleaning_url: str) -> None:
        r = requests.post(
            f"{cleaning_url}/clean_file",
            json={"url": "https://example.com", "source_file_id": "abc"},
            timeout=10,
        )
        assert r.status_code == 422

    def test_path_outside_scrapped_data_rejected(self, cleaning_url: str) -> None:
        """Paths outside /scrapped-data must be rejected (path traversal guard)."""
        r = requests.post(
            f"{cleaning_url}/clean_file",
            json={
                "file_path": "/etc/passwd",
                "meta_data_path": "/etc/hosts",
                "directory_path": "/tmp",
                "source_file_id": "x",
                "source_base_id": "x",
                "agency_base_id": "x",
                "source_run_report_base_id": "x",
                "url": "https://example.com",
                "logs_path": "/tmp/x.log",
            },
            timeout=10,
        )
        assert r.status_code == 422

    def test_nonexistent_file_returns_422(self, cleaning_url: str) -> None:
        """FilePath validation must reject paths that don't exist on disk."""
        r = requests.post(
            f"{cleaning_url}/clean_file",
            json={
                "file_path": "/scrapped-data/does/not/exist.html",
                "meta_data_path": "/scrapped-data/does/not/exist.meta.json",
                "directory_path": "/scrapped-data/does/not",
                "source_file_id": "x",
                "source_base_id": "x",
                "agency_base_id": "x",
                "source_run_report_base_id": "x",
                "url": "https://example.com",
                "logs_path": "/scrapped-data/does/not/x.log",
            },
            timeout=10,
        )
        assert r.status_code == 422

    def test_use_llm_defaults_to_false_in_schema(self, cleaning_url: str) -> None:
        r = requests.get(f"{cleaning_url}/openapi.json", timeout=10)
        props = r.json()["components"]["schemas"]["EntityToClean"]["properties"]
        assert props["use_llm"]["default"] is False
        assert props["use_llm_correction"]["default"] is False


# ---------------------------------------------------------------------------
# /clean_source_async — validation and async behaviour
# ---------------------------------------------------------------------------

class TestCleanSourceAsync:
    def test_empty_body_returns_422(self, cleaning_url: str) -> None:
        r = requests.post(f"{cleaning_url}/clean_source_async", json={}, timeout=10)
        assert r.status_code == 422

    def test_valid_empty_file_list_returns_immediately(
        self, cleaning_url: str, scrapped_dir: Path
    ) -> None:
        """
        An empty files list is valid — the endpoint must return 200 immediately
        (well under 2 seconds) because processing is asynchronous.
        """
        log_path = scrapped_dir / "clean.log"
        # logs_path for SourceCleaningTask must be within /scrapped-data (path-traversal guard).
        # Convert from the host-side path to the container-side path via the bind-mount mapping.

        payload = {
            "source_base_id": "src-001",
            "agency_base_id": "agency-001",
            "source_run_report_base_id": "report-001",
            "logs_path": _to_container(log_path),
            "files": [],
            "use_llm": False,
            "use_llm_correction": False,
        }

        start = time.monotonic()
        r = requests.post(f"{cleaning_url}/clean_source_async", json=payload, timeout=10)
        elapsed = time.monotonic() - start

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "started"
        assert body["source_run_report_base_id"] == "report-001"
        assert elapsed < 2.0, f"Async endpoint blocked for {elapsed:.2f}s — expected < 2s"

    def test_response_contains_source_run_report_base_id(
        self, cleaning_url: str, scrapped_dir: Path
    ) -> None:
        payload = {
            "source_base_id": "src-xyz",
            "agency_base_id": "agency-xyz",
            "source_run_report_base_id": "my-report-id",
            "logs_path": _to_container(scrapped_dir / "x.log"),
            "files": [],
        }
        r = requests.post(f"{cleaning_url}/clean_source_async", json=payload, timeout=10)
        assert r.status_code == 200
        assert r.json()["source_run_report_base_id"] == "my-report-id"