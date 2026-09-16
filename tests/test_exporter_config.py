"""Unit tests for content-external's settings, rule 1, and config redaction.

Pure: no Docker, no compose stack, no network. tests/conftest.py has no
autouse fixtures, so nothing here starts the cleaning stack.
"""

import logging
import os
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
    redacted_settings,
)

WORK_DIR_ENV = "CONTENT_WORK_DIR"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every variable Settings reads.

    Derived from the model itself, so a field added by A12, H or L is covered
    without editing this fixture. It is needed because tests/conftest.py
    already seeds RUUTER_INTERNAL for the cleaning tests, and a developer
    shell may carry AWS_* credentials.
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
    assert settings.ruuter_internal == "http://ruuter-internal:8089"
    assert settings.ckb_resql == "http://resql-ckb:8090/ckb"
    assert settings.vault_addr == "http://vault:8200"
    assert settings.vault_token_path == "/agent/out/token"
    assert settings.vault_secret_path == ""
    assert settings.azure_storage_container == "agency-content"


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
    settings = load_settings()
    assert settings.content_work_dir == "/work"
    assert settings.export_concurrency == 8


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


def _llm_module_settings(**overrides: object) -> Settings:
    """The minimum viable llm_module deployment, so each test can break
    exactly one thing about it."""
    kwargs: dict[str, object] = {
        "content_sink": "llm_module",
        "manifest_store_backend": "s3",
        "manifest_store_endpoint_url": "https://store.example",
        "manifest_store_bucket": "content-manifests",
        "llm_module_base_url": "https://llm.example/ingest",
        "llm_module_vault_secret_path": "llm/connections/ingest",
    }
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


def test_azure_manifest_store_does_not_require_an_endpoint_url() -> None:
    """Azure Blob addresses its container through the account, not an
    endpoint URL, so requiring one would be a refusal with no remedy."""
    settings = _llm_module_settings(
        manifest_store_backend="azure_blob",
        manifest_store_endpoint_url="",
        manifest_store_bucket="content-manifests",
    )
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
    target = tmp_path / "new" / "work"
    resolved = assert_work_dir_usable(_settings(target.as_posix()))

    assert resolved.is_dir()
    assert not list(resolved.glob(".content-external-write-probe"))


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
