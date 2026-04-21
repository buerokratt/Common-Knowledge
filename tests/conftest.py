"""
conftest.py — Test container lifecycle and shared fixtures for the cleaning service.

Stack started (in order):
  1. vault-test    (dev mode, root token = "root", port 18200)
  2. mock-ruuter   (HTTP stub, port 18089)
  3. [conftest bootstraps Vault: AppRole, policy, secrets]
  4. [conftest creates a scoped service token and writes it to
     ./test-vault/agent-out/token — no vault-agent container needed]
  5. cleaning-server-test (reads token from bind-mount, port 18001)

Skipping vault-agent entirely eliminates the file-permission issues that
arise when a container running as uid=100 (vault) writes files that the
host user then needs to read. conftest uses the Vault HTTP API directly
via hvac and writes the token as the host user.

Required environment variables (set in GitHub Secrets or a local .env):
  AZURE_OPENAI_API_KEY        real Azure OpenAI key
  AZURE_OPENAI_ENDPOINT       e.g. https://my-resource.openai.azure.com/
  AZURE_OPENAI_DEPLOYMENT     deployment name (default: gpt-4o-mini)
  AZURE_OPENAI_API_VERSION    API version   (default: 2024-02-01)

If credentials are absent the stack still starts; LLM tests skip via
the `require_llm_creds` fixture.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Generator

import hvac
import pytest
import requests
from loguru import logger


# ---------------------------------------------------------------------------
# Early environment setup — runs before any module is imported/collected
# ---------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    """
    Set required env vars before pytest collects any modules.
    'api/config.py' instantiates Settings() at module level, which requires
    RUUTER_INTERNAL.  Without this hook every test_tasks.py import fails with
    a ValidationError before any fixture has a chance to run.
    The value is a placeholder — unit tests mock all outbound HTTP calls, and
    integration tests override it via the real docker-compose env vars.
    """
    os.environ.setdefault("RUUTER_INTERNAL", "http://localhost:8089")


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent
COMPOSE_FILE = PROJECT_ROOT / "docker-compose-test.yml"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Token directory is bind-mounted as /agent/out inside cleaning-server-test.
# conftest writes the token here directly (no vault-agent container).
TEST_VAULT_TOKEN_DIR = PROJECT_ROOT / "test-vault" / "agent-out"

# Scrapped data bind-mounted as /scrapped-data inside cleaning-server-test
TEST_SCRAPPED_DIR = PROJECT_ROOT / "test-scrapped-data"

# Host-side URLs (mapped ports from docker-compose-test.yml)
VAULT_HOST_URL = "http://localhost:18200"
VAULT_ROOT_TOKEN = "root"
CLEANING_HOST_URL = "http://localhost:18001"
MOCK_RUUTER_HOST_URL = "http://localhost:18089"

# Vault KV path that cleaning-server reads (matches VAULT_SECRET_PATH env var)
VAULT_SECRET_PATH = "llm/connections/azure_openai/cleaner"


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _wait_http(url: str, timeout: int = 60, interval: float = 2.0) -> None:
    """Poll `url` with GET until status < 500, or raise TimeoutError."""
    deadline = time.time() + timeout
    last_exc: Exception | None = None
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=3)
            if r.status_code < 500:
                return
        except Exception as exc:
            last_exc = exc
        time.sleep(interval)
    raise TimeoutError(
        f"Service at {url} did not become available within {timeout}s. "
        f"Last error: {last_exc}"
    )


def _run(
    cmd: list[str], cwd: Path | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd or PROJECT_ROOT),
        check=check,
        capture_output=True,
        text=True,
    )


def _bootstrap_vault(
    azure_api_key: str,
    azure_endpoint: str,
    azure_deployment: str,
    azure_api_version: str,
) -> str:
    """
    Configure the Vault dev instance and return a scoped service token.

    Steps:
      1. Enable AppRole auth
      2. Create cleaner-policy (read-only on LLM secrets)
      3. Create a long-lived service token with that policy
      4. Write Azure OpenAI secret (real or placeholder)

    Returns the service token string so conftest can write it to disk.
    """
    logger.info("Bootstrapping Vault...")
    client = hvac.Client(url=VAULT_HOST_URL, token=VAULT_ROOT_TOKEN)

    # Enable AppRole (not strictly needed since we use a direct token, but
    # keeps the setup consistent with production)
    enabled_methods = client.sys.list_auth_methods()
    if "approle/" not in enabled_methods:
        client.sys.enable_auth_method("approle")
        logger.info("AppRole auth method enabled")

    # Scoped policy
    policy = """
path "secret/data/llm/connections/*"     { capabilities = ["read", "list"] }
path "secret/metadata/llm/connections/*" { capabilities = ["read", "list"] }
path "auth/token/lookup-self"            { capabilities = ["read"] }
"""
    client.sys.create_or_update_policy("cleaner-policy", policy)
    logger.info("Policy 'cleaner-policy' written")

    # Create a long-lived service token scoped to cleaner-policy.
    # TTL of 24h is more than enough for any test session.
    token_response = client.auth.token.create(
        policies=["cleaner-policy"],
        ttl="24h",
        renewable=False,
        no_default_policy=True,
    )
    service_token: str = token_response["auth"]["client_token"]
    logger.info("Service token created with cleaner-policy")

    # Write Azure secrets
    if azure_api_key and azure_endpoint:
        client.secrets.kv.v2.create_or_update_secret(
            mount_point="secret",
            path=VAULT_SECRET_PATH,
            secret={
                "api_key": azure_api_key,
                "endpoint": azure_endpoint,
                "deployment": azure_deployment,
                "api_version": azure_api_version,
            },
        )
        logger.info(f"Azure OpenAI secret written to secret/data/{VAULT_SECRET_PATH}")
    else:
        client.secrets.kv.v2.create_or_update_secret(
            mount_point="secret",
            path=VAULT_SECRET_PATH,
            secret={
                "api_key": "REPLACE_ME",
                "endpoint": "https://REPLACE_ME.openai.azure.com/",
                "deployment": "gpt-4o-mini",
                "api_version": "2024-02-01",
            },
        )
        logger.warning(
            "Azure OpenAI credentials not set — placeholder secret written. "
            "LLM tests will be skipped."
        )

    return service_token


# ---------------------------------------------------------------------------
# Stack manager
# ---------------------------------------------------------------------------


class CleaningTestStack:
    """Manages the Docker Compose test stack lifecycle for the test session."""

    def __init__(self) -> None:
        if not COMPOSE_FILE.exists():
            raise FileNotFoundError(f"Test compose file not found: {COMPOSE_FILE}")

    def start(
        self,
        azure_api_key: str,
        azure_endpoint: str,
        azure_deployment: str,
        azure_api_version: str,
    ) -> None:
        logger.info("Starting cleaning test stack...")

        # Tear down any containers that may be running from a previous session or
        # a manual `docker compose up`.  This MUST happen before we delete and
        # recreate the bind-mount directories below: Docker captures the host
        # directory inode at container-start time, so deleting a directory while
        # a container is still running leaves the container pointing at a stale
        # inode.  Files written to the newly-created host directory are then
        # invisible inside the container, causing FilePath validation to fail (422).
        logger.info("Tearing down any pre-existing containers...")
        _run(
            ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "--remove-orphans"],
            check=False,
        )

        # Now safe to delete/recreate bind-mount directories (all containers stopped)
        for d in (TEST_VAULT_TOKEN_DIR, TEST_SCRAPPED_DIR):
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True, exist_ok=True)

        # Step 1: Start Vault and mock-ruuter
        logger.info("Starting vault-test and mock-ruuter...")
        _run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE_FILE),
                "up",
                "-d",
                "vault-test",
                "mock-ruuter",
            ]
        )

        logger.info("Waiting for Vault dev instance...")
        _wait_http(f"{VAULT_HOST_URL}/v1/sys/health", timeout=30)

        # Step 2: Bootstrap Vault and get a scoped token back
        service_token = _bootstrap_vault(
            azure_api_key, azure_endpoint, azure_deployment, azure_api_version
        )

        # Step 3: Write the token to the bind-mount directory.
        # The cleaning server reads it at VAULT_TOKEN_PATH=/agent/out/token.
        token_file = TEST_VAULT_TOKEN_DIR / "token"
        token_file.write_text(service_token)
        token_file.chmod(0o644)
        logger.info(f"Service token written to {token_file}")

        # Step 4: Start the cleaning server
        logger.info("Starting cleaning-server-test...")
        _run(
            [
                "docker",
                "compose",
                "-f",
                str(COMPOSE_FILE),
                "up",
                "-d",
                "cleaning-server-test",
            ]
        )

        logger.info("Waiting for cleaning-server-test to become healthy...")
        _wait_http(f"{CLEANING_HOST_URL}/docs", timeout=120)

        logger.info("Cleaning test stack is fully ready")

    def stop(self) -> None:
        logger.info("Stopping cleaning test stack...")
        try:
            _run(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(COMPOSE_FILE),
                    "down",
                    "--remove-orphans",
                ]
            )
        except Exception as exc:
            logger.warning(f"docker compose down failed: {exc}")

    def capture_logs(self) -> None:
        for service in [
            "cleaning-server-test",
            "vault-test",
            "mock-ruuter",
        ]:
            try:
                result = _run(
                    [
                        "docker",
                        "compose",
                        "-f",
                        str(COMPOSE_FILE),
                        "logs",
                        "--tail",
                        "100",
                        service,
                    ],
                    check=False,
                )
                logger.info(
                    f"\n{'=' * 60}\nLOGS: {service}\n{'=' * 60}\n{result.stdout}"
                )
                if result.stderr:
                    logger.warning(result.stderr)
            except Exception as exc:
                logger.warning(f"Could not capture logs for {service}: {exc}")


# ---------------------------------------------------------------------------
# Session-scoped stack fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def cleaning_stack() -> Generator[CleaningTestStack, None, None]:
    """
    Start the full Docker Compose stack once per test session and tear it
    down after all tests complete. Logs are always captured on exit.
    """
    api_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-mini")
    api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01")

    stack = CleaningTestStack()
    try:
        stack.start(api_key, endpoint, deployment, api_version)
        yield stack
    except Exception as exc:
        logger.error(f"Stack startup failed: {exc}")
        stack.capture_logs()
        raise
    finally:
        stack.capture_logs()
        stack.stop()
        for path in (TEST_SCRAPPED_DIR, TEST_VAULT_TOKEN_DIR):
            if path.exists():
                shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Derived session fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def cleaning_url(cleaning_stack: CleaningTestStack) -> str:
    return CLEANING_HOST_URL


@pytest.fixture(scope="session")
def mock_ruuter_url(cleaning_stack: CleaningTestStack) -> str:
    return MOCK_RUUTER_HOST_URL


@pytest.fixture(scope="session")
def vault_client(cleaning_stack: CleaningTestStack) -> hvac.Client:
    """Direct Vault client (root token) for test-side secret inspection."""
    return hvac.Client(url=VAULT_HOST_URL, token=VAULT_ROOT_TOKEN)


# ---------------------------------------------------------------------------
# Function-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def require_llm_creds() -> None:
    """Skip the calling test if Azure OpenAI credentials are not set."""
    if not os.environ.get("AZURE_OPENAI_API_KEY") or not os.environ.get(
        "AZURE_OPENAI_ENDPOINT"
    ):
        pytest.skip("Azure OpenAI credentials not set — skipping LLM test")


@pytest.fixture
def scrapped_dir(cleaning_stack: CleaningTestStack) -> Generator[Path, None, None]:
    """
    A fresh temporary directory under test-scrapped-data/ for a single test.
    Cleaned up after the test regardless of outcome.
    """
    test_dir = TEST_SCRAPPED_DIR / f"test-{time.time_ns()}"
    test_dir.mkdir(parents=True, exist_ok=True)
    yield test_dir
    if test_dir.exists():
        shutil.rmtree(test_dir, ignore_errors=True)


@pytest.fixture
def reset_mock_ruuter(mock_ruuter_url: str) -> None:
    """Reset the mock Ruuter call log before a test."""
    requests.get(f"{mock_ruuter_url}/reset", timeout=5)


# ---------------------------------------------------------------------------
# Payload / file helpers (imported by test_api.py and test_integration.py)
# ---------------------------------------------------------------------------


def make_entity_payload(
    file_path: Path,
    meta_path: Path,
    directory: Path,
    url: str = "https://example.com/test",
    use_llm: bool = False,
    use_llm_correction: bool = False,
) -> dict[str, str | bool]:
    """
    Build a valid EntityToClean JSON payload.
    Paths are translated from host-side (under TEST_SCRAPPED_DIR) to
    container-side (/scrapped-data/...) using the bind-mount mapping.
    """

    def to_container(host_path: Path) -> str:
        rel = host_path.relative_to(TEST_SCRAPPED_DIR)
        return f"/scrapped-data/{rel}"

    return {
        "file_path": to_container(file_path),
        "meta_data_path": to_container(meta_path),
        "directory_path": to_container(directory),
        "source_file_id": "test-file-id-001",
        "source_base_id": "test-source-id-001",
        "agency_base_id": "test-agency-id-001",
        "source_run_report_base_id": "test-report-id-001",
        "url": url,
        "logs_path": to_container(directory / "test.log"),
        "use_llm": use_llm,
        "use_llm_correction": use_llm_correction,
    }


def write_test_file(
    directory: Path,
    content: str,
    filename: str,
    file_type: str,
    url: str = "https://example.com/test",
) -> tuple[Path, Path]:
    """
    Write a source file and its companion .meta.json into `directory`.
    Also touches test.log so FilePath validation in EntityToClean passes.
    Returns (file_path, meta_path).
    """
    directory.mkdir(parents=True, exist_ok=True)

    file_path = directory / filename
    file_path.write_text(content, encoding="utf-8")

    meta_path = directory / f"{filename}.meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "file_type": file_type,
                "url": url,
                "metadata": {"cleaned": False},
            }
        ),
        encoding="utf-8",
    )

    (directory / "test.log").touch()

    return file_path, meta_path
