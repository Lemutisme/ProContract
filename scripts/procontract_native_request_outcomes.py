"""Read-only request/turn reconciliation; does not decide task validity or adoption."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3


def reconcile(requests):
    """Keep failures visible; a success in another turn cannot recover them.

    Even a later success in the same turn establishes only that response, not
    completion of the failed request's intended operation or the whole task.
    Input rows are read-only observations with monotonically increasing IDs.
    """
    ordered = sorted(requests, key=lambda row: row['id'])
    if len({row['id'] for row in ordered}) != len(ordered):
        raise ValueError('duplicate request identity')

    def successful(row):
        return (row['terminal'] == 'response.completed'
                and (row['http_status'] is None or row['http_status'] < 400)
                and row['failure'] is None)

    failures, incomplete, deadline_limited = [], [], []
    for row in ordered:
        failed = ((row['http_status'] is not None and row['http_status'] >= 400)
                  or row['terminal'] == 'response.failed'
                  or row['failure'] not in (None, 'task_deadline'))
        if row['failure'] == 'task_deadline':
            deadline_limited.append(row['id'])
        if row['terminal'] is None:
            incomplete.append(row['id'])
        if not failed:
            continue
        turn = row.get('turn_id')
        thread = row.get('thread_id')
        same_turn = [later['id'] for later in ordered
                     if isinstance(turn, str) and turn
                     and isinstance(thread, str) and thread
                     and later['id'] > row['id']
                     and later.get('turn_id') == turn
                     and later.get('thread_id') == thread
                     and successful(later)]
        failures.append({
            'request': row['id'], 'thread_id': row.get('thread_id'),
            'turn_id': turn, 'role': row.get('role', 'unknown'),
            'http_status': row['http_status'], 'terminal': row['terminal'],
            'failure': row['failure'],
            'later_successful_requests_same_turn': same_turn,
            'continuation_observed_in_same_turn': bool(same_turn),
        })
    return {
        'requests': len(ordered), 'failures': failures,
        'failed_requests_without_later_same_turn_success': sum(
            not row['continuation_observed_in_same_turn'] for row in failures),
        'incomplete_terminal_receipts': incomplete,
        'deadline_limited_requests': deadline_limited,
        'scope': 'Request/turn observations only. Main completion, another turn, '
                 'or shutdown does not erase a request failure. Same-turn '
                 'continuation does not certify the original effect.',
    }


def inspect_run(run):
    run = Path(run).resolve()
    result = json.loads((run / 'result.json').read_text())
    database = run / 'requests.sqlite'
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        rows = db.execute('SELECT id,http_status,terminal,failure FROM requests ORDER BY id').fetchall()
    requests, payloads = [], []
    for identity, status, terminal, failure in rows:
        path = run / 'requests' / f'{identity}.forwarded.json'
        body = json.loads(path.read_text()) if path.exists() else {}
        metadata = body.get('client_metadata') or {}
        props = body.get('text', {}).get('format', {}).get('schema', {}).get('properties', {})
        role = ('continuation' if 'choice' in props else 'reviewer' if 'assessments' in props
                else 'drafter' if 'differential_cases' in props else 'executor' if not props else 'unclassified')
        requests.append({'id': identity, 'http_status': status, 'terminal': terminal,
                         'failure': failure, 'thread_id': metadata.get('thread_id'),
                         'turn_id': metadata.get('turn_id'), 'role': role})
        if path.exists():
            payloads.append({'request': identity, 'path': str(path),
                             'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    completed = [turn for turn in result.get('completed_turns', [])
                 if turn.get('status') == 'completed' and not turn.get('error')]
    shutdown = json.loads((run / 'shutdown.json').read_text())
    return {
        'run': str(run), 'main_completed_turn_ids': [turn.get('id') for turn in completed],
        'main_turn_completed': bool(completed), 'requests': reconcile(requests),
        'native_status': result.get('status'), 'native_disposition': result.get('disposition'),
        'effects_closed': (shutdown.get('native', {}).get('status') == 'exited'
                           and shutdown.get('environment', {}).get('confirmed') is True),
        'payloads': payloads,
        'interpretation': 'Artifact correctness requires its own measurement. '
                          'These fields do not promote an artifact, settle a '
                          'responsibility, classify every failure cause or change costs.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(inspect_run(args.run), indent=2))


if __name__ == '__main__':
    main()
