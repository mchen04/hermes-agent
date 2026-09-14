"""Classify execution delivery evidence independently of generation success."""

from dataclasses import dataclass
from typing import Optional


def _classify_delivery_outcome(
    *, delivery_error, should_deliver: bool, unresolved_origin: bool,
    normalized_deliver: str, incident_acked: bool, success: bool,
    delivery_queued=None, delivery_unverified=None,
) -> str:
    if delivery_error:
        return "failed"
    if should_deliver and delivery_unverified:
        return "unverified"
    if should_deliver and delivery_queued:
        return "queued"
    if should_deliver and unresolved_origin:
        return "not_configured"
    if should_deliver and normalized_deliver != "local":
        return "delivered"
    if incident_acked and not success:
        # Failure ping withheld: operator acked this exact signature (vs. plain "suppressed").
        return "suppressed_acked"
    return "suppressed"


@dataclass
class _RunDelivery:
    """Mutable outcome of the save/compose/deliver phase, read back by the bookkeeping tail."""
    job: dict
    success: bool
    error: Optional[str]
    delivery_attempted: bool = False
    delivery_error: Optional[str] = None
    should_deliver: bool = False
    unresolved_origin: bool = False
    blocked_config: bool = False
    incident_acked: bool = False
    failure_incident_id: Optional[str] = None
    side_effect_ownership_lost: bool = False
    output_file: Optional[str] = None


def _run_delivery_outcome(d: _RunDelivery) -> str:
    from cron.scheduler_delivery import _delivery_lane_value, _normalize_deliver_value
    job = d.job
    return _classify_delivery_outcome(
        delivery_error=d.delivery_error,
        delivery_queued=job.get("last_delivery_queued"),
        delivery_unverified=job.get("last_delivery_unverified"),
        should_deliver=d.should_deliver,
        unresolved_origin=d.unresolved_origin,
        # Read the lane the notice was actually routed through (failure_deliver on failure).
        normalized_deliver=_normalize_deliver_value(_delivery_lane_value(job, for_failure=not d.success)),
        incident_acked=d.incident_acked,
        success=d.success,
    )
