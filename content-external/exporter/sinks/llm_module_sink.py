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

- DELETES ON THE SAME WIRE (L9). Whole documents go in batches, one item per
  document_id; a tail goes as (chunk_id, ordinal) pairs, the coordinates C16
  hands over rendered to point ids. Item verdicts follow D3: 2xx is
  `deleted`, 404/410 is `missing` (already gone is success), retryable items
  are resent alone, and whatever is left is `failed` with a code, for the
  deferred-deletion journal (F7). The far side deletes a document's points
  before its record (F4) — that is the contract's, not an ordering here.

- THE SWEEP CARRIES A KEEP LIST (L10). `delete where agency_id = X and
  synced_at < before and document_id not in keep`. An unchanged document is
  never re-sent, so `synced_at` alone would sweep live content; the keep list
  is the committed manifest's documents. It is never split: a keep list sent
  in pieces would let each piece delete what the others keep, so one that
  does not fit one request is refused.

- CAPABILITIES ARE THE TERMS' (L13). Agency delete, the sweep and read-back
  (L17) all depend on what the llm-module has promised — an agency_id on
  every point (Open item 7, the contract's §13 q4) and a probe (Open item
  10, §13 q13) — so each raises NotSupported until AGREED_TERMS says
  otherwise, and the sink never infers them from a response.

- A WHOLE-REQUEST 404 IS A FAILURE, NOT "ALREADY GONE". Only an item's 404
  or 410 means missing (contract §9). A request-level 404 is what a wrong
  base URL or route looks like, and reading it as success would report every
  deletion done while doing none — so it fails, and the journal retries it.
"""

import hashlib
import json
import logging
import random
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import requests
from pydantic import BaseModel, ValidationError
from requests.adapters import HTTPAdapter

from exporter.core.ids import make_chunk_id, validate_segment
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
    CredentialUnavailableError,
    DeleteOutcome,
    DocumentProbe,
    NotSupported,
    SinkCapabilities,
    StoreError,
    UpsertOutcome,
    UpsertStatus,
)
from exporter.sinks.llm_module_contract import (
    AGREED_TERMS,
    DOCUMENT_DELETE_PATH,
    INGEST_PATH,
    RECORD_REF,
    SUPPORTED_INGEST_SCHEMA_VERSIONS,
    AgencyDeleteRequest,
    ChunkDeleteRequest,
    ChunkRef,
    ContractTerms,
    DeletedDocumentsResponse,
    DeleteVerdict,
    DocumentDeleteRequest,
    DocumentProbeResponse,
    IngestChunk,
    IngestItem,
    IngestPart,
    IngestRequest,
    IngestResponse,
    ItemVerdict,
    SweepRequest,
    agency_delete_path,
    chunk_delete_path,
    classify_delete_status,
    classify_item_status,
    document_path,
    sweep_path,
)

__all__ = [
    "DELETE_BATCH_IDS",
    "ITEM_RETRY_ROUNDS",
    "LlmModuleSink",
    "encode_request",
    "format_timestamp",
]

logger = logging.getLogger(__name__)

# L6: how many times failed items are resent within one upsert, after the
# first send. Small on purpose — the llm-module embeds what it receives, and
# a document still failing after this is retried whole by the next tick.
ITEM_RETRY_ROUNDS = 2

# L9: the most document ids one delete request names. A request is also
# packed under max_request_bytes; this keeps one far-side transaction small
# whatever the cap is.
DELETE_BATCH_IDS = 500

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


def format_timestamp(moment: datetime) -> str:
    """RFC 3339 in UTC with a `Z`, the shape `synced_at` is written in."""
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _encoded_size(value: object) -> int:
    """Bytes `value` takes inside a compact JSON body."""
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


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
        self._base_url = base_url.rstrip("/")
        self._token_provider = token_provider
        self._schema_version = ingest_schema_version
        self._max_request_bytes = max_request_bytes
        self._max_response_bytes = max(2 * max_request_bytes, _MIN_RESPONSE_BYTES)
        self._timeout = (connect_timeout, read_timeout)
        self._terms = terms
        # TLS verification stays at requests' default, which is on. Nothing
        # here ever sets `verify`. The default session pools one connection
        # per document the terms let run at once (L12).
        #
        # trust_env is off, so the environment cannot change what is sent:
        # with it on, a ~/.netrc entry for the host replaces the bearer header
        # with Basic auth, and REQUESTS_CA_BUNDLE / *_PROXY reroute or re-trust
        # the connection that carries the document text.
        if session is None:
            session = requests.Session()
            session.trust_env = False
            session.mount("https://", HTTPAdapter(pool_maxsize=terms.max_concurrency))
        self._session = session
        self._policy = policy
        self._sleep = sleep
        self._rand = rand

    @property
    def capabilities(self) -> SinkCapabilities:
        """Declared from this code and from AGREED_TERMS, never inferred (L13).

        `atomic_per_document` is False although a document that fits one
        request is atomic: a split one is not, and the field cannot say
        "sometimes" (an accepted deviation from L13's row, recorded there).
        F3 holds either way, because every part carries the record.
        `supports_agency_scoped_delete` and read-back wait on Open items 7
        and 10 (§13 q4/q11 and q13). `supports_batch_delete` is this code's: document
        deletes go in batches. `rechunk_requires_ack` because the llm-module
        embeds what it receives, so a re-chunk is a full re-embed (L18).
        """
        return SinkCapabilities(
            can_list=False,
            can_read_back=self._terms.supports_read_back,
            supports_metadata_only_merge=self._terms.supports_metadata_only_merge,
            supports_batch_delete=True,
            atomic_per_document=False,
            supports_agency_scoped_delete=self._terms.supports_agency_scoped_delete,
            max_concurrency=self._terms.max_concurrency,
            rechunk_requires_ack=True,
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
        describe = (
            f"llm-module upsert {request.document_id} part "
            f"{request.part.index + 1}/{request.part.count}"
        )
        answer = self._exchange("POST", INGEST_PATH, body, token, key, describe)
        if isinstance(answer, _Failure):
            return answer
        return self._itemised(*answer)

    def _exchange(
        self,
        method: str,
        path: str,
        body: bytes | None,
        token: str,
        key: str | None,
        describe: str,
        *,
        absent_ok: bool = False,
    ) -> tuple[int, bytes] | _Failure:
        """One request, with F8's backoff on the transient failures (L12).

        Every verb goes through here, so all of them get the same bounded
        read, timeouts, no redirects and redacted failure codes. `key` is the
        Idempotency-Key less its body digest; a request with a body always
        has one. `absent_ok` returns a 404 as (404, b"") instead of failing —
        read-back's "nothing held".
        """
        headers = {"Authorization": f"Bearer {token}"}
        if body is not None:
            digest = hashlib.sha256(body).hexdigest()[:_KEY_DIGEST_HEX]
            headers["Content-Type"] = "application/json; charset=utf-8"
            headers["Idempotency-Key"] = f"{key}:{digest}"

        def send() -> tuple[int, bytes]:
            # Streamed, so the body is read up to a bound rather than whole;
            # the `with` hands the connection back on every path.
            with self._session.request(
                method,
                self._base_url + path,
                data=body,
                headers=headers,
                timeout=self._timeout,
                allow_redirects=False,
                stream=True,
            ) as response:
                if absent_ok and response.status_code == 404:
                    return 404, b""
                response.raise_for_status()
                return response.status_code, self._read_capped(response)

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
            "%s: HTTP %d, %d bytes sent, %.2fs",
            describe,
            status,
            len(body or b""),
            time.monotonic() - started,
        )
        return status, content

    def _itemised(self, status: int, content: bytes) -> IngestResponse | _Failure:
        """An item-by-item answer, or why it cannot be read as one."""
        if status not in _ITEMISED_STATUSES:
            return _Failure(True, f"http-{status}")
        try:
            parsed = IngestResponse.model_validate_json(content)
        except ValidationError:
            return _Failure(False, "malformed-response")
        if parsed.ingest_schema_version != self._schema_version:
            return _Failure(True, "schema-version-mismatch")
        return parsed

    def _answer[R: BaseModel](
        self, answer: tuple[int, bytes] | _Failure, model: type[R]
    ) -> R | _Failure:
        """A single-object answer (agency delete, sweep, read-back): 200 and
        `model`, in this contract's version."""
        if isinstance(answer, _Failure):
            return answer
        status, content = answer
        if status != 200:
            return _Failure(True, f"http-{status}")
        try:
            parsed = model.model_validate_json(content)
        except ValidationError:
            return _Failure(False, "malformed-response")
        if getattr(parsed, "ingest_schema_version", None) != self._schema_version:
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
        up without a restart (L11 supplies the Vault-backed provider, which
        retries a Vault blip itself).

        A CredentialUnavailableError's message is logged, because it is
        written to be — it is what tells a placeholder secret from Vault being
        down. Any other exception is reduced to its type: its text is
        unknown."""
        try:
            token = self._token_provider()
        except CredentialUnavailableError as exc:
            logger.warning(
                "llm-module credential unavailable: %s",
                sanitize_sensitive_text(str(exc)),
            )
            return None
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

    # -- deletes (L9) ------------------------------------------------------

    def _delete_documents(self, document_ids: tuple[str, ...]) -> DeleteOutcome[str]:
        token = self._token()
        if token is None:
            return DeleteOutcome(
                failed=dict.fromkeys(document_ids, "credential-unavailable")
            )

        def build(refs: Sequence[str]) -> DocumentDeleteRequest:
            return DocumentDeleteRequest(
                ingest_schema_version=self._schema_version,
                agency_id=self.agency_id,
                document_ids=list(refs),
            )

        batches, unsent = self._pack(document_ids, build)
        outcomes: list[DeleteOutcome[str]] = [
            DeleteOutcome(failed=dict.fromkeys(unsent, "request-cap-too-small"))
        ]
        for index, batch in enumerate(batches):
            verdicts = self._delete_rounds(
                batch,
                build,
                DOCUMENT_DELETE_PATH,
                token,
                key=f"{self._run_id}:del:b{index}",
                describe=(
                    f"llm-module delete batch {index + 1}/{len(batches)} "
                    f"({len(batch)} documents)"
                ),
            )
            outcomes.append(_delete_outcome(verdicts, {ref: ref for ref in batch}))
        return DeleteOutcome.combine(*outcomes)

    def _delete_chunks(
        self, document_id: str, ordinals: tuple[int, ...]
    ) -> DeleteOutcome[int]:
        token = self._token()
        if token is None:
            return DeleteOutcome(
                failed=dict.fromkeys(ordinals, "credential-unavailable")
            )
        # C16's coordinates, rendered to the point ids L4 made them.
        by_ref = {
            make_chunk_id(self.agency_id, document_id, ordinal): ordinal
            for ordinal in ordinals
        }

        def build(refs: Sequence[str]) -> ChunkDeleteRequest:
            return ChunkDeleteRequest(
                ingest_schema_version=self._schema_version,
                agency_id=self.agency_id,
                document_id=document_id,
                chunks=[ChunkRef(chunk_id=ref, ordinal=by_ref[ref]) for ref in refs],
            )

        path = chunk_delete_path(validate_segment("document_id", document_id))
        batches, unsent = self._pack(tuple(by_ref), build)
        outcomes: list[DeleteOutcome[int]] = [
            DeleteOutcome(
                failed={by_ref[ref]: "request-cap-too-small" for ref in unsent}
            )
        ]
        for index, batch in enumerate(batches):
            verdicts = self._delete_rounds(
                batch,
                build,
                path,
                token,
                key=f"{self._run_id}:{document_id}:del:b{index}",
                describe=(
                    f"llm-module tail delete {document_id} batch "
                    f"{index + 1}/{len(batches)} ({len(batch)} chunks)"
                ),
            )
            outcomes.append(_delete_outcome(verdicts, by_ref))
        return DeleteOutcome.combine(*outcomes)

    def _delete_agency(self) -> DeleteOutcome[str]:
        """Capability already checked: AGREED_TERMS says points carry a
        filterable agency_id (Open item 7)."""
        agency = self.agency_id
        token = self._token()
        if token is None:
            return DeleteOutcome(failed={agency: "credential-unavailable"})
        body = encode_request(
            AgencyDeleteRequest(
                ingest_schema_version=self._schema_version, agency_id=agency
            )
        )
        answer = self._answer(
            self._exchange(
                "POST",
                agency_delete_path(agency),
                body,
                token,
                f"{self._run_id}:del-agency",
                "llm-module agency delete",
            ),
            DeletedDocumentsResponse,
        )
        if isinstance(answer, _Failure):
            return DeleteOutcome(failed={agency: answer.detail})
        logger.info(
            "llm-module agency delete: %d document(s) removed",
            len(answer.deleted_document_ids),
        )
        if answer.deleted_document_ids:
            return DeleteOutcome(deleted=(agency,))
        return DeleteOutcome(missing=(agency,))

    def _delete_rounds(
        self,
        refs: Sequence[str],
        build: Callable[[Sequence[str]], BaseModel],
        path: str,
        token: str,
        *,
        key: str,
        describe: str,
    ) -> dict[str, tuple[DeleteVerdict, str]]:
        """Each ref's final verdict and code. Retryable items are resent
        alone for up to ITEM_RETRY_ROUNDS more rounds, under a new key each
        round (as an upsert's are); what still fails is `failed`. A request
        that fails outright fails every ref it carried."""
        settled: dict[str, tuple[DeleteVerdict, str]] = {}
        pending = tuple(refs)
        codes: dict[str, str] = {}
        for round_ in range(ITEM_RETRY_ROUNDS + 1):
            if round_:
                self._sleep(full_jitter_delay(round_ - 1, self._policy, self._rand))
            round_key = f"{key}:r{round_}" if round_ else key
            answer = self._exchange(
                "POST", path, encode_request(build(pending)), token, round_key, describe
            )
            response = (
                answer if isinstance(answer, _Failure) else self._itemised(*answer)
            )
            verdicts = (
                response
                if isinstance(response, _Failure)
                else _delete_verdicts(response, pending)
            )
            if isinstance(verdicts, _Failure):
                for ref in pending:
                    settled[ref] = ("failed", verdicts.detail)
                return settled
            retry: list[str] = []
            for ref in pending:
                verdict, code = verdicts[ref]
                if verdict == "retryable":
                    retry.append(ref)
                    codes[ref] = code
                else:
                    settled[ref] = (verdict, code)
            if not retry:
                return settled
            pending = tuple(retry)
        for ref in pending:
            settled[ref] = ("failed", f"items-still-failing: {codes[ref]}")
        return settled

    def _pack(
        self, refs: tuple[str, ...], build: Callable[[Sequence[str]], BaseModel]
    ) -> tuple[list[tuple[str, ...]], tuple[str, ...]]:
        """`refs` in order, in batches of at most DELETE_BATCH_IDS that each
        encode under max_request_bytes, plus the refs left unsent.

        Sizes are exact: the body with one ref, less that ref, is the
        envelope; each ref adds its own size and a comma. Packing stops at
        the first ref that cannot fit even alone, and it and every ref after
        it are returned unsent, for the caller to fail.
        """
        sizes = [self._ref_size(build, ref) for ref in refs]
        envelope = len(encode_request(build(refs[:1]))) - sizes[0]
        batches: list[tuple[str, ...]] = []
        current: list[str] = []
        used = envelope
        for index, (ref, size) in enumerate(zip(refs, sizes, strict=True)):
            extra = size + (1 if current else 0)
            if current and (
                len(current) >= DELETE_BATCH_IDS
                or used + extra > self._max_request_bytes
            ):
                batches.append(tuple(current))
                current, used, extra = [], envelope, size
            if used + extra > self._max_request_bytes:
                if current:
                    batches.append(tuple(current))
                return batches, refs[index:]
            current.append(ref)
            used += extra
        batches.append(tuple(current))
        return batches, ()

    @staticmethod
    def _ref_size(build: Callable[[Sequence[str]], BaseModel], ref: str) -> int:
        """What one ref adds to a delete body: its id, or its {chunk_id,
        ordinal} object."""
        request = build((ref,))
        if isinstance(request, ChunkDeleteRequest):
            return len(encode_request(request.chunks[0]))
        return _encoded_size(ref)

    # -- the stale sweep (L10) ---------------------------------------------

    def _sweep_stale(
        self, before: datetime, keep: frozenset[str]
    ) -> DeleteOutcome[str]:
        if not self._terms.supports_stale_sweep:
            raise NotSupported(
                "the llm-module has not agreed a synced_at sweep (it needs a "
                "filterable agency_id on every point, Open item 7, §13 q4/q12)"
            )
        token = self._token()
        if token is None:
            raise StoreError("llm-module sweep: credential unavailable")
        body = encode_request(
            SweepRequest(
                ingest_schema_version=self._schema_version,
                agency_id=self.agency_id,
                before=format_timestamp(before),
                keep_document_ids=sorted(keep),
            )
        )
        if len(body) > self._max_request_bytes:
            # Never split: each piece of a keep list would delete what the
            # other pieces keep.
            raise StoreError(
                f"llm-module sweep: a keep list of {len(keep)} documents encodes "
                f"to {len(body)} bytes, over LLM_MODULE_MAX_REQUEST_BYTES="
                f"{self._max_request_bytes}; refusing to split it"
            )
        answer = self._answer(
            self._exchange(
                "POST",
                sweep_path(self.agency_id),
                body,
                token,
                f"{self._run_id}:sweep",
                "llm-module stale sweep",
            ),
            DeletedDocumentsResponse,
        )
        if isinstance(answer, _Failure):
            raise StoreError(f"llm-module sweep failed ({answer.detail})")
        deleted = tuple(dict.fromkeys(answer.deleted_document_ids))
        kept_but_deleted = keep.intersection(deleted)
        if kept_but_deleted:
            # The manifest still vouches for these, and Gate 2 will not
            # resend them: say so loudly rather than report a clean sweep.
            # This only detects; prevention is the far side's, which the
            # contract requires to enforce the keep list inside the delete
            # (§10, §13 q12).
            raise StoreError(
                f"llm-module sweep deleted {len(kept_but_deleted)} document(s) "
                "the keep list named; the committed manifest now claims "
                "content the destination does not hold"
            )
        logger.info("llm-module stale sweep: %d document(s) removed", len(deleted))
        return DeleteOutcome(deleted=deleted)

    # -- read-back (L17) ---------------------------------------------------

    def _read_document(self, document_id: str) -> DocumentProbe | None:
        """Capability already checked: AGREED_TERMS promises the probe (Open
        item 10). A failure raises, because "could not ask" must never read
        as "nothing there"."""
        token = self._token()
        if token is None:
            raise StoreError("llm-module read-back: credential unavailable")
        answer = self._exchange(
            "GET",
            document_path(validate_segment("document_id", document_id)),
            None,
            token,
            None,
            f"llm-module read-back {document_id}",
            absent_ok=True,
        )
        if not isinstance(answer, _Failure) and answer[0] == 404:
            return None
        probe = self._answer(answer, DocumentProbeResponse)
        if isinstance(probe, _Failure):
            raise StoreError(
                f"llm-module read-back of {document_id} failed ({probe.detail})"
            )
        if probe.document_id != document_id:
            raise StoreError(
                f"llm-module read-back of {document_id} answered for another "
                "document; refusing to trust it"
            )
        if not probe.record_present and probe.chunk_count == 0:
            return None
        ordinals = (
            tuple(probe.chunk_ordinals)
            if self._terms.read_back_lists_ordinals and probe.chunk_ordinals is not None
            else None
        )
        try:
            return DocumentProbe(
                document_id=document_id,
                record_present=probe.record_present,
                chunk_count=probe.chunk_count,
                chunk_ordinals=ordinals,
            )
        except ValueError:
            raise StoreError(
                f"llm-module read-back of {document_id} is inconsistent "
                "(chunk_count disagrees with chunk_ordinals)"
            ) from None

    def _holds_agency_content(self) -> bool:
        raise NotSupported("the llm-module sink cannot list (can_list=False)")


def _delete_verdicts(
    response: IngestResponse, refs: Sequence[str]
) -> dict[str, tuple[DeleteVerdict, str]] | _Failure:
    """Each sent ref's delete verdict and code — the same trust rules as an
    upsert's: a ref answered twice or never sent spoils the answer, and one
    left out is resent."""
    sent = set(refs)
    items: dict[str, IngestItem] = {}
    for item in response.items:
        if item.ref in items or item.ref not in sent:
            return _Failure(False, "malformed-response")
        items[item.ref] = item
    verdicts: dict[str, tuple[DeleteVerdict, str]] = {}
    for ref in refs:
        item = items.get(ref)
        if item is None:
            verdicts[ref] = ("retryable", "unacknowledged")
            continue
        code = str(item.status)
        if item.code:
            code = f"{code}:{_short_code(item.code)}"
        verdicts[ref] = (classify_delete_status(item.status), code)
    return verdicts


def _delete_outcome[T: (str, int)](
    verdicts: dict[str, tuple[DeleteVerdict, str]], items: dict[str, T]
) -> DeleteOutcome[T]:
    """Ref verdicts, reported in the caller's vocabulary (D3)."""
    deleted: list[T] = []
    missing: list[T] = []
    failed: dict[T, str] = {}
    for ref, (verdict, code) in verdicts.items():
        item = items[ref]
        if verdict == "deleted":
            deleted.append(item)
        elif verdict == "missing":
            missing.append(item)
        else:
            failed[item] = code
    return DeleteOutcome(deleted=tuple(deleted), missing=tuple(missing), failed=failed)


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
