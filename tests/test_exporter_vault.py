"""get_vault_secrets for the Azure Blob backend. HTTP is faked; no network."""

import json
from pathlib import Path
from typing import Any

import pytest
import requests

from exporter.api import config as config_module
from exporter.api.config import ConfigurationError, Settings, get_vault_secrets
from exporter.stores.base import StoreError

TOKEN = "hvs.first-token"
SECRET_PATH = "blob/connections/azure_blob/content"
VAULT_ADDR = "http://vault.test:8200"
ACCOUNT_URL = "https://exampleaccount.blob.core.windows.net"
ACCOUNT_KEY = "c2VjcmV0LWFjY291bnQta2V5"
SAS_TOKEN = "sv=2024-11-04&ss=b&sig=c2VjcmV0LXNpZw"
SECRET_VALUES = (TOKEN, ACCOUNT_KEY, SAS_TOKEN)


@pytest.fixture
def token_file(tmp_path: Path) -> Path:
    path = tmp_path / "token"
    path.write_text(f"{TOKEN}\n", encoding="utf-8")
    return path


def _settings(
    tmp_path: Path, token_file: Path, secret_path: str = SECRET_PATH
) -> Settings:
    return Settings(
        content_work_dir=str(tmp_path / "work"),
        vault_addr=VAULT_ADDR,
        vault_token_path=str(token_file),
        vault_secret_path=secret_path,
    )


def _response(
    status: int = 200, body: object = None, raw: bytes | None = None
) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode() if raw is None else raw
    return response


def _kv(**fields: object) -> requests.Response:
    return _response(body={"data": {"data": fields, "metadata": {"version": 1}}})


def _fake_vault(
    monkeypatch: pytest.MonkeyPatch, *outcomes: requests.Response | Exception
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    queue = list(outcomes)

    def fake_get(url: str, **kwargs: object) -> requests.Response:
        calls.append({"url": url, **kwargs})
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(config_module.requests, "get", fake_get)
    return calls


def _assert_no_secret_in(text: str) -> None:
    for value in SECRET_VALUES:
        assert value not in text


@pytest.mark.parametrize(
    ("kind", "value"), [("account_key", ACCOUNT_KEY), ("sas_token", SAS_TOKEN)]
)
def test_each_credential_kind_is_parsed_from_one_bounded_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    token_file: Path,
    kind: str,
    value: str,
) -> None:
    calls = _fake_vault(monkeypatch, _kv(account_url=ACCOUNT_URL, **{kind: value}))

    secrets = get_vault_secrets(_settings(tmp_path, token_file))

    assert secrets.account_url == ACCOUNT_URL
    assert secrets.credential.get_secret_value() == value
    assert secrets.credential_kind == kind
    _assert_no_secret_in(repr(secrets))
    [call] = calls
    assert call["url"] == f"{VAULT_ADDR}/v1/secret/data/{SECRET_PATH}"
    assert call["headers"] == {"X-Vault-Token": TOKEN}
    assert 0 < call["timeout"] <= 30


@pytest.mark.parametrize(
    "fields",
    [
        {
            "account_url": ACCOUNT_URL,
            "account_key": ACCOUNT_KEY,
            "sas_token": SAS_TOKEN,
        },
        {"account_url": ACCOUNT_URL},
        {"account_url": ACCOUNT_URL, "account_key": " ", "sas_token": ""},
        {"account_key": ACCOUNT_KEY},
        {"account_url": "  ", "sas_token": SAS_TOKEN},
        {"account_url": 7, "account_key": ACCOUNT_KEY},
        {
            "account_url": "https://REPLACE_ME.blob.core.windows.net",
            "account_key": "REPLACE_ME",
        },
        {"account_url": ACCOUNT_URL, "sas_token": f"REPLACE_ME{SAS_TOKEN}"},
        {"account_url": ACCOUNT_URL, "account_key": ACCOUNT_KEY, "note": "REPLACE_ME"},
        {"account_url": f"{ACCOUNT_URL}?{SAS_TOKEN}", "sas_token": SAS_TOKEN},
    ],
    ids=[
        "both-credentials",
        "neither-credential",
        "blank-credentials",
        "no-account-url",
        "blank-account-url",
        "non-string-account-url",
        "placeholder-secret",
        "placeholder-in-sas",
        "placeholder-in-extra-field",
        "query-in-account-url",
    ],
)
def test_unusable_secret_is_a_configuration_error_naming_no_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    token_file: Path,
    fields: dict[str, object],
) -> None:
    _fake_vault(monkeypatch, _kv(**fields))

    with pytest.raises(ConfigurationError) as excinfo:
        get_vault_secrets(_settings(tmp_path, token_file))

    _assert_no_secret_in(str(excinfo.value))
    _assert_no_secret_in(repr(excinfo.value))


def test_token_file_is_reread_on_every_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, token_file: Path
) -> None:
    secret = {"account_url": ACCOUNT_URL, "account_key": ACCOUNT_KEY}
    calls = _fake_vault(monkeypatch, _kv(**secret), _kv(**secret))
    settings = _settings(tmp_path, token_file)

    get_vault_secrets(settings)
    token_file.write_text("hvs.rotated-token", encoding="utf-8")
    get_vault_secrets(settings)

    assert [call["headers"]["X-Vault-Token"] for call in calls] == [
        TOKEN,
        "hvs.rotated-token",
    ]


def test_empty_secret_path_is_refused_before_any_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, token_file: Path
) -> None:
    calls = _fake_vault(monkeypatch)

    with pytest.raises(ConfigurationError, match="VAULT_SECRET_PATH"):
        get_vault_secrets(_settings(tmp_path, token_file, secret_path=""))

    assert calls == []


@pytest.mark.parametrize("content", [None, "", " \n"])
def test_missing_or_empty_token_file_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    token_file: Path,
    content: str | None,
) -> None:
    if content is None:
        token_file.unlink()
    else:
        token_file.write_text(content, encoding="utf-8")
    calls = _fake_vault(monkeypatch)

    with pytest.raises(ConfigurationError):
        get_vault_secrets(_settings(tmp_path, token_file))

    assert calls == []


# Every error body echoes a secret, so a message built from it would leak.
_LEAKY_BODY = {"errors": [ACCOUNT_KEY, TOKEN]}


@pytest.mark.parametrize("status", [403, 404])
def test_vault_refusal_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, token_file: Path, status: int
) -> None:
    _fake_vault(monkeypatch, _response(status, _LEAKY_BODY))

    with pytest.raises(ConfigurationError) as excinfo:
        get_vault_secrets(_settings(tmp_path, token_file))

    assert str(status) in str(excinfo.value)
    _assert_no_secret_in(str(excinfo.value))


@pytest.mark.parametrize(
    ("outcome", "permanent"),
    [
        (_response(500, _LEAKY_BODY), False),
        (_response(503, _LEAKY_BODY), False),
        (_response(429, _LEAKY_BODY), False),
        (requests.ReadTimeout("read timed out"), False),
        (requests.ConnectTimeout("connect timed out"), False),
        (requests.ConnectionError("connection refused"), False),
        (_response(400, _LEAKY_BODY), True),
        (_response(401, _LEAKY_BODY), True),
        (_response(405, _LEAKY_BODY), True),
        (_response(raw=f"<html>{ACCOUNT_KEY}</html>".encode()), True),
        (_response(body=[ACCOUNT_KEY]), True),
        (_response(body={"data": ACCOUNT_KEY}), True),
        (_response(body={"data": {"data": [ACCOUNT_KEY]}}), True),
    ],
    ids=[
        "500",
        "503",
        "429",
        "read-timeout",
        "connect-timeout",
        "connection-error",
        "400",
        "401",
        "405",
        "not-json",
        "json-list",
        "data-not-object",
        "inner-data-not-object",
    ],
)
def test_vault_failure_is_a_store_error_retryable_only_when_retrying_can_help(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    token_file: Path,
    outcome: requests.Response | Exception,
    permanent: bool,
) -> None:
    _fake_vault(monkeypatch, outcome)

    with pytest.raises(StoreError) as excinfo:
        get_vault_secrets(_settings(tmp_path, token_file))

    assert excinfo.value.permanent is permanent
    _assert_no_secret_in(str(excinfo.value))
    _assert_no_secret_in(repr(excinfo.value))
