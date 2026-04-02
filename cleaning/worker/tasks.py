import base64
import datetime
import json
import logging
import mimetypes
import re
import textwrap
from pathlib import Path
from urllib.parse import urljoin

import pymupdf4llm
import requests
import trafilatura
from bs4 import BeautifulSoup
from langdetect import detect, LangDetectException
from markdownify import markdownify
from openai import AzureOpenAI, APIError
from pydantic import ValidationError
from unstructured.documents.elements import Title, ListItem, Table, CodeSnippet
from unstructured.partition.auto import partition
from unstructured.partition.html import partition_html

from api.config import settings, get_vault_secrets, VaultSecrets
from api.models import EntityToClean, SourceCleaningTask
from worker.utils import catch_error, cleanup_directory, send_error

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# elements_to_markdown helper
# ---------------------------------------------------------------------------
# unstructured.staging.base.elements_to_markdown was removed in 0.18.x.
# We implement the same logic directly using the element types we care about.

def _elements_to_markdown(elements: list) -> str:
    """
    Convert a list of Unstructured elements to a Markdown string.

    Mapping:
      Title       -> ## heading
      ListItem    -> - bullet
      Table       -> kept as-is (Unstructured renders HTML tables; we pass through)
      CodeSnippet -> fenced code block
      Everything else -> plain paragraph
    """
    lines: list[str] = []
    for el in elements:
        text = str(el).strip()
        if not text:
            continue
        if isinstance(el, Title):
            lines.append(f"## {text}")
        elif isinstance(el, ListItem):
            lines.append(f"- {text}")
        elif isinstance(el, Table):
            # Table text from Unstructured is whitespace-separated cell values;
            # wrap in a code block so it stays readable.
            lines.append(f"```\n{text}\n```")
        elif isinstance(el, CodeSnippet):
            lines.append(f"```\n{text}\n```")
        else:
            lines.append(text)
    return "\n\n".join(lines)



# ---------------------------------------------------------------------------
# Azure OpenAI client -- created per task using fresh Vault secrets
# ---------------------------------------------------------------------------

def _make_openai_client(secrets: VaultSecrets) -> AzureOpenAI:
    return AzureOpenAI(
        api_key=secrets.azure_openai_api_key.get_secret_value(),
        api_version=secrets.azure_openai_api_version,
        azure_endpoint=secrets.azure_openai_endpoint,
    )


# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------

def normalize_newlines(text: str) -> str:
    """Collapse 3+ consecutive newlines down to 2."""
    return re.sub(r'\n{3,}', '\n\n', text)


# ---------------------------------------------------------------------------
# HTML extraction helpers
# ---------------------------------------------------------------------------

def _beautifulsoup_extract(html: str) -> str:
    """
    Multi-step fallback extractor that produces Markdown output.

    Steps:
      1. Strip noisy elements (header, footer, nav, script, style, aside, form).
      2. If a <main> element exists, try partition_html on it; fall back to
         markdownify on that element if partition returns nothing.
      3. Otherwise try partition_html with skip_headers_and_footers on the body
         element (not the full document, to avoid re-processing html/head
         boilerplate); fall back to markdownify if partition returns nothing.
    """
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["header", "footer", "nav", "script", "style", "aside", "form"]):
        tag.decompose()

    main_element = soup.find("main")
    if main_element:
        partitioned = partition_html(
            text=str(main_element),
            languages=settings.languages,
            skip_headers_and_footers=True,
        )
        if partitioned:
            return _elements_to_markdown(partitioned)
        return markdownify(str(main_element), heading_style="ATX")

    # Use <body> if present, otherwise fall back to the cleaned soup root.
    # This avoids feeding <html>/<head> wrapper noise into partition_html.
    target = soup.find("body") or soup
    partitioned = partition_html(
        text=str(target),
        languages=settings.languages,
        skip_headers_and_footers=True,
    )
    if partitioned:
        return _elements_to_markdown(partitioned)

    return markdownify(str(target), heading_style="ATX")


def _trafilatura_extract(html: str, url: str | None = None) -> str | None:
    result = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        favor_precision=True,
    )
    return result or None


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

_EVAL_SYSTEM = textwrap.dedent("""
    You are a quality-evaluation assistant for web-page text extraction.
    You will receive a Markdown-formatted extraction of a web page's main body.
    Evaluate it on the following criteria:
      1. Readability  - is the text coherent and human-readable?
      2. Completeness - does it appear to contain the full main content
         without obvious truncation or missing sections?
      3. Cleanliness  - is it free from navigation menus, cookie banners,
         footer boilerplate, and other noise?
      4. Structure    - are headings, lists, and paragraphs logically preserved?

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
    where appropriate). Do not include any commentary - only the extracted Markdown.
""").strip()


def _llm_evaluate(client: AzureOpenAI, deployment: str, extracted_markdown: str) -> tuple[bool, str]:
    """
    Ask the LLM to evaluate the quality of an extraction.
    Returns (passed, reason). On any API or parse error returns (False, reason)
    so the caller can fall back gracefully without crashing.
    """
    try:
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
    except APIError as e:
        return False, f"LLM API error during evaluation: {e}"
    except Exception as e:
        return False, f"Unexpected error during LLM evaluation: {e}"

    try:
        result = json.loads(raw)
        return bool(result.get("pass", False)), result.get("reason", "no reason given")
    except json.JSONDecodeError:
        return False, f"LLM returned non-JSON: {raw[:120]}"


def _llm_extract(client: AzureOpenAI, deployment: str, html: str) -> str:
    """
    Ask the LLM to re-extract the main content from raw HTML.
    Returns the extracted Markdown, or empty string on any error so the
    caller can fall back gracefully without crashing.
    """
    try:
        response = client.chat.completions.create(
            model=deployment,
            temperature=0,
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": html},
            ],
        )
        return (response.choices[0].message.content or "").strip()
    except APIError as e:
        logger.error(f"LLM API error during re-extraction: {e}")
        return ""
    except Exception as e:
        logger.error(f"Unexpected error during LLM re-extraction: {e}")
        return ""


# ---------------------------------------------------------------------------
# HTML cleaning
# ---------------------------------------------------------------------------

def clean_html(entity: EntityToClean, client: AzureOpenAI | None, deployment: str | None) -> str:
    """
    Three modes controlled by entity.use_llm and entity.use_llm_correction:

    use_llm=False (default):
        trafilatura -> on empty/error fall back to BeautifulSoup.

    use_llm=True, use_llm_correction=False:
        trafilatura (or BeautifulSoup if trafilatura is empty) ->
        LLM evaluation -> if PASS return extraction, if FAIL fall back
        to BeautifulSoup.

    use_llm=True, use_llm_correction=True:
        trafilatura (or BeautifulSoup if trafilatura is empty) ->
        LLM evaluation -> if PASS return extraction, if FAIL send raw
        HTML to LLM for full re-extraction -> if LLM re-extraction is
        empty fall back to BeautifulSoup.

    Note: use_llm_correction=True has no effect when use_llm=False.
    """
    with entity.file_path.open("r", encoding="utf-8", errors="replace") as f:
        html = f.read()

    # Guard: warn if correction is requested without evaluation being enabled.
    if entity.use_llm_correction and not entity.use_llm:
        logger.warning(
            f"[html] use_llm_correction=True but use_llm=False for {entity.url}; "
            "correction will be ignored -- set use_llm=True to enable it."
        )

    # No LLM path
    if not entity.use_llm:
        extracted = _trafilatura_extract(html, url=entity.url)
        if extracted:
            logger.info(f"[html] trafilatura succeeded for {entity.url}")
            return extracted
        logger.warning(f"[html] trafilatura empty, falling back to BeautifulSoup for {entity.url}")
        return _beautifulsoup_extract(html)

    # LLM paths
    extracted = _trafilatura_extract(html, url=entity.url)
    if not extracted:
        logger.warning(f"[html] trafilatura empty for {entity.url}; using BeautifulSoup before LLM eval")
        extracted = _beautifulsoup_extract(html)

    logger.info(f"[html] running LLM evaluation for {entity.url}")
    passed, reason = _llm_evaluate(client, deployment, extracted)
    logger.info(f"[html] LLM evaluation {'PASSED' if passed else 'FAILED'} for {entity.url}: {reason}")

    if passed:
        return extracted

    if not entity.use_llm_correction:
        logger.warning(f"[html] LLM eval failed, falling back to BeautifulSoup for {entity.url}")
        return _beautifulsoup_extract(html)

    logger.info(f"[html] LLM correction: re-extracting from raw HTML for {entity.url}")
    corrected = _llm_extract(client, deployment, html)
    if corrected:
        return corrected

    logger.warning(f"[html] LLM re-extraction empty; falling back to BeautifulSoup for {entity.url}")
    return _beautifulsoup_extract(html)


# ---------------------------------------------------------------------------
# Non-HTML cleaning
# ---------------------------------------------------------------------------

def clean_pdf(entity: EntityToClean) -> str:
    """
    Use pymupdf4llm for PDF-to-Markdown conversion.
    Handles multi-column layouts, tables, headings, and hyperlinks automatically.
    Images are intentionally skipped here — they are extracted separately by
    extract_images_from_pdf() and uploaded as individual files.
    """
    return pymupdf4llm.to_markdown(
        entity.file_path.as_posix(),
        show_progress=False
    )


def clean_any_file(entity: EntityToClean) -> str:
    """
    Use Unstructured for all non-HTML, non-PDF formats (DOCX, DOC, etc.).
    Output is rendered as Markdown via _elements_to_markdown so headings,
    lists, tables, and emphasis are all preserved.
    """
    partitioned = partition(filename=entity.file_path.as_posix(), languages=settings.languages)
    return _elements_to_markdown(partitioned)


# ---------------------------------------------------------------------------
# Image extraction
# ---------------------------------------------------------------------------

def _images_dir(entity: EntityToClean) -> Path:
    """Return (and create) the per-job images subdirectory."""
    d = entity.directory_path / "images"
    d.mkdir(exist_ok=True)
    return d


def extract_images_from_html(entity: EntityToClean) -> list[Path]:
    """
    Extract images referenced by <img src="..."> in the HTML file.

    - data-URI images are decoded and saved immediately.
    - http/https URLs are downloaded; relative URLs are resolved against
      entity.url before downloading.
    - Any individual download failure is logged and skipped so one bad
      image does not abort the whole job.
    """
    with entity.file_path.open("r", encoding="utf-8", errors="replace") as f:
        html = f.read()

    soup = BeautifulSoup(html, "lxml")
    images_dir = _images_dir(entity)
    saved: list[Path] = []

    for idx, img_tag in enumerate(soup.find_all("img", src=True)):
        src = img_tag["src"].strip()
        if not src:
            continue

        # --- data URI ---------------------------------------------------------
        data_match = re.match(r"data:(image/[\w+.-]+);base64,(.+)", src, re.DOTALL)
        if data_match:
            mime = data_match.group(1)
            ext = mimetypes.guess_extension(mime) or ".bin"
            ext = ".jpg" if ext == ".jpe" else ext
            try:
                image_bytes = base64.b64decode(data_match.group(2))
                out_path = images_dir / f"image_{idx:03d}{ext}"
                out_path.write_bytes(image_bytes)
                saved.append(out_path)
            except Exception as e:
                logger.warning(f"[images/html] data-URI decode failed (idx {idx}): {e}")
            continue

        # --- remote URL -------------------------------------------------------
        try:
            full_url = urljoin(entity.url, src) if not src.startswith("http") else src
        except Exception:
            continue
        if not full_url.startswith("http"):
            continue

        try:
            r = requests.get(full_url, timeout=15)
            r.raise_for_status()
            content_type = r.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
            ext = mimetypes.guess_extension(content_type) or ".jpg"
            ext = ".jpg" if ext == ".jpe" else ext
            out_path = images_dir / f"image_{idx:03d}{ext}"
            out_path.write_bytes(r.content)
            saved.append(out_path)
        except Exception as e:
            logger.warning(f"[images/html] download failed for {full_url}: {e}")

    logger.info(f"[images/html] extracted {len(saved)} image(s) from {entity.file_path}")
    return saved


def extract_images_from_pdf(entity: EntityToClean) -> list[Path]:
    """
    Extract embedded images from a PDF using PyMuPDF (fitz).
    Each unique xref is saved once; duplicates (same image referenced on
    multiple pages) are skipped to avoid inflating the image set.
    """
    import fitz  # PyMuPDF — already a transitive dep via pymupdf4llm

    images_dir = _images_dir(entity)
    saved: list[Path] = []
    seen_xrefs: set[int] = set()

    doc = fitz.open(entity.file_path.as_posix())
    try:
        for page_num in range(len(doc)):
            for img_info in doc.get_page_images(page_num, full=True):
                xref = img_info[0]
                if xref in seen_xrefs:
                    continue
                seen_xrefs.add(xref)
                try:
                    img_data = doc.extract_image(xref)
                    ext = f".{img_data['ext']}"
                    out_path = images_dir / f"image_{len(saved):03d}{ext}"
                    out_path.write_bytes(img_data["image"])
                    saved.append(out_path)
                except Exception as e:
                    logger.warning(f"[images/pdf] xref {xref} extraction failed: {e}")
    finally:
        doc.close()

    logger.info(f"[images/pdf] extracted {len(saved)} image(s) from {entity.file_path}")
    return saved


def extract_images_from_docx(entity: EntityToClean) -> list[Path]:
    """
    Extract embedded images from a DOCX file via python-docx relationship
    parts.  Only image relationships are processed; other media (audio, video)
    are skipped.
    """
    from docx import Document  # python-docx

    images_dir = _images_dir(entity)
    saved: list[Path] = []

    doc = Document(entity.file_path.as_posix())
    for idx, rel in enumerate(doc.part.rels.values()):
        if "image" not in rel.reltype:
            continue
        try:
            image_part = rel.target_part
            ext = Path(image_part.partname).suffix or ".png"
            out_path = images_dir / f"image_{idx:03d}{ext}"
            out_path.write_bytes(image_part.blob)
            saved.append(out_path)
        except Exception as e:
            logger.warning(f"[images/docx] rel {idx} extraction failed: {e}")

    logger.info(f"[images/docx] extracted {len(saved)} image(s) from {entity.file_path}")
    return saved


def extract_images_from_pptx(entity: EntityToClean) -> list[Path]:
    """
    Extract embedded images from a PPTX file via python-pptx.
    Iterates every slide and picks out picture shapes (shape_type == 13,
    i.e. MSO_SHAPE_TYPE.PICTURE).
    """
    from pptx import Presentation  # python-pptx
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    images_dir = _images_dir(entity)
    saved: list[Path] = []

    prs = Presentation(entity.file_path.as_posix())
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
                continue
            try:
                image = shape.image
                ext = f".{image.ext}"
                out_path = images_dir / f"image_{len(saved):03d}{ext}"
                out_path.write_bytes(image.blob)
                saved.append(out_path)
            except Exception as e:
                logger.warning(f"[images/pptx] shape extraction failed: {e}")

    logger.info(f"[images/pptx] extracted {len(saved)} image(s) from {entity.file_path}")
    return saved


def extract_images(entity: EntityToClean, file_type: str) -> list[Path]:
    """
    Dispatcher: call the right extractor for the given file type.
    Returns an empty list for formats with no image content (e.g. .txt).
    Any unexpected top-level failure is caught, logged, and treated as
    zero images so the rest of the pipeline is never blocked.
    """
    extractors = {
        ".html": extract_images_from_html,
        ".pdf":  extract_images_from_pdf,
        ".docx": extract_images_from_docx,
        ".doc":  extract_images_from_docx,
        ".pptx": extract_images_from_pptx,
        ".ppt":  extract_images_from_pptx,
    }
    extractor = extractors.get(file_type)
    if extractor is None:
        return []
    try:
        return extractor(entity)
    except Exception as e:
        logger.error(f"[images] top-level extraction error for {entity.file_path} ({file_type}): {e}")
        return []


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def set_up_logging(entity: EntityToClean):
    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s [%(name)s.%(funcName)s:%(lineno)d] %(message)s"
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Attach stdout handler once only (root logger persists across processes).
    if not root.handlers:
        stdout_handler = logging.StreamHandler()
        stdout_handler.setFormatter(fmt)
        root.addHandler(stdout_handler)

    # Attach a per-job file handler, avoiding duplicates.
    # Resolve both paths to absolute strings before comparing so that relative
    # vs absolute differences do not create spurious duplicates.
    job_logger = logging.getLogger(__name__)
    log_path = entity.logs_path.resolve().as_posix()
    for handler in job_logger.handlers:
        if (
            isinstance(handler, logging.FileHandler)
            and Path(handler.baseFilename).resolve().as_posix() == log_path
        ):
            break
    else:
        file_handler = logging.FileHandler(log_path)
        file_handler.setFormatter(fmt)
        job_logger.addHandler(file_handler)


# ---------------------------------------------------------------------------
# Main single-file task
# ---------------------------------------------------------------------------

def clean_file_task(entity: EntityToClean):
    with catch_error(entity):
        set_up_logging(entity)
        logger.info(f"Cleaning file {entity.file_path.as_posix()}")

        ruuter_timeout = 30  # seconds

        with entity.meta_data_path.open("r") as f:
            metadata = json.load(f)

        file_type = metadata.get("file_type", "")

        # Only fetch Vault secrets when LLM is actually requested.
        # The service must start and process non-LLM jobs even when Vault
        # is not configured.
        client = None
        deployment_name = None
        if entity.use_llm:
            secrets = get_vault_secrets()
            client = _make_openai_client(secrets)
            deployment_name = secrets.azure_openai_deployment

        if file_type == ".html":
            cleaned_text = clean_html(entity, client, deployment_name)
            logger.info(f"Cleaned as HTML for {entity.file_path.as_posix()}")
        elif file_type == ".pdf":
            cleaned_text = clean_pdf(entity)
            logger.info(f"Cleaned as PDF (pymupdf4llm) for {entity.file_path.as_posix()}")
        elif file_type in (".pptx", ".ppt"):
            cleaned_text = clean_any_file(entity)
            logger.info(f"Cleaned as PPTX (unstructured) for {entity.file_path.as_posix()}")
        else:
            cleaned_text = clean_any_file(entity)
            logger.info(f"Cleaned as unstructured file for {entity.file_path.as_posix()}")

        # Normalise excessive whitespace
        cleaned_text = normalize_newlines(cleaned_text)

        # Detect language
        detected_language = None
        if cleaned_text.strip():
            try:
                detected_language = detect(cleaned_text)
                logger.info(f"Detected language: {detected_language} for {entity.file_path.as_posix()}")
            except LangDetectException as e:
                logger.error(f"Language detection failed for {entity.file_path.as_posix()}: {e}")

        # Write and upload cleaned text
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
            raise RuntimeError("Invalid JSON response from Ruuter for cleaned text upload") from exc
        if not isinstance(response_json, dict) or "response" not in response_json:
            raise RuntimeError("Missing 'response' key in Ruuter response for cleaned text upload")
        uploaded_cleaned_text_url = response_json["response"]
        logger.info(f"Saved cleaned text for {entity.file_path.as_posix()}")

        # Write and upload cleaned metadata.
        # Both mutations happen before the write so the file is always consistent.
        # Remove any stale top-level "language" key the scrapper may have placed there;
        # the canonical location is metadata["metadata"]["language"].
        metadata.pop("language", None)
        metadata["metadata"]["cleaned"] = True
        metadata["metadata"]["language"] = detected_language
        cleaned_metadata_filename = entity.directory_path / "cleaned.meta.json"
        with cleaned_metadata_filename.open("w") as f:
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
            raise RuntimeError("Invalid JSON response from Ruuter for cleaned metadata upload") from exc
        if not isinstance(response_json, dict) or "response" not in response_json:
            raise RuntimeError("Missing 'response' key in Ruuter response for cleaned metadata upload")
        uploaded_cleaned_metadata_url = response_json["response"]
        logger.info(f"Saved cleaned metadata for {entity.file_path.as_posix()}")

        # Extract and upload images (only when explicitly requested)
        # Failures on individual images are logged but never raise — a missing
        # image must not abort an otherwise-successful cleaning job.
        uploaded_image_urls: list[str] = []
        extracted_images = extract_images(entity, file_type) if entity.extract_images else []
        for img_path in extracted_images:
            try:
                r = requests.post(
                    f"{settings.ruuter_internal}/ckb/pipeline/upload-file-sync",
                    json={"source_file_path": img_path.as_posix()},
                    timeout=ruuter_timeout,
                )
                r.raise_for_status()
                img_url = r.json().get("response", "")
                if img_url:
                    uploaded_image_urls.append(img_url)
                    logger.info(f"Uploaded image {img_path.name} -> {img_url}")
                else:
                    logger.warning(f"[images] Ruuter returned empty URL for {img_path.name}")
            except Exception as e:
                logger.warning(f"[images] failed to upload {img_path.name}: {e}")

        if uploaded_image_urls:
            logger.info(
                f"Uploaded {len(uploaded_image_urls)}/{len(extracted_images)} image(s) "
                f"for {entity.file_path.as_posix()}"
            )

        # Update the database record
        # NOTE: Ruuter's update-cleaned-file endpoint must be extended to
        # accept the image_urls field so images are persisted in the DB.
        r = requests.post(
            f"{settings.ruuter_internal}/ckb/source-file/update-cleaned-file",
            json={
                "base_id": entity.source_file_id,
                "cleaned_data_url": uploaded_cleaned_text_url,
                "cleaned_metadata_url": uploaded_cleaned_metadata_url,
                "image_urls": uploaded_image_urls,
            },
            timeout=ruuter_timeout,
        )
        r.raise_for_status()

        # All uploads confirmed -- safe to delete the working directory
        cleanup_directory(entity)


# ---------------------------------------------------------------------------
# Batch source task
# ---------------------------------------------------------------------------

def _to_local_path(path_or_url: str | None) -> Path | None:
    if not path_or_url:
        return None
    return Path('/' + path_or_url.replace('uploads/', '').lstrip('/'))


def clean_source_task(task: SourceCleaningTask):
    logs_path = Path(task.logs_path)
    logs_path.parent.mkdir(parents=True, exist_ok=True)
    logs_path.touch(exist_ok=True)

    for file in task.files:
        try:
            requests.post(
                f"{settings.ruuter_internal}/ckb/source-file/update-scrapped-file-stop-scrapping",
                json={"base_id": file.baseId, "status": "cleaning"},
                timeout=30,
            )

            fallback_directory = (
                Path("/scrapped-data") / task.agency_base_id / file.sourceBaseId / file.baseId
            )
            file_path = _to_local_path(file.originalDataUrl) or fallback_directory / "source.html"
            meta_data_path = (
                _to_local_path(file.originalMetadataUrl) or fallback_directory / "source.meta.json"
            )

            entity = EntityToClean(
                file_path=file_path,
                meta_data_path=meta_data_path,
                directory_path=fallback_directory,
                source_file_id=file.baseId,
                url=file.url,
                logs_path=logs_path,
                source_base_id=file.sourceBaseId,
                agency_base_id=task.agency_base_id,
                source_run_report_base_id=task.source_run_report_base_id,
                use_llm=task.use_llm,
                use_llm_correction=task.use_llm_correction,
                extract_images=task.extract_images,
            )
            clean_file_task(entity)
        except Exception as e:
            # Catches both ValidationError and any runtime error -- both are
            # reported the same way and must not stop the remaining files.
            send_error(
                file.url, "cleaning", str(e),
                file.sourceBaseId, task.agency_base_id, task.source_run_report_base_id,
            )

    # Upload the cleaning log
    cleaning_log_url = ""
    if logs_path.exists():
        try:
            upload_result = requests.post(
                f"{settings.ruuter_internal}/ckb/pipeline/upload-file-sync",
                json={"source_file_path": logs_path.as_posix()},
                timeout=30,
            )
            upload_result.raise_for_status()
            cleaning_log_url = upload_result.json().get("response", "")
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to upload cleaning log file: {e}")

    # Update the run report
    try:
        response = requests.post(
            f"{settings.ruuter_internal}/ckb/reports/update",
            json={
                "baseId": task.source_run_report_base_id,
                "scrapingFinishedAt": datetime.datetime.now(datetime.UTC).isoformat(),
                "scrapingLogUrl": task.scraping_log_url,
                "cleaningLogUrl": cleaning_log_url,
            },
            timeout=30,
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to update report with cleaning log URL: {e}")

    # These two final status updates are best-effort -- wrap each independently
    # so a failure on the first does not prevent the second from running.
    try:
        requests.post(
            f"{settings.ruuter_internal}/ckb/source/update-status",
            json={"source_id": task.source_base_id, "status": "finished"},
            timeout=30,
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to update source status: {e}")

    try:
        requests.post(
            f"{settings.ruuter_internal}/ckb/agency/update-zip-dirty",
            json={"sourceId": task.source_base_id, "agencyId": task.agency_base_id},
            timeout=30,
        )
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to update agency zip-dirty: {e}")