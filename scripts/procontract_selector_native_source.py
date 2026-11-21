"""Protected import of a closed native selector source; never an old execution replay.

The import is a new, ordinary Operations verification operation. Its identity is
only `imported_by`. Checkpoint provenance keeps the original native run, selector
invocation and gateway allocation. SQLite is opened only on private byte copies.
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import gzip
import json
import math
from pathlib import Path
import re
import sqlite3
import tempfile

import procontract_operations as ops
from procontract_decision_replay import strict_json
from procontract_frozen_evidence import FrozenEvidence
from procontract_selector_capture import _base_instructions, _initial_prompt

PROFILE = "native-selector-source-profile/v1"
DESCRIPTOR = "native-selector-source-descriptor/v1"
SOURCE = "native-selector-source/v1"
PROOF = "native-selector-source-proof/v1"
RESULT = "native-selector-source-import/v1"
REMOTE_SCOPE = "host gateway terminal and native returned/closed observations; no raw SSE or provider-signed shutdown"
ROLES = {"manifest", "archive", "native_closure", "session", "allocation", "run", "final_closure",
         "owned_effects", "budget", "budget_wal", "budget_shm"}
REQUEST_ROLES = {"binding", "original", "effective", "audit_original", "audit_forwarded", "identity"}
HEX = re.compile(r"[0-9a-f]{64}")


def implementation():
    return ops.sha({"operations": ops.IMPLEMENTATION, "sqlite": sqlite3.sqlite_version, "sources": {
        name: ops.sha(Path(__file__).with_name(name).read_bytes()) for name in (
            "procontract_selector_native_source.py", "procontract_frozen_evidence.py",
            "procontract_selector_capture.py", "procontract_policy_splice.py", "procontract_decision_replay.py")}})


def same(a, b):
    return ops.encoded(a) == ops.encoded(b)


def text(value, what):
    if not isinstance(value, str) or not value:
        raise ValueError(what + " must be a nonempty string")
    return value


def digest(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError("expected a lowercase SHA256")
    return value


def reference(path, *, absent=False):
    path = Path(path).absolute()
    if absent and not path.exists():
        return {"path": str(path), "absent": True}
    return {"path": str(path), "sha256": ops.sha(path.read_bytes())}


def describe(state_root, capture, *, collection, owned_effects):
    """Host-only descriptor generator. It reads files, never opens a source DB."""
    state = Path(state_root).resolve(); capture = digest(capture)
    native, wire = state / "native-selector-captures", state / "selector-request-bindings"
    manifest_path = native / (capture + ".json")
    manifest = strict_json(manifest_path.read_bytes())
    thread = text(manifest["native_thread_id"], "native thread")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", thread):
        raise ValueError("native thread is not a filename identity")
    closures = [p for p in native.glob("*.json") if (lambda v:
        v.get("schema") == "native-selector-closure/v1" and v.get("capture") == capture)(strict_json(p.read_bytes()))]
    sessions = list((state / "codex-home/sessions").glob("**/*" + thread + ".jsonl"))
    if len(closures) != 1 or len(sessions) != 1:
        raise ValueError("source needs one native closure and one native session")
    files = {"manifest": reference(manifest_path), "archive": reference(native / (digest(manifest["archive_sha256"]) + ".json")),
             "native_closure": reference(closures[0]), "session": reference(sessions[0]),
             "allocation": reference(state / "allocation.json"), "run": reference(state / "run.json"),
             "final_closure": reference(state / "FINAL_CLOSURE.json"), "owned_effects": reference(owned_effects),
             "budget": reference(state / "budget.sqlite"),
             "budget_wal": reference(state / "budget.sqlite-wal", absent=True),
             "budget_shm": reference(state / "budget.sqlite-shm", absent=True)}
    requests = []
    for path in wire.glob(capture + ".*.binding.json"):
        b = strict_json(path.read_bytes()); n = text(b["gateway_request_id"], "gateway request")
        if not re.fullmatch(r"[1-9][0-9]*", n):
            raise ValueError("gateway request ID is not canonical")
        requests.append({"request_id": int(n), "ordinal": b["request_ordinal"], "binding": reference(path),
                         "original": reference(wire / (digest(b["original_sha256"]) + ".payload.json")),
                         "effective": reference(wire / (digest(b["effective_payload_sha256"]) + ".payload.json")),
                         "audit_original": reference(state / "requests" / (n + ".original.json.gz")),
                         "audit_forwarded": reference(state / "requests" / (n + ".forwarded.json.gz")),
                         "identity": reference(state / "requests" / (n + ".identity.json"))})
    return {"schema": DESCRIPTOR, "collection": text(collection, "collection"), "state_root": str(state),
            "capture": capture, "files": files, "requests": sorted(requests, key=lambda row: row["ordinal"])}


def profile(host_root, sources):
    if not isinstance(sources, dict) or not sources or any(not isinstance(k, str) or not k for k in sources):
        raise ValueError("profile needs named host sources")
    for ref in sources.values():
        _ref(ref)
    return {"schema": PROFILE, "implementation": implementation(),
            "host_root": str(Path(host_root).resolve()), "sources": strict_json(ops.encoded(sources))}


def _ref(ref, *, allow_absent=False):
    if not isinstance(ref, dict) or not Path(text(ref.get("path"), "source path")).is_absolute():
        raise ValueError("source reference must name an absolute path")
    if allow_absent and set(ref) == {"path", "absent"} and ref["absent"] is True:
        return
    if set(ref) != {"path", "sha256"}:
        raise ValueError("source reference must name exact bytes")
    digest(ref["sha256"])


def _descriptor(value):
    if (not isinstance(value, dict) or set(value) != {"schema", "collection", "state_root", "capture", "files", "requests"}
            or value["schema"] != DESCRIPTOR or set(value["files"]) != ROLES):
        raise ValueError("unknown or incomplete native source descriptor")
    text(value["collection"], "collection"); digest(value["capture"])
    state = Path(text(value["state_root"], "state_root"))
    if not state.is_absolute():
        raise ValueError("source state root must be absolute")
    for role, ref in value["files"].items():
        _ref(ref, allow_absent=role in ("budget_wal", "budget_shm"))
    if Path(value["files"]["budget"]["path"]) != state / "budget.sqlite":
        raise ValueError("budget path is not this source allocator")
    for role, suffix in (("budget_wal", "-wal"), ("budget_shm", "-shm")):
        if value["files"][role]["path"] != value["files"]["budget"]["path"] + suffix:
            raise ValueError("source DB sidecar path differs")
    if not isinstance(value["requests"], list) or not value["requests"]:
        raise ValueError("source must enumerate its native requests")
    for request in value["requests"]:
        if set(request) != REQUEST_ROLES | {"request_id", "ordinal"}:
            raise ValueError("request proof fields are incomplete")
        if type(request["request_id"]) is not int or request["request_id"] < 1 or type(request["ordinal"]) is not int or request["ordinal"] < 0:
            raise ValueError("request IDs and ordinals must be integers")
        for role in REQUEST_ROLES:
            _ref(request[role])
    return value


def _file(ref):
    path = Path(ref["path"])
    if ref.get("absent") is True:
        if path.exists() or path.is_symlink():
            raise ValueError("an absent source sidecar appeared")
        return None, {"absent": True}
    if path.is_symlink() or not path.is_file():
        raise ValueError("source is not a regular file")
    stat = path.stat(); raw = path.read_bytes()
    if ops.sha(raw) != ref["sha256"]:
        raise ValueError("source bytes differ from host descriptor")
    return raw, {"sha256": ops.sha(raw), "stat": {k: getattr(stat, k) for k in
        ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")}}


def _references(descriptor):
    refs = list(descriptor["files"].values())
    refs += [row[role] for row in descriptor["requests"] for role in sorted(REQUEST_ROLES)]
    found = {}
    for ref in refs:
        if ref["path"] in found and not same(found[ref["path"]], ref):
            raise ValueError("one source path has conflicting pins")
        found[ref["path"]] = ref
    return found


def _query_database(files):
    # `files` are already read/retained bytes. Source paths are never passed to SQLite.
    with tempfile.TemporaryDirectory(prefix="native-selector-custody-") as tmp:
        root = Path(tmp); database = root / "budget.sqlite"
        database.write_bytes(files["budget"])
        if files["budget_wal"] is not None:
            (root / "budget.sqlite-wal").write_bytes(files["budget_wal"])
        db = sqlite3.connect(database); db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA query_only=ON")
            return {table: [dict(row) for row in db.execute("SELECT * FROM " + table)] for table in
                    ("budget", "requests", "request_context", "response_identity")}
        finally:
            db.close()


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _validate(descriptor, files, requests):
    """Re-derive source facts, including request rows, solely from the pinned bytes."""
    descriptor = _descriptor(descriptor)
    read = lambda name: strict_json(files[name])
    manifest, archive, closure = read("manifest"), read("archive"), read("native_closure")
    if (ops.sha(files["manifest"]) != descriptor["capture"] or manifest.get("schema") != "native-selector-capture/v1"
            or manifest.get("entry") != "initial_native_selector_exchange"):
        raise ValueError("descriptor is not a native initial capture")
    if (ops.sha(files["archive"]) != manifest["archive_sha256"] or archive.get("schema") != "frozen-public-evidence/v1"
            or not same(archive["boundary"], manifest["boundary"])):
        raise ValueError("native evidence boundary differs")
    FrozenEvidence(archive)
    worker = text(manifest["native_thread_id"], "native worker")
    if (closure.get("schema") != "native-selector-closure/v1" or closure.get("capture") != descriptor["capture"]
            or closure.get("native_thread_id") != worker or closure.get("shutdown_confirmed") is not True
            or closure.get("outcome") != "worker_returned" or type(closure.get("request_metadata_issued")) is not int):
        raise ValueError("native source worker did not return and close")
    n = closure["request_metadata_issued"]
    if n < 1 or [row["ordinal"] for row in descriptor["requests"]] != list(range(n)):
        raise ValueError("source requests do not cover every issued native ordinal")
    if len({row["request_id"] for row in descriptor["requests"]}) != n:
        raise ValueError("source repeats a gateway request")
    session = [strict_json(line) for line in files["session"].splitlines() if line]
    metas = [row["payload"] for row in session if row.get("type") == "session_meta"]
    starts = [row["payload"] for row in session if row.get("type") == "event_msg" and row.get("payload", {}).get("type") == "task_started"]
    ends = [row["payload"] for row in session if row.get("type") == "event_msg" and row.get("payload", {}).get("type") == "task_complete"]
    if (len(metas) != 1 or metas[0].get("id") != worker or metas[0].get("source") != {"internal": "extension_worker"}
            or len(starts) != 1 or len(ends) != 1 or starts[0].get("turn_id") != ends[0].get("turn_id")):
        raise ValueError("source native session identity/return differs")
    turn = text(starts[0].get("turn_id"), "native turn")
    text(ends[0].get("last_agent_message"), "native final message")
    run, allocated = read("run"), read("allocation")
    record = text(manifest["boundary"]["contract"]["id"], "native responsibility")
    root_thread = text(run.get("thread_id"), "source root thread")
    if (run.get("status", {}).get("contract_id") != record or run.get("status", {}).get("thread_id") != root_thread
            or run.get("effects_resolved") is not True or run.get("native_exit", {}).get("status") != "exited"
            or type(run.get("native_exit", {}).get("returncode")) is not int):
        raise ValueError("source native run identity/closure differs")
    final, owned = read("final_closure"), read("owned_effects")
    history = final.get("history") or []
    observed = history[-1].get("observation", {}) if history else {}
    native, containers = observed.get("native", {}), observed.get("container_closure", {})
    gateway = native.get("gateway", {})
    if (final.get("effects_resolved") is not True or not history or history[-1].get("proven_closed") is not True
            or native.get("model_dispatch_stopped") is not True or native.get("process_exited") is not True
            or not same(native.get("group_members"), []) or not same(native.get("residuals"), [])
            or gateway.get("created") is not True or gateway.get("closing") is not True
            or gateway.get("socket_fileno") != -1 or gateway.get("thread_alive") is not False
            or type(gateway.get("handlers")) is not int or gateway["handlers"] != 0
            or type(gateway.get("connections")) is not int or gateway["connections"] != 0
            or containers.get("effects_resolved") is not True or not same(containers.get("remaining"), [])
            or not same(containers.get("errors"), []) or owned.get("effects_resolved") is not True
            or owned.get("main_closed") is not True or not same(owned.get("owned_running_containers"), [])
            or not same(owned.get("owned_native_processes"), [])):
        raise ValueError("source host effects have not closed")
    tables = _query_database(files)
    if len(tables["budget"]) != 1:
        raise ValueError("source has no unique allocator")
    budget = tables["budget"][0]; budget_profile = strict_json(budget["profile"])
    if not same({k: allocated.get(k) for k in budget_profile}, budget_profile):
        raise ValueError("source declared allocation differs from actual budget")
    for key in ("started", "deadline"):
        if not same(allocated.get(key), budget[key]) or not same(run.get("budget", {}).get(key), budget[key]):
            raise ValueError("original allocation times changed")
    if not _number(budget["started"]) or (budget["deadline"] is not None and not _number(budget["deadline"])):
        raise ValueError("original allocation is not armed")
    allocation_id = ops.sha((descriptor["files"]["budget"]["path"] + ":" + str(budget["started"])).encode())
    indexed = {str(row["id"]): row for row in tables["requests"]}
    contexts = {str(row["request_id"]): strict_json(row["snapshot_json"]) for row in tables["request_context"]}
    models = {str(row["request_id"]): row for row in tables["response_identity"]}
    if type(run.get("budget", {}).get("used")) is not int or run["budget"]["used"] != len(indexed):
        raise ValueError("source final request accounting differs")
    # The checkpoint execution is the original native ROOT, not only this worker.
    # A summary effects flag cannot close another reserved request whose outcome
    # is unknown. V1 deliberately admits only this fully returned source regime.
    for other in indexed.values():
        if (type(other["http_status"]) is not int or other["http_status"] != 200
                or other["terminal"] != "response.completed" or other["failure"] is not None
                or not _number(other["started"]) or not _number(other["finished"])
                or other["finished"] < other["started"]):
            raise ValueError("source root has a request without a completed host terminal")
    observations = []; initial = None
    for coordinate, raw in zip(descriptor["requests"], requests, strict=True):
        b = strict_json(raw["binding"]); original = strict_json(raw["original"]); effective = strict_json(raw["effective"])
        request_id, ordinal = str(coordinate["request_id"]), coordinate["ordinal"]
        row, context, model = indexed.get(request_id), contexts.get(request_id), models.get(request_id)
        if (not row or not context or not model or b.get("schema") != "native-selector-gateway-binding/v1"
                or b.get("capture") != descriptor["capture"] or b.get("native_thread_id") != worker
                or type(b.get("request_ordinal")) is not int or b["request_ordinal"] != ordinal
                or b.get("gateway_request_id") != request_id or b.get("initial") is not (ordinal == 0)
                or b.get("archive_sha256") != manifest["archive_sha256"] or not same(b.get("boundary"), manifest["boundary"])):
            raise ValueError("gateway binding has different native coordinates")
        for role, key, column in (("original", "original_sha256", "original_sha256"), ("effective", "effective_payload_sha256", "forwarded_sha256")):
            if ops.sha(raw[role]) != b[key] or row[column] != b[key]:
                raise ValueError("source request bytes differ from its actual allocator row")
        if gzip.decompress(raw["audit_original"]) != raw["original"] or gzip.decompress(raw["audit_forwarded"]) != raw["effective"]:
            raise ValueError("source independent gateway byte audit differs")
        if strict_json(raw["identity"]).get("thread-id") != worker:
            raise ValueError("source request header belongs to another native worker")
        metadata = {"procontract_capture": descriptor["capture"], "procontract_capture_thread": worker,
                    "procontract_capture_request": str(ordinal)}
        for payload in (original, effective):
            if any(payload.get("client_metadata", {}).get(k) != v for k, v in metadata.items()):
                raise ValueError("request native metadata differs")
            if (payload.get("model") != manifest["model"] or payload.get("reasoning", {}).get("effort") != manifest["reasoning_effort"]
                    or _base_instructions(payload)[0] != manifest["instructions"]
                    or not same(payload.get("text", {}).get("format", {}).get("schema"), manifest["response_schema"])):
                raise ValueError("actual request settings differ from native manifest")
            if (payload.get("stream") is not True or payload.get("previous_response_id")
                    or payload.get("conversation") or payload.get("background")):
                raise ValueError("source depends on unretained remote state")
        for key in ("tools", "tool_choice", "parallel_tool_calls", "reasoning", "text", "instructions", "client_metadata"):
            if (key in original) != (key in effective) or not same(original.get(key), effective.get(key)):
                raise ValueError("gateway changed native tools or model settings")
        if not same(b.get("base_instructions_location"), _base_instructions(effective)[1]):
            raise ValueError("source base instruction location differs")
        if (context.get("allocation_id") != allocation_id or not same(context, b.get("allocation"))
                or context.get("schema") != "task_resources/v1" or context.get("source") != "task_allocator"
                or context.get("model_requests", {}).get("current_request_included") is not True
                or not same(context.get("time", {}).get("started_at"), budget["started"])
                or not same(context.get("time", {}).get("deadline"), budget["deadline"])
                or not same(context.get("model_requests", {}).get("limit"), budget_profile["requests"])):
            raise ValueError("request did not use its original gateway allocation")
        item = effective["input"][-1]
        resource = item.get("content", [{}])[0].get("text", "")
        prefix, suffix = "<task_resource_snapshot>\n", "\n</task_resource_snapshot>"
        if (item.get("role") != "developer" or not resource.startswith(prefix) or not resource.endswith(suffix)
                or not same(strict_json(resource[len(prefix):-len(suffix)]), context)):
            raise ValueError("actual wire allocator snapshot differs")
        if (type(row["http_status"]) is not int or row["http_status"] != 200 or row["terminal"] != "response.completed"
                or row["failure"] is not None or not _number(row["started"]) or not _number(row["finished"])
                or row["finished"] < row["started"] or row["started"] < budget["started"]
                or (budget["deadline"] is not None and row["started"] >= budget["deadline"])
                or model["model"] != manifest["model"] or model["effort"] != manifest["reasoning_effort"]):
            raise ValueError("source request lacks its matching completed host terminal")
        if ordinal == 0:
            if (not same(effective["input"][:-1], original["input"]) or _initial_prompt(original) != manifest["prompt"]
                    or ops.sha(manifest["prompt"].encode()) != manifest["prompt_sha256"]
                    or any(x.get("type") in ("reasoning", "custom_tool_call", "custom_tool_call_output", "function_call")
                           or x.get("type") == "function_call_output" and x.get("call_id") for x in original["input"])):
                raise ValueError("initial source is not the captured initial exchange")
            initial = raw["effective"]
        observations.append({"gateway_request_id": request_id, "ordinal": ordinal,
                             **{key: row[key] for key in ("started", "finished", "http_status", "terminal", "failure", "original_sha256", "forwarded_sha256")}})
    origin = {"collection": descriptor["collection"], "source_root_thread": root_thread,
              "native_worker_thread": worker, "native_turn_id": turn, "capture": descriptor["capture"],
              "gateway_requests": observations, "original_allocation_id": allocation_id, "remote_terminal_scope": REMOTE_SCOPE,
              "source_root_requests": {"count": len(indexed), "all_completed": True,
                                       "rows_sha256": ops.sha(sorted(indexed.values(), key=lambda row: row["id"]))}}
    return {"record": record, "execution": "native-run:" + ops.sha({"collection": descriptor["collection"], "thread": root_thread}),
            "allocation": "native-allocation:" + ops.sha({"collection": descriptor["collection"], "allocation": allocation_id}),
            "closure": {"completed": True, "shutdown_confirmed": True, "execution_state": "native_run_exited",
                        "exit_code": run["native_exit"]["returncode"], "source_worker_returned": True, "remote_terminal_scope": REMOTE_SCOPE},
            "payload": initial, "evidence": archive, "boundary": archive["boundary"], "origin": origin,
            "native_archive_sha256": ops.sha(files["archive"]), "evidence_file_sha256": ops.sha((ops.encoded(archive) + "\n").encode())}


def _put_bytes(host, raw):
    return host.put("native_selector_source_bytes", {"sha256": ops.sha(raw), "bytes": len(raw),
        "encoding": "base64", "body": base64.b64encode(raw).decode()}, source="native-source-importer", notify=False)


def _get(host, identity, kind):
    body = host.get(identity, kind)
    if ops.sha({"schema": 1, "kind": kind, "body": body}) != identity:
        raise ValueError("retained object identity differs")
    return body


def _bytes(host, identity):
    body = _get(host, identity, "native_selector_source_bytes")
    raw = base64.b64decode(body["body"], validate=True)
    if body.get("encoding") != "base64" or type(body.get("bytes")) is not int or len(raw) != body["bytes"] or ops.sha(raw) != body["sha256"]:
        raise ValueError("retained source bytes differ")
    return raw


def _load(profile_path, assignment_path):
    profile_path, assignment_path = Path(profile_path).resolve(), Path(assignment_path).resolve()
    config_raw = profile_path.read_bytes(); config = strict_json(config_raw)
    if config.get("schema") != PROFILE or config.get("implementation") != implementation():
        raise ValueError("unqualified native import profile")
    host = ops.Operations(config["host_root"])
    assignment = strict_json(assignment_path.read_bytes()); item = host.inspect(assignment["request"]["id"])
    if (assignment_path != host.operation_dir(item["id"]) / "assignment.json" or item["state"] != "running"
            or not same(assignment, {**item["request"], "version": host.get(assignment["request"]["version"], "version")})):
        raise ValueError("import requires its actual running Operations assignment")
    cap = item["request"]["measurement"]; sources = cap.get("sources", {})
    if (cap["identity"] != implementation() or sources.get(str(profile_path)) != ops.sha(config_raw)
            or any(ops.sha(Path(path).read_bytes()) != sha for path, sha in sources.items())):
        raise ValueError("import implementation/profile binding changed")
    inputs = assignment["request"]["inputs"]
    if set(inputs) != {"source"} or inputs["source"] not in config["sources"]:
        raise ValueError("import selects one host-registered source")
    ref = config["sources"][inputs["source"]]; _ref(ref)
    descriptor_raw, descriptor_stat = _file(ref)
    descriptor = _descriptor(strict_json(descriptor_raw))
    return host, item, config_raw, str(profile_path), descriptor_raw, descriptor_stat, descriptor


def run(profile_path, assignment_path):
    host, item, profile_raw, profile_path, descriptor_raw, descriptor_stat, descriptor = _load(profile_path, assignment_path)
    directory = host.operation_dir(item["id"])
    with (directory / "native-import.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (directory / "result.json").exists():
            return strict_json((directory / "result.json").read_bytes())
        refs = _references(descriptor); raw_by_path = {}; before = {}
        for path, ref in refs.items():
            raw_by_path[path], before[path] = _file(ref)
        files = {role: raw_by_path[ref["path"]] for role, ref in descriptor["files"].items()}
        requests = [{role: raw_by_path[row[role]["path"]] for role in REQUEST_ROLES} for row in descriptor["requests"]]
        resolved = _validate(descriptor, files, requests)
        after = {path: _file(ref)[1] for path, ref in refs.items()}
        config = strict_json(profile_raw); chosen = item["request"]["request"]["inputs"]["source"]
        if not same(before, after) or _file(config["sources"][chosen])[1] != descriptor_stat:
            raise ValueError("source changed during custody/validation")
        blobs = {path: None if raw is None else _put_bytes(host, raw) for path, raw in raw_by_path.items()}
        proof = host.put("native_selector_source_proof", {"schema": PROOF, "descriptor": _put_bytes(host, descriptor_raw),
            "files": {role: blobs[ref["path"]] for role, ref in descriptor["files"].items()},
            "requests": [{role: blobs[row[role]["path"]] for role in REQUEST_ROLES} for row in descriptor["requests"]],
            "custody": {"source_before": before, "source_after": after, "engine_opened_source": False}},
            source="native-source-importer", notify=False)
        profile_id = host.put("native_selector_source_profile", {"path": profile_path, "sha256": ops.sha(profile_raw),
            "raw": _put_bytes(host, profile_raw)}, source="native-source-importer", notify=False)
        source = host.put("native_selector_source", {"schema": SOURCE, "implementation": implementation(), "proof": proof,
            "profile": profile_id, "source_name": chosen,
            "imported_by": {"execution": item["id"], "assignment": ops.sha(item["request"])},
            "derived": {k: v for k, v in resolved.items() if k not in ("payload", "evidence")}},
            source="native-source-importer", notify=False)
        result = {"schema": RESULT, "source": source, "effects_resolved": True, "admissible": False}
        ops.atomic(directory / "result.json", result)
        return result


def resolve(host, source_id):
    source = _get(host, source_id, "native_selector_source")
    if source.get("schema") != SOURCE or source.get("implementation") != implementation():
        raise ValueError("unknown native source producer")
    imported = source["imported_by"]; item = host.inspect(imported["execution"])
    if item["state"] != "finished" or item.get("outcome", {}).get("shutdown_confirmed") is not True:
        raise ValueError("native source importer has not closed")
    outcome = _get(host, item["outcome"]["object"], "operation_outcome")
    if (outcome.get("assignment") != ops.sha(item["request"]) or imported["assignment"] != outcome["assignment"]
            or item["request"]["measurement"]["identity"] != implementation()
            or not same(outcome.get("measurement"), {"schema": RESULT, "source": source_id, "effects_resolved": True, "admissible": False})):
        raise ValueError("source does not belong to its actual completed import")
    retained = _get(host, source["profile"], "native_selector_source_profile"); profile_raw = _bytes(host, retained["raw"])
    config = strict_json(profile_raw)
    if (ops.sha(profile_raw) != retained["sha256"] or config.get("schema") != PROFILE or config.get("implementation") != implementation()
            or item["request"]["measurement"].get("sources", {}).get(retained["path"]) != retained["sha256"]
            or not same(item["request"]["request"]["inputs"], {"source": source["source_name"]})):
        raise ValueError("source was not named by its admitted import profile")
    proof = _get(host, source["proof"], "native_selector_source_proof")
    descriptor_raw = _bytes(host, proof["descriptor"]); descriptor = _descriptor(strict_json(descriptor_raw))
    selected = config["sources"].get(source["source_name"])
    if not selected or ops.sha(descriptor_raw) != selected["sha256"] or proof.get("schema") != PROOF:
        raise ValueError("proof descriptor differs from the host source registry")
    if (proof["custody"].get("engine_opened_source") is not False
            or not same(proof["custody"]["source_before"], proof["custody"]["source_after"])):
        raise ValueError("source custody was not stable")
    files = {role: None if identity is None else _bytes(host, identity) for role, identity in proof["files"].items()}
    requests = [{role: _bytes(host, identity) for role, identity in row.items()} for row in proof["requests"]]
    if set(files) != ROLES or len(requests) != len(descriptor["requests"]):
        raise ValueError("source proof is incomplete")
    for role, raw in files.items():
        ref = descriptor["files"][role]
        if (raw is None) != (ref.get("absent") is True) or raw is not None and ops.sha(raw) != ref["sha256"]:
            raise ValueError("retained source role differs from its descriptor")
    for coordinate, raw in zip(descriptor["requests"], requests, strict=True):
        if set(raw) != REQUEST_ROLES or any(ops.sha(raw[role]) != coordinate[role]["sha256"] for role in REQUEST_ROLES):
            raise ValueError("retained request proof differs from descriptor")
    resolved = _validate(descriptor, files, requests)
    if not same({k: v for k, v in resolved.items() if k not in ("payload", "evidence")}, source["derived"]):
        raise ValueError("source facts do not rederive from immutable proof")
    return {**resolved, "source_id": source_id, "parent_measurement": item["request"]["request"]["measurement"],
            "imported_by": imported, "origin": {"kind": "native_selector_source", "object": source_id, **resolved["origin"]}}


def _capture_body(source_id, resolved, evidence_id):
    return {"schema": "selector-evidence-capture/v1", "origin": {"kind": "native_selector_source", "object": source_id},
            "payload_sha256": ops.sha(resolved["payload"]), "payload_base64": base64.b64encode(resolved["payload"]).decode(),
            "evidence": evidence_id, "evidence_file_sha256": resolved["evidence_file_sha256"],
            "source_archive_sha256": resolved["native_archive_sha256"], "boundary": resolved["boundary"],
            "scope": "native initial selector context/public evidence; original execution retained separately from import"}


def capture(host, source_id, target):
    resolved = resolve(host, source_id); raw = (ops.encoded(resolved["evidence"]) + "\n").encode()
    path = Path(target); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        import os
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    path.chmod(0o400)
    evidence_id = host.put("frozen_public_evidence", resolved["evidence"], source="native-source-importer", notify=False)
    return host.put("selector_evidence_capture", _capture_body(source_id, resolved, evidence_id), source="native-source-importer")


def resolve_capture(host, captured):
    if isinstance(captured, str):
        captured = _get(host, captured, "selector_evidence_capture")
    if (not isinstance(captured, dict) or any(k in captured for k in ("parent", "parent_assignment", "parent_outcome"))
            or set(captured.get("origin", {})) != {"kind", "object"} or captured["origin"]["kind"] != "native_selector_source"):
        raise ValueError("native origin is mutually exclusive with an Operations parent")
    source_id = captured["origin"]["object"]; resolved = resolve(host, source_id)
    evidence = _get(host, captured["evidence"], "frozen_public_evidence")
    if not same(evidence, resolved["evidence"]) or not same(captured, _capture_body(source_id, resolved, captured["evidence"])):
        raise ValueError("capture differs from its original native source")
    return resolved


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--profile", required=True); parser.add_argument("assignment")
    args = parser.parse_args(); print(ops.encoded(run(args.profile, args.assignment)))


if __name__ == "__main__":
    main()
