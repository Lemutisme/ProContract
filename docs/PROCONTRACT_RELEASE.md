# ProContract-Codex: build from source and install

[Documentation](README.md) · [Quickstart](PROCONTRACT_QUICKSTART.md) ·
[Code map](PROCONTRACT_CODE_MAP.md) · [Testing](PROCONTRACT_TESTING.md)

This guide builds the ProContract fork of Codex from a clone of this
repository and installs it as the command **`procodex`**. There is no prebuilt
package; every step below starts from source.

To see the kernel work before building everything, use the
[quickstart](PROCONTRACT_QUICKSTART.md): it needs only the store binary.

The package contains native ProContract (kernel, store, extension and
controller), durable progress and continuation, and the explicit `/rsi`
research interface. Tasks have no deadline unless you supply one. Automatic
research and adoption are off. The upstream Codex commit this fork is based on
is named in [PROCONTRACT_UPSTREAM_CHANGES.md](PROCONTRACT_UPSTREAM_CHANGES.md).

## Requirements

- Linux. The package builder accepts Linux targets only, and the launcher
  refuses to start on other systems.
- Docker, to run tasks. The launcher executes project tools in a local
  container.
- The Rust toolchain named in `codex-rs/rust-toolchain.toml`.
- Python 3.11 or later, and Git.
- A C toolchain, `pkg-config` and `libcap-dev` (Debian and Ubuntu package
  names).
- Network access during the build: Cargo downloads crates, and the builder
  downloads a pinned ripgrep and prebuilt V8 artifacts unless you supply them
  (see below).

## Build the package

The builder requires a clean, committed checkout and an output directory that
is outside the checkout and does not exist yet. From the repository root:

```sh
python3 scripts/build_procodex_package.py \
  --version 0.0.0-local \
  --output "$HOME/procodex-build" --jobs 4
```

`--version` is any semantic version you choose for this build. The builder
compiles `codex`, `codex-code-mode-host`, `codex-exec-server`,
`pro-contract-store`, `pro-contract-check` and `bwrap` from the checked-out
commit with `cargo build --locked`, and assembles them in the upstream Codex
package layout. It writes into the output directory:

| Path | Content |
| --- | --- |
| `package/` | The package to install. `package/build-source.json` records the source commit, build profile and a SHA-256 for every packaged file. |
| `procodex-<version>-<target>.tar.gz`, `SHA256SUMS` | The same package as an archive, with its checksum. |
| `BUILD.json`, `build.log` | The build record and Cargo's output. |
| `target/` | Cargo's target directory, unless `--target-dir` names another. |

Options:

- `--profile release` is the default. `--profile dev-small` or `--profile dev`
  gives a faster, unoptimized build and is recorded as such.
- `--target TRIPLE` selects a Linux target; the default is the host's.
- `--offline` passes `--offline` to Cargo. The helper artifacts must then be
  available locally too: give ripgrep with `--rg-bin PATH`, and set both
  `RUSTY_V8_ARCHIVE` and `RUSTY_V8_SRC_BINDING_PATH` in the environment.

If the checkout changes while the build runs, the builder stops without
producing a package.

## Install

Build an execution image that has the tools your projects need.
`docker/procodex` is a small starting point based on Ubuntu 24.04; building it
downloads the base image and packages. The installer takes the image by its
immutable ID.

```sh
docker build -t procodex-runtime docker/procodex
image_id="$(docker image inspect --format '{{.Id}}' procodex-runtime)"
python3 scripts/install_procodex.py \
  --package "$HOME/procodex-build/package" --image "$image_id"
~/.local/bin/procodex --version
```

Run the installer from the same clean checkout, at the commit the package was
built from. It checks that commit and every packaged file against
`build-source.json`, then copies the package, the launcher scripts, the task
strategy files and `strategies/` to
`~/.local/share/procodex/releases/<commit>`, points
`~/.local/share/procodex/current` at it and writes the launcher
`~/.local/bin/procodex`. `--prefix DIR` installs under another prefix instead
of `~/.local`.

The installer does not overwrite an existing release of the same commit or a
`procodex` executable it did not write. It makes no model calls and does not
change your Codex configuration or credentials. An installed `codex` stays as
it is.

## Run

```sh
cd /path/to/project
~/.local/bin/procodex --check 'python3 -m unittest discover'
```

The model provider is read from your existing Codex configuration
(`CODEX_HOME/config.toml`, normally `~/.codex/config.toml`). ProCodex keeps its
own task state under `~/.procodex/`. Project tools run offline in the execution
image; the launcher does not pull images.

On hosts where nested user namespaces are unavailable, pass `--container-only`
to use Docker as the execution sandbox. Pass `--task-seconds N` only when the
task has a hard time limit; resuming a task keeps the limits it was admitted
with. The [launcher guide](../scripts/PROCODEX.md) covers strategy bundles,
inputs, status, checks and review.

## Self-improvement (RSI)

`procodex rsi --help` shows the durable research controls. Starting `procodex`
with `--rsi-config PATH`, where the file is a research control profile written
by the owner, enables `/rsi` in the terminal interface without starting any
inference. The installed release includes the `strategies/research/`
instruction library; research methods stay separate from the kernel. See the
[research runtime](PROCONTRACT_RESEARCH_RUNTIME.md) and the
[operations interface](PROCONTRACT_OPERATIONS.md).

## Check the result

The [testing guide](PROCONTRACT_TESTING.md) lists the kernel and store tests,
the Python harness tests, and the native end-to-end fixtures that run against a
built package and a local image.
