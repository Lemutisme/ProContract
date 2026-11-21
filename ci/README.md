# Retained upstream workflow recipes

`upstream-workflows/` preserves the upstream Codex GitHub Actions definitions and
helper files. They are ordinary source files in this repository.
The private repository currently uses the documented local release build and
qualification commands, without enabling upstream publication workflows.

Before enabling CI for this fork, select the required workflows, adapt their
repository permissions and publication destinations, and restore the selected
files under `.github/workflows/`. Historical upstream paths in these retained
recipes are intentionally preserved.
