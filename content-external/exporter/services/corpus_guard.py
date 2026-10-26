"""The one gate for anything concluded from "this run saw the whole corpus"
(F20).

A run that did not classify every row — `--limit`, a systemic abort (F19), a
run that ended early — has seen a slice. Everything it did not touch looks
stale, absent or orphaned from inside that slice, so any operation that acts
on completeness would delete live content after it:

- `sweep_stale` (L10): every document not synced this run is "stale";
- the orphan tail sweep (J5), when it runs after an export;
- `run_purge` (J7), which is the whole-agency case of the same reasoning;
- and the corpus watermark, which the coordinator withholds itself from the
  same flag.

The production-readiness review found this invariant recognised for the
sweeps and missed for the watermark, which is the lesson this module exists
to encode: the flag is computed once, by the coordinator, and carried on the
RunReport as `run_observed_full_corpus`. Consumers call
`require_observed_full_corpus` and never re-derive completeness from
`limit`, the outcome or the counts — there were two consumers when the rule
was written and a third was one refactor away.

SEEING THE CORPUS IS NECESSARY, NOT SUFFICIENT. A run can classify every row
and still hold documents back: an upsert the destination did not confirm, a
tail it did not delete, a document skipped by a size guard. Each keeps its
previous state and its old `synced_at`, and `sweep_stale(before=run_start)`
would delete it as stale while it is live. So the guard also requires
`documents_held_back == 0`. The watermark has always required the same.

WHAT THIS GUARD CANNOT COVER, for Stage L to carry: an `unchanged` document,
and one held because its row is not ready (`in_review`, `cleaning`), are not
touched by a run either, by design. A `synced_at` sweep (L10) must therefore
spare every document the committed manifest lists, or refresh `synced_at` on
them; no run-level flag can make that sweep safe on its own.

Stage J and Stage L build the sweeps; this guard is what they must call
before acting.
"""

from exporter.core.schemas import RunReport

__all__ = ["IncompleteCorpusError", "require_observed_full_corpus"]


class IncompleteCorpusError(RuntimeError):
    """An operation that needs a full-corpus run was asked to act on a run
    that was not one."""


def require_observed_full_corpus(report: RunReport, *, operation: str) -> None:
    """Refuse `operation` unless `report` is a successful run that observed
    the full corpus and held no document back. A refusal is the safe
    direction: the next clean run makes the operation valid again, while
    acting now could delete live content."""
    if (
        report.outcome == "success"
        and report.run_observed_full_corpus
        and report.documents_held_back == 0
    ):
        return
    reasons: list[str] = []
    if report.outcome != "success":
        reasons.append(f"its outcome was {report.outcome!r}")
    if not report.run_observed_full_corpus:
        if report.limit is not None:
            reasons.append(f"it was limited to {report.limit} read(s)")
        else:
            reasons.append("it did not classify every row")
    if report.documents_held_back:
        reasons.append(
            f"it held {report.documents_held_back} document(s) back, which "
            "keep their old state and would look stale"
        )
    raise IncompleteCorpusError(
        f"refusing {operation} after run {report.run_id}: "
        f"{'; '.join(reasons)}. Only a successful run that observed the whole "
        "corpus may drive it; the next full run will."
    )
