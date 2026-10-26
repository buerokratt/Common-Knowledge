"""An in-process llm-module, for testing the llm-module sink with no network.

`FakeLlmModule` implements the far side of the proposed ingest contract
(content-external/docs/llm-module-ingest-contract.md): merge-or-create on
`chunk_id`, one item per submitted ref, 207 when any item failed, and — §7 —
a request whose record is not applied applies none of its chunks. Failures
are scripted: a whole-request reply or exception, an item status for a given
ref, or an item left out of the response.

`session_for(fake)` returns a real `requests.Session` with the fake mounted
on https://, so the sink's own transport code — headers, timeouts,
raise_for_status, the backoff wrapper — runs unchanged. This is the seed of
L15's stub: a FastAPI app can later wrap the same `handle`.
"""

import io
import json
import threading
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

__all__ = ["FakeLlmModule", "Reply", "Seen", "session_for"]


@dataclass(frozen=True)
class Reply:
    """A scripted whole-request answer. `body=None` sends an empty body."""

    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes | None = None


@dataclass(frozen=True)
class Seen:
    """One request as the fake received it."""

    headers: Mapping[str, str]
    raw: bytes
    body: dict[str, Any]
    timeout: object
    verify: object

    @property
    def chunk_ids(self) -> list[str]:
        return [chunk["chunk_id"] for chunk in self.body["chunks"] or ()]

    @property
    def key(self) -> str:
        """The Idempotency-Key less its trailing body digest."""
        return self.headers["Idempotency-Key"].rsplit(":", 1)[0]

    @property
    def key_digest(self) -> str:
        return self.headers["Idempotency-Key"].rsplit(":", 1)[1]


class FakeLlmModule:
    """The llm-module's ingest endpoint, held in memory."""

    def __init__(self, *, schema_version: int = 1) -> None:
        self.schema_version = schema_version
        self.records: dict[str, dict[str, Any]] = {}
        self.points: dict[str, dict[str, Any]] = {}
        self.requests: list[Seen] = []
        self._replies: deque[Reply | BaseException] = deque()
        # Per ref, what its next submissions get: a failure, or None to apply.
        self._item_failures: dict[str, deque[tuple[int, str | None] | None]] = {}
        self._dropped: set[str] = set()
        self._lock = threading.Lock()

    # -- scripting ---------------------------------------------------------

    def reply_next(self, *replies: Reply | BaseException) -> None:
        """Answer the next requests with these, in order, instead of
        processing them. An exception is raised from the transport."""
        self._replies.extend(replies)

    def fail_item(
        self,
        ref: str,
        status: int,
        code: str | None = None,
        *,
        times: int = 1,
        after: int = 0,
    ) -> None:
        """After `after` submissions of `ref` are applied, the next `times`
        get `status`, unapplied."""
        queue = self._item_failures.setdefault(ref, deque())
        queue.extend([None] * after)
        queue.extend([(status, code)] * times)

    def drop_item(self, ref: str) -> None:
        """Apply `ref` next time, but leave it out of the response."""
        self._dropped.add(ref)

    # -- the endpoint ------------------------------------------------------

    def handle(
        self, request: requests.PreparedRequest, *, timeout: object, verify: object
    ) -> tuple[int, dict[str, str], bytes]:
        raw = request.body if isinstance(request.body, bytes) else b""
        with self._lock:
            body = json.loads(raw)
            self.requests.append(
                Seen(dict(request.headers), raw, body, timeout, verify)
            )
            if self._replies:
                reply = self._replies.popleft()
                if isinstance(reply, BaseException):
                    raise reply
                return reply.status, dict(reply.headers), reply.body or b""
            items = self._apply(body)
            failed = any(not 200 <= item["status"] < 300 for item in items)
            items = [item for item in items if item["ref"] not in self._dropped]
            self._dropped.clear()
            payload = {"ingest_schema_version": self.schema_version, "items": items}
            return (
                207 if failed else 200,
                {"Content-Type": "application/json"},
                json.dumps(payload).encode("utf-8"),
            )

    def _take(self, ref: str) -> tuple[int, str | None] | None:
        queue = self._item_failures.get(ref)
        return queue.popleft() if queue else None

    def _apply(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        chunks = body["chunks"]
        refs = ["record", *(chunk["chunk_id"] for chunk in chunks or ())]
        record_failure = self._take("record")
        if record_failure is not None:
            # §7: the record did not land, so neither does any chunk.
            status, code = record_failure
            return [{"ref": ref, "status": status, "code": code} for ref in refs]

        document_id = body["document_id"]
        envelope = {k: v for k, v in body.items() if k not in ("chunks", "part")}
        self.records[document_id] = envelope
        items: list[dict[str, Any]] = [{"ref": "record", "status": 200}]
        if chunks is None:
            # A metadata-only merge: every existing point takes the new
            # record fields, and its text and embedding stay as they are.
            for point in self.points.values():
                if point["document_id"] == document_id:
                    point["record"] = envelope
            return items
        for chunk in chunks:
            failure = self._take(chunk["chunk_id"])
            if failure is not None:
                status, code = failure
                items.append({"ref": chunk["chunk_id"], "status": status, "code": code})
                continue
            self.points[chunk["chunk_id"]] = {
                "agency_id": body["agency_id"],
                "document_id": document_id,
                "ordinal": chunk["ordinal"],
                "text": chunk["text"],
                "synced_at": body["synced_at"],
                "record": envelope,
            }
            items.append({"ref": chunk["chunk_id"], "status": 200})
        return items

    def points_of(self, document_id: str) -> dict[str, dict[str, Any]]:
        return {
            chunk_id: point
            for chunk_id, point in self.points.items()
            if point["document_id"] == document_id
        }


class _Adapter(BaseAdapter):
    def __init__(self, fake: FakeLlmModule) -> None:
        super().__init__()
        self._fake = fake

    def send(
        self,
        request: requests.PreparedRequest,
        stream: bool = False,
        timeout: object = None,
        verify: object = True,
        cert: object = None,
        proxies: object = None,
    ) -> requests.Response:
        status, headers, content = self._fake.handle(
            request, timeout=timeout, verify=verify
        )
        response = requests.Response()
        response.status_code = status
        response.reason = str(status)
        response.headers = CaseInsensitiveDict(headers)
        # A file-like raw, so a streamed read (iter_content) and requests'
        # own read of a non-streamed body both work as against a socket.
        response.raw = io.BytesIO(content)
        response.encoding = "utf-8"
        response.url = request.url or ""
        response.request = request
        return response

    def close(self) -> None:
        pass


def session_for(fake: FakeLlmModule) -> requests.Session:
    """A real Session whose https:// traffic goes to `fake`."""
    session = requests.Session()
    session.mount("https://", _Adapter(fake))
    return session
