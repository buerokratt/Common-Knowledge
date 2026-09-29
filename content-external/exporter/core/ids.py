"""Chunk id generation.

A chunk id is a function of coordinates only — agency, document and
ordinal — never of the chunk's own text. If it hashed the text, editing
one paragraph would shift every downstream chunk's content and re-key the
whole document: a small correction would look like a full delete-and-
reinsert. Keyed on coordinates, the same edit overwrites the *same* ids,
so the update path is idempotent by construction. Whether the text itself
changed is still detected separately, via content_sha256 in the manifest —
that is the right place for it, not here.
"""

import hashlib

from exporter.core.constants import ID_NAMESPACE, ID_VERSION

# Hex digest length, in bytes -> 32 hex characters. "128" in blake2b_128
# refers to this digest size, not to blake2b's own (512-bit) internal state.
_CHUNK_ID_DIGEST_SIZE = 16

# Leads every id, so a chunk id is never mistaken for a plain hex number
# and is always safe as a key/identifier in stores that dislike a
# leading digit.
_CHUNK_ID_PREFIX = "c"


def make_chunk_id(agency_id: str, document_id: str, ordinal: int) -> str:
    """A chunk's id: "c" + blake2b_128 hex of its coordinates.

    Coordinates only — agency_id, document_id, ordinal — plus ID_NAMESPACE
    and ID_VERSION so this service's ids cannot collide with an unrelated
    system's, and so a deliberate ID_VERSION bump re-keys every chunk this
    service has ever produced.
    """
    payload = "\x1f".join(
        (ID_NAMESPACE, str(ID_VERSION), agency_id, document_id, str(ordinal))
    ).encode("utf-8")
    digest = hashlib.blake2b(payload, digest_size=_CHUNK_ID_DIGEST_SIZE).hexdigest()
    return _CHUNK_ID_PREFIX + digest
