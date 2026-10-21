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


def test_document_query_pages_latest_versions_without_filtering_signals() -> None:
    path = RESQL_DIR / "ckb/GET/source_file/list_agency_export_documents.sql"
    header, body = path.read_text(encoding="utf-8").split("*/", 1)
    assert "method: get" in header
    assert "field: after_base_id" in header
    assert "field: page_size" in header
    sql = " ".join(body.split())
    assert sql.startswith("SELECT DISTINCT ON (base_id) ")
    assert sql.split("FROM", 1)[1] == (
        " data_collection.source_file"
        " WHERE agency_base_id = :agency_base_id::UUID"
        " AND base_id > :after_base_id::UUID"
        " ORDER BY base_id, updated_at DESC LIMIT :page_size::INTEGER;"
    )
    columns = sql.split("FROM", 1)[0].removeprefix("SELECT DISTINCT ON (base_id) ")
    assert [column.strip() for column in columns.split(",")] == [
        "base_id",
        "source_base_id",
        "url",
        "page_title",
        "subsector",
        "status",
        "cleaned_data_url",
        "cleaned_metadata_url",
        "edited_data_url",
        "edited_metadata_url",
        "file_size",
        "is_excluded",
        "is_deleted",
        "updated_at",
    ]
