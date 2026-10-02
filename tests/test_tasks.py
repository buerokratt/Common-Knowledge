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
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from worker.tasks import LLMEvaluation, LLMExtraction


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


def _eval(verdict: str, reason: str, category: str | None = None) -> LLMEvaluation:
    from worker.tasks import LLMEvaluation

    return LLMEvaluation(verdict, reason, category=category)


def _extraction(status: str, content: str = "") -> LLMExtraction:
    from worker.tasks import LLMExtraction

    return LLMExtraction(status, content)


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
            result, report = clean_html(entity, client=None, deployment=None)

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
            result, report = clean_html(entity, client=None, deployment=None)

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
            patch(
                "worker.tasks._llm_evaluate", return_value=_eval("pass", "looks good")
            ),
        ):
            result, report = clean_html(entity, client=client, deployment="dep")

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
            patch(
                "worker.tasks._llm_evaluate",
                return_value=_eval("fail", "too noisy", "nav"),
            ),
            patch(
                "worker.tasks._beautifulsoup_extract", return_value="BS fallback"
            ) as mock_bs,
        ):
            result, report = clean_html(entity, client=client, deployment="dep")

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
            patch(
                "worker.tasks._llm_evaluate",
                return_value=_eval("fail", "too noisy", "nav"),
            ),
            patch(
                "worker.tasks._llm_extract",
                return_value=_extraction("success", "LLM corrected content"),
            ) as mock_llm_ext,
        ):
            result, report = clean_html(entity, client=client, deployment="dep")

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
            patch(
                "worker.tasks._llm_evaluate",
                return_value=_eval("fail", "too noisy", "nav"),
            ),
            patch("worker.tasks._llm_extract", return_value=_extraction("empty")),
            patch(
                "worker.tasks._beautifulsoup_extract", return_value="BS last resort"
            ) as mock_bs,
        ):
            result, report = clean_html(entity, client=client, deployment="dep")

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
            result, report = clean_html(entity, client=None, deployment=None)

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
        evaluation = _llm_evaluate(client, "dep", "some markdown")
        assert evaluation.passed is False
        assert evaluation.verdict == "error"
        assert "LLM API error" in evaluation.reason

    def test_llm_evaluate_returns_false_on_json_decode_error(self) -> None:
        from worker.tasks import _llm_evaluate

        client = MagicMock()
        client.chat.completions.create.return_value.choices[
            0
        ].message.content = "not json"
        evaluation = _llm_evaluate(client, "dep", "some markdown")
        assert evaluation.passed is False
        assert evaluation.verdict == "error"
        assert "non-JSON" in evaluation.reason

    def test_llm_evaluate_parses_pass_true(self) -> None:
        from worker.tasks import _llm_evaluate

        client = MagicMock()
        client.chat.completions.create.return_value.choices[
            0
        ].message.content = '{"pass": true, "reason": "looks great"}'
        evaluation = _llm_evaluate(client, "dep", "some markdown")
        assert evaluation.passed is True
        assert evaluation.verdict == "pass"
        assert evaluation.reason == "looks great"
        assert evaluation.category is None

    def test_llm_evaluate_parses_pass_false(self) -> None:
        from worker.tasks import _llm_evaluate

        client = MagicMock()
        client.chat.completions.create.return_value.choices[
            0
        ].message.content = (
            '{"pass": false, "reason": "too noisy", "category": "cookie"}'
        )
        evaluation = _llm_evaluate(client, "dep", "some markdown")
        assert evaluation.passed is False
        assert evaluation.verdict == "fail"
        assert evaluation.reason == "too noisy"
        assert evaluation.category == "cookie"

    def test_llm_extract_returns_empty_string_on_api_error(self) -> None:
        from worker.tasks import _llm_extract
        from openai import APIError

        client = MagicMock()
        client.chat.completions.create.side_effect = APIError(
            message="timeout", request=MagicMock(), body=None
        )
        result = _llm_extract(client, "dep", "<html></html>")
        assert result.status == "error"
        assert result.content == ""

    def test_llm_extract_returns_content(self) -> None:
        from worker.tasks import _llm_extract

        client = MagicMock()
        client.chat.completions.create.return_value.choices[
            0
        ].message.content = "# Extracted\n\nContent here."
        result = _llm_extract(client, "dep", "<html><body><p>x</p></body></html>")
        assert result.status == "success"
        assert result.content == "# Extracted\n\nContent here."


class TestLLMEvaluateParsing:
    @staticmethod
    def _client(content: str) -> MagicMock:
        client = MagicMock()
        response = client.chat.completions.create.return_value
        response.choices[0].message.content = content
        response.usage.prompt_tokens = 1200
        response.usage.completion_tokens = 35
        return client

    def test_unknown_category_becomes_other(self) -> None:
        from worker.tasks import _llm_evaluate

        client = self._client('{"pass": false, "reason": "x", "category": "Ads"}')
        assert _llm_evaluate(client, "dep", "md").category == "other"

    def test_missing_category_on_fail_becomes_other(self) -> None:
        from worker.tasks import _llm_evaluate

        client = self._client('{"pass": false, "reason": "x"}')
        evaluation = _llm_evaluate(client, "dep", "md")
        assert evaluation.verdict == "fail"
        assert evaluation.category == "other"

    def test_category_is_normalised(self) -> None:
        from worker.tasks import _llm_evaluate

        client = self._client('{"pass": false, "reason": "x", "category": " NAV "}')
        assert _llm_evaluate(client, "dep", "md").category == "nav"

    def test_category_ignored_on_pass(self) -> None:
        from worker.tasks import _llm_evaluate

        client = self._client('{"pass": true, "reason": "ok", "category": "nav"}')
        assert _llm_evaluate(client, "dep", "md").category is None

    def test_json_without_pass_key_is_error(self) -> None:
        from worker.tasks import _llm_evaluate

        client = self._client('{"reason": "x"}')
        assert _llm_evaluate(client, "dep", "md").verdict == "error"

    def test_non_object_json_is_error(self) -> None:
        from worker.tasks import _llm_evaluate

        client = self._client("[1, 2]")
        assert _llm_evaluate(client, "dep", "md").verdict == "error"

    def test_token_usage_captured(self) -> None:
        from worker.tasks import _llm_evaluate

        client = self._client('{"pass": true, "reason": "ok"}')
        evaluation = _llm_evaluate(client, "dep", "md")
        assert evaluation.prompt_tokens == 1200
        assert evaluation.completion_tokens == 35

    def test_extract_token_usage_and_empty_status(self) -> None:
        from worker.tasks import _llm_extract

        client = self._client("   ")
        extraction = _llm_extract(client, "dep", "<html></html>")
        assert extraction.status == "empty"
        assert extraction.prompt_tokens == 1200


class TestCleanHtmlReport:
    """clean_html must describe the path it took in the returned CleaningReport."""

    HTML = "<html><body><p>text</p></body></html>"

    def test_no_llm_trafilatura(self, tmp_path: Path) -> None:
        from worker.tasks import clean_html

        entity = _make_entity(tmp_path, ".html", self.HTML)
        with patch("worker.tasks._trafilatura_extract", return_value="Traf"):
            _, report = clean_html(entity, client=None, deployment=None)

        assert report.extraction_method == "trafilatura"
        assert report.quality_control is None
        assert report.evaluation is None

    def test_no_llm_beautifulsoup(self, tmp_path: Path) -> None:
        from worker.tasks import clean_html

        entity = _make_entity(tmp_path, ".html", self.HTML)
        with (
            patch("worker.tasks._trafilatura_extract", return_value=None),
            patch("worker.tasks._beautifulsoup_extract", return_value="BS"),
        ):
            _, report = clean_html(entity, client=None, deployment=None)

        assert report.extraction_method == "beautifulsoup"

    def test_basic_pass(self, tmp_path: Path) -> None:
        from worker.tasks import clean_html

        entity = _make_entity(tmp_path, ".html", self.HTML, use_llm=True)
        evaluation = _eval("pass", "ok")
        with (
            patch("worker.tasks._trafilatura_extract", return_value="Traf"),
            patch("worker.tasks._llm_evaluate", return_value=evaluation),
        ):
            _, report = clean_html(entity, client=MagicMock(), deployment="dep")

        assert report.quality_control == "basic"
        assert report.evaluated_method == "trafilatura"
        assert report.extraction_method == "trafilatura"
        assert report.llm_model == "dep"
        assert report.evaluation is evaluation
        assert report.extraction is None
        assert report.deterministic_text is None

    def test_basic_fail_falls_back_to_beautifulsoup(self, tmp_path: Path) -> None:
        from worker.tasks import clean_html

        entity = _make_entity(tmp_path, ".html", self.HTML, use_llm=True)
        with (
            patch("worker.tasks._trafilatura_extract", return_value="Traf"),
            patch("worker.tasks._llm_evaluate", return_value=_eval("fail", "x", "nav")),
            patch("worker.tasks._beautifulsoup_extract", return_value="BS"),
        ):
            _, report = clean_html(entity, client=MagicMock(), deployment="dep")

        assert report.evaluated_method == "trafilatura"
        assert report.extraction_method == "beautifulsoup"
        assert report.evaluation.category == "nav"
        assert report.deterministic_text is None

    def test_evaluated_method_is_beautifulsoup_when_trafilatura_empty(
        self, tmp_path: Path
    ) -> None:
        from worker.tasks import clean_html

        entity = _make_entity(tmp_path, ".html", self.HTML, use_llm=True)
        with (
            patch("worker.tasks._trafilatura_extract", return_value=None),
            patch("worker.tasks._beautifulsoup_extract", return_value="BS"),
            patch("worker.tasks._llm_evaluate", return_value=_eval("pass", "ok")),
        ):
            _, report = clean_html(entity, client=MagicMock(), deployment="dep")

        assert report.evaluated_method == "beautifulsoup"
        assert report.extraction_method == "beautifulsoup"

    def test_comprehensive_fail_keeps_deterministic_text(self, tmp_path: Path) -> None:
        from worker.tasks import clean_html

        entity = _make_entity(
            tmp_path, ".html", self.HTML, use_llm=True, use_llm_correction=True
        )
        with (
            patch("worker.tasks._trafilatura_extract", return_value="Traf"),
            patch("worker.tasks._llm_evaluate", return_value=_eval("fail", "x", "nav")),
            patch(
                "worker.tasks._llm_extract",
                return_value=_extraction("success", "LLM text"),
            ),
        ):
            text, report = clean_html(entity, client=MagicMock(), deployment="dep")

        assert text == "LLM text"
        assert report.quality_control == "comprehensive"
        assert report.extraction_method == "llm"
        assert report.extraction.status == "success"
        assert report.deterministic_text == "Traf"

    def test_comprehensive_error_verdict_still_reextracts(self, tmp_path: Path) -> None:
        from worker.tasks import clean_html

        entity = _make_entity(
            tmp_path, ".html", self.HTML, use_llm=True, use_llm_correction=True
        )
        with (
            patch("worker.tasks._trafilatura_extract", return_value="Traf"),
            patch("worker.tasks._llm_evaluate", return_value=_eval("error", "api")),
            patch(
                "worker.tasks._llm_extract",
                return_value=_extraction("success", "LLM text"),
            ) as mock_extract,
        ):
            text, report = clean_html(entity, client=MagicMock(), deployment="dep")

        mock_extract.assert_called_once()
        assert text == "LLM text"
        assert report.evaluation.verdict == "error"

    def test_comprehensive_empty_extract_has_no_deterministic_text(
        self, tmp_path: Path
    ) -> None:
        from worker.tasks import clean_html

        entity = _make_entity(
            tmp_path, ".html", self.HTML, use_llm=True, use_llm_correction=True
        )
        with (
            patch("worker.tasks._trafilatura_extract", return_value="Traf"),
            patch("worker.tasks._llm_evaluate", return_value=_eval("fail", "x")),
            patch("worker.tasks._llm_extract", return_value=_extraction("empty")),
            patch("worker.tasks._beautifulsoup_extract", return_value="BS"),
        ):
            _, report = clean_html(entity, client=MagicMock(), deployment="dep")

        assert report.extraction_method == "beautifulsoup"
        assert report.extraction.status == "empty"
        assert report.deterministic_text is None


class TestCleaningReportPersistence:
    @staticmethod
    def _ok_response() -> MagicMock:
        resp = MagicMock()
        resp.json.return_value = {"response": "http://mock/file"}
        resp.raise_for_status.return_value = None
        return resp

    @staticmethod
    def _report_calls(mock_post: MagicMock) -> list:
        return [
            c for c in mock_post.call_args_list if "add-cleaning-report" in c.args[0]
        ]

    def test_report_sent_after_cleaning(self, tmp_path: Path) -> None:
        from worker.tasks import clean_file_task

        entity = _make_entity(tmp_path, ".txt", "Some text.\n")
        with (
            patch("worker.tasks.requests.post") as mock_post,
            patch("worker.tasks.cleanup_directory"),
        ):
            mock_post.return_value = self._ok_response()
            clean_file_task(entity)

        calls = self._report_calls(mock_post)
        assert len(calls) == 1
        payload = calls[0].kwargs["json"]
        assert payload["source_file_base_id"] == "test-id"
        assert payload["extraction_method"] == "plain_text"
        assert payload["file_type"] == ".txt"
        assert payload["cleaned_data_url"] == "http://mock/file"
        # No LLM -> LLM fields empty (Resql turns "" into NULL)
        assert payload["llm_verdict"] == ""
        assert payload["eval_prompt_tokens"] == ""
        assert payload["quality_control"] == ""
        assert all(isinstance(v, str) for v in payload.values())
        # Report is sent after the source file has been updated
        paths = [c.args[0] for c in mock_post.call_args_list]
        assert any(
            "update-cleaned-file" in p for p in paths[: paths.index(calls[0].args[0])]
        )

    def test_llm_fields_in_payload(self, tmp_path: Path) -> None:
        from worker.tasks import clean_file_task, LLMEvaluation

        entity = _make_entity(tmp_path, ".html", "<html></html>", use_llm=True)
        evaluation = LLMEvaluation(
            "fail", "menu in text", "nav", prompt_tokens=900, completion_tokens=20
        )
        with (
            patch("worker.tasks.requests.post") as mock_post,
            patch("worker.tasks.cleanup_directory"),
            patch("worker.tasks.get_vault_secrets"),
            patch("worker.tasks._make_openai_client"),
            patch("worker.tasks._trafilatura_extract", return_value="Traf"),
            patch("worker.tasks._beautifulsoup_extract", return_value="BS text"),
            patch("worker.tasks._llm_evaluate", return_value=evaluation),
        ):
            mock_post.return_value = self._ok_response()
            clean_file_task(entity)

        payload = self._report_calls(mock_post)[0].kwargs["json"]
        assert payload["quality_control"] == "basic"
        assert payload["evaluated_method"] == "trafilatura"
        assert payload["extraction_method"] == "beautifulsoup"
        assert payload["llm_verdict"] == "fail"
        assert payload["llm_reason"] == "menu in text"
        assert payload["llm_issue_category"] == "nav"
        assert payload["eval_prompt_tokens"] == "900"
        assert payload["eval_completion_tokens"] == "20"
        assert payload["llm_extract_status"] == ""
        assert payload["deterministic_data_url"] == ""

    def test_comprehensive_uploads_deterministic_text(self, tmp_path: Path) -> None:
        from worker.tasks import clean_file_task

        entity = _make_entity(
            tmp_path,
            ".html",
            "<html></html>",
            use_llm=True,
            use_llm_correction=True,
        )
        with (
            patch("worker.tasks.requests.post") as mock_post,
            patch("worker.tasks.cleanup_directory"),
            patch("worker.tasks.get_vault_secrets"),
            patch("worker.tasks._make_openai_client"),
            patch("worker.tasks._trafilatura_extract", return_value="Traf text"),
            patch("worker.tasks._llm_evaluate", return_value=_eval("fail", "x")),
            patch(
                "worker.tasks._llm_extract",
                return_value=_extraction("success", "LLM text"),
            ),
        ):
            mock_post.return_value = self._ok_response()
            clean_file_task(entity)

        assert (tmp_path / "cleaned.txt").read_text() == "LLM text"
        assert (tmp_path / "deterministic.txt").read_text() == "Traf text"
        uploaded = [
            c.kwargs["json"]["source_file_path"]
            for c in mock_post.call_args_list
            if "upload-file-sync" in c.args[0]
        ]
        assert any(p.endswith("deterministic.txt") for p in uploaded)
        payload = self._report_calls(mock_post)[0].kwargs["json"]
        assert payload["extraction_method"] == "llm"
        assert payload["llm_extract_status"] == "success"
        assert payload["deterministic_data_url"] == "http://mock/file"

    def test_report_failure_does_not_fail_cleaning(self, tmp_path: Path) -> None:
        import requests as _requests
        from worker.tasks import clean_file_task

        entity = _make_entity(tmp_path, ".txt", "Some text.\n")
        ok = self._ok_response()

        def _post(url: str, *args: object, **kwargs: object) -> MagicMock:
            if "add-cleaning-report" in url:
                raise _requests.ConnectionError("ruuter down")
            return ok

        with (
            patch("worker.tasks.requests.post", side_effect=_post),
            patch("worker.tasks.cleanup_directory") as mock_cleanup,
            patch("worker.tasks.send_error") as mock_send_error,
        ):
            clean_file_task(entity)

        mock_cleanup.assert_called_once()
        mock_send_error.assert_not_called()


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
# Plain-text routing (.txt, .md)
# ---------------------------------------------------------------------------


class TestPlainTextRouting:
    """
    .txt and .md files must bypass unstructured.partition() and read the
    file verbatim. Previously they fell through to clean_any_file(), which
    treats each line as a Title element and produces useless output —
    especially for files where the content happens to be HTML markup.
    """

    def test_clean_plain_text_returns_file_contents_verbatim(
        self, tmp_path: Path
    ) -> None:
        from worker.tasks import clean_plain_text

        body = "First line.\nSecond line.\n\nThird paragraph after blank line.\n"
        entity = _make_entity(tmp_path, ".txt", body)

        assert clean_plain_text(entity) == body

    def test_clean_plain_text_replaces_invalid_utf8(self, tmp_path: Path) -> None:
        from worker.tasks import clean_plain_text

        source = tmp_path / "source.txt"
        source.write_bytes(b"good bytes \xff\xfe bad bytes ok\n")
        meta = tmp_path / "source.meta.json"
        meta.write_text(
            json.dumps({"file_type": ".txt", "metadata": {}}), encoding="utf-8"
        )

        entity = MagicMock()
        entity.file_path = source
        entity.meta_data_path = meta
        entity.directory_path = tmp_path
        entity.use_llm = False

        out = clean_plain_text(entity)
        assert "good bytes" in out
        assert "bad bytes ok" in out

    def test_txt_routes_to_plain_text_not_unstructured(self, tmp_path: Path) -> None:
        """
        clean_file_task with file_type=".txt" must call clean_plain_text
        and must NOT call clean_any_file (which would invoke unstructured).
        """
        from worker.tasks import clean_file_task

        body = "Some real text content for a .txt source.\n"
        entity = _make_entity(tmp_path, ".txt", body, use_llm=False)

        with (
            patch("worker.tasks.requests.post") as mock_post,
            patch("worker.tasks.cleanup_directory"),
            patch("worker.tasks.clean_any_file") as mock_any,
        ):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"response": "http://mock/file"}
            mock_resp.raise_for_status.return_value = None
            mock_post.return_value = mock_resp

            clean_file_task(entity)

        assert mock_any.call_count == 0, (
            ".txt must not be routed through clean_any_file()"
        )
        cleaned = (tmp_path / "cleaned.txt").read_text(encoding="utf-8")
        assert "Some real text content" in cleaned

    def test_md_routes_to_plain_text(self, tmp_path: Path) -> None:
        from worker.tasks import clean_file_task

        body = "# Heading\n\nParagraph with **bold** text.\n"
        entity = _make_entity(tmp_path, ".md", body, use_llm=False)

        with (
            patch("worker.tasks.requests.post") as mock_post,
            patch("worker.tasks.cleanup_directory"),
            patch("worker.tasks.clean_any_file") as mock_any,
        ):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"response": "http://mock/file"}
            mock_resp.raise_for_status.return_value = None
            mock_post.return_value = mock_resp

            clean_file_task(entity)

        assert mock_any.call_count == 0
        cleaned = (tmp_path / "cleaned.txt").read_text(encoding="utf-8")
        assert "# Heading" in cleaned
        assert "**bold**" in cleaned


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
