import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import install_procodex_standalone as installer
from procodex_standalone import selected_home


class StandaloneTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.protected = self.root / "live-home"
        self.protected.mkdir()
        self.live = self.root / "codex"
        self.live.write_text("untouched stock fixture\n")
        self.package = self.root / "review-package"
        (self.package / "bin").mkdir(parents=True)
        native = self.package / "bin/codex"
        native.write_text("#!/usr/bin/python3\nimport json,os,sys\nprint(json.dumps({'argv':sys.argv[1:],'home':os.environ.get('CODEX_HOME')}))\n")
        native.chmod(0o755)
        source = {"native_source_commit": "native-pin", "source_commit": "package-pin", "version": "0.0.0", "files": {"bin/codex": installer.sha(native)}}
        (self.package / "build-source.json").write_text(json.dumps(source))
        proof = self.root / "proof.json"; proof.write_text('{"passed":true}')
        gate = {"safe_to_install": True, "native_source_commit": "native-pin", "package_manifest_sha256": installer.sha(self.package / "build-source.json"), "evidence": [{"path": str(proof), "sha256": installer.sha(proof)}]}
        self.admission = self.root / "admission.json"; self.admission.write_text(json.dumps(gate))
        self.prefix, self.entry = self.root / "new-prefix", self.root / "path/procodex"

    def install(self):
        return installer.install(self.package, self.prefix, self.entry, self.admission, self.protected, self.live)

    def call(self, *args, code_home=None):
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin"}
        if code_home is not None: env["CODEX_HOME"] = str(code_home)
        return subprocess.run([str(self.entry), *args], env=env, capture_output=True, text=True)

    def test_default_home_forwarding_and_separate_build_identity(self):
        before = installer.pin(self.live)
        installed = self.install()
        result = self.call("exec", "--json", "prompt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"argv": ["exec", "--json", "prompt"], "home": str(self.prefix / "home")})
        info = self.call("--build-info")
        self.assertEqual(json.loads(info.stdout)["native_source_commit"], "native-pin")
        self.assertIn("procodex level-1", self.call("--version").stdout)
        self.assertEqual(installer.pin(self.live), before)
        self.assertTrue(installed["live_codex_unchanged"])
        self.assertEqual(sorted(p.name for p in (self.prefix / "home").iterdir()), ["config.toml"])
        self.assertEqual(sorted(p.name for p in self.entry.parent.iterdir()), ["procodex"])

    def test_live_home_alias_and_descendant_are_refused_before_native_runs(self):
        self.install()
        alias = self.root / "alias"; alias.symlink_to(self.protected, target_is_directory=True)
        for home in (self.protected, alias, self.protected / "child", self.root, ""):
            with self.subTest(home=home):
                result = self.call("exec", "anything", code_home=home)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
        self.assertEqual(list(self.protected.iterdir()), [])

    def test_explicit_private_home_is_forwarded(self):
        self.install()
        private = self.root / "other-private"
        self.assertEqual(json.loads(self.call("exec", code_home=private).stdout)["home"], str(private))

    def test_existing_entry_and_changed_package_refuse_without_mutation(self):
        self.entry.parent.mkdir(); self.entry.write_text("other owner")
        with self.assertRaises(FileExistsError): self.install()
        self.assertEqual(self.entry.read_text(), "other owner")
        self.assertFalse(self.prefix.exists())
        self.entry.unlink()
        (self.package / "bin/codex").write_text("changed")
        with self.assertRaisesRegex(ValueError, "package file changed"): self.install()
        self.assertFalse(self.entry.exists()); self.assertFalse(self.prefix.exists())

    def test_missing_admission_or_codex_named_entry_is_rejected(self):
        gate = json.loads(self.admission.read_text()); gate["safe_to_install"] = False
        self.admission.write_text(json.dumps(gate))
        with self.assertRaisesRegex(ValueError, "completed admission"): self.install()
        with self.assertRaisesRegex(ValueError, "named procodex"):
            installer.install(self.package, self.prefix, self.root / "path/codex", self.admission, self.protected, self.live)
        self.assertFalse(self.prefix.exists())


if __name__ == "__main__": unittest.main()
