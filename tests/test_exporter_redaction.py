"""F13 — no document text leaves through any side channel.

A sentinel is planted in every document's text and in its sidecar title, and
then a sequence of runs is driven through the CLI over the in-memory fakes:
a first publish, a shrink that deletes a tail, a deletion, a destination
that refuses a write, and a source that fails a read. Afterwards the sentinel
must appear nowhere except where it belongs — the destination's own objects.
Not in a log record at any level, not in a traceback, not in a report, not in
the last-run record, not in a deletion journal or its mirror, not in the
manifest, and not on the CLI's stdout or stderr.

This is the unit-level half of verification 27 ("capture a full run's logs
and error reports and confirm no document text appears in any of them"); the
staging half runs against the real llm-module.
"""

import logging
from dataclasses import replace
from pathlib import Path

import pytest

from exporter.api.config import Settings
from exporter.core.ids import document_metadata_key
from exporter.core.redaction import sanitize_sensitive_text
from exporter.run_sync import main
from exporter_fakes import (
    FakeDocumentSource,
    InMemoryStore,
    RecordingSink,
    make_row,
    patch_wiring,
)

AGENCY = "agency-1"
# ASCII, so it cannot hide behind a JSON \u escape or NFC normalisation.
SENTINEL = "ZQXSENTINELQXZ"


def body(sentences: int) -> str:
    return " ".join(
        f"Lause {i}: {SENTINEL} räägib, kuidas taotlus esitada."
        for i in range(sentences)
    )


def sidecar(document_id: str) -> dict[str, object]:
    return {
        "source_url": f"https://www.eesti.ee/{document_id}",
        "title": f"{SENTINEL} pealkiri õäöü",
    }


def test_no_document_text_escapes_any_side_channel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    work_dir = tmp_path / "work"
    monkeypatch.setenv("CONTENT_WORK_DIR", work_dir.as_posix())

    source = FakeDocumentSource()
    for document_id in ("d1", "d2", "d3"):
        source.put(make_row(document_id), body(30), sidecar=sidecar(document_id))
    destination = InMemoryStore()
    manifest_client = InMemoryStore()
    patch_wiring(
        monkeypatch,
        source=source,
        sink=RecordingSink(store=destination),
        manifest_client=manifest_client,
        checkpoint_every=1,  # first-run checkpoints are a channel too
    )

    def move(document_id: str, at: str, text: str | None = None, **row: object) -> None:
        current = source.documents[document_id]
        source.put(
            replace(current.row, updated_at=at, **row),  # type: ignore[arg-type]
            current.text if text is None else text,
            sidecar=sidecar(document_id),
        )

    def run(run_id: str) -> int:
        return main(["--agency-id", AGENCY, "--run-id", run_id])

    with caplog.at_level(logging.DEBUG):
        codes = [run("r1")]  # publish, with first-run checkpoints
        move("d1", "2026-10-02T09:00:00Z", body(2))  # shrinks: a tail delete
        move("d2", "2026-10-02T09:00:00Z", is_deleted=True)  # a document delete
        codes.append(run("r2"))
        # The destination refuses d3's record: a per-document failure.
        destination.fail_keys.add(document_metadata_key("content", AGENCY, "d3"))
        move("d3", "2026-10-03T09:00:00Z", body(25))
        codes.append(run("r3"))
        destination.fail_keys.clear()
        # The source fails a read: the whole run fails.
        source.fail_reads.add("d3")
        codes.append(run("r4"))

    assert codes == [0, 0, 0, 1]
    # The sentinel really was published — the check below is not vacuous.
    assert any(SENTINEL.encode() in obj.payload for obj in destination.objects.values())

    out, err = capsys.readouterr()
    # Each scenario really happened, so each channel really was exercised.
    assert '"checkpoints_committed": 3' in out
    assert '"deleted_count": 1' in out
    assert '"publish_failed_count": 1' in out
    assert '"error_type": "StoreError"' in out
    channels: dict[str, str] = {"stdout": out, "stderr": err, "logs": caplog.text}
    for record in caplog.records:
        assert record.exc_info is None, f"traceback logged by {record.name}"
        channels[f"log args {record.name}"] = repr(record.args)
    for path in work_dir.rglob("*"):
        if path.is_file():
            channels[str(path)] = path.read_bytes().decode("utf-8", errors="replace")
    for key, obj in manifest_client.objects.items():
        channels[f"manifest store {key}"] = obj.payload.decode("utf-8")

    assert any("_deferred" in name for name in channels), "no journal was written"
    leaked = [name for name, text in channels.items() if SENTINEL in text]
    assert leaked == []


# --- signed URLs in exception text ---------------------------------------------


@pytest.mark.parametrize(
    ("url", "secrets"),
    [
        (
            "https://bucket.s3.eu-north-1.amazonaws.com/a/b.md"
            "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIAEXAMPLE%2F2026"
            "&X-Amz-Security-Token=FQoGZXIvYXdzEXAMPLE&X-Amz-Signature=deadbeef01",
            ["AKIAEXAMPLE", "FQoGZXIvYXdzEXAMPLE", "deadbeef01"],
        ),
        (
            "https://bucket.s3.amazonaws.com/a.md"
            "?AWSAccessKeyId=AKIAOLDSTYLE&Expires=1&Signature=c2lnbmF0dXJl",
            ["AKIAOLDSTYLE", "c2lnbmF0dXJl"],
        ),
        (
            "https://acct.blob.core.windows.net/c/a.md"
            "?sv=2024&se=2026&sig=QXp1cmVTaWc%3D",
            ["QXp1cmVTaWc"],
        ),
        (
            "https://storage.googleapis.com/b/a.md"
            "?X-Goog-Credential=svc%40p.iam&X-Goog-Signature=0123abcd",
            ["svc%40p.iam", "0123abcd"],
        ),
    ],
    ids=["s3-sigv4", "s3-sigv2", "azure-sas", "gcs-v4"],
)
def test_a_signed_url_in_an_error_message_is_redacted(
    url: str, secrets: list[str]
) -> None:
    """F13: "never a signed URL". A requests or botocore error quotes the URL
    it failed on, and the message reaches send_error and the run log."""
    message = sanitize_sensitive_text(f"HTTPSConnectionPool: Max retries for {url}")
    assert [secret for secret in secrets if secret in message] == []
    assert "[redacted]" in message
