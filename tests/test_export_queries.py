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


DOCUMENT_COLUMNS = [
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
    "type",
    "updated_at",
]


def _statement(relative: str) -> tuple[str, str]:
    """The declaration header, and the SQL with comments and whitespace
    collapsed."""
    header, body = (RESQL_DIR / relative).read_text(encoding="utf-8").split("*/", 1)
    lines = [line for line in body.splitlines() if not line.lstrip().startswith("--")]
    return header, " ".join(" ".join(lines).split())


def test_document_query_pages_latest_versions_without_filtering_signals() -> None:
    header, sql = _statement("ckb/GET/source_file/list_agency_export_documents.sql")
    assert "method: get" in header
    assert "field: after_base_id" in header
    assert "field: page_size" in header
    page = sql.removeprefix("WITH page AS ( ").split(" ) SELECT ", 1)[0]
    assert page.startswith("SELECT DISTINCT ON (base_id) ")
    # Paged on the latest version per base_id, and nothing else: deleted,
    # excluded and unfinished rows are signals the diff needs.
    assert page.split("FROM", 1)[1] == (
        " data_collection.source_file"
        " WHERE agency_base_id = :agency_base_id::UUID"
        " AND base_id > :after_base_id::UUID"
        " ORDER BY base_id, updated_at DESC LIMIT :page_size::INTEGER"
    )
    columns = page.split("FROM", 1)[0].removeprefix("SELECT DISTINCT ON (base_id) ")
    assert [column.strip() for column in columns.split(",")] == DOCUMENT_COLUMNS
    outer = sql.split(" ) SELECT ", 1)[1].split(" FROM page AS p ", 1)[0]
    # Split on top-level commas only, not the one inside COALESCE(...).
    assert [column.strip() for column in re.split(r",(?![^()]*\))", outer)] == [
        *(f"p.{column}" for column in DOCUMENT_COLUMNS[:-1]),
        "COALESCE(ls.is_deleted, FALSE) AS source_is_deleted",
        "p.updated_at",
    ]
    # The source's latest version, not any deleted one: a LEFT JOIN, so a file
    # never disappears from the rows because of its source.
    assert (
        "LEFT JOIN LATERAL ( SELECT s.is_deleted FROM data_collection.source AS s"
        " WHERE s.base_id = p.source_base_id ORDER BY s.updated_at DESC LIMIT 1"
        " ) AS ls ON TRUE"
    ) in sql
    assert sql.endswith(" ORDER BY p.base_id;")


def test_watermark_moves_on_file_versions_and_source_deletions() -> None:
    _, sql = _statement("ckb/GET/agency/get_agency_export_watermark.sql")
    # Every file version, deleted ones included: a soft delete moves it.
    assert (
        "SELECT MAX(sf.updated_at) FROM data_collection.source_file AS sf"
        " WHERE sf.agency_base_id = :agency_base_id::UUID )"
    ) in sql
    # Deleted source versions only: routine source rewrites must not.
    assert (
        "SELECT MAX(s.updated_at) FROM data_collection.source AS s"
        " WHERE s.agency_base_id = :agency_base_id::UUID AND s.is_deleted = TRUE )"
    ) in sql
    assert sql.count("GREATEST(") == 1
    assert "lf.is_deleted = FALSE AND ls.is_deleted IS NOT TRUE" in sql
