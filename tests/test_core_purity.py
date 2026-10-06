"""The rules Stage B places on exporter/core/ as a whole.

- Purity (C20, "Do not let any sink concern reach this stage"): nothing under
  core/ does I/O or knows a destination. Checked as an import allowlist, so a
  new import is a reviewed decision rather than a quiet addition.
- The metadata sidecar must not influence chunk boundaries or content_sha256:
  chunking reads the content and nothing else, or metadata_changed becomes
  unrepresentable.

The blob key templates and builders do live here (B1, B6). They are pure
string formatting that names no store client, which is how they meet the
rule: they import nothing outside core/ and the standard library, the same
as everything else this file checks.
"""

import ast
import inspect
from pathlib import Path

import pytest

from exporter.core import chunking, text_normaliser
from exporter.core.chunking import Chunker, chunk_document

CORE_DIR = Path(chunking.__file__).parent
CORE_MODULES = sorted(CORE_DIR.glob("*.py"))

# Pure standard-library modules: text, hashing and data shapes only. No os,
# io, pathlib, socket, http, urllib, subprocess, tempfile, logging handlers.
_ALLOWED_STDLIB = frozenset(
    {
        "bisect",
        "collections",
        "collections.abc",
        "dataclasses",
        "enum",
        "functools",
        "hashlib",
        "itertools",
        "math",
        "re",
        "types",
        "typing",
        "unicodedata",
    }
)
# Builtins that reach outside the process.
_FORBIDDEN_CALLS = frozenset({"open", "input", "exec", "eval", "__import__"})


def _imports(tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module)
    return found


def _disallowed(module: str) -> bool:
    return module not in _ALLOWED_STDLIB and not module.startswith("exporter.core.")


def test_core_has_modules_to_check() -> None:
    assert {p.stem for p in CORE_MODULES} >= {
        "constants",
        "schemas",
        "ids",
        "text_normaliser",
        "chunking",
    }


@pytest.mark.parametrize("path", CORE_MODULES, ids=lambda p: p.stem)
def test_core_module_imports_nothing_impure(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad = sorted(module for module in _imports(tree) if _disallowed(module))
    assert not bad, (
        f"exporter/core/{path.name} imports {bad}. core/ does no I/O and names "
        "no sink, store or HTTP status; anything that does belongs in "
        "sinks/, stores/ or services/."
    )
    calls = sorted(
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FORBIDDEN_CALLS
    )
    assert not calls, f"exporter/core/{path.name} calls {calls}"


def test_the_purity_check_catches_an_impure_import() -> None:
    """The check's own regression test, so a broken collector cannot leave
    the test above passing vacuously."""
    tree = ast.parse(
        "import os\nfrom exporter.sinks.object_store import X\nfrom . import ids\n"
    )
    assert sorted(m for m in _imports(tree) if _disallowed(m)) == [
        "exporter.sinks.object_store",
        "os",
    ]


# --- the metadata sidecar cannot reach chunking ----------------------------


def test_chunking_reads_text_and_coordinates_only() -> None:
    """chunk_document is the only door into chunking, and it takes the
    normalised content and the chunk's coordinates — nothing a sidecar could
    arrive through."""
    assert list(inspect.signature(chunk_document).parameters) == [
        "text",
        "agency_id",
        "document_id",
        "chunker",
    ]
    assert list(inspect.signature(Chunker.split).parameters) == ["self", "text"]


def test_content_hash_reads_text_only() -> None:
    assert list(inspect.signature(text_normaliser.sha256_text).parameters) == ["text"]
