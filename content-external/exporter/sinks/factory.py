"""The one place CONTENT_SINK becomes a sink (D0, A13).

No other module may branch on which sink is configured. That rule is enforced
by tests/test_exporter_sink_resolution.py, which allows a comparison or a
dispatch table on a sink name here, in stores/factory.py and in api/config.py,
and nowhere else.

`object_store` is built over the destination store from stores/factory.py
(D7, D14). `llm_module` still refuses, naming L9: the sink itself exists
(sinks/llm_module_sink.py, L1-L8), but it cannot delete yet, and the
coordinator deletes on any run that sees a deletion — so selecting it now
would fail the first such run. It is registered here, with its run id and its
Vault-backed token provider (L11), when L9 lands. Nothing calls this at
startup, so neither the refusal nor a missing EXTERNAL_S3_* value can affect
/health.

THE SINK ID IS THE CONTENT_SINK VALUE. The manifest records it (F17), and a
mismatch fails the run rather than reading as a first run. Bucket and endpoint
are deliberately not part of it: moving the same objects to a new endpoint is
not a change of destination, and must not trip that guard.
"""

from collections.abc import Callable

from exporter.api.config import ConfigurationError, Settings
from exporter.sinks.base import ContentSink
from exporter.sinks.object_store_sink import ObjectStoreSink
from exporter.stores.factory import build_destination_store


def _build_object_store(settings: Settings, agency_id: str) -> ContentSink:
    return ObjectStoreSink(
        store=build_destination_store(settings),
        prefix=settings.content_external_prefix,
        agency_id=agency_id,
        sink_id=settings.content_sink,
    )


# CONTENT_SINK value -> builder.
_SINKS: dict[str, Callable[[Settings, str], ContentSink]] = {
    "object_store": _build_object_store,
}

# CONTENT_SINK value -> the task that will register an implementation.
_NOT_YET_BUILT: dict[str, str] = {
    "llm_module": "L9",
}


def build_sink(settings: Settings, *, agency_id: str) -> ContentSink:
    """The configured sink, bound to `agency_id`."""
    name = settings.content_sink
    builder = _SINKS.get(name)
    if builder is not None:
        return builder(settings, agency_id)
    task = _NOT_YET_BUILT.get(name)
    if task is not None:
        raise ConfigurationError(
            f"CONTENT_SINK={name} has no implementation in this build yet "
            f"(task {task})."
        )
    raise ConfigurationError(f"CONTENT_SINK={name} is not a known sink")
