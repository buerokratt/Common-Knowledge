"""Corpus-level chunking assertions (B14).

On the llm_module sink this pipeline is the only chunker in the retrieval
path, so there is no second implementation to compare boundaries against.
Hand-written strings (tests/test_chunking.py) are not enough evidence on
their own; this runs the chunker over committed, real cleaned Estonian
documents and asserts what must hold across all of them:

- every chunk is within [min, max], except a document's last, which may be
  shorter than min only if it is the whole document;
- dropping each chunk's overlap and concatenating reproduces the text;
- no boundary falls inside an ordinal, an abbreviation or a § reference —
  counted, so a regression reads as a number;
- boundaries match a committed snapshot, so a chunker edit cannot move them
  silently.

The protected-span detector here is written independently of
chunking._is_protected_break. Checking the guard against itself would pass
whatever the guard did.

Corpus: tests/fixtures/chunking_corpus/documents/*.md, provenance in
SOURCES.md beside it. A missing corpus fails the tests rather than skipping
them, so B14 cannot be switched off by deleting the fixtures.

Regenerate the snapshot after a deliberate CHUNKER_VERSION or
NORMALISER_VERSION bump, or after adding documents, and review its diff:

    CKB_UPDATE_CHUNKING_SNAPSHOT=1 uv run pytest tests/test_chunking_corpus.py

Regeneration refuses to run when boundaries moved for an unchanged document
under an unchanged fingerprint: that is a chunker edit without a
CHUNKER_VERSION bump.
"""

import bisect
import copy
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from exporter.core.chunking import _ESTONIAN_ABBREVIATIONS, Chunker, chunk_document
from exporter.core.constants import CHUNK_PROFILES, CHUNKER_VERSION, NORMALISER_VERSION
from exporter.core.ids import chunker_fingerprint
from exporter.core.schemas import TextSpan
from exporter.core.text_normaliser import normalise_nfc, sha256_text

CORPUS_DIR = Path(__file__).parent / "fixtures" / "chunking_corpus"
DOCUMENTS_DIR = CORPUS_DIR / "documents"
SNAPSHOT_PATH = CORPUS_DIR / "snapshot.json"
UPDATE_ENV = "CKB_UPDATE_CHUNKING_SNAPSHOT"

# Below this the corpus says little about real documents, whatever passes.
MIN_DOCUMENTS = 3
# Locations quoted in a failure message, and context either side of each.
MAX_REPORTED = 20
CONTEXT = 30

# --- the independent protected-span detector -------------------------------
#
# A protected span runs from the start of a guarded token to the first
# character of the word after it: "3. d" in "3. detsember". A chunk boundary
# strictly inside one cuts the pair apart. Only space and tab count as the
# gap — a line break between them is structure the author wrote, and the
# chunker is allowed to break there.

# The B9 spec's list plus the other forms seen in Estonian public text.
# Matched case-insensitively. Kept here rather than imported, on purpose;
# test_detector_covers_every_chunker_abbreviation keeps it from falling
# behind the chunker's set.
# fmt: off
_ABBREVIATIONS = (
    "jne", "jm", "jt", "jms", "jpt", "nt", "vt", "vrd", "lk", "nr", "lg",
    "ptk", "aa", "sh", "st", "mh", "nn", "nö", "ca", "hr", "pr", "dr",
    "prof", "mag", "tel", "tn", "mnt", "pst", "km", "kr", "ingl", "lad",
    "vm", "vms", "ekr", "pkr",
    "v.a", "s.t", "k.a", "e.m.a", "m.a.j", "e.k", "p.o", "e.kr", "p.kr",
)
# fmt: on

# Opening brackets and quotes, and Markdown emphasis on either side: the
# corpus is cleaned Markdown, where a law's headings read "**§ 1. ****Pealkiri".
_TOKEN_START = r"(?<!\S)[(\[{„“«'\"*_]*"
_FOLLOWED_BY_WORD = r"[*_]*[ \t]+\S"
# Emphasis may also close before the period: "**2024**. aasta".
_PERIOD = r"[*_]*\."
_ABBREVIATION_ALTERNATION = "|".join(
    re.escape(a) for a in sorted(_ABBREVIATIONS, key=len, reverse=True)
)
_PROTECTED_FORMS = {
    "ordinal": rf"\d+(?:\.\d+)*{_PERIOD}",
    "section": r"§§?",
    "abbreviation": rf"(?i:{_ABBREVIATION_ALTERNATION}){_PERIOD}",
    # Well-formed numerals only, and never the empty match the pattern allows.
    "roman": (
        r"(?=[IVXLCDM])M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})"
        + _PERIOD
    ),
    # Any single letter, either case: "A. Tammsaare", and "2024. a." for aasta.
    "initial": rf"[^\W\d_]{_PERIOD}",
}
# Wrapped in a lookahead so overlapping spans are all found: "§ 5. Kohaldamisala"
# holds both "§ 5" and "5. K".
_PROTECTED_PATTERNS = {
    kind: re.compile(rf"(?=({_TOKEN_START}(?:{form}){_FOLLOWED_BY_WORD}))")
    for kind, form in _PROTECTED_FORMS.items()
}


def protected_spans(text: str) -> list[tuple[int, int, str]]:
    """Every (start, end, kind) a chunk boundary must not fall inside."""
    return sorted(
        (match.start(1), match.end(1), kind)
        for kind, pattern in _PROTECTED_PATTERNS.items()
        for match in pattern.finditer(text)
    )


def boundaries(spans: list[TextSpan]) -> list[int]:
    """Every position a chunk starts or ends at, other than 0 and len."""
    inner = {span.end for span in spans[:-1]} | {span.start for span in spans[1:]}
    return sorted(inner)


def protected_violations(text: str, spans: list[TextSpan]) -> list[tuple[int, str]]:
    """(boundary, kind) for every boundary strictly inside a protected span."""
    cuts = boundaries(spans)
    found: list[tuple[int, str]] = []
    for start, end, kind in protected_spans(text):
        i = bisect.bisect_right(cuts, start)
        while i < len(cuts) and cuts[i] < end:
            found.append((cuts[i], kind))
            i += 1
    return found


def _context(text: str, position: int) -> str:
    return repr(
        text[max(0, position - CONTEXT) : position]
        + "|"
        + text[position : position + CONTEXT]
    )


# (phrase, kind of the protected span that opens it)
_DETECTOR_CASES = (
    ("3. detsember", "ordinal"),
    ("2024. aasta", "ordinal"),
    ("5.2. punkt", "ordinal"),
    ("§ 5", "section"),
    ("§§ 5", "section"),
    ("jne. Järgmine", "abbreviation"),
    ("(nt. taotlus", "abbreviation"),
    ("mag. Tamm", "abbreviation"),
    ("v.a. puhkepäevad", "abbreviation"),
    ("II. osa", "roman"),
    ("A. Tammsaare", "initial"),
    ("a. lõpus", "initial"),
    ("**§ 1. ****Seaduse", "section"),
    ("**3. detsember**", "ordinal"),
    ("**2024.** aasta", "ordinal"),
    ("_nt._ taotlus", "abbreviation"),
    ("**2024**. aasta", "ordinal"),
    ("**3**. detsember", "ordinal"),
    ("_nt_. taotlus", "abbreviation"),
    ("**II**. osa", "roman"),
    ("**A**. Tammsaare", "initial"),
)


def test_detector_cases_cover_every_form() -> None:
    assert {kind for _, kind in _DETECTOR_CASES} == set(_PROTECTED_FORMS)


@pytest.mark.parametrize(("phrase", "kind"), _DETECTOR_CASES)
def test_detector_reports_every_cut_inside_a_protected_span(
    phrase: str, kind: str
) -> None:
    """Runs without the corpus: a detector that missed a form would make the
    corpus assertion pass vacuously. Every interior position is tried, so
    both a cut after the period and a cut after the space are covered."""
    text = f"Eelmine lause. {phrase} ja edasi"
    at = text.index(phrase)
    opening = [
        (start, end)
        for start, end, found in protected_spans(text)
        if found == kind and start == at
    ]
    assert opening, f"no {kind} span detected at the start of {phrase!r}"
    start, end = opening[0]
    for cut in range(start + 1, end):
        split = [TextSpan(0, cut), TextSpan(cut, len(text))]
        assert (cut, kind) in protected_violations(text, split), _context(text, cut)


def test_detector_covers_every_chunker_abbreviation() -> None:
    """The detector's list is written separately, but must not be narrower
    than the chunker's: an abbreviation only the chunker knows is one whose
    guard could be removed without this file noticing."""
    detector = {a.lower() for a in _ABBREVIATIONS}
    assert not sorted(_ESTONIAN_ABBREVIATIONS - detector)


def test_detector_ignores_sentence_ends_and_line_breaks() -> None:
    text = "Taotlus esitati. Järgmine DVD. **Sissejuhatus.** Kolm\n4.\nrida"
    assert protected_spans(text) == []


# --- the corpus --------------------------------------------------------------


@pytest.fixture(scope="module")
def corpus() -> dict[str, str]:
    """Normalised text of every committed document, by file name — exactly
    the string the pipeline hashes and chunks."""
    paths = sorted(DOCUMENTS_DIR.glob("*.md")) if DOCUMENTS_DIR.is_dir() else []
    if not paths:
        # Fail, not skip: a skipped corpus test is a green CI run with B14
        # silently switched off.
        pytest.fail(
            "B14: the fixture corpus is missing. Expected cleaned Markdown "
            f"exports in {DOCUMENTS_DIR.relative_to(Path(__file__).parent.parent)}, "
            "each listed in SOURCES.md."
        )
    return {
        path.name: normalise_nfc(path.read_text(encoding="utf-8")) for path in paths
    }


@pytest.fixture(scope="module")
def chunked(corpus: dict[str, str]) -> dict[str, dict[str, list[TextSpan]]]:
    """profile name -> document name -> spans."""
    return {
        name: {
            document: Chunker.from_profile(profile).split(text)
            for document, text in corpus.items()
        }
        for name, profile in CHUNK_PROFILES.items()
    }


def test_corpus_exercises_the_guard(corpus: dict[str, str]) -> None:
    """A corpus with no ordinals proves nothing about the ordinal guard."""
    assert len(corpus) >= MIN_DOCUMENTS, (
        f"B14: {len(corpus)} documents; at least {MIN_DOCUMENTS} needed"
    )
    kinds = {kind for text in corpus.values() for _, _, kind in protected_spans(text)}
    missing = {"ordinal", "section", "abbreviation"} - kinds
    assert not missing, f"B14: the corpus contains no {sorted(missing)} at all"
    sourced = (CORPUS_DIR / "SOURCES.md").read_text(encoding="utf-8")
    unlisted = sorted(name for name in corpus if name not in sourced)
    assert not unlisted, f"B14: documents with no provenance in SOURCES.md: {unlisted}"


def _invariant_violations(
    document: str, text: str, spans: list[TextSpan], chunker: Chunker
) -> list[str]:
    if not text:
        return [] if not spans else [f"{document}: chunks for an empty document"]
    found: list[str] = []
    if spans[0].start != 0 or spans[-1].end != len(text):
        found.append(f"{document}: spans do not cover [0, {len(text)})")
    for prev, cur in zip(spans, spans[1:], strict=False):
        if not prev.start < cur.start <= prev.end:
            found.append(
                f"{document}: {prev} -> {cur} does not advance or leaves a gap"
            )
        if cur.end <= prev.end:
            found.append(
                f"{document}: {cur} lies inside {prev}, so it would be "
                "published and embedded twice"
            )
        if prev.end - cur.start >= chunker.min_size:
            found.append(
                f"{document}: {cur} starts min_size or more before {prev} ends"
            )
    for i, span in enumerate(spans):
        length = span.end - span.start
        if length > chunker.max_size:
            found.append(f"{document}: {span} is {length} > max {chunker.max_size}")
        if i < len(spans) - 1 and length < chunker.min_size:
            found.append(f"{document}: {span} is {length} < min {chunker.min_size}")
    if len(spans) > 1 and spans[-1].end - spans[-1].start < chunker.min_size:
        found.append(f"{document}: final {spans[-1]} is a sliver below min")
    # Rebuild from the chunk text the pipeline publishes, not by re-slicing
    # `text` with the spans — that would only restate the coverage checks.
    chunks = chunk_document(
        text, agency_id="fixture-agency", document_id=document, chunker=chunker
    )
    if [chunk.span for chunk in chunks] != spans:
        found.append(f"{document}: chunk_document spans differ from split()")
    rebuilt = chunks[0].text if chunks else ""
    for prev, cur in zip(chunks, chunks[1:], strict=False):
        overlap = prev.span.end - cur.span.start
        if overlap > 2 * chunker.overlap:
            found.append(
                f"{document}: {cur.span} repeats {overlap} characters of the "
                f"previous chunk; at most 2 * overlap = {2 * chunker.overlap}"
            )
        if cur.text[:overlap] != prev.text[len(prev.text) - overlap :]:
            found.append(f"{document}: {cur.span} does not open on its overlap")
        rebuilt += cur.text[overlap:]
    if rebuilt != text:
        found.append(f"{document}: chunks minus overlap do not rebuild the text")
    return found


@pytest.mark.parametrize("profile", sorted(CHUNK_PROFILES))
def test_chunk_invariants_hold_over_the_corpus(
    corpus: dict[str, str],
    chunked: dict[str, dict[str, list[TextSpan]]],
    profile: str,
) -> None:
    chunker = Chunker.from_profile(CHUNK_PROFILES[profile])
    found = [
        violation
        for document, text in corpus.items()
        for violation in _invariant_violations(
            document, text, chunked[profile][document], chunker
        )
    ]
    assert not found, (
        f"{len(found)} invariant violations under {profile}:\n  "
        + "\n  ".join(found[:MAX_REPORTED])
    )


def _protected_report(
    corpus: dict[str, str], spans_by_document: dict[str, list[TextSpan]]
) -> list[str]:
    return [
        f"{document}@{cut} ({kind}): {_context(text, cut)}"
        for document, text in corpus.items()
        for cut, kind in protected_violations(text, spans_by_document[document])
    ]


@pytest.mark.parametrize("profile", sorted(CHUNK_PROFILES))
def test_no_boundary_inside_a_protected_span(
    corpus: dict[str, str],
    chunked: dict[str, dict[str, list[TextSpan]]],
    profile: str,
) -> None:
    """The B9 bar on real text: zero, reported as a count."""
    found = _protected_report(corpus, chunked[profile])
    assert not found, (
        f"{len(found)} boundaries inside an ordinal, abbreviation or § reference "
        f"under {profile}:\n  " + "\n  ".join(found[:MAX_REPORTED])
    )


# --- the stability snapshot --------------------------------------------------


def _metrics(
    corpus: dict[str, str], spans_by_document: dict[str, list[TextSpan]]
) -> dict[str, float | int]:
    lengths = [
        span.end - span.start for spans in spans_by_document.values() for span in spans
    ]
    hard_cuts = sum(
        1
        for document, spans in spans_by_document.items()
        for span in spans[:-1]
        if not corpus[document][span.end - 1].isspace()
        and not corpus[document][span.end].isspace()
    )
    return {
        "chunks": len(lengths),
        "min_length": min(lengths, default=0),
        "mean_length": round(sum(lengths) / len(lengths), 1) if lengths else 0,
        "hard_cuts": hard_cuts,
        "protected_boundaries": len(_protected_report(corpus, spans_by_document)),
    }


def _snapshot(
    corpus: dict[str, str], chunked: dict[str, dict[str, list[TextSpan]]]
) -> dict[str, Any]:
    return {
        "chunker_version": CHUNKER_VERSION,
        "normaliser_version": NORMALISER_VERSION,
        "documents": {
            document: {"length": len(text), "normalised_sha256": sha256_text(text)}
            for document, text in corpus.items()
        },
        "profiles": {
            name: {
                "fingerprint": chunker_fingerprint(CHUNK_PROFILES[name]),
                "metrics": _metrics(corpus, chunked[name]),
                "spans": {
                    document: [f"{span.start}-{span.end}" for span in spans]
                    for document, spans in chunked[name].items()
                },
            }
            for name in sorted(chunked)
        },
    }


def _first_difference(expected: dict[str, Any], actual: dict[str, Any]) -> str:
    """The most specific explanation for a snapshot mismatch."""
    versions = ("chunker_version", "normaliser_version")
    if any(expected.get(key) != actual[key] for key in versions):
        return (
            "CHUNKER_VERSION or NORMALISER_VERSION changed. Regenerate the "
            f"snapshot ({UPDATE_ENV}=1) and review how far boundaries moved."
        )
    if set(expected.get("documents", {})) != set(actual["documents"]):
        return f"The corpus gained or lost documents. Regenerate ({UPDATE_ENV}=1)."
    for document, entry in actual["documents"].items():
        if expected["documents"][document] != entry:
            return (
                f"{document}: normalised text changed with no NORMALISER_VERSION "
                "bump. If the fixture was edited, regenerate; if normalisation "
                "changed, bump NORMALISER_VERSION in constants.py."
            )
    removed = sorted(set(expected.get("profiles", {})) - set(actual["profiles"]))
    if removed:
        return f"Profiles {removed} were removed from CHUNK_PROFILES. Regenerate."
    for name, profile in actual["profiles"].items():
        previous = expected.get("profiles", {}).get(name)
        if previous is None or previous["fingerprint"] != profile["fingerprint"]:
            return f"Profile {name} is new or its geometry changed. Regenerate."
        for document, spans in profile["spans"].items():
            if previous["spans"].get(document) != spans:
                return (
                    f"{document} under {name}: boundaries moved with no "
                    "CHUNKER_VERSION bump. Published chunks would silently "
                    "change geometry — bump CHUNKER_VERSION in constants.py, "
                    "then regenerate."
                )
    return "Metrics differ; regenerate and review the diff."


def _unversioned_moves(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """Documents whose boundaries moved although nothing that may move them
    did: the profile's fingerprint (versions + geometry) and the document's
    normalised text are both unchanged. Only a chunker edit can do that, and
    a chunker edit must bump CHUNKER_VERSION."""
    moved: list[str] = []
    for name, profile in actual["profiles"].items():
        previous = expected.get("profiles", {}).get(name)
        if previous is None or previous["fingerprint"] != profile["fingerprint"]:
            continue
        for document, spans in profile["spans"].items():
            if (
                expected.get("documents", {}).get(document)
                != actual["documents"][document]
            ):
                continue  # a new or edited fixture; its boundaries may move
            if previous["spans"].get(document) != spans:
                moved.append(f"{document} under {name}")
    return moved


def test_regeneration_refuses_only_an_unversioned_move() -> None:
    base = {
        "documents": {"d.md": {"length": 3, "normalised_sha256": "x"}},
        "profiles": {"p": {"fingerprint": "f", "spans": {"d.md": ["0-3"]}}},
    }
    moved = copy.deepcopy(base)
    moved["profiles"]["p"]["spans"]["d.md"] = ["0-2", "1-3"]
    assert _unversioned_moves(base, moved) == ["d.md under p"]

    bumped = copy.deepcopy(moved)
    bumped["profiles"]["p"]["fingerprint"] = "g"
    assert _unversioned_moves(base, bumped) == []

    edited = copy.deepcopy(moved)
    edited["documents"]["d.md"]["normalised_sha256"] = "y"
    assert _unversioned_moves(base, edited) == []


def test_boundaries_match_the_snapshot(
    corpus: dict[str, str], chunked: dict[str, dict[str, list[TextSpan]]]
) -> None:
    actual = _snapshot(corpus, chunked)
    if os.environ.get(UPDATE_ENV) == "1":
        # Regenerating rewrites boundaries and versions together, so on its
        # own it would let a chunker edit land with no CHUNKER_VERSION bump.
        if SNAPSHOT_PATH.is_file():
            moved = _unversioned_moves(
                json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8")), actual
            )
            assert not moved, (
                "Refusing to regenerate the snapshot: boundaries moved with no "
                "CHUNKER_VERSION bump, so published chunks would silently change "
                "geometry. Bump CHUNKER_VERSION in constants.py first. Moved: "
                + ", ".join(moved[:MAX_REPORTED])
            )
        SNAPSHOT_PATH.write_text(
            json.dumps(actual, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return

    assert SNAPSHOT_PATH.is_file(), (
        f"B14: no snapshot. Generate it with {UPDATE_ENV}=1 and commit it."
    )
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert expected == actual, _first_difference(expected, actual)
