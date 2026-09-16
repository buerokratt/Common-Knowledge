"""A16 — the memory product, asserted against the container's own limit.

Pure: temp files stand in for the cgroup pseudo-files, so these run on any
platform and need no container.

The thing under test is a multiplication that nothing previously performed.
EXPORT_CONCURRENCY is documented at 4 with a cap of 16 (F16) and
MAX_DOCUMENT_BYTES at 20 MB (E9), each in its own place, and peak memory is
their product times ~3. The failure mode is why it is a startup refusal
rather than a documented note: an OOMKill on an INCREMENTAL run never
converges, because manifest checkpointing is first-run-only by design (F15),
so the run dies at the same document every hour committing nothing — and
F19's systemic-failure abort does not catch it, an OOMKill not being a sink
failure.
"""

from pathlib import Path

import pytest

from exporter.api.config import (
    MEMORY_BASE_OVERHEAD_BYTES,
    MEMORY_PER_DOCUMENT_FACTOR,
    ConfigurationError,
    Settings,
    assert_memory_budget,
    memory_budget_bytes,
    read_container_memory_limit_bytes,
)

MIB = 1024 * 1024


def _settings(**overrides: object) -> Settings:
    kwargs: dict[str, object] = {"content_work_dir": "/var/lib/content-external"}
    kwargs.update(overrides)
    return Settings(**kwargs)  # pyright: ignore[reportArgumentType, reportCallIssue]


def _limit_file(tmp_path: Path, contents: str, name: str = "memory.max") -> Path:
    path = tmp_path / name
    path.write_text(contents, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# read_container_memory_limit_bytes
# --------------------------------------------------------------------------


def test_cgroup_v2_numeric_limit_is_read(tmp_path: Path) -> None:
    limit = _limit_file(tmp_path, "536870912\n")
    assert read_container_memory_limit_bytes([limit]) == 536870912


def test_cgroup_v2_max_means_unlimited(tmp_path: Path) -> None:
    """v2 writes the literal string when there is no limit."""
    limit = _limit_file(tmp_path, "max\n")
    assert read_container_memory_limit_bytes([limit]) is None


def test_cgroup_v1_numeric_limit_is_read(tmp_path: Path) -> None:
    limit = _limit_file(tmp_path, "268435456", name="memory.limit_in_bytes")
    assert read_container_memory_limit_bytes([limit]) == 268435456


def test_cgroup_v1_sentinel_means_unlimited(tmp_path: Path) -> None:
    """v1 writes PAGE_COUNTER_MAX rather than a word, and its exact value
    varies with kernel and page size — hence a threshold, not an equality."""
    limit = _limit_file(tmp_path, "9223372036854771712", name="memory.limit_in_bytes")
    assert read_container_memory_limit_bytes([limit]) is None


def test_an_absent_file_yields_none(tmp_path: Path) -> None:
    assert read_container_memory_limit_bytes([tmp_path / "nope"]) is None


def test_no_candidates_at_all_yields_none() -> None:
    assert read_container_memory_limit_bytes([]) is None


def test_candidates_are_tried_in_order(tmp_path: Path) -> None:
    """v2 before v1: a host running both must not be read through the legacy
    hierarchy, which can carry a different value."""
    v1 = _limit_file(tmp_path, "111111", name="memory.limit_in_bytes")
    v2 = _limit_file(tmp_path, "222222", name="memory.max")
    assert read_container_memory_limit_bytes([v2, v1]) == 222222


def test_an_unreadable_candidate_falls_through_to_the_next(tmp_path: Path) -> None:
    v1 = _limit_file(tmp_path, "333333", name="memory.limit_in_bytes")
    assert read_container_memory_limit_bytes([tmp_path / "missing", v1]) == 333333


@pytest.mark.parametrize("contents", ["", "   ", "not-a-number", "12.5"])
def test_unparseable_contents_do_not_raise(tmp_path: Path, contents: str) -> None:
    """A cgroup layout this does not recognise must degrade to "unknown", not
    take the service down — the assertion is a safety net, not a dependency."""
    assert read_container_memory_limit_bytes([_limit_file(tmp_path, contents)]) is None


def test_a_zero_limit_is_not_treated_as_a_limit(tmp_path: Path) -> None:
    """0 would otherwise make every configuration refuse to start."""
    assert read_container_memory_limit_bytes([_limit_file(tmp_path, "0")]) is None


# --------------------------------------------------------------------------
# memory_budget_bytes — the product itself
# --------------------------------------------------------------------------


def test_the_budget_is_the_documented_product() -> None:
    settings = _settings(export_concurrency=4, max_document_bytes=20_971_520)
    expected = 4 * 20_971_520 * MEMORY_PER_DOCUMENT_FACTOR + MEMORY_BASE_OVERHEAD_BYTES
    assert memory_budget_bytes(settings) == expected


def test_the_default_budget_is_about_430_mib() -> None:
    """Pins the number the chart's limits.memory is sized from, so a change to
    either factor's default shows up here rather than in an OOMKill."""
    budget = memory_budget_bytes(_settings())
    assert 400 * MIB < budget < 450 * MIB


def test_the_documented_concurrency_cap_is_about_1_2_gib() -> None:
    """F16's cap of 16 is the number the readiness review called out as ~1 GB
    and the reason the chart limit is 1Gi rather than 512Mi."""
    budget = memory_budget_bytes(_settings(export_concurrency=16))
    assert 1150 * MIB < budget < 1300 * MIB


def test_the_overhead_is_added_once_not_multiplied() -> None:
    """It is interpreter and library cost, not per-document cost. Multiplying
    it would make the assertion refuse configurations that are actually fine."""
    one = memory_budget_bytes(_settings(export_concurrency=1))
    two = memory_budget_bytes(_settings(export_concurrency=2))
    per_document = two - one
    assert per_document == 20_971_520 * MEMORY_PER_DOCUMENT_FACTOR


# --------------------------------------------------------------------------
# assert_memory_budget
# --------------------------------------------------------------------------


def test_a_budget_within_the_limit_passes(tmp_path: Path) -> None:
    assert_memory_budget(_settings(), [_limit_file(tmp_path, str(2 * 1024 * MIB))])


def test_a_budget_over_the_limit_refuses_to_start(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        assert_memory_budget(
            _settings(export_concurrency=16), [_limit_file(tmp_path, str(512 * MIB))]
        )
    message = str(excinfo.value)
    # Both factors named, because the operator has to choose which to change.
    assert "EXPORT_CONCURRENCY" in message
    assert "MAX_DOCUMENT_BYTES" in message
    assert "512 MiB" in message


def test_the_refusal_explains_why_it_is_not_a_warning(tmp_path: Path) -> None:
    """ "Refusing to start over a memory estimate" looks excessive until you
    know the OOMKill would recur hourly and commit nothing, so the message
    carries that."""
    with pytest.raises(ConfigurationError) as excinfo:
        assert_memory_budget(
            _settings(export_concurrency=16), [_limit_file(tmp_path, str(512 * MIB))]
        )
    message = str(excinfo.value)
    assert "never converges" in message
    assert "checkpointing is first-run-only" in message


def test_a_budget_exactly_at_the_limit_passes(tmp_path: Path) -> None:
    """Boundary pinned deliberately: the comparison is <=, so a limit sized
    from the documented formula is not rejected by rounding."""
    settings = _settings()
    exact = memory_budget_bytes(settings)
    assert_memory_budget(settings, [_limit_file(tmp_path, str(exact))])


def test_an_unreadable_limit_warns_and_does_not_raise(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Running outside a container is normal for a developer and must not be
    fatal — the assertion is a safety net, not a dependency."""
    with caplog.at_level("WARNING", logger="exporter.api.config"):
        assert_memory_budget(_settings(), [tmp_path / "absent"])

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "unverified" in warnings[0].getMessage()


def test_an_unlimited_cgroup_warns_rather_than_passing_silently(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """ "No limit" and "cannot tell" both skip the check, and an operator
    reading the log should be able to see that it was skipped."""
    with caplog.at_level("WARNING", logger="exporter.api.config"):
        assert_memory_budget(_settings(), [_limit_file(tmp_path, "max")])
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_a_passing_budget_is_recorded_at_info(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The product is the thing nobody had written down, so the run records
    what it computed even on the happy path."""
    with caplog.at_level("INFO", logger="exporter.api.config"):
        assert_memory_budget(_settings(), [_limit_file(tmp_path, str(2 * 1024 * MIB))])

    messages = [r.getMessage() for r in caplog.records]
    assert any("memory budget" in m for m in messages)


def test_lowering_concurrency_is_a_remedy(tmp_path: Path) -> None:
    """The message tells the operator to lower EXPORT_CONCURRENCY or raise the
    limit; this asserts the first of those actually works."""
    limit = _limit_file(tmp_path, str(512 * MIB))
    with pytest.raises(ConfigurationError):
        assert_memory_budget(_settings(export_concurrency=16), [limit])

    assert_memory_budget(_settings(export_concurrency=4), [limit])
