"""F21 — the marker that lets a bootstrap which died before its first
checkpoint resume, instead of being refused as a populated destination.
The coordinator's use of it is in tests/test_publish_service.py."""

import json
from pathlib import Path

import pytest

from exporter.services.bootstrap_marker import BOOTSTRAP_DIRNAME, BootstrapMarker


def _marker(tmp_path: Path, *, sink_id: str = "object_store") -> BootstrapMarker:
    return BootstrapMarker(tmp_path, agency_id="agency-1", sink_id=sink_id)


def test_absent_until_created_and_gone_once_cleared(tmp_path: Path) -> None:
    marker = _marker(tmp_path)
    assert marker.present() is False

    marker.create("run-1")

    assert marker.present() is True
    assert marker.path == tmp_path / BOOTSTRAP_DIRNAME / "agency-1.inprogress"
    found = marker.read()
    assert found is not None
    assert found["run_id"] == "run-1"
    assert found["sink_id"] == "object_store"
    assert found["created_at"]

    marker.clear()
    assert marker.present() is False


def test_clearing_an_absent_marker_is_not_an_error(tmp_path: Path) -> None:
    _marker(tmp_path).clear()


def test_a_marker_for_another_sink_vouches_for_nothing(tmp_path: Path) -> None:
    """Switching CONTENT_SINK mid-bootstrap: the content the marker speaks
    for is in the other destination."""
    _marker(tmp_path, sink_id="object_store").create("run-1")
    assert _marker(tmp_path, sink_id="llm_module").present() is False


@pytest.mark.parametrize("payload", ["not json", "[]", json.dumps({"sink_id": 1})])
def test_an_unreadable_marker_is_never_trusted(tmp_path: Path, payload: str) -> None:
    marker = _marker(tmp_path)
    marker.path.parent.mkdir(parents=True)
    marker.path.write_text(payload, encoding="utf-8")
    assert marker.present() is False


def test_an_agency_id_that_escapes_its_folder_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        BootstrapMarker(tmp_path, agency_id="../x", sink_id="object_store")
