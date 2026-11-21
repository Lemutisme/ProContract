"""Inventory of an older store for an explicit migration of its adoptions. Read-only.

A store written before acceptances were recorded holds its adoptions only as journal entries,
and a journal entry's shape proves nothing: a plain settlement plus an appended selection looks
the same. What the old store did keep, for every adoption that succeeded, is an idempotency row
whose command hash is the digest, in the store's `adoption` domain, of the whole request: the
settling command, the incumbent fence it expected, and the identities it recorded. A plain
command is hashed in another domain and an appended entry leaves no row. So an old adoption is
verified exactly when that request can be rebuilt from the kernel event, the journal entry and
the fence, and its digest is found with the Contract state the kernel event produced.

`snapshot` copies the database with its write-ahead log, checks the source did not change while
it was copied, and leaves a receipt. `plan` takes only such a snapshot: it refuses a directory
without the receipt, reads a private copy of the snapshot's files, and gives no manifest if the
snapshot changed before or while it was read, so the manifest's stated input is what was read.

The manifest lists, for every incumbent-shaped journal entry, whether it is a verified adoption
and why, what it would register, and what stays unknown. Unknown is not invalid: nothing unknown
is registered, selected or withdrawn. A manifest is `applicable` only when the kernel chain, the
journal chain and the current Contract states were verified; otherwise it is a diagnostic.

`target` makes a new store directory from a snapshot, with its own receipt, and `apply` migrates
only such a directory: a source store or a snapshot is refused before it is opened. `apply` sends
the store which journal selections and settling events to look at, the exact state they were
planned for and the principal's authorization. The store derives the rest from its own records
(the fence each adoption carried, whether what it was built on is known, which selection was
current) and registers in one transaction or changes nothing. Pointing a runtime at the migrated
store, and keeping older writers away from it, are the host's separate steps.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import procontract_store as kernel

IMPLEMENTATION = "procontract_migration/5"
LEDGER = "ledger_1.sqlite"
PARTS = (LEDGER, LEDGER + "-wal")  # the -shm index is rebuilt by any reader and is not copied
OPENED = PARTS + (LEDGER + "-shm", LEDGER + "-journal")  # every file an engine may open beside a ledger
RECEIPT = "SNAPSHOT.json"
TARGET = "TARGET.json"
ZERO = "0" * 64


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _identity(directory: Path) -> dict:
    return {name: {"sha256": _sha256(directory / name),
                   "bytes": (directory / name).stat().st_size if (directory / name).exists() else None}
            for name in PARTS}


def _copy(source: Path, destination: Path) -> None:
    for name in PARTS:
        if (source / name).exists():
            shutil.copyfile(source / name, destination / name)


def snapshot(source: Path, destination: Path) -> dict:
    """Copy a store's database and write-ahead log without opening them, refuse the copy if the
    source changed meanwhile, and leave a receipt naming the source and the files' digests. The
    source is never opened by a database engine."""
    source, destination = Path(source), Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("the destination of a snapshot is a new, empty directory")
    destination.mkdir(parents=True, exist_ok=True)
    before = _identity(source)
    if before[LEDGER]["sha256"] is None:
        raise ValueError("no ledger in the source")
    _copy(source, destination)
    if _identity(source) != before or _identity(destination) != before:
        shutil.rmtree(destination)
        raise RuntimeError("the source changed while it was copied; nothing is kept")
    receipt = {"schema": "pro-contract-store/snapshot/1", "source": str(source.resolve()), "files": before,
               "taken_at": datetime.now(timezone.utc).isoformat()}
    (destination / RECEIPT).write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n")
    return receipt


def _rows(database: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    return database.execute(query).fetchall()


def _binary() -> dict:
    path = Path(shutil.which(kernel.store_bin()) or kernel.store_bin()).resolve()
    return {"path": str(path), "sha256": _sha256(path)}


def plan(snapshot_directory: Path, *, verify: bool = True) -> dict:
    """The migration manifest for a snapshot made by `snapshot`.

    For each journal entry shaped like an incumbent selection it reports `verified` with the
    evidence (kernel event, idempotency key, the fence that makes the digest match), or the
    reason it is not. `supports` is `no_incumbent_expected` when the adoption expected no
    incumbent in this store, which only says it was built on no earlier adoption of this store;
    otherwise the old request named nothing and they are `unknown`.

    `historical_current` is what the old store's latest entry selected. `would_register` gives
    the acceptance rows and, separately, a selection to activate, which is present only when the
    historical current is a verified adoption that still settles its Contract and whose supports
    are known. With `verify=False`, or when a chain or a current state does not verify, the
    manifest is marked not applicable: a diagnostic, never an input to a migration.
    """
    directory = Path(snapshot_directory)
    receipt_path = directory / RECEIPT
    if not receipt_path.exists():
        raise ValueError("not a snapshot: no receipt; take one with snapshot() instead of pointing at a store")
    # The receipt is read once: these bytes are hashed, parsed and reported, and checked unchanged
    # at the end together with the files. Nothing about the snapshot is read a second time.
    receipt_bytes = receipt_path.read_bytes()
    receipt_digest = hashlib.sha256(receipt_bytes).hexdigest()
    receipt = json.loads(receipt_bytes)
    pinned = _identity(directory)
    if pinned != receipt["files"]:
        raise RuntimeError("the snapshot's files differ from its receipt; no manifest is given")

    with tempfile.TemporaryDirectory() as private:
        view = Path(private)
        _copy(directory, view)
        if _identity(view) != pinned:
            raise RuntimeError("the snapshot changed while it was read; no manifest is given")
        database = sqlite3.connect(f"file:{view / LEDGER}?mode=ro", uri=True)
        database.row_factory = sqlite3.Row
        try:
            schema = [row["sql"] for row in _rows(
                database, "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name")]
            tables = sorted(row["name"] for row in _rows(database, "SELECT name FROM sqlite_master WHERE type='table'"))
            events = _rows(database, "SELECT seq, contract_id, command_json, state_hash, prev_hash, hash "
                                     "FROM events ORDER BY seq")
            journal = _rows(database, "SELECT seq, event_json FROM experiment_events WHERE kind='selection' ORDER BY seq")
            journal_end = _rows(database, "SELECT count(*) AS n, max(seq) AS last FROM experiment_events")[0]
            journal_head = _rows(database, "SELECT hash FROM experiment_events ORDER BY seq DESC LIMIT 1")
            retained = {row["command_hash"]: row for row in _rows(
                database, "SELECT key, command_hash, result_json FROM idempotency")}
            contracts = {row["id"]: row["state_json"] for row in _rows(database, "SELECT id, state_json FROM contracts")}
            recorded = "acceptances" in tables and bool(_rows(database, "SELECT 1 FROM acceptances LIMIT 1"))
        finally:
            database.close()
        journal_chain = None
        if verify:
            check = Path(private) / "journal-check"  # the store binary opens, and may alter, only this copy
            check.mkdir()
            _copy(directory, check)
            try:
                kernel._run(["verify", "--store", str(check)])
                journal_chain = True
            except RuntimeError:
                journal_chain = False
    if _identity(directory) != pinned or _sha256(receipt_path) != receipt_digest:
        raise RuntimeError("the snapshot changed while it was read; no manifest is given")

    kernel_chain = states_current = None
    if verify:
        previous, kernel_chain, last_state = ZERO, True, {}
        for row in events:
            expected = kernel.digest_input("event", json.dumps([previous, json.loads(row["command_json"]),
                                                                row["state_hash"]]))
            kernel_chain = kernel_chain and row["prev_hash"] == previous and row["hash"] == expected
            previous, last_state[row["contract_id"]] = row["hash"], row["state_hash"]
        states_current = set(contracts) == set(last_state) and all(
            kernel.digest_input("contract_state", state) == last_state[contract]
            for contract, state in contracts.items())
    contracts = {contract: json.loads(state) for contract, state in contracts.items()}

    discharges: dict = {}
    for row in events:
        command = json.loads(row["command_json"])
        if command["command"]["type"] == "discharge":
            discharges.setdefault((row["contract_id"], command["command"]["coordinate"]["subject_hash"]), []).append(
                (row["seq"], command, row["state_hash"]))

    entries, fence = [], None
    results = {"enabled": verify, "checked": 0, "passed": 0, "failed": 0}
    for row in journal:
        event = json.loads(row["event_json"])
        body = event["body"]
        if body.get("role") != "incumbent":
            continue
        entry = {"selection": row["seq"], "adoption": body.get("adoption"), "version": body.get("version"),
                 "expected_incumbent_seq": fence, "verified": False}
        candidates, matches, mismatched = discharges.get((body.get("adoption"), body.get("version")), []), [], 0
        for seq, command, state_hash in candidates:
            request = {"command": command, "expected_incumbent_seq": fence, "identities": event["identities"]}
            kept = retained.get(kernel.digest("adoption", request))
            if kept is None:
                continue
            if verify:
                results["checked"] += 1
                if kernel.digest_input("contract_state", kept["result_json"]) != state_hash:
                    results["failed"] += 1
                    mismatched += 1
                    continue
                results["passed"] += 1
            matches.append({"event": seq, "idempotency_key": kept["key"], "command_hash": kept["command_hash"],
                            "generation": command["command"]["coordinate"]["generation"]})
        if len(matches) == 1:
            contract = contracts.get(body["adoption"]) or {}
            coordinate = (contract.get("settlement") or {}).get("coordinate", {})
            entry.update(
                verified=True, evidence=matches[0],
                settles_its_contract_now=(contract.get("standing") == "discharged"
                                          and coordinate.get("generation") == matches[0]["generation"]
                                          and coordinate.get("subject_hash") == body["version"]),
                # The old request named no supports. Expecting no incumbent says only that it was
                # built on no earlier adoption of this store; otherwise nothing is recorded.
                supports="no_incumbent_expected" if fence is None else "unknown")
        elif matches:
            entry["reason"] = "more than one retained adoption request matches; the source is ambiguous"
        elif mismatched:
            entry["reason"] = ("the adoption request was retained, but its retained result is not the state the "
                               "settling event produced")
        elif not candidates:
            entry["reason"] = "no settling command of that Contract for that version"
        else:
            entry["reason"] = ("a settling command exists, but no adoption request with this entry's fence and "
                               "identities was retained: a plain settlement with an appended entry, or another fence")
        entries.append(entry)
        fence = row["seq"]  # the old fence was the latest incumbent-shaped entry, whoever wrote it

    verified = [entry for entry in entries if entry["verified"]]
    last = entries[-1] if entries else None
    historical = None if last is None else {
        "selection": last["selection"], "verified": last["verified"],
        "settles_its_contract_now": last.get("settles_its_contract_now", False), "supports": last.get("supports")}
    activate = None
    if last and last["verified"] and last["settles_its_contract_now"] and last["supports"] == "no_incumbent_expected":
        activate = {"selection": last["selection"], "acceptance": last["selection"]}
    register = [{"selection": entry["selection"], "contract_id": entry["adoption"],
                 "event": entry["evidence"]["event"], "generation": entry["evidence"]["generation"],
                 "subject_hash": entry["version"], "expected_incumbent_seq": entry["expected_incumbent_seq"],
                 "supports_known": entry["supports"] == "no_incumbent_expected",
                 "supports": [] if entry["supports"] == "no_incumbent_expected" else None}
                for entry in verified]
    unknown = [{"selection": entry["selection"], "reason": entry["reason"]} for entry in entries if not entry["verified"]]
    unknown += [{"selection": entry["selection"], "reason": "verified, but what it was built on is not recorded"}
                for entry in verified if entry["supports"] == "unknown"]
    if last is not None and activate is None:
        unknown.append({"selection": last["selection"], "reason":
                        "no selection can be activated: the store's latest entry is not a verified adoption that "
                        "still settles its Contract with known supports; nothing is selected in its place"})
    # Each verified entry passed its own retained-result check; an entry that failed it is listed
    # unknown and does not make the other entries diagnostic. The three store-wide checks do.
    checks = {"retained_result_states": results, "kernel_chain": kernel_chain,
              "journal_chain": journal_chain, "current_states_match_their_last_event": states_current}
    diagnostic = [name for name in ("kernel_chain", "journal_chain", "current_states_match_their_last_event")
                  if checks[name] is not True] + ([] if verify else ["retained_result_states"])
    manifest = {
        "schema": "pro-contract-store/acceptance-migration-plan/2",
        "implementation": {"planner": IMPLEMENTATION, "store_binary": _binary()},
        "snapshot": {"receipt_sha256": receipt_digest, "source": receipt["source"], "files": pinned},
        # What a target store must hold, exactly, for this manifest to be applied to it.
        "state": {"kernel_events": len(events), "kernel_head": events[-1]["hash"] if events else ZERO,
                  "journal_events": journal_end["n"], "journal_last": journal_end["last"],
                  "journal_head": journal_head[0]["hash"] if journal_head else ZERO},
        "source_protocol": {"tables": tables, "schema_sha256": hashlib.sha256("\n".join(schema).encode()).hexdigest(),
                            "already_records_acceptances": recorded},
        "checks": checks, "applicable": not diagnostic, "diagnostic_only_because": diagnostic,
        "counts": {"kernel_events": len(events), "incumbent_shaped_entries": len(entries),
                   "verified_adoptions": len(verified), "unknown": len(unknown)},
        "entries": entries, "historical_current": historical,
        "would_register": {"acceptances": register, "selection_to_activate": activate},
        "unknown": unknown,
        "authorization": None,  # supplied by the principal when a manifest is applied, never here
    }
    manifest["digest"] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    return manifest


def target(snapshot_directory: Path, destination: Path) -> Path:
    """A new store directory holding exactly a snapshot's database and log: what is migrated,
    in place of the source the snapshot was taken from. It carries its own receipt, naming the
    snapshot it was made from; `apply` works on nothing else."""
    directory, destination = Path(snapshot_directory), Path(destination)
    receipt_bytes = (directory / RECEIPT).read_bytes()
    receipt = json.loads(receipt_bytes)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("the target of a migration is a new, empty directory")
    destination.mkdir(parents=True, exist_ok=True)
    _copy(directory, destination)
    if _identity(destination) != receipt["files"] or _identity(directory) != receipt["files"]:
        shutil.rmtree(destination)
        raise RuntimeError("the snapshot differs from its receipt; no target is made")
    (destination / TARGET).write_text(json.dumps({
        "schema": "pro-contract-store/migration-target/2",
        "snapshot_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest(),
        "source": receipt["source"], "files": receipt["files"],
        # What this target is, physically: where it lives and which file its ledger is.
        "target": str(destination.resolve()), "ledger_inode": os.lstat(destination / LEDGER).st_ino,
        "made_at": datetime.now(timezone.utc).isoformat()}, indent=1, sort_keys=True) + "\n")
    return destination


def _file(path: Path) -> tuple[int, int] | None:
    """Which file a path names, without following a link."""
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return None
    return status.st_dev, status.st_ino


def _own_target(directory: Path, manifest: dict) -> Path:
    """The real location of a directory, if it is physically the target that `target` made from
    the manifest's snapshot; refused otherwise, before any store is opened.

    A receipt alone proves nothing: it can be copied into another directory, and the ledger
    beside it can be replaced by a link to the source. So the receipt must name this location,
    this snapshot and this source; nothing in the directory may be a link or have a second name;
    no database file may be the source's; and the ledger must be the file the target was made
    with. Content is not compared: a migration changes it, and a repeated call must still pass."""
    place = Path(directory).resolve()
    marker = place / TARGET
    if not marker.is_file() or (place / RECEIPT).exists():
        raise ValueError("not a migration target: make one from the snapshot with target(); "
                         "a source store or a snapshot is never migrated")
    made = json.loads(marker.read_text())
    snapshot = manifest["snapshot"]
    if (made.get("snapshot_receipt_sha256"), made.get("source"), made.get("files")) != (
            snapshot["receipt_sha256"], snapshot["source"], snapshot["files"]):
        raise ValueError("this target was made from another snapshot than the manifest was planned on")
    if made.get("target") != str(place):
        raise ValueError("this receipt belongs to a target made elsewhere")
    source = Path(made["source"]).resolve()
    if place == source:
        raise ValueError("the target is the source the snapshot was taken from")
    with os.scandir(place) as listing:
        entries = list(listing)
    for entry in entries:
        status = entry.stat(follow_symlinks=False)
        if stat.S_ISDIR(status.st_mode):
            continue
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
            raise ValueError(f"{entry.name} in the target is a link or has another name elsewhere; "
                             "a target holds only its own files")
    for name in OPENED:
        here = _file(place / name)
        if here is not None and here == _file(source / name):
            raise ValueError(f"{name} in the target is the source's own file")
    if os.lstat(place / LEDGER).st_ino != made.get("ledger_inode"):
        raise ValueError("the ledger in the target is not the file the target was made with")
    return place


def apply(manifest: dict, target_directory: Path, *, authorization: str, identities: dict | None = None) -> dict:
    """Migrate a target store by an applicable manifest, under the principal's authorization.

    Before the store is opened: the manifest must carry its own digest and be applicable; the
    directory must physically be the target that `target` made from the very snapshot the
    manifest was planned on (`_own_target`): its receipt names that snapshot, that source and
    this very location; nothing in it is a link or has a second name; its ledger is the file it
    was made with. A store that is not such a target is never opened, so a manifest does not
    reach the store it was read from, directly, through a copied receipt or through a link. The
    check and the opening are two steps: keeping other writers and renames out of the target
    while a migration runs is the host's, and is not established here.

    The request names only which journal selections and settling events to look at. The store
    does the rest in one transaction and derives everything else from its own records: the state
    it must be in, the fence each adoption carried, whether what it was built on is known, and
    which selection was current. A selection is activated only if the manifest offers one, and
    the store accepts it only if it was the store's own latest. Repeating the same manifest
    returns the same result.
    """
    body = {key: value for key, value in manifest.items() if key != "digest"}
    if hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() != manifest.get("digest"):
        raise ValueError("the manifest does not match its digest")
    if manifest.get("applicable") is not True:
        raise ValueError("a diagnostic manifest is not applied: " + ", ".join(manifest.get("diagnostic_only_because", [])))
    if not (isinstance(authorization, str) and authorization.strip()):
        raise ValueError("authorization is the host's receipt of the principal's act: a non-empty string")
    place = _own_target(Path(target_directory), manifest)
    rows = [row for row in manifest["would_register"]["acceptances"] if row["supports_known"]]
    activate = manifest["would_register"]["selection_to_activate"]
    request = {
        "manifest": manifest["digest"],
        "state": {name: manifest["state"][name]
                  for name in ("kernel_events", "kernel_head", "journal_events", "journal_head")},
        "acceptances": [{"selection": row["selection"], "event": row["event"]} for row in rows],
        "selection_to_activate": activate["acceptance"] if activate else None,
        "authorization": authorization,
        "identities": identities or kernel.identities(harness=IMPLEMENTATION),
    }
    return kernel.migrate(place, f"migration:{manifest['digest']}", request)
