
# Cleaning Service

The cleaning service extracts and normalises the main body content from scraped files before they are indexed. It supports HTML files and a wide range of other document formats (PDF, DOCX, XLSX, etc.).

---

## What Changed

### HTML extraction

Previously HTML files were processed by BeautifulSoup, which returned plain text by stripping all tags. The new pipeline replaces this as the primary extractor:

| Step     | Tool                                       | Purpose                                                                                                        |
| -------- | ------------------------------------------ | -------------------------------------------------------------------------------------------------------------- |
| 1        | **trafilatura**                      | Extracts main body content, ignoring navigation, footers, cookie banners, and ads. Output is**Markdown** |
| 2        | **LLM evaluation** *(optional)*    | Assesses whether the extraction is readable, complete, clean, and well-structured                              |
| 3        | **LLM re-extraction** *(optional)* | If evaluation fails, sends raw HTML to the LLM and asks it to extract and format the main body itself          |
| fallback | **BeautifulSoup**                    | Used when trafilatura returns nothing, or as a last resort if LLM re-extraction also fails                     |

Non-HTML files (PDF, DOCX, etc.) are unchanged — they continue to use `unstructured.partition.auto`.

---

## Output Format Change

|                       | Before     | After                                                               |
| --------------------- | ---------- | ------------------------------------------------------------------- |
| **HTML files**  | Plain text | **Markdown** (headings, lists, bold/italic, tables preserved) |
| **Other files** | Plain text | Markdown                                                            |

The output is still written to `cleaned.txt` in the job directory and uploaded via the same ruuter endpoint — the file name and upload flow did not change.

---

## API Input

**Endpoint:** `POST /clean_file`

### Existing fields (unchanged)

```json
{
  "file_path": "/scrapped-data/job-123/page.html",
  "meta_data_path": "/scrapped-data/job-123/page.meta.json",
  "directory_path": "/scrapped-data/job-123",
  "source_file_id": "abc123",
  "url": "https://example.com/page",
  "logs_path": "/scrapped-data/job-123/job.log"
}
```

### New optional fields

```json
{
  "use_llm": false,
  "use_llm_correction": false
}
```

| Field                  | Type     | Default   | Description                                                                |
| ---------------------- | -------- | --------- | -------------------------------------------------------------------------- |
| `use_llm`            | `bool` | `false` | Enables LLM evaluation of the trafilatura extraction                       |
| `use_llm_correction` | `bool` | `false` | If evaluation fails, re-extracts using the LLM. Requires `use_llm: true` |

Note: passing `use_llm_correction: true` without `use_llm: true` has no effect — correction is silently skipped because the evaluation step that triggers it is disabled.

---

## Behaviour by Configuration

### `use_llm: false` (default)

```
trafilatura → success?  → use result (Markdown)
           → empty?    → BeautifulSoup fallback (plain text)
```

No LLM calls are made. No Azure credentials required.

---

### `use_llm: true`, `use_llm_correction: false`

```
trafilatura → LLM evaluates quality
           → pass?  → use trafilatura result
           → fail?  → use trafilatura result anyway (evaluation logged as warning)
```

The evaluation result is recorded in the logs but does not change the output. Useful for monitoring extraction quality without correcting it.

---

### `use_llm: true`, `use_llm_correction: true`

```
trafilatura → LLM evaluates quality
           → pass?  → use trafilatura result
           → fail?  → LLM re-extracts from raw HTML
                    → success?  → use LLM result
                    → empty?    → BeautifulSoup fallback
```

This is the highest-quality mode. It costs two LLM calls per HTML file when extraction quality is poor, and one call when it is good.

---

## LLM Evaluation Criteria

The evaluator uses `gpt-4o-mini` and scores the extraction on four criteria:

1. **Readability** — is the text coherent and human-readable?
2. **Completeness** — does it appear fully extracted without truncation?
3. **Cleanliness** — is it free from nav menus, cookie banners, and boilerplate?
4. **Structure** — are headings, lists, and paragraphs logically preserved?

The model returns `{"pass": true/false, "reason": "..."}`. The reason is always written to the job log regardless of the result.

---

## Secrets and Configuration

The LLM calls use Azure OpenAI. Credentials are **not** passed via environment variables — they are fetched from HashiCorp Vault at task time via the `vault-agent-cleaner` proxy.

Vault secrets are only fetched when processing an HTML file **with** `use_llm: true`. Non-HTML files and HTML files processed without LLM calls do not require Vault access at all.

The secret is stored in Vault at:

```
secret/data/llm/connections/azure_openai/cleaner
```

With these fields:

```json
{
  "api_key": "...",
  "endpoint": "https://<resource>.openai.azure.com/",
  "deployment": "gpt-4o-mini",
  "api_version": "2024-02-01"
}
```

The service will raise an error on the affected task if Vault is unreachable, the secret is missing/empty, or any field still contains the placeholder value `REPLACE_ME`. Update it with:

```bash
# Get root token
docker exec vault cat /vault/file/unseal-keys.json | grep -o '"root_token":"[^"]*"' | cut -d'"' -f4

# Write real credentials
docker exec -e VAULT_TOKEN=<root-token> -e VAULT_ADDR=http://127.0.0.1:8200 vault \
  vault kv put secret/llm/connections/azure_openai/cleaner \
    api_key="..." \
    endpoint="https://<resource>.openai.azure.com/" \
    deployment="gpt-4o-mini" \
    api_version="2024-02-01"

docker compose restart cleaning-server
```

---

## Example Requests

**Basic — no LLM:**

```bash
curl -X POST http://localhost:8123/clean_file \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "/scrapped-data/job-1/page.html",
    "meta_data_path": "/scrapped-data/job-1/page.meta.json",
    "directory_path": "/scrapped-data/job-1",
    "source_file_id": "job-1",
    "url": "https://example.com",
    "logs_path": "/scrapped-data/job-1/job.log"
  }'
```

**With LLM evaluation and correction:**

```bash
curl -X POST http://localhost:8123/clean_file \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "/scrapped-data/job-1/page.html",
    "meta_data_path": "/scrapped-data/job-1/page.meta.json",
    "directory_path": "/scrapped-data/job-1",
    "source_file_id": "job-1",
    "url": "https://example.com",
    "logs_path": "/scrapped-data/job-1/job.log",
    "use_llm": true,
    "use_llm_correction": true
  }'
```
