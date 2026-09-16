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
from exporter.services.agency_guard import MultipleAgenciesError
from exporter.services.run_state import LAST_RUN_FILENAME, RunStateStore

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
    """The lifespan really does mkdir and a lock probe, so point it at a temp
    dir — the configured /var/lib/content-external is not creatable on a CI
    runner or a developer machine.

    It also stubs A17's agency count. The lifespan calls Resql to assert N=1,
    and while that check is boot-tolerant — an unreachable Resql warns and
    serves — letting every test here attempt a real connection would make the
    suite depend on DNS behaviour and pay a timeout for it. The two tests that
    care about the check drive it explicitly.
    """
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.setattr(
        "exporter.api.app.check_single_agency_at_startup", lambda _settings: 1
    )
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
    """Null is the honest answer: an hourly job that has never run is not the
    same as one that ran and succeeded."""
    with TestClient(app) as client:
        assert client.get("/health").json()["last_run"] is None


def test_health_reports_a_recorded_run(work_dir_env: Path) -> None:
    """A15's headline. An hourly job whose health endpoint cannot say when it
    last succeeded is not observable."""
    RunStateStore(work_dir_env).record(
        outcome="success",
        run_id="r-1",
        agency_id="agency-1",
        duration_seconds=42.5,
        finished_at="2026-09-16T17:04:11Z",
    )

    with TestClient(app) as client:
        last_run = client.get("/health").json()["last_run"]

    assert last_run["outcome"] == "success"
    assert last_run["run_id"] == "r-1"
    assert last_run["agency_id"] == "agency-1"
    assert last_run["finished_at"] == "2026-09-16T17:04:11Z"
    assert last_run["duration_seconds"] == 42.5


def test_health_stays_200_after_a_failed_run(work_dir_env: Path) -> None:
    """Load-bearing, and the reason last_run exists at all.

    A failed run must NOT become a non-200: both probes point at /health, so a
    503 would restart the pod — which fixes neither a wrong base URL nor a
    rotated credential, and the resulting CrashLoopBackOff would hide the one
    log line that says why. The failure is visible in last_run.outcome; the
    status code is about whether the process is serving.
    """
    RunStateStore(work_dir_env).record(
        outcome="failed", run_id="r-2", agency_id="agency-1", duration_seconds=3.0
    )

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["last_run"]["outcome"] == "failed"


def test_health_reports_the_consecutive_counters(work_dir_env: Path) -> None:
    """The agreed alert threshold is two consecutive failures, so the count
    has to be readable without diffing two probe responses."""
    store = RunStateStore(work_dir_env)
    for _ in range(3):
        store.record(
            outcome="failed", run_id="r", agency_id="agency-1", duration_seconds=1.0
        )

    with TestClient(app) as client:
        last_run = client.get("/health").json()["last_run"]

    assert last_run["consecutive_failures"] == 3
    assert last_run["consecutive_busy"] == 0


def test_health_survives_a_corrupt_last_run_record(work_dir_env: Path) -> None:
    """/health must never 500 over its own bookkeeping — that would take a
    working service out of a load balancer for an observability detail."""
    work_dir_env.mkdir(parents=True, exist_ok=True)
    (work_dir_env / LAST_RUN_FILENAME).write_text("{ not json", encoding="utf-8")

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["last_run"] is None


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


def test_startup_refuses_when_more_than_one_agency_exists(
    work_dir_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A17 at the ASGI layer: the app does not come up, rather than coming up
    and silently exporting only whichever agency wins the lock each hour."""

    def two_agencies(_settings: Settings) -> int:
        raise MultipleAgenciesError("2 agencies exist")

    monkeypatch.setattr("exporter.api.app.check_single_agency_at_startup", two_agencies)

    with pytest.raises(ConfigurationError) as excinfo, TestClient(app):
        pass
    assert "2 agencies" in str(excinfo.value)


def test_startup_survives_an_unreachable_resql(
    work_dir_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of A17's asymmetry: the agency check is the only startup
    check that tolerates its own failure, because Resql may still be starting
    and this service has no depends_on for it."""
    monkeypatch.setattr(
        "exporter.api.app.check_single_agency_at_startup", lambda _settings: None
    )

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


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
