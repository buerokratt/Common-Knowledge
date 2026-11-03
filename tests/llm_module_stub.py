"""A stub llm-module (L15): the proposed ingest contract as a FastAPI app.

It serves content-external/docs/llm-module-ingest-contract.md over a
`FakeLlmModule` (tests/llm_module_fake.py), so the far side's behaviour lives
in one place and both the in-process tests and a laptop run use it:

- In CI, `session_for(fake, via_stub=True)` sends the sink's requests through
  this app in-process (Starlette's TestClient), with no network. The sink
  conformance suite runs that way (L16).
- On a laptop it is a real server. The sink refuses plain http://, so serve
  it over TLS with a self-signed certificate and point the exporter's
  REQUESTS_CA_BUNDLE at that certificate:

      uv run uvicorn --app-dir tests llm_module_stub:app --port 8443 \\
          --ssl-keyfile stub.key --ssl-certfile stub.crt

  The module-level `app` holds a fresh fake; every failure script is the
  fake's, so a laptop run sees a well-behaved llm-module.

Nothing here is shipped: it lives under tests/ and adds no dependency
(FastAPI is already the service's own, and TestClient's httpx is in the dev
group).
"""

import json
from typing import Any

from fastapi import FastAPI, Request, Response
from llm_module_fake import FakeLlmModule

__all__ = ["TRANSPORT_HEADER", "app", "build_stub_app"]

# How the in-process adapter hands the caller's timeout and TLS setting to
# the fake, which would otherwise only see them on a direct mount. Stripped
# before the fake records the request's headers.
TRANSPORT_HEADER = "x-stub-transport"


def _transport(raw: str | None) -> tuple[Any, Any]:
    if raw is None:
        return None, None
    parsed = json.loads(raw)
    timeout = parsed.get("timeout")
    return (tuple(timeout) if isinstance(timeout, list) else timeout), parsed.get(
        "verify"
    )


def build_stub_app(fake: FakeLlmModule | None = None) -> FastAPI:
    """The stub, serving `fake` (a new one when None)."""
    served = fake if fake is not None else FakeLlmModule()
    stub = FastAPI(title="llm-module stub", docs_url=None, redoc_url=None)
    stub.state.fake = served

    @stub.api_route("/ingest/v1/{rest:path}", methods=["GET", "POST"])
    async def ingest(rest: str, request: Request) -> Response:
        raw = await request.body()
        headers = {
            name: value
            for name, value in request.headers.items()
            if name != TRANSPORT_HEADER
        }
        timeout, verify = _transport(request.headers.get(TRANSPORT_HEADER))
        status, out_headers, content = served.handle(
            request.method,
            request.url.path,
            _title_case(headers),
            raw,
            timeout=timeout,
            verify=verify,
        )
        return Response(content=content, status_code=status, headers=out_headers)

    return stub


def _title_case(headers: dict[str, str]) -> dict[str, str]:
    """ASGI lower-cases header names; the fake's tests read them as sent
    (`Idempotency-Key`, `Authorization`)."""
    return {
        "-".join(p.capitalize() for p in k.split("-")): v for k, v in headers.items()
    }


app = build_stub_app()
