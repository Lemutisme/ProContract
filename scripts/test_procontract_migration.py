"""The migration inventory against stores written by the older store binary. Read-only on copies;
no model, grader or container runs. Needs PRO_CONTRACT_LEGACY_BIN: a pro-contract-store built
before acceptances were recorded."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import procontract_migration as migration
import procontract_store as kernel

LEGACY = os.environ.get("PRO_CONTRACT_LEGACY_BIN")


@unittest.skipUnless(LEGACY and Path(LEGACY).exists(), "no older pro-contract-store binary given")
class OldStore(unittest.TestCase):
    """A store written by the older binary, and the helpers that build it."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.store = self.root / "old"
        self.store.mkdir()
        patch = mock.patch.dict(os.environ, {"PRO_CONTRACT_STORE_BIN": LEGACY})
        patch.start()
        self.addCleanup(patch.stop)
        self.count = 0

    def digest(self, value):
        return kernel.digest("test", value)

    def command(self, contract, role, provenance, body):
        current = kernel.contract(self.store, contract)
        return {"contract_id": contract, "expected_version": current["version"] if current else 0,
                "role": role, "provenance": provenance, "command": body}

    def apply(self, contract, role, provenance, body):
        self.count += 1
        return kernel.apply(self.store, f"k{self.count}", self.command(contract, role, provenance, body))

    def supported(self, contract, version):
        """Issue (when new), propose and support; return the discharge that would settle it."""
        if kernel.contract(self.store, contract) is None:
            self.apply(contract, "issuer", "delegate", {"type": "issue", "owner": "p", "bindings": {
                "terms_hash": self.digest(1), "capture_policy_hash": self.digest(2),
                "evidence_policy_hash": self.digest(3)}})
        state = kernel.contract(self.store, contract)
        if state["candidate"] is None:
            state = self.apply(contract, "executor", "automation", {"type": "propose", "subject_hash": version})
        coordinate = {"contract_id": contract, "revision": state["revision"], **state["bindings"],
                      "generation": state["candidate"]["generation"], "subject_hash": version,
                      "environment_digest": self.digest(4), "evaluator_digest": self.digest(5),
                      "evidence_hash": self.digest(6), "basis": self.digest(7), "reach": "within"}
        self.apply(contract, "verifier", "automation", {
            "type": "support", "coordinate": coordinate, "certificate": self.digest(8)})
        return self.command(contract, "settler", "human", {
            "type": "discharge", "coordinate": coordinate, "attestation": self.digest(9),
            "decision": {"type": "explicit"}})

    def adopt(self, contract, version, fence):
        self.count += 1
        kernel.adopt(self.store, f"adoption:{self.count}", self.supported(contract, version), fence,
                     kernel.identities(harness="H"))
        return kernel.events(self.store)[-1]["seq"]

    def snapshot(self):
        copy = self.root / f"copy-{self.count}"
        self.count += 1
        return copy, migration.snapshot(self.store, copy)

    def plan(self, **options):
        copy, receipt = self.snapshot()
        manifest = migration.plan(copy, **options)
        self.assertEqual(manifest["snapshot"]["files"], receipt["files"])
        # Planning read a private copy: the snapshot holds its files and its receipt, unchanged.
        self.assertEqual(migration._identity(copy), receipt["files"])
        self.assertLessEqual({path.name for path in copy.iterdir()}, {*migration.PARTS, migration.RECEIPT})
        return manifest



@unittest.skipUnless(LEGACY and Path(LEGACY).exists(), "no older pro-contract-store binary given")
class MigrationPlanTest(OldStore):
    def test_an_adoption_is_verified_by_its_retained_request(self):
        v0, v1 = self.digest("v0"), self.digest("v1")
        first = self.adopt("root", v0, None)
        second = self.adopt("child", v1, first)
        manifest = self.plan()
        entries = {entry["selection"]: entry for entry in manifest["entries"]}
        self.assertEqual([entries[s]["verified"] for s in (first, second)], [True, True])
        self.assertEqual((entries[first]["supports"], entries[second]["supports"]),
                         ("no_incumbent_expected", "unknown"))
        self.assertEqual(entries[second]["expected_incumbent_seq"], first)
        self.assertTrue(entries[first]["evidence"]["idempotency_key"].startswith("adoption:"))
        # What the old store selected is reported; it is not offered for activation, because
        # what the second adoption was built on is not recorded.
        self.assertEqual(manifest["historical_current"]["selection"], second)
        self.assertIsNone(manifest["would_register"]["selection_to_activate"])
        self.assertEqual([(row["supports_known"], row["supports"]) for row in manifest["would_register"]["acceptances"]],
                         [(True, []), (False, None)])
        self.assertEqual([item["selection"] for item in manifest["unknown"]], [second, second])
        self.assertIsNone(manifest["authorization"])
        self.assertFalse(manifest["source_protocol"]["already_records_acceptances"])
        self.assertEqual((manifest["applicable"], manifest["checks"]), (True, {
            "retained_result_states": {"enabled": True, "checked": 2, "passed": 2, "failed": 0},
            "kernel_chain": True, "journal_chain": True, "current_states_match_their_last_event": True}))
        self.assertEqual(manifest["state"]["kernel_events"], 8)
        self.assertTrue(manifest["implementation"]["store_binary"]["sha256"])

    def test_a_lone_first_adoption_can_be_activated(self):
        first = self.adopt("root", self.digest("v0"), None)
        manifest = self.plan()
        self.assertEqual(manifest["would_register"]["selection_to_activate"], {"selection": first, "acceptance": first})
        self.assertEqual(manifest["unknown"], [])

    def test_without_verification_a_manifest_is_only_a_diagnostic(self):
        self.adopt("root", self.digest("v0"), None)
        manifest = self.plan(verify=False)
        self.assertFalse(manifest["applicable"])
        self.assertEqual(len(manifest["diagnostic_only_because"]), 4)
        self.assertEqual(manifest["checks"]["retained_result_states"], {"enabled": False, "checked": 0, "passed": 0,
                                                                       "failed": 0})

    def test_a_plan_reads_only_a_snapshot_and_only_the_one_its_receipt_names(self):
        self.adopt("root", self.digest("v0"), None)
        with self.assertRaisesRegex(ValueError, "not a snapshot"):
            migration.plan(self.store)
        copy, _ = self.snapshot()
        with open(copy / migration.LEDGER, "ab") as stream:  # the snapshot is altered after its receipt
            stream.write(b"\0")
        with self.assertRaisesRegex(RuntimeError, "differ from its receipt"):
            migration.plan(copy)

    def test_the_receipt_is_read_once_and_must_not_change(self):
        self.adopt("root", self.digest("v0"), None)
        copy, _ = self.snapshot()
        real = migration._copy

        def the_receipt_is_rewritten(source, destination):
            real(source, destination)
            if source == copy:  # same database and log, another source named in the receipt
                receipt = json.loads((copy / migration.RECEIPT).read_text())
                (copy / migration.RECEIPT).write_text(json.dumps(dict(receipt, source="/somewhere/else")))

        with mock.patch.object(migration, "_copy", side_effect=the_receipt_is_rewritten):
            with self.assertRaisesRegex(RuntimeError, "changed while it was read"):
                migration.plan(copy)

    def test_a_retained_request_with_another_result_is_reported_as_such(self):
        first = self.adopt("root", self.digest("v0"), None)
        copy, receipt = self.snapshot()
        real = kernel.digest_input

        def another_state(domain, text, **more):
            return "f" * 64 if domain == "contract_state" and '"discharged"' in text else real(domain, text, **more)

        with mock.patch.object(kernel, "digest_input", side_effect=another_state):
            manifest = migration.plan(copy)
        entry, = manifest["entries"]
        self.assertEqual((entry["selection"], entry["verified"]), (first, False))
        self.assertIn("retained result is not the state", entry["reason"])
        self.assertEqual(manifest["checks"]["retained_result_states"]["failed"], 1)

    def test_no_manifest_when_the_snapshot_moves_while_it_is_read(self):
        self.adopt("root", self.digest("v0"), None)
        copy, _ = self.snapshot()
        real = migration._copy

        def a_writer_advances_the_snapshot(source, destination):
            real(source, destination)
            if source == copy and not (copy / "moved").exists():
                with open(copy / migration.LEDGER, "ab") as stream:
                    stream.write(b"\0")

        with mock.patch.object(migration, "_copy", side_effect=a_writer_advances_the_snapshot):
            with self.assertRaisesRegex(RuntimeError, "changed while it was read"):
                migration.plan(copy)

    def test_a_plain_settlement_with_an_appended_entry_is_not_an_adoption(self):
        v0 = self.digest("v0")
        discharge = self.supported("root", v0)
        self.count += 1
        kernel.apply(self.store, f"k{self.count}", discharge)  # settled through the plain command path
        forged = kernel.append(self.store, "selection", kernel.identities(harness="H"),
                               {"role": "incumbent", "version": v0, "adoption": "root"})["seq"]
        manifest = self.plan()
        entry, = manifest["entries"]
        self.assertEqual((entry["selection"], entry["verified"]), (forged, False))
        self.assertIn("no adoption request", entry["reason"])
        self.assertEqual(manifest["would_register"], {"acceptances": [], "selection_to_activate": None})
        self.assertEqual(kernel.contract(self.store, "root")["standing"], "discharged")  # left as it is

    def test_an_appended_entry_between_adoptions_is_reported_and_the_later_adoption_still_verifies(self):
        v0, v1 = self.digest("v0"), self.digest("v1")
        first = self.adopt("root", v0, None)
        stray = kernel.append(self.store, "selection", kernel.identities(),
                              {"role": "incumbent", "version": v0, "adoption": "root"})["seq"]
        second = self.adopt("child", v1, stray)  # the old fence was the latest entry, whoever wrote it
        manifest = self.plan()
        entries = {entry["selection"]: entry for entry in manifest["entries"]}
        self.assertEqual([entries[s]["verified"] for s in (first, stray, second)], [True, False, True])
        self.assertEqual(entries[second]["expected_incumbent_seq"], stray)
        self.assertIn(stray, [item["selection"] for item in manifest["unknown"]])

    def test_a_withdrawn_adoption_is_verified_and_not_current(self):
        v0 = self.digest("v0")
        first = self.adopt("root", v0, None)
        settled = kernel.contract(self.store, "root")
        self.apply("root", "verifier", "automation", {
            "type": "withdraw", "cause": self.digest("c"),
            "target": {"type": "settlement", "coordinate": settled["settlement"]["coordinate"]}})
        manifest = self.plan()
        entry, = manifest["entries"]
        self.assertEqual((entry["selection"], entry["verified"], entry["settles_its_contract_now"]),
                         (first, True, False))
        self.assertIsNone(manifest["would_register"]["selection_to_activate"])
        self.assertEqual(manifest["historical_current"]["settles_its_contract_now"], False)
        self.assertIn("no selection can be activated", manifest["unknown"][-1]["reason"])

    def test_a_snapshot_refuses_a_source_that_changes_and_a_used_destination(self):
        self.adopt("root", self.digest("v0"), None)
        copy = self.root / "copy"
        real = migration.shutil.copyfile

        def changing(source, destination):
            real(source, destination)
            with open(source, "ab") as stream:  # another writer touches the source during the copy
                stream.write(b"\0")

        with mock.patch.object(migration.shutil, "copyfile", side_effect=changing):
            with self.assertRaisesRegex(RuntimeError, "changed while it was copied"):
                migration.snapshot(self.store, copy)
        self.assertFalse(copy.exists())
        fresh = self.root / "fresh"
        fresh.mkdir()
        (fresh / "x").write_text("x")
        with self.assertRaisesRegex(ValueError, "new, empty directory"):
            migration.snapshot(self.store, fresh)

    def test_the_manifest_digest_binds_its_content(self):
        self.adopt("root", self.digest("v0"), None)
        manifest = self.plan()
        body = {key: value for key, value in manifest.items() if key != "digest"}
        self.assertEqual(manifest["digest"], migration.hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()).hexdigest())


CURRENT = os.environ.get("PRO_CONTRACT_STORE_BIN")


@unittest.skipUnless(LEGACY and Path(LEGACY).exists() and CURRENT and Path(CURRENT).exists(),
                     "needs both an older store binary and the one built from this branch")
class MigrationApplyTest(OldStore):
    def migrated(self, **options):
        """Plan from a snapshot of the old store, make a target from it, and apply with the new binary."""
        copy, _ = self.snapshot()
        manifest = migration.plan(copy)
        target = migration.target(copy, self.root / f"target-{self.count}")
        self.count += 1
        os.environ["PRO_CONTRACT_STORE_BIN"] = CURRENT  # from here the store binary of this branch
        return manifest, target

    def test_a_verified_first_adoption_is_registered_and_selected(self):
        v0, v1 = self.digest("v0"), self.digest("v1")
        first = self.adopt("root", v0, None)
        child = self.supported("child", v1)  # supported on the old store, not yet adopted
        manifest, target = self.migrated()
        result = migration.apply(manifest, target, authorization="the principal authorized this migration")
        self.assertEqual((result["registered"], result["activated"]), ([first], first))
        current = kernel.incumbent(target)
        self.assertEqual((current["selection"], current["acceptance"]["subject_hash"], current["acceptance"]["live"]),
                         (first, v0, True))
        self.assertEqual(migration.apply(manifest, target, authorization="the principal authorized this migration"),
                         result)
        with self.assertRaisesRegex(RuntimeError, "IdempotencyConflict"):
            migration.apply(manifest, target, authorization="someone else")
        # The migrated store goes on as a store of this branch: an adoption built on the migrated one.
        kernel.adopt(target, "adoption:child", child, first, kernel.identities(harness="H"), supports=[first])
        self.assertEqual(kernel.incumbent(target)["acceptance"]["supports"], [first])
        kernel._run(["verify", "--store", str(target)])
        self.assertIsNone(kernel.incumbent(self.store))  # the source was never opened by the new binary

    def test_unknown_supports_are_not_registered_and_nothing_is_selected(self):
        first = self.adopt("root", self.digest("v0"), None)
        self.adopt("child", self.digest("v1"), first)
        manifest, target = self.migrated()
        result = migration.apply(manifest, target, authorization="authorized")
        self.assertEqual((result["registered"], result["activated"]), ([first], None))
        self.assertIsNone(kernel.incumbent(target))
        self.assertEqual([row["selection"] for row in kernel.acceptances(target)], [first])

    def test_the_store_verifies_a_nomination_itself(self):
        v0 = self.digest("v0")
        discharge = self.supported("root", v0)
        self.count += 1
        kernel.apply(self.store, f"k{self.count}", discharge)
        forged = kernel.append(self.store, "selection", kernel.identities(harness="H"),
                               {"role": "incumbent", "version": v0, "adoption": "root"})["seq"]
        manifest, target = self.migrated()
        self.assertEqual(manifest["would_register"]["acceptances"], [])
        # A manifest edited to nominate the plain settlement, with its digest recomputed.
        manifest["would_register"]["acceptances"] = [{
            "selection": forged, "contract_id": "root", "event": manifest["state"]["kernel_events"],
            "generation": 1, "subject_hash": v0, "expected_incumbent_seq": None, "supports_known": True,
            "supports": []}]
        with self.assertRaisesRegex(RuntimeError, "NotAnAdoption"):
            migration.apply(self.resealed(manifest), target, authorization="authorized")
        self.assertEqual(kernel.acceptances(target), [])

    def resealed(self, manifest):
        """A manifest edited by hand, with its digest recomputed so that it matches."""
        body = {key: value for key, value in manifest.items() if key != "digest"}
        return dict(body, digest=migration.hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest())

    def test_only_a_target_made_from_the_manifests_snapshot_is_ever_opened(self):
        self.adopt("root", self.digest("v0"), None)
        copy, receipt = self.snapshot()
        manifest = migration.plan(copy)
        before = migration._identity(self.store)
        os.environ["PRO_CONTRACT_STORE_BIN"] = CURRENT
        for directory in (self.store, copy):  # the source itself, and the snapshot
            with self.assertRaisesRegex(ValueError, "not a migration target"):
                migration.apply(manifest, directory, authorization="authorized")
        self.assertEqual((migration._identity(self.store), migration._identity(copy)), (before, receipt["files"]))
        os.environ["PRO_CONTRACT_STORE_BIN"] = LEGACY
        other, _ = self.snapshot()
        elsewhere = migration.target(other, self.root / "elsewhere")
        os.environ["PRO_CONTRACT_STORE_BIN"] = CURRENT
        with self.assertRaisesRegex(ValueError, "another snapshot"):
            migration.apply(manifest, elsewhere, authorization="authorized")
        self.assertEqual(kernel.incumbent(migration.target(copy, self.root / "right")), None)

    def test_a_target_is_its_own_files_at_its_own_place(self):
        self.adopt("root", self.digest("v0"), None)
        copy, _ = self.snapshot()
        manifest = migration.plan(copy)
        before = migration._identity(self.store)
        os.environ["PRO_CONTRACT_STORE_BIN"] = CURRENT
        ledger = migration.LEDGER

        def link(name, make):
            made = migration.target(copy, self.root / name)
            for part in migration.PARTS:  # the receipt stays; the files are the source's
                if (self.store / part).exists():
                    (made / part).unlink()
                    make(self.store / part, made / part)
            return made

        for made in (link("symbolic", lambda source, path: path.symlink_to(source)), link("hard", os.link)):
            with self.assertRaisesRegex(ValueError, "is a link or has another name"):
                migration.apply(manifest, made, authorization="authorized")
            for part in migration.PARTS:
                (made / part).unlink(missing_ok=True)  # leave the source's files with one name again

        aside = migration.target(copy, self.root / "aside")
        (aside / "ledger.txt").symlink_to(self.store / ledger)  # any link in a target is refused
        with self.assertRaisesRegex(ValueError, "is a link or has another name"):
            migration.apply(manifest, aside, authorization="authorized")

        swapped = migration.target(copy, self.root / "swapped")
        migration.shutil.copyfile(copy / ledger, swapped / "other")  # same bytes, another file
        os.replace(swapped / "other", swapped / ledger)
        with self.assertRaisesRegex(ValueError, "not the file the target was made with"):
            migration.apply(manifest, swapped, authorization="authorized")

        moved = self.root / "moved"
        migration.shutil.copytree(migration.target(copy, self.root / "made"), moved)
        with self.assertRaisesRegex(ValueError, "made elsewhere"):
            migration.apply(manifest, moved, authorization="authorized")

        # A receipt planted in the source, with the manifest's source edited to point elsewhere.
        planted = migration.target(copy, self.root / "planted")
        migration.shutil.copyfile(planted / migration.TARGET, self.store / migration.TARGET)
        edited = dict(manifest, snapshot=dict(manifest["snapshot"], source=str(self.root / "nowhere")))
        with self.assertRaisesRegex(ValueError, "another snapshot"):
            migration.apply(self.resealed(edited), self.store, authorization="authorized")
        with self.assertRaisesRegex(ValueError, "made elsewhere"):
            migration.apply(manifest, self.store, authorization="authorized")
        (self.store / migration.TARGET).unlink()
        self.assertEqual(migration._identity(self.store), before)  # the source was never written

        # The legitimate target still migrates, and repeats exactly after its content has changed.
        good = migration.target(copy, self.root / "good")
        (self.root / "alias").symlink_to(good)  # another way to the same target is the same target
        result = migration.apply(manifest, self.root / "alias", authorization="authorized")
        self.assertNotEqual(migration._identity(good), migration._identity(copy))
        self.assertEqual(migration.apply(manifest, good, authorization="authorized"), result)
        self.assertEqual(migration._identity(self.store), before)

    def test_a_retained_request_cannot_be_reused_at_another_position(self):
        v0 = self.digest("v0")
        first = self.adopt("root", v0, None)
        clone = kernel.append(self.store, "selection", kernel.identities(harness="H"),
                              {"role": "incumbent", "version": v0, "adoption": "root"})["seq"]
        manifest, target = self.migrated()
        real = manifest["would_register"]["acceptances"][0]
        manifest["would_register"]["acceptances"].append(dict(real, selection=clone))
        manifest["would_register"]["selection_to_activate"] = {"selection": clone, "acceptance": clone}
        with self.assertRaisesRegex(RuntimeError, "NotAnAdoption"):
            migration.apply(self.resealed(manifest), target, authorization="authorized")
        self.assertEqual(kernel.acceptances(target), [])

    def test_a_flag_does_not_make_unknown_supports_known(self):
        first = self.adopt("root", self.digest("v0"), None)
        second = self.adopt("child", self.digest("v1"), first)
        manifest, target = self.migrated()
        for row in manifest["would_register"]["acceptances"]:
            row.update(supports_known=True, supports=[])
        manifest["would_register"]["selection_to_activate"] = {"selection": second, "acceptance": second}
        with self.assertRaisesRegex(RuntimeError, "stays unknown"):
            migration.apply(self.resealed(manifest), target, authorization="authorized")
        self.assertEqual(kernel.acceptances(target), [])

    def test_a_migration_does_not_choose_an_earlier_version(self):
        first = self.adopt("root", self.digest("v0"), None)
        self.adopt("child", self.digest("v1"), first)
        manifest, target = self.migrated()
        self.assertIsNone(manifest["would_register"]["selection_to_activate"])
        manifest["would_register"]["selection_to_activate"] = {"selection": first, "acceptance": first}
        with self.assertRaisesRegex(RuntimeError, "not the store's current selection"):
            migration.apply(self.resealed(manifest), target, authorization="authorized")
        self.assertEqual(kernel.acceptances(target), [])
        # The principal choosing the registered first version is a separate, recorded selection.
        untouched, target = self.migrated()
        migration.apply(untouched, target, authorization="authorized")
        self.assertIsNone(kernel.incumbent(target))
        kernel.select(target, "reselect", first, None, kernel.identities(),
                      {"type": "explicit", "receipt": "the principal selected v0"})
        self.assertEqual(kernel.incumbent(target)["acceptance"]["selection"], first)

    def test_a_manifest_applies_only_to_the_state_it_was_planned_for(self):
        self.adopt("root", self.digest("v0"), None)
        manifest, target = self.migrated()
        kernel.append(target, "exposure", kernel.identities(), {"view": "x", "pool": "p", "grain": "cases"})
        with self.assertRaisesRegex(RuntimeError, "MigrationStateChanged"):
            migration.apply(manifest, target, authorization="authorized")
        self.assertEqual(kernel.acceptances(target), [])

    def test_a_diagnostic_or_altered_manifest_or_an_empty_authorization_is_refused(self):
        self.adopt("root", self.digest("v0"), None)
        copy, _ = self.snapshot()
        diagnostic = migration.plan(copy, verify=False)
        manifest = migration.plan(copy)
        target = migration.target(copy, self.root / "target")
        os.environ["PRO_CONTRACT_STORE_BIN"] = CURRENT
        with self.assertRaisesRegex(ValueError, "diagnostic manifest"):
            migration.apply(diagnostic, target, authorization="authorized")
        with self.assertRaisesRegex(ValueError, "does not match its digest"):
            migration.apply(dict(manifest, applicable=True, unknown=[{"x": 1}]), target, authorization="authorized")
        with self.assertRaisesRegex(ValueError, "non-empty string"):
            migration.apply(manifest, target, authorization=" ")
        with self.assertRaisesRegex(ValueError, "new, empty directory"):
            migration.target(copy, target)
        self.assertEqual(kernel.acceptances(target), [])


if __name__ == "__main__":
    unittest.main()
