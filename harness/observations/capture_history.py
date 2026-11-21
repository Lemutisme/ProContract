"""Host-configured composition with an existing capture provider; no task policy."""
import argparse
import json
from pathlib import Path
import subprocess

from native_history import capture_history, validate_limits


def compose(provider, sessions, **limits):
    if provider.get("schema") != "procontract_capture_provider/v1":
        raise ValueError("unsupported base capture provider")
    root = Path(provider["projection_root"])
    if not root.is_absolute():
        raise ValueError("provider projection must be absolute")
    projection = provider["projection"]
    observations = projection["observations"]
    if any(row["path"] == "EXECUTION_HISTORY" or row["path"].startswith("EXECUTION_HISTORY/") for row in observations):
        raise ValueError("history observation namespace already declared")
    result = capture_history(Path(sessions), root, **limits)
    return {**provider, "projection": {**projection, "observations": [*observations, *result["observations"]]}}, result["diagnostics"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--max-bytes-per-log", type=int, required=True)
    parser.add_argument("--max-file-bytes", type=int, required=True)
    parser.add_argument("--max-total-bytes", type=int, required=True)
    parser.add_argument("provider", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.provider[1:] if args.provider[:1] == ["--"] else args.provider
    if not command:
        parser.error("a host-selected base provider command is required after --")
    validate_limits(max_bytes_per_log=args.max_bytes_per_log, max_file_bytes=args.max_file_bytes,
                    max_total_bytes=args.max_total_bytes)
    output = subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE)
    provider = json.loads(output.stdout)
    diagnostic_path = args.diagnostics.resolve()
    if diagnostic_path.is_relative_to(Path(provider["projection_root"]).resolve()) or diagnostic_path.is_relative_to(args.sessions.resolve()):
        raise ValueError("collection diagnostics must stay outside source sessions and captured projection")
    result, diagnostics = compose(provider, args.sessions, max_bytes_per_log=args.max_bytes_per_log,
                                  max_file_bytes=args.max_file_bytes, max_total_bytes=args.max_total_bytes)
    args.diagnostics.write_text(json.dumps(diagnostics, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
