"""The one place CONTENT_SINK becomes a sink (D0, A13).

No other module may branch on which sink is configured. That rule is enforced
by tests/test_exporter_sink_resolution.py, which allows a comparison or a
dispatch table on a sink name here, in stores/factory.py and in api/config.py,
and nowhere else.

`object_store` is built over the destination store from stores/factory.py
(D7, D14). `llm_module` is the HTTP sink (Stage L), given the run id its
Idempotency-Keys carry and a token provider that reads Vault on every call
(L11). It is refused while the ingest contract is PROPOSED — the startup
validator refuses it first, and this re-asserts it, so no path selects the
sink against an unagreed contract. Nothing calls this at startup, so neither
a refusal here nor a missing EXTERNAL_S3_* value can affect /health.

THE SINK ID IS THE CONTENT_SINK VALUE. The manifest records it (F17), and a
mismatch fails the run rather than reading as a first run. Bucket, endpoint
and base URL are deliberately not part of it: moving the same content to a
new address is not a change of destination, and must not trip that guard.
"""

import logging
from collections.abc import Callable

from exporter.api.config import (
    ConfigurationError,
    Settings,
    fetch_llm_module_token,
    llm_module_contract_refusal,
)
from exporter.sinks.base import ContentSink
from exporter.sinks.llm_module_contract import AGREED_TERMS
from exporter.sinks.llm_module_sink import LlmModuleSink
from exporter.sinks.object_store_sink import ObjectStoreSink
from exporter.stores.factory import build_destination_store

logger = logging.getLogger(__name__)


def _build_object_store(settings: Settings, agency_id: str, run_id: str) -> ContentSink:
    return ObjectStoreSink(
        store=build_destination_store(settings),
        prefix=settings.content_external_prefix,
        agency_id=agency_id,
        sink_id=settings.content_sink,
    )


def _build_llm_module(settings: Settings, agency_id: str, run_id: str) -> ContentSink:
    refusal = llm_module_contract_refusal(
        allow_proposed=settings.llm_module_allow_proposed_contract
    )
    if refusal is not None:
        raise ConfigurationError(refusal)
    if AGREED_TERMS.status != "agreed":
        logger.warning(
            "CONTENT_SINK=llm_module against the PROPOSED ingest contract "
            "(LLM_MODULE_ALLOW_PROPOSED_CONTRACT=true): development only"
        )
    return LlmModuleSink(
        base_url=settings.llm_module_base_url,
        agency_id=agency_id,
        sink_id=settings.content_sink,
        run_id=run_id,
        token_provider=lambda: fetch_llm_module_token(settings),
        ingest_schema_version=settings.llm_module_ingest_schema_version,
        max_request_bytes=settings.llm_module_max_request_bytes,
        connect_timeout=settings.llm_module_connect_timeout,
        read_timeout=settings.llm_module_read_timeout,
    )


# CONTENT_SINK value -> builder.
_SINKS: dict[str, Callable[[Settings, str, str], ContentSink]] = {
    "object_store": _build_object_store,
    "llm_module": _build_llm_module,
}


def build_sink(settings: Settings, *, agency_id: str, run_id: str) -> ContentSink:
    """The configured sink, bound to `agency_id` for the run `run_id`."""
    builder = _SINKS.get(settings.content_sink)
    if builder is None:
        raise ConfigurationError(
            f"CONTENT_SINK={settings.content_sink} is not a known sink"
        )
    return builder(settings, agency_id, run_id)
