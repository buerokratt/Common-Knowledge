"""The one place CONTENT_SINK becomes a sink (D0, A13).

No other module may branch on which sink is configured. That rule is enforced
by tests/test_exporter_sink_resolution.py, which allows a comparison or a
dispatch table on a sink name here, in stores/factory.py and in api/config.py,
and nowhere else.

SKELETON UNTIL D14. Neither sink class exists yet, so every CONTENT_SINK
value maps to the task that builds it, and `build_sink` refuses with a message
naming that task. D14 replaces the `object_store` entry with a constructor that
receives an ExternalStoreClient from stores/factory.py (D7); L1 does the same
for `llm_module`. Nothing calls this yet — Stage F wires it into the run — so
the refusal cannot affect startup.
"""

from exporter.api.config import ConfigurationError, Settings
from exporter.sinks.base import ContentSink

# CONTENT_SINK value -> the task that will register an implementation.
_NOT_YET_BUILT: dict[str, str] = {
    "object_store": "D14",
    "llm_module": "L1",
}


def build_sink(settings: Settings, *, agency_id: str) -> ContentSink:
    """The configured sink, bound to `agency_id`."""
    name = settings.content_sink
    task = _NOT_YET_BUILT.get(name)
    if task is not None:
        raise ConfigurationError(
            f"CONTENT_SINK={name} has no implementation in this build yet "
            f"(task {task}); the sink layer currently ships interfaces only."
        )
    raise ConfigurationError(f"CONTENT_SINK={name} is not a known sink")
