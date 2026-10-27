"""An in-process llm-module, for testing the llm-module sink with no network.

`FakeLlmModule` implements the far side of the proposed ingest contract
(content-external/docs/llm-module-ingest-contract.md):

- ingest: merge-or-create on `chunk_id`, one item per submitted ref, 207 when
  any item failed, and — §7 — a request whose record is not applied applies
  none of its chunks;
- deletes (L9): whole documents (points first, then the record — F4), tail
  chunks by point id, and a whole agency;
- the stale sweep (L10): the agency's documents synced before `before` and
  absent from the keep list;
- read-back (L17): the record and the ordinals held for a document.

Every write and delete lands in `events` as ("put" | "delete", document_id,
ordinal | None), None being the record, so the conformance suite can assert
F3 and F4 from the far side's point of view.

Failures are scripted: a whole-request reply or exception, an item status
for a given ref (`fail_item`, or `fail_always` until `heal`), an item left
out of the response, a response slower than the caller's read timeout, or a
body over a size limit (413).

`session_for(fake)` returns a real `requests.Session` with the fake mounted
on https://, so the sink's own transport code — headers, timeouts,
raise_for_status, the backoff wrapper — runs unchanged. With
`via_stub=True` each request instead goes through the FastAPI stub in
tests/llm_module_stub.py (L15), so the stub app itself is what is exercised.
"""

import io
import json
import threading
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict

__all__ = ["Event", "FakeLlmModule", "Reply", "Seen", "session_for"]

# ("put" | "delete", document_id, ordinal | None); None is the record.
Event = tuple[str, str, int | None]

_PREFIX = "/ingest/v1"


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
    body: Any
    timeout: object
    verify: object
    method: str = "POST"
    path: str = f"{_PREFIX}/documents"

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


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class FakeLlmModule:
    """The llm-module's ingest API, held in memory."""

    def __init__(self, *, schema_version: int = 1) -> None:
        self.schema_version = schema_version
        self.records: dict[str, dict[str, Any]] = {}
        self.points: dict[str, dict[str, Any]] = {}
        self.requests: list[Seen] = []
        self.events: list[Event] = []
        self._replies: deque[Reply | BaseException] = deque()
        # Per ref, what its next submissions get: a failure, or None to apply.
        self._item_failures: dict[str, deque[tuple[int, str | None] | None]] = {}
        self._persistent: dict[str, tuple[int, str | None]] = {}
        self._dropped: set[str] = set()
        self._delay: float | None = None
        self._max_body: int | None = None
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

    def fail_always(self, ref: str, status: int, code: str | None = None) -> None:
        """Every submission of `ref` gets `status`, unapplied, until heal()."""
        self._persistent[ref] = (status, code)

    def heal(self) -> None:
        """Forget every scripted item failure."""
        self._persistent.clear()
        self._item_failures.clear()

    def drop_item(self, ref: str) -> None:
        """Apply `ref` next time, but leave it out of the response."""
        self._dropped.add(ref)

    def respond_after(self, seconds: float | None) -> None:
        """Every request takes `seconds`: one whose read timeout is shorter
        raises ReadTimeout, as a socket would. None is instant again."""
        self._delay = seconds

    def reject_bodies_over(self, limit: int | None) -> None:
        """Answer 413 to a body over `limit` bytes, applying nothing."""
        self._max_body = limit

    # -- the API -----------------------------------------------------------

    def handle(
        self,
        method: str,
        path: str,
        headers: Mapping[str, str],
        raw: bytes,
        *,
        timeout: object = None,
        verify: object = None,
    ) -> tuple[int, dict[str, str], bytes]:
        with self._lock:
            body = json.loads(raw) if raw else None
            self.requests.append(
                Seen(dict(headers), raw, body, timeout, verify, method, path)
            )
            self._wait(timeout)
            if self._replies:
                reply = self._replies.popleft()
                if isinstance(reply, BaseException):
                    raise reply
                return reply.status, dict(reply.headers), reply.body or b""
            if self._max_body is not None and len(raw) > self._max_body:
                return 413, {}, b""
            return self._route(method, path, body)

    def _wait(self, timeout: object) -> None:
        if self._delay is None:
            return
        read = timeout[1] if isinstance(timeout, tuple) else timeout
        if isinstance(read, int | float) and read < self._delay:
            raise requests.ReadTimeout("the fake llm-module answered too slowly")

    def _route(
        self, method: str, path: str, body: dict[str, Any] | None
    ) -> tuple[int, dict[str, str], bytes]:
        parts = path.removeprefix(_PREFIX).strip("/").split("/")
        if method == "GET" and len(parts) == 2 and parts[0] == "documents":
            probe = self.probe(parts[1])
            if probe is None:
                return 404, {}, b""
            return self._json(probe)
        if method != "POST" or body is None:
            return 404, {}, b""
        if parts == ["documents"]:
            return self._items(self._apply(body))
        if parts == ["documents", "delete"]:
            return self._items(self._delete_documents(body["document_ids"]))
        if (
            len(parts) == 4
            and parts[0] == "documents"
            and parts[2:] == ["chunks", "delete"]
        ):
            return self._items(self._delete_chunks(parts[1], body["chunks"]))
        if len(parts) == 3 and parts[0] == "agencies" and parts[2] == "delete":
            return self._json({"deleted_document_ids": self._delete_agency(parts[1])})
        if len(parts) == 3 and parts[0] == "agencies" and parts[2] == "sweep":
            swept = self._sweep(
                parts[1],
                _parse_time(body["before"]),
                set(body["keep_document_ids"]),
            )
            return self._json({"deleted_document_ids": swept})
        return 404, {}, b""

    def _json(self, payload: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
        payload = {"ingest_schema_version": self.schema_version, **payload}
        return (
            200,
            {"Content-Type": "application/json"},
            json.dumps(payload).encode("utf-8"),
        )

    def _items(self, items: list[dict[str, Any]]) -> tuple[int, dict[str, str], bytes]:
        failed = any(not 200 <= item["status"] < 300 for item in items)
        items = [item for item in items if item["ref"] not in self._dropped]
        self._dropped.clear()
        status, headers, content = self._json({"items": items})
        return (207 if failed else status), headers, content

    def _take(self, ref: str) -> tuple[int, str | None] | None:
        if ref in self._persistent:
            return self._persistent[ref]
        queue = self._item_failures.get(ref)
        return queue.popleft() if queue else None

    # -- ingest ------------------------------------------------------------

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
        self.events.append(("put", document_id, None))
        items: list[dict[str, Any]] = [{"ref": "record", "status": 200}]
        if chunks is None:
            # A metadata-only merge: every existing point takes the new
            # record fields, and its text and embedding stay as they are.
            for point in self.points.values():
                if point["document_id"] == document_id:
                    point["record"] = envelope
                    point["synced_at"] = body["synced_at"]
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
            self.events.append(("put", document_id, chunk["ordinal"]))
            items.append({"ref": chunk["chunk_id"], "status": 200})
        return items

    # -- deletes and the sweep ---------------------------------------------

    def _remove_document(self, document_id: str) -> bool:
        """Points first, then the record (F4). True if anything was held."""
        held = False
        for chunk_id, point in sorted(
            self.points_of(document_id).items(), key=lambda item: item[1]["ordinal"]
        ):
            del self.points[chunk_id]
            self.events.append(("delete", document_id, point["ordinal"]))
            held = True
        if self.records.pop(document_id, None) is not None:
            self.events.append(("delete", document_id, None))
            held = True
        return held

    def _delete_documents(self, document_ids: list[str]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for document_id in document_ids:
            failure = self._take(document_id)
            if failure is not None:
                status, code = failure
                items.append({"ref": document_id, "status": status, "code": code})
                continue
            found = self._remove_document(document_id)
            items.append({"ref": document_id, "status": 200 if found else 404})
        return items

    def _delete_chunks(
        self, document_id: str, chunks: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for chunk in chunks:
            chunk_id = chunk["chunk_id"]
            failure = self._take(chunk_id)
            if failure is not None:
                status, code = failure
                items.append({"ref": chunk_id, "status": status, "code": code})
                continue
            point = self.points.get(chunk_id)
            if point is None or point["document_id"] != document_id:
                items.append({"ref": chunk_id, "status": 404})
                continue
            del self.points[chunk_id]
            self.events.append(("delete", document_id, point["ordinal"]))
            items.append({"ref": chunk_id, "status": 200})
        return items

    def _documents_of(self, agency_id: str) -> dict[str, str]:
        """document_id -> its newest synced_at, over its record and points."""
        held: dict[str, str] = {}
        holders = [*self.records.values(), *self.points.values()]
        for holder in holders:
            if holder["agency_id"] != agency_id:
                continue
            document_id = holder["document_id"]
            synced = holder["synced_at"]
            if document_id not in held or _parse_time(synced) > _parse_time(
                held[document_id]
            ):
                held[document_id] = synced
        return held

    def _delete_agency(self, agency_id: str) -> list[str]:
        deleted = sorted(self._documents_of(agency_id))
        for document_id in deleted:
            self._remove_document(document_id)
        return deleted

    def _sweep(self, agency_id: str, before: datetime, keep: set[str]) -> list[str]:
        stale = sorted(
            document_id
            for document_id, synced in self._documents_of(agency_id).items()
            if document_id not in keep and _parse_time(synced) < before
        )
        for document_id in stale:
            self._remove_document(document_id)
        return stale

    # -- reading the state -------------------------------------------------

    def points_of(self, document_id: str) -> dict[str, dict[str, Any]]:
        return {
            chunk_id: point
            for chunk_id, point in self.points.items()
            if point["document_id"] == document_id
        }

    def probe(self, document_id: str) -> dict[str, Any] | None:
        """What GET /ingest/v1/documents/{id} answers, or None for a 404."""
        ordinals = sorted(p["ordinal"] for p in self.points_of(document_id).values())
        if document_id not in self.records and not ordinals:
            return None
        return {
            "document_id": document_id,
            "record_present": document_id in self.records,
            "chunk_count": len(ordinals),
            "chunk_ordinals": ordinals,
        }

    def plant_point(
        self,
        document_id: str,
        ordinal: int,
        *,
        chunk_id: str,
        agency_id: str,
        synced_at: str,
    ) -> None:
        """A point the exporter did not write in this test — an orphan left
        by an earlier run, for the sweep to find."""
        self.points[chunk_id] = {
            "agency_id": agency_id,
            "document_id": document_id,
            "ordinal": ordinal,
            "text": "",
            "synced_at": synced_at,
            "record": None,
        }


def _response(
    request: requests.PreparedRequest,
    status: int,
    headers: Mapping[str, str],
    content: bytes,
) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response.reason = str(status)
    response.headers = CaseInsensitiveDict(headers)
    # A file-like raw, so a streamed read (iter_content) and requests' own
    # read of a non-streamed body both work as against a socket.
    response.raw = io.BytesIO(content)
    response.encoding = "utf-8"
    response.url = request.url or ""
    response.request = request
    return response


def _body(request: requests.PreparedRequest) -> bytes:
    return request.body if isinstance(request.body, bytes) else b""


class _Adapter(BaseAdapter):
    """Straight into the fake, in the caller's thread."""

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
            request.method or "GET",
            urlsplit(request.url or "").path,
            dict(request.headers),
            _body(request),
            timeout=timeout,
            verify=verify,
        )
        return _response(request, status, headers, content)

    def close(self) -> None:
        pass


class _StubAdapter(BaseAdapter):
    """Through the FastAPI stub app (L15), in-process via TestClient."""

    def __init__(self, fake: FakeLlmModule) -> None:
        super().__init__()
        # Imported here: the plain fake must not need FastAPI.
        from fastapi.testclient import TestClient
        from llm_module_stub import TRANSPORT_HEADER, build_stub_app

        self._header = TRANSPORT_HEADER
        self._client = TestClient(build_stub_app(fake), base_url="https://stub")

    def send(
        self,
        request: requests.PreparedRequest,
        stream: bool = False,
        timeout: object = None,
        verify: object = True,
        cert: object = None,
        proxies: object = None,
    ) -> requests.Response:
        headers = dict(request.headers)
        headers[self._header] = json.dumps(
            {
                "timeout": list(timeout) if isinstance(timeout, tuple) else timeout,
                "verify": verify,
            }
        )
        answer = self._client.request(
            request.method or "GET",
            urlsplit(request.url or "").path,
            content=_body(request),
            headers=headers,
        )
        return _response(request, answer.status_code, answer.headers, answer.content)

    def close(self) -> None:
        self._client.close()


def session_for(fake: FakeLlmModule, *, via_stub: bool = False) -> requests.Session:
    """A real Session whose https:// traffic goes to `fake` — directly, or
    through the FastAPI stub app when `via_stub`."""
    session = requests.Session()
    session.mount("https://", _StubAdapter(fake) if via_stub else _Adapter(fake))
    return session
