"""Unit tests for content-external's settings, rule 1, and config redaction.

Pure: no Docker, no compose stack, no network. tests/conftest.py has no
autouse fixtures, so nothing here starts the cleaning stack.
"""

import logging
import os
import re
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from exporter.api import config as config_module
from exporter.api.config import (
    CONFIG_LOG_PREFIX,
    ConfigurationError,
    Settings,
    assert_work_dir_usable,
    load_settings,
    log_redacted_config,
    missing_destination_settings,
    redacted_settings,
    warn_on_unconfigured_destination,
)
from exporter.core.constants import (
    CHUNK_PROFILES,
    DEFAULT_CHUNK_PROFILE,
    MAX_CHUNK_ORDINAL,
)
from exporter.core.ids import chunker_fingerprint
from exporter.sinks.base import CredentialUnavailableError
from exporter.sinks.llm_module_contract import PROPOSED_TERMS

WORK_DIR_ENV = "CONTENT_WORK_DIR"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every variable Settings reads.

    Derived from the model itself, so a field added by A12, H or L is covered
    without editing this fixture. It is needed because a developer shell may
    carry AWS_* credentials or a CKB_* endpoint.
    """
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def _settings(
    work_dir: str = "/var/lib/content-external", **kwargs: object
) -> Settings:
    return Settings(content_work_dir=work_dir, **kwargs)  # pyright: ignore[reportArgumentType]


# --------------------------------------------------------------------------
# Defaults and requiredness
# --------------------------------------------------------------------------


def test_defaults_match_the_documented_env_block(clean_env: None) -> None:
    """Keeps content-external-pipeline.md's env block and this code one
    artifact. A default changed in one place and not the other fails here."""
    settings = _settings()

    assert settings.content_sink == "object_store"
    assert settings.content_external_store_backend == "s3"
    assert settings.content_external_prefix == "content"
    assert settings.sink_failure_abort_threshold == 25
    assert settings.manifest_store_backend == ""
    assert settings.manifest_store_prefix == "content-manifests"
    assert settings.llm_module_ingest_schema_version == 1
    assert settings.llm_module_max_request_bytes == 8_388_608
    assert settings.llm_module_connect_timeout == 5.0
    assert settings.llm_module_read_timeout == 120.0
    assert settings.chunk_profile == "azure_native"
    assert settings.require_metadata_sidecar is True
    assert settings.diff_on_missing_manifest == "first_run"
    assert settings.export_concurrency == 4
    assert settings.bootstrap_checkpoint_every == 250
    assert settings.max_document_bytes == 20_971_520
    assert settings.max_chunks_per_document == 5_000
    assert settings.ckb_query_timeout == 35.0
    assert settings.ckb_s3_connect_timeout == 5.0
    assert settings.ckb_s3_read_timeout == 60.0
    # No endpoint is a default in code: they come from constants.ini, read by
    # load_settings() (see the constants.ini tests below).
    assert settings.ckb_ruuter_internal == ""
    assert settings.ckb_vault == ""
    assert "ckb_resql" not in Settings.model_fields
    assert settings.vault_token_path == "/agent/out/token"
    assert settings.vault_secret_path == ""
    assert settings.azure_storage_container == ""
    assert settings.external_s3_region == "us-east-1"
    assert settings.external_s3_supports_batch_delete is True


def test_the_destination_store_is_not_required_at_startup(clean_env: None) -> None:
    """EXTERNAL_S3_* is refused by stores/factory.py when a run builds the
    store, not here: /health must come up before the destination is set."""
    settings = _settings()
    assert settings.external_s3_endpoint_url == ""
    assert settings.external_s3_access_key_id.get_secret_value() == ""


def test_work_dir_is_required(clean_env: None) -> None:
    """No code default, on purpose: a fallback is what lets a deployment that
    forgot to set it write to a path that is not the mounted volume."""
    with pytest.raises(ValidationError) as excinfo:
        Settings()  # pyright: ignore[reportCallIssue]
    assert "content_work_dir" in str(excinfo.value)


def test_env_var_overrides_the_default(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(WORK_DIR_ENV, "/work")
    monkeypatch.setenv("EXPORT_CONCURRENCY", "8")
    monkeypatch.setenv("EXPORT_PAGE_SIZE", "1")
    settings = load_settings()
    assert settings.content_work_dir == "/work"
    assert settings.export_concurrency == 8
    assert settings.export_page_size == 1


def test_nothing_but_the_work_dir_is_required(clean_env: None) -> None:
    """The staged-strictness decision, encoded so tightening it is a
    conversation rather than a silent change.

    Still true after A12: the matrix makes things required *conditionally*,
    and the default deployment shape (object_store, manifest store inherited)
    is row 1 of it — explicitly OK with zero configuration.
    """
    assert _settings().content_sink == "object_store"


# --------------------------------------------------------------------------
# A12 — the startup validation matrix
#
# The four rows of content-external-pipeline.md#config-and-secrets, plus the
# completeness check the matrix implies. Each test asserts the message NAMES
# the variable to set, because that is the actual deliverable: the person
# reading it is looking at a crashed container.
# --------------------------------------------------------------------------


_LLM_MODULE_MINIMUM: dict[str, str] = {
    "content_sink": "llm_module",
    "chunk_profile": "azure_native",
    "llm_module_chunk_profile_confirmed": "azure_native",
    "manifest_store_backend": "s3",
    "manifest_store_endpoint_url": "https://store.example",
    "manifest_store_bucket": "content-manifests",
    # D18: an explicit s3 manifest store authenticates with these.
    "external_s3_access_key_id": "manifest-key-id",
    "external_s3_secret_access_key": "manifest-secret",
    "llm_module_base_url": "https://llm.example/ingest",
    "llm_module_collection": "ckb_chunks_v1",
    "llm_module_vault_secret_path": "llm/connections/ingest",
    # No LLM_MODULE_ALLOW_PROPOSED_CONTRACT: the contract is AGREED
    # (AGREED_TERMS), so the minimum deployment needs no development flag.
}


def _llm_module_settings(**overrides: object) -> Settings:
    """The minimum viable llm_module deployment, so each test can break
    exactly one thing about it."""
    kwargs: dict[str, object] = {**_LLM_MODULE_MINIMUM}
    kwargs.update(overrides)
    return _settings(**kwargs)


def test_matrix_row_1_object_store_needs_no_manifest_store_config(
    clean_env: None,
) -> None:
    """Row 1: the whole reason the two-sink change is additive rather than a
    revision. An object-store deployment gains NO configuration."""
    settings = _settings()
    assert settings.manifest_store_is_inherited is True
    assert settings.resolved_manifest_store_backend == "s3"


def test_matrix_row_2_llm_module_without_a_manifest_store_is_refused() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _settings(content_sink="llm_module")
    message = str(excinfo.value)
    assert "MANIFEST_STORE_BACKEND" in message
    # The reason, not just the refusal: that sink has nowhere to PUT one.
    assert "nowhere to hold manifest.json" in message


@pytest.mark.parametrize(
    ("dropped", "expected"),
    [
        ("llm_module_base_url", "LLM_MODULE_BASE_URL"),
        ("llm_module_collection", "LLM_MODULE_COLLECTION"),
        ("llm_module_vault_secret_path", "LLM_MODULE_VAULT_SECRET_PATH"),
    ],
)
def test_matrix_row_3_llm_module_needs_a_url_and_a_credential(
    dropped: str, expected: str
) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _llm_module_settings(**{dropped: ""})
    assert expected in str(excinfo.value)


def test_matrix_row_3_names_both_when_both_are_missing() -> None:
    """One restart per missing variable is a bad way to configure a service."""
    with pytest.raises(ValidationError) as excinfo:
        _llm_module_settings(llm_module_base_url="", llm_module_vault_secret_path="")
    message = str(excinfo.value)
    assert "LLM_MODULE_BASE_URL" in message
    assert "LLM_MODULE_VAULT_SECRET_PATH" in message


@pytest.mark.parametrize(
    ("collection", "expected"),
    [
        ("contextual_chunks_azure", "never in place"),
        ("Contextual_Chunks_AWS", "never in place"),
        ("ckb/chunks", "LLM_MODULE_COLLECTION"),
        ("ckb chunks", "LLM_MODULE_COLLECTION"),
        ("..", "LLM_MODULE_COLLECTION"),
        ("c" * 65, "LLM_MODULE_COLLECTION"),
    ],
)
def test_l19_the_collection_is_a_new_one_and_a_plain_segment(
    collection: str, expected: str
) -> None:
    """L19: bootstrap beside, never into the llm-module's existing corpus,
    and the name goes into a URL path, so it must be one plain segment."""
    with pytest.raises(ValidationError) as excinfo:
        _llm_module_settings(llm_module_collection=collection)
    assert expected in str(excinfo.value)


def test_l19_a_new_collection_is_accepted() -> None:
    settings = _llm_module_settings(llm_module_collection="ckb_chunks-v2")
    assert settings.llm_module_collection == "ckb_chunks-v2"
    assert settings.sink_id == "llm_module:ckb_chunks-v2"


@pytest.mark.parametrize("collection", ["ckb_retrieval", "CKB_RETRIEVAL"])
def test_l19_a_protected_collection_is_refused(collection: str) -> None:
    """The retrieval alias, named by the deployment: prose saying "never the
    alias" protects nothing, a startup refusal does."""
    with pytest.raises(ValidationError) as excinfo:
        _llm_module_settings(
            llm_module_collection=collection,
            llm_module_protected_collections="ckb_live, ckb_retrieval,",
        )
    assert "LLM_MODULE_PROTECTED_COLLECTIONS" in str(excinfo.value)


def test_l19_protected_collections_parse_and_do_not_block_others() -> None:
    settings = _llm_module_settings(
        llm_module_protected_collections=" ckb_retrieval ,, ckb_live "
    )
    assert settings.protected_llm_module_collections == {"ckb_retrieval", "ckb_live"}
    assert settings.llm_module_collection == "ckb_chunks_v1"


def test_the_object_store_sink_id_is_the_sink_name(clean_env: None) -> None:
    assert _settings().sink_id == "object_store"


def test_matrix_row_4_local_manifest_store_needs_the_dev_flag() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _settings(manifest_store_backend="local")
    message = str(excinfo.value)
    assert "MANIFEST_STORE_ALLOW_LOCAL" in message
    # D19's reasoning has to travel with the refusal, because "just set the
    # flag" is the obvious and wrong response to reading only the first line.
    assert "permanently orphaned" in message


def test_local_manifest_store_is_allowed_with_the_dev_flag() -> None:
    settings = _settings(
        manifest_store_backend="local", manifest_store_allow_local=True
    )
    assert settings.resolved_manifest_store_backend == "local"


def test_llm_module_base_url_must_be_https() -> None:
    """The push body IS Estonian government document text (L14), so plaintext
    is refused here rather than discovered on the wire."""
    with pytest.raises(ValidationError) as excinfo:
        _llm_module_settings(llm_module_base_url="http://llm.example/ingest")
    assert "https://" in str(excinfo.value)


def test_llm_module_base_url_error_does_not_echo_a_credential() -> None:
    """The refusal quotes the offending URL, and a URL can carry userinfo."""
    with pytest.raises(ValidationError) as excinfo:
        _llm_module_settings(llm_module_base_url="http://svc:hunter2@llm.example")
    assert "hunter2" not in str(excinfo.value)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"manifest_store_backend": "s3", "manifest_store_bucket": ""}, "BUCKET"),
        (
            {"manifest_store_backend": "s3", "manifest_store_endpoint_url": ""},
            "ENDPOINT_URL",
        ),
        (
            {"manifest_store_backend": "azure_blob", "manifest_store_bucket": ""},
            "BUCKET",
        ),
    ],
)
def test_an_explicitly_set_manifest_store_must_be_complete(
    overrides: dict[str, object], expected: str
) -> None:
    """Not a documented row, but implied by row 2: without this, a bare
    MANIFEST_STORE_BACKEND=s3 satisfies row 2 while naming a store that cannot
    be addressed, and the refusal lands at first commit after all."""
    with pytest.raises(ValidationError) as excinfo:
        _llm_module_settings(**overrides)
    assert expected in str(excinfo.value)


def test_an_explicit_s3_manifest_store_needs_the_external_keys_at_startup() -> None:
    """D18: refused at startup, naming both, not at first commit — by which
    point a run has published documents it then cannot record."""
    with pytest.raises(ValidationError) as excinfo:
        _llm_module_settings(
            external_s3_access_key_id="", external_s3_secret_access_key=""
        )
    message = str(excinfo.value)
    assert "EXTERNAL_S3_ACCESS_KEY_ID" in message
    assert "EXTERNAL_S3_SECRET_ACCESS_KEY" in message
    # And never a suggestion to use CKB's own read-only keys (rule 6).
    assert "never CKB's own AWS_* keys" in message


def test_an_explicit_s3_manifest_store_on_ckbs_own_bucket_is_refused() -> None:
    """Rule 1's bucket twin: CKB's bucket holds the scraped tree the Global
    Classifier zips. The trailing slash must not be a way around it."""
    with pytest.raises(ValidationError) as excinfo:
        _llm_module_settings(
            s3_endpoint_url="https://store.example/",
            s3_bucket_name="content-manifests",
        )
    assert "CKB's own bucket" in str(excinfo.value)


def test_the_same_bucket_name_on_another_endpoint_is_not_ckbs_bucket() -> None:
    settings = _llm_module_settings(
        s3_endpoint_url="https://ckb-store.internal",
        s3_bucket_name="content-manifests",
    )
    assert settings.resolved_manifest_store_bucket == "content-manifests"


def test_an_azure_manifest_store_needs_no_endpoint_or_s3_keys_at_startup() -> None:
    settings = _llm_module_settings(
        manifest_store_backend="azure_blob",
        manifest_store_endpoint_url="",
        manifest_store_bucket="content-manifests",
        vault_secret_path="blob/connections/azure_blob/content",
        external_s3_access_key_id="",
        external_s3_secret_access_key="",
    )
    assert settings.resolved_manifest_store_backend == "azure_blob"
    assert settings.resolved_manifest_store_bucket == "content-manifests"


def test_local_manifest_store_needs_no_bucket() -> None:
    """It resolves to CONTENT_WORK_DIR, which is already required and already
    asserted."""
    assert (
        _settings(
            manifest_store_backend="local", manifest_store_allow_local=True
        ).manifest_store_bucket
        == ""
    )


@pytest.mark.parametrize("value", ["S3", "minio", "gcs", "azure", "filesystem"])
def test_manifest_store_backend_is_an_enum(value: str) -> None:
    """A typo must be a startup refusal, not a backend that resolves to
    nothing at first commit."""
    with pytest.raises(ValidationError):
        _settings(manifest_store_backend=value)


# --------------------------------------------------------------------------
# A12 — manifest-store resolution (D16, D17)
# --------------------------------------------------------------------------


def test_inherited_resolution_takes_endpoint_bucket_and_prefix_from_the_sink(
    clean_env: None,
) -> None:
    """Verification 20: an object-store deployment with no MANIFEST_STORE_*
    writes to the pre-change key. Inheritance is all-or-nothing."""
    settings = _settings(
        content_external_prefix="content",
        external_s3_endpoint_url="https://store.example",
        external_s3_bucket_name="agency-content",
    )

    assert settings.resolved_manifest_store_backend == "s3"
    assert settings.resolved_manifest_store_endpoint_url == "https://store.example"
    assert settings.resolved_manifest_store_bucket == "agency-content"
    assert settings.resolved_manifest_store_prefix == "content"


def test_inherited_prefix_ignores_the_manifest_store_prefix_default(
    clean_env: None,
) -> None:
    """The clause verification 20 actually turns on.

    MANIFEST_STORE_PREFIX defaults to a non-empty "content-manifests", which
    reads like a default that always applies. If it did, an object-store
    deployment that set nothing would silently relocate its manifest and stop
    being byte-identical to the pre-change design.
    """
    settings = _settings(content_external_prefix="content")

    assert settings.manifest_store_prefix == "content-manifests"
    assert settings.resolved_manifest_store_prefix == "content"


def test_inherited_bucket_follows_the_store_backend(clean_env: None) -> None:
    """On azure_blob the sink's own "bucket" is its container."""
    settings = _settings(
        content_external_store_backend="azure_blob",
        azure_storage_container="agency-content",
        external_s3_bucket_name="should-not-be-used",
    )
    assert settings.resolved_manifest_store_bucket == "agency-content"


def test_an_explicit_manifest_store_overrides_every_inherited_field() -> None:
    settings = _llm_module_settings(manifest_store_prefix="content-manifests")

    assert settings.manifest_store_is_inherited is False
    assert settings.resolved_manifest_store_backend == "s3"
    assert settings.resolved_manifest_store_endpoint_url == "https://store.example"
    assert settings.resolved_manifest_store_bucket == "content-manifests"
    assert settings.resolved_manifest_store_prefix == "content-manifests"


def test_setting_only_a_manifest_prefix_does_not_count_as_configured(
    clean_env: None,
) -> None:
    """MANIFEST_STORE_BACKEND is the switch, deliberately — a prefix alone
    names no store, and treating it as one would half-apply inheritance."""
    settings = _settings(manifest_store_prefix="somewhere-else")
    assert settings.manifest_store_is_inherited is True
    assert settings.resolved_manifest_store_prefix == "content"


def test_an_ignored_manifest_store_setting_is_warned_about(
    clean_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    """The operator who set a bucket and forgot the backend believes the
    manifest moved. It did not, and startup says so."""
    settings = _settings(manifest_store_bucket="manifests")

    with caplog.at_level(logging.WARNING, logger="exporter.api.config"):
        log_redacted_config(settings)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "MANIFEST_STORE_BUCKET is set" in message
    assert "MANIFEST_STORE_BACKEND is empty" in message


def test_default_manifest_store_settings_are_not_warned_about(
    clean_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    """The documented MANIFEST_STORE_PREFIX default is not an operator act."""
    with caplog.at_level(logging.WARNING, logger="exporter.api.config"):
        log_redacted_config(_settings())
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_an_explicit_manifest_store_is_never_warned_about(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="exporter.api.config"):
        log_redacted_config(_llm_module_settings())
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


# --------------------------------------------------------------------------
# Startup warning for an unconfigured destination
# --------------------------------------------------------------------------

_COMPLETE_S3_DESTINATION: dict[str, object] = {
    "external_s3_endpoint_url": "http://minio:9000",
    "external_s3_bucket_name": "content-external",
    "external_s3_access_key_id": SecretStr("writer"),
    "external_s3_secret_access_key": SecretStr("writer-secret"),
}


def _destination_warnings(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> list[str]:
    with caplog.at_level(logging.WARNING, logger="exporter.api.config"):
        warn_on_unconfigured_destination(settings)
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_an_empty_s3_destination_is_warned_about_by_name(
    clean_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    """The defaults: s3 with nothing set. Startup says so instead of the first
    sign being a `failed` run an export interval later."""
    warnings = _destination_warnings(_settings(), caplog)

    assert len(warnings) == 1
    message = warnings[0]
    assert message.startswith(CONFIG_LOG_PREFIX)
    assert "CONTENT_EXTERNAL_STORE_BACKEND=s3" in message
    for name in (
        "EXTERNAL_S3_ENDPOINT_URL",
        "EXTERNAL_S3_BUCKET_NAME",
        "EXTERNAL_S3_ACCESS_KEY_ID",
        "EXTERNAL_S3_SECRET_ACCESS_KEY",
    ):
        assert name in message


def test_an_azure_destination_without_a_container_is_warned_about(
    clean_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(
        content_external_store_backend="azure_blob",
        vault_secret_path="blob/connections/azure_blob/content",
    )
    warnings = _destination_warnings(settings, caplog)

    assert len(warnings) == 1
    assert "CONTENT_EXTERNAL_STORE_BACKEND=azure_blob" in warnings[0]
    assert "AZURE_STORAGE_CONTAINER is empty" in warnings[0]
    assert "VAULT_SECRET_PATH" not in warnings[0]


def test_the_destination_warning_never_carries_a_value(
    clean_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(
        external_s3_access_key_id=SecretStr("AKIA-never-log-me"),
        external_s3_secret_access_key=SecretStr("s3cr3t-never-log-me"),
    )
    warnings = _destination_warnings(settings, caplog)

    assert len(warnings) == 1
    assert "never-log-me" not in warnings[0]
    assert "EXTERNAL_S3_ACCESS_KEY_ID" not in warnings[0]


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param(_COMPLETE_S3_DESTINATION, id="complete-s3"),
        pytest.param(
            {
                "content_external_store_backend": "azure_blob",
                "azure_storage_container": "content",
                "vault_secret_path": "blob/connections/azure_blob/content",
            },
            id="complete-azure",
        ),
    ],
)
def test_a_complete_destination_is_not_warned_about(
    clean_env: None, caplog: pytest.LogCaptureFixture, overrides: dict[str, object]
) -> None:
    assert not _destination_warnings(_settings(**overrides), caplog)


def test_the_llm_module_sink_is_not_warned_about(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """That sink builds no object-store destination, so its empty
    EXTERNAL_S3_* fields are not a misconfiguration."""
    assert not _destination_warnings(_llm_module_settings(), caplog)


def test_missing_destination_settings_is_what_the_factory_refuses(
    clean_env: None,
) -> None:
    """The warning and the run-time refusal read one list, so they can never
    name different variables."""
    from exporter.stores.factory import build_destination_store

    settings = _settings(external_s3_bucket_name="content-external")
    missing = missing_destination_settings(settings)
    assert missing == [
        "EXTERNAL_S3_ENDPOINT_URL",
        "EXTERNAL_S3_ACCESS_KEY_ID",
        "EXTERNAL_S3_SECRET_ACCESS_KEY",
    ]
    with pytest.raises(ConfigurationError, match=", ".join(missing)):
        build_destination_store(settings)


# --------------------------------------------------------------------------
# Rule 1
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "work_dir",
    [
        "/scrapped-data",
        "/scrapped-data/",
        "/scrapped-data/x",
        "/scrapped-data/work/locks",
        "//scrapped-data//work",
        "/scrapped-data/./x",
        "/SCRAPPED-DATA/work",
        "/uploads/scrapped-data/agency-1",
        "/var/lib/foo/scrapped-data/bar",
    ],
)
def test_rule_one_rejects(work_dir: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _settings(work_dir)
    message = str(excinfo.value)
    assert "CONTENT_WORK_DIR" in message
    assert "rule 1" in message


@pytest.mark.parametrize(
    "work_dir",
    [
        "",
        "   ",
        "relative/work",
        "C:\\work",
        "/scrapped-data/../x",
        "/var/lib/../content-external",
    ],
)
def test_malformed_paths_are_rejected(work_dir: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _settings(work_dir)
    assert "CONTENT_WORK_DIR" in str(excinfo.value)


@pytest.mark.parametrize(
    ("work_dir", "expected"),
    [
        ("/var/lib/content-external", "/var/lib/content-external"),
        ("/content-external-work", "/content-external-work"),
        # Contains "scrapped-data" but is not under it — component equality,
        # not startswith and not substring.
        ("/scrapped-data-archive", "/scrapped-data-archive"),
        ("/var/scrapped-data-backup/work", "/var/scrapped-data-backup/work"),
        ("/scrapped-datax", "/scrapped-datax"),
        ("//content//work//", "/content/work"),
        ("/content/work/", "/content/work"),
    ],
)
def test_rule_one_accepts_and_normalises(work_dir: str, expected: str) -> None:
    assert _settings(work_dir).content_work_dir == expected


@pytest.mark.parametrize(
    "variable", ["content_external_prefix", "manifest_store_prefix"]
)
@pytest.mark.parametrize(
    "prefix", ["scrapped-data", "uploads/scrapped-data", "content/SCRAPPED-DATA/x"]
)
def test_rule_one_rejects_a_key_prefix_inside_the_scraped_tree(
    variable: str, prefix: str
) -> None:
    """The destination bucket is not CKB's, but rule 1 is about the path
    component, and a key prefix is where every external write lands."""
    with pytest.raises(ValidationError, match="rule 1"):
        _settings(**{variable: prefix})


def test_rule_one_allows_a_key_prefix_that_only_resembles_the_scraped_tree() -> None:
    assert (
        _settings(
            content_external_prefix="scrapped-data-archive"
        ).content_external_prefix
        == "scrapped-data-archive"
    )


@pytest.mark.skipif(
    os.name == "nt", reason="the POSIX-only rule is what a Linux container sees"
)
@pytest.mark.parametrize("work_dir", ["C:/work", "D:work", "C:/scrapped-data/work"])
def test_windows_shaped_paths_are_rejected_off_windows(work_dir: str) -> None:
    """The production check is strict POSIX.

    On Linux, Path("C:/work") is RELATIVE, so accepting it in a container
    would put the run lock and the deletion journal in a directory named "C:"
    under /app rather than on the mounted volume. The host-absolute carve-out
    exists only so the filesystem tests can run on a developer machine, and
    this asserts it cannot reach the container.
    """
    with pytest.raises(ValidationError) as excinfo:
        _settings(work_dir)
    assert "CONTENT_WORK_DIR" in str(excinfo.value)


@pytest.mark.skipif(os.name != "nt", reason="host-absolute carve-out is Windows-only")
def test_windows_absolute_path_is_accepted_on_windows() -> None:
    """The other half of the carve-out, so tightening or loosening it cannot
    happen silently."""
    assert _settings("C:/work").content_work_dir == "C:/work"


@pytest.mark.skipif(os.name != "nt", reason="host-absolute carve-out is Windows-only")
def test_drive_relative_path_is_rejected_even_on_windows() -> None:
    """is_absolute(), not .drive: "D:work" has a drive but is drive-RELATIVE."""
    with pytest.raises(ValidationError):
        _settings("D:work")


@pytest.mark.skipif(os.name != "nt", reason="host-absolute carve-out is Windows-only")
def test_rule_one_still_applies_to_a_windows_absolute_path() -> None:
    """The carve-out is about shape only — it never exempts a path from
    rule 1."""
    with pytest.raises(ValidationError) as excinfo:
        _settings("C:/scrapped-data/work")
    assert "rule 1" in str(excinfo.value)


def test_rule_one_message_explains_the_mechanism() -> None:
    """The message is the deliverable, not just the refusal — the person
    reading it has not read the design doc."""
    with pytest.raises(ValidationError) as excinfo:
        _settings("/scrapped-data/work")
    message = str(excinfo.value)
    assert "Classifier" in message
    assert "hourly" in message


@pytest.mark.skipif(
    os.name == "nt", reason="symlink creation needs privileges on Windows"
)
def test_realpath_check_rejects_a_symlink_into_a_scrapped_data_tree(
    tmp_path: Path,
) -> None:
    """The pure predicate passes /work; only the realpath pass can see that
    /work -> .../scrapped-data/work. This is what makes the check hard to
    bypass."""
    target = tmp_path / "scrapped-data" / "work"
    target.mkdir(parents=True)
    link = tmp_path / "work"
    link.symlink_to(target, target_is_directory=True)

    settings = _settings(link.as_posix())
    with pytest.raises(ConfigurationError) as excinfo:
        assert_work_dir_usable(settings)
    assert "rule 1" in str(excinfo.value)


def test_work_dir_is_created_and_probed(tmp_path: Path) -> None:
    """A14 replaced the write probe with the exclusive-lock probe, so the
    filename moved with it. Left asserting "no probe file remains" because a
    probe that litters the volume it is validating is its own small bug."""
    target = tmp_path / "new" / "work"
    resolved = assert_work_dir_usable(_settings(target.as_posix()))

    assert resolved.is_dir()
    assert not list(resolved.glob(".startup-lock-probe"))


def test_work_dir_pointing_at_a_file_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "afile"
    target.write_text("", encoding="utf-8")

    with pytest.raises(ConfigurationError) as excinfo:
        assert_work_dir_usable(_settings(target.as_posix()))
    assert "CONTENT_WORK_DIR" in str(excinfo.value)


# --------------------------------------------------------------------------
# Per-field validation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("content_sink", "sftp"),
        ("content_external_store_backend", "gcs"),
        # "reprocess_all" is named "never" in the design; the enum makes it
        # unreachable rather than merely discouraged.
        ("diff_on_missing_manifest", "reprocess_all"),
        ("content_external_prefix", ""),
        ("chunk_profile", ""),
    ],
)
def test_enum_and_string_fields_reject_bad_values(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _settings(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("export_concurrency", 0),
        ("export_concurrency", 17),  # F16's documented cap of ~16
        ("export_concurrency", "four"),
        ("export_page_size", 0),
        ("export_page_size", -1),
        ("ckb_query_timeout", 0),
        ("ckb_query_timeout", -1),
        # Below the content-external/export-* flows' 25 s step timeout + 5.
        ("ckb_query_timeout", 10),
        ("ckb_query_timeout", 29.9),
        # Above what the export-documents flow's `limit:` can buffer.
        ("export_page_size", 5_001),
        ("export_page_size", 20_000),
        ("ckb_s3_connect_timeout", 0),
        ("ckb_s3_connect_timeout", -1),
        ("ckb_s3_read_timeout", 0),
        ("ckb_s3_read_timeout", -1),
        ("max_document_bytes", -1),
        ("max_chunks_per_document", 0),
        ("bootstrap_checkpoint_every", 0),
        ("sink_failure_abort_threshold", 0),
        ("llm_module_connect_timeout", 0),
        ("llm_module_ingest_schema_version", 0),
    ],
)
def test_numeric_bounds(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _settings(**{field: value})


def test_settings_are_frozen() -> None:
    settings = _settings()
    with pytest.raises(ValidationError):
        settings.export_concurrency = 8  # pyright: ignore[reportAttributeAccessIssue]


def test_module_has_no_settings_singleton() -> None:
    """The cross-cutting Config rule ("never a module-global singleton reached
    into from a service") as an executable check rather than a review
    convention."""
    assert not hasattr(config_module, "settings")


def test_load_settings_sanitises_the_validation_error(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(WORK_DIR_ENV, "/var/lib/content-external")
    monkeypatch.setenv("EXTERNAL_S3_ENDPOINT_URL", "https://u:hunter2@store.example/")
    monkeypatch.setenv("EXPORT_CONCURRENCY", "0")

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    message = str(excinfo.value)
    assert "export_concurrency" in message
    assert "hunter2" not in message


# --------------------------------------------------------------------------
# Chunk profile (B11)
#
# One env var naming a reviewed preset — never four knobs — and pinned
# explicitly on the llm_module sink, where the far side embeds our chunks and
# an oversized one is truncated in silence.
# --------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_default_chunk_profile_is_a_known_preset(clean_env: None) -> None:
    assert DEFAULT_CHUNK_PROFILE in CHUNK_PROFILES
    assert _settings().chunk_profile == DEFAULT_CHUNK_PROFILE


@pytest.mark.parametrize("name", sorted(CHUNK_PROFILES))
def test_every_preset_is_accepted_and_resolves_to_its_row(
    clean_env: None, name: str
) -> None:
    assert _settings(chunk_profile=name).resolved_chunk_profile == CHUNK_PROFILES[name]


@pytest.mark.parametrize("name", ["medium", "Compact", "azure_native "])
def test_unknown_chunk_profile_is_refused_by_name(clean_env: None, name: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _settings(chunk_profile=name)
    message = str(excinfo.value)
    assert "CHUNK_PROFILE" in message
    for preset in CHUNK_PROFILES:
        assert preset in message


def test_llm_module_without_an_explicit_chunk_profile_is_refused(
    clean_env: None,
) -> None:
    unpinned = {k: v for k, v in _LLM_MODULE_MINIMUM.items() if k != "chunk_profile"}
    with pytest.raises(ValidationError) as excinfo:
        _settings(**unpinned)
    message = str(excinfo.value)
    assert "CHUNK_PROFILE" in message
    assert "silently truncated" in message


@pytest.mark.parametrize("confirmed", ["", "compact", "Azure_Native"])
def test_llm_module_needs_the_chunk_profile_confirmed_by_name(
    clean_env: None, confirmed: str
) -> None:
    """B11 / §13 q1: compose and the chart set CHUNK_PROFILE for every
    deployment, so an explicit CHUNK_PROFILE proves nothing was chosen. The
    confirmation must name the profile in force — and stops matching when
    CHUNK_PROFILE changes without it."""
    with pytest.raises(ValidationError) as excinfo:
        _llm_module_settings(llm_module_chunk_profile_confirmed=confirmed)
    message = str(excinfo.value)
    assert "LLM_MODULE_CHUNK_PROFILE_CONFIRMED=azure_native" in message
    assert "silently truncated" in message


def test_a_confirmed_chunk_profile_is_accepted(clean_env: None) -> None:
    settings = _llm_module_settings(
        chunk_profile="compact", llm_module_chunk_profile_confirmed="compact"
    )
    assert settings.chunk_profile == "compact"


def test_compose_and_chart_never_confirm_a_chunk_profile() -> None:
    """The confirmation is a deliberate act per llm_module deployment; a
    default on either surface would make it the same rubber stamp an
    explicit CHUNK_PROFILE already is there."""
    for surface in (
        REPO_ROOT / "docker-compose.yml",
        REPO_ROOT / "charts" / "ckb" / "values.yaml",
    ):
        for line in surface.read_text(encoding="utf-8").splitlines():
            if "LLM_MODULE_CHUNK_PROFILE_CONFIRMED" in line:
                assert line.lstrip().startswith("#"), (surface.name, line)


def test_the_metadata_merge_is_off_unless_the_deployment_opts_in(
    clean_env: None,
) -> None:
    """L25: the far side answering a merge without applying it is silent, so
    the agreed term alone does not switch the merge on (sinks/factory.py)."""
    assert _llm_module_settings().llm_module_enable_metadata_merge is False
    settings = _llm_module_settings(llm_module_enable_metadata_merge=True)
    assert settings.llm_module_enable_metadata_merge is True


def test_the_merge_opt_in_is_refused_when_the_contract_does_not_promise_it(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config_module, "AGREED_TERMS", PROPOSED_TERMS)
    with pytest.raises(ValidationError, match="LLM_MODULE_ENABLE_METADATA_MERGE"):
        _llm_module_settings(
            llm_module_enable_metadata_merge=True,
            llm_module_allow_proposed_contract=True,
        )


def test_object_store_may_rely_on_the_default_chunk_profile(clean_env: None) -> None:
    settings = _settings()
    assert "chunk_profile" not in settings.model_fields_set
    assert settings.resolved_chunk_profile == CHUNK_PROFILES[DEFAULT_CHUNK_PROFILE]


# A setting name that would carry one of the four chunk sizes on its own.
# max_chunks_per_document is a safety limit, not geometry, and does not match.
_GEOMETRY_KNOB = re.compile(
    r"overlap|target|(?:^|_)(?:min|max)_(?:size|chars|length)"
    r"|chunk_(?:size|min|max|length)"
)


def test_chunk_geometry_is_never_a_separate_setting() -> None:
    """B11: the four sizes are one reviewed row in CHUNK_PROFILES, picked by
    name. A Settings field per size is the four independent knobs the
    presets exist to prevent — and with extra="ignore", a stray CHUNK_TARGET
    in the environment is silently dropped, not honoured."""
    knobs = sorted(
        name for name in Settings.model_fields if _GEOMETRY_KNOB.search(name)
    )
    assert not knobs, (
        f"{knobs} look like separate chunk-size settings. Add a preset to "
        "CHUNK_PROFILES in exporter/core/constants.py instead."
    )
    assert _GEOMETRY_KNOB.search("chunk_overlap")
    assert not _GEOMETRY_KNOB.search("max_chunks_per_document")


def test_max_chunks_per_document_is_capped_by_the_chunk_key(clean_env: None) -> None:
    """B6: {ordinal:05d} holds ordinals up to MAX_CHUNK_ORDINAL; one more
    chunk and keys stop sorting in ordinal order."""
    assert _settings(max_chunks_per_document=MAX_CHUNK_ORDINAL + 1)
    with pytest.raises(ValidationError, match="max_chunks_per_document"):
        _settings(max_chunks_per_document=MAX_CHUNK_ORDINAL + 2)


@pytest.mark.parametrize("field", ["content_external_prefix", "manifest_store_prefix"])
@pytest.mark.parametrize("bad", ["content/", "/content", "a//b", "a/../b"])
def test_a_malformed_key_prefix_is_refused_at_startup(
    clean_env: None, field: str, bad: str
) -> None:
    with pytest.raises(ValidationError, match=field):
        _settings(**{field: bad})


def test_a_multi_segment_key_prefix_is_accepted(clean_env: None) -> None:
    assert _settings(content_external_prefix="ckb/content").content_external_prefix


def _set_llm_module_env(monkeypatch: pytest.MonkeyPatch, *, pinned: bool) -> None:
    monkeypatch.setenv(WORK_DIR_ENV, "/var/lib/content-external")
    for name, value in _LLM_MODULE_MINIMUM.items():
        if name != "chunk_profile" or pinned:
            monkeypatch.setenv(name.upper(), value)


def test_chunk_profile_from_the_environment_counts_as_pinned(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pinning rule reads model_fields_set, so it depends on
    pydantic-settings counting an env value as explicitly set — including
    one that happens to equal the default."""
    _set_llm_module_env(monkeypatch, pinned=True)
    settings = load_settings()
    assert settings.chunk_profile == DEFAULT_CHUNK_PROFILE
    assert "chunk_profile" in settings.model_fields_set


def test_llm_module_env_without_chunk_profile_refuses_to_start(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_llm_module_env(monkeypatch, pinned=False)
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "CHUNK_PROFILE" in str(excinfo.value)


def test_llm_module_env_without_a_manifest_store_refuses_to_start(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D18 through the startup path itself, not only the model: the lifespan
    calls load_settings, so this is the refusal a crashed container shows."""
    _set_llm_module_env(monkeypatch, pinned=True)
    monkeypatch.delenv("MANIFEST_STORE_BACKEND")
    with pytest.raises(ConfigurationError) as excinfo:
        load_settings()
    assert "MANIFEST_STORE_BACKEND" in str(excinfo.value)


def test_config_echo_carries_the_chunk_geometry_and_fingerprint(
    clean_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A profile name says which preset; only the numbers and the fingerprint
    say whether two deployments would chunk a document identically."""
    with caplog.at_level(logging.INFO, logger="exporter.api.config"):
        log_redacted_config(_settings(chunk_profile="compact"))

    message = caplog.records[-1].getMessage()
    assert "chunk_profile=compact" in message
    assert "chunk_geometry=target=450,overlap=80,min=120,max=700" in message
    fingerprint = chunker_fingerprint(CHUNK_PROFILES["compact"])
    assert f"chunker_fingerprint={fingerprint}" in message


def test_compose_and_chart_pin_the_same_known_chunk_profile() -> None:
    """compose alone passes local testing and silently leaves Kubernetes on
    whatever the code default is, so both surfaces are checked together."""
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    values = (REPO_ROOT / "charts" / "ckb" / "values.yaml").read_text(encoding="utf-8")

    in_compose = re.findall(r"^\s*-\s*CHUNK_PROFILE=(\S+)\s*$", compose, re.MULTILINE)
    in_chart = re.findall(
        r"-\s*name:\s*CHUNK_PROFILE\s*\n\s*value:\s*\"?([^\"\s]+)\"?", values
    )
    assert len(in_compose) == 1, in_compose
    assert in_chart == in_compose
    assert in_compose[0] in CHUNK_PROFILES


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------


def test_redaction_never_emits_a_secret(clean_env: None) -> None:
    settings = _settings(
        aws_access_key_id=SecretStr("AKIAEXAMPLE"),
        aws_secret_access_key=SecretStr("s3cr3t-never-log-me"),
    )
    fields = redacted_settings(settings)

    assert "s3cr3t-never-log-me" not in repr(fields)
    assert "AKIAEXAMPLE" not in repr(fields)
    assert fields["aws_secret_access_key"] == "[set]"
    assert fields["aws_access_key_id"] == "[set]"


def test_destination_credentials_are_redacted(clean_env: None) -> None:
    settings = _settings(
        external_s3_access_key_id=SecretStr("EXTKEYEXAMPLE"),
        external_s3_secret_access_key=SecretStr("ext-s3cr3t-never-log-me"),
    )
    fields = redacted_settings(settings)

    assert "ext-s3cr3t-never-log-me" not in repr(fields)
    assert "EXTKEYEXAMPLE" not in repr(fields)
    assert fields["external_s3_access_key_id"] == "[set]"
    assert fields["external_s3_secret_access_key"] == "[set]"


def test_unset_secrets_report_unset(clean_env: None) -> None:
    fields = redacted_settings(_settings())
    assert fields["aws_secret_access_key"] == "[unset]"


def test_every_secret_shaped_field_is_redacted(clean_env: None) -> None:
    """Iterates the model, so a credential field added by A12, H or L is
    covered without anyone remembering this file exists."""
    settings = _settings()
    fields = redacted_settings(settings)

    for name, field in Settings.model_fields.items():
        # Calls the real predicate rather than restating it, so a refactor of
        # _is_secret_field cannot leave this test passing against stale
        # duplicated logic.
        if config_module._is_secret_field(name, field.annotation):
            assert fields[name] in {"[set]", "[unset]"}, name


def test_address_allowlist_contains_no_secretstr_field() -> None:
    """The interlock that makes the allowlist safe: it can exempt an address
    from the name-based denylist, but it can never unhide a real credential."""
    for name in config_module._ADDRESS_NOT_CREDENTIAL:
        assert Settings.model_fields[name].annotation is not SecretStr


def test_redaction_strips_userinfo_from_a_url(clean_env: None) -> None:
    settings = _settings(llm_module_base_url="https://svc:hunter2@llm.example/ingest")
    value = redacted_settings(settings)["llm_module_base_url"]
    assert "hunter2" not in value
    assert "[redacted]" in value


def test_echo_logs_once_at_info_with_no_secret(
    clean_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(aws_secret_access_key=SecretStr("s3cr3t-never-log-me"))

    with caplog.at_level(logging.INFO, logger="exporter.api.config"):
        log_redacted_config(settings)

    records = [r for r in caplog.records if r.name == "exporter.api.config"]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    message = records[0].getMessage()
    assert message.startswith(CONFIG_LOG_PREFIX)
    assert "s3cr3t-never-log-me" not in message


# --------------------------------------------------------------------------
# The contract gate (Stage L)
# --------------------------------------------------------------------------


@pytest.fixture
def proposed_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """The contract as it was before the agreement was recorded — so the
    gate's refusal is still proven, and still works if the agreement is
    withdrawn (AGREED_TERMS back to PROPOSED_TERMS)."""
    monkeypatch.setattr(config_module, "AGREED_TERMS", PROPOSED_TERMS)


def test_llm_module_is_refused_while_the_contract_is_proposed(
    proposed_contract: None,
) -> None:
    """Against an unagreed contract two wrong guesses fail silently (L4,
    B11), so the sink is refused at startup, naming the open items."""
    with pytest.raises(ValidationError, match="PROPOSED") as excinfo:
        _llm_module_settings(llm_module_allow_proposed_contract=False)
    assert "Open items 5 and 7" in str(excinfo.value)
    assert "LLM_MODULE_ALLOW_PROPOSED_CONTRACT" in str(excinfo.value)


def test_the_development_flag_lets_llm_module_start_against_the_proposal(
    proposed_contract: None,
) -> None:
    settings = _llm_module_settings(llm_module_allow_proposed_contract=True)
    assert settings.content_sink == "llm_module"


def test_the_agreed_contract_lifts_the_gate() -> None:
    """The recorded agreement (AGREED_TERMS.status = "agreed"): an
    llm_module deployment starts with no development flag."""
    assert config_module.AGREED_TERMS.status == "agreed"
    settings = _llm_module_settings(llm_module_allow_proposed_contract=False)
    assert settings.content_sink == "llm_module"
    assert config_module.llm_module_contract_refusal(allow_proposed=False) is None


def test_the_contract_gate_never_touches_an_object_store_deployment() -> None:
    assert _settings(llm_module_allow_proposed_contract=False).content_sink == (
        "object_store"
    )


def test_the_stale_sweep_is_off_unless_asked_for(clean_env: None) -> None:
    assert _settings().stale_sweep_enabled is False


# --------------------------------------------------------------------------
# L11 — the llm-module credential, from Vault, per call
# --------------------------------------------------------------------------

_SECRET_PATH = "llm/connections/llm_module/content"


class _VaultResponse:
    def __init__(self, status: int, payload: object) -> None:
        self.status_code = status
        self._payload = payload
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(response=self)  # type: ignore[arg-type]

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _vault(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    response: _VaultResponse,
    *,
    agent_token: str = "agent-token-1",
) -> tuple[Settings, list[dict[str, object]]]:
    token_file = tmp_path / "token"
    token_file.write_text(agent_token, encoding="utf-8")
    calls: list[dict[str, object]] = []

    def get(url: str, **kwargs: object) -> _VaultResponse:
        calls.append({"url": url, **kwargs})
        return response

    monkeypatch.setattr(config_module.requests, "get", get)
    settings = Settings.model_construct(
        ckb_vault="http://vault:8200/",
        vault_token_path=str(token_file),
        llm_module_vault_secret_path=f"/{_SECRET_PATH}/",
    )
    return settings, calls


def _secret(token: object) -> _VaultResponse:
    return _VaultResponse(200, {"data": {"data": {"token": token}}})


def test_the_credential_is_read_from_vault_with_the_agent_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings, calls = _vault(monkeypatch, tmp_path, _secret("  bearer-xyz  "))

    assert config_module.fetch_llm_module_token(settings) == "bearer-xyz"
    assert config_module.fetch_llm_module_token(settings) == "bearer-xyz"

    # Read on every call, never cached: Vault Agent's rotation is respected.
    assert len(calls) == 2
    assert calls[0]["url"] == f"http://vault:8200/v1/secret/data/{_SECRET_PATH}"
    assert calls[0]["headers"] == {"X-Vault-Token": "agent-token-1"}
    assert calls[0]["allow_redirects"] is False
    assert calls[0]["timeout"]


@pytest.mark.parametrize(
    ("response", "match"),
    [
        (_secret("REPLACE_ME"), "placeholder"),
        (_secret(""), "no 'token' value"),
        (_secret(None), "no 'token' value"),
        (_VaultResponse(200, {"data": {}}), "unexpected shape"),
        (_VaultResponse(200, ValueError("not json")), "unexpected shape"),
        (_VaultResponse(403, {}), "HTTP 403"),
    ],
)
def test_an_unusable_vault_answer_is_refused_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    response: _VaultResponse,
    match: str,
) -> None:
    settings, _ = _vault(monkeypatch, tmp_path, response)

    with pytest.raises(CredentialUnavailableError, match=match) as excinfo:
        config_module.fetch_llm_module_token(settings, sleep=_no_sleep)
    assert "agent-token-1" not in str(excinfo.value)


def _no_sleep(_: float) -> None:
    pass


def test_a_refused_vault_read_is_not_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings, calls = _vault(monkeypatch, tmp_path, _VaultResponse(403, {}))

    with pytest.raises(CredentialUnavailableError, match="HTTP 403"):
        config_module.fetch_llm_module_token(settings, sleep=_no_sleep)
    assert len(calls) == 1


def test_a_vault_blip_is_retried_inside_one_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings, calls = _vault(monkeypatch, tmp_path, _secret("bearer-xyz"))
    answers = iter([_VaultResponse(503, {}), _VaultResponse(503, {})])

    def get(url: str, **kwargs: object) -> _VaultResponse:
        calls.append({"url": url, **kwargs})
        return next(answers, _secret("bearer-xyz"))

    monkeypatch.setattr(config_module.requests, "get", get)
    slept: list[float] = []

    assert (
        config_module.fetch_llm_module_token(settings, sleep=slept.append)
        == "bearer-xyz"
    )
    assert len(calls) == 3
    assert len(slept) == 2


def test_vault_down_past_the_backoff_names_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import requests

    settings, calls = _vault(monkeypatch, tmp_path, _secret("x"))

    def get(url: str, **kwargs: object) -> _VaultResponse:
        calls.append({"url": url, **kwargs})
        raise requests.ConnectionError("connection refused to http://vault:8200")

    monkeypatch.setattr(config_module.requests, "get", get)

    with pytest.raises(CredentialUnavailableError, match="is Vault up") as excinfo:
        config_module.fetch_llm_module_token(settings, sleep=_no_sleep)
    assert len(calls) == config_module.VAULT_READ_POLICY.max_attempts
    assert "agent-token-1" not in str(excinfo.value)


def test_a_missing_agent_token_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings, calls = _vault(monkeypatch, tmp_path, _secret("bearer-xyz"))
    Path(settings.vault_token_path).unlink()

    with pytest.raises(CredentialUnavailableError, match="Vault Agent token"):
        config_module.fetch_llm_module_token(settings)
    assert calls == []


def test_an_empty_agent_token_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings, calls = _vault(monkeypatch, tmp_path, _secret("x"), agent_token="  ")

    with pytest.raises(CredentialUnavailableError, match="is empty"):
        config_module.fetch_llm_module_token(settings)
    assert calls == []


# --------------------------------------------------------------------------
# Endpoints come from constants.ini, never from code
# --------------------------------------------------------------------------


def _constants(tmp_path: Path, body: str) -> list[Path]:
    path = tmp_path / "constants.ini"
    path.write_text(body, encoding="utf-8")
    return [path]


def test_endpoints_are_read_from_constants_ini(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The shared file Ruuter, cleaning and scrapper read. Keys the exporter
    does not know (CKB_RESQL among them) are ignored."""
    monkeypatch.setenv(WORK_DIR_ENV, "/var/lib/content-external")
    files = _constants(
        tmp_path,
        "CKB_RESQL=http://resql-ckb:8090/ckb\n"
        "CKB_RUUTER_INTERNAL=http://ruuter-internal.test:8089/ckb\n"
        "CKB_VAULT=http://vault.test:8200\n"
        "BUROKRATT_CHATBOT_WIDGET_DOMAIN_ID=\n",
    )

    settings = load_settings(files)

    assert settings.ckb_ruuter_internal == "http://ruuter-internal.test:8089/ckb"
    assert settings.ckb_vault == "http://vault.test:8200"


def test_an_environment_variable_overrides_constants_ini(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The docker-compose-test.yml pattern: env wins over the file."""
    monkeypatch.setenv(WORK_DIR_ENV, "/var/lib/content-external")
    monkeypatch.setenv("CKB_RUUTER_INTERNAL", "http://mock-ruuter:8089/ckb")
    files = _constants(
        tmp_path, "CKB_RUUTER_INTERNAL=http://ruuter-internal:8089/ckb\n"
    )

    assert load_settings(files).ckb_ruuter_internal == "http://mock-ruuter:8089/ckb"


@pytest.mark.parametrize(
    "body", ["", "CKB_RUUTER_INTERNAL=\n", "CKB_RUUTER_INTERNAL=ruuter-internal:8089\n"]
)
def test_startup_refuses_without_a_ruuter_internal_endpoint(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: str
) -> None:
    """Missing, empty or not a URL: the service never falls back to a host
    name baked into code."""
    monkeypatch.setenv(WORK_DIR_ENV, "/var/lib/content-external")
    with pytest.raises(ConfigurationError, match="CKB_RUUTER_INTERNAL") as excinfo:
        load_settings(_constants(tmp_path, body))
    assert "constants.ini" in str(excinfo.value)


def test_startup_refuses_when_constants_ini_is_not_mounted(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(WORK_DIR_ENV, "/var/lib/content-external")
    with pytest.raises(ConfigurationError, match="CKB_RUUTER_INTERNAL"):
        load_settings([tmp_path / "absent.ini"])


def test_vault_is_not_required_for_an_s3_object_store(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(WORK_DIR_ENV, "/var/lib/content-external")
    files = _constants(
        tmp_path, "CKB_RUUTER_INTERNAL=http://ruuter-internal:8089/ckb\n"
    )

    assert load_settings(files).ckb_vault == ""


def test_vault_is_required_when_the_configuration_reads_it(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(WORK_DIR_ENV, "/var/lib/content-external")
    monkeypatch.setenv("CONTENT_EXTERNAL_STORE_BACKEND", "azure_blob")
    files = _constants(
        tmp_path, "CKB_RUUTER_INTERNAL=http://ruuter-internal:8089/ckb\n"
    )

    with pytest.raises(ConfigurationError, match="CKB_VAULT"):
        load_settings(files)


@pytest.mark.parametrize(
    "overrides, required",
    [
        ({}, False),
        ({"content_external_store_backend": "azure_blob"}, True),
        (
            {
                "manifest_store_backend": "azure_blob",
                "manifest_store_bucket": "manifests",
                "vault_secret_path": "blob/x",
            },
            True,
        ),
    ],
)
def test_vault_is_required_exactly_when_something_reads_it(
    overrides: dict[str, object], required: bool
) -> None:
    assert _settings(**overrides).vault_is_required is required


def test_the_repo_constants_ini_satisfies_startup(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file compose and the chart mount defines what startup requires,
    with the project layer included, as cleaning and scrapper expect it."""
    monkeypatch.setenv(WORK_DIR_ENV, "/var/lib/content-external")
    settings = load_settings()

    assert settings.ckb_ruuter_internal.endswith("/ckb")
    assert settings.ckb_vault.startswith("http")


def test_constants_files_cover_the_container_and_the_repo() -> None:
    """/app/constants.ini in the container (config.py is /app/exporter/api/),
    the repo root's when run from a checkout."""
    repo_root = Path(__file__).resolve().parents[1]
    content_external = repo_root / "content-external"
    assert repo_root / "constants.ini" in config_module.CONSTANTS_FILES
    assert content_external / "constants.ini" in config_module.CONSTANTS_FILES


# --------------------------------------------------------------------------
# Settings the content-external flows' fixed Ruuter values bound
# --------------------------------------------------------------------------


def test_the_flow_bounds_are_inclusive() -> None:
    """The floor and ceiling themselves are valid values."""
    from exporter.services.ckb_ruuter import (
        MAX_EXPORT_PAGE_SIZE,
        MIN_QUERY_TIMEOUT_SECONDS,
    )

    settings = _settings(
        ckb_query_timeout=MIN_QUERY_TIMEOUT_SECONDS,
        export_page_size=MAX_EXPORT_PAGE_SIZE,
    )
    assert settings.ckb_query_timeout == MIN_QUERY_TIMEOUT_SECONDS
    assert settings.export_page_size == MAX_EXPORT_PAGE_SIZE


@pytest.mark.parametrize(
    ("variable", "value", "names"),
    [
        ("CKB_QUERY_TIMEOUT", "20", "step timeout"),
        ("EXPORT_PAGE_SIZE", "10000", "export-documents flow"),
    ],
)
def test_a_value_the_flows_cannot_honour_refuses_startup_by_name(
    clean_env: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    variable: str,
    value: str,
    names: str,
) -> None:
    """The refusal names the variable and the flow it would break, because
    the person reading it is looking at a crashed container."""
    monkeypatch.setenv(WORK_DIR_ENV, "/var/lib/content-external")
    monkeypatch.setenv(variable, value)
    files = _constants(
        tmp_path, "CKB_RUUTER_INTERNAL=http://ruuter-internal:8089/ckb\n"
    )

    with pytest.raises(ConfigurationError) as excinfo:
        load_settings(files)
    assert variable in str(excinfo.value)
    assert names in str(excinfo.value)
