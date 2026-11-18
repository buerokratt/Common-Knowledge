"""Static checks for the content-external gate on the chatbot's LLM module toggle.

The three content-external trigger flows must ask the content-export-gate
template first, and no path through a flow may reach content-external unless
the gate said run. Ruuter flows are named-step state machines: a step goes to
its `next`, a switch to one of its branches or its default, a failed call to
its `error`, and a step without `next` falls through to the following step.
"""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
RUUTER_INTERNAL = REPO_ROOT / "DSL" / "Ruuter.internal" / "ckb"
FLOWS_DIR = RUUTER_INTERNAL / "GET" / "pipeline"
TEMPLATE = RUUTER_INTERNAL / "TEMPLATES" / "pipeline" / "content-export-gate.yml"
QUERY = (
    REPO_ROOT
    / "DSL"
    / "Resql"
    / "users"
    / "GET"
    / "config"
    / "get_domain_configuration.sql"
)
CONSTANTS = REPO_ROOT / "constants.ini"

FLOWS = {
    "content-export.yml": "export",
    "content-drain.yml": "drain",
    "content-reconcile.yml": "reconcile",
}
GATE_TEMPLATE_PATH = "[#CKB_PROJECT_LAYER]/pipeline/content-export-gate"
CONTENT_EXTERNAL = "[#CKB_CONTENT_EXTERNAL]"

EXPECTED_DECISIONS = {
    "chatbot_domain_not_configured": False,
    "chatbot_domain_unknown": False,
    "toggle_unreadable": False,
    "llm_module_active": False,
    "llm_module_inactive": True,
}


def load_steps(path: Path) -> dict[str, dict[str, Any]]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {name: step for name, step in doc.items() if name != "declaration"}


def successors(steps: dict[str, dict[str, Any]], name: str) -> Iterator[str]:
    step = steps[name]
    for branch in step.get("switch", []):
        yield branch["next"]
    if "error" in step:
        yield step["error"]
    if "next" in step:
        yield step["next"]
    else:
        names = list(steps)
        index = names.index(name)
        if index + 1 < len(names):
            yield names[index + 1]


def reachable(steps: dict[str, dict[str, Any]], start: str) -> set[str]:
    seen: set[str] = set()
    pending = [start]
    while pending:
        name = pending.pop()
        if name == "end" or name in seen:
            continue
        seen.add(name)
        pending.extend(successors(steps, name))
    return seen


def calls_content_external(step: dict[str, Any]) -> bool:
    return CONTENT_EXTERNAL in str(step.get("args", {}).get("url", ""))


@pytest.mark.parametrize("flow", FLOWS)
def test_flow_asks_the_gate_first(flow: str) -> None:
    steps = load_steps(FLOWS_DIR / flow)
    first, second = list(steps)[:2]

    assert steps[first]["template"] == GATE_TEMPLATE_PATH
    assert steps[first]["requestType"] == "templates"
    assert steps[first]["body"] == {"operation": FLOWS[flow]}
    assert steps[first]["next"] == second

    gate_result = steps[first]["result"]
    branches = steps[second]["switch"]
    assert len(branches) == 1
    assert branches[0]["condition"] == f"${{{gate_result}.run === true}}"


@pytest.mark.parametrize("flow", FLOWS)
def test_only_the_run_branch_reaches_content_external(flow: str) -> None:
    steps = load_steps(FLOWS_DIR / flow)
    first, second = list(steps)[:2]
    # The gate step can only lead to the switch, so the switch's two exits are
    # the only ways on: its run branch, and its default.
    assert list(successors(steps, first)) == [second]

    run_path = reachable(steps, steps[second]["switch"][0]["next"])
    assert any(calls_content_external(steps[name]) for name in run_path)

    skip_path = reachable(steps, steps[second]["next"])
    leaks = [name for name in skip_path if calls_content_external(steps[name])]
    assert not leaks, f"{flow}: reachable without the gate: {leaks}"


@pytest.mark.parametrize("flow", FLOWS)
def test_skip_returns_the_gate(flow: str) -> None:
    steps = load_steps(FLOWS_DIR / flow)
    first, second = list(steps)[:2]
    gate_result = steps[first]["result"]
    skip_path = reachable(steps, steps[second]["next"])
    returns = [steps[name]["return"] for name in skip_path if "return" in steps[name]]
    assert returns == [f"${{{gate_result}}}"]


def gate_decisions(steps: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map each step that assigns the template's result to that result."""
    return {
        name: step["assign"]["gate"]
        for name, step in steps.items()
        if "gate" in step.get("assign", {})
    }


def test_template_covers_the_decision_table() -> None:
    decisions = gate_decisions(load_steps(TEMPLATE))
    table = {gate["reason"]: gate["run"] for gate in decisions.values()}
    assert table == EXPECTED_DECISIONS
    for gate in decisions.values():
        assert gate["status"] == ("allowed" if gate["run"] else "skipped")
        assert gate["operation"] == "${operation}"


def test_every_decision_is_reachable_and_returned() -> None:
    steps = load_steps(TEMPLATE)
    everything = reachable(steps, next(iter(steps)))
    for name in gate_decisions(steps):
        assert name in everything, f"{name} is unreachable"
        assert reachable(steps, name) == {name, "returnGate"}
    assert steps["returnGate"]["return"] == "${gate}"


def test_template_reads_the_toggle_for_the_configured_domain() -> None:
    steps = load_steps(TEMPLATE)
    calls = [step for step in steps.values() if "call" in step]
    assert len(calls) == 1
    (call,) = calls
    assert call["call"] == "http.get"
    assert call["args"]["url"] == ("[#CKB_USERS_RESQL]/config/get_domain_configuration")
    assert call["args"]["query"]["key"] == "llm_module_active"
    assert "chatbotDomain" in call["args"]["query"]["domain"]

    decisions = gate_decisions(steps)
    by_reason = {gate["reason"]: name for name, gate in decisions.items()}
    assert call["error"] == by_reason["toggle_unreadable"]


def test_template_skips_before_the_call_when_no_domain_is_configured() -> None:
    steps = load_steps(TEMPLATE)
    first = next(iter(steps))
    assert (
        steps[first]["assign"]["chatbotDomain"]
        == "[#BUROKRATT_CHATBOT_WIDGET_DOMAIN_ID]"
    )

    by_reason = {gate["reason"]: name for name, gate in gate_decisions(steps).items()}
    check = steps[steps[first]["next"]]
    (branch,) = check["switch"]
    assert branch["next"] == by_reason["chatbot_domain_not_configured"]
    assert "call" in steps[check["next"]]


def test_only_the_value_true_marks_the_llm_module_active() -> None:
    steps = load_steps(TEMPLATE)
    by_reason = {gate["reason"]: name for name, gate in gate_decisions(steps).items()}
    deciding = [
        step
        for step in steps.values()
        for branch in step.get("switch", [])
        if branch["next"] == by_reason["llm_module_active"]
    ]
    assert len(deciding) == 1
    (step,) = deciding
    assert step["switch"][0]["condition"].endswith(".value === 'true'}")
    # A null value (no row for the domain) falls to the default: run.
    assert step["next"] == by_reason["llm_module_inactive"]


def query_declaration() -> dict[str, Any]:
    match = re.match(r"\s*/\*(.*?)\*/", QUERY.read_text(encoding="utf-8"), re.S)
    assert match, "the Resql declaration block is missing"
    declaration: dict[str, Any] = yaml.safe_load(match.group(1))["declaration"]
    return declaration


def test_query_declares_its_parameters_and_response() -> None:
    declaration = query_declaration()
    assert declaration["method"] == "get"
    assert declaration["namespace"] == "config"
    params = {field["field"] for field in declaration["allowlist"]["query"]}
    assert params == {"key", "domain"}
    fields = {field["field"] for field in declaration["response"]["fields"]}
    assert fields == {"domain_known", "value"}

    body = QUERY.read_text(encoding="utf-8").split("*/", 1)[1]
    assert set(re.findall(r"(?<!:):(\w+)", body)) == params


def constants() -> dict[str, str]:
    pairs = (
        line.split("=", 1)
        for line in CONSTANTS.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    return {key: value for key, value in pairs}


def test_domain_constant_ships_empty() -> None:
    # Empty in the repo: the domain is per deployment, and empty skips.
    assert constants()["BUROKRATT_CHATBOT_WIDGET_DOMAIN_ID"] == ""


@pytest.mark.parametrize("path", [TEMPLATE, *(FLOWS_DIR / flow for flow in FLOWS)])
def test_every_placeholder_is_defined(path: Path) -> None:
    used = set(re.findall(r"\[#(\w+)\]", path.read_text(encoding="utf-8")))
    assert used <= constants().keys()


# --- the timer's two flows: sync (manual re-check) and status (polled) ------

SYNC = FLOWS_DIR / "content-export-sync.yml"
STATUS = FLOWS_DIR / "content-export-status.yml"


def content_external_urls(
    steps: dict[str, dict[str, Any]], names: set[str]
) -> set[str]:
    return {
        str(steps[name]["args"]["url"])
        for name in names
        if calls_content_external(steps[name])
    }


def test_sync_starts_the_timer_only_when_the_gate_says_run() -> None:
    steps = load_steps(SYNC)
    first, second = list(steps)[:2]
    assert steps[first]["template"] == GATE_TEMPLATE_PATH
    assert steps[first]["body"] == {"operation": "sync"}
    assert list(successors(steps, first)) == [second]
    (branch,) = steps[second]["switch"]
    assert branch["condition"] == f"${{{steps[first]['result']}.run === true}}"

    run_urls = content_external_urls(steps, reachable(steps, branch["next"]))
    skip_urls = content_external_urls(steps, reachable(steps, steps[second]["next"]))
    assert run_urls == {f"{CONTENT_EXTERNAL}/schedule/start"}
    # A skip, for any reason, only ever stops the timer.
    assert skip_urls == {f"{CONTENT_EXTERNAL}/schedule/stop"}


def test_sync_returns_the_gate_whatever_content_external_answers() -> None:
    steps = load_steps(SYNC)
    gate_result = steps[next(iter(steps))]["result"]
    for name in ("startSchedule", "stopSchedule"):
        assert reachable(steps, steps[name]["error"]) >= {"returnGate"}
        assert steps[name]["next"] == "returnGate"
    assert steps["returnGate"]["return"] == f"${{{gate_result}}}"


def test_status_reads_the_gate_and_calls_nothing_back() -> None:
    steps = load_steps(STATUS)
    first = next(iter(steps))
    assert steps[first]["template"] == GATE_TEMPLATE_PATH
    assert steps[first]["body"] == {"operation": "status"}
    assert not [name for name, step in steps.items() if "call" in step]
    assert steps[steps[first]["next"]]["return"] == f"${{{steps[first]['result']}}}"


@pytest.mark.parametrize("path", [SYNC, STATUS])
def test_timer_flow_placeholders_are_defined(path: Path) -> None:
    used = set(re.findall(r"\[#(\w+)\]", path.read_text(encoding="utf-8")))
    assert used <= constants().keys()
