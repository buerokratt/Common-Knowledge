"""Tests for content-external's ids and key builders (B4-B7, B12, B13).

Pure: no Docker, no network. The golden values below are the point, not
incidental: a chunk id or a fingerprint that changes without a deliberate
version bump silently re-keys or re-chunks a published corpus, so any change
to either scheme must fail here first.
"""

import ast
import inspect
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from exporter.api.config import Settings
from exporter.core import ids
from exporter.core.constants import (
    CHUNK_PROFILES,
    CHUNKER_VERSION,
    ID_VERSION,
    MAX_CHUNK_ORDINAL,
    NORMALISER_VERSION,
    ChunkProfile,
)
from exporter.core.ids import (
    chunker_fingerprint,
    document_chunk_key,
    document_metadata_key,
    make_chunk_id,
    manifest_key,
    pending_deletions_key,
)

# Pinned outputs. If one of these fails, either revert the change or bump
# the matching version constant deliberately and update the value here.
GOLDEN_CHUNK_ID = "c77d639bffd4b0db594b86216a35dcd85"  # make_chunk_id("a1", "d1", 0)
# CHUNKER_VERSION 3 (no chunk inside its predecessor; emphasis anywhere in a
# guarded token), with ID_NAMESPACE and ID_VERSION now fingerprint inputs.
GOLDEN_FINGERPRINTS = {
    "azure_native": "5940bd4486d2275ac3a6d064225f389923694b9bcc6a9cda7dec49d3957235e7",
    "compact": "0244a36bbac3ace7bf520bba4a16e4bb850a122e8ef0be168894dbad43cbd18a",
}

# --- B4: chunk ids ---------------------------------------------------------


def test_chunk_id_is_pinned() -> None:
    """Stable across runs, processes and machines — the update path's
    idempotency rests on this one value not drifting."""
    assert make_chunk_id("a1", "d1", 0) == GOLDEN_CHUNK_ID


def test_chunk_id_format() -> None:
    assert re.fullmatch(r"c[0-9a-f]{32}", make_chunk_id("a1", "d1", 12345))


@pytest.mark.parametrize(
    "other",
    [("a2", "d1", 0), ("a1", "d2", 0), ("a1", "d1", 1)],
    ids=["agency", "document", "ordinal"],
)
def test_every_coordinate_changes_the_chunk_id(other: tuple[str, str, int]) -> None:
    assert make_chunk_id(*other) != GOLDEN_CHUNK_ID


def test_chunk_id_takes_coordinates_only() -> None:
    """B4: never the chunk's text. Hashing text would re-key every chunk
    downstream of a one-paragraph edit."""
    params = list(inspect.signature(make_chunk_id).parameters)
    assert params == ["agency_id", "document_id", "ordinal"]


# Everything make_chunk_id's body may read. The signature alone does not stop
# the body hashing something else; this does.
_CHUNK_ID_NAMES = frozenset(
    {
        "ID_NAMESPACE",
        "ID_VERSION",
        "agency_id",
        "document_id",
        "ordinal",
        "str",
        "payload",
        "hashlib",
        "_CHUNK_ID_DIGEST_SIZE",
        "digest",
        "_CHUNK_ID_PREFIX",
    }
)


def test_chunk_id_body_reads_coordinates_and_constants_only() -> None:
    names, _ = _loaded_inputs(_ids_function("make_chunk_id"))
    assert names == _CHUNK_ID_NAMES, (
        f"B4: make_chunk_id gained or lost an input: {sorted(names ^ _CHUNK_ID_NAMES)}"
    )


def test_id_version_bump_rekeys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ids, "ID_VERSION", ID_VERSION + 1)
    assert make_chunk_id("a1", "d1", 0) != GOLDEN_CHUNK_ID


def test_id_namespace_is_part_of_the_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ids, "ID_NAMESPACE", "some-other-system")
    assert make_chunk_id("a1", "d1", 0) != GOLDEN_CHUNK_ID


# --- B5: no derived document id --------------------------------------------


def test_there_is_no_make_document_id() -> None:
    assert not hasattr(ids, "make_document_id"), (
        "B5: document_id IS source_file.base_id. See the comment above "
        "make_chunk_id in exporter/core/ids.py for the three bugs a derived "
        "id reintroduces."
    )


# --- B7: the chunker fingerprint -------------------------------------------


@pytest.mark.parametrize("name", sorted(CHUNK_PROFILES))
def test_fingerprint_is_pinned(name: str) -> None:
    assert chunker_fingerprint(CHUNK_PROFILES[name]) == GOLDEN_FINGERPRINTS[name]


def test_golden_fingerprints_cover_every_profile() -> None:
    """A new preset must get its own pinned value, not slip past the test."""
    assert set(GOLDEN_FINGERPRINTS) == set(CHUNK_PROFILES)


def test_every_profile_has_a_distinct_fingerprint() -> None:
    fingerprints = [chunker_fingerprint(p) for p in CHUNK_PROFILES.values()]
    assert len(set(fingerprints)) == len(fingerprints)


@pytest.mark.parametrize("field", ChunkProfile._fields)
def test_each_size_param_changes_the_fingerprint(field: str) -> None:
    base = CHUNK_PROFILES["azure_native"]
    moved = base._replace(**{field: getattr(base, field) + 1})
    assert chunker_fingerprint(moved) != chunker_fingerprint(base)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CHUNKER_VERSION", CHUNKER_VERSION + 1),
        ("NORMALISER_VERSION", NORMALISER_VERSION + 1),
        # Either re-keys every chunk id, so either must re-publish every
        # document, not only the ones whose content changed.
        ("ID_VERSION", ID_VERSION + 1),
        ("ID_NAMESPACE", "some-other-system"),
    ],
)
def test_version_bump_changes_the_fingerprint(
    monkeypatch: pytest.MonkeyPatch, name: str, value: object
) -> None:
    monkeypatch.setattr(ids, name, value)
    assert (
        chunker_fingerprint(CHUNK_PROFILES["azure_native"])
        != GOLDEN_FINGERPRINTS["azure_native"]
    )


# --- B6: blob key builders -------------------------------------------------


def test_key_builders_fill_the_templates() -> None:
    assert manifest_key("content", "a1") == "content/agencies/a1/manifest.json"
    assert (
        document_metadata_key("content", "a1", "d1")
        == "content/agencies/a1/documents/d1/metadata.json"
    )
    assert (
        document_chunk_key("content", "a1", "d1", 7)
        == "content/agencies/a1/documents/d1/chunks/00007.json"
    )
    assert (
        pending_deletions_key("content", "r1")
        == "content/_control/deletions/pending-r1.jsonl"
    )


def test_chunk_keys_sort_in_ordinal_order() -> None:
    """Zero-padding is what makes a store listing come back in ordinal
    order, which the orphan sweep relies on."""
    keys = [document_chunk_key("p", "a", "d", i) for i in range(100_000)]
    assert keys == sorted(keys)


def test_metadata_and_chunks_own_disjoint_key_spaces() -> None:
    chunk_dir = document_chunk_key("p", "a", "d", 0).rsplit("/", 1)[0] + "/"
    assert not document_metadata_key("p", "a", "d").startswith(chunk_dir)


def test_a_multi_segment_prefix_is_accepted() -> None:
    assert manifest_key("ckb/content", "a1") == "ckb/content/agencies/a1/manifest.json"


@pytest.mark.parametrize(
    "builder",
    [
        lambda v: manifest_key("p", v),
        lambda v: document_metadata_key("p", "a", v),
        lambda v: document_chunk_key("p", v, "d", 0),
        lambda v: pending_deletions_key("p", v),
    ],
    ids=["manifest-agency", "metadata-document", "chunk-agency", "deletions-run"],
)
@pytest.mark.parametrize("bad", ["", ".", "..", "../x", "a/b"])
def test_key_builders_refuse_a_bad_id_segment(
    builder: Callable[[str], str], bad: str
) -> None:
    """A key is a path: "../x" or "a/b" as an id writes where the manifest
    and the orphan sweep never look."""
    with pytest.raises(ValueError):
        builder(bad)


@pytest.mark.parametrize("bad", ["", "/content", "content/", "a//b", "a/../b", "."])
def test_key_builders_refuse_a_bad_prefix(bad: str) -> None:
    with pytest.raises(ValueError):
        manifest_key(bad, "a1")


@pytest.mark.parametrize("bad", [-1, MAX_CHUNK_ORDINAL + 1, True, 1.0])
def test_chunk_key_refuses_an_ordinal_the_template_cannot_sort(bad: object) -> None:
    with pytest.raises(ValueError):
        document_chunk_key("p", "a", "d", bad)  # type: ignore[arg-type]


def test_the_largest_ordinal_still_has_five_digits() -> None:
    key = document_chunk_key("p", "a", "d", MAX_CHUNK_ORDINAL)
    assert key.endswith(f"/chunks/{MAX_CHUNK_ORDINAL}.json")
    assert len(str(MAX_CHUNK_ORDINAL)) == 5


# --- B13: the fingerprint does not include the sink -------------------------
#
# Switching destination must not re-chunk: the geometry did not change, the
# destination did, and the manifest's sink_id guards destination identity
# (F17). On the llm_module sink a re-chunk is a full corpus re-embed on
# someone else's service.

# Everything chunker_fingerprint may read. A new name here is a new input to
# the fingerprint, which is a decision to review, not a refactor.
_FINGERPRINT_NAMES = frozenset(
    {
        "CHUNKER_VERSION",
        "NORMALISER_VERSION",
        "ID_NAMESPACE",
        "ID_VERSION",
        "hashlib",
        "payload",
        "profile",
        "str",
    }
)


def _loaded_inputs(function: ast.FunctionDef) -> tuple[set[str], set[str]]:
    """The bare names a function's body reads, and the attributes it reads
    off its `profile` argument. The body only: the signature is checked
    separately, and its annotation is a type, not an input."""
    names: set[str] = set()
    profile_fields: set[str] = set()
    body = ast.Module(body=function.body, type_ignores=[])
    for node in ast.walk(body):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            names.add(node.id)
        elif (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "profile"
        ):
            profile_fields.add(node.attr)
    return names, profile_fields


def _ids_function(name: str) -> ast.FunctionDef:
    tree = ast.parse(Path(inspect.getfile(ids)).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in exporter/core/ids.py")


def _fingerprint_function() -> ast.FunctionDef:
    return _ids_function("chunker_fingerprint")


def test_fingerprint_takes_only_the_profile() -> None:
    signature = inspect.signature(chunker_fingerprint)
    assert list(signature.parameters) == ["profile"]
    assert signature.parameters["profile"].annotation is ChunkProfile


def test_fingerprint_reads_nothing_but_versions_and_geometry() -> None:
    names, profile_fields = _loaded_inputs(_fingerprint_function())
    assert names == _FINGERPRINT_NAMES, (
        "B13: chunker_fingerprint gained or lost an input: "
        f"{sorted(names ^ _FINGERPRINT_NAMES)}. It must cover CHUNKER_VERSION, "
        "NORMALISER_VERSION, ID_NAMESPACE, ID_VERSION and the four size params "
        "and nothing else — never "
        "the sink, the store, a prefix or a setting. Destination identity is "
        "the manifest's sink_id (F17)."
    )
    assert profile_fields == set(ChunkProfile._fields)


def test_an_extra_fingerprint_input_would_be_caught() -> None:
    """The check's own regression test, so a broken collector cannot leave
    the test above passing vacuously."""
    leaky = ast.parse(
        "def chunker_fingerprint(profile):\n"
        "    return str((CHUNKER_VERSION, profile.target, SINK_ID))\n"
    ).body[0]
    assert isinstance(leaky, ast.FunctionDef)
    names, _ = _loaded_inputs(leaky)
    assert "SINK_ID" in names


# Every destination shape a deployment can configure, each with the minimum
# settings A12's matrix accepts.
_DESTINATIONS: dict[str, dict[str, str]] = {
    "object_store-s3": {"content_sink": "object_store"},
    "object_store-azure_blob": {
        "content_sink": "object_store",
        "content_external_store_backend": "azure_blob",
        "content_external_prefix": "elsewhere",
    },
    "object_store-own-manifest-store": {
        "content_sink": "object_store",
        "manifest_store_backend": "s3",
        "manifest_store_endpoint_url": "https://store.example",
        "manifest_store_bucket": "content-manifests",
    },
    "llm_module-s3-manifest": {
        "content_sink": "llm_module",
        "manifest_store_backend": "s3",
        "manifest_store_endpoint_url": "https://store.example",
        "manifest_store_bucket": "content-manifests",
        "llm_module_base_url": "https://llm.example/ingest",
        "llm_module_vault_secret_path": "llm/connections/ingest",
    },
    "llm_module-azure_blob-manifest": {
        "content_sink": "llm_module",
        "manifest_store_backend": "azure_blob",
        "manifest_store_bucket": "content-manifests",
        "llm_module_base_url": "https://llm.example/ingest",
        "llm_module_vault_secret_path": "llm/connections/ingest",
    },
}


@pytest.mark.parametrize("name", sorted(CHUNK_PROFILES))
def test_fingerprint_is_identical_for_every_destination(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    for field in Settings.model_fields:
        monkeypatch.delenv(field.upper(), raising=False)

    fingerprints = {
        destination: chunker_fingerprint(
            Settings(
                content_work_dir="/var/lib/content-external",
                chunk_profile=name,
                **overrides,  # pyright: ignore[reportArgumentType]
            ).resolved_chunk_profile
        )
        for destination, overrides in _DESTINATIONS.items()
    }
    assert set(fingerprints.values()) == {GOLDEN_FINGERPRINTS[name]}, fingerprints
