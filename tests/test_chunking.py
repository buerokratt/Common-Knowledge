"""Tests for content-external's chunker (B8, B9, B10).

Most tests use a deliberately small geometry so a boundary can be placed
exactly; the invariant sweep also runs both real profiles. The fixture-corpus
assertions over real cleaned Estonian documents are B14, not here.
"""

import random

import pytest

from exporter.core.chunking import Chunker, _is_protected_break, chunk_document
from exporter.core.constants import CHUNK_PROFILES
from exporter.core.ids import make_chunk_id
from exporter.core.schemas import TextSpan
from exporter.core.text_normaliser import normalise_nfc

SMALL = Chunker(target=60, overlap=10, min_size=20, max_size=100)

# No punctuation, so a filler built from these offers only space breaks.
WORDS = (
    "taotlus",
    "esitatakse",
    "riigiportaalis",
    "kodanik",
    "saab",
    "teenuse",
    "kohta",
    "infot",
    "ning",
    "vajadusel",
    "pöördub",
    "ametisse",
    "õigus",
    "töötasu",
)

# Pairs whose internal spaces must never become a boundary.
GUARDED_PHRASES = (
    "3. detsember",
    "2024. aasta",
    "§ 5. Kohaldamisala",
    "§ 5",
    "jne. Järgmine",
    "nt. taotlus",
    "A. Tammsaare",
    "II. osa",
    "v.a. puhkepäevad",
    "lg. 2",
    "(vt. lisa",
)


def words(rng: random.Random, length: int) -> str:
    """Space-separated filler of at least `length` characters."""
    out: list[str] = []
    while len(" ".join(out)) < length:
        out.append(rng.choice(WORDS))
    return " ".join(out)


def assert_invariants(
    text: str, spans: list[TextSpan], chunker: Chunker, *, tidy: bool = True
) -> None:
    """Every property split() promises. `tidy` adds the no-leading-or-
    trailing-whitespace check, which only holds when the text has usable
    breaks — not when every window had to be hard-cut."""
    if not text:
        assert spans == []
        return
    assert spans[0].start == 0
    assert spans[-1].end == len(text)
    for prev, cur in zip(spans, spans[1:], strict=False):
        assert prev.start < cur.start <= prev.end, (prev, cur)
    for i, span in enumerate(spans):
        length = span.end - span.start
        assert length <= chunker.max_size, span
        if i < len(spans) - 1:
            assert length >= chunker.min_size, span
        if tidy:
            chunk = text[span.start : span.end]
            assert chunk == chunk.strip(), repr(chunk)
    rebuilt = text[spans[0].start : spans[0].end]
    for prev, cur in zip(spans, spans[1:], strict=False):
        rebuilt += text[cur.start : cur.end][prev.end - cur.start :]
    assert rebuilt == text


def assert_phrase_intact(text: str, spans: list[TextSpan], phrase: str) -> None:
    i = text.index(phrase)
    j = i + len(phrase)
    for span in spans:
        assert not i < span.end < j, f"chunk ends inside {phrase!r}"
        assert not i < span.start < j, f"chunk starts inside {phrase!r}"


# --- B10: validation -------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"target": 60, "overlap": 60, "min_size": 20, "max_size": 100}, "overlap"),
        ({"target": 60, "overlap": 70, "min_size": 20, "max_size": 100}, "overlap"),
        ({"target": 60, "overlap": -1, "min_size": 20, "max_size": 100}, "overlap"),
        ({"target": 120, "overlap": 10, "min_size": 20, "max_size": 100}, "max_size"),
        ({"target": 60, "overlap": 10, "min_size": 61, "max_size": 100}, "min_size"),
        ({"target": 60, "overlap": 10, "min_size": 0, "max_size": 100}, "min_size"),
        (
            {"target": 60, "overlap": 10, "min_size": 51, "max_size": 100},
            r"2 \* min_size",
        ),
        ({"target": True, "overlap": 10, "min_size": 20, "max_size": 100}, "target"),
        ({"target": 60, "overlap": 10.0, "min_size": 20, "max_size": 100}, "overlap"),
    ],
)
def test_invalid_geometry_raises(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Chunker(**kwargs)


@pytest.mark.parametrize("name", sorted(CHUNK_PROFILES))
def test_every_profile_validates(name: str) -> None:
    profile = CHUNK_PROFILES[name]
    chunker = Chunker.from_profile(profile)
    assert (chunker.target, chunker.overlap) == (profile.target, profile.overlap)
    assert (chunker.min_size, chunker.max_size) == (profile.min, profile.max)


def test_zero_overlap_is_valid() -> None:
    chunker = Chunker(target=60, overlap=0, min_size=20, max_size=100)
    text = words(random.Random(1), 400)
    spans = chunker.split(text)
    assert_invariants(text, spans, chunker, tidy=False)
    for prev, cur in zip(spans, spans[1:], strict=False):
        assert cur.start == prev.end


# --- trivial input ---------------------------------------------------------


def test_empty_text_has_no_chunks() -> None:
    assert SMALL.split("") == []


def test_text_within_max_is_one_chunk() -> None:
    text = words(random.Random(2), 95)[:100].strip()
    assert SMALL.split(text) == [TextSpan(0, len(text))]


@pytest.mark.parametrize(("length", "count"), [(100, 1), (101, 2)])
def test_max_size_is_the_single_chunk_limit(length: int, count: int) -> None:
    assert len(SMALL.split("x" * length)) == count


# --- B10: termination ------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "tidy"),
    [
        ("x" * 50_000, True),
        (" ".join(f"{i}." for i in range(1, 3_000)), False),
        ("\n" * 5_000, False),
        (". " * 3_000, False),
        ("a b" * 5_000, True),
    ],
    ids=["no-whitespace", "all-guarded", "all-newlines", "all-periods", "tiny-words"],
)
def test_pathological_input_terminates(text: str, tidy: bool) -> None:
    spans = SMALL.split(text)
    assert_invariants(text, spans, SMALL, tidy=tidy)


def test_hard_cut_without_whitespace_advances_by_target_minus_overlap() -> None:
    text = "x" * 1_000
    spans = SMALL.split(text)
    assert spans[0] == TextSpan(0, SMALL.target)
    assert spans[1].start == spans[0].end - SMALL.overlap


# --- B8: separator priority ------------------------------------------------


def test_paragraph_break_wins_over_sentence_break() -> None:
    rng = random.Random(3)
    text = f"{words(rng, 25)}\n\n{words(rng, 15)}. {words(rng, 200)}"
    assert SMALL.split(text)[0].end == text.index("\n\n")


def test_list_item_wins_over_plain_line_break() -> None:
    rng = random.Random(4)
    text = f"{words(rng, 25)}\n- {words(rng, 12)}\n{words(rng, 200)}"
    assert SMALL.split(text)[0].end == text.index("\n- ")


def test_sentence_break_wins_over_comma() -> None:
    rng = random.Random(5)
    text = f"{words(rng, 25)}. {words(rng, 15)}, {words(rng, 200)}"
    assert SMALL.split(text)[0].end == text.index(". ") + 1


def test_without_punctuation_breaks_at_a_space() -> None:
    text = words(random.Random(6), 300)
    end = SMALL.split(text)[0].end
    assert text[end] == " "
    assert SMALL.min_size <= end <= SMALL.target


def test_breaks_past_target_when_nothing_earlier() -> None:
    text = "x" * 70 + " " + words(random.Random(7), 200)
    assert SMALL.split(text)[0].end == 70


def test_tail_is_never_a_sliver() -> None:
    rng = random.Random(8)
    for length in range(101, 400):
        text = words(rng, length)
        spans = SMALL.split(text)
        assert spans[-1].end - spans[-1].start >= SMALL.min_size, length


def test_tail_is_never_a_sliver_at_the_tightest_valid_geometry() -> None:
    # max_size == 2 * min_size is the edge the validation allows; below it
    # the tail guard has no room and the final chunk could come out short.
    rng = random.Random(17)
    for _ in range(300):
        min_size = rng.randint(5, 40)
        target = rng.randint(min_size, 2 * min_size)
        chunker = Chunker(
            target=target,
            overlap=rng.randint(0, target - 1),
            min_size=min_size,
            max_size=2 * min_size,
        )
        text = random_document(rng, rng.randint(1, 4))
        assert_invariants(text, chunker.split(text), chunker, tidy=False)


# --- B9: the Estonian guard ------------------------------------------------


@pytest.mark.parametrize("phrase", GUARDED_PHRASES)
def test_guarded_phrase_is_never_split(phrase: str) -> None:
    # Slide the phrase across every position relative to the first window,
    # so at some shift it sits exactly where the ideal break would fall.
    rng = random.Random(9)
    for shift in range(0, 90):
        # lstrip: at shift 0 the head is empty, and a leading space would be
        # kept as first-line indentation.
        head = f"{words(rng, shift)} {phrase}".lstrip()
        text = normalise_nfc(f"{head} {words(rng, 250)}")
        spans = SMALL.split(text)
        assert_invariants(text, spans, SMALL)
        assert_phrase_intact(text, spans, phrase)


def test_real_sentence_end_still_breaks() -> None:
    rng = random.Random(10)
    head = words(rng, 40)
    text = f"{head} esitati. Järgmine {words(rng, 200)}"
    assert SMALL.split(text)[0].end == text.index("esitati.") + len("esitati.")


@pytest.mark.parametrize(
    ("token", "protected"),
    [
        ("3.", True),
        ("2024.", True),
        ("5.2.", True),
        ("§", True),
        ("§§", True),
        ("II.", True),
        ("XIV.", True),
        ("MCMXCIV.", True),
        ("CD.", True),  # a valid numeral too; the guard cannot tell them apart
        ("A.", True),
        ("a.", True),  # "2024. a." — aasta
        ("(nt.", True),
        ("jne.", True),
        ("v.a.", True),
        ("vm.", True),
        ("vms.", True),
        ("eKr.", True),
        ("pKr.", True),
        ("e.Kr.", True),
        ("DVD.", False),
        ("LCD.", False),
        ("IIII.", False),
        ("vii.", False),
        ("esitati.", False),
        ("5.)", False),
        ("taotlus", False),
        ("1" * 41 + ".", False),
    ],
)
def test_is_protected_break(token: str, protected: bool) -> None:
    text = f"eelmine {token}"
    assert _is_protected_break(text, len(text)) is protected


def test_lowercase_roman_lookalike_is_not_guarded() -> None:
    rng = random.Random(11)
    text = f"{words(rng, 40)} vii. Järgmine {words(rng, 200)}"
    assert SMALL.split(text)[0].end == text.index("vii.") + len("vii.")


# --- invariants over generated documents -----------------------------------


def random_document(rng: random.Random, paragraphs: int) -> str:
    fragments = (*GUARDED_PHRASES, "https://www.eesti.ee/" + "x" * 150)
    parts: list[str] = []
    for _ in range(paragraphs):
        if rng.random() < 0.2:
            items = [f"- {words(rng, rng.randint(5, 60))}" for _ in range(4)]
            parts.append("\n".join(items))
            continue
        sentences: list[str] = []
        for _ in range(rng.randint(1, 6)):
            sentence = words(rng, rng.randint(5, 140))
            if rng.random() < 0.4:
                sentence += f" {rng.choice(fragments)} {words(rng, 20)}"
            if rng.random() < 0.3:
                sentence = sentence.replace(" ", ", ", 1)
            sentences.append(sentence + rng.choice(".!?"))
        parts.append(" ".join(sentences))
    return normalise_nfc("\n\n".join(parts))


@pytest.mark.parametrize(
    "chunker",
    [SMALL, *(Chunker.from_profile(p) for p in CHUNK_PROFILES.values())],
    ids=["small", *CHUNK_PROFILES],
)
def test_invariants_hold_over_generated_documents(chunker: Chunker) -> None:
    rng = random.Random(12)
    for _ in range(200):
        text = random_document(rng, rng.randint(1, 25))
        spans = chunker.split(text)
        assert_invariants(text, spans, chunker)
        assert spans == chunker.split(text)


# --- overlap ---------------------------------------------------------------


def test_next_chunk_starts_at_a_word_start_near_the_overlap() -> None:
    # Within one overlap normally; up to two when the overlap region falls
    # inside a word longer than it ("riigiportaalis" is 14 characters).
    text = words(random.Random(13), 600)
    spans = SMALL.split(text)
    for prev, cur in zip(spans, spans[1:], strict=False):
        assert text[cur.start - 1] == " "
        assert prev.end - 2 * SMALL.overlap <= cur.start < prev.end


def test_long_word_in_overlap_reaches_back_to_its_start() -> None:
    # The last space before target is the one right after the long word, and
    # the 10-character overlap region then falls entirely inside it.
    head = "kodanik saab teenuse kohta infot ning"
    tail = "x" * 30 + " " + words(random.Random(16), 200)
    text = f"{head} riigiportaalis {tail}"
    spans = SMALL.split(text)
    word_start = text.index("riigiportaalis")
    word_end = word_start + len("riigiportaalis")
    assert spans[0].end == word_end
    assert spans[1].start == word_start


# --- chunk_document --------------------------------------------------------


def test_chunk_document_ids_ordinals_and_text() -> None:
    text = random_document(random.Random(14), 10)
    chunks = chunk_document(text, agency_id="a1", document_id="d1", chunker=SMALL)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert chunk.chunk_id == make_chunk_id("a1", "d1", chunk.ordinal)
        assert chunk.document_id == "d1"
        assert chunk.text == text[chunk.span.start : chunk.span.end]
    assert [c.span for c in chunks] == SMALL.split(text)


def test_chunk_document_is_deterministic() -> None:
    text = random_document(random.Random(15), 10)
    first = chunk_document(text, agency_id="a1", document_id="d1", chunker=SMALL)
    second = chunk_document(text, agency_id="a1", document_id="d1", chunker=SMALL)
    assert first == second
