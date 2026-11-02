"""Azure factory wiring against real SDK clients; Vault is faked, no network."""

from dataclasses import replace
from pathlib import Path

import pytest
from azure.core.credentials import AzureNamedKeyCredential, AzureSasCredential
from azure.identity import ClientSecretCredential
from pydantic import SecretStr, ValidationError

from exporter.api.config import AzureBlobSecrets, ConfigurationError, Settings
from exporter.stores import factory
from exporter.stores.azure_blob_store import AzureBlobStore
from exporter.stores.base import StoreError

ACCOUNT_URL = "https://exportaccount.blob.core.windows.net"
ACCOUNT_KEY = "c2VjcmV0LWFjY291bnQta2V5"
SAS_TOKEN = "sv=2024-11-04&sig=test-signature"
SECRETS = AzureBlobSecrets(ACCOUNT_URL, SecretStr(ACCOUNT_KEY), "account_key")


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return Settings(
        content_work_dir=str(tmp_path / "work"),
        content_external_store_backend="azure_blob",
        azure_storage_container="export-container",
        vault_secret_path="blob/connections/azure_blob/content",
        external_s3_supports_batch_delete=False,
    )


@pytest.mark.parametrize(
    ("secrets", "credential_type"),
    [
        (SECRETS, AzureNamedKeyCredential),
        (
            replace(
                SECRETS, credential_kind="sas_token", credential=SecretStr(SAS_TOKEN)
            ),
            AzureSasCredential,
        ),
        (
            replace(
                SECRETS,
                credential_kind="sas_token",
                credential=SecretStr("?" + SAS_TOKEN),
            ),
            AzureSasCredential,
        ),
        (
            replace(
                SECRETS,
                credential_kind="service_principal",
                credential=SecretStr(""),
                tenant_id="tenant-id",
                client_id="client-id",
                client_secret=SecretStr("client-secret"),
            ),
            ClientSecretCredential,
        ),
    ],
    ids=["account-key", "sas", "sas-leading-question-mark", "service-principal"],
)
def test_destination_uses_vault_credentials_without_s3_settings(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    secrets: AzureBlobSecrets,
    credential_type: type,
) -> None:
    credentials = []

    def build_azure(
        *,
        account_url: str,
        container: str,
        credential: object,
        supports_batch_delete: bool,
    ) -> AzureBlobStore:
        # The SDK converts named keys to a signing policy, so capture its input.
        credentials.append(credential)
        return AzureBlobStore(
            account_url=account_url,
            container=container,
            credential=credential,
            supports_batch_delete=supports_batch_delete,
        )

    monkeypatch.setattr(factory, "AzureBlobStore", build_azure)
    monkeypatch.setattr(factory, "get_vault_secrets", lambda settings: secrets)
    monkeypatch.setenv("AZURE_AUTHORITY_HOST", "https://authority.invalid")
    store = factory.build_destination_store(settings)

    assert isinstance(store, AzureBlobStore)
    assert store._client.url == f"{ACCOUNT_URL}/{settings.azure_storage_container}"
    assert store.capabilities.supports_batch_delete is True
    [credential] = credentials
    assert isinstance(credential, credential_type)
    if isinstance(credential, AzureNamedKeyCredential):
        assert credential.named_key.name == "exportaccount"
        assert credential.named_key.key == ACCOUNT_KEY
    elif isinstance(credential, AzureSasCredential):
        assert credential.signature == SAS_TOKEN
    else:
        assert credential._tenant_id == secrets.tenant_id
        assert credential._client_id == secrets.client_id
        assert credential._client_credential == secrets.client_secret.get_secret_value()
        assert credential._authority == "https://login.microsoftonline.com"


def test_an_invalid_tenant_id_is_a_configuration_error(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        factory,
        "get_vault_secrets",
        lambda settings: replace(
            SECRETS,
            credential_kind="service_principal",
            credential=SecretStr(""),
            tenant_id="not a tenant/id",
            client_id="client-id",
            client_secret=SecretStr("never-print-client-secret"),
        ),
    )
    with pytest.raises(ConfigurationError) as excinfo:
        factory.build_destination_store(settings)
    assert str(excinfo.value) == "Azure tenant_id from Vault is invalid"
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__


@pytest.mark.parametrize(
    "account_url", ["", "account/path", "https:///container", "https://[invalid"]
)
def test_account_key_requires_a_host_without_echoing_the_url(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, account_url: str
) -> None:
    monkeypatch.setattr(
        factory,
        "get_vault_secrets",
        lambda settings: replace(SECRETS, account_url=account_url),
    )
    with pytest.raises(ConfigurationError, match="account host") as excinfo:
        factory.build_destination_store(settings)
    assert str(excinfo.value) == "Azure account_url must have an account host"


@pytest.mark.parametrize(
    ("container", "secret_path", "missing"),
    [
        ("", "", ["AZURE_STORAGE_CONTAINER", "VAULT_SECRET_PATH"]),
        ("", "secret/path", ["AZURE_STORAGE_CONTAINER"]),
        ("container", "", ["VAULT_SECRET_PATH"]),
    ],
)
def test_destination_names_all_missing_settings_before_reading_vault(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    container: str,
    secret_path: str,
    missing: list[str],
) -> None:
    monkeypatch.setattr(
        factory,
        "get_vault_secrets",
        lambda settings: pytest.fail("Vault must not be read"),
    )
    settings = settings.model_copy(
        update={"azure_storage_container": container, "vault_secret_path": secret_path}
    )
    with pytest.raises(ConfigurationError) as excinfo:
        factory.build_destination_store(settings)
    for name in missing:
        assert name in str(excinfo.value)
    assert "EXTERNAL_S3" not in str(excinfo.value)


@pytest.mark.parametrize("explicit_manifest", [False, True])
def test_each_build_reads_rotated_vault_credentials(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, explicit_manifest: bool
) -> None:
    if explicit_manifest:
        settings = settings.model_copy(
            update={
                "manifest_store_backend": "azure_blob",
                "manifest_store_bucket": "manifests",
            }
        )
    values = iter([ACCOUNT_KEY, "cm90YXRlZC1rZXk="])
    calls = []

    def read_vault(received: Settings) -> AzureBlobSecrets:
        calls.append(received)
        return replace(SECRETS, credential=SecretStr(next(values)))

    monkeypatch.setattr(factory, "get_vault_secrets", read_vault)
    builder = (
        factory.build_manifest_store_client
        if explicit_manifest
        else factory.build_destination_store
    )
    first, second = builder(settings), builder(settings)
    assert isinstance(first, AzureBlobStore)
    assert isinstance(second, AzureBlobStore)
    assert first._client.credential.account_key == ACCOUNT_KEY
    assert second._client.credential.account_key == "cm90YXRlZC1rZXk="
    assert calls == [settings, settings]


@pytest.mark.parametrize(
    "error",
    [ConfigurationError("Vault configuration failed"), StoreError("Vault unavailable")],
)
@pytest.mark.parametrize("explicit_manifest", [False, True])
def test_vault_errors_propagate_unchanged(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    explicit_manifest: bool,
) -> None:
    if explicit_manifest:
        settings = settings.model_copy(
            update={
                "manifest_store_backend": "azure_blob",
                "manifest_store_bucket": "manifests",
            }
        )

    def read_vault(settings: Settings) -> AzureBlobSecrets:
        raise error

    monkeypatch.setattr(factory, "get_vault_secrets", read_vault)
    with pytest.raises(type(error)) as excinfo:
        factory.build_manifest_store_client(settings)
    assert excinfo.value is error


@pytest.mark.parametrize("explicit_manifest", [False, True])
def test_manifest_uses_its_resolved_container_and_vault_account(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, explicit_manifest: bool
) -> None:
    settings = settings.model_copy(
        update={
            "manifest_store_backend": "azure_blob" if explicit_manifest else "",
            "manifest_store_bucket": "manifest-container",
            "manifest_store_endpoint_url": "https://ignored.example",
            "external_s3_endpoint_url": "https://ignored-s3.example",
            "external_s3_access_key_id": SecretStr("ignored-key"),
            "external_s3_secret_access_key": SecretStr("ignored-secret"),
        }
    )
    monkeypatch.setattr(factory, "get_vault_secrets", lambda settings: SECRETS)
    store = factory.build_manifest_store_client(settings)
    assert isinstance(store, AzureBlobStore)
    container = (
        "manifest-container" if explicit_manifest else settings.azure_storage_container
    )
    assert store._client.url == f"{ACCOUNT_URL}/{container}"
    assert store._client.credential.account_key == ACCOUNT_KEY
    assert store.capabilities.supports_batch_delete is True


@pytest.mark.parametrize("bucket", ["", "manifests"])
def test_explicit_azure_manifest_requires_secret_path_at_startup(
    settings: Settings, bucket: str
) -> None:
    values = settings.model_dump() | {
        "manifest_store_backend": "azure_blob",
        "manifest_store_bucket": bucket,
        "vault_secret_path": "",
    }
    with pytest.raises(ValidationError) as excinfo:
        Settings.model_validate(values)
    message = str(excinfo.value)
    assert "VAULT_SECRET_PATH" in message
    if not bucket:
        assert "MANIFEST_STORE_BUCKET" in message


def test_connection_repr_hides_even_a_plaintext_credential() -> None:
    connection = factory.StoreConnection(
        endpoint_url=ACCOUNT_URL,
        bucket="container",
        access_key_id="",
        secret_access_key="",
        region="",
        credential="never-print-this-credential",
    )
    assert "never-print-this-credential" not in repr(connection)
    assert "credential=" not in repr(connection)
