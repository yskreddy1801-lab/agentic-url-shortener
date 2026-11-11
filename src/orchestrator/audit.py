"""Hash-chained audit events.

Each event commits to the previous event hash so a gap or an edit is detectable.
"""

from __future__ import annotations

import hashlib
import json

GENESIS = "0" * 64


def canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def hash_event(
    *,
    seq: int,
    run_id: str,
    event_type: str,
    stage_id: str | None,
    attempt: int | None,
    ts: str,
    details: dict,
    prev_hash: str,
) -> str:
    body = {
        "seq": seq,
        "run_id": run_id,
        "type": event_type,
        "stage_id": stage_id,
        "attempt": attempt,
        "ts": ts,
        "details": details,
        "prev_hash": prev_hash,
    }
    return hashlib.sha256(canonical(body).encode()).hexdigest()


def verify_event_chain(events: list[dict]) -> None:
    prev = GENESIS
    for event in events:
        if event["prev_hash"] != prev:
            raise AssertionError(f"event {event['seq']} broke the hash chain")
        expected = hash_event(
            seq=event["seq"],
            run_id=event["run_id"],
            event_type=event["type"],
            stage_id=event["stage_id"],
            attempt=event["attempt"],
            ts=event["ts"],
            details=event["details"],
            prev_hash=event["prev_hash"],
        )
        if event["event_hash"] != expected:
            raise AssertionError(f"event {event['seq']} hash does not match its payload")
        prev = event["event_hash"]
