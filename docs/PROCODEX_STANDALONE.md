# Installing the native client beside Codex

[Documentation](README.md) · [Quickstart](PROCONTRACT_QUICKSTART.md)

`scripts/install_procodex_standalone.py` installs a built package under a new
command name, `procodex`, with its own state directory. It is meant for trying
this fork on a machine where Codex is already in use. Nothing named `codex` is
created or changed, and the client never opens the existing Codex home.

## What the installer does

- Copies the package into a new prefix and writes one launcher named `procodex`.
  Both the prefix and the launcher must not exist yet.
- Creates a home directory inside the prefix. Its configuration starts with
  ProContract disabled, so the first runs behave as a plain Codex client.
- Copies no credentials, configuration, hooks or sessions.
- Records the resolved path, hash and file status of the existing `codex`
  before and after, and fails if they differ.
- Writes `installation.json` in the prefix: the build identity, the paths, and
  both readings of the existing `codex`.

## Running it

```sh
python3 scripts/install_procodex_standalone.py \
  --package PACKAGE_DIR \
  --prefix NEW_PREFIX \
  --entry SOME_BIN_DIR/procodex \
  --admission ADMISSION.json \
  --protected-home "$HOME/.codex" \
  --live-codex "$(command -v codex)"
```

`PACKAGE_DIR` is a package produced by `scripts/build_procodex_package.py`; its
`build-source.json` lists every file with its hash, and the installer checks
them. `ADMISSION.json` is the recorded decision to install this build. It names
the native source commit and the package manifest hash, lists the evidence files
it rests on with their hashes, and sets `"safe_to_install": true`. The installer
refuses when any of these does not match the package.

## Using the client

```sh
env -u CODEX_HOME procodex --build-info
env -u CODEX_HOME procodex --version
```

With `CODEX_HOME` unset the client uses its own home. A different private
directory may be passed in `CODEX_HOME`. The protected home, any directory
inside it and any directory above it are refused before the native client
starts. All other options are passed to the native client unchanged.

## Removing it

Only the prefix and the launcher belong to the installation:

```sh
rm SOME_BIN_DIR/procodex
rm -rf NEW_PREFIX
```

The prefix contains the client's home; keep any work stored there first.
