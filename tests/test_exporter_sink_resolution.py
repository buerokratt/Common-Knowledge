"""A13 — one place resolves the sink, enforced rather than reviewed.

`sinks/factory.py` (D0) maps CONTENT_SINK to a class and is the only place
allowed to branch on which sink is configured. This module is that rule as an
executable check, following the precedent already in
tests/test_exporter_config.py::test_module_has_no_settings_singleton — a
cross-cutting rule that lives in a docstring survives exactly as long as the
person who wrote it.

WHY A COMPARISON CHECK AND NOT A MENTION CHECK. `GET /health` legitimately
reports `settings.content_sink` so an operator can tell which sink a
deployment runs, and Stage F will legitimately pass the value into a report
and a log line. Those are not branches. A rule that forbade *mentioning* the
sink would either fail on them or be widened until it caught nothing, so the
check is specifically about DECIDING: comparisons, match statements, and
dispatch tables keyed on a sink value.

AST rather than grep, because the shapes that matter are not textual. `if
settings.content_sink == "llm_module"` and `{"object_store": ..., }[name]` and
`match sink:` are the same violation wearing three syntaxes, and a regex that
caught all three would also catch the Literal declaration and the docstrings.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPORTER_ROOT = REPO_ROOT / "content-external" / "exporter"

# The two config axes. Both are checked, because "one place decides the
# destination" has been twice as easy to violate since the sink split added a
# second axis above the store backend.
SINK_VALUES = frozenset({"object_store", "llm_module"})
STORE_BACKEND_VALUES = frozenset({"s3", "azure_blob"})
SETTING_NAMES = frozenset({"content_sink", "content_external_store_backend"})

# Files permitted to decide. Relative to EXPORTER_ROOT, POSIX-spelled.
#
#   sinks/factory.py   — D0: the only place mapping CONTENT_SINK to a class
#   stores/factory.py  — D7: the same rule, one axis down
#   api/config.py      — declares the Literals and validates the combinations
#                        (A12's matrix is inherently a set of comparisons)
ALLOWED = frozenset(
    {
        "sinks/factory.py",
        "stores/factory.py",
        "api/config.py",
    }
)


def _python_files() -> list[Path]:
    return sorted(EXPORTER_ROOT.rglob("*.py"))


def _relative(path: Path) -> str:
    return path.relative_to(EXPORTER_ROOT).as_posix()


def _decides_on_a_destination(node: ast.expr) -> bool:
    """True if this expression is a destination value or the setting itself.

    Unwraps tuple/list/set literals so `in ("s3", "azure_blob")` is caught the
    same way `== "s3"` is.
    """
    if isinstance(node, ast.Constant):
        return node.value in SINK_VALUES or node.value in STORE_BACKEND_VALUES
    if isinstance(node, ast.Attribute):
        return node.attr in SETTING_NAMES
    if isinstance(node, ast.Name):
        return node.id in SETTING_NAMES
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        return any(_decides_on_a_destination(element) for element in node.elts)
    return False


def _violations(path: Path) -> list[str]:
    """Every line in `path` that decides on a destination."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    label = _relative(path)

    for node in ast.walk(tree):
        # `x == "llm_module"`, `settings.content_sink != ...`, `x in (...)`
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            if any(_decides_on_a_destination(operand) for operand in operands):
                found.append(f"{label}:{node.lineno} comparison on a destination")

        # `match settings.content_sink:` — a comparison with different syntax.
        elif isinstance(node, ast.Match):
            if _decides_on_a_destination(node.subject):
                found.append(f"{label}:{node.lineno} match on a destination")

        # `{"object_store": A, "llm_module": B}[name]` — a dispatch table,
        # which is a factory. Legitimate in a factory, nowhere else.
        elif isinstance(node, ast.Dict):
            keys = [key for key in node.keys if key is not None]
            if any(_decides_on_a_destination(key) for key in keys):
                found.append(f"{label}:{node.lineno} dict keyed on a destination")

    return found


def test_the_exporter_package_is_present() -> None:
    """Guards the guard: a moved package would make every check below pass by
    scanning nothing at all."""
    assert EXPORTER_ROOT.is_dir()
    assert len(_python_files()) > 0


@pytest.mark.parametrize("path", _python_files(), ids=_relative)
def test_only_a_factory_decides_the_destination(path: Path) -> None:
    """No module outside the two factories may branch on CONTENT_SINK or
    CONTENT_EXTERNAL_STORE_BACKEND.

    Parametrised per file so the failure names the offending module, and so a
    new module lands in this gate without anyone editing this test.
    """
    if _relative(path) in ALLOWED:
        pytest.skip("allowed to decide — see ALLOWED")

    violations = _violations(path)
    assert not violations, (
        "A13: only sinks/factory.py may resolve the sink (D0) and only "
        "stores/factory.py the store backend (D7). Found:\n  "
        + "\n  ".join(violations)
        + "\n\nStage F holds a ContentSink it cannot interrogate — that is "
        "what lets one sink meet the F3 ordering guarantee by write order and "
        "the other by sending one request, without the coordinator knowing "
        "which. Move the decision into the factory and give the sink a "
        "capability or a method instead."
    )


def test_the_allowlist_names_only_factories_and_config() -> None:
    """The allowlist is the whole strength of this check, so widening it is a
    visible act rather than a quiet one."""
    assert ALLOWED == {"sinks/factory.py", "stores/factory.py", "api/config.py"}


def test_reporting_the_configured_sink_is_not_a_violation() -> None:
    """The check must permit reading the value, or it would forbid /health
    reporting which sink a deployment runs — which the README promises and an
    operator needs in order to read a reconcile report correctly."""
    tree = ast.parse("def health(s):\n    return {'sink': s.content_sink}\n")
    compares = [n for n in ast.walk(tree) if isinstance(n, ast.Compare)]
    dicts = [n for n in ast.walk(tree) if isinstance(n, ast.Dict)]

    assert not compares
    assert dicts and not any(
        _decides_on_a_destination(key)
        for node in dicts
        for key in node.keys
        if key is not None
    )


def test_a_branch_on_the_sink_would_be_caught(tmp_path: Path) -> None:
    """The check's own regression test. Without this, a refactor that broke
    _violations would leave every file above passing vacuously."""
    offender = tmp_path / "leaky.py"
    offender.write_text(
        "def publish(settings):\n"
        "    if settings.content_sink == 'llm_module':\n"
        "        return 1\n"
        "    return 2\n",
        encoding="utf-8",
    )
    tree = ast.parse(offender.read_text(encoding="utf-8"))
    compares = [n for n in ast.walk(tree) if isinstance(n, ast.Compare)]

    assert compares
    assert any(
        _decides_on_a_destination(operand)
        for node in compares
        for operand in [node.left, *node.comparators]
    )
