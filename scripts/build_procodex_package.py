#!/usr/bin/env python3
"""Build a source-bound Linux ProCodex package using the existing Codex layout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parent.parent
NATIVE_BINARIES = ('codex', 'codex-code-mode-host', 'codex-exec-server',
                   'pro-contract-store', 'pro-contract-check')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def git(*args):
    return subprocess.check_output(['git', '-C', str(REPO), *args], text=True).strip()


def build(args):
    if git('status', '--porcelain'):
        raise RuntimeError('Build from a clean committed source checkout.')
    revision = git('rev-parse', 'HEAD')
    output = args.output.resolve()
    if output.is_relative_to(REPO):
        raise ValueError('Choose a build output directory outside the source checkout.')
    output.mkdir(parents=True, exist_ok=False)
    os.environ['CODEX_REPO_ROOT'] = str(REPO)
    from codex_package.archive import write_archive
    from codex_package.cli import parse_package_version
    from codex_package.layout import build_package_dir, prepare_package_dir, validate_package_dir
    from codex_package.ripgrep import resolve_rg_bin
    from codex_package.targets import PACKAGE_VARIANTS, TARGET_SPECS, PackageInputs
    from codex_package.v8 import resolve_codex_v8_cargo_env

    version = parse_package_version(args.version)
    target = args.target or subprocess.check_output(['rustc', '-vV'], cwd=REPO/'codex-rs', text=True).split('host: ', 1)[1].splitlines()[0]
    spec = TARGET_SPECS[target]
    if not spec.is_linux:
        raise ValueError('The ProCodex launcher currently supports Linux.')
    target_dir = (args.target_dir or output/'target').resolve()
    env = {**os.environ, **resolve_codex_v8_cargo_env(spec),
           'CARGO_TARGET_DIR': str(target_dir), 'CODEX_BUILD_COMMIT': revision}
    argv = ['cargo', 'build', '--locked', '--target', target, '--profile', args.profile, '--jobs', str(args.jobs)]
    if args.offline:
        argv.append('--offline')
    for name in (*NATIVE_BINARIES, 'bwrap'):
        argv += ['--bin', name]
    record = {'schema': 1, 'source_commit': revision, 'source_tree': git('rev-parse', 'HEAD^{tree}'),
              'rust_source_tree': git('rev-parse', 'HEAD:codex-rs'), 'target': target,
              'profile': args.profile, 'version': version, 'command': argv,
              'rustc': subprocess.check_output(['rustc', '-vV'], cwd=REPO/'codex-rs', text=True),
              'cargo_lock_sha256': digest(REPO/'codex-rs/Cargo.lock'),
              'started_at': time.time(), 'complete': False}
    receipt = output/'BUILD.json'
    receipt.write_text(json.dumps(record, indent=2)+'\n')
    with (output/'build.log').open('w') as log:
        result = subprocess.run(argv, cwd=REPO/'codex-rs', env=env, stdout=log, stderr=subprocess.STDOUT)
    record.update(build_exit=result.returncode, build_finished_at=time.time())
    receipt.write_text(json.dumps(record, indent=2)+'\n')
    result.check_returncode()
    if git('rev-parse', 'HEAD') != revision or git('status', '--porcelain'):
        raise RuntimeError('Source changed during the build; retain diagnostics without publishing a package.')
    built = target_dir/target/('debug' if args.profile == 'dev' else args.profile)
    package = output/'package'
    inputs = PackageInputs(entrypoint_bin=built/'codex', code_mode_host_bin=built/'codex-code-mode-host',
                           exec_server_bin=built/'codex-exec-server',
                           bwrap_bin=built/'bwrap', rg_bin=resolve_rg_bin(spec, args.rg_bin),
                           codex_command_runner_bin=None, codex_windows_sandbox_setup_bin=None)
    prepare_package_dir(package, force=False)
    build_package_dir(package, version, PACKAGE_VARIANTS['codex'], spec, inputs)
    shutil.copy2(built/'pro-contract-store', package/'bin/pro-contract-store')
    shutil.copy2(built/'pro-contract-check', package/'bin/pro-contract-check')
    for name in ['LICENSE', 'NOTICE']:
        if (REPO/name).exists():
            shutil.copy2(REPO/name, package/name)
    validate_package_dir(package, PACKAGE_VARIANTS['codex'], spec)
    files = {str(p.relative_to(package)): digest(p) for p in sorted(package.rglob('*')) if p.is_file()}
    provenance = {**record, 'complete': True, 'files': files,
                  'binaries': {name: files['bin/'+name] for name in NATIVE_BINARIES}}
    (package/'build-source.json').write_text(json.dumps(provenance, indent=2)+'\n')
    archive = output/f'procodex-{version}-{target}.tar.gz'
    write_archive(package, archive, force=False)
    checksum = digest(archive)
    (output/'SHA256SUMS').write_text(checksum+'  '+archive.name+'\n')
    record.update(complete=True, archive=archive.name, archive_sha256=checksum, finished_at=time.time(),
                  binaries=provenance['binaries'], package=str(package))
    receipt.write_text(json.dumps(record, indent=2)+'\n')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--version', required=True)
    parser.add_argument('--target')
    parser.add_argument('--target-dir', type=Path)
    parser.add_argument('--profile', choices=['release', 'dev-small', 'dev'], default='release')
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--offline', action='store_true', help='Use the local Cargo cache; helper artifacts must also be available.')
    parser.add_argument('--rg-bin', type=Path)
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    print(json.dumps(build(args), indent=2))


if __name__ == '__main__':
    main()
