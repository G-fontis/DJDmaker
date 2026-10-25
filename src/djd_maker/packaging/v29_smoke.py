"""Opt-in real-process sequence contention and nonblocking Qt acceptance."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback


def sequence_owner(path,ready,release):
    if os.environ.get('DJD_PACKAGING_SMOKE')!='1':return 3
    from djd_maker.core.sequence_lock import sequence_lock
    with sequence_lock(Path(path)):
        Path(ready).write_text(str(os.getpid()))
        deadline=time.monotonic()+45
        while not Path(release).exists():
            if time.monotonic()>deadline:return 9
            time.sleep(.05) # Controlled fixture owner, never production lock scope.
    return 0


def sequence_allocate(root,identity,report):
    if os.environ.get('DJD_PACKAGING_SMOKE')!='1':return 3
    from djd_maker.core.repositories import JobRepository
    from djd_maker.core.models import Job
    repo=JobRepository(Path(root)/'system/jobs');job=Job(str(Path(root)/(identity+'.txt')),id=identity)
    repo.save(job);Path(report).write_text(json.dumps({'job_no':job.job_no,'pid':os.getpid()}))
    return 0


def _child(kind,*args):
    if getattr(sys,'frozen',False):return [sys.executable,'--packaging-sequence-'+kind,*map(str,args)]
    function='sequence_owner' if kind=='owner' else 'sequence_allocate'
    return [sys.executable,'-c',f'from djd_maker.packaging.v29_smoke import {function};import sys;raise SystemExit({function}(*sys.argv[1:]))',*map(str,args)]


def run_v29_smoke(root,report):
    if os.environ.get('DJD_PACKAGING_SMOKE')!='1':return 3
    from .acceptance_assets import initialize_fixture_assets
    from djd_maker.gui.app import build_desktop
    from djd_maker.core.repositories import JobRepository
    from djd_maker.core.models import Job
    from djd_maker.core.sequence_lock import sequence_lock
    from PySide6.QtWidgets import QMessageBox
    root,report=Path(root).resolve(),Path(report).resolve();root.mkdir(parents=True,exist_ok=False)
    result={'passed':False,'checks':[]};service=None;window=None;owner=None
    old_critical=QMessageBox.critical;dialogs=[]
    QMessageBox.critical=lambda *args,**kwargs:dialogs.append(str(args))
    flags=getattr(subprocess,'CREATE_NO_WINDOW',0)
    try:
        initialize_fixture_assets(root);repo=JobRepository(root/'system/jobs')
        (root/'input').mkdir(exist_ok=True)
        source=root/'input/existing.txt';source.write_text('isolated fixture',encoding='utf8');repo.save(Job(str(source)))
        app,window,service=build_desktop(root);app.setQuitOnLastWindowClosed(False);window.show()
        errors=[];statuses=[];window.controller.operation_failed.connect(lambda *v:errors.append(v))
        window.controller.status_changed.connect(statuses.append)
        def wait(condition,timeout=12):
            deadline=time.monotonic()+timeout
            while not condition() and time.monotonic()<deadline:app.processEvents();time.sleep(.02)
            assert condition(),'acceptance timeout'
        ready,release=root/'owner-ready',root/'owner-release'
        owner=subprocess.Popen(_child('owner',repo._number_path,ready,release),creationflags=flags)
        wait(ready.exists)
        before=repo._number_path.read_bytes()
        window.controller.reload();wait(lambda:not window.controller.busy)
        assert repo._number_path.read_bytes()==before and not errors and not dialogs
        result['checks'].append('read_only_reload_during_live_owner')
        (root/'input/pending.txt').write_text('new isolated input',encoding='utf8')
        window.controller.reload()
        wait(lambda:any(v.get('runtime',{}).get('stage')=='WAITING_FOR_JOBNO_LOCK' for v in statuses))
        assert not errors and not dialogs and service.status()['stop_reason'] is None
        app.processEvents()
        window.grab().save(str(report.with_name(report.stem+'-wait.png')))
        result['checks'].append('non_error_wait_no_blocking_dialog')
        release.write_text('release');assert owner.wait(timeout=10)==0
        wait(lambda:len(repo.list())==2 and not service._number_wait_pending.is_set())
        assert sorted(j.job_no for j in repo.list())==[1,2]
        assert not errors and not dialogs and service.status()['stop_reason'] is None
        result['checks'].append('owner_release_auto_resume_no_manual_restart')
        processes=[]
        for identity in ('concurrent-a','concurrent-b'):
            target=root/(identity+'.json')
            processes.append(subprocess.Popen(_child('allocate',root,identity,target),creationflags=flags))
        assert [p.wait(timeout=20) for p in processes]==[0,0]
        allocated=[json.loads((root/(name+'.json')).read_text())['job_no'] for name in ('concurrent-a','concurrent-b')]
        assert sorted(allocated)==[3,4]
        result['checks'].append('real_concurrent_process_allocation_unique')
        try:
            with sequence_lock(repo._number_path):raise RuntimeError('controlled exception')
        except RuntimeError:pass
        repo.save(Job(str(root/'after-exception.txt')))
        assert len({j.job_no for j in repo.list()})==5
        result['checks'].append('exception_releases_handle_next_allocation_success')
        recovery=JobRepository(root/'rebuild/jobs');recovery.save(Job('recovery.txt'))
        hashes={p:p.read_bytes() for p in recovery.directory.glob('*.json')}
        recovery._number_path.write_text('{')
        recovery.ensure_job_numbers();assert recovery._number_document().load()['next_job_no']==2
        assert all(p.read_bytes()==v for p,v in hashes.items())
        result['checks'].append('backup_rebuild_preserves_job_bytes')
        window.reload_jobs(local_only=True);window.resize(1600,1000);app.processEvents()
        window.grab().save(str(report.with_suffix('.png')))
        result.update(passed=True,allocated=allocated,jobs=len(repo.list()),dialogs=len(dialogs),errors=errors,owner_exit=owner.returncode)
    except Exception:result['error']=traceback.format_exc()
    finally:
        if owner and owner.poll() is None:
            (root/'owner-release').write_text('release');owner.wait(timeout=10)
        if window:window.close()
        if service:service.shutdown()
        QMessageBox.critical=old_critical
    report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return 0 if result['passed'] else 9
