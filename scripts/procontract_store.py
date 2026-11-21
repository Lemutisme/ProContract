"""Python access to a ProContract store through the `pro-contract-store` CLI, so that hashing,
capture and the experiment chain have exactly one implementation."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_BIN = REPO / "codex-rs" / "target" / "debug" / "pro-contract-store"


def store_bin() -> str:
    return os.environ.get("PRO_CONTRACT_STORE_BIN", str(DEFAULT_BIN))


def _run(
    args: list[str], stdin: str | None = None, *, binary: str | Path | None = None
) -> str:
    result = subprocess.run(
        [store_bin() if binary is None else binary, *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"pro-contract-store {args[0]} failed: {result.stderr.strip()}"
        )
    return result.stdout


def identities(
    harness: str | None = None,
    model: str | None = None,
    effort: str | None = None,
    evaluator_epoch: str | None = None,
) -> dict:
    return {
        "harness": harness,
        "policies": {},
        "model": model,
        "effort": effort,
        "evaluator_epoch": evaluator_epoch,
    }


def capture(store: Path, root: Path, excluded: list[str]) -> dict:
    args = ["capture", "--store", str(store), "--root", str(root)]
    for path in excluded:
        args += ["--exclude", path]
    return json.loads(_run(args))


def materialize(store: Path, subject: str, dest: Path) -> None:
    _run(
        [
            "materialize",
            "--store",
            str(store),
            "--subject",
            subject,
            "--dest",
            str(dest),
        ]
    )


def append(store: Path, kind: str, identities: dict, body: dict) -> dict:
    event = {"kind": kind, "identities": identities, "body": body}
    return json.loads(_run(["append", "--store", str(store)], stdin=json.dumps(event)))


def events(store: Path) -> list[dict]:
    if not (store / "ledger_1.sqlite").exists():
        return []
    return json.loads(_run(["events", "--store", str(store)]))


def apply(store: Path, key: str, command: dict) -> dict:
    """Applies an authenticated kernel command; a rejection raises with the kernel's reason."""
    return json.loads(
        _run(["apply", "--store", str(store), "--key", key], stdin=json.dumps(command))
    )


def contract(store: Path, contract_id: str) -> dict | None:
    if not (store / "ledger_1.sqlite").exists():
        return None
    return json.loads(_run(["contract", "--store", str(store), "--id", contract_id]))


def digest(domain: str, value, *, binary: str | Path | None = None) -> str:
    """Use the explicit authenticated binary when supplied, otherwise the host default."""
    return digest_input(domain, json.dumps(value), binary=binary)


def digest_input(domain: str, input_json: str, *, binary: str | Path | None = None) -> str:
    """Submit a retained JSON representation without reordering its objects.

    The native v1 algorithm serializes its parsed JSON value. With preserve_order
    enabled, dictionary insertion order is part of that historical preimage.
    This function does not change native hashing or reinterpret old digests.
    """
    return _run(
        ["digest", "--domain", domain], stdin=input_json, binary=binary
    ).strip()


JSON_BINDING_CODEC = "pro-contract/native-json-input-v1"


def json_binding(domain: str, value) -> dict:
    """Freeze the exact digest request as a string inside a versioned receipt."""
    wire = json.dumps(value, allow_nan=False)
    command = store_bin()
    executable = Path(shutil.which(command) or command).resolve(strict=True)
    with executable.open('rb') as stream:
        executable_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {"codec": JSON_BINDING_CODEC, "domain": domain, "input_json": wire,
            "input_sha256": hashlib.sha256(wire.encode()).hexdigest(),
            "native_binary": {"path": str(executable), "sha256": executable_hash},
            "digest": digest_input(domain, wire, binary=executable)}


def binding_matches(binding: dict, domain: str, value) -> bool:
    """Check custody and native hash, then compare JSON structure modulo key order.

    Array order and numeric representations remain significant. A reordered
    display is not a new contract; a changed value is not a harmless reformat.
    Structural comparison never replaces or changes the accepted native digest.
    """
    if binding.get("codec") != JSON_BINDING_CODEC or binding.get("domain") != domain:
        raise ValueError("unknown binding codec or domain")
    wire = binding.get("input_json")
    if not isinstance(wire, str) or hashlib.sha256(wire.encode()).hexdigest() != binding.get("input_sha256"):
        raise ValueError("binding input identity differs")
    if digest_input(domain, wire) != binding.get("digest"):
        raise ValueError("binding native digest differs")
    canonical = lambda v: json.dumps(v, sort_keys=True, ensure_ascii=True, allow_nan=False)
    # Round-trip the caller through its actual JSON wire representation too.
    return canonical(json.loads(wire)) == canonical(json.loads(json.dumps(value, allow_nan=False)))


def adopt(
    store: Path,
    key: str,
    command: dict,
    expected_incumbent_seq: int | None,
    identities: dict,
    supports: list[int] | None = None,
) -> dict:
    """Atomically settle an adoption and publish its incumbent, fenced by the old selection.

    `supports` names exact earlier acceptances by the sequence number of their adopting
    selection; the store commits only while every one of them is live.
    """
    request = {
        "command": command,
        "expected_incumbent_seq": expected_incumbent_seq,
        "identities": identities,
    }
    if supports:
        request["supports"] = supports
    return json.loads(
        _run(["adopt", "--store", str(store), "--key", key], stdin=json.dumps(request))
    )


def select(
    store: Path,
    key: str,
    acceptance: int,
    expected_incumbent_seq: int | None,
    identities: dict,
    decision: dict,
) -> int:
    """Record a new incumbent selection of a live acceptance under the incumbent fence."""
    request = {
        "acceptance": acceptance,
        "expected_incumbent_seq": expected_incumbent_seq,
        "identities": identities,
        "decision": decision,
    }
    return int(_run(["select", "--store", str(store), "--key", key], stdin=json.dumps(request)))


def live(store: Path, selection: int) -> bool:
    """Whether the acceptance recorded by an adopting selection is live in the store now."""
    return _run(["live", "--store", str(store), "--selection", str(selection)]).strip() == "true"


def incumbent(store: Path) -> dict | None:
    """The current selection of an incumbent as the store recorded it, with the acceptance it
    selects and that acceptance's liveness; `None` before any. Selections other writers appended
    to the journal are not it."""
    if not (store / "ledger_1.sqlite").exists():
        return None
    return json.loads(_run(["incumbent", "--store", str(store)]))


def acceptances(store: Path) -> list[dict]:
    """Every acceptance the store recorded in an adoption transaction, with its liveness."""
    if not (store / "ledger_1.sqlite").exists():
        return []
    return json.loads(_run(["acceptances", "--store", str(store)]))


def migrate(store: Path, key: str, request: dict) -> dict:
    """Register adoptions an earlier binary recorded only in the journal. The store verifies each
    nominated adoption against its own records in one transaction; see procontract_migration."""
    return json.loads(_run(["migrate", "--store", str(store), "--key", key], stdin=json.dumps(request)))
