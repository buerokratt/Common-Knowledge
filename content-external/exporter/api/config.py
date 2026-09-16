"""Environment-built configuration for the content-external exporter.

Deliberately unlike cleaning/api/config.py in one respect: there is NO
module-level `settings = Settings()` singleton. Settings are built once in the
FastAPI lifespan (exporter/api/app.py) and injected from there — handlers via
Depends, services as a constructor argument. Nothing under exporter/services/
or exporter/core/ may import a settings object. That is the cross-cutting
Config rule: "injected as constructor arguments, never a module-global
singleton reached into from a service."

Scope note (A10, closed by A12). Every variable in the design's env block is
declared here with its documented default. CONTENT_WORK_DIR is the only
unconditionally required one; everything else is required *conditionally*, and
those conditions are the @model_validator methods at the bottom of Settings —
the four-row startup validation matrix from
content-external-pipeline.md#config-and-secrets.

MANIFEST-STORE RESOLUTION IS ALL-OR-NOTHING (A12, D17). The switch is whether
MANIFEST_STORE_BACKEND is set. When it is empty the manifest store IS the
object-store sink's own store — endpoint, bucket AND prefix all inherited —
and MANIFEST_STORE_* is ignored in its entirety, including
MANIFEST_STORE_PREFIX's non-empty default.

That last clause is the non-obvious one, so it is spelled out. The design's env
block documents `MANIFEST_STORE_PREFIX=content-manifests`, which reads like a
default that always applies. If resolution merged field-by-field, an
object-store deployment that set nothing would write its manifest under
`content-manifests/...` instead of under CONTENT_EXTERNAL_PREFIX — and
verification 20 requires that such a deployment write to the pre-change key
*byte-identically*, gaining no configuration at all. So `content-manifests` is
the recommended value for an llm_module deployment (which must set the backend
explicitly anyway), never a default that silently relocates an object-store
deployment's manifest.
"""

import logging
import os
import posixpath
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Literal

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from exporter.core.redaction import sanitize_sensitive_text

logger = logging.getLogger(__name__)

# Stable prefix so the startup echo is one grep away, and so two deployments
# can be diffed line-for-line. A15's per-run terminal line follows the same
# convention.
CONFIG_LOG_PREFIX = "content-external config"

# Rule 1. The scraped tree is /scrapped-data in every container that mounts it
# and uploads/scrapped-data/ in S3. Both end in the same path component, so
# the component is what we forbid — that one predicate covers both shapes.
SCRAPPED_DATA_COMPONENT = "scrapped-data"

_SET = "[set]"
_UNSET = "[unset]"

# Name-based denylist, so a credential field added by A12, H or L is redacted
# without anyone remembering this list exists.
_SECRET_NAME_PARTS = (
    "key",
    "secret",
    "token",
    "password",
    "passwd",
    "credential",
    "sas",
)

# Names that trip the denylist but are addresses, not credentials. A path in
# Vault's namespace is not a secret, and hiding it makes a misconfigured
# sidecar much harder to diagnose. This allowlist CANNOT override a SecretStr
# annotation (see _is_secret_field), so it cannot be used to unhide a real
# credential — and a test asserts that interlock holds.
_ADDRESS_NOT_CREDENTIAL = frozenset(
    {"vault_token_path", "vault_secret_path", "llm_module_vault_secret_path"}
)


class ConfigurationError(RuntimeError):
    """The environment is unusable. Always fatal — never degraded service."""


def normalise_container_path(raw: str, *, variable: str) -> str:
    """Normalise an absolute POSIX container path. Pure: no filesystem access.

    PurePosixPath/posixpath rather than Path, because these are container
    paths and the unit tests run on developer Windows machines, where
    Path("/work").is_absolute() is False.

    A fully absolute Windows path (C:/work) is accepted ONLY when this code is
    itself running on Windows, which the service never is — it ships in a
    Linux container. That keeps the filesystem-touching tests runnable on a
    developer machine without loosening anything in production, where the
    check stays strict POSIX and therefore matches the Helm-side mirror in
    charts/ckb/templates/_helpers.tpl exactly.

    Gating on the host matters: on Linux, Path("C:/work") is a RELATIVE path,
    so accepting that shape in a container would put the run lock and the
    deletion journal in a directory named "C:" under the CWD (/app, the code
    directory) instead of the mounted volume — the same silent
    non-persistence that giving content_work_dir a default would have caused.

    '..' segments are rejected rather than resolved: resolving them without
    the filesystem is unsound, and resolving them with it would make this
    function do I/O. No legitimate value contains one.
    """
    value = raw.strip()
    if not value:
        raise ValueError(f"{variable} must not be empty")
    if "\\" in value:
        raise ValueError(f"{variable} must be a POSIX path, got {value!r}")
    # is_absolute(), not .drive: "D:work" is drive-RELATIVE and must not pass.
    host_absolute = os.name == "nt" and PureWindowsPath(value).is_absolute()
    if not value.startswith("/") and not host_absolute:
        raise ValueError(f"{variable} must be an absolute path, got {value!r}")
    if ".." in PurePosixPath(value).parts:
        raise ValueError(
            f"{variable} must be normalised with no '..' segments, got {value!r}"
        )
    # posixpath.normpath preserves exactly two leading slashes (POSIX leaves
    # //foo implementation-defined). Nothing here wants that distinction.
    normalised = posixpath.normpath(value).replace("//", "/", 1)
    return normalised if normalised == "/" else normalised.rstrip("/")


def assert_not_under_scrapped_data(path: str, *, variable: str) -> None:
    """Rule 1. Refuse any path with a `scrapped-data` component.

    Component-wise, not substring: /var/lib/scrapped-data-archive is a
    different directory and is allowed; /var/lib/scrapped-data/x is not. A
    component check also catches /uploads/scrapped-data/x and
    /data/uploads/scrapped-data/x, which a startswith("/scrapped-data") check
    misses and which are exactly what a well-meaning operator would try.

    Case-insensitive, because a case variant is never a legitimate value here
    and being wrong in that direction costs nothing.
    """
    parts = [part.casefold() for part in PurePosixPath(path).parts]
    if SCRAPPED_DATA_COMPONENT in parts:
        raise ValueError(
            f"rule 1: {variable}={path!r} is inside a "
            f"'{SCRAPPED_DATA_COMPONENT}' tree. Everything written there is "
            "zipped into the Global Classifier's payload on the next hourly "
            f"run, silently. Point {variable} at this service's own durable "
            "volume (the named volume in docker-compose.yml, the PVC in the "
            "chart)."
        )


class Settings(BaseSettings):
    """Typed environment for the exporter. Frozen; built once, injected."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    # --- the destination ---------------------------------------------------
    # Declared here; the four-row validation matrix deciding which
    # combinations are legal is A12's deliverable.
    content_sink: Literal["object_store", "llm_module"] = "object_store"
    content_external_store_backend: Literal["s3", "azure_blob"] = "s3"
    content_external_prefix: str = Field(default="content", min_length=1)
    sink_failure_abort_threshold: Annotated[int, Field(ge=1)] = 25

    # --- where the manifest lives — never the destination ------------------
    # Blank is the load-bearing value, not merely the absence of one: it means
    # "inherit the object-store sink's store, bucket and prefix wholesale"
    # (D17), which is what makes the two-sink change additive for an
    # object-store deployment. See the module docstring.
    #
    # Literal rather than str so a typo (`MANIFEST_STORE_BACKEND=S3`) is a
    # startup refusal rather than a backend that resolves to nothing at first
    # commit — the failure this whole matrix exists to pull forward.
    manifest_store_backend: Literal["", "s3", "azure_blob", "local"] = ""
    manifest_store_endpoint_url: str = ""
    manifest_store_bucket: str = ""
    manifest_store_prefix: str = "content-manifests"
    # D19's dev flag. `local` puts the manifest on CONTENT_WORK_DIR, and a
    # re-provisioned volume then reads as `first_run`: the corpus is
    # republished AND every document CKB deleted while the manifest was gone
    # is permanently orphaned, because it is absent from the rows and absent
    # from the manifest, so it can never classify `deleted`. That state must
    # be reachable only by a deliberate act, never by a routine volume event.
    manifest_store_allow_local: bool = False

    # --- llm-module sink (Stage L) -----------------------------------------
    llm_module_base_url: str = ""
    llm_module_ingest_schema_version: Annotated[int, Field(ge=1)] = 1
    llm_module_max_request_bytes: Annotated[int, Field(ge=1)] = 8_388_608
    llm_module_connect_timeout: Annotated[float, Field(gt=0)] = 5.0
    llm_module_read_timeout: Annotated[float, Field(gt=0)] = 120.0
    llm_module_vault_secret_path: str = ""

    # --- this service's own durable volume ---------------------------------
    # Holds only the run lock (F2) and the deferred-deletion journal. Both
    # must survive a restart, and it must never be under a scrapped-data tree
    # (rule 1).
    #
    # REQUIRED, with no default, on purpose. A code default is what makes a
    # deployment that forgets to set it fail silently: the process would come
    # up writing to a path that is not the mounted volume, and nobody would
    # notice until a restart lost the deletion journal. Every deployment
    # surface sets it explicitly — docker-compose.yml and the chart both point
    # at /var/lib/content-external.
    content_work_dir: str

    # --- chunk geometry ----------------------------------------------------
    # chunk_profile is str, not Literal: CHUNK_PROFILES lives in
    # exporter/core/constants.py (B11) and is the single source of truth for
    # the preset list. A Literal here would be a second place to edit for
    # every preset change and the two would drift. B11 adds the membership
    # validator against CHUNK_PROFILES.
    chunk_profile: str = Field(default="azure_native", min_length=1)
    require_metadata_sidecar: bool = True
    diff_on_missing_manifest: Literal["first_run", "fail"] = "first_run"
    # le=16 enforces F16's documented cap, which makes A16's memory-product
    # assertion a second line of defence rather than the only one.
    export_concurrency: Annotated[int, Field(ge=1, le=16)] = 4
    bootstrap_checkpoint_every: Annotated[int, Field(ge=1)] = 250
    max_document_bytes: Annotated[int, Field(ge=1)] = 20_971_520
    max_chunks_per_document: Annotated[int, Field(ge=1)] = 5_000

    # --- CKB ---------------------------------------------------------------
    ruuter_internal: str = "http://ruuter-internal:8089"
    ckb_resql: str = "http://resql-ckb:8090/ckb"

    # --- reading CKB's own bucket — read-only credentials -------------------
    s3_endpoint_url: str = ""
    s3_bucket_name: str = ""
    aws_access_key_id: SecretStr = SecretStr("")
    aws_secret_access_key: SecretStr = SecretStr("")
    aws_region: str = ""

    # --- the external destination — a DIFFERENT bucket and credentials ------
    external_s3_endpoint_url: str = ""
    external_s3_bucket_name: str = ""

    # --- Vault -------------------------------------------------------------
    # No default for vault_secret_path: the documented value names the Azure
    # path, and Azure is Stage H. A default that is wrong for the s3 and
    # llm_module deployments is worse than no default. The Stage-H example
    # lives in content-external/README.md instead.
    vault_addr: str = "http://vault:8200"
    vault_token_path: str = "/agent/out/token"
    vault_secret_path: str = ""
    azure_storage_container: str = "agency-content"

    @field_validator("content_work_dir")
    @classmethod
    def _check_work_dir(cls, value: str) -> str:
        normalised = normalise_container_path(value, variable="CONTENT_WORK_DIR")
        assert_not_under_scrapped_data(normalised, variable="CONTENT_WORK_DIR")
        return normalised

    # ----------------------------------------------------------------------
    # A12 — the startup validation matrix.
    #
    # Four combinations must fail HERE rather than at first commit. The reason
    # is specific and not general tidiness: a run that discovers a missing
    # manifest store after pushing 2,000 documents has published them with no
    # record of having done so, which is indistinguishable from never having
    # run. Every message names the variable to set, because the person reading
    # it is looking at a crashed container and not at the design document.
    #
    # mode="after" runs in definition order on the constructed model. Nothing
    # here mutates, which is what makes it compatible with frozen=True.
    # ----------------------------------------------------------------------

    @model_validator(mode="after")
    def _check_manifest_store_for_llm_module(self) -> "Settings":
        """Matrix row 2: `llm_module` with no manifest store refuses to start.

        The llm-module is an HTTP API with nowhere to PUT a manifest, and the
        manifest cannot be delegated to it: it would make a failed push
        indistinguishable from a successful one, which is the single property
        this design's correctness rests on.
        """
        if self.content_sink == "llm_module" and not self.manifest_store_backend:
            raise ValueError(
                "CONTENT_SINK=llm_module requires MANIFEST_STORE_BACKEND to be "
                "set (s3 or azure_blob). That sink is an HTTP API and has "
                "nowhere to hold manifest.json, so the manifest store cannot "
                "be inherited from it the way an object-store deployment "
                "inherits its own store. Set MANIFEST_STORE_BACKEND plus "
                "MANIFEST_STORE_BUCKET (and MANIFEST_STORE_ENDPOINT_URL for "
                "s3) — a dedicated bucket or prefix on the S3-compatible "
                "endpoint CKB already runs is the obvious choice."
            )
        return self

    @model_validator(mode="after")
    def _check_manifest_store_is_complete(self) -> "Settings":
        """An explicitly-set manifest store must be usable.

        Not a row of the documented matrix, but implied by it: without this,
        `MANIFEST_STORE_BACKEND=s3` alone satisfies row 2 while naming a store
        that cannot be addressed, and the refusal lands at first commit after
        all — exactly what the matrix exists to prevent.

        `local` needs nothing: it resolves to CONTENT_WORK_DIR, which is
        already required and already asserted.
        """
        if not self.manifest_store_backend or self.manifest_store_backend == "local":
            return self
        missing = []
        if not self.manifest_store_bucket:
            missing.append("MANIFEST_STORE_BUCKET")
        if self.manifest_store_backend == "s3" and not self.manifest_store_endpoint_url:
            missing.append("MANIFEST_STORE_ENDPOINT_URL")
        if missing:
            raise ValueError(
                f"MANIFEST_STORE_BACKEND={self.manifest_store_backend} is set "
                f"but {' and '.join(missing)} "
                f"{'are' if len(missing) > 1 else 'is'} empty. A manifest "
                "store that cannot be addressed is worse than none: the run "
                "would publish documents and then fail to record that it had."
            )
        return self

    @model_validator(mode="after")
    def _check_local_manifest_store_is_dev_only(self) -> "Settings":
        """Matrix row 4: `local` is refused without the explicit dev flag.

        D19. A filesystem manifest is fine for local work and dangerous in a
        deployment, and the danger is not that it is lost — it is what being
        lost *looks like*.
        """
        if (
            self.manifest_store_backend == "local"
            and not self.manifest_store_allow_local
        ):
            raise ValueError(
                "MANIFEST_STORE_BACKEND=local is development only and "
                "requires MANIFEST_STORE_ALLOW_LOCAL=true. A filesystem "
                "manifest on CONTENT_WORK_DIR means a re-provisioned volume "
                "reads as a first run: the entire corpus is republished, and "
                "every document CKB deleted while the manifest was gone is "
                "permanently orphaned — absent from the rows and absent from "
                "the manifest, it can never classify as deleted. That must be "
                "reachable by a deliberate act, never by a routine volume "
                "event. For any real deployment set an object store instead."
            )
        return self

    @model_validator(mode="after")
    def _check_llm_module_endpoint_and_credential(self) -> "Settings":
        """Matrix row 3: `llm_module` needs a base URL and a credential path.

        TLS is required and certificate verification is never disabled, so an
        `http://` base URL is refused here rather than discovered when the
        first document leaves the cluster — the push body IS Estonian
        government document text (L14).
        """
        if self.content_sink != "llm_module":
            return self
        missing = []
        if not self.llm_module_base_url:
            missing.append("LLM_MODULE_BASE_URL")
        if not self.llm_module_vault_secret_path:
            missing.append("LLM_MODULE_VAULT_SECRET_PATH")
        if missing:
            raise ValueError(
                "CONTENT_SINK=llm_module requires "
                f"{' and '.join(missing)} to be set. Without "
                "a base URL there is nowhere to push; without a Vault secret "
                "path there is no credential, and the run would report "
                "`failed` every hour with the reason only in stdout."
            )
        if not self.llm_module_base_url.startswith("https://"):
            raise ValueError(
                "LLM_MODULE_BASE_URL must use https://, got "
                f"{sanitize_sensitive_text(self.llm_module_base_url)!r}. The "
                "request body carries Estonian government document text and "
                "the llm-module is outside bykstack, so TLS is required and "
                "certificate verification is never disabled."
            )
        return self

    @property
    def work_dir_path(self) -> Path:
        """The work dir as a real Path, for the components that do I/O."""
        return Path(self.content_work_dir)

    # ----------------------------------------------------------------------
    # A12 — manifest-store resolution (D16, D17).
    #
    # Read these, never the raw MANIFEST_STORE_* fields, anywhere that builds
    # the manifest store. Inheritance is all-or-nothing and the module
    # docstring says why; splitting it field-by-field would silently relocate
    # an object-store deployment's manifest and break verification 20.
    # ----------------------------------------------------------------------

    @property
    def manifest_store_is_inherited(self) -> bool:
        """True when the manifest store IS the object-store sink's own store.

        Only reachable on the object-store sink: an empty backend is refused
        for `llm_module` by _check_manifest_store_for_llm_module, so this
        needs no sink branch of its own.
        """
        return not self.manifest_store_backend

    @property
    def resolved_manifest_store_backend(self) -> str:
        if self.manifest_store_is_inherited:
            return self.content_external_store_backend
        return self.manifest_store_backend

    @property
    def resolved_manifest_store_endpoint_url(self) -> str:
        if self.manifest_store_is_inherited:
            return self.external_s3_endpoint_url
        return self.manifest_store_endpoint_url

    @property
    def resolved_manifest_store_bucket(self) -> str:
        """The bucket (s3) or container (azure_blob) holding manifest.json."""
        if not self.manifest_store_is_inherited:
            return self.manifest_store_bucket
        if self.content_external_store_backend == "azure_blob":
            return self.azure_storage_container
        return self.external_s3_bucket_name

    @property
    def resolved_manifest_store_prefix(self) -> str:
        """CONTENT_EXTERNAL_PREFIX when inherited — NOT MANIFEST_STORE_PREFIX.

        This is the clause verification 20 turns on. See the module docstring:
        `content-manifests` is a recommendation for an llm_module deployment,
        not a default that may relocate an object-store deployment's manifest.
        """
        if self.manifest_store_is_inherited:
            return self.content_external_prefix
        return self.manifest_store_prefix


def load_settings() -> Settings:
    """Build Settings from the environment, or die.

    A pydantic ValidationError echoes the offending input value, and some of
    these fields are URLs that can legitimately carry userinfo, so the message
    is sanitised before it reaches a log or a crash trace. `from None` rather
    than `from exc`: chaining would re-print the unsanitised ValidationError
    in the traceback.
    """
    try:
        return Settings()  # pyright: ignore[reportCallIssue]
    except ValidationError as exc:
        raise ConfigurationError(
            "content-external configuration is invalid:\n"
            + sanitize_sensitive_text(str(exc))
        ) from None


def assert_work_dir_usable(settings: Settings) -> Path:
    """Create the work dir if absent, re-apply rule 1 to its REAL path, and
    prove it is writable.

    Deliberately NOT a field validator: a validator that touches the
    filesystem makes every unit test need a real directory, and fails outright
    on Windows, where "/var/lib/content-external" is not an absolute path.

    Resolving is what closes the symlink hole. CONTENT_WORK_DIR=/work reads
    clean against the pure predicate, but /work -> /scrapped-data/work is rule
    1 violated, and only the realpath can tell the difference. Running the
    identical predicate in both places is what makes the check hard to bypass.

    A14 extends this function with the exclusive-flock self-test, replacing
    the write probe below rather than adding a second, weaker check beside it.
    """
    path = settings.work_dir_path
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigurationError(
            f"CONTENT_WORK_DIR={settings.content_work_dir} could not be created: {exc}"
        ) from exc

    real = path.resolve()
    try:
        assert_not_under_scrapped_data(
            real.as_posix(), variable="CONTENT_WORK_DIR (resolved)"
        )
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc

    probe = real / ".content-external-write-probe"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise ConfigurationError(
            f"CONTENT_WORK_DIR={settings.content_work_dir} is not writable: "
            f"{exc}. It holds the run lock and the deletion journal."
        ) from exc
    return real


def _is_secret_field(name: str, annotation: object) -> bool:
    if annotation is SecretStr:
        return True
    if name in _ADDRESS_NOT_CREDENTIAL:
        return False
    return any(part in name for part in _SECRET_NAME_PARTS)


def _has_value(value: object) -> bool:
    if isinstance(value, SecretStr):
        return bool(value.get_secret_value())
    return bool(value)


def redacted_settings(settings: Settings) -> dict[str, str]:
    """Every field, safe to log.

    Secrets become [set]/[unset] rather than a flat [redacted]: the question
    an operator asks of a startup echo is "is the credential there at all?",
    and a uniform [redacted] cannot answer it — which pushes people to
    `docker exec ... env`, which prints the real value. Presence is not a
    secret.

    Non-secret strings still go through sanitize_sensitive_text, because a URL
    can carry userinfo (https://user:pw@llm.example).
    """
    out: dict[str, str] = {}
    for name, field in type(settings).model_fields.items():
        value = getattr(settings, name)
        if _is_secret_field(name, field.annotation):
            out[name] = _SET if _has_value(value) else _UNSET
        elif isinstance(value, str):
            out[name] = sanitize_sensitive_text(value) if value else _UNSET
        else:
            out[name] = str(value)
    return out


def log_redacted_config(settings: Settings) -> None:
    """One sorted key=value line at INFO, behind a stable prefix.

    One line rather than one per field so that
    `docker logs | grep 'content-external config'` yields a single complete
    record that can be diffed between two deployments.
    """
    fields = redacted_settings(settings)
    logger.info(
        "%s %s",
        CONFIG_LOG_PREFIX,
        " ".join(f"{name}={value}" for name, value in sorted(fields.items())),
    )
