"""Deliver an explicit frozen-evidence computation grant to native settings.

This does not choose when to review, grant task acceptance, enable image viewing,
or renew an allocation. The native inspection runtime owns reservations/lifetime.
"""
import copy
import json
import subprocess
import time
import uuid


def review_grant(value, *, image=None):
    if not isinstance(value, dict) or set(value) - {'max_calls', 'timeout_secs', 'max_output_bytes', 'image', 'resources'}:
        raise ValueError('invalid review computation profile')
    result = copy.deepcopy(value)
    for key, low, high in [('max_calls', 1, 64), ('timeout_secs', 1, 900), ('max_output_bytes', 256, 1048576)]:
        if type(result.get(key)) is not int or not low <= result[key] <= high:
            raise ValueError('invalid review computation ' + key)
    if image is not None:
        result['image'] = image
    if 'image' in result and (not isinstance(result['image'], str) or not result['image'].strip()):
        raise ValueError('review computation image must be nonempty')
    resources=result.get('resources')
    if resources is not None:
        fields={'memory_bytes':64,'cpu_millis':32,'pids':32,'tmpfs_bytes':64}
        if not isinstance(resources,dict) or set(resources)!=set(fields):
            raise ValueError('review resources need memory_bytes, cpu_millis, pids and tmpfs_bytes')
        if any(type(resources[key]) is not int or not 1<=resources[key]<2**bits for key,bits in fields.items()):
            raise ValueError('review resources must be positive representable integers')
    return result


def apply_review_profile(settings, profile, *, task_image=None):
    """Used by external-container adapters as well as local configuration.

    Image omission chooses the provisioner's actual task image, not a verifier
    image or the dummy public-check image. No image is built/pulled by this helper.
    """
    result = copy.deepcopy(settings)
    if profile is not None:
        effective_image = profile.get('image', task_image) if isinstance(profile, dict) else None
        if effective_image is None:
            raise ValueError('external review computation needs its declared analysis or task image')
        result['review_execution'] = review_grant(profile, image=effective_image)
    return result


def qualify_review_image(profile, *, task_image, docker='docker', timeout=15):
    """Probe the actual Python entrypoint before an adapter starts its task grant.

    This observes tool readiness, not application dependencies or result quality.
    It never pulls an image and only creates/removes its own probe container.
    """
    grant=apply_review_profile({},profile,task_image=task_image).get('review_execution')
    if grant is None:return {'passed':True,'grant':None,'scope':'No reviewer computation configured.'}
    start=time.time();name='pc-review-probe-'+uuid.uuid4().hex;attempted=False
    record={'passed':False,'grant':grant,'phase':'preparation','model_calls':0,'container':name}
    try:
        image=subprocess.check_output([docker,'image','inspect','--format','{{.Id}}',grant['image']],text=True,timeout=timeout).strip()
        grant['image']=image
        resources=grant.get('resources') or {'memory_bytes':512<<20,'cpu_millis':1000,'pids':64,'tmpfs_bytes':128<<20}
        argv=[docker,'run','--rm','--pull','never','--name',name,'--network','none','--read-only',
              '--cap-drop','ALL','--security-opt','no-new-privileges','--memory',str(resources['memory_bytes']),
              '--cpus',str(resources['cpu_millis']/1000),'--pids-limit',str(resources['pids']),
              '--tmpfs',f"/tmp:rw,nosuid,nodev,size={resources['tmpfs_bytes']}",
              '--entrypoint','python3',image,'-I','-c',
              'import json,sys;print(json.dumps({"python":list(sys.version_info[:3]),"executable":sys.executable}))']
        attempted=True
        result=subprocess.run(argv,text=True,capture_output=True,timeout=timeout)
        record.update(exit_code=result.returncode,stderr=result.stderr[-2048:])
        if result.returncode==0:
            record.update(passed=True,observed=json.loads(result.stdout))
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        record['error']=str(error)
    finally:
        # Name is generated solely for this probe; no foreign container is selected.
        record['effects_closed']=not attempted
        if attempted:
            try:
                cleanup=subprocess.run([docker,'rm','-f',name],text=True,capture_output=True,timeout=timeout)
                remaining=subprocess.run([docker,'inspect',name],text=True,capture_output=True,timeout=timeout)
                record['cleanup_exit_code']=cleanup.returncode
                record['effects_closed']=remaining.returncode!=0 and 'no such' in remaining.stderr.lower()
                if not record['effects_closed']:record['closure_diagnostic']=remaining.stderr[-2048:]
            except (OSError,subprocess.SubprocessError) as error:
                record['cleanup_error']=str(error)
        record['passed']=record['passed'] and record['effects_closed']
        record['elapsed_seconds']=time.time()-start
    record['scope']='Pinned local image Python entrypoint and probe closure only; task dependency coverage and aggregate resource admission are separate.'
    return record
