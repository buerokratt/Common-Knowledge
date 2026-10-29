import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEDULER = REPO_ROOT / "DSL" / "CronManager" / "scheduler.yml"
SCRIPTS_DIR = REPO_ROOT / "DSL" / "CronManagerScripts"

# The Global Classifier's producer: it keeps the ZIP and agency.data_hash fresh.
# It must never change in this file.
EXPECTED_ZIPPING_BLOCK = b"""\
trigger_zipping:
  trigger: "0 0 */1 * * ?"
  type: exec
  command: "/bin/bash ../app/scripts/trigger_zipping.sh"
"""

TEXT = SCHEDULER.read_text(encoding="utf-8")
# Taken from the raw text so a file that no longer parses still yields entries.
JOB_NAMES = re.findall(r"(?m)^([^\s#][^:]*):", TEXT)


def test_file_parses_to_a_mapping_of_jobs() -> None:
    jobs = yaml.safe_load(TEXT)
    assert isinstance(jobs, dict)
    assert all(isinstance(job, dict) for job in jobs.values())
    # safe_load lets a repeated key silently replace the earlier entry.
    assert list(jobs) == JOB_NAMES


@pytest.mark.parametrize("name", JOB_NAMES)
def test_entry_loads(name: str) -> None:
    job = yaml.safe_load(TEXT)[name]
    assert {"trigger", "type"} <= job.keys()
    assert job["type"] in {"exec", "http"}
    if job["type"] == "exec":
        script = Path(job["command"].split()[-1]).name
        assert job["command"] == f"/bin/bash ../app/scripts/{script}"
        assert (SCRIPTS_DIR / script).is_file(), f"{script} is not in {SCRIPTS_DIR}"


@pytest.mark.parametrize("name", JOB_NAMES)
def test_trigger_is_quoted_and_disabled_or_quartz(name: str) -> None:
    trigger = yaml.safe_load(TEXT)[name]["trigger"]
    assert isinstance(trigger, str), f"quote the trigger, got {trigger!r}"
    assert trigger == "false" or len(trigger.split()) in (6, 7), trigger


def test_trigger_zipping_is_byte_identical() -> None:
    blocks = re.split(rb"(?m)^(?=\S)|^\r?\n", SCHEDULER.read_bytes())
    zipping = [block for block in blocks if block.startswith(b"trigger_zipping:")]
    assert len(zipping) == 1
    assert zipping[0] == EXPECTED_ZIPPING_BLOCK


def test_trigger_content_export_exists() -> None:
    assert "trigger_content_export" in yaml.safe_load(TEXT)
