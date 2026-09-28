"""Invariant constants.

Pure: no I/O, no network, no destination — so it belongs under core/
alongside the rest of the unit-tested heart.

These are the values an operator must NOT be able to change at runtime.
They are baked into the code, not read from the environment, because they
feed the chunker fingerprint: if any of them could drift independently of
a deliberate code change, the fingerprint would stop being a reliable
signal that "something about how we process documents changed" and a
corpus could end up with mixed geometry with no record of how that
happened. Bump a value here only when you deliberately want every document
to be treated as changed on the next run.
"""

from typing import NamedTuple

# Mixed into make_chunk_id() alongside ID_VERSION, the document id and the
# ordinal, so a chunk id from this service cannot collide with an id
# produced by an unrelated system that happens to hash the same way.
ID_NAMESPACE = "ckb-content-external"

# Bump only to deliberately re-key every chunk id ever produced — e.g. if
# make_chunk_id()'s hashing scheme itself changes. A bump makes every
# existing chunk id look new, which is exactly the point: it forces a full
# re-publish rather than silently colliding old ids with new ones.
ID_VERSION = 1

# Bump when chunking.py's algorithm changes in a way that would move chunk
# boundaries for existing documents (separator priority, the Estonian
# ordinal guard, the overlap/termination logic, etc).
CHUNKER_VERSION = 1

# Bump when text_normaliser.py's normalisation rules change (e.g. a new
# zero-width character added to the strip set).
NORMALISER_VERSION = 1


class ChunkProfile(NamedTuple):
    """One named chunk-geometry preset. All four sizes are in characters."""

    target: int
    overlap: int
    min: int
    max: int


# Named presets only — never four independent knobs an operator can set
# separately. Chunk size hinges on who embeds the chunks: azure_native
# assumes the destination does its own vectorisation with a generous
# token budget; compact is for an embedder with a tight token limit, where
# an azure_native-sized Estonian chunk would be silently truncated.
#
# Add a third preset here if neither fits a given llm-module deployment's
# embedder — that is cheap. Exposing the four numbers as separate settings
# is exactly what this table exists to prevent.
CHUNK_PROFILES: dict[str, ChunkProfile] = {
    "azure_native": ChunkProfile(target=1200, overlap=200, min=200, max=2000),
    "compact": ChunkProfile(target=450, overlap=80, min=120, max=700),
}

DEFAULT_CHUNK_PROFILE = "azure_native"

# Blob key templates. The literal layout strings live here as the single
# source of truth; ids.py's key-builder functions are what fill in
# {id}/{ordinal}/{run_id} — no destination-specific logic (no sink, no
# store client) belongs in this file.
MANIFEST_BLOB_KEY_TEMPLATE = "manifest.json"
DOCUMENT_METADATA_BLOB_KEY_TEMPLATE = "documents/{document_id}/metadata.json"
DOCUMENT_CHUNK_BLOB_KEY_TEMPLATE = (
    "documents/{document_id}/chunks/{ordinal:05d}.json"
)
PENDING_DELETIONS_BLOB_KEY_TEMPLATE = "_control/deletions/pending-{run_id}.jsonl"
