"""Text normalisation and hashing helpers. B3.

No I/O, no destination knowledge — pure functions only, so this is
unit-testable with no Docker, no S3, no database.

NFC is load-bearing, not cosmetic. Estonian characters (o~ a~ o" u" s^ z^,
i.e. o a o u s z with diacritics) can arrive composed (one code point per
character) or decomposed (base letter + a combining mark) depending on the
source. Without normalising to one consistent form, the same sentence
produces two different hashes, the diff reports a spurious change, and the
document is re-uploaded on every single run. With this service as the only
chunker in the retrieval path, it also decides whether the same word in a
composed and a decomposed document embeds to the same place.

BOM, line endings, invisible characters and whitespace are the other
sources of the same class of bug: bytes that are invisible or encoding-only,
that would otherwise make a document look "changed" between runs when
nothing the reader would call content actually changed.

The input is cleaned Markdown, so whitespace canonicalisation is deliberately
conservative about line structure: it never joins lines, never removes
leading indentation (nested lists and indented code depend on it) and never
removes a paragraph break — the chunker's highest-priority separator is
"\\n\\n", and list markers are recognised at the start of a line.

It is not conservative inside a line. Interior runs of spaces collapse
everywhere, including in fenced code and column-aligned tables, so alignment
is lost while every token survives; and Unicode space variants become U+0020
in indentation too. This text is retrieval input, where column alignment
carries nothing an embedder or the assistant could use.

Every rule here feeds content_sha256 and chunk boundaries. Changing one after
the first published run means bumping NORMALISER_VERSION in constants.py, so
the fingerprint re-chunks the corpus rather than leaving it half on old rules.
"""

import hashlib
import re
import unicodedata

# UTF-8 BOM, once decoded to text, is U+FEFF (ZERO WIDTH NO-BREAK SPACE used
# as a byte-order mark). Stripped only when it is the FIRST character —
# U+FEFF elsewhere in a document is a zero-width character, not a marker,
# and is handled by strip_zero_width below.
_BOM = "\ufeff"

# Line terminators other than CR/LF that the chunker's "\n" separators would
# not see, so a document using them would chunk as one long line. Mostly PDF
# extraction (form feed at page breaks) and text pasted from word processors
# (U+2028). str.splitlines() also breaks on U+001C..U+001E (file, group and
# record separators); those are data delimiters, not line breaks anyone
# writes in prose, so they are left alone.
#   U+000B  VERTICAL TAB
#   U+000C  FORM FEED
#   U+0085  NEXT LINE
#   U+2028  LINE SEPARATOR
#   U+2029  PARAGRAPH SEPARATOR
_EXTRA_LINE_BREAKS = "\x0b\x0c\x85\u2028\u2029"
_LINE_BREAK_TRANSLATION = {ord(char): "\n" for char in _EXTRA_LINE_BREAKS}

# Characters that render as nothing but are still distinct code points, so
# they would otherwise cause the same text to hash differently depending on
# whether an invisible character slipped in during copy-paste or export —
# and, inside a word, split it into two tokens for the embedder.
#   U+00AD  SOFT HYPHEN (CMS hyphenation hints in long Estonian compounds)
#   U+200B  ZERO WIDTH SPACE
#   U+200C  ZERO WIDTH NON-JOINER
#   U+200D  ZERO WIDTH JOINER
#   U+200E  LEFT-TO-RIGHT MARK
#   U+200F  RIGHT-TO-LEFT MARK
#   U+2060  WORD JOINER
#   U+FEFF  ZERO WIDTH NO-BREAK SPACE (when not a leading BOM)
_ZERO_WIDTH_CHARS = "\u00ad\u200b\u200c\u200d\u200e\u200f\u2060\ufeff"
_ZERO_WIDTH_TRANSLATION = {ord(char): None for char in _ZERO_WIDTH_CHARS}

# Horizontal spaces that look like U+0020 but are not, so NFC leaves them
# alone. A no-break space is common in Estonian web text exactly where the
# chunker's guard looks — "3.\u00a0detsember", "§\u00a05" — and if it survived,
# the guard on ". " would never see those as the ordinal it protects.
#   U+00A0         NO-BREAK SPACE
#   U+1680         OGHAM SPACE MARK
#   U+2000-U+200A  EN QUAD .. HAIR SPACE
#   U+202F         NARROW NO-BREAK SPACE
#   U+205F         MEDIUM MATHEMATICAL SPACE
#   U+3000         IDEOGRAPHIC SPACE
# Tab is deliberately not here: it is indentation in Markdown.
_SPACE_VARIANTS = (
    "\u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u202f\u205f\u3000"
)
_SPACE_TRANSLATION = {ord(char): " " for char in _SPACE_VARIANTS}

# A run of two or more spaces that follows a non-space character, i.e. inside
# a line rather than at its start. Leading indentation is left untouched.
_INTERIOR_SPACE_RUN = re.compile(r"(?<=\S) {2,}")

# Spaces and tabs immediately before a line break or the end of the text.
_TRAILING_WHITESPACE = re.compile(r"[ \t]+$", re.MULTILINE)

# Three or more consecutive line breaks — two or more blank lines. Markdown
# renders these identically to one blank line.
_BLANK_LINE_RUN = re.compile(r"\n{3,}")


def strip_bom(text: str) -> str:
    """Remove a leading UTF-8 BOM (U+FEFF), if present."""
    return text[1:] if text.startswith(_BOM) else text


def normalise_line_endings(text: str) -> str:
    """CRLF, lone CR and the other Unicode line terminators all become LF.

    Order matters: replacing "\\r\\n" first prevents it from becoming two
    newlines when the lone-CR replacement runs afterwards.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text.translate(_LINE_BREAK_TRANSLATION)


def strip_zero_width(text: str) -> str:
    """Remove zero-width and other invisible characters anywhere in the text."""
    return text.translate(_ZERO_WIDTH_TRANSLATION)


def canonicalise_whitespace(text: str) -> str:
    """Make whitespace that differs only invisibly compare equal.

    In order: Unicode space variants become U+0020; interior runs of spaces
    collapse to one; trailing spaces and tabs on each line are removed (which
    also empties whitespace-only lines); runs of blank lines collapse to one;
    blank lines at the start and end of the whole text are removed. The first
    line's indentation is kept like any other line's: four leading spaces
    make it a code block, and stripping them would make it a paragraph.

    Expects LF line endings, so run normalise_line_endings() first.

    The one Markdown construct this changes is a hard line break written as
    two trailing spaces, which becomes a soft break. That is invisible to
    retrieval and is the same trade every Markdown formatter makes.
    """
    text = text.translate(_SPACE_TRANSLATION)
    text = _INTERIOR_SPACE_RUN.sub(" ", text)
    text = _TRAILING_WHITESPACE.sub("", text)
    text = _BLANK_LINE_RUN.sub("\n\n", text)
    # Only newlines: _TRAILING_WHITESPACE has already emptied every
    # whitespace-only line, so this cannot leave stray spaces at either end.
    return text.strip("\n")


def normalise_nfc(text: str) -> str:
    """The composed normalisation step: BOM, line endings, invisible
    characters, Unicode NFC, then whitespace.

    This is the text content_sha256 is computed over and the text the
    chunker splits — one function, so the two cannot drift apart.

    NFC after the character-level strips: it operates on combining-mark
    sequences, and doing it afterwards avoids NFC being asked to compose
    across a character this function is about to delete anyway. Whitespace
    last, so a space left either side of a removed zero-width character
    still collapses to one.

    Idempotent: normalise_nfc(normalise_nfc(t)) == normalise_nfc(t).
    """
    text = strip_bom(text)
    text = normalise_line_endings(text)
    text = strip_zero_width(text)
    text = unicodedata.normalize("NFC", text)
    return canonicalise_whitespace(text)


def sha256_hex(data: bytes) -> str:
    """Hex digest of raw bytes. Used for raw_sha256 — the content object's
    bytes exactly as stored, before any normalisation."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Hex digest of text, encoded as UTF-8. Used for content_sha256, over
    the output of normalise_nfc()."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
