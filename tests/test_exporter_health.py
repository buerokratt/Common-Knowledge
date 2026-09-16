"""Tests for content-external's GET /health and its fail-fast startup.

Every test uses `with TestClient(app)`. Without the context manager the ASGI
lifespan never runs, app.state.settings is never set, and /health 500s — so
the `with` is load-bearing, not style.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from exporter.api.app import app, get_settings
from exporter.api.config import ConfigurationError, Settings

HEALTH_KEYS = {
    "status",
    "sink",
    "store_backend",
    "chunk_profile",
    "work_dir",
    "last_run",
}


@pytest.fixture
def work_dir_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """The lifespan really does mkdir and a write probe, so point it at a
    temp dir — the configured /var/lib/content-external is not creatable on a
    CI runner or a developer machine."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    work_dir = tmp_path / "work"
    monkeypatch.setenv("CONTENT_WORK_DIR", work_dir.as_posix())
    return work_dir


def test_health_returns_ok_and_the_configured_identity(work_dir_env: Path) -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == HEALTH_KEYS
    assert body["status"] == "ok"
    assert body["sink"] == "object_store"
    assert body["store_backend"] == "s3"
    assert body["chunk_profile"] == "azure_native"
    assert body["work_dir"] == work_dir_env.as_posix()


def test_health_reports_the_configured_sink(
    work_dir_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """content-external/README.md tells an operator to check GET /health for
    which sink this deployment runs. That promise, made executable.

    The four extra variables are not noise: A12's matrix refuses
    CONTENT_SINK=llm_module without a manifest store and without a base URL
    and credential path, so this is the minimum viable llm_module deployment.
    Asserting the whole set here means a future relaxation of the matrix shows
    up as this test passing with fewer of them, rather than silently.
    """
    monkeypatch.setenv("CONTENT_SINK", "llm_module")
    monkeypatch.setenv("MANIFEST_STORE_BACKEND", "s3")
    monkeypatch.setenv("MANIFEST_STORE_ENDPOINT_URL", "https://store.example")
    monkeypatch.setenv("MANIFEST_STORE_BUCKET", "content-manifests")
    monkeypatch.setenv("LLM_MODULE_BASE_URL", "https://llm.example/ingest")
    monkeypatch.setenv("LLM_MODULE_VAULT_SECRET_PATH", "llm/connections/ingest")

    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["sink"] == "llm_module"


def test_health_last_run_is_null_before_any_run(work_dir_env: Path) -> None:
    """The A15 contract anchor: if A15 renames this field or makes it
    non-optional, this fails."""
    with TestClient(app) as client:
        assert client.get("/health").json()["last_run"] is None


def test_startup_fails_when_work_dir_violates_rule_one(
    work_dir_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-fast proved at the ASGI layer: the app does not come up at all,
    rather than serving an unhealthy /health."""
    monkeypatch.setenv("CONTENT_WORK_DIR", "/scrapped-data/work")
    with pytest.raises(ConfigurationError) as excinfo, TestClient(app):
        pass
    assert "rule 1" in str(excinfo.value)


def test_startup_fails_when_work_dir_is_missing(
    work_dir_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CONTENT_WORK_DIR")
    with pytest.raises(ConfigurationError) as excinfo, TestClient(app):
        pass
    assert "content_work_dir" in str(excinfo.value)


def test_startup_fails_when_work_dir_is_unwritable(
    work_dir_env: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    blocker = tmp_path / "afile"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("CONTENT_WORK_DIR", blocker.as_posix())

    with pytest.raises(ConfigurationError), TestClient(app):
        pass


def test_health_uses_injected_settings(work_dir_env: Path) -> None:
    """Proves the injection rule is real — and gives later stages the pattern
    for testing a service without touching the environment."""
    override = Settings(
        content_work_dir=work_dir_env.as_posix(),
        chunk_profile="compact",
    )  # pyright: ignore[reportCallIssue]
    app.dependency_overrides[get_settings] = lambda: override
    try:
        with TestClient(app) as client:
            assert client.get("/health").json()["chunk_profile"] == "compact"
    finally:
        app.dependency_overrides.clear()
