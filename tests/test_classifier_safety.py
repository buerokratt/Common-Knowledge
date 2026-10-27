"""L24 — the hourly ZIP job stays, enforced rather than written down.

The Global Classifier pulls `zipped/{agency}.zip` through `client/data/*` and
detects change through `agency.data_hash`. The llm-module stops consuming that
ZIP at the cutover, and "the llm-module doesn't pull it any more" reads to a
later reader like "nothing pulls it any more". Removing or disabling the job
would freeze `data_hash`, so the Classifier would compare equal hashes, report
no change, and go permanently stale — with no error anywhere. This module is
the tripwire for that inference, and for the six rules' write ban on the zip
surface (README, rules 2-5):

1. The job that produces the ZIP is still scheduled, hourly, and still calls
   `pipeline/zip`.
2. Nothing this service ships names the zip surface at all. It reads CKB's
   own Resql and bucket; it has no reason to mention `data_hash`, `zip_dirty`,
   `is_zipping`, `zipped_data_url`, `client/data/*` or `pipeline/zip`, so a
   mention is a review question before it is a bug.

A grep rather than an AST walk, unlike test_exporter_sink_resolution.py: the
surface is a set of names, not a branching shape, and it has to be checked in
YAML and the Dockerfile as well as Python. Prose (`*.md`) is excluded — the
README and the runbook explain these rules by naming them — and so is
`scripts/`, the measurement SQL, which writes synthetic rows to a disposable
local database and is never shipped.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = REPO_ROOT / "content-external"
SCHEDULER = REPO_ROOT / "DSL" / "CronManager" / "scheduler.yml"
ZIP_SCRIPT = REPO_ROOT / "DSL" / "CronManagerScripts" / "trigger_zipping.sh"
ZIP_FLOW = (
    REPO_ROOT / "DSL" / "Ruuter.internal" / "ckb" / "GET" / "pipeline" / "zip.yml"
)

# The job as it stands, which nothing in this feature may change (L24).
ZIP_JOB = "trigger_zipping"
ZIP_CRON = "0 0 */1 * * ?"

# The zip surface: the Classifier's contract (rules 2-5) and the job's names.
# `pipeline/zip` is the endpoint; `pipeline/zip.yml` is the flow's file name,
# which services/agency_guard.py cites as a design shape (A17) — naming the
# file is not calling the endpoint, so the file name is let through.
FORBIDDEN = re.compile(
    r"update[-_]zip[-_]dirty"
    r"|update[-_]data[-_]hash"
    r"|\bis_zipping\b"
    r"|\bzip_dirty\b"
    r"|zipped_data_url"
    r"|\bdata_hash\b"
    r"|client/data/"
    r"|pipeline/zip(?!\.yml)",
    re.IGNORECASE,
)

SCANNED_SUFFIXES = frozenset({".py", ".yml", ".yaml", ".toml", ".ini", ".sh"})
SKIPPED_DIRS = frozenset({"scripts", "__pycache__"})


def _shipped_files() -> list[Path]:
    """What this service ships: code, config and its Dockerfile."""
    found: list[Path] = []
    for path in SERVICE_ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(SERVICE_ROOT)
        if SKIPPED_DIRS.intersection(relative.parts[:-1]):
            continue
        if path.suffix in SCANNED_SUFFIXES or path.name == "Dockerfile":
            found.append(path)
    return sorted(found)


def _relative(path: Path) -> str:
    return path.relative_to(SERVICE_ROOT).as_posix()


def _mentions(text: str) -> list[str]:
    """`line: match` for every mention of the zip surface in `text`."""
    return [
        f"{number}: {match.group(0)}"
        for number, line in enumerate(text.splitlines(), start=1)
        for match in FORBIDDEN.finditer(line)
    ]


def _job_block(text: str, name: str) -> str | None:
    """The indented lines under `name:` at the top level of a CronManager
    file, or None when there is no such job. No YAML dependency: the file is
    a flat map of jobs, each with a few `key: value` lines."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.rstrip() == f"{name}:":
            block: list[str] = []
            for following in lines[index + 1 :]:
                if following and not following.startswith((" ", "\t")):
                    break
                block.append(following)
            return "\n".join(block)
    return None


# --- 1. the job stays ------------------------------------------------------


def test_the_hourly_zip_job_is_still_scheduled() -> None:
    block = _job_block(SCHEDULER.read_text(encoding="utf-8"), ZIP_JOB)

    assert block is not None, (
        f"L24: {ZIP_JOB} is gone from {SCHEDULER.relative_to(REPO_ROOT)}. The "
        "Global Classifier still consumes zipped/{agency}.zip; without this job "
        "agency.data_hash freezes and the Classifier goes stale silently. The "
        "llm-module no longer pulling the ZIP is not a reason to stop it."
    )
    assert f'trigger: "{ZIP_CRON}"' in block, (
        f"L24: {ZIP_JOB} must keep its hourly schedule {ZIP_CRON!r}; no PR in "
        "the content-external feature may re-schedule the zip path."
    )
    assert "trigger_zipping.sh" in block


def test_the_zip_job_still_drives_the_zip_flow() -> None:
    assert ZIP_SCRIPT.is_file()
    assert "/pipeline/zip" in ZIP_SCRIPT.read_text(encoding="utf-8")
    assert ZIP_FLOW.is_file(), (
        "L24: pipeline/zip.yml builds the Classifier's ZIP and must stay."
    )


# --- 2. the exporter never touches the zip surface -------------------------


def test_the_service_is_present() -> None:
    """Guards the guard: a moved service would make every check below pass by
    scanning nothing at all."""
    files = {_relative(path) for path in _shipped_files()}
    assert "Dockerfile" in files
    assert "exporter/api/config.py" in files


@pytest.mark.parametrize("path", _shipped_files(), ids=_relative)
def test_nothing_shipped_names_the_zip_surface(path: Path) -> None:
    mentions = _mentions(path.read_text(encoding="utf-8"))
    assert not mentions, (
        f"content-external/{_relative(path)} names the zip surface:\n  "
        + "\n  ".join(mentions)
        + "\n\nRules 2-5 (README) and L24: this service never writes zipped/, "
        "any agency column, or calls update-zip-dirty / update-data-hash, and "
        "never claims is_zipping. It has no reason to name them."
    )


def test_a_mention_would_be_caught() -> None:
    """The check's own regression test."""
    leaky = (
        "requests.post(f'{RUUTER}/agency/update-data-hash')\n"
        "row['is_zipping']\n"
        "SELECT data_hash FROM agency\n"
        "GET /ckb/client/data/import\n"
        "requests.get(f'{RUUTER_INTERNAL}/pipeline/zip')\n"
    )
    assert len(_mentions(leaky)) == 5
    assert _mentions("the drain-and-recurse shape pipeline/zip.yml uses") == []
    # A longer identifier that merely contains a name is not a mention.
    assert _mentions("original_data_hash, metadata_hashes") == []


def test_a_removed_job_would_be_caught() -> None:
    without = 'trigger_scheduler:\n  trigger: "0 */5 * * * ?"\n'
    assert _job_block(without, ZIP_JOB) is None
    moved = f'{ZIP_JOB}:\n  trigger: "0 0 */6 * * ?"\n  type: exec\n'
    block = _job_block(moved, ZIP_JOB)
    assert block is not None
    assert f'trigger: "{ZIP_CRON}"' not in block
