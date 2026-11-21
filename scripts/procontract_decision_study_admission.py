"""One durable shared slot for a sequential decision study.

The provisioner supplies the existing capacity registry. A separate keeper holds
the slot after caller loss or uncertain remote closure. Only a retained closure
written after child reconciliation releases it. No grant starts while queued.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import procontract_operations as ops


def census(capacity_root, capacity):
    records = []
    for root in {Path(capacity_root), *map(Path, capacity['external_job_roots'])}:
        for path in (root / 'jobs').glob('*.json'):
            value = json.loads(path.read_bytes())
            current = ops.process_identity(value.get('pid', 0))
            if current and str(current['start_ticks']) == str(value.get('start_ticks')):
                if value.get('boot') not in (None, current['boot']):
                    continue
                records.append(value)
    return records


def acquire(config, directory):
    """Try once; false leaves the study queued, without a child allocation."""
    directory = Path(directory).resolve()
    root = Path(config['capacity_root']).resolve()
    with (root / 'shared-admission.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        lease = directory / 'lease.json'
        if lease.exists():
            value = json.loads(lease.read_bytes())
            if not ops.alive(value['keeper']):
                raise ValueError('admitted slot keeper disappeared; host reconciliation required')
            return True
        cap = json.loads((root / 'capacity.json').read_bytes())
        live = census(root, cap)
        memory = next(int(line.split()[1]) * 1024 for line in
                      Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
        free = shutil.disk_usage(directory).free
        need = (cap['disk_admission_reserve_bytes'] + config['disk_reservation_bytes']
                + sum(row.get('disk_reservation_bytes', 4 << 30) for row in live))
        reason = ('paused' if (root / 'PAUSE_ADMISSIONS').exists() or (directory / 'PAUSE_ADMISSIONS').exists()
                  else 'capacity' if len(live) >= cap['max_concurrent_attempts']
                  else 'memory' if memory < cap['admission_memory_reserve_bytes']
                  else 'disk' if free < need else None)
        observation = {'at': time.time(), 'allowed': reason is None, 'reason': reason,
                       'active_slots': len(live), 'memory_available_bytes': memory,
                       'disk_free_bytes': free, 'required_disk_bytes': need}
        ops.atomic(directory / 'admission.json', observation)
        if reason:
            return False
        with (directory / 'keeper.log').open('ab') as log:
            child = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()),
                                      'keep', str(directory)], stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=log, start_new_session=True)
        keeper = ops.process_identity(child.pid)
        if keeper is None:
            raise ValueError('slot keeper did not start')
        if str(directory) not in cap['external_job_roots']:
            cap['external_job_roots'].append(str(directory))
            ops.atomic(root / 'capacity.json', cap)
        job = directory / 'jobs' / 'decision-study.json'
        ops.atomic(job, {**keeper, 'task': directory.name, 'kind': 'decision-study',
                         'study_root': str(directory), 'slots': 1,
                         'disk_reservation_bytes': config['disk_reservation_bytes']})
        ops.atomic(lease, {'keeper': keeper, 'job': str(job), 'capacity_root': str(root),
                           'observation': observation})
        return True


def keep(directory):
    directory = Path(directory)
    # No model effect can begin until acquire has published lease + job.
    while not (directory / 'lease.json').exists():
        time.sleep(.1)
    lease = json.loads((directory / 'lease.json').read_bytes())
    if lease['keeper'] != ops.process_identity(os.getpid()):
        raise ValueError('keeper identity differs')
    while True:
        closure = directory / 'closure.json'
        if closure.exists() and json.loads(closure.read_bytes()).get('effects_resolved') is True:
            with (Path(lease['capacity_root']) / 'shared-admission.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                job = Path(lease['job'])
                value = json.loads(job.read_bytes())
                if value['pid'] != os.getpid() or value['start_ticks'] != lease['keeper']['start_ticks']:
                    raise ValueError('slot owner changed')
                ops.atomic(job, {**value, 'pid': 0, 'start_ticks': '0', 'slot_closed': True,
                                 'closed_at': time.time(), 'closure': str(closure)})
            return
        time.sleep(.25)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['keep'])
    parser.add_argument('directory')
    keep(parser.parse_args().directory)
