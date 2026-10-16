"""Tests for content-external's text normalisation (B3).

The property that matters is that text which differs only invisibly —
encoding, line endings, invisible characters, whitespace — normalises to the
same string and so the same content_sha256. The second property is that the
Markdown structure the chunker splits on survives: paragraph breaks, line
starts and leading indentation.
"""

import random
import unicodedata

import pytest

from exporter.core.text_normaliser import (
    canonicalise_whitespace,
    normalise_line_endings,
    normalise_nfc,
    sha256_hex,
    sha256_text,
    strip_bom,
    strip_zero_width,
)

ESTONIAN = "Taotluse esitamine: õ ä ö ü š ž"

# One canonical document and the variants of it that must hash identically.
CANONICAL = "# Pealkiri\n\nEsimene lõik 3. detsember.\n\n- punkt üks\n  - alampunkt"
EQUIVALENT_VARIANTS = {
    "decomposed": unicodedata.normalize("NFD", CANONICAL),
    "bom": "\ufeff" + CANONICAL,
    "crlf": CANONICAL.replace("\n", "\r\n"),
    "lone_cr": CANONICAL.replace("\n", "\r"),
    "zero_width": CANONICAL.replace("lõik", "lõ\u200bik"),
    "soft_hyphen": CANONICAL.replace("Esimene", "Esi\u00admene"),
    "nbsp_after_ordinal": CANONICAL.replace("3. detsember", "3.\u00a0detsember"),
    "trailing_spaces": CANONICAL.replace("\n", "  \n"),
    "extra_blank_lines": CANONICAL.replace("\n\n", "\n\n\n\n"),
    "surrounding_blank_lines": "\n \n" + CANONICAL + " \n\t\n",
    "doubled_interior_space": CANONICAL.replace("lõik 3.", "lõik   3."),
}


def test_composed_and_decomposed_hash_identically() -> None:
    composed = unicodedata.normalize("NFC", ESTONIAN)
    decomposed = unicodedata.normalize("NFD", ESTONIAN)
    assert composed != decomposed
    assert sha256_text(normalise_nfc(composed)) == sha256_text(
        normalise_nfc(decomposed)
    )


@pytest.mark.parametrize("variant", EQUIVALENT_VARIANTS)
def test_invisible_differences_normalise_identically(variant: str) -> None:
    assert normalise_nfc(EQUIVALENT_VARIANTS[variant]) == CANONICAL


@pytest.mark.parametrize("variant", EQUIVALENT_VARIANTS)
def test_normalise_is_idempotent(variant: str) -> None:
    once = normalise_nfc(EQUIVALENT_VARIANTS[variant])
    assert normalise_nfc(once) == once


def test_visible_change_still_changes_the_hash() -> None:
    edited = CANONICAL.replace("3. detsember", "4. detsember")
    assert sha256_text(normalise_nfc(edited)) != sha256_text(normalise_nfc(CANONICAL))


def test_raw_hash_sees_encoding_churn_content_hash_does_not() -> None:
    crlf = EQUIVALENT_VARIANTS["crlf"]
    assert sha256_hex(crlf.encode()) != sha256_hex(CANONICAL.encode())
    assert sha256_text(normalise_nfc(crlf)) == sha256_text(CANONICAL)


def test_bom_stripped_only_when_leading() -> None:
    assert strip_bom("\ufeffabc") == "abc"
    assert strip_bom("a\ufeffbc") == "a\ufeffbc"
    assert normalise_nfc("a\ufeffbc") == "abc"


@pytest.mark.parametrize("terminator", ["\x0b", "\x0c", "\x85", "\u2028", "\u2029"])
def test_unicode_line_terminators_become_lf(terminator: str) -> None:
    assert normalise_line_endings(f"üks{terminator}kaks") == "üks\nkaks"


def test_crlf_does_not_double() -> None:
    assert normalise_line_endings("a\r\nb\rc") == "a\nb\nc"


@pytest.mark.parametrize(
    "char", ["\u00ad", "\u200b", "\u200c", "\u200d", "\u200e", "\u200f", "\u2060"]
)
def test_invisible_characters_removed(char: str) -> None:
    assert strip_zero_width(f"taot{char}lus") == "taotlus"


def test_space_left_by_removed_zero_width_collapses() -> None:
    assert normalise_nfc("üks \u200b kaks") == "üks kaks"


@pytest.mark.parametrize(
    "space", ["\u00a0", "\u202f", "\u2002", "\u2009", "\u3000", "\u1680"]
)
def test_space_variants_become_ascii_space(space: str) -> None:
    assert canonicalise_whitespace(f"§{space}5") == "§ 5"


def test_paragraph_break_preserved() -> None:
    assert canonicalise_whitespace("üks\n\nkaks") == "üks\n\nkaks"


def test_single_line_break_preserved() -> None:
    assert canonicalise_whitespace("üks\nkaks") == "üks\nkaks"


def test_whitespace_only_lines_count_as_blank() -> None:
    assert canonicalise_whitespace("üks\n   \n \t \nkaks") == "üks\n\nkaks"


def test_leading_indentation_preserved() -> None:
    nested = "- üks\n    - kaks\n\tkolm\n        kood  =  1"
    assert (
        canonicalise_whitespace(nested) == "- üks\n    - kaks\n\tkolm\n        kood = 1"
    )


def test_first_line_indentation_preserved() -> None:
    assert canonicalise_whitespace("    kood = 1\nüks") == "    kood = 1\nüks"
    assert normalise_nfc("\n \n    kood = 1\n\n") == "    kood = 1"


def test_interior_tab_preserved() -> None:
    assert canonicalise_whitespace("üks\tkaks") == "üks\tkaks"


def test_empty_and_whitespace_only_documents() -> None:
    assert normalise_nfc("") == ""
    assert normalise_nfc("\ufeff \r\n\u200b\u00a0\n") == ""


# Every character class some rule above acts on, plus letters for them to
# act between. A random string over this hits rule interactions no
# hand-written variant does.
_FUZZ_ALPHABET = (
    "a\u00f5\u0161\u00a7.-",
    "o\u0303",  # decomposed \u00f5
    "\u0303",  # stray combining mark
    " \t\n\r",
    "\ufeff\u200b\u00ad",
    "\u00a0\u2009\u3000",
    "\x0c\u2028\x85",
    "\x1c",
)


def test_normalise_is_idempotent_over_random_text() -> None:
    rng = random.Random(1)
    pieces = [p for group in _FUZZ_ALPHABET for p in (group, *group)]
    for _ in range(5_000):
        text = "".join(rng.choices(pieces, k=rng.randint(0, 40)))
        once = normalise_nfc(text)
        assert normalise_nfc(once) == once, repr(text)
        assert "\n\n\n" not in once, repr(text)
        assert not once.startswith("\n") and not once.endswith("\n"), repr(text)
        assert all(line == line.rstrip(" \t") for line in once.split("\n"))
