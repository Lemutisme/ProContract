"""Export a captured native institution prefix into two private test stores.

Host-only, zero execution. The input database must be an existing custody copy;
SQLite only opens a further private byte copy. Mutable records are classified,
not silently taken from the later completed source state. No grant is renewed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def domain(name, raw):
    return sha(("pro_contract/v1/" + name + "\0").encode() + raw)


def read(path):
    raw = Path(path).read_bytes()
    return raw, json.loads(raw)


def state(path):
    path = Path(path)
    if not path.exists():
        return {"exists": False}
    s = path.stat()
    return {"exists": True, "sha256": sha(path.read_bytes()), "stat": {
        k: getattr(s, k) for k in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")}}


def _tables(database, receipt):
    database = Path(database).resolve()
    if (receipt.get("source_engine_opened") is not False or receipt.get("source_stable") is not True
            or database != Path(receipt["private_copy"]).resolve()
            or str(database) in receipt["source_before"]):
        raise ValueError("input must be the recorded custody copy, not a historical source")
    paths = [Path(str(database) + suffix) for suffix in ("", "-wal", "-shm")]
    before = {str(p): state(p) for p in paths}
    with tempfile.TemporaryDirectory(prefix="native-prefix-read-") as tmp:
        for p in paths[:2]:
            if p.exists():
                (Path(tmp) / p.name).write_bytes(p.read_bytes())
        conn = sqlite3.connect(Path(tmp) / database.name); conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA query_only=ON")
            schema = [dict(row) for row in conn.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name!='sqlite_sequence' ORDER BY name")]
            names = {row["name"] for row in schema}
            if names != {"contracts", "events", "idempotency", "records", "subjects", "experiment_events", "acceptances", "incumbents"}:
                raise ValueError("unqualified native store schema")
            tables = {name: [dict(row) for row in conn.execute('SELECT * FROM "' + name + '"')] for name in names}
        finally:
            conn.close()
    if before != {str(p): state(p) for p in paths}:
        raise ValueError("custody copy changed while exporting")
    return schema, tables, before


def _chain(rows, kind):
    rows = sorted(rows, key=lambda row: row["seq"]); previous = "0" * 64
    for index, row in enumerate(rows, 1):
        if row["seq"] != index or row["prev_hash"] != previous:
            raise ValueError("native prefix chain is discontinuous")
        body = row["command_json"] if kind == "event" else row["event_json"]
        raw = (json.dumps(previous) + "," + body +
               ("," + json.dumps(row["state_hash"]) if kind == "event" else ""))
        if domain(kind, ("[" + raw + "]").encode()) != row["hash"]:
            raise ValueError("native chain hash differs")
        previous = row["hash"]
    return rows


def prepare(database, blobs, settings_path, capture_path, custody_receipt):
    custody_raw, custody = read(custody_receipt)
    settings_raw, settings = read(settings_path); capture_raw, capture = read(capture_path)
    if capture.get("schema") != "native-selector-capture/v1" or sha(capture_raw) != Path(capture_path).stem:
        raise ValueError("capture is not its retained native manifest")
    schema, tables, database_state = _tables(database, custody)
    events = _chain(tables["events"], "event"); journal = _chain(tables["experiment_events"], "experiment_event")
    boundary = capture["boundary"]; kh, jh = boundary["kernel_head"], boundary["journal_head"]
    if events[kh["seq"] - 1]["hash"] != kh["hash"] or journal[jh["seq"] - 1]["hash"] != jh["hash"]:
        raise ValueError("captured heads are not in the retained source")
    events = events[:kh["seq"]]; journal = journal[:jh["seq"]]
    record = boundary["contract"]["id"]; current = boundary["selected_subject"]
    if any(row["contract_id"] != record for row in events):
        raise ValueError("this control exports one responsibility only")
    if tables["acceptances"] or tables["incumbents"]:
        raise ValueError("acceptance-bearing prefix export is outside this control")
    states = [row["state_json"] for row in tables["contracts"]] + [row["result_json"] for row in tables["idempotency"]]
    states = {raw for raw in states if domain("contract_state", raw.encode()) == events[-1]["state_hash"]}
    if len(states) != 1:
        raise ValueError("target contract state is not uniquely retained")
    contract_raw = states.pop(); contract = json.loads(contract_raw)
    if json.dumps(contract, sort_keys=True) != json.dumps(boundary["contract"], sort_keys=True):
        raise ValueError("target institution differs from captured boundary")
    if contract["candidate"]["subject_hash"] != current:
        raise ValueError("captured selection differs from the institution candidate")
    decoded = [json.loads(row["event_json"]) for row in journal]
    intakes = [(row, event["body"]) for row, event in zip(journal, decoded) if event["kind"] == "intake"]
    if len(intakes) != 1:
        raise ValueError("prefix needs a unique original intake")
    intake_row, intake = intakes[0]; thread = intake["thread_id"]; base = intake["base_subject"]
    if record != thread + ".1":
        raise ValueError("native thread and responsibility identity differ")
    deliveries = [e["body"] for e in decoded if e["kind"] == "capture" and e["body"].get("phase") == "task_delivery"]
    if len(deliveries) != 1 or deliveries[0]["subject_hash"] != current:
        raise ValueError("control requires one retained closed handoff")
    selected = {base, current}; subjects = [row for row in tables["subjects"] if row["subject_hash"] in selected]
    if {row["subject_hash"] for row in subjects} != selected:
        raise ValueError("base/current subject binding missing")
    blobs = Path(blobs).resolve(); retained = {}; manifests = {}; blob_states = {}
    def blob(identity):
        path = blobs / identity[:2] / identity
        raw = path.read_bytes()
        if sha(raw) != identity:
            raise ValueError("source blob hash differs")
        retained[identity] = raw
        blob_states[str(path)] = state(path)
        return raw
    for subject in subjects:
        raw = blob(subject["manifest"]); manifest = json.loads(raw)
        if domain("subject_manifest", raw) != subject["subject_hash"]:
            raise ValueError("subject domain hash differs")
        for entry in manifest["entries"]:
            kind = entry["kind"]
            if kind["type"] == "file" and len(blob(kind["digest"])) != kind["len"]:
                raise ValueError("subject file length differs")
        manifests[subject["subject_hash"]] = manifest
    # Keep only extant blob objects referenced by this prefix and their nested
    # content-addressed references. Later journal entries are never roots.
    pending = [json.loads(row["event_json"]) for row in journal]
    scanned = set()
    def hashes(value):
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value): yield value
        elif isinstance(value, dict):
            for item in value.values(): yield from hashes(item)
        elif isinstance(value, list):
            for item in value: yield from hashes(item)
    while pending:
        for identity in hashes(pending.pop()):
            if identity in scanned: continue
            scanned.add(identity)
            if not (blobs / identity[:2] / identity).is_file(): continue
            raw = blob(identity)
            try: pending.append(json.loads(raw))
            except (ValueError, UnicodeError): pass
    policy_prefix, separator, facts_suffix = capture["prompt"].rpartition("\n\n")
    if not separator or not policy_prefix or not facts_suffix.startswith("{"):
        raise ValueError("capture prompt lacks its policy/facts separator")
    facts = json.loads(facts_suffix)
    if facts["contract_id"] != record or facts["subject_hash"] != current:
        raise ValueError("capture prompt refers to another responsibility or subject")
    grant = facts["execution_grant"]
    copied = []; classification = []
    for row in tables["records"]:
        kind, key, body = row["kind"], row["key"], json.loads(row["json"])
        basis = None; category = "unknown_at_boundary_omitted"
        if kind == "capture_projection_v1" and key in selected:
            by_path = {entry["path"]: entry["kind"] for entry in manifests[key]["entries"]}
            if body["subject_hash"] != key or any(by_path.get(entry["path"], {}).get("digest") != entry["sha256"] for entry in body["projection"]["observations"]):
                raise ValueError("immutable projection binding differs from subject")
            category = "immutable_original_record"; basis = "subject-bound compare-exchange record; every observation matches immutable subject"
        elif kind == "succession" and key == thread:
            if (body.get("launches") != 0 or grant["sessions_used"] != 0 or body.get("pending") is not None
                    or body.get("executors") != [] or body.get("lessons") != [] or body.get("previous_executor_claim") != ""
                    or body["started_ms"] != grant["started_ms"] or body["deadline_ms"] != grant["deadline_ms"]
                    or body["grant"].get("total_secs") != settings["succession"].get("total_secs")
                    or body["grant"].get("max_sessions") != settings["succession"].get("max_sessions")):
                raise ValueError("cannot position original succession record at this boundary")
            category = "original_bytes_with_derived_temporal_position"
            basis = "captured same original start/deadline and zero launches; later monotone launch count also zero; no grant value changed"
        elif kind == "progress_grant_v1" and key == thread:
            if body.get("used") != 0 or grant["publications_used"] != 0 or body.get("pending") is not None or body.get("completed") != []:
                raise ValueError("cannot position original progress record at this boundary")
            category = "original_bytes_with_derived_temporal_position"
            basis = "captured zero publications and later monotone used count zero; exact retained grant binding is checked by native restore"
        classification.append({"kind": kind, "key": key, "source_json_sha256": sha(row["json"].encode()),
                               "classification": category, "copied": basis is not None, "basis": basis,
                               "omission": None if basis else "Later mutable record is not an as-of snapshot; no zero/default record is fabricated"})
        if basis: copied.append(row)
    # Missing grant records stay missing, so native recovery reports its actual
    # first blocker; this exporter does not manufacture an allowance.
    command_hashes = {domain("command", row["command_json"].encode()) for row in events}
    state_hashes = {row["state_hash"] for row in events}
    idempotency = [row for row in tables["idempotency"] if row["command_hash"] in command_hashes
                   and domain("contract_state", row["result_json"].encode()) in state_hashes]
    selected_tables = {name: [] for name in tables}
    selected_tables.update(events=events, experiment_events=journal, subjects=subjects, records=copied,
        idempotency=idempotency, contracts=[{"id": record, "version": contract["version"], "state_json": contract_raw}])
    if settings_raw != Path(settings_path).read_bytes() or capture_raw != Path(capture_path).read_bytes() or custody_raw != Path(custody_receipt).read_bytes():
        raise ValueError("source descriptor changed while exporting")
    if blob_states != {path: state(path) for path in blob_states}:
        raise ValueError("source blob changed while exporting")
    return {"schema": schema, "tables": selected_tables, "blobs": retained, "settings": settings_raw,
            "metadata": {"schema": "native-prefix-export/v1", "implementation": sha(Path(__file__).read_bytes()),
                "source_database": str(Path(database).resolve()), "database_custody": database_state,
                "custody_receipt_sha256": sha(custody_raw), "settings_sha256": sha(settings_raw),
                "capture_sha256": sha(capture_raw), "thread_id": thread, "record": record,
                "base_subject": base, "current_subject": current, "boundary": boundary,
                "contract": contract, "kernel_count": len(events), "journal_count": len(journal),
                "records": classification, "subject_bindings": subjects,
                "blob_sha256": sorted(retained), "new_grant": False, "deadline_changed": False,
                "scope": "institution/subject recovery control; no executor, handoff callback, grant or environment restored"}}


def export_pair(database, blobs, settings, capture, custody_receipt, target):
    prepared = prepare(database, blobs, settings, capture, custody_receipt)
    target = Path(target).absolute(); target.mkdir(parents=True, exist_ok=False)
    stores = []
    for side in ("left", "right"):
        root = target / side; root.mkdir(); store = root / "store"; store.mkdir()
        conn = sqlite3.connect(store / "ledger_1.sqlite")
        try:
            for table in prepared["schema"]: conn.execute(table["sql"])
            for name, rows in prepared["tables"].items():
                for row in rows:
                    columns = list(row)
                    conn.execute('INSERT INTO "'+name+'" ('+','.join('"'+c+'"' for c in columns)+') VALUES ('+','.join('?' for _ in columns)+')', list(row.values()))
            conn.commit()
        finally: conn.close()
        for identity, raw in prepared["blobs"].items():
            path = store / "blobs" / identity[:2] / identity; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
        (root / "source-settings.json").write_bytes(prepared["settings"])
        (root / "EXPORT.json").write_bytes(encoded(prepared["metadata"]) + b"\n")
        stores.append({"side": side, "store": str(store), "export": str(root / "EXPORT.json"),
                       "settings": str(root / "source-settings.json"), "initial_db_sha256": sha((store / "ledger_1.sqlite").read_bytes())})
    result = {"schema": "native-prefix-pair/v1", "stores": stores, "metadata": prepared["metadata"],
              "source_unchanged": True, "native_restore_executed": False, "models": 0, "containers": 0}
    (target / "PAIR.json").write_bytes(encoded(result) + b"\n")
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("database", "blobs", "settings", "capture", "custody-receipt", "target"): p.add_argument("--" + name, required=True)
    args = vars(p.parse_args()); result = export_pair(**args)
    print(json.dumps({"pair": str(Path(args["target"]) / "PAIR.json"), "stores": result["stores"]}, indent=2))


if __name__ == "__main__": main()
