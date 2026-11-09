"""Chunking. B8 (separator split), B9 (Estonian sentence guard), B10
(termination + validation).

Pure: no I/O, no network, no destination. On the llm_module sink this is the
only chunker in the retrieval path — the far side's is being removed — so
these boundaries are the passages the assistant answers from, and there is no
second implementation to compare them against.

The input is the output of normalise_nfc(), the same string content_sha256 is
computed over. Nothing here normalises again: hashing one string and chunking
another is how the two would drift apart. The chunker still terminates and
keeps every invariant on un-normalised input; it only chunks it less tidily.

Shape: a window slides over the text. Each window ends at the best separator
it contains, trying separators in priority order — paragraph, list item,
line, sentence, clause, word — and the next window starts `overlap`
characters before that end. No LangChain: the split-then-merge it does cannot
express the B10 loop, and every boundary rule here would be fighting it.

Priority ranks separators within a range, not across target: the window is
searched between min_size and target first, and only if that range has no
break at all is the stretch past target searched. So a word break just
before target beats a paragraph break just after it — chunks stay near
target, and structure decides where in that range they end.

Every chunk is text[span.start:span.end] exactly, spans start at 0, end at
len(text), and each one starts no later than the previous one ends and ends
strictly after it — no chunk is ever a copy of part of its predecessor. So
dropping each chunk's overlap with its predecessor and concatenating
reproduces the text byte for byte — the property B14 checks over a corpus.

Changing a separator, the guard rules or the abbreviation set moves
boundaries for documents already published: bump CHUNKER_VERSION in
constants.py, so the fingerprint re-chunks the corpus instead of leaving it
on mixed geometry.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import NamedTuple

from exporter.core.constants import ChunkProfile
from exporter.core.ids import make_chunk_id
from exporter.core.schemas import Chunk, TextSpan


class _Tier(NamedTuple):
    """One separator. A chunk ending at this separator ends at
    match.start() + end_offset: before the separator's whitespace, after any
    punctuation it keeps."""

    pattern: re.Pattern[str]
    end_offset: int
    guarded: bool  # subject to the B9 Estonian guard


# Priority order. The three newline tiers are structure the author wrote and
# are never guarded; the guard applies where a "." can be an ordinal or an
# abbreviation rather than a sentence end — the ". " tier, and the space tier
# that would otherwise cut the same place one character later.
_TIERS = (
    _Tier(re.compile(r"\n\n"), 0, False),
    # A line break that starts a list item: "- ", "* ", "+ ", "• ", "1. ", "1) "
    _Tier(re.compile(r"\n(?=[ \t]*(?:[-*+•]|\d{1,3}[.)])[ \t])"), 0, False),
    _Tier(re.compile(r"\n"), 0, False),
    _Tier(re.compile(r"[!?] "), 1, False),
    _Tier(re.compile(r"\. "), 1, True),
    _Tier(re.compile(r"[;,] "), 1, False),
    _Tier(re.compile(r" "), 0, True),
)

# finditer() stops at endpos, which would truncate the list-item lookahead
# for a break right at the window's edge. Indentation deeper than this is not
# a list item anyone wrote on purpose.
_LOOKAHEAD = 64

# --- B9: the Estonian sentence guard ------------------------------------
#
# Estonian ordinals carry a trailing period — "3. detsember", "2024. aasta",
# "§ 5." — so an unguarded sentence splitter cuts dates and legal references
# in half, and a date broken across a boundary is a date neither half can
# retrieve. The same goes for abbreviations and initials. The guard only ever
# refuses a break; a false positive costs a chunk ending at a lower-priority
# separator, never lost text.

# Reviewed list; entries with internal dots ("v.a") match the whole token.
# Single letters are handled by a rule, not listed here.
# fmt: off
_ESTONIAN_ABBREVIATIONS = frozenset({
    "jne", "jm", "jt", "jms", "jpt", "nt", "vt", "vrd", "lk", "nr", "lg",
    "ptk", "aa", "st", "sh", "mh", "nn", "nö", "ca", "hr", "pr", "dr",
    "prof", "mag", "tel", "tn", "mnt", "pst", "km", "kr", "ingl", "lad",
    "vm", "vms", "ekr", "pkr",
    "v.a", "s.t", "k.a", "e.m.a", "m.a.j", "e.k", "p.o", "e.kr", "p.kr",
})
# fmt: on

# "5", "2024", and dotted section numbers like "5.2"
_NUMERIC = re.compile(r"\d+(?:\.\d+)*")
# "II. osa". Case-sensitive: a lowercase "iv." is far more likely a word.
# Well-formed numerals only, so "DVD." or "LCD." still ends a sentence;
# a word that is also a valid numeral ("CD.", "MIX.") stays guarded.
_ROMAN_NUMERAL = re.compile(
    r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
)
# Stripped from the front of a token so "(nt." still reads as "nt."
_OPENING_PUNCTUATION = "([{„“«'\""
# Markdown emphasis, removed wherever it sits in a token: the corpus is
# cleaned Markdown, and a law's section headings arrive as
# "**§ 1. ****Pealkiri**", so "**§", "**2024.**" and "**2024**." must read
# as "§" and "2024.".
_EMPHASIS = str.maketrans("", "", "*_")
# A token longer than this is not an ordinal or an abbreviation; stop looking
# rather than misread the tail of a URL as one.
_MAX_TOKEN = 40


def _is_protected_break(text: str, end: int) -> bool:
    """Whether a boundary at `end` would cut an ordinal, an abbreviation, an
    initial or a § reference away from what follows it.

    Looks at the token immediately before `end` — the run of non-whitespace
    characters ending there.
    """
    start = end
    while start > 0 and not text[start - 1].isspace():
        start -= 1
        if end - start > _MAX_TOKEN:
            return False
    token = text[start:end].translate(_EMPHASIS).lstrip(_OPENING_PUNCTUATION)
    if token in ("§", "§§"):
        return True
    if not token.endswith("."):
        return False
    body = token[:-1]
    if not body:
        return False
    return (
        _NUMERIC.fullmatch(body) is not None
        or _ROMAN_NUMERAL.fullmatch(body) is not None
        or (len(body) == 1 and body.isalpha())
        or body.lower() in _ESTONIAN_ABBREVIATIONS
    )


# --- B8 + B10: the chunker -----------------------------------------------


@dataclass(frozen=True, kw_only=True)
class Chunker:
    """Chunk geometry, validated once. All four sizes are in characters.

    Build it from a ChunkProfile via from_profile(); the four sizes are never
    four independent settings (B11).
    """

    target: int
    overlap: int
    min_size: int
    max_size: int

    def __post_init__(self) -> None:
        for name in ("target", "overlap", "min_size", "max_size"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an int, got {value!r}")
        if self.overlap < 0:
            raise ValueError(f"overlap must be >= 0, got {self.overlap}")
        if self.overlap >= self.target:
            raise ValueError(
                f"overlap ({self.overlap}) must be < target ({self.target})"
            )
        if self.target > self.max_size:
            raise ValueError(
                f"target ({self.target}) must be <= max_size ({self.max_size})"
            )
        if self.min_size < 1:
            raise ValueError(f"min_size must be >= 1, got {self.min_size}")
        if self.min_size > self.target:
            raise ValueError(
                f"min_size ({self.min_size}) must be <= target ({self.target})"
            )
        # The tail guard in _find_end can only keep the final chunk at
        # min_size or longer if a window can hold two minimum-size chunks.
        if self.max_size < 2 * self.min_size:
            raise ValueError(
                f"max_size ({self.max_size}) must be >= 2 * min_size ({self.min_size})"
            )

    @classmethod
    def from_profile(cls, profile: ChunkProfile) -> "Chunker":
        return cls(
            target=profile.target,
            overlap=profile.overlap,
            min_size=profile.min,
            max_size=profile.max,
        )

    def split(self, text: str) -> list[TextSpan]:
        """Chunk boundaries for `text`, as half-open spans into it."""
        n = len(text)
        spans: list[TextSpan] = []
        start = 0
        while n > 0:
            if n - start <= self.max_size:
                spans.append(TextSpan(start, n))
                break
            end = self._find_end(text, start)
            spans.append(TextSpan(start, end))
            # B10: the +1 floor makes non-termination impossible whatever
            # _overlap_start returns — start strictly increases every pass.
            start = max(self._overlap_start(text, start, end), start + 1)
        return spans

    def _find_end(self, text: str, start: int) -> int:
        lo = start + self.min_size
        # Tail guard: never leave a remainder too short to be a chunk of its
        # own, so a document does not end on a sliver that is mostly overlap.
        # split() only calls this with more than max_size left, and
        # max_size >= 2 * min_size, so hi > lo always.
        hi = min(start + self.max_size, len(text) - self.min_size)
        soft = min(start + self.target, hi)

        cache: dict[int, list[int]] = {}

        def ends(tier_index: int) -> list[int]:
            if tier_index not in cache:
                cache[tier_index] = _candidate_ends(text, _TIERS[tier_index], lo, hi)
            return cache[tier_index]

        # Pass 1: the highest-priority separator with a break between min and
        # target, taking the last one so the chunk is as close to target as
        # that separator allows.
        for i in range(len(_TIERS)):
            before = [end for end in ends(i) if end <= soft]
            if before:
                return before[-1]
        # Pass 2: nothing usable before target, so the first break after it.
        for i in range(len(_TIERS)):
            after = [end for end in ends(i) if end > soft]
            if after:
                return after[0]
        # Pass 3: no usable whitespace anywhere in the window — a long URL or
        # base64-encoded data. Cut at target, but never between a base
        # character and a combining mark NFC could not compose onto it.
        return _off_combining_mark(text, soft, lo)

    def _overlap_start(self, text: str, start: int, end: int) -> int:
        """Where the next chunk starts: `overlap` characters before `end`,
        moved to a word start so no chunk opens on half a word or on the
        second half of a guarded pair ("3. |detsember").

        Never min_size or more before `end`. The next window's earliest end
        is its start + min_size, so a start that far back would let it end
        at or before `end` — a chunk wholly inside this one, published and
        embedded twice. azure_native has overlap == min_size, so this caps
        its effective overlap at min_size - 1."""
        if self.overlap == 0:
            return end
        floor = max(end - self.min_size + 1, start + 1)
        lo = max(end - self.overlap, floor)
        first_protected: int | None = None
        for p in range(lo, end):
            if _is_word_start(text, p):
                if not _is_protected_break(text, p - 1):
                    return p
                if first_protected is None:
                    first_protected = p
        # The overlap region is inside one long word, or every word start in
        # it follows an ordinal or an abbreviation. Reach back for a clean
        # word start instead — at most one more overlap's worth, and never
        # past the floor, so the next chunk can never become a near-copy of
        # this one.
        for p in range(lo - 1, max(start, lo - self.overlap - 1, floor - 1), -1):
            if _is_word_start(text, p) and not _is_protected_break(text, p - 1):
                return p
        # A run of guarded tokens, or one token, longer than that: open on a
        # whole word if there is one, else mid-token.
        if first_protected is not None:
            return first_protected
        return _off_combining_mark(text, lo, floor)


def _off_combining_mark(text: str, p: int, floor: int) -> int:
    """`p`, moved back to the base character if it sits on a combining mark,
    so a cut at p does not orphan the mark. Never below `floor`."""
    while p > floor and unicodedata.combining(text[p]):
        p -= 1
    return p


def _is_word_start(text: str, p: int) -> bool:
    return text[p - 1].isspace() and not text[p].isspace()


def _candidate_ends(text: str, tier: _Tier, lo: int, hi: int) -> list[int]:
    """Every valid chunk end this tier offers in [lo, hi], ascending."""
    found: list[int] = []
    pos = max(0, lo - tier.end_offset)
    for match in tier.pattern.finditer(text, pos, min(len(text), hi + _LOOKAHEAD)):
        end = match.start() + tier.end_offset
        if end > hi:
            break
        if end < lo or text[end - 1].isspace():
            continue
        if tier.guarded and _is_protected_break(text, end):
            continue
        found.append(end)
    return found


def chunk_document(
    text: str, *, agency_id: str, document_id: str, chunker: Chunker
) -> list[Chunk]:
    """Chunk one document's normalised text into id'd chunks.

    Ids are coordinates only (B4): the ordinal, never the chunk's text.
    """
    return [
        Chunk(
            chunk_id=make_chunk_id(agency_id, document_id, ordinal),
            document_id=document_id,
            ordinal=ordinal,
            text=text[span.start : span.end],
            span=span,
        )
        for ordinal, span in enumerate(chunker.split(text))
    ]
