import re
from pathlib import Path

import pytest

RESQL_DIR = Path(__file__).resolve().parents[1] / "DSL" / "Resql"
EXPORT_FILES = sorted(path for path in RESQL_DIR.rglob("*export*") if path.is_file())


def test_export_queries_exist() -> None:
    assert len(EXPORT_FILES) >= 2


@pytest.mark.parametrize("path", EXPORT_FILES, ids=lambda path: path.name)
def test_export_queries_are_read_only(path: Path) -> None:
    assert not re.search(
        r"\b(insert|update|delete|copy_row_with_modifications)\b",
        path.read_text(encoding="utf-8"),
        re.IGNORECASE,
    ), f"{path.relative_to(RESQL_DIR)} contains a forbidden write operation"
