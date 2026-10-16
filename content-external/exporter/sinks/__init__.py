"""Content sinks — the destinations this service can publish to.

ONE PLACE RESOLVES THE SINK (A13).

`sinks/factory.py` (D0) maps CONTENT_SINK to a class, and it is the only
place in this service that may branch on which sink is configured. No
`if sink == "llm_module"` may appear under `core/`, `services/` or `api/`.

This is the same rule D7 sets for store backends, restated because there are
now two axes to get wrong: CONTENT_SINK picks the sink, and
CONTENT_EXTERNAL_STORE_BACKEND picks the store *beneath* the object-store
sink. Two factories, two axes, no third place.

Why it matters more than a tidiness rule. Stage F's entire deliverable is
ordering, and it holds a `ContentSink` it cannot interrogate — that is what
lets the object-store sink meet the F3 guarantee by write order and the
llm-module sink meet it by sending one request, without the coordinator
knowing which. A single `if` in `services/` reintroduces the fork this
layering exists to prevent, and it reintroduces it in the one component least
able to absorb it.

The rule is enforced by tests/test_exporter_sink_resolution.py rather than by
review, because a convention that only lives in a docstring is a convention
that survives exactly as long as the person who wrote it.

Contents arrive in Stage D: base.py (the ContentSink ABC and
SinkCapabilities), object_store_sink.py, factory.py. Stage L adds
llm_module_sink.py and nothing else.
"""
