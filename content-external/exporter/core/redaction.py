"""Credential redaction for anything this service logs.

Pure: no I/O, no network, and it names no destination — so it belongs under
core/ alongside the rest of the unit-tested heart.

This is a re-implementation of `sanitize_sensitive_text` from
cleaning/worker/utils.py:41 (which is itself mirrored in
scrapper/scrapper/utils.py). The cross-cutting Logging rule says "reuse
sanitize_sensitive_text", and that cannot be taken literally: each service is
its own Docker root with its own top-level package, so a cross-service import
is impossible without the sys.path mutation the Imports rule forbids. Same
name and same behaviour is the closest available thing to reuse.

ONE DELIBERATE SUPERSET. This copy also redacts the signing parameters of a
presigned URL (S3 SigV4 and SigV2, GCS, Azure SAS). F13 says "never a signed
URL", and this service handles them where the other two do not. The CKB
copies are left alone because this pipeline makes no CKB changes; port the
extra names there if they are ever wanted.
"""

import re

# Query/form params commonly used to pass credentials or tokens.
_SENSITIVE_PARAM_NAMES = (
    "token",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "pwd",
    "secret",
    "access_token",
    "auth",
    "session",
    "sessionid",
    "sid",
    # Presigned URLs (F13): the names core/metadata.py's _SIGNED_URL_PARAMS
    # refuses as citations. The signature is the credential; the access key
    # id and the session token identify and extend it.
    "x-amz-signature",
    "x-amz-credential",
    "x-amz-security-token",
    "awsaccesskeyid",
    "signature",
    "x-goog-signature",
    "x-goog-credential",
    "sig",
)

_USERINFO_RE = re.compile(r"://[^\s/@]+:[^\s/@]+@")
_AUTH_HEADER_RE = re.compile(
    r"(authorization[\"']?\s*[:=]\s*[\"']?(basic|bearer)\s+)\S+", re.IGNORECASE
)
_SENSITIVE_PARAM_RE = re.compile(
    r"([?&](?:" + "|".join(_SENSITIVE_PARAM_NAMES) + r")=)[^&\s\"'<>]+",
    re.IGNORECASE,
)


def sanitize_sensitive_text(text: str) -> str:
    """Redact credentials/tokens from a URL or error message before it is
    logged or sent anywhere (e.g. userinfo in a URL, Authorization headers
    appearing in exception text, credential-style query params)."""
    if not text:
        return text

    sanitized = _USERINFO_RE.sub("://[redacted]@", text)
    sanitized = _AUTH_HEADER_RE.sub(r"\1[redacted]", sanitized)
    sanitized = _SENSITIVE_PARAM_RE.sub(r"\1[redacted]", sanitized)
    return sanitized
