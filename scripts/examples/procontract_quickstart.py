#!/usr/bin/env python3
"""ProContract quickstart: one task and one self-improvement step under the kernel.

Everything runs in a temporary directory, with this repository's Python modules and the
`pro-contract-store` binary. No model, container or network is used. The worker and the
verifier of part (a) are a few lines of this script, and every measured number in part (b)
is synthetic: generated here from a fixed seed, not taken from any run.

    cd scripts
    PRO_CONTRACT_STORE_BIN=/path/to/pro-contract-store python3 examples/procontract_quickstart.py

Part (a) uses the kernel commands directly through `procontract_store`. Part (b) uses
`procontract_credit`, which records an improvement as a Contract in the same store.
See docs/PROCONTRACT_QUICKSTART.md.
"""

import argparse
import os
import random
import runpy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # scripts/, where the modules live

import procontract_credit as credit  # noqa: E402
import procontract_store as kernel  # noqa: E402


def store_binary() -> str:
    """The store binary both modules call, named by PRO_CONTRACT_STORE_BIN."""
    path = os.environ.get("PRO_CONTRACT_STORE_BIN")
    if not path:
        sys.exit(
            "PRO_CONTRACT_STORE_BIN is not set.\n"
            "Build the store binary (docs/PROCONTRACT_QUICKSTART.md gives the command) and set\n"
            "PRO_CONTRACT_STORE_BIN to the path of the built pro-contract-store executable."
        )
    binary = Path(path).expanduser().resolve()
    if not (binary.is_file() and os.access(binary, os.X_OK)):
        sys.exit(f"PRO_CONTRACT_STORE_BIN does not name an executable file: {path}")
    os.environ["PRO_CONTRACT_STORE_BIN"] = str(binary)
    return str(binary)


def short(digest: str) -> str:
    return digest[:10]


def state(contract: dict) -> str:
    """One line of what the kernel holds for a Contract."""
    candidate, support, settlement = contract["candidate"], contract["support"], contract["settlement"]
    line = (
        f"standing={contract['standing']} version={contract['version']} generation={contract['generation']} "
        f"candidate={short(candidate['subject_hash']) if candidate else 'none'} "
        f"support={'reach ' + support['coordinate']['reach'] if support else 'none'}"
    )
    return line + (f" settlement={settlement['decision']['type']}" if settlement else "")


def reason(refusal: Exception) -> str:
    """The kernel's own reason, without the wrapper's prefix."""
    return str(refusal).split("Error: ", 1)[-1]


def apply(store: Path, key: str, contract_id: str, role: str, provenance: str, command: dict) -> dict:
    """Send one command for an actor the host has authenticated as `role`. The command names the
    Contract version the actor observed; the kernel returns the next state or rejects."""
    current = kernel.contract(store, contract_id)
    return kernel.apply(store, key, {
        "contract_id": contract_id, "expected_version": current["version"] if current else 0,
        "role": role, "provenance": provenance, "command": command})


# ------------------------------------------------------------------ (a) one task

TASK = "Write median(values) in median.py: the median of a non-empty list of numbers."
CHECKS = [([3, 1, 2], 2), ([5], 5), ([1, 2, 3, 4], 2.5), ([4, 1, 3, 2], 2.5)]

FIRST_ATTEMPT = """def median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2]
"""

REPAIRED = """def median(values):
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2
"""


def worker_writes(store: Path, workspace: Path, source: str) -> str:
    """The scripted worker edits its workspace; the host captures the files as a frozen,
    content-addressed subject and returns its hash."""
    (workspace / "median.py").write_text(source)
    return kernel.capture(store, workspace, [])["subject_hash"]


def verifier_checks(store: Path, subject: str, scratch: Path) -> list[dict]:
    """The scripted verifier runs its checks on the frozen subject, not on the worker's
    workspace: the store writes the captured bytes into a fresh directory."""
    frozen = scratch / f"frozen-{short(subject)}"
    kernel.materialize(store, subject, frozen)
    median = runpy.run_path(str(frozen / "median.py"))["median"]
    return [{"input": values, "expected": expected, "returned": median(list(values))}
            for values, expected in CHECKS]


def coordinate(contract: dict, evidence: str) -> dict:
    """What a Support binds: the Contract's terms and policies, the exact candidate and its
    generation, and the verifier's evaluator, environment and evidence."""
    candidate = contract["candidate"]
    return {
        "contract_id": contract["id"], "revision": contract["revision"], **contract["bindings"],
        "generation": candidate["generation"], "subject_hash": candidate["subject_hash"],
        "environment_digest": kernel.digest("environment", "quickstart: this Python process"),
        "evaluator_digest": kernel.digest("evaluator", "quickstart: scripted checks"),
        "evidence_hash": evidence,
        "basis": kernel.digest("basis", {"checks": CHECKS}),
        # The checks are ones the worker could have run itself, so this evidence is within its reach.
        "reach": "within"}


def one_task(store: Path, scratch: Path) -> None:
    print("(a) One task under the kernel")
    print(f"    task: {TASK}")
    contract_id = "task.median"
    workspace = scratch / "workspace"
    workspace.mkdir()
    (workspace / "TASK.md").write_text(TASK + "\n")
    intake = kernel.capture(store, workspace, [])  # the store's capture policy, bound at issue

    contract = apply(store, "a1-issue", contract_id, "issuer", "human", {
        "type": "issue", "owner": "user", "bindings": {
            "terms_hash": kernel.digest("terms", {"request": TASK}),
            "capture_policy_hash": intake["capture_policy"],
            "evidence_policy_hash": kernel.digest("evidence_policy", {"checks": CHECKS})}})
    print("  1. the user issues a Contract")
    print(f"     {state(contract)}")

    subject = worker_writes(store, workspace, FIRST_ATTEMPT)
    contract = apply(store, "a2-propose", contract_id, "executor", "automation",
                     {"type": "propose", "subject_hash": subject})
    print("  2. the worker proposes a candidate (its captured workspace)")
    print(f"     {state(contract)}")

    print('  3. the worker reports "done"')
    print("     the report is text: no kernel command carries it, and a worker's only command is Propose")
    claimed = coordinate(contract, kernel.digest("evidence", "the worker says it is done"))
    attempts = {
        "Support of its own candidate": {
            "type": "support", "coordinate": claimed, "certificate": kernel.digest("certificate", claimed)},
        "Discharge of the Contract": {
            "type": "discharge", "coordinate": claimed, "decision": {"type": "explicit"},
            "attestation": kernel.digest("attestation", "the worker says it is done")}}
    for index, (name, command) in enumerate(attempts.items()):
        try:
            apply(store, f"a3-done-{index}", contract_id, "executor", "automation", command)
            sys.exit(f"unexpected: the kernel accepted the worker's {name}")
        except RuntimeError as refusal:
            print(f"     the worker sends {name}: {reason(refusal)}")
    unchanged = kernel.contract(store, contract_id)
    if unchanged != contract:
        sys.exit("unexpected: the Contract changed")
    print(f"     {state(unchanged)}   (unchanged)")

    results = verifier_checks(store, subject, scratch)
    counterexample = next(row for row in results if row["returned"] != row["expected"])
    contract = apply(store, "a4-defeat", contract_id, "verifier", "automation", {
        "type": "defeat", "defeater": kernel.digest("defeater", counterexample),
        "target": {"type": "candidate", **contract["candidate"]}})
    print("  4. the verifier checks the frozen candidate and records a Defeat")
    print(f"     counterexample: median({counterexample['input']}) returned {counterexample['returned']}, "
          f"expected {counterexample['expected']}")
    print(f"     {state(contract)}")

    subject = worker_writes(store, workspace, REPAIRED)
    contract = apply(store, "a5-propose", contract_id, "executor", "automation",
                     {"type": "propose", "subject_hash": subject})
    print("  5. the worker proposes a repaired candidate")
    print(f"     {state(contract)}")

    results = verifier_checks(store, subject, scratch)
    if any(row["returned"] != row["expected"] for row in results):
        sys.exit("unexpected: the repaired candidate fails a check")
    supported = coordinate(contract, kernel.digest("evidence", results))
    contract = apply(store, "a6-support", contract_id, "verifier", "automation", {
        "type": "support", "coordinate": supported, "certificate": kernel.digest("certificate", supported)})
    print("  6. the verifier checks the frozen candidate and records Support")
    print(f"     checks passed: {len(results)} of {len(CHECKS)}")
    print(f"     {state(contract)}")
    print("     still outstanding: Support is the verifier's assessment of this exact candidate;")
    print("     closing the Contract is a separate command, Discharge, that neither worker nor verifier can send")


# ------------------------------------------------------------------ (b) one self-improvement step

UNITS = 6
CASES = [f"case-{index}" for index in range(4)]


def synthetic_outcomes(rng: random.Random, effect: bool) -> list[tuple[dict, dict]]:
    """SYNTHETIC results, one pair per unit: which of the measure's cases pass when one recorded
    state is continued under the incumbent and under the candidate. Made-up numbers: v0 passes one
    or two of the four cases; v1 passes one or two more, except on one unit where it changes
    nothing. With `effect` false, v1 passes exactly what v0 passes on every unit."""
    def passing(count: int) -> dict:
        return {"cases": {name: index < count for index, name in enumerate(CASES)}}

    flat = rng.randrange(UNITS)
    rows = []
    for index in range(UNITS):
        base = rng.choice([1, 2])
        gain = rng.choice([1, 2]) if effect and index != flat else 0
        rows.append((passing(base), passing(base + gain)))
    return rows


def one_improvement(store: Path, seed: int, effect: bool) -> str:
    print("(b) One self-improvement step, as a Contract in the same store")
    digest = kernel.digest
    versions = {"v0": digest("version", "quickstart harness v0"),
                "v1": digest("version", "quickstart harness v1: v0 with one change")}
    name = {value: key for key, value in versions.items()}
    scope = "quickstart harness"

    credit.install(store, "principal", scope, versions["v0"], "principal",
                   authorization="quickstart: the principal chose v0")
    print("  1. the principal installs version v0 for the scope")
    print(f"     incumbent: {name[credit.incumbent(store)['version']]}")

    measure = {"pool": digest("pool", "quickstart: synthetic held-out cases"),
               "evaluator": digest("evaluator", "quickstart: scripted evaluator"),
               "environment": digest("environment", "quickstart: scripted environment"),
               "cases": credit.case_set(CASES), "total": len(CASES)}
    terms = {
        "scope": scope,
        "defect": "illustrative: after a Defeat the next attempt does not receive the counterexample",
        "evidence": ["illustrative: step 4 of part (a)"],
        "criterion": {"measure": measure, "min_tasks": UNITS, "min_effect": 0.1, "lower_bound": 0,
                      "cost_unit": "requests", "max_cost_ratio": 1.5},
        "convention": "adopt when the criterion is met on cases beyond the improver's reach"}
    try:
        credit.locate(store, "principal", dict(terms, version=versions["v1"]))
        sys.exit("unexpected: terms that name a version were accepted")
    except ValueError as refusal:
        refused = str(refusal)
    contract = credit.locate(store, "principal", terms)
    contract_id = contract["id"]
    criterion = terms["criterion"]
    print("  2. a defect is located and its terms are issued, before any candidate exists")
    print(f"     criterion: at least {criterion['min_tasks']} units, mean effect >= {criterion['min_effect']}, "
          f"interval above {criterion['lower_bound']}, cost ratio <= {criterion['max_cost_ratio']}")
    print(f"     convention: {terms['convention']}")
    print(f"     Contract {contract_id[:24]}...: {state(contract)}")
    print(f"     terms that name a version are refused: {refused}")

    contract = credit.propose(store, contract_id, versions["v1"], base=versions["v0"],
                              change="deliver the last counterexample to the next attempt", author="improver")
    print("  3. the improver proposes candidate v1: one change to the incumbent v0")
    print(f"     {state(contract)}")

    print(f"  4. measured effect on {UNITS} units (SYNTHETIC numbers generated by this script, seed {seed})")
    print("     unit     v0    v1    difference")
    pairs = []
    for index, outcomes in enumerate(synthetic_outcomes(random.Random(seed), effect), start=1):
        unit = f"unit-{index}"
        # One recorded state of the unit, continued once under each version.
        origin = credit.state(store, kernel.identities(), unit, digest("state", [unit, "origin"]),
                              task=unit, family="synthetic")
        sides, values = [], []
        for version, outcome in zip(("v0", "v1"), outcomes):
            subject = digest("state", [unit, version])
            sides.append(credit.state(
                store, kernel.identities(harness=version), unit, subject, task=unit, family="synthetic",
                parent=origin, inputs={"version": versions[version]},
                receipt=digest("execution", [unit, version]),
                cost={"requests": 10 if version == "v0" else 12}))
            credit.measure(store, measure, subject, outcome)
            values.append(credit.value(outcome))
        pairs.append(tuple(sides))
        print(f"     {unit}   {values[0]:.2f}  {values[1]:.2f}  {values[1] - values[0]:+.2f}")

    judged = credit.judge(store, contract_id, pairs)
    statement, measured = judged["statement"], judged["statement"]["effect"]
    print("  5. the judgment is computed from the recorded states and measurements")
    print(f"     effect: mean {measured['mean']:+.3f}, 95% interval [{measured['low']:+.3f}, {measured['high']:+.3f}], "
          f"{measured['tasks']} units, {measured['wins']} better, {measured['losses']} worse, "
          f"cost ratio {measured['cost_ratio']:.2f}")
    print(f"     unmet criteria: {statement['unmet'] or 'none'}")
    print(f"     reach: {statement['reach']} (the improver has not read these cases or drawn a verdict from them)")
    print(f"     {state(judged['contract'])}")

    if statement["unmet"]:
        print("  6. no adoption: the judgment is a Defeat, so there is no Support to settle on")
        print(f"     incumbent: {name[credit.incumbent(store)['version']]}")
        print(f"     improvements still owed: {len(credit.backlog(store))} (the defect stays in the backlog)")
        return contract_id
    settled = credit.adopt(store, contract_id)
    decision = settled["settlement"]["decision"]["type"]
    serving = credit.incumbent(store)
    print("  6. adoption: the Contract's Discharge and the new incumbent, in one store transaction")
    print(f"     decision: {decision} under the convention in the terms (no explicit act by the principal)")
    print(f"     {state(settled)}")
    print(f"     incumbent: {name[serving['version']]}, adopted by Contract {serving['adoption'][:24]}...")
    print(f"     improvements still owed: {len(credit.backlog(store))}")
    for row in credit.history(store, contract_id):
        print(f"     history: candidate {name[row['version']]} by {row['author']}, "
              f"{len(row['judgments'])} judgment, adopted at journal entry {row['adopted']}")
    return contract_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", action="store_true",
                        help="keep the temporary directory and print its path, to inspect the store afterwards")
    parser.add_argument("--seed", type=int, default=0, help="seed of the synthetic numbers in part (b)")
    parser.add_argument("--no-effect", action="store_true",
                        help="make the synthetic candidate no better than the incumbent, to see a Defeat instead")
    args = parser.parse_args()
    binary = store_binary()
    root = Path(tempfile.mkdtemp(prefix="procontract-quickstart-"))
    store = root / "store"
    print("ProContract quickstart: no model, no container, no network")
    print()
    try:
        one_task(store, root)
        print()
        improvement = one_improvement(store, args.seed, not args.no_effect)
        print()
        subprocess.run([binary, "verify", "--store", str(store)], check=True)
        kinds: dict = {}
        for row in kernel.events(store):
            kinds[row["event"]["kind"]] = kinds.get(row["event"]["kind"], 0) + 1
        print("The store's journal verifies as one hash chain: "
              + ", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items())))
        for contract_id in ("task.median", improvement):
            print(f"  {contract_id[:24]:<24}  {state(kernel.contract(store, contract_id))}")
    finally:
        if args.keep:
            print(f"kept: {root}")
        else:
            shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
