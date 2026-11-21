#!/usr/bin/env python3
"""Qualify real arg0 dispatch through direct and explicit-loader entrypoints.

Build with: cargo build --offline --locked -p codex-arg0 --example launch_probe
Then run this script with --probe TARGET/debug/examples/launch_probe --output FILE.
All edits and compiler outputs belong to a private temporary directory beside FILE.
No model, task executable, container or benchmark process is started.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


PATCH_AND_REEXEC = r'''set -eu
test -z "${CODEX_INTERNAL_ARG0_DISPATCH+x}"
case "$(command -v apply_patch)" in
  "$CODEX_HOME"/tmp/arg0-linux/*/apply_patch) ;;
  *) echo 'patch helper does not belong to this native invocation' >&2; exit 1 ;;
esac
apply_patch <<'PATCH'
*** Begin Patch
*** Add File: result with spaces.txt
+literal $value $(do-not-run) 'quoted'
*** End Patch
PATCH
test "$(cat 'result with spaces.txt')" = "literal \$value \$(do-not-run) 'quoted'"
"$CODEX_QUALIFIED_SELF_EXE" /bin/sh -c 'test -z "${CODEX_INTERNAL_ARG0_DISPATCH+x}"; printf "reexec-ok\n"'
printf 'patch-and-reexec-ok\n'
'''

PRELOAD = r'''#include <fcntl.h>
#include <stdlib.h>
#include <unistd.h>
__attribute__((constructor)) static void loaded(void) {
    const char *path = getenv("PC_LAUNCH_CONTROL_MARKER");
    if (path) {
        int fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0600);
        if (fd >= 0) { (void)write(fd, "x", 1); close(fd); }
    }
}
'''


def run(argv, *, cwd=None, env=None):
    p = subprocess.run(list(map(str, argv)), cwd=cwd, env=env,
                       capture_output=True, text=True, timeout=60)
    return {'argv': list(map(str, argv)), 'returncode': p.returncode,
            'stdout': p.stdout, 'stderr': p.stderr}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--probe', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    probe = args.probe.resolve(strict=True)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    cc = shutil.which('cc')
    assert cc, 'qualification requires a C compiler for an actual preload dependency'
    elf = run(['readelf', '-l', probe])
    assert elf['returncode'] == 0, elf
    loader = re.search(r'Requesting program interpreter: (.+?)\]', elf['stdout'])[1]
    result = {'probe': str(probe), 'probe_sha256': hashlib.sha256(probe.read_bytes()).hexdigest(),
              'loader': loader, 'cases': [], 'model_calls': 0, 'benchmark_processes': 0}
    with tempfile.TemporaryDirectory(prefix="arg0 launch ' ", dir=output.parent) as temporary:
        root = Path(temporary)
        library_dir = root / 'runtime libs'
        library_dir.mkdir()
        source = library_dir / 'marker.c'
        source.write_text(PRELOAD)
        compile_result = run([cc, '-shared', '-fPIC', source, '-o', library_dir / 'liblaunch_marker.so'])
        assert compile_result['returncode'] == 0, compile_result
        alias = root / 'renamed probe'
        alias.symlink_to(probe)
        launches = [
            ('direct', [probe], False),
            ('direct_alias', [alias], False),
            ('loader_relative_dependencies', [loader, '--library-path', 'runtime libs',
                 '--preload', 'liblaunch_marker.so', os.path.relpath(probe, root)], True),
            ('loader_spoofed_argv0', [loader, '--library-path', 'runtime libs',
                 '--preload', 'liblaunch_marker.so', '--argv0', 'different-program-name', probe], True),
        ]
        for name, launch, preloaded in launches:
            work = root / name
            work.mkdir()
            marker = root / f'{name}.marker'
            codex_home = root / f'{name}.codex-home'
            codex_home.mkdir()
            environment = dict(os.environ)
            environment.update(CODEX_HOME=str(codex_home), PATH='/usr/bin:/bin',
                               PC_LAUNCH_CONTROL_MARKER=str(marker),
                               # A bogus mode cannot select a path or survive into
                               # arbitrary task children. Existing alias names are
                               # dispatch modes, not added execution authority.
                               CODEX_INTERNAL_ARG0_DISPATCH='/not/a/dispatch-mode')
            command = [*launch, '/bin/sh', '-c', 'cd "$1"\n' + PATCH_AND_REEXEC,
                       'qualification', str(work)]
            observed = run(command, cwd=root, env=environment)
            observed['name'] = name
            observed['artifact'] = (work / 'result with spaces.txt').read_text() if (work / 'result with spaces.txt').exists() else None
            observed['preload_invocations'] = len(marker.read_bytes()) if marker.exists() else 0
            result['cases'].append(observed)
            output.write_text(json.dumps(result, indent=2) + '\n')
            assert observed['returncode'] == 0, observed
            assert observed['artifact'] == "literal $value $(do-not-run) 'quoted'\n", observed
            assert 'reexec-ok' in observed['stdout'] and 'patch-and-reexec-ok' in observed['stdout'], observed
            if preloaded:
                # Initial native program, actual patch-helper process, and native
                # self re-exec each need the dependency after cwd changes.
                assert observed['preload_invocations'] == 3, observed
            else:
                assert observed['preload_invocations'] == 0, observed
    result['all_cases_passed'] = True
    result['temporary_effects_closed'] = True
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'cases_passed': len(result['cases']), 'temporary_effects_closed': True,
                      'model_calls': 0, 'output': str(output)}))


if __name__ == '__main__':
    main()
