# Retained upstream workflow recipes

`upstream-workflows/` preserves the upstream Codex GitHub Actions definitions and
helper files. They are ordinary source files in this repository and do not
run: this fork is built and tested with the commands in
[docs/PROCONTRACT_RELEASE.md](../docs/PROCONTRACT_RELEASE.md) and
[docs/PROCONTRACT_TESTING.md](../docs/PROCONTRACT_TESTING.md).

Before enabling CI for this fork, select the required workflows, adapt their
repository permissions and publication destinations, and restore the selected
files under `.github/workflows/`. Historical upstream paths in these retained
recipes are intentionally preserved.
