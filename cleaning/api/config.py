import requests
from pydantic import SecretStr
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """
    Non-sensitive bootstrapping values only.
    Secrets are fetched fresh from Vault on every call to get_vault_secrets()
    so that token rotation by Vault Agent is always respected.
    """
    ruuter_internal: str
    languages: list[str] = ['est', 'rus', 'eng']

    vault_addr: str                      # e.g. http://vault:8200
    vault_secret_path: str               # e.g. llm/connections/azure_openai/cleaner
    vault_token_path: str = "/agent/out/token"

    # Defaults — overridden at call time by get_vault_secrets()
    azure_openai_api_version: str = "2024-02-01"
    azure_openai_deployment: str = "gpt-4o-mini"


# Singleton for non-secret config; secrets are never stored here
settings = Settings()


def _read_token() -> str:
    """Read the current token from the file Vault Agent maintains."""
    try:
        token = open(settings.vault_token_path).read().strip()
    except OSError as e:
        raise RuntimeError(
            f"Could not read Vault token from {settings.vault_token_path}: {e}"
        ) from e
    if not token:
        raise RuntimeError(f"Vault token file {settings.vault_token_path} is empty")
    return token


class VaultSecrets:
    """Holds secrets fetched from Vault. Not cached — fetched per task."""
    def __init__(self, api_key: SecretStr, endpoint: str, api_version: str, deployment: str):
        self.azure_openai_api_key: SecretStr = api_key
        self.azure_openai_endpoint: str = endpoint
        self.azure_openai_api_version: str = api_version
        self.azure_openai_deployment: str = deployment


def get_vault_secrets() -> VaultSecrets:
    """
    Fetch secrets from Vault using the current token.
    Called once per task so token rotation is always reflected.
    """
    token = _read_token()
    url = f"{settings.vault_addr}/v1/secret/data/{settings.vault_secret_path}"

    try:
        response = requests.get(url, headers={"X-Vault-Token": token}, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"Failed to fetch secrets from Vault at {url}: {e}") from e

    try:
        data: dict = response.json()["data"]["data"]
    except (KeyError, ValueError) as e:
        raise RuntimeError(f"Unexpected Vault response structure: {e}") from e

    # Basic validation to fail fast on misconfiguration or placeholder values
    api_key_raw = data.get("api_key")
    endpoint_raw = data.get("endpoint")

    if not isinstance(api_key_raw, str) or not api_key_raw.strip():
        raise RuntimeError("Vault secret 'api_key' is missing or empty")
    if not isinstance(endpoint_raw, str) or not endpoint_raw.strip():
        raise RuntimeError("Vault secret 'endpoint' is missing or empty")

    placeholder_marker = "REPLACE_ME"
    if placeholder_marker in api_key_raw or placeholder_marker in endpoint_raw:
        raise RuntimeError(
            "Vault secrets contain placeholder values (e.g. 'REPLACE_ME'); "
            "ensure real credentials are stored in Vault."
        )

    return VaultSecrets(
        api_key=SecretStr(api_key_raw),
        endpoint=endpoint_raw,
        api_version=data.get("api_version", settings.azure_openai_api_version),
        deployment=data.get("deployment", settings.azure_openai_deployment),
    )