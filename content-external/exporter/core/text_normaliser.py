"""Text normalisation and hashing helpers. B3.

No I/O, no destination knowledge — pure functions only, so this is
unit-testable with no Docker, no S3, no database.

NFC is load-bearing, not cosmetic. Estonian characters (o~ a~ o" u" s^ z^,
i.e. o a o u s z with diacritics) can arrive composed (one code point per
character) or decomposed (base letter + a combining mark) depending on the
source. Without normalising to one consistent form, the same sentence
produces two different hashes, the diff reports a spurious change, and the
document is re-uploaded on every single run.

BOM, CRLF and zero-width characters are the other three sources of the same
class of bug: bytes that are invisible or encoding-only, that would
otherwise make a document look "changed" between runs when nothing the
reader would call content actually changed.
"""

import hashlib
import unicodedata

# UTF-8 BOM, once decoded to text, is U+FEFF (ZERO WIDTH NO-BREAK SPACE used
# as a byte-order mark). Stripped only when it is the FIRST character —
# U+FEFF elsewhere in a document is a zero-width character, not a marker,
# and is handled by strip_zero_width below.
_BOM = "\ufeff"

# Characters that render as nothing but are still distinct code points, so
# they would otherwise cause the same text to hash differently depending on
# whether an invisible character slipped in during copy-paste or export.
#   U+200B  ZERO WIDTH SPACE
#   U+200C  ZERO WIDTH NON-JOINER
#   U+200D  ZERO WIDTH JOINER
#   U+FEFF  ZERO WIDTH NO-BREAK SPACE (when not a leading BOM)
_ZERO_WIDTH_CHARS = "\u200b\u200c\u200d\ufeff"
_ZERO_WIDTH_TRANSLATION = {ord(char): None for char in _ZERO_WIDTH_CHARS}


def strip_bom(text: str) -> str:
    """Remove a leading UTF-8 BOM (U+FEFF), if present."""
    return text[1:] if text.startswith(_BOM) else text


def normalise_line_endings(text: str) -> str:
    """CRLF and lone CR both become LF.

    Order matters: replacing "\\r\\n" first prevents it from becoming two
    newlines when the lone-CR replacement runs afterwards.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def strip_zero_width(text: str) -> str:
    """Remove zero-width characters anywhere in the text."""
    return text.translate(_ZERO_WIDTH_TRANSLATION)


def normalise_nfc(text: str) -> str:
    """The composed normalisation step: BOM, line endings, zero-width
    characters, then Unicode NFC.

    NFC last: it operates on combining-mark sequences, and doing it after
    the character-level strips avoids NFC being asked to compose across a
    character this function is about to delete anyway.
    """
    text = strip_bom(text)
    text = normalise_line_endings(text)
    text = strip_zero_width(text)
    return unicodedata.normalize("NFC", text)


def sha256_hex(data: bytes) -> str:
    """Hex digest of raw bytes. Used for raw_sha256 — the content object's
    bytes exactly as stored, before any normalisation."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Hex digest of text, encoded as UTF-8. Used for content_sha256, over
    text that has already been through normalise_nfc() and stripped."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
