import json
import logging
import textwrap

import requests
import trafilatura
from bs4 import BeautifulSoup
from markdownify import markdownify
from openai import AzureOpenAI
from unstructured.partition.auto import partition
from unstructured.staging.base import elements_to_markdown

from api.config import settings, get_vault_secrets, VaultSecrets
from api.models import EntityToClean
from worker.utils import catch_error, cleanup_directory

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OpenAI client — created per task using fresh secrets from Vault
# ---------------------------------------------------------------------------

def _make_openai_client(secrets: VaultSecrets) -> AzureOpenAI:
    return AzureOpenAI(
        api_key=secrets.azure_openai_api_key.get_secret_value(),
        api_version=secrets.azure_openai_api_version,
        azure_endpoint=secrets.azure_openai_endpoint,
    )


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _beautifulsoup_extract(html: str) -> str:
    # markdownify converts HTML to Markdown directly, avoiding plain-text output
    return markdownify(html, heading_style="ATX", strip=["script", "style", "nav", "footer"])


def _trafilatura_extract(html: str, url: str | None = None) -> str | None:
    result = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        favor_recall=True,
    )
    return result or None


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

_EVAL_SYSTEM = textwrap.dedent("""
    You are a quality-evaluation assistant for web-page text extraction.
    You will receive a Markdown-formatted extraction of a web page's main body.
    Evaluate it on the following criteria:
      1. Readability – is the text coherent and human-readable?
      2. Completeness – does it appear to contain the full main content
         without obvious truncation or missing sections?
      3. Cleanliness – is it free from navigation menus, cookie banners,
         footer boilerplate, and other noise?
      4. Structure – are headings, lists, and paragraphs logically preserved?

    Reply with ONLY a JSON object in this exact shape (no markdown fences):
    {"pass": true, "reason": "<one-sentence explanation>"}
    or
    {"pass": false, "reason": "<one-sentence explanation>"}
""").strip()

_EXTRACT_SYSTEM = textwrap.dedent("""
    You are an expert web-page content extractor.
    You will receive raw HTML of a web page.
    Extract ONLY the main body content (article text, documentation, etc.).
    Ignore navigation, sidebars, footers, cookie notices, and ads.
    Return the result formatted as clean Markdown (use headings, lists, bold/italic
    where appropriate). Do not include any commentary—only the extracted Markdown.
""").strip()


def _llm_evaluate(client: AzureOpenAI, deployment: str, extracted_markdown: str) -> tuple[bool, str]:
    response = client.chat.completions.create(
        model=deployment,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _EVAL_SYSTEM},
            {"role": "user", "content": f"# Extraction to evaluate\n\n{extracted_markdown}"},
        ],
    )
    raw = response.choices[0].message.content or "{}"
    try:
        result = json.loads(raw)
        return bool(result.get("pass", False)), result.get("reason", "no reason given")
    except json.JSONDecodeError:
        return False, f"LLM returned non-JSON: {raw[:120]}"


def _llm_extract(client: AzureOpenAI, deployment: str, html: str) -> str:
    response = client.chat.completions.create(
        model=deployment,
        temperature=0,
        messages=[
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user", "content": html},
        ],
    )
    return (response.choices[0].message.content or "").strip()


# ---------------------------------------------------------------------------
# HTML cleaning
# ---------------------------------------------------------------------------

def clean_html(entity: EntityToClean, client: AzureOpenAI, deployment: str) -> str:
    with entity.file_path.open("r", encoding="utf-8", errors="replace") as f:
        html = f.read()

    if not entity.use_llm:
        extracted = _trafilatura_extract(html, url=entity.url)
        if extracted:
            logger.info(f"[html] trafilatura succeeded for {entity.url}")
            return extracted
        logger.warning(f"[html] trafilatura empty, falling back to markdownify for {entity.url}")
        return _beautifulsoup_extract(html)

    extracted = _trafilatura_extract(html, url=entity.url)
    if not extracted:
        logger.warning(f"[html] trafilatura empty for {entity.url}; using raw text for LLM path")
        extracted = _beautifulsoup_extract(html)

    logger.info(f"[html] running LLM evaluation for {entity.url}")
    passed, reason = _llm_evaluate(client, deployment, extracted)
    logger.info(f"[html] LLM evaluation {'PASSED' if passed else 'FAILED'} for {entity.url}: {reason}")

    if passed:
        return extracted

    if not entity.use_llm_correction:
        logger.warning(f"[html] LLM evaluation failed but correction disabled for {entity.url}")
        return extracted

    logger.info(f"[html] LLM correction: re-extracting from raw HTML for {entity.url}")
    corrected = _llm_extract(client, deployment, html)
    if corrected:
        return corrected

    logger.warning(f"[html] LLM re-extraction empty; falling back to markdownify for {entity.url}")
    return _beautifulsoup_extract(html)


# ---------------------------------------------------------------------------
# Non-HTML cleaning
# ---------------------------------------------------------------------------

def clean_any_file(entity: EntityToClean) -> str:
    # elements_to_markdown renders Unstructured elements with proper MD formatting:
    # headings, lists, tables, bold/italic, code blocks, etc.
    partitioned = partition(filename=entity.file_path.as_posix(), languages=settings.languages)
    return elements_to_markdown(partitioned)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def set_up_logging(entity: EntityToClean):
    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s"
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Add stdout handler once only (root logger persists across background tasks)
    if not root.handlers:
        stdout_handler = logging.StreamHandler()
        stdout_handler.setFormatter(fmt)
        root.addHandler(stdout_handler)

    # Attach a per-job file handler to this module's logger, avoiding duplicates
    job_logger = logging.getLogger(__name__)
    log_path = entity.logs_path.as_posix()
    for handler in job_logger.handlers:
        if isinstance(handler, logging.FileHandler) and getattr(handler, "baseFilename", None) == log_path:
            break
    else:
        file_handler = logging.FileHandler(log_path)
        file_handler.setFormatter(fmt)
        job_logger.addHandler(file_handler)


# ---------------------------------------------------------------------------
# Main task
# ---------------------------------------------------------------------------

def clean_file_task(entity: EntityToClean):
    with catch_error(entity):
        set_up_logging(entity)
        logger.info(f"Cleaning file {entity.file_path.as_posix()}")

        # Explicit timeout for Ruuter requests to avoid hanging worker threads
        ruuter_timeout = 30  # seconds

        with entity.meta_data_path.open("r") as f:
            metadata = json.load(f)

        if metadata["file_type"] == ".html":
            # Only fetch Vault secrets / create Azure client when needed
            client = None
            deployment_name = None
            if entity.use_llm:
                # Fetch fresh secrets on every task — respects Vault Agent token rotation
                secrets = get_vault_secrets()
                client = _make_openai_client(secrets)
                deployment_name = secrets.azure_openai_deployment
            cleaned_text = clean_html(entity, client, deployment_name)
            logger.info(f"Cleaned as html for {entity.file_path.as_posix()}")
        else:
            cleaned_text = clean_any_file(entity)
            logger.info(f"Cleaned as unstructured file for {entity.file_path.as_posix()}")

        cleaned_text_filename = entity.directory_path / "cleaned.txt"
        with cleaned_text_filename.open("w") as f:
            f.write(cleaned_text)

        r = requests.post(
            f"{settings.ruuter_internal}/ckb/pipeline/upload-file-sync",
            json={"source_file_path": cleaned_text_filename.as_posix()},
            timeout=ruuter_timeout,
        )
        r.raise_for_status()
        try:
            response_json = r.json()
        except ValueError as exc:
            logger.error("Non-JSON response from Ruuter upload-file-sync for cleaned text")
            raise RuntimeError("Invalid JSON response from Ruuter for cleaned text upload") from exc
        if not isinstance(response_json, dict) or "response" not in response_json:
            logger.error("Missing 'response' key in Ruuter response for cleaned text upload: %r", response_json)
            raise RuntimeError("Missing 'response' key in Ruuter response for cleaned text upload")
        uploaded_cleaned_text_url = response_json["response"]
        logger.info(f"Saved cleaned text for {entity.file_path.as_posix()}")

        cleaned_metadata_filename = entity.directory_path / "cleaned.meta.json"
        with cleaned_metadata_filename.open("w") as f:
            metadata["metadata"]["cleaned"] = True
            json.dump(metadata, f)

        r = requests.post(
            f"{settings.ruuter_internal}/ckb/pipeline/upload-file-sync",
            json={"source_file_path": cleaned_metadata_filename.as_posix()},
            timeout=ruuter_timeout,
        )
        r.raise_for_status()
        try:
            response_json = r.json()
        except ValueError as exc:
            logger.error("Non-JSON response from Ruuter upload-file-sync for cleaned metadata")
            raise RuntimeError("Invalid JSON response from Ruuter for cleaned metadata upload") from exc
        if not isinstance(response_json, dict) or "response" not in response_json:
            logger.error(
                "Missing 'response' key in Ruuter response for cleaned metadata upload: %r",
                response_json,
            )
            raise RuntimeError("Missing 'response' key in Ruuter response for cleaned metadata upload")
        uploaded_cleaned_metadata_url = response_json["response"]
        logger.info(f"Saved cleaned metadata for {entity.file_path.as_posix()}")

        r = requests.post(
            f"{settings.ruuter_internal}/ckb/source-file/update-cleaned-file",
            json={
                "base_id": entity.source_file_id,
                "cleaned_data_url": uploaded_cleaned_text_url,
                "cleaned_metadata_url": uploaded_cleaned_metadata_url,
            },
            timeout=ruuter_timeout,
        )
        r.raise_for_status()

        # All uploads confirmed — safe to remove the working directory
        cleanup_directory(entity)