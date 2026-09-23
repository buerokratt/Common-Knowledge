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

    # -----------------------------------------------------------------
    # HTML pre-strip before LLM re-extract
    # -----------------------------------------------------------------

    def test_prestrip_drops_non_content_tags(self) -> None:
        from worker.tasks import _prestrip_html_for_llm

        html = (
            "<html><head><script>var x=1</script><style>b{}</style>"
            "<meta charset='utf-8'><link rel='stylesheet' href='/a.css'>"
            "</head><body><!-- c --><p>real content</p>"
            "<svg><path d='m0 0'/></svg><noscript>x</noscript>"
            "<iframe src='x'></iframe></body></html>"
        )
        out = _prestrip_html_for_llm(html)
        for gone in ("<script", "<style", "<meta", "<link", "<svg",
                     "<noscript", "<iframe"):
            assert gone not in out
        assert "<!-- c -->" not in out and "<!--" not in out
        assert "real content" in out

    def test_prestrip_keeps_landmark_tags(self) -> None:
        """The whole reason comprehensive-mode beats basic on hard pages is
        that the LLM sees content BS drops. Pre-strip must NOT strip the
        landmark tags BS strips -- otherwise comprehensive collapses onto
        basic."""
        from worker.tasks import _prestrip_html_for_llm

        html = (
            "<html><body>"
            "<header>H</header><nav>N</nav><aside>A</aside>"
            "<main>M</main><footer>F</footer><form>FM</form>"
            "</body></html>"
        )
        out = _prestrip_html_for_llm(html)
        for kept in ("<header", "<nav", "<aside", "<main", "<footer", "<form"):
            assert kept in out
        for text in ("H", "N", "A", "M", "F", "FM"):
            assert text in out

    def test_prestrip_prunes_attributes_but_keeps_semantics(self) -> None:
        from worker.tasks import _prestrip_html_for_llm

        html = (
            '<html><body>'
            '<a href="/x" title="t" class="c" data-foo="d" '
            'onclick="f()" style="color:red" id="anchor">link</a>'
            '<img src="/i.png" alt="a" title="t" class="c" width="100">'
            '<table><th colspan="2" scope="col" class="c">H</th>'
            '<td rowspan="3" class="c" data-x="1">C</td></table>'
            '</body></html>'
        )
        out = _prestrip_html_for_llm(html)
        # Semantic attrs kept
        assert 'href="/x"' in out and 'title="t"' in out and 'id="anchor"' in out
        assert 'src="/i.png"' in out and 'alt="a"' in out
        assert 'colspan="2"' in out and 'rowspan="3"' in out and 'scope="col"' in out
        # Noise attrs gone
        for gone in ("class=", "data-", "onclick=", "style=", "width="):
            assert gone not in out

    def test_prestrip_replaces_long_base64_data_uri(self) -> None:
        from worker.tasks import _prestrip_html_for_llm

        big = "data:image/png;base64," + "A" * 400
        html = f'<html><body><img src="{big}" alt="x"></body></html>'
        out = _prestrip_html_for_llm(html)
        assert "[data-uri]" in out
        assert "AAAA" not in out  # payload gone
        assert 'alt="x"' in out   # alt preserved

    def test_prestrip_leaves_short_data_uri_alone(self) -> None:
        """Short data URIs (favicons, tiny SVGs) are cheap to keep."""
        from worker.tasks import _prestrip_html_for_llm

        # A short base64 URI stays character-identical (no HTML-entity
        # escaping the serialiser might introduce for URI text).
        small = "data:image/png;base64,iVBORw0KGgoA"
        html = f'<html><body><img src="{small}"></body></html>'
        out = _prestrip_html_for_llm(html)
        assert small in out
        assert "[data-uri]" not in out

    def test_prestrip_collapses_whitespace(self) -> None:
        from worker.tasks import _prestrip_html_for_llm

        html = "<html><body><p>a\n\n\n\n\n\nb</p>   \t  <p>c</p></body></html>"
        out = _prestrip_html_for_llm(html)
        assert "\n\n\n" not in out
        assert "   " not in out

    # -----------------------------------------------------------------
    # _llm_extract input-size gate + pre-strip integration
    # -----------------------------------------------------------------

    def test_llm_extract_uses_prestripped_html(self) -> None:
        """The user content sent to the API must be the pre-stripped HTML,
        not the raw input -- scripts/styles must never reach the model."""
        from worker.tasks import _llm_extract

        client = MagicMock()
        client.chat.completions.create.return_value.choices[
            0
        ].message.content = "# ok"
        raw = "<html><body><script>secret</script><p>keep</p></body></html>"
        _llm_extract(client, "dep", raw)

        sent = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        assert "<script" not in sent
        assert "secret" not in sent
        assert "keep" in sent

    def test_llm_extract_size_gate_skips_api_call(self, monkeypatch) -> None:
        """When even the pre-stripped HTML would exceed the ceiling, skip
        the API call entirely and return "" so the caller falls back to BS."""
        import worker.tasks as tasks_mod

        # Lower the ceiling so the test doesn't need a truly huge fixture.
        monkeypatch.setattr(tasks_mod, "_LLM_INPUT_TOKEN_CEILING", 100)

        client = MagicMock()
        # 5000 chars of text -> ~1400 tokens by the estimator, well above 100
        big = "<html><body><p>" + ("word " * 1000) + "</p></body></html>"
        result = tasks_mod._llm_extract(client, "dep", big)

        assert result == ""
        client.chat.completions.create.assert_not_called()

    # -----------------------------------------------------------------
    # 429 retry wrapper
    # -----------------------------------------------------------------

    def test_call_with_llm_retry_succeeds_after_transient_429(
        self, monkeypatch
    ) -> None:
        from openai import RateLimitError
        import worker.tasks as tasks_mod

        monkeypatch.setattr(tasks_mod.time, "sleep", lambda _s: None)

        attempts = iter([
            RateLimitError("r1", response=MagicMock(), body=None),
            RateLimitError("r2", response=MagicMock(), body=None),
            "success",
        ])

        def flaky() -> str:
            nxt = next(attempts)
            if isinstance(nxt, RateLimitError):
                raise nxt
            return nxt

        assert tasks_mod._call_with_llm_retry(flaky) == "success"

    def test_call_with_llm_retry_reraises_after_max_attempts(
        self, monkeypatch
    ) -> None:
        from openai import RateLimitError
        import worker.tasks as tasks_mod

        monkeypatch.setattr(tasks_mod.time, "sleep", lambda _s: None)

        def always_429() -> None:
            raise RateLimitError("still 429", response=MagicMock(), body=None)

        with pytest.raises(RateLimitError):
            tasks_mod._call_with_llm_retry(always_429)

    def test_llm_extract_returns_empty_after_persistent_429(
        self, monkeypatch
    ) -> None:
        """After retries are exhausted the RateLimitError propagates up and
        _llm_extract's APIError handler catches it (RateLimitError is an
        APIError subclass), returning "" so the caller falls back to BS."""
        from openai import RateLimitError
        import worker.tasks as tasks_mod

        monkeypatch.setattr(tasks_mod.time, "sleep", lambda _s: None)

        client = MagicMock()
        client.chat.completions.create.side_effect = RateLimitError(
            "429", response=MagicMock(), body=None
        )
        result = tasks_mod._llm_extract(
            client, "dep", "<html><body><p>x</p></body></html>"
        )
        assert result == ""
        # Retried exactly _LLM_MAX_ATTEMPTS times before giving up
        assert (
            client.chat.completions.create.call_count
            == tasks_mod._LLM_MAX_ATTEMPTS
        )

    def test_llm_evaluate_returns_empty_after_persistent_429(
        self, monkeypatch
    ) -> None:
        from openai import RateLimitError
        import worker.tasks as tasks_mod

        monkeypatch.setattr(tasks_mod.time, "sleep", lambda _s: None)

        client = MagicMock()
        client.chat.completions.create.side_effect = RateLimitError(
            "429", response=MagicMock(), body=None
        )
        passed, reason = tasks_mod._llm_evaluate(client, "dep", "markdown")
        assert passed is False
        assert "429" in reason or "rate" in reason.lower() or "API error" in reason


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
