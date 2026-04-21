"""
test_integration.py — End-to-end integration tests against the running containers.

Sends real HTTP requests to cleaning-server-test, which processes actual files
and calls the mock-ruuter stub. Verifies the full pipeline:
  file on disk -> cleaning-server -> extracted text -> mock upload -> mock DB update

All tests require the `cleaning_stack` session fixture (containers running).
LLM-path tests additionally use `require_llm_creds` and are auto-skipped when
Azure credentials are absent.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import requests

from conftest import (
    FIXTURES_DIR,
    TEST_SCRAPPED_DIR,
    make_entity_payload,
    write_test_file,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean_file(
    cleaning_url: str, payload: dict[str, str | bool], timeout: int = 120
) -> requests.Response:
    return requests.post(f"{cleaning_url}/clean_file", json=payload, timeout=timeout)


def _get_calls(mock_ruuter_url: str) -> list[dict[str, object]]:
    r = requests.get(f"{mock_ruuter_url}/calls", timeout=5)
    r.raise_for_status()
    return r.json()


def _reset_calls(mock_ruuter_url: str) -> None:
    requests.get(f"{mock_ruuter_url}/reset", timeout=5)


def _new_calls_since(mock_ruuter_url: str, before: int) -> list[dict[str, object]]:
    return _get_calls(mock_ruuter_url)[before:]


# ---------------------------------------------------------------------------
# HTML — no LLM path
# ---------------------------------------------------------------------------


class TestHTMLCleaningNoLLM:
    def test_article_with_main_returns_200(
        self, cleaning_url: str, scrapped_dir: Path
    ) -> None:
        fixture = (FIXTURES_DIR / "html" / "article_with_main.html").read_text()
        file_path, meta_path = write_test_file(
            scrapped_dir,
            fixture,
            "source.html",
            ".html",
            url="https://example.com/pension",
        )
        r = _clean_file(
            cleaning_url, make_entity_payload(file_path, meta_path, scrapped_dir)
        )
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_article_with_main_produces_cleaned_txt(
        self, cleaning_url: str, scrapped_dir: Path
    ) -> None:
        fixture = (FIXTURES_DIR / "html" / "article_with_main.html").read_text()
        file_path, meta_path = write_test_file(
            scrapped_dir, fixture, "source.html", ".html"
        )
        r = _clean_file(
            cleaning_url, make_entity_payload(file_path, meta_path, scrapped_dir)
        )
        assert r.status_code == 200

        cleaned = scrapped_dir / "cleaned.txt"
        assert cleaned.exists(), "cleaned.txt was not created"
        assert len(cleaned.read_text()) > 0

    def test_article_content_is_extracted_correctly(
        self, cleaning_url: str, scrapped_dir: Path
    ) -> None:
        """Key article content must be present; header/footer noise must not be."""
        fixture = (FIXTURES_DIR / "html" / "article_with_main.html").read_text()
        file_path, meta_path = write_test_file(
            scrapped_dir, fixture, "source.html", ".html"
        )
        _clean_file(
            cleaning_url, make_entity_payload(file_path, meta_path, scrapped_dir)
        )

        content = (scrapped_dir / "cleaned.txt").read_text()
        assert "pension" in content.lower() or "retirement" in content.lower()
        assert "65" in content
        # Footer / script noise must be stripped
        assert "Cookie policy" not in content
        assert "console.log" not in content

    def test_output_is_markdown_not_raw_html(
        self, cleaning_url: str, scrapped_dir: Path
    ) -> None:
        fixture = (FIXTURES_DIR / "html" / "article_with_main.html").read_text()
        file_path, meta_path = write_test_file(
            scrapped_dir, fixture, "source.html", ".html"
        )
        _clean_file(
            cleaning_url, make_entity_payload(file_path, meta_path, scrapped_dir)
        )

        content = (scrapped_dir / "cleaned.txt").read_text()
        assert "<h1>" not in content, "Raw HTML h1 tag found in output"
        assert "<p>" not in content, "Raw HTML p tag found in output"

    def test_no_triple_newlines_in_output(
        self, cleaning_url: str, scrapped_dir: Path
    ) -> None:
        fixture = (FIXTURES_DIR / "html" / "article_with_main.html").read_text()
        file_path, meta_path = write_test_file(
            scrapped_dir, fixture, "source.html", ".html"
        )
        _clean_file(
            cleaning_url, make_entity_payload(file_path, meta_path, scrapped_dir)
        )
        assert "\n\n\n" not in (scrapped_dir / "cleaned.txt").read_text()

    def test_article_without_main_succeeds(
        self, cleaning_url: str, scrapped_dir: Path
    ) -> None:
        fixture = (FIXTURES_DIR / "html" / "article_no_main.html").read_text()
        file_path, meta_path = write_test_file(
            scrapped_dir, fixture, "source.html", ".html"
        )
        r = _clean_file(
            cleaning_url, make_entity_payload(file_path, meta_path, scrapped_dir)
        )
        assert r.status_code == 200

        content = (scrapped_dir / "cleaned.txt").read_text()
        assert "building permit" in content.lower() or "Building Permit" in content

    def test_noisy_page_returns_200(
        self, cleaning_url: str, scrapped_dir: Path
    ) -> None:
        fixture = (FIXTURES_DIR / "html" / "noisy_page.html").read_text()
        file_path, meta_path = write_test_file(
            scrapped_dir, fixture, "source.html", ".html"
        )
        r = _clean_file(
            cleaning_url,
            make_entity_payload(
                file_path, meta_path, scrapped_dir, url="https://example.com/tax"
            ),
        )
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Metadata assertions
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_cleaned_flag_set_inside_metadata_key(
        self, cleaning_url: str, scrapped_dir: Path
    ) -> None:
        fixture = (FIXTURES_DIR / "html" / "article_with_main.html").read_text()
        file_path, meta_path = write_test_file(
            scrapped_dir, fixture, "source.html", ".html"
        )
        r = _clean_file(
            cleaning_url, make_entity_payload(file_path, meta_path, scrapped_dir)
        )
        assert r.status_code == 200

        meta = json.loads((scrapped_dir / "cleaned.meta.json").read_text())
        assert meta["metadata"]["cleaned"] is True

    def test_language_stored_inside_metadata_key(
        self, cleaning_url: str, scrapped_dir: Path
    ) -> None:
        fixture = (FIXTURES_DIR / "html" / "article_with_main.html").read_text()
        file_path, meta_path = write_test_file(
            scrapped_dir, fixture, "source.html", ".html"
        )

        original_meta = json.loads(meta_path.read_text())
        original_meta["language"] = "et"
        meta_path.write_text(json.dumps(original_meta))

        r = _clean_file(
            cleaning_url, make_entity_payload(file_path, meta_path, scrapped_dir)
        )
        assert r.status_code == 200

        meta = json.loads((scrapped_dir / "cleaned.meta.json").read_text())
        assert "language" in meta["metadata"]
        assert meta["metadata"]["language"] is not None
        # The stale top-level key must have been stripped
        assert "language" not in meta
        # Must not be at the top level (old bug)
        assert "language" not in meta


# ---------------------------------------------------------------------------
# Mock Ruuter call verification
# ---------------------------------------------------------------------------


class TestRuuterInteraction:
    def test_two_upload_calls_and_one_update_per_file(
        self, cleaning_url: str, mock_ruuter_url: str, scrapped_dir: Path
    ) -> None:
        _reset_calls(mock_ruuter_url)

        fixture = (FIXTURES_DIR / "html" / "article_with_main.html").read_text()
        file_path, meta_path = write_test_file(
            scrapped_dir, fixture, "source.html", ".html"
        )
        r = _clean_file(
            cleaning_url, make_entity_payload(file_path, meta_path, scrapped_dir)
        )
        assert r.status_code == 200

        calls = _get_calls(mock_ruuter_url)
        uploads = [c for c in calls if "upload-file-sync" in c["path"]]
        updates = [c for c in calls if "update-cleaned-file" in c["path"]]

        assert len(uploads) == 2, f"Expected 2 upload calls, got {len(uploads)}"
        assert len(updates) == 1, f"Expected 1 update call, got {len(updates)}"

    def test_upload_calls_reference_correct_filenames(
        self, cleaning_url: str, mock_ruuter_url: str, scrapped_dir: Path
    ) -> None:
        _reset_calls(mock_ruuter_url)

        fixture = (FIXTURES_DIR / "html" / "article_with_main.html").read_text()
        file_path, meta_path = write_test_file(
            scrapped_dir, fixture, "source.html", ".html"
        )
        r = _clean_file(
            cleaning_url, make_entity_payload(file_path, meta_path, scrapped_dir)
        )
        assert r.status_code == 200

        uploads = [
            c for c in _get_calls(mock_ruuter_url) if "upload-file-sync" in c["path"]
        ]
        uploaded_paths = [c["body"].get("source_file_path", "") for c in uploads]

        assert any(
            "cleaned.txt" in p for p in uploaded_paths
        ), f"cleaned.txt not found in upload paths: {uploaded_paths}"
        assert any(
            "cleaned.meta.json" in p for p in uploaded_paths
        ), f"cleaned.meta.json not found in upload paths: {uploaded_paths}"

    def test_update_call_contains_correct_base_id(
        self, cleaning_url: str, mock_ruuter_url: str, scrapped_dir: Path
    ) -> None:
        _reset_calls(mock_ruuter_url)

        fixture = (FIXTURES_DIR / "html" / "article_with_main.html").read_text()
        file_path, meta_path = write_test_file(
            scrapped_dir, fixture, "source.html", ".html"
        )
        r = _clean_file(
            cleaning_url, make_entity_payload(file_path, meta_path, scrapped_dir)
        )
        assert r.status_code == 200

        updates = [
            c for c in _get_calls(mock_ruuter_url) if "update-cleaned-file" in c["path"]
        ]
        assert updates[0]["body"]["base_id"] == "test-file-id-001"
        assert "cleaned_data_url" in updates[0]["body"]
        assert "cleaned_metadata_url" in updates[0]["body"]


# ---------------------------------------------------------------------------
# Vault / LLM path — only runs when Azure credentials are set
# ---------------------------------------------------------------------------


class TestLLMPath:
    def test_use_llm_eval_path_returns_200(
        self, cleaning_url: str, scrapped_dir: Path, require_llm_creds: None
    ) -> None:
        fixture = (FIXTURES_DIR / "html" / "article_with_main.html").read_text()
        file_path, meta_path = write_test_file(
            scrapped_dir, fixture, "source.html", ".html"
        )
        r = _clean_file(
            cleaning_url,
            make_entity_payload(
                file_path,
                meta_path,
                scrapped_dir,
                use_llm=True,
                use_llm_correction=False,
            ),
            timeout=180,
        )
        assert r.status_code == 200
        assert len((scrapped_dir / "cleaned.txt").read_text()) > 100

    def test_use_llm_correction_path_returns_200(
        self, cleaning_url: str, scrapped_dir: Path, require_llm_creds: None
    ) -> None:
        """LLM correction path on a noisy page must still produce a result."""
        fixture = (FIXTURES_DIR / "html" / "noisy_page.html").read_text()
        file_path, meta_path = write_test_file(
            scrapped_dir,
            fixture,
            "source.html",
            ".html",
            url="https://example.com/tax",
        )
        r = _clean_file(
            cleaning_url,
            make_entity_payload(
                file_path,
                meta_path,
                scrapped_dir,
                url="https://example.com/tax",
                use_llm=True,
                use_llm_correction=True,
            ),
            timeout=180,
        )
        assert r.status_code == 200
        content = (scrapped_dir / "cleaned.txt").read_text()
        assert len(content) > 0

    def test_no_llm_path_does_not_require_vault_secret(
        self, cleaning_url: str, scrapped_dir: Path
    ) -> None:
        """
        The key safety guarantee: use_llm=False must work even when Vault has
        only a placeholder secret. No require_llm_creds — intentionally runs
        in all environments.
        """
        fixture = (FIXTURES_DIR / "html" / "article_with_main.html").read_text()
        file_path, meta_path = write_test_file(
            scrapped_dir, fixture, "source.html", ".html"
        )
        r = _clean_file(
            cleaning_url,
            make_entity_payload(file_path, meta_path, scrapped_dir, use_llm=False),
        )
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# /clean_source_async end-to-end
# ---------------------------------------------------------------------------


class TestCleanSourceAsyncEndToEnd:
    def test_async_batch_processes_single_file(
        self, cleaning_url: str, mock_ruuter_url: str, scrapped_dir: Path
    ) -> None:
        """
        Submit a batch of one file via /clean_source_async.
        The endpoint returns immediately; after a short wait the mock Ruuter
        must have received the update-cleaned-file call.

        SourceCleaningTask.files[].originalDataUrl is passed to _to_local_path()
        which does: Path('/' + url.replace('uploads/', '').lstrip('/'))
        So we must pass the full container path with the leading slash stripped,
        e.g. "scrapped-data/test-123/source.html" -> "/scrapped-data/test-123/source.html"
        """
        _reset_calls(mock_ruuter_url)

        fixture = (FIXTURES_DIR / "html" / "article_with_main.html").read_text()
        file_path, meta_path = write_test_file(
            scrapped_dir,
            fixture,
            "source.html",
            ".html",
            url="https://example.com/async-test",
        )

        def to_container(p: Path) -> str:
            """Convert host path to container-side /scrapped-data/... path."""
            rel = p.relative_to(TEST_SCRAPPED_DIR)
            return f"/scrapped-data/{rel}"

        def to_url(p: Path) -> str:
            """
            _to_local_path does: Path('/' + url.replace('uploads/', '').lstrip('/'))
            So "scrapped-data/foo/bar" -> Path("/scrapped-data/foo/bar")
            """
            rel = p.relative_to(TEST_SCRAPPED_DIR)
            return f"scrapped-data/{rel}"

        # logs_path for SourceCleaningTask is plain Path — container-side
        log_container = to_container(scrapped_dir / "clean-source.log")
        # Touch the log file so it exists for EntityToClean FilePath validation
        (scrapped_dir / "clean-source.log").touch()

        # clean_source_task builds fallback_directory as:
        #   /scrapped-data / agency_base_id / sourceBaseId / baseId
        # EntityToClean.directory_path uses this fallback even when originalDataUrl
        # is provided (because directory_path is always the fallback, not derived
        # from the URL). We must ensure this directory exists inside the container.
        # The easiest way: make the IDs spell out a path that IS our scrapped_dir.
        # scrapped_dir container path = /scrapped-data/test-xxx
        # fallback = /scrapped-data/test-xxx/x/x — create it.
        dir_name = scrapped_dir.name  # e.g. "test-1774339..."
        fallback_sub = scrapped_dir / "async-src" / "async-file"
        fallback_sub.mkdir(parents=True, exist_ok=True)

        payload = {
            "source_base_id": "src-async-001",
            "agency_base_id": dir_name,
            "source_run_report_base_id": "report-async-001",
            "logs_path": log_container,
            "use_llm": False,
            "use_llm_correction": False,
            "files": [
                {
                    "baseId": "async-file",
                    "sourceBaseId": "async-src",
                    "url": "https://example.com/async-test",
                    "originalDataUrl": to_url(file_path),
                    "originalMetadataUrl": to_url(meta_path),
                }
            ],
        }

        start = time.monotonic()
        r = requests.post(
            f"{cleaning_url}/clean_source_async", json=payload, timeout=10
        )
        elapsed = time.monotonic() - start

        assert r.status_code == 200
        assert r.json()["status"] == "started"
        assert elapsed < 2.0, "Async endpoint must return immediately"

        # Wait for the background process to finish
        deadline = time.time() + 90
        while time.time() < deadline:
            calls = _get_calls(mock_ruuter_url)
            if any("update-cleaned-file" in c["path"] for c in calls):
                return
            time.sleep(2)

        pytest.fail("Background cleaning task did not complete within 90s")
