"""The rules Stages B and C place on exporter/core/ as a whole.

- Purity (C20, "Do not let any sink concern reach this stage"): nothing under
  core/ does I/O or knows a destination. Checked three ways, so a new import
  or a new destination word is a reviewed decision rather than a quiet
  addition: an import allowlist; a vocabulary scan of every name and string
  literal for sink, store, transport, HTTP and Qdrant words; and no public
  core function taking a sink, store or backend parameter. That last one is
  what keeps classify() the same for every destination.
- The metadata sidecar must not influence chunk boundaries or content_sha256:
  chunking reads the content and nothing else, or metadata_changed becomes
  unrepresentable.

The blob key templates and builders do live here (B1, B6). They are pure
string formatting that names no store client, which is how they meet the
rule: they import nothing outside core/ and the standard library, the same
as everything else this file checks, and their names are the only blob
words the vocabulary scan allows. The task breakdown's original check, a
grep for 'sinks|stores|blob|http' over core/, could never pass beside B6,
the citation-scheme check in metadata.py or the sink_id field; this file is
the form of that check that can.
"""

import ast
import importlib
import inspect
import re
from pathlib import Path

import pytest

from exporter.core import chunking, diff, text_normaliser
from exporter.core.chunking import Chunker, chunk_document

CORE_DIR = Path(chunking.__file__).parent
CORE_MODULES = sorted(CORE_DIR.glob("*.py"))


def code_words(path: Path) -> list[str]:
    """Everything in a Python file a program could act on: names, imports,
    attributes and string literals, but not docstrings or comments. Prose
    may record a decision about a destination; code may not act on one."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    words: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            words.append(node.id)
        elif isinstance(node, ast.Attribute):
            words.append(node.attr)
        elif isinstance(node, ast.alias):
            words.append(node.name)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            words.append(node.name)
        elif isinstance(node, ast.arg):  # parameters, private functions included
            words.append(node.arg)
        elif isinstance(node, ast.keyword) and node.arg is not None:  # f(name=...)
            words.append(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name is not None:
            words.append(node.name)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            words.append(node.value)
    return words


# Pure standard-library modules: text, hashing and data shapes only. No os,
# io, pathlib, socket, http, subprocess, tempfile, logging handlers — and no
# urllib except urllib.parse, which is string parsing (metadata.py validates
# citation URLs with it). Names match exactly, so urllib.request stays out.
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
        "json",
        "math",
        "re",
        "types",
        "typing",
        "unicodedata",
        "urllib.parse",
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
        "metadata",
        "diff",
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
        "import urllib.request\nfrom urllib.parse import urlsplit\n"
    )
    assert sorted(m for m in _imports(tree) if _disallowed(m)) == [
        "exporter.sinks.object_store",
        "os",
        "urllib.request",
    ]


def test_code_words_sees_every_kind_of_name(tmp_path: Path) -> None:
    """The collector's own regression test: parameters, call-site keywords,
    async functions and exception names are all code, not prose."""
    source = tmp_path / "names.py"
    source.write_text(
        "async def fetch_async() -> None: ...\n"
        "def _helper(sink_param: int) -> int:\n"
        "    try:\n"
        "        return call(store_kw=sink_param)\n"
        "    except ValueError as caught_error:\n"
        "        raise caught_error\n",
        encoding="utf-8",
    )
    words = set(code_words(source))
    assert {"fetch_async", "sink_param", "store_kw", "caught_error"} <= words


# --- C10: nothing in the service uses DVC ----------------------------------

CONTENT_EXTERNAL_DIR = CORE_DIR.parents[1]


def test_nothing_in_the_service_uses_dvc() -> None:
    """C10: no DVC functions, no DVC config and none of the ancestor's
    direct-credential settings. Python files are checked by their code,
    other files (Dockerfile, config) by their whole text; Markdown is prose."""
    offenders: list[str] = []
    for path in CONTENT_EXTERNAL_DIR.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".md":
            continue
        if path.suffix == ".py":
            text = "\n".join(code_words(path))
        else:
            text = path.read_text(encoding="utf-8", errors="ignore")
        if "dvc" in text.lower():
            offenders.append(path.relative_to(CONTENT_EXTERNAL_DIR).as_posix())
    assert not offenders


def test_the_dvc_check_sees_code_but_not_prose(tmp_path: Path) -> None:
    """The check's own regression test, so it cannot pass vacuously."""
    prose = tmp_path / "prose.py"
    prose.write_text('"""No DVC here."""\n# nor dvc here\nx = 1\n', encoding="utf-8")
    code = tmp_path / "code.py"
    code.write_text("def initialize_dvc() -> None:\n    pass\n", encoding="utf-8")
    assert not any("dvc" in w.lower() for w in code_words(prose))
    assert "initialize_dvc" in code_words(code)


# --- C20: core names no destination ----------------------------------------

# Matched against each code word, case-insensitively.
_DESTINATION_VOCABULARY = {
    "sink identity": re.compile(r"object_store|llm[_-]module|sink", re.IGNORECASE),
    "store or transport": re.compile(
        r"blob|boto|s3|etag|presign|put_bytes|list_keys", re.IGNORECASE
    ),
    "HTTP": re.compile(r"http|status_code|retry_after", re.IGNORECASE),
    "Qdrant": re.compile(r"qdrant|contextual_chunks", re.IGNORECASE),
}

# (module, exact word) pairs that may match, each with its reason. Anything
# else is a destination detail that belongs in sinks/, stores/ or services/.
_ALLOWED_DESTINATION_WORDS = frozenset(
    {
        # B1/B6: the blob key layout is pure string formatting kept beside
        # the chunk ids it is built from; it names no store client.
        ("constants", "MANIFEST_BLOB_KEY_TEMPLATE"),
        ("constants", "DOCUMENT_METADATA_BLOB_KEY_TEMPLATE"),
        ("constants", "DOCUMENT_CHUNK_BLOB_KEY_TEMPLATE"),
        ("constants", "PENDING_DELETIONS_BLOB_KEY_TEMPLATE"),
        ("ids", "MANIFEST_BLOB_KEY_TEMPLATE"),
        ("ids", "DOCUMENT_METADATA_BLOB_KEY_TEMPLATE"),
        ("ids", "DOCUMENT_CHUNK_BLOB_KEY_TEMPLATE"),
        ("ids", "PENDING_DELETIONS_BLOB_KEY_TEMPLATE"),
        # C2: a citation must be an http(s) URL. These are URL schemes of
        # the cited page, not a transport this service speaks.
        ("metadata", "http"),
        ("metadata", "https"),
        # B2: an opaque identity on the manifest and the run report. The
        # diff never reads it (test_diff: test_sink_id_has_no_effect...).
        ("schemas", "sink_id"),
        # B2: StoreObject's field, the one shape a store returns.
        ("schemas", "etag"),
    }
)

# Parameter-name fragments no public core function may take.
_DESTINATION_PARAMETERS = re.compile(r"sink|store|backend", re.IGNORECASE)


def _destination_words(stem: str, words: list[str]) -> list[tuple[str, str, str]]:
    """(category, module, word) for every disallowed destination word."""
    return sorted(
        {
            (category, stem, word)
            for word in words
            for category, pattern in _DESTINATION_VOCABULARY.items()
            if pattern.search(word) and (stem, word) not in _ALLOWED_DESTINATION_WORDS
        }
    )


@pytest.mark.parametrize("path", CORE_MODULES, ids=lambda p: p.stem)
def test_core_module_names_no_destination(path: Path) -> None:
    found = _destination_words(path.stem, code_words(path))
    assert not found, (
        f"exporter/core/{path.name} names a destination: {found}. classify() "
        "must give the same result for every destination; move this to "
        "sinks/, stores/ or services/, or, if it truly is not a destination "
        "detail, allowlist it here with the reason."
    )


def test_every_allowed_destination_word_is_still_used() -> None:
    """A stale allowlist entry would quietly permit the word again later."""
    used = {
        (path.stem, word)
        for path in CORE_MODULES
        for word in code_words(path)
        if any(p.search(word) for p in _DESTINATION_VOCABULARY.values())
    }
    assert set(_ALLOWED_DESTINATION_WORDS) - used == set()


def test_the_vocabulary_check_catches_a_destination_word(tmp_path: Path) -> None:
    """The check's own regression test, so it cannot pass vacuously."""
    planted = tmp_path / "diff.py"
    planted.write_text(
        '"""Prose may say llm_module and blob."""\n'
        "# so may comments: http, sink\n"
        'TARGET = "llm_module"\n'
        "def retry(status_code: int) -> int:\n"
        "    return status_code\n"
        "def _private(sink: object) -> object:\n"
        "    return dispatch(blob_key=sink)\n",
        encoding="utf-8",
    )
    found = _destination_words("diff", code_words(planted))
    assert ("sink identity", "diff", "llm_module") in found
    assert ("HTTP", "diff", "status_code") in found
    # A private function's parameter and a call-site keyword are code too.
    assert ("sink identity", "diff", "sink") in found
    assert ("store or transport", "diff", "blob_key") in found
    assert not any(word in {"blob", "http"} for _, _, word in found)
    # An allowlisted pair passes only in its own module.
    assert _destination_words("metadata", ["https"]) == []
    assert _destination_words("diff", ["https"]) == [("HTTP", "diff", "https")]


def _public_callables(module: object) -> list[tuple[str, object]]:
    found: list[tuple[str, object]] = []
    for name, value in vars(module).items():
        if name.startswith("_") or getattr(value, "__module__", None) != getattr(
            module, "__name__", None
        ):
            continue
        if inspect.isfunction(value):
            found.append((name, value))
        elif inspect.isclass(value):
            found.extend(
                (f"{name}.{attr}", member)
                for attr, member in vars(value).items()
                if not attr.startswith("_")
                and (inspect.isfunction(member) or isinstance(member, classmethod))
            )
    return found


@pytest.mark.parametrize("path", CORE_MODULES, ids=lambda p: p.stem)
def test_no_public_core_function_takes_a_destination(path: Path) -> None:
    """classify(), record() and everything else in core/ cannot be told
    which destination is configured, so they cannot act on it."""
    module = importlib.import_module(f"exporter.core.{path.stem}")
    offenders = []
    for name, value in _public_callables(module):
        target = value.__func__ if isinstance(value, classmethod) else value
        offenders.extend(
            f"{name}({parameter})"
            for parameter in inspect.signature(target).parameters  # type: ignore[arg-type]
            if _DESTINATION_PARAMETERS.search(parameter)
        )
    assert not offenders, f"exporter/core/{path.name}: {offenders}"


def test_the_signature_check_sees_functions_and_methods() -> None:
    names = {name for name, _ in _public_callables(diff)}
    assert {"classify", "record", "DiffResult.with_chunk_tails"} <= names


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
