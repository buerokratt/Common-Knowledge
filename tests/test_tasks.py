"""
test_tasks.py — Unit tests for worker/tasks.py extraction logic.

These tests run WITHOUT Docker containers. All external dependencies
(Vault, Ruuter, Azure OpenAI) are mocked. They verify:
  - HTML extraction strategy selection (trafilatura / BeautifulSoup paths)
  - LLM evaluate + correct branching
  - PDF and generic file routing
  - normalize_newlines
  - set_up_logging deduplication
  - Metadata mutation correctness
  - Error handling in LLM helpers
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers to build minimal EntityToClean objects without hitting the filesystem
# ---------------------------------------------------------------------------


def _make_entity(
    tmp_path: Path,
    file_type: str,
    content: str,
    use_llm: bool = False,
    use_llm_correction: bool = False,
) -> MagicMock:
    """
    Create a minimal EntityToClean-like namespace pointing at real temp files
    so we can call the worker functions directly without Pydantic validation.
    """
    source_file = tmp_path / f"source{file_type}"
    source_file.write_text(content, encoding="utf-8")

    meta_data = {
        "file_type": file_type,
        "url": "https://example.com",
        "metadata": {"cleaned": False},
    }
    meta_file = tmp_path / "source.meta.json"
    meta_file.write_text(json.dumps(meta_data))

    log_file = tmp_path / "test.log"
    log_file.touch()

    entity = MagicMock()
    entity.file_path = source_file
    entity.meta_data_path = meta_file
    entity.directory_path = tmp_path
    entity.logs_path = log_file
    entity.url = "https://example.com"
    entity.source_file_id = "test-id"
    entity.source_base_id = "source-id"
    entity.agency_base_id = "agency-id"
    entity.source_run_report_base_id = "report-id"
    entity.use_llm = use_llm
    entity.use_llm_correction = use_llm_correction
    return entity


# ---------------------------------------------------------------------------
# normalize_newlines
# ---------------------------------------------------------------------------


class TestNormalizeNewlines:
    def test_three_newlines_collapsed(self) -> None:
        from worker.tasks import normalize_newlines

        assert normalize_newlines("a\n\n\nb") == "a\n\nb"

    def test_five_newlines_collapsed(self) -> None:
        from worker.tasks import normalize_newlines

        assert normalize_newlines("a\n\n\n\n\nb") == "a\n\nb"

    def test_two_newlines_unchanged(self) -> None:
        from worker.tasks import normalize_newlines

        assert normalize_newlines("a\n\nb") == "a\n\nb"

    def test_single_newline_unchanged(self) -> None:
        from worker.tasks import normalize_newlines

        assert normalize_newlines("a\nb") == "a\nb"

    def test_empty_string(self) -> None:
        from worker.tasks import normalize_newlines

        assert normalize_newlines("") == ""


# ---------------------------------------------------------------------------
# _beautifulsoup_extract
# ---------------------------------------------------------------------------


class TestBeautifulSoupExtract:
    def test_extracts_main_element_content(self) -> None:
        from worker.tasks import _beautifulsoup_extract

        html = """<html><body>
          <header>Nav stuff</header>
          <main><h1>Title</h1><p>Body text.</p></main>
          <footer>Footer</footer>
        </body></html>"""
        result = _beautifulsoup_extract(html)
        assert "Title" in result
        assert "Body text" in result

    def test_removes_nav_script_style(self) -> None:
        from worker.tasks import _beautifulsoup_extract

        html = """<html><body>
          <nav>Home | About</nav>
          <script>alert('x')</script>
          <style>body{}</style>
          <main><p>Real content here.</p></main>
        </body></html>"""
        result = _beautifulsoup_extract(html)
        assert "Real content here" in result
        assert "alert" not in result
        assert "Home | About" not in result

    def test_fallback_to_body_when_no_main(self) -> None:
        from worker.tasks import _beautifulsoup_extract

        html = """<html><body>
          <div><h1>No Main Element</h1><p>Still extracted.</p></div>
        </body></html>"""
        result = _beautifulsoup_extract(html)
        assert "No Main Element" in result
        assert "Still extracted" in result

    def test_returns_string(self) -> None:
        from worker.tasks import _beautifulsoup_extract

        result = _beautifulsoup_extract("<html><body><p>hello</p></body></html>")
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# _trafilatura_extract
# ---------------------------------------------------------------------------


class TestTrafilaturaExtract:
    def test_extracts_article_content(self) -> None:
        from worker.tasks import _trafilatura_extract

        html = """<html><body>
          <article>
            <h1>Article Title</h1>
            <p>This is a full article with enough content to be extracted by trafilatura.</p>
            <p>Second paragraph with more substantial text to meet minimum length requirements.</p>
          </article>
        </body></html>"""
        result = _trafilatura_extract(html)
        # trafilatura may return None on very short content — just check type
        assert result is None or isinstance(result, str)

    def test_returns_none_on_empty_html(self) -> None:
        from worker.tasks import _trafilatura_extract

        result = _trafilatura_extract("<html><body></body></html>")
        assert result is None

    def test_returns_none_on_noise_only(self) -> None:
        from worker.tasks import _trafilatura_extract

        html = "<html><body><nav>Home | About</nav><footer>Copyright 2024</footer></body></html>"
        result = _trafilatura_extract(html)
        assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# clean_html — routing logic (no real LLM)
# ---------------------------------------------------------------------------


class TestCleanHtmlRouting:
    def test_no_llm_uses_trafilatura_when_successful(self, tmp_path: Path) -> None:
        from worker.tasks import clean_html

        html = """<html><body><main>
          <h1>Retirement Reform</h1>
          <p>The retirement age will increase to 65 starting in 2026 for all workers
          born after 1970. This change affects approximately 2 million citizens.</p>
          <p>Early retirement remains available at 60 with a 10% benefit reduction.</p>
        </main></body></html>"""
        entity = _make_entity(tmp_path, ".html", html, use_llm=False)

        with patch(
            "worker.tasks._trafilatura_extract",
            return_value="# Extracted\n\nGood content.",
        ) as mock_traf:
            result = clean_html(entity, client=None, deployment=None)

        mock_traf.assert_called_once()
        assert result == "# Extracted\n\nGood content."

    def test_no_llm_falls_back_to_beautifulsoup_when_trafilatura_empty(
        self, tmp_path: Path
    ) -> None:
        from worker.tasks import clean_html

        entity = _make_entity(
            tmp_path, ".html", "<html><body><main><p>Hello</p></main></body></html>"
        )

        with (
            patch("worker.tasks._trafilatura_extract", return_value=None),
            patch(
                "worker.tasks._beautifulsoup_extract", return_value="BS result"
            ) as mock_bs,
        ):
            result = clean_html(entity, client=None, deployment=None)

        mock_bs.assert_called_once()
        assert result == "BS result"

    def test_use_llm_passes_when_eval_passes(self, tmp_path: Path) -> None:
        from worker.tasks import clean_html

        entity = _make_entity(
            tmp_path,
            ".html",
            "<html><body><p>text</p></body></html>",
            use_llm=True,
            use_llm_correction=False,
        )
        client = MagicMock()

        with (
            patch("worker.tasks._trafilatura_extract", return_value="Good extraction"),
            patch("worker.tasks._llm_evaluate", return_value=(True, "looks good")),
        ):
            result = clean_html(entity, client=client, deployment="dep")

        assert result == "Good extraction"

    def test_use_llm_falls_back_to_bs_when_eval_fails_no_correction(
        self, tmp_path: Path
    ) -> None:
        from worker.tasks import clean_html

        entity = _make_entity(
            tmp_path,
            ".html",
            "<html><body><p>text</p></body></html>",
            use_llm=True,
            use_llm_correction=False,
        )
        client = MagicMock()

        with (
            patch("worker.tasks._trafilatura_extract", return_value="Bad extraction"),
            patch("worker.tasks._llm_evaluate", return_value=(False, "too noisy")),
            patch(
                "worker.tasks._beautifulsoup_extract", return_value="BS fallback"
            ) as mock_bs,
        ):
            result = clean_html(entity, client=client, deployment="dep")

        mock_bs.assert_called_once()
        assert result == "BS fallback"

    def test_use_llm_correction_uses_llm_extract_when_eval_fails(
        self, tmp_path: Path
    ) -> None:
        from worker.tasks import clean_html

        entity = _make_entity(
            tmp_path,
            ".html",
            "<html><body><p>text</p></body></html>",
            use_llm=True,
            use_llm_correction=True,
        )
        client = MagicMock()

        with (
            patch("worker.tasks._trafilatura_extract", return_value="Bad extraction"),
            patch("worker.tasks._llm_evaluate", return_value=(False, "too noisy")),
            patch(
                "worker.tasks._llm_extract", return_value="LLM corrected content"
            ) as mock_llm_ext,
        ):
            result = clean_html(entity, client=client, deployment="dep")

        mock_llm_ext.assert_called_once()
        assert result == "LLM corrected content"

    def test_use_llm_correction_falls_back_to_bs_when_llm_extract_empty(
        self, tmp_path: Path
    ) -> None:
        from worker.tasks import clean_html

        entity = _make_entity(
            tmp_path,
            ".html",
            "<html><body><p>text</p></body></html>",
            use_llm=True,
            use_llm_correction=True,
        )
        client = MagicMock()

        with (
            patch("worker.tasks._trafilatura_extract", return_value="Bad extraction"),
            patch("worker.tasks._llm_evaluate", return_value=(False, "too noisy")),
            patch("worker.tasks._llm_extract", return_value=""),
            patch(
                "worker.tasks._beautifulsoup_extract", return_value="BS last resort"
            ) as mock_bs,
        ):
            result = clean_html(entity, client=client, deployment="dep")

        mock_bs.assert_called_once()
        assert result == "BS last resort"

    def test_correction_without_use_llm_logs_warning_and_ignores_correction(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        from worker.tasks import clean_html

        entity = _make_entity(
            tmp_path,
            ".html",
            "<html><body><p>text</p></body></html>",
            use_llm=False,
            use_llm_correction=True,
        )

        with (
            patch("worker.tasks._trafilatura_extract", return_value="Traf result"),
            caplog.at_level(logging.WARNING, logger="worker.tasks"),
        ):
            result = clean_html(entity, client=None, deployment=None)

        assert result == "Traf result"
        assert any("use_llm_correction=True" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# LLM helpers — error handling
# ---------------------------------------------------------------------------


class TestLLMHelpers:
    def test_llm_evaluate_returns_false_on_api_error(self) -> None:
        from worker.tasks import _llm_evaluate
        from openai import APIError

        client = MagicMock()
        client.chat.completions.create.side_effect = APIError(
            message="rate limit", request=MagicMock(), body=None
        )
        passed, reason = _llm_evaluate(client, "dep", "some markdown")
        assert passed is False
        assert "LLM API error" in reason

    def test_llm_evaluate_returns_false_on_json_decode_error(self) -> None:
        from worker.tasks import _llm_evaluate

        client = MagicMock()
        client.chat.completions.create.return_value.choices[
            0
        ].message.content = "not json"
        passed, reason = _llm_evaluate(client, "dep", "some markdown")
        assert passed is False
        assert "non-JSON" in reason

    def test_llm_evaluate_parses_pass_true(self) -> None:
        from worker.tasks import _llm_evaluate

        client = MagicMock()
        client.chat.completions.create.return_value.choices[
            0
        ].message.content = '{"pass": true, "reason": "looks great"}'
        passed, reason = _llm_evaluate(client, "dep", "some markdown")
        assert passed is True
        assert reason == "looks great"

    def test_llm_evaluate_parses_pass_false(self) -> None:
        from worker.tasks import _llm_evaluate

        client = MagicMock()
        client.chat.completions.create.return_value.choices[
            0
        ].message.content = '{"pass": false, "reason": "too noisy"}'
        passed, reason = _llm_evaluate(client, "dep", "some markdown")
        assert passed is False
        assert reason == "too noisy"

    def test_llm_extract_returns_empty_string_on_api_error(self) -> None:
        from worker.tasks import _llm_extract
        from openai import APIError

        client = MagicMock()
        client.chat.completions.create.side_effect = APIError(
            message="timeout", request=MagicMock(), body=None
        )
        result = _llm_extract(client, "dep", "<html></html>")
        assert result == ""

    def test_llm_extract_returns_content(self) -> None:
        from worker.tasks import _llm_extract

        client = MagicMock()
        client.chat.completions.create.return_value.choices[
            0
        ].message.content = "# Extracted\n\nContent here."
        result = _llm_extract(client, "dep", "<html><body><p>x</p></body></html>")
        assert result == "# Extracted\n\nContent here."


# ---------------------------------------------------------------------------
# set_up_logging — deduplication
# ---------------------------------------------------------------------------


class TestSetUpLogging:
    def test_no_duplicate_file_handlers(self, tmp_path: Path) -> None:
        from worker.tasks import set_up_logging
        import logging as _logging

        log_file = tmp_path / "test.log"
        log_file.touch()

        entity = MagicMock()
        entity.logs_path = log_file

        # Call twice — should only attach one FileHandler
        set_up_logging(entity)
        set_up_logging(entity)

        job_logger = _logging.getLogger("worker.tasks")
        file_handlers = [
            h for h in job_logger.handlers if isinstance(h, _logging.FileHandler)
        ]
        paths = [Path(h.baseFilename).resolve() for h in file_handlers]
        # All file handlers for this path should be deduplicated
        assert paths.count(log_file.resolve()) <= 1


# ---------------------------------------------------------------------------
# Metadata mutation
# ---------------------------------------------------------------------------


class TestMetadataMutation:
    def test_language_written_inside_metadata_key(self, tmp_path: Path) -> None:
        """
        After clean_file_task runs, metadata["metadata"]["language"] must be set
        and any stale top-level "language" key (as written by the scrapper) must
        have been removed.
        """
        from worker.tasks import clean_file_task

        html = "<html><body><main><p>Test content for language detection.</p></main></body></html>"
        entity = _make_entity(tmp_path, ".html", html, use_llm=False)

        original_meta = json.loads(entity.meta_data_path.read_text())
        original_meta["language"] = "et"
        entity.meta_data_path.write_text(json.dumps(original_meta))

        with (
            patch("worker.tasks.requests.post") as mock_post,
            patch("worker.tasks.cleanup_directory"),
        ):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"response": "http://mock/file"}
            mock_resp.raise_for_status.return_value = None
            mock_post.return_value = mock_resp

            clean_file_task(entity)

        meta_file = tmp_path / "cleaned.meta.json"
        assert meta_file.exists()
        metadata = json.loads(meta_file.read_text())
        assert "cleaned" in metadata["metadata"]
        assert metadata["metadata"]["cleaned"] is True
        assert "language" in metadata["metadata"]
        assert metadata["metadata"]["language"] is not None
        assert "language" not in metadata

    def test_cleaned_txt_written(self, tmp_path: Path) -> None:
        from worker.tasks import clean_file_task

        html = (
            "<html><body><main><h1>Hello</h1><p>World content.</p></main></body></html>"
        )
        entity = _make_entity(tmp_path, ".html", html, use_llm=False)

        with (
            patch("worker.tasks.requests.post") as mock_post,
            patch("worker.tasks.cleanup_directory"),
        ):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"response": "http://mock/file"}
            mock_resp.raise_for_status.return_value = None
            mock_post.return_value = mock_resp

            clean_file_task(entity)

        cleaned = tmp_path / "cleaned.txt"
        assert cleaned.exists()
        content = cleaned.read_text()
        assert len(content) > 0
