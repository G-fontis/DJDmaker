"""Opt-in isolated dual-GUI / numbering / real media process acceptance."""
import json
import os
from pathlib import Path
import traceback


def run_v28_smoke(root, report):
    if os.environ.get('DJD_PACKAGING_SMOKE') != '1':
        return 3
    from dataclasses import replace, fields
    from PySide6.QtCore import Qt
    from .acceptance_assets import initialize_fixture_assets
    from .portable_e2e import _make_fixture
    from .preflight import application_root
    from djd_maker.gui.app import build_desktop
    from djd_maker.core.models import Job, JobState, DownloadSafetyGate
    from djd_maker.core.repositories import JobRepository
    from djd_maker.core.cancellation import run_process, CancellationToken, cancellation_scope
    from djd_maker.core.media_process import process_audit_snapshot
    from djd_maker.core.runtime_operation import operation_scope
    from djd_maker.adapters import ending as ending_module
    from djd_maker.adapters.ending import EndingEngineAdapter
    from djd_maker.adapters.hls import HlsAdapter
    from djd_maker.media.validator import VideoValidator, resolve_executable
    from djd_maker.media.raw_store import RawSafeStore
    from djd_maker.orchestration.pipeline import PipelineCoordinator, PipelinePaths
    from djd_maker.testing.fake_notebook import FakeNotebookAdapter
    from djd_maker.core.artifact_ownership import _digest
    root,report=Path(root).resolve(),Path(report).resolve()
    root.mkdir(parents=True,exist_ok=False)
    result={'passed':False,'checks':[],'runs':[]}
    service=None;window=None
    original=ending_module.run_process
    try:
        initialize_fixture_assets(root)
        numbers=JobRepository(root/'system/jobs')
        for i in range(4):
            job=Job(str(root/f'input/{i}.txt'),id=f'legacy{i}',job_no=10,
                    created_at=f'2026-01-0{i+1}T00:00:00+00:00',state=[JobState.WAITING,JobState.COMPLETED,JobState.FAILED,JobState.WAITING_VIDEO][i])
            numbers._document(job.id).save(numbers._envelope(job))
        app,window,service=build_desktop(root)
        app.setQuitOnLastWindowClosed(False)
        window.show();app.processEvents()
        assert [numbers.require(f'legacy{i}').job_no for i in range(4)]==[10,11,12,13]
        for mode in ('PHASE1','PHASE2'):
            if window.settings.gui_type!=mode:assert window.switch_gui(mode)
            window.resize(1600,1000)
            window.select_all_button.click();app.processEvents()
            assert len(window._checked_job_ids)==4
            assert all(window.job_table.item(r,6).checkState()==Qt.CheckState.Checked for r in range(4))
            window.grab().save(str(report.with_name(report.stem+'-'+mode+'.png')))
            window.clear_all_button.click();assert not window._checked_job_ids
            displayed=[window.job_table.item(r,0).text() for r in range(4)]
            assert len(set(displayed))==4
            result['checks'].append(mode+'_selection_clear_unique')
        assert numbers.ensure_job_numbers()=={}
        newcomer=Job(str(root/'new.txt'));numbers.save(newcomer)
        assert newcomer.job_no==14
        assert JobRepository(numbers.directory).require(newcomer.id).job_no==14
        result['checks'].append('migration_allocation_restart')
        tools=application_root()/'runtime/ffmpeg'
        ffmpeg=resolve_executable('ffmpeg',tools/'ffmpeg.exe' if (tools/'ffmpeg.exe').is_file() else None)
        ffprobe=resolve_executable('ffprobe',tools/'ffprobe.exe' if (tools/'ffprobe.exe').is_file() else None)
        fixture=root/'fixture.mp4';_make_fixture(ffmpeg,fixture,'blue',1)
        validator=VideoValidator(ffprobe,stability_interval_seconds=0)
        for exhausted in (False,True):
            folder=root/('exhausted' if exhausted else 'retry-success');folder.mkdir()
            repo=JobRepository(folder/'jobs');attempts=[];events=[];token=CancellationToken()
            for name in ('a','b'):
                source=folder/(name+'.txt');source.write_text('isolated fixture',encoding='utf8')
                raw=folder/'raw'/f'{name}.mp4';raw.parent.mkdir(exist_ok=True);raw.write_bytes(fixture.read_bytes())
                job=Job(str(source),state=JobState.RAW_READY,raw_path=str(raw),source_sha256=_digest(source),
                        ending_enabled=False,tail_cut_enabled=False,encode_enabled=True,hls_zip_enabled=False,
                        download_validation_status='DOWNLOAD_VALIDATED')
                # This synthetic input represents an already accepted download;
                # all real media validation still runs before publication.
                job.safety_gate = DownloadSafetyGate(**{f.name:True for f in fields(DownloadSafetyGate)})
                repo.save(job)
            def injected(command,**kwargs):
                is_a=any(str(x)==str(folder/'raw/a.mp4') for x in command)
                if is_a:
                    attempts.append(1)
                    if exhausted or len(attempts)==1:
                        # Real child process exits with the Windows NTSTATUS;
                        # controlled exit does not trigger an OS crash dialog.
                        return run_process([os.environ.get('COMSPEC','cmd.exe'),'/d','/c','exit 3221225794'],**kwargs)
                return original(command,**kwargs)
            ending_module.run_process=injected
            remote=FakeNotebookAdapter({})
            pipe=PipelineCoordinator(jobs=repo,notebook=remote,raw_store=RawSafeStore(validator),
                ending=EndingEngineAdapter(ffmpeg,ffprobe,validator=validator),hls=HlsAdapter(ffmpeg,ffprobe),validator=validator,
                paths=PipelinePaths(folder/'raw',folder/'output',folder/'work',None),ffmpeg_concurrency=2)
            pipe.runtime_callback=lambda record:events.append((record.get('stage'),dict(record)))
            with cancellation_scope(token),operation_scope(lambda stage,fields:events.append((stage,fields))):
                pipe._run_local_tasks(repo.list())
            a,b=sorted(repo.list(),key=lambda j:j.script_name)
            assert b.state==JobState.COMPLETED
            assert a.state==(JobState.FAILED if exhausted else JobState.COMPLETED),a.error_message
            assert not token.event.is_set() and not remote.submit_calls and not remote.download_calls
            assert len(attempts)==(4 if exhausted else 2)
            retries=[record for stage,record in events if stage=='ffmpeg.retry']
            assert len(retries)==(3 if exhausted else 1)
            window._append_runtime_message({'runtime':retries[0]})
            assert '自動再試行' in window.runtime_messages.toPlainText()
            if exhausted:assert a.error_code=='FFMPEG_RETRY_EXHAUSTED'
            result['runs'].append({'exhausted':exhausted,'attempts':len(attempts),'a':a.state.value,'b':b.state.value,
                'retry_events':sum(stage=='ffmpeg.retry' for stage,_ in events),'user_stop':False})
        result['process_audit']=process_audit_snapshot()
        assert result['process_audit']['active']==0
        result['passed']=True
    except Exception:
        result['error']=traceback.format_exc()
    finally:
        ending_module.run_process=original
        if window:window.close()
        if service:service.shutdown()
    report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return 0 if result['passed'] else 9
