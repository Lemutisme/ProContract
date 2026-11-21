"""Frozen, paged native command history; no behavioral judgment or model-wire facts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat


SCOPE = (
    "Host-owned native CommandExecution observations in the indexed source windows. "
    "Raw records retain their reported failures, pending states and timestamps. "
    "These are observations, not distinct-operation counts, complete OS/network activity, "
    "or evidence that an unobserved action never happened."
)
INDEX = "EXECUTION_HISTORY/index.json"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()


def unavailable(limits, observed_events, source_errors):
    return dict(schema="procontract_native_history/v1", state="unavailable", scope=SCOPE,
                reason="history_and_index_exceed_storage_bounds", limits=limits,
                observed_events=observed_events, omitted_events=observed_events,
                source_error_count=source_errors, windows=[], chunks=[], events=[])


def validate_limits(*, max_bytes_per_log, max_file_bytes, max_total_bytes):
    limits = dict(max_bytes_per_log=max_bytes_per_log, max_file_bytes=max_file_bytes,
                  max_total_bytes=max_total_bytes)
    if any(type(value) is not int or value <= 0 for value in limits.values()):
        raise ValueError("history byte bounds must be positive integers")
    # Even an unavailable optional history needs a readable, truthful result.
    # Validate this capability shape before invoking the base capture provider.
    minimum = len(encoded(unavailable(limits, (1 << 64) - 1, (1 << 64) - 1)))
    if min(max_file_bytes, max_total_bytes) < minimum:
        raise ValueError(f"history bounds need at least {minimum} bytes for an unavailable-history record")
    return limits


def capture_history(sessions: Path, projection: Path, *, max_bytes_per_log: int,
                    max_file_bytes: int, max_total_bytes: int) -> dict:
    """Compose into a fresh host projection, preserving original event-line bytes.

    Limits are caller-supplied observation/storage bounds, never evidence cutoffs.
    A physical scan receipt is returned separately from the semantic projection:
    appending valid non-command telemetry does not change captured command history.
    """
    limits = validate_limits(max_bytes_per_log=max_bytes_per_log, max_file_bytes=max_file_bytes,
                             max_total_bytes=max_total_bytes)
    sessions, projection = Path(sessions), Path(projection)
    if not sessions.is_absolute() or sessions.is_symlink() or not sessions.is_dir():
        raise ValueError("history needs an existing host-owned session directory")
    if not projection.is_dir() or projection.is_symlink():
        raise ValueError("history needs an existing host projection")
    directory = projection / "EXECUTION_HISTORY"
    directory.mkdir()  # A prior or task-created lookalike is never overwritten.
    rows, windows, errors, scans = [], [], [], []
    chunks, chunk_bytes, chunk = [], [], bytearray()
    retained_bytes = 0

    def flush():
        nonlocal chunk
        if not chunk:
            return
        name = f"EXECUTION_HISTORY/{len(chunks):06}.jsonl"
        raw = bytes(chunk)
        chunk_bytes.append(raw)
        chunks.append(dict(path=name, sha256=digest(raw), bytes=len(raw)))
        chunk = bytearray()

    for source in sorted(sessions.rglob("rollout-*.jsonl")):
        relative = str(source.relative_to(sessions))
        if source.is_symlink() or not source.resolve().is_relative_to(sessions.resolve()):
            errors.append(dict(path=relative, reason="source link or escape"))
            continue
        try:
            fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("source is not a regular file")
                start = max(0, info.st_size - max_bytes_per_log)
                stream.seek(start)
                raw = stream.read(info.st_size - start)
                if len(raw) != info.st_size - start:
                    raise ValueError("source shrank during scan")
        except (OSError, ValueError) as error:
            errors.append(dict(path=relative, reason=type(error).__name__))
            continue
        scans.append(dict(path=relative, offset=start, bytes=len(raw), sha256=digest(raw)))
        window = dict(path=relative, start=start, through_event_byte=None,
                      prefix_omitted=start > 0, partial_or_invalid_lines=0,
                      observed_events=0, retained_events=0, omitted_events=[])
        offset = start
        pending_invalid = 0
        lines = raw.splitlines(keepends=True)
        if start and lines:
            # A bounded tail may begin inside a record; never interpret its suffix.
            offset += len(lines.pop(0))
            pending_invalid += 1
        for line in lines:
            position = offset
            offset += len(line)
            if not line.endswith(b"\n"):
                pending_invalid += 1
                continue
            try:
                record = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                pending_invalid += 1
                continue
            payload = record.get("payload") if isinstance(record, dict) and record.get("type") == "event_msg" else None
            item = payload.get("item") if isinstance(payload, dict) and payload.get("type") == "item_completed" else None
            if not isinstance(item, dict) or item.get("type") != "CommandExecution":
                continue
            identity = (payload.get("thread_id"), payload.get("turn_id"), item.get("id"))
            if not all(isinstance(value, str) and value for value in identity):
                pending_invalid += 1
                continue
            window["partial_or_invalid_lines"] += pending_invalid
            pending_invalid = 0
            window["observed_events"] += 1
            window["through_event_byte"] = offset
            origin = dict(path=relative, offset=position, bytes=len(line), sha256=digest(line))
            reason = ("record_exceeds_file_bound" if len(line) > max_file_bytes else
                      "retention_byte_bound" if retained_bytes + len(line) > max_total_bytes else None)
            if reason:
                window["omitted_events"].append(dict(origin=origin, reason=reason))
                continue
            if len(chunk) + len(line) > max_file_bytes:
                flush()
            rows.append(dict(thread_id=identity[0], turn_id=identity[1], item_id=identity[2],
                             status=item.get("status"), exit_code=item.get("exit_code"),
                             completed_at_ms=payload.get("completed_at_ms"), origin=origin,
                             chunk=f"EXECUTION_HISTORY/{len(chunks):06}.jsonl",
                             offset=len(chunk), bytes=len(line), sha256=digest(line)))
            chunk.extend(line)
            retained_bytes += len(line)
            window["retained_events"] += 1
        # Empty channels supply no execution facts. Physical scans stay in the
        # external collection receipt; faults or an omitted prefix remain visible.
        if window["observed_events"] or window["prefix_omitted"] or window["partial_or_invalid_lines"]:
            windows.append(window)
        scans[-1]["after_event_cutoff_partial_or_invalid_lines"] = pending_invalid
    flush()
    index = dict(schema="procontract_native_history/v1", state="available", scope=SCOPE,
                 selection="native event_msg/item_completed/CommandExecution; source order then byte offset",
                 cutoff="Through the last observed matching event in each listed source; no claim about later activity or other channels.",
                 limits=limits,
                 windows=windows, source_errors=errors, chunks=chunks, events=rows)
    raw_index = encoded(index)
    if len(raw_index) > max_file_bytes or retained_bytes + len(raw_index) > max_total_bytes:
        # Optional history cannot erase a successful base artifact capture. Do not
        # publish unindexed partial chunks or present a storage failure as an empty
        # complete history. The minimum readable failure record was admitted above.
        index = unavailable(limits, sum(w["observed_events"] for w in windows), len(errors))
        raw_index = encoded(index)
        chunks, chunk_bytes, rows, retained_bytes = [], [], [], 0
    for descriptor, raw in zip(chunks, chunk_bytes):
        (projection / descriptor["path"]).write_bytes(raw)
    (projection / INDEX).write_bytes(raw_index)
    observations = [dict(path=INDEX, sha256=digest(raw_index), kind="capture_metadata", scope=SCOPE)]
    observations.extend(dict(path=c["path"], sha256=c["sha256"], kind="execution_observation", scope=SCOPE) for c in chunks)
    return dict(index_path=INDEX, observations=observations,
                diagnostics=dict(scans=scans, retained_events=len(rows), retained_bytes=retained_bytes,
                                 index_bytes=len(raw_index), source_errors=errors))
