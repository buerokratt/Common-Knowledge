"""The llm-module sink — D0's verbs as HTTP pushes to the llm-module (L1).

The wire is sinks/llm_module_contract.py, documented in
content-external/docs/llm-module-ingest-contract.md. This class owns
everything between a DocumentRecord and that wire:

- ONE DOCUMENT PER REQUEST (L5). A document that fits
  `max_request_bytes` is one request. One that does not is split into
  ordered parts, and EVERY PART CARRIES THE FULL RECORD — so no chunk ever
  reaches the llm-module without its record, which is how F3 holds here
  rather than by write order. If a part's record is not applied, no later
  part is sent. A chunk is never truncated: a document whose record plus a
  single chunk cannot fit — or, with no chunks, whose record alone cannot —
  is FAILED_PERMANENT and nothing is sent. So is one that cannot be encoded
  as UTF-8 at all (a lone surrogate in its text).

- TWO RETRY LAYERS, NOT NESTED (L6). A whole request that fails with a
  connection error, a timeout or 408/429/5xx is retried by
  services/backoff.py, honouring Retry-After. A 200 or 207 is read item by
  item, and a 2xx envelope is never success on its own: only chunks whose
  items were applied count. Retryable items are resent alone for up to
  ITEM_RETRY_ROUNDS more rounds; what is still failing after that is
  FAILED_RETRYABLE, and the next tick resends the document. A permanent item
  stops the document at once. Only PUBLISHED enters the manifest (F18).

- IDEMPOTENT BY CONSTRUCTION (L4, L7). The point id is the chunk's
  coordinate id, sent untouched, and the contract requires merge-or-create —
  so every replay overwrites. `Idempotency-Key` is
  {run_id}:{document_id}:p{part}[:r{round}]:{body digest}. The digest makes
  two different bodies never share a key, even when an operator reuses a
  --run-id after the content changed; the round makes a byte-identical
  resend a new key, so a far side that caches replies cannot answer it with
  the failure it is resending for. A transport retry of one body always
  reuses its key.

- NOTHING READABLE IN A LOG (L14, F13). The request body IS the document
  text. Logs, exceptions and UpsertOutcome.detail carry ids, counts, part
  numbers and status codes only — never a body, never `str()` of a requests
  exception, never the token. The far side's item codes are kept only when
  they look like codes; a response body is read up to a bound.

NOT YET: deletes (L9), the synced_at sweep (L10) and read-back (L17). Their
hooks raise NotSupported naming the task, and the factory keeps refusing
CONTENT_SINK=llm_module until L9 lands, because the coordinator deletes on
any run that sees a deletion.
"""

import hashlib
import json
import logging
import random
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

import requests
from pydantic import BaseModel, ValidationError

from exporter.core.ids import validate_segment
from exporter.core.redaction import sanitize_sensitive_text
from exporter.core.schemas import Chunk, DocumentRecord, to_json_dict
from exporter.services.backoff import (
    DEFAULT_POLICY,
    BackoffPolicy,
    full_jitter_delay,
    is_retryable_http,
    retry_with_backoff,
)
from exporter.sinks.base import (
    ContentSink,
    DeleteOutcome,
    DocumentProbe,
    NotSupported,
    SinkCapabilities,
    UpsertOutcome,
    UpsertStatus,
)
from exporter.sinks.llm_module_contract import (
    AGREED_TERMS,
    INGEST_PATH,
    RECORD_REF,
    SUPPORTED_INGEST_SCHEMA_VERSIONS,
    ContractTerms,
    IngestChunk,
    IngestItem,
    IngestPart,
    IngestRequest,
    IngestResponse,
    ItemVerdict,
    classify_item_status,
)

__all__ = ["ITEM_RETRY_ROUNDS", "LlmModuleSink", "encode_request"]

logger = logging.getLogger(__name__)

# L6: how many times failed items are resent within one upsert, after the
# first send. Small on purpose — the llm-module embeds what it receives, and
# a document still failing after this is retried whole by the next tick.
ITEM_RETRY_ROUNDS = 2

# The statuses a response body is read for. Anything else below 400 (a 3xx,
# since redirects are never followed, or an unexpected 2xx) is refused.
_ITEMISED_STATUSES = frozenset({200, 207})

# A response body is read up to max(2 x the request cap, this) bytes. One
# item per sent ref is far smaller than the request that named those refs;
# past the bound the far side is broken, not busy.
_MIN_RESPONSE_BYTES = 1 << 20
_READ_PIECE_BYTES = 64 * 1024

# How many hex digits of the body's SHA-256 end the Idempotency-Key.
_KEY_DIGEST_HEX = 16

# What an item code from the far side must look like to reach a report.
# Anything else — a sentence, Estonian text, something long — becomes "-".
_ITEM_CODE = re.compile(r"[A-Za-z0-9._-]{1,40}")


def encode_request(request: BaseModel) -> bytes:
    """The exact bytes sent for `request`: compact UTF-8 JSON, never escaped
    to ASCII (C6's rule is for blob headers, not a JSON body)."""
    return json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True)
class _Failure:
    """Why a document stopped. `detail` is a code, never text from a body."""

    permanent: bool
    detail: str


@dataclass
class _Round:
    """What one send of the record plus the pending chunks achieved."""

    applied: set[str] = field(default_factory=set)
    # chunk_id -> the code it failed with this round; resent next round.
    retry: dict[str, str] = field(default_factory=dict)
    # The record itself was not applied, for a retryable reason.
    record_retry: str | None = None
    failure: _Failure | None = None

    @property
    def settled(self) -> bool:
        return not self.retry and self.record_retry is None

    def codes(self) -> str:
        found = set(self.retry.values())
        if self.record_retry is not None:
            found.add(self.record_retry)
        return ",".join(sorted(found))


class _DocumentTooLargeError(Exception):
    """The record plus one chunk (or the record alone, when there are no
    chunks) does not fit one request."""


class _ResponseTooLargeError(Exception):
    """The response body passed its bound before it ended."""


class LlmModuleSink(ContentSink):
    """Documents pushed to the llm-module, one request per document."""

    def __init__(
        self,
        *,
        base_url: str,
        agency_id: str,
        sink_id: str,
        run_id: str,
        token_provider: Callable[[], str],
        ingest_schema_version: int,
        max_request_bytes: int,
        connect_timeout: float,
        read_timeout: float,
        terms: ContractTerms = AGREED_TERMS,
        session: requests.Session | None = None,
        policy: BackoffPolicy = DEFAULT_POLICY,
        sleep: Callable[[float], None] = time.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        super().__init__(agency_id=agency_id, sink_id=sink_id)
        if not base_url.startswith("https://"):
            raise ValueError(
                "the llm-module base URL must use https://, got "
                f"{sanitize_sensitive_text(base_url)!r}"
            )
        if ingest_schema_version not in SUPPORTED_INGEST_SCHEMA_VERSIONS:
            raise ValueError(
                f"ingest_schema_version {ingest_schema_version!r} is not one this "
                f"sink speaks ({sorted(SUPPORTED_INGEST_SCHEMA_VERSIONS)})"
            )
        if max_request_bytes < 1:
            raise ValueError(f"max_request_bytes must be >= 1, got {max_request_bytes}")
        if connect_timeout <= 0 or read_timeout <= 0:
            raise ValueError("connect_timeout and read_timeout must be > 0")
        # It becomes part of every Idempotency-Key.
        self._run_id = validate_segment("run_id", run_id)
        self._url = base_url.rstrip("/") + INGEST_PATH
        self._token_provider = token_provider
        self._schema_version = ingest_schema_version
        self._max_request_bytes = max_request_bytes
        self._max_response_bytes = max(2 * max_request_bytes, _MIN_RESPONSE_BYTES)
        self._timeout = (connect_timeout, read_timeout)
        self._terms = terms
        # TLS verification stays at requests' default, which is on. Nothing
        # here ever sets `verify`.
        self._session = session if session is not None else requests.Session()
        self._policy = policy
        self._sleep = sleep
        self._rand = rand

    @property
    def capabilities(self) -> SinkCapabilities:
        """Declared from this code and from AGREED_TERMS, never inferred.

        `atomic_per_document` is False although a document that fits one
        request is atomic: a split one is not, and the field cannot say
        "sometimes". F3 holds either way, because every part carries the
        record. `supports_agency_scoped_delete` waits on Open item 7 — the
        llm-module has no agency_id on its points today.
        """
        return SinkCapabilities(
            can_list=False,
            can_read_back=False,
            supports_metadata_only_merge=self._terms.supports_metadata_only_merge,
            supports_batch_delete=False,
            atomic_per_document=False,
            supports_agency_scoped_delete=False,
            max_concurrency=self._terms.max_concurrency,
        )

    # -- upsert (L3-L8) ----------------------------------------------------

    def _upsert_document(
        self, record: DocumentRecord, chunks: tuple[Chunk, ...] | None
    ) -> UpsertOutcome:
        token = self._token()
        if token is None:
            # Permanent, so F19's consecutive-permanent abort stops a run whose
            # credential is gone rather than failing every document in turn.
            return self._outcome(
                record,
                chunks,
                UpsertStatus.FAILED_PERMANENT,
                0,
                "credential-unavailable",
            )

        applied: set[str] = set()
        pending = chunks
        last = ""
        for round_ in range(ITEM_RETRY_ROUNDS + 1):
            if round_:
                self._sleep(full_jitter_delay(round_ - 1, self._policy, self._rand))
            result = self._send_round(record, pending, token, round_)
            applied |= result.applied
            if result.failure is not None:
                status = (
                    UpsertStatus.FAILED_PERMANENT
                    if result.failure.permanent
                    else UpsertStatus.FAILED_RETRYABLE
                )
                return self._outcome(
                    record, chunks, status, len(applied), result.failure.detail
                )
            if result.settled:
                return self._outcome(
                    record, chunks, UpsertStatus.PUBLISHED, len(applied)
                )
            last = result.codes()
            if pending is not None:
                pending = tuple(c for c in pending if c.chunk_id in result.retry)
        return self._outcome(
            record,
            chunks,
            UpsertStatus.FAILED_RETRYABLE,
            len(applied),
            f"items-still-failing: {last}",
        )

    def _send_round(
        self,
        record: DocumentRecord,
        chunks: tuple[Chunk, ...] | None,
        token: str,
        round_: int,
    ) -> _Round:
        """Send the record and `chunks` (None: metadata only), part by part."""
        result = _Round()
        try:
            parts = self._plan_parts(record, chunks)
        except _DocumentTooLargeError:
            result.failure = _Failure(True, "request-cap-too-small")
            return result
        except ValidationError:
            # A record the wire models refuse. The error quotes its input, so
            # it is reduced to a code here (F13).
            result.failure = _Failure(True, "invalid-document")
            return result
        except ValueError:
            # A lone surrogate: a str, but not encodable as UTF-8. The
            # UnicodeEncodeError quotes the text around it, so it is reduced
            # to a code here (F13). (NaN and Infinity never get this far:
            # pydantic's JSON mode writes them as null.)
            result.failure = _Failure(True, "unencodable-document")
            return result

        for index, part in enumerate(parts):
            request = self._request(record, part, index, len(parts))
            key = f"{self._run_id}:{record.document_id}:p{index}"
            if round_:
                key += f":r{round_}"
            answer = self._post(request, token, key)
            if isinstance(answer, _Failure):
                result.failure = answer
                return result
            refs = [RECORD_REF, *(c.chunk_id for c in part or ())]
            verdicts = self._verdicts(answer, refs)
            if isinstance(verdicts, _Failure):
                result.failure = verdicts
                return result

            record_verdict, record_code = verdicts[RECORD_REF]
            if record_verdict != "applied":
                if record_verdict == "permanent":
                    result.failure = _Failure(True, f"record-{record_code}")
                    return result
                # F3: a part whose record did not land applied none of its
                # chunks (contract §7), and no later part is sent. All of
                # them go round again.
                result.record_retry = f"record-{record_code}"
                for unsent in parts[index:]:
                    for chunk in unsent or ():
                        result.retry[chunk.chunk_id] = "record-not-applied"
                return result

            # The whole part is read before a permanent item stops the
            # document, so chunks_written counts every chunk that landed.
            for chunk in part or ():
                verdict, code = verdicts[chunk.chunk_id]
                if verdict == "applied":
                    result.applied.add(chunk.chunk_id)
                elif verdict == "retryable":
                    result.retry[chunk.chunk_id] = code
                elif result.failure is None:
                    result.failure = _Failure(True, f"chunk-{code}")
            if result.failure is not None:
                return result
        return result

    # -- the wire ----------------------------------------------------------

    def _request(
        self,
        record: DocumentRecord,
        chunks: Sequence[IngestChunk] | None,
        index: int,
        count: int,
    ) -> IngestRequest:
        """The record exactly as the core built it, plus this part (L3)."""
        return IngestRequest(
            ingest_schema_version=self._schema_version,
            agency_id=self.agency_id,
            document_id=record.document_id,
            source_base_id=record.source_base_id,
            content_origin=cast(Any, record.content_origin),
            source_url=record.source_url,
            raw_sha256=record.raw_sha256,
            content_sha256=record.content_sha256,
            metadata_sha256=record.metadata_sha256,
            chunk_count=record.chunk_count,
            synced_at=record.synced_at,
            source=cast("dict[str, Any]", to_json_dict(record.source)),
            part=IngestPart(index=index, count=count, final=index == count - 1),
            chunks=None if chunks is None else list(chunks),
        )

    @staticmethod
    def _wire_chunk(chunk: Chunk) -> IngestChunk:
        """L4: the chunk id goes out untouched — it is the point id."""
        return IngestChunk(
            chunk_id=chunk.chunk_id,
            ordinal=chunk.ordinal,
            text=chunk.text,
            char_start=chunk.span.start,
            char_end=chunk.span.end,
        )

    def _plan_parts(
        self, record: DocumentRecord, chunks: tuple[Chunk, ...] | None
    ) -> list[list[IngestChunk] | None]:
        """Chunks packed in ordinal order into parts that each fit the cap.

        The sizes are an upper bound, computed once: the record with an empty
        chunk list, plus slack for the widest `part` value this document can
        need, plus each encoded chunk and the comma before it. A document with
        no chunks is one part with none; a metadata-only merge is one part
        with `None`. Either way the record alone must fit.
        """
        if chunks is None:
            merge = self._request(record, None, 0, 1)
            if len(encode_request(merge)) > self._max_request_bytes:
                raise _DocumentTooLargeError
            return [None]

        wire = [self._wire_chunk(chunk) for chunk in chunks]
        sizes = [len(encode_request(chunk)) for chunk in wire]
        digits = len(str(max(len(wire), 1)))
        # {"index":0,"count":1,"final":true} at its widest: both numbers at
        # `digits` digits, and "false" is one byte longer than "true".
        slack = 2 * (digits - 1) + 1
        base = len(encode_request(self._request(record, [], 0, 1))) + slack
        if base > self._max_request_bytes:
            raise _DocumentTooLargeError

        packed: list[list[IngestChunk]] = [[]]
        used = base
        for chunk, size in zip(wire, sizes, strict=True):
            if base + size > self._max_request_bytes:
                raise _DocumentTooLargeError
            current = packed[-1]
            extra = size + (1 if current else 0)
            if current and used + extra > self._max_request_bytes:
                packed.append([])
                current = packed[-1]
                used = base
                extra = size
            current.append(chunk)
            used += extra
        return [*packed]

    def _post(
        self, request: IngestRequest, token: str, key: str
    ) -> IngestResponse | _Failure:
        """Send one part; `key` is the Idempotency-Key less its digest."""
        body = encode_request(request)
        if len(body) > self._max_request_bytes:
            # _plan_parts' bound is exact-or-over, so this is a bug here, not
            # something the llm-module did.
            raise RuntimeError(
                f"document {request.document_id} part {request.part.index}: "
                f"planned under {self._max_request_bytes} bytes but encoded to "
                f"{len(body)}"
            )
        digest = hashlib.sha256(body).hexdigest()[:_KEY_DIGEST_HEX]
        headers = {
            "Authorization": f"Bearer {token}",
            "Idempotency-Key": f"{key}:{digest}",
            "Content-Type": "application/json; charset=utf-8",
        }

        def send() -> tuple[int, bytes]:
            # Streamed, so the body is read up to a bound rather than whole;
            # the `with` hands the connection back on every path.
            with self._session.post(
                self._url,
                data=body,
                headers=headers,
                timeout=self._timeout,
                allow_redirects=False,
                stream=True,
            ) as response:
                response.raise_for_status()
                return response.status_code, self._read_capped(response)

        describe = (
            f"llm-module upsert {request.document_id} part "
            f"{request.part.index + 1}/{request.part.count}"
        )
        started = time.monotonic()
        try:
            status, content = retry_with_backoff(
                send,
                describe=describe,
                policy=self._policy,
                is_retryable=_is_retryable,
                sleep=self._sleep,
                rand=self._rand,
            )
        except requests.RequestException as exc:
            code = _request_error_code(exc)
            logger.info("%s: failed (%s)", describe, code)
            return _Failure(not _is_retryable(exc), code)
        except _ResponseTooLargeError:
            logger.info("%s: failed (response-too-large)", describe)
            return _Failure(True, "response-too-large")

        logger.debug(
            "%s: HTTP %d, %d chunks, %d bytes, %.2fs",
            describe,
            status,
            len(request.chunks or ()),
            len(body),
            time.monotonic() - started,
        )
        if status not in _ITEMISED_STATUSES:
            return _Failure(True, f"http-{status}")
        try:
            parsed = IngestResponse.model_validate_json(content)
        except ValidationError:
            return _Failure(False, "malformed-response")
        if parsed.ingest_schema_version != self._schema_version:
            return _Failure(True, "schema-version-mismatch")
        return parsed

    @staticmethod
    def _verdicts(
        response: IngestResponse, refs: Sequence[str]
    ) -> dict[str, tuple[ItemVerdict, str]] | _Failure:
        """Each sent ref's verdict and code. A ref answered twice, or one that
        was never sent, makes the whole answer untrustworthy; a ref with no
        answer is retried (contract §3)."""
        sent = set(refs)
        items: dict[str, IngestItem] = {}
        for item in response.items:
            if item.ref in items or item.ref not in sent:
                return _Failure(False, "malformed-response")
            items[item.ref] = item
        verdicts: dict[str, tuple[ItemVerdict, str]] = {}
        for ref in refs:
            item = items.get(ref)
            if item is None:
                verdicts[ref] = ("retryable", "unacknowledged")
                continue
            code = str(item.status)
            if item.code:
                code = f"{code}:{_short_code(item.code)}"
            verdicts[ref] = (classify_item_status(item.status), code)
        return verdicts

    # -- helpers -----------------------------------------------------------

    def _read_capped(self, response: requests.Response) -> bytes:
        content = bytearray()
        for piece in response.iter_content(chunk_size=_READ_PIECE_BYTES):
            content += piece
            if len(content) > self._max_response_bytes:
                raise _ResponseTooLargeError
        return bytes(content)

    def _token(self) -> str | None:
        """The bearer token for this upsert, or None if there is none usable.
        Fetched per upsert and never cached, so a rotated credential is picked
        up without a restart (L11 supplies the Vault-backed provider)."""
        try:
            token = self._token_provider()
        except Exception as exc:
            logger.warning("llm-module credential unavailable (%s)", type(exc).__name__)
            return None
        if not isinstance(token, str) or not token.strip():
            logger.warning("llm-module credential unavailable (empty)")
            return None
        token = token.strip()
        # Printable ASCII only: CR/LF would split the header, and anything
        # non-ASCII fails inside http.client with the character in the error.
        if not (token.isascii() and token.isprintable()):
            logger.warning("llm-module credential unavailable (malformed)")
            return None
        return token

    @staticmethod
    def _outcome(
        record: DocumentRecord,
        chunks: tuple[Chunk, ...] | None,
        status: UpsertStatus,
        written: int,
        detail: str | None = None,
    ) -> UpsertOutcome:
        return UpsertOutcome(
            document_id=record.document_id,
            status=status,
            chunks_written=0 if chunks is None else written,
            metadata_only=chunks is None,
            detail=detail,
        )

    # -- not yet (L9, L10, L17) -------------------------------------------

    def _delete_documents(self, document_ids: tuple[str, ...]) -> DeleteOutcome[str]:
        raise NotSupported("the llm-module sink's deletes arrive with task L9")

    def _delete_chunks(
        self, document_id: str, ordinals: tuple[int, ...]
    ) -> DeleteOutcome[int]:
        raise NotSupported("the llm-module sink's deletes arrive with task L9")

    def _delete_agency(self) -> DeleteOutcome[str]:
        raise NotSupported("the llm-module sink's deletes arrive with task L9")

    def _sweep_stale(self, before: datetime) -> DeleteOutcome[str]:
        raise NotSupported(
            "the llm-module sink's synced_at sweep arrives with task L10"
        )

    def _read_document(self, document_id: str) -> DocumentProbe | None:
        raise NotSupported("the llm-module sink's read-back arrives with task L17")

    def _holds_agency_content(self) -> bool:
        raise NotSupported("the llm-module sink cannot list (can_list=False)")


# A response cut off part way (a reset mid-body, a broken chunked or gzip
# stream). As transient as a connection error, but not one to requests.
_TRUNCATED_RESPONSE = (
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ContentDecodingError,
)


def _is_retryable(exc: BaseException) -> bool:
    """backoff.py's HTTP rule, plus a response cut off part way."""
    return isinstance(exc, _TRUNCATED_RESPONSE) or is_retryable_http(exc)


def _request_error_code(exc: requests.RequestException) -> str:
    """A code for a failed request — never `str(exc)`, which can carry the
    URL and, for some errors, part of the response."""
    if isinstance(exc, requests.exceptions.SSLError):
        return "tls-error"
    if isinstance(exc, requests.Timeout):
        return "timeout"
    if isinstance(exc, requests.ConnectionError):
        return "connection-error"
    if isinstance(exc, _TRUNCATED_RESPONSE):
        return "truncated-response"
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return f"http-{exc.response.status_code}"
    return type(exc).__name__


def _short_code(code: str) -> str:
    """The far side's item code, if it looks like one. The contract says it
    never carries document text; this enforces it — anything but a short run
    of ASCII letters, digits and -_. (a sentence, Estonian text) becomes "-"
    rather than a squeezed copy of itself."""
    return code if _ITEM_CODE.fullmatch(code) else "-"
