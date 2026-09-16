"""CKB is one agency per deployment, and this service asserts it.

A17. G1 fans out from `list_agencies` and POSTs an export per agency; F2 takes
a **whole-run** lock. With two agencies the second POST finds the lock held,
reports `busy` and **exits 0** — every hour, forever, and nothing complains,
because `busy` is by design not an error. That is silent starvation: the
second agency is simply never exported.

CKB enforces one agency per deployment (agency creation is existence-checked
and returns 409), so this is dormant. It is asserted anyway because the design
says repeatedly that the model is agency-keyed and that "one agency is N=1,
not a special case" — and this is the one place where that is not true. The
alternative is the drain-and-recurse shape pipeline/zip.yml uses, but its
termination guarantee is a claimed flag on the agency row, and rules 3-5
forbid this service writing any `agency` column. So: assert, loudly.

WHY THE TWO ENTRY POINTS BEHAVE DIFFERENTLY. `count_agencies` is an HTTP call
to Resql, so "I could not find out" is a real third answer alongside one and
many, and what to do about it depends on when you asked:

  at startup    more than one -> refuse to start
                unreachable   -> WARN and serve anyway
  at run start  more than one -> fail the run
                unreachable   -> fail the run

A hard startup assert would couple this container's boot to Resql being up,
which neither docker-compose (no depends_on for this service) nor Kubernetes
guarantees — so a transient Resql restart would become a CrashLoopBackOff
here, and a service that will not boot is worse than one that boots and warns.
At run start the answer is load-bearing and the run needs the agency list
anyway, so there is nothing to tolerate.

WHY IT READS RESQL DIRECTLY. `list_agencies` has no Ruuter-internal wrapper
today; its only caller is the public DSL/Ruuter/ckb/GET/agency/all.yml. Adding
one here would pre-empt G1, which says to reuse that query and not add
anything. CKB_RESQL is already a Settings field and this is a read-only
SELECT, so the read-only posture holds.
"""

import logging
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from exporter.api.config import Settings

logger = logging.getLogger(__name__)

# Short, because this runs in the startup path. A slow Resql should delay boot
# by seconds and then warn, not hold the container unable to serve.
STARTUP_TIMEOUT_SECONDS = 5.0

# One of the enum values in list_agencies.sql's declaration. Note that
# DSL/Ruuter/ckb/GET/agency/all.yml defaults to 'updated_at desc', which is
# NOT in that enum and falls through to the trailing tiebreaker — harmless
# there, and not worth copying.
_SORTING = "name asc"

# page_size=1 is all that is needed: the query reports the unpaginated total
# through a window function, so one row carries the count.
_PAGE_SIZE = 1


class AgencyCountUnavailableError(RuntimeError):
    """The agency count could not be determined. Not the same as N=1."""


class MultipleAgenciesError(RuntimeError):
    """More than one agency exists, which this service cannot serve correctly."""


def _explain(count: int) -> str:
    return (
        f"{count} agencies exist, and this service supports exactly one per "
        "deployment. The export takes a single whole-run lock, so a second "
        "agency's export would find it held, report `busy` and exit 0 — every "
        "hour, forever, with no error anywhere, because a concurrent run is "
        "by design not a failure. The second agency would simply never be "
        "exported. Run one deployment per agency, or implement the "
        "drain-and-recurse shape pipeline/zip.yml uses."
    )


def count_agencies(
    settings: "Settings", *, timeout: float = STARTUP_TIMEOUT_SECONDS
) -> int:
    """The number of live, non-api agencies in CKB.

    Raises AgencyCountUnavailableError for anything that is not a definite
    answer. The query already filters `is_deleted = FALSE AND type <> 'api'`
    and takes the latest row per base_id, so its total is the count that
    matters here.
    """
    url = f"{settings.ckb_resql}/agency/list_agencies"
    params = {"page": 1, "page_size": _PAGE_SIZE, "sorting": _SORTING}

    try:
        response = requests.get(url, params=params, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise AgencyCountUnavailableError(f"could not reach Resql: {exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise AgencyCountUnavailableError("Resql returned a non-JSON body") from exc

    # Resql returns the rows directly. The dict form is Ruuter's envelope,
    # accepted in case this ever moves behind an internal endpoint.
    if isinstance(payload, dict):
        payload = payload.get("response", payload)

    if not isinstance(payload, list):
        raise AgencyCountUnavailableError("Resql did not return a list of agencies")

    if not payload:
        # A CKB with no agency yet. Nothing to starve, so not a violation.
        return 0

    first = payload[0]
    if not isinstance(first, dict) or "total" not in first:
        # Deliberately NOT falling back to len(payload): page_size is 1, so
        # the length is 1 whether there is one agency or fifty. A fallback
        # would report N=1 for every deployment and silently disable the
        # check this module exists to perform.
        raise AgencyCountUnavailableError(
            "list_agencies did not report `total`; the count cannot be "
            "determined from a single page"
        )

    try:
        return int(first["total"])
    except (TypeError, ValueError) as exc:
        raise AgencyCountUnavailableError("`total` was not a number") from exc


def check_single_agency_at_startup(
    settings: "Settings", *, timeout: float = STARTUP_TIMEOUT_SECONDS
) -> int | None:
    """Boot-tolerant N=1 assertion. Returns the count, or None if unknown.

    Raises MultipleAgenciesError when the answer is definite and wrong. An
    unreachable Resql is a warning: see the module docstring for why boot must
    not depend on it.
    """
    try:
        count = count_agencies(settings, timeout=timeout)
    except AgencyCountUnavailableError as exc:
        logger.warning(
            "could not confirm this deployment has exactly one agency (%s). "
            "Serving anyway, because Resql may still be starting and a "
            "service that refuses to boot is worse than one that warns. The "
            "check is repeated authoritatively at the start of every export.",
            exc,
        )
        return None

    if count > 1:
        raise MultipleAgenciesError(_explain(count))

    logger.info("agency count check passed: %d agency configured", count)
    return count


def require_single_agency(
    settings: "Settings", *, timeout: float = STARTUP_TIMEOUT_SECONDS
) -> int:
    """Authoritative N=1 assertion, for the start of a run.

    Raises on both failure modes. Stage F calls this before taking the lock:
    at that point the run needs the agency list regardless, so an unreachable
    Resql is a failed run and not something to tolerate — and a failed run is
    visible, which silent starvation is not.
    """
    count = count_agencies(settings, timeout=timeout)
    if count > 1:
        raise MultipleAgenciesError(_explain(count))
    return count
