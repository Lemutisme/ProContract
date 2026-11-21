"""Compose retained history into an already provisioned capture capability.

This only produces configuration. The caller owns version delivery, source
identity, allocation, native pause/closure and the experiment's comparison.
"""
from copy import deepcopy
from pathlib import Path
import sys

from native_history import validate_limits


def with_history(settings, *, sessions, diagnostics, max_bytes_per_log,
                 max_file_bytes, max_total_bytes, python=None):
    limits=dict(max_bytes_per_log=max_bytes_per_log,max_file_bytes=max_file_bytes,
                max_total_bytes=max_total_bytes)
    validate_limits(**limits)
    value=deepcopy(settings)
    provider=value['evaluation'].get('capture_provider')
    if not provider:
        raise ValueError('history composition requires an existing capture provider')
    if not Path(provider['program']).is_absolute():
        raise ValueError('base provider program must be absolute')
    sessions=Path(sessions);diagnostics=Path(diagnostics)
    if not sessions.is_absolute() or not diagnostics.is_absolute():
        raise ValueError('history and diagnostic paths must be provisioner-owned absolute paths')
    projection=Path(value['evaluation']['workspace_host_root']).resolve()
    if diagnostics.resolve().is_relative_to(projection) or diagnostics.resolve().is_relative_to(sessions.resolve()):
        raise ValueError('diagnostics must be outside the captured projection and source sessions')
    interpreter=Path(python or sys.executable).resolve()
    wrapper=Path(__file__).with_name('capture_history.py').resolve()
    args=[str(wrapper),'--sessions',str(sessions),'--diagnostics',str(diagnostics)]
    for flag,number in limits.items():args+=['--'+flag.replace('_','-'),str(number)]
    args+=['--',provider['program'],*provider.get('args',[])]
    # Keep the original provider timeout and exact source container binding.
    provider.update(program=str(interpreter),args=args)
    return value
