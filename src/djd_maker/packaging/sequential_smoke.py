"""Opt-in release verifier: real GUI/media, fake remote, isolated runtime only."""
import json
import os
import time
from pathlib import Path


def run_sequential_smoke(root: Path, report: Path, *, save_fault: bool = False) -> int:
    if os.environ.get('DJD_PACKAGING_SMOKE') != '1':
        return 3
    from PySide6.QtCore import QTimer, QObject, Slot, Qt
    from djd_maker.core.commands import EventId
    from PySide6.QtWidgets import QApplication
    from djd_maker.gui.app import build_desktop
    from djd_maker.gui.dialogs import PresetDialog
    from djd_maker.core.models import Job, JobState
    from djd_maker.media.validator import VideoValidator
    from djd_maker.media.raw_store import RawSafeStore
    from djd_maker.adapters.ending import EndingEngineAdapter
    from djd_maker.adapters.hls import HlsAdapter
    from djd_maker.testing.fake_notebook import FakeNotebookAdapter
    from djd_maker.orchestration.pipeline import PipelineCoordinator, PipelinePaths
    from djd_maker.packaging.preflight import application_root
    from djd_maker.packaging.portable_e2e import _make_fixture

    root = root.resolve()
    if root.exists():
        raise FileExistsError('Smoke runtime must be fresh')
    root.mkdir(parents=True)
    tools = application_root() / 'runtime/ffmpeg'
    ffmpeg, ffprobe = tools/'ffmpeg.exe', tools/'ffprobe.exe'
    fixture = root/'fixture.mp4'
    _make_fixture(ffmpeg, fixture, 'blue', 2)
    app, window, service = build_desktop(root)
    app.setQuitOnLastWindowClosed(False)
    preset = window.preset_repository.create('release fixture', 'never sent to Google')
    window.preset_repository.select(preset.id)
    calls, runtime, errors, dialogs, table_updates = [], [], [], [], []
    fault = {'enabled': False, 'attempts': 0, 'recovered': False, 'isolation': False, 'overlay': False,
             'controlled_stop_after_wait': False}
    original_replace = os.replace
    def fault_replace(source, destination):
        if save_fault and fault['enabled'] and Path(destination) == root/'system/jobs/release0.json':
            fault['attempts'] += 1
            error = PermissionError('isolated package fixture: sharing violation')
            error.winerror = 32
            raise error
        return original_replace(source, destination)
    if save_fault:
        os.replace = fault_replace
    from djd_maker.core.runtime_operation import report_operation
    from types import SimpleNamespace
    sources = {}
    for index in range(2):
        source = root/'input'/f'lesson{index}.txt'
        source.parent.mkdir(exist_ok=True)
        source.write_text('release fixture', encoding='utf-8')
        job = Job(str(source), id=f'release{index}', state=JobState.WAITING,
                  notebook_id=f'fake{index}', notebook_url=f'https://notebook.google.com/notebook/fake{index}',
                  next_poll_at='2020-01-01T00:00:00+00:00')
        job.snapshot_preset(preset)
        service.jobs.save(job)
        sources[str(source)] = fixture
    class Remote(FakeNotebookAdapter):
        def submit(self, job):
            calls.append(['submit',job.id])
            report_operation('chat.send')
            report_operation('chat.sent')
            if save_fault and job.id == 'release0' and not fault['recovered']:
                fault['enabled'] = True
            report_operation('generation.accepted')
            return SimpleNamespace(notebook_id=job.notebook_id,notebook_url=job.notebook_url,remote_status='READY',reserved=False)
        def inspect_status(self, job):
            calls.append(['check', job.id])
            return 'READY'
        def download_artifact(self, job, destination):
            calls.append(['download', job.id])
            return super().download_artifact(job, destination)
        def delete_video_artifact(self, job, gate):
            calls.append(['delete', job.id])
            return super().delete_video_artifact(job, gate)
    remote = Remote(sources)
    validator = VideoValidator(ffprobe)
    pipeline = PipelineCoordinator(jobs=service.jobs, notebook=remote,
        raw_store=RawSafeStore(validator,root/'raw_files'),
        ending=EndingEngineAdapter(ffmpeg,ffprobe,validator=validator),
        hls=HlsAdapter(ffmpeg,ffprobe), validator=validator,
        paths=PipelinePaths(root/'raw_files',root/'output',root/'work',None),
        scheduler=service.scheduler,generation_preset=preset)
    service.pipeline_factory=lambda:pipeline
    update=service._runtime_update
    def observe(record):
        runtime.append(dict(record));update(record)
    service._runtime_update=observe
    class TableProbe(QObject):
        @Slot(object)
        def receive(self, event):
            if event.id != EventId.JOB_UPDATED:
                return
            job = event.payload
            table_updates.append({'job':job.id,'stage':job.presentation_stage,'state':job.state.value,
                                  'display':next(j.presentation_stage for j in window.jobs if j.id==job.id)})
    probe=TableProbe()
    # Observe the same queued event as MainWindow, after its connected consumer.
    # The legacy upstream signal fires before the queued GUI update is delivered.
    window.controller.presentation_event.connect(probe.receive, Qt.ConnectionType.QueuedConnection)
    window.controller.operation_failed.disconnect(window._operation_failed)
    window.controller.operation_failed.connect(lambda op,msg:errors.append([op,msg]))
    def close_modal():
        dialog=QApplication.activeModalWidget()
        if dialog:
            dialogs.append(type(dialog).__name__);dialog.reject()
    def begin():
        QTimer.singleShot(150,close_modal);window.show_settings()
        dialog=PresetDialog(window)
        QTimer.singleShot(150,close_modal);dialog.exec()
        window.set_jobs(service.jobs.list());window.job_table.selectRow(0)
        QTimer.singleShot(150,close_modal);window.show_selected_job()
        window.show_logs();dialogs.append('LogDialog' if window._log_dialog.isVisible() else 'MISSING')
        window._log_dialog.close()
        window.start_processing()
    start=time.monotonic()
    def tick():
        # V126 deliberately waits while a save-deferred job remains. Observe
        # that wait, then use an explicit Stop for the restart-recovery test.
        if (save_fault and not fault['recovered'] and not fault['controlled_stop_after_wait']
                and service._worker is not None and pipeline.wait_seconds
                and service.jobs.get('release1').state is JobState.COMPLETED
                and pipeline.deferred_ids == {'release0'}):
            fault['controlled_stop_after_wait'] = True
            service.request_stop()
        if time.monotonic()-start>120:
            errors.append(['timeout']);service.stop()
        if time.monotonic()-start>3 and service._worker is None:
            jobs=service.jobs.list()
            if save_fault and not fault['recovered']:
                fault['isolation'] = (service.jobs.get('release1').state is JobState.COMPLETED
                    and service.jobs.get('release0').state is not JobState.COMPLETED
                    and pipeline.deferred_ids == {'release0'} and not errors)
                fault['overlay'] = any('状態保存待ち' in window.job_table.item(row,5).text()
                    for row in range(window.job_table.rowCount()))
                if not fault['isolation']:
                    errors.append(['isolation failed'])
                window.grab().save(str(root/'save-deferred.png'))
                fault['enabled'] = False
                fault['recovered'] = True
                from djd_maker.core.deferred_state import DeferredStateStore
                service.jobs._deferred_state = DeferredStateStore(root/'system/recovery/deferred')
                pipeline.deferred = service.jobs._deferred_state
                window.start_processing()
                return
            expected=[['submit',f'release{i}'] for i in range(2)]+[[action,f'release{i}'] for i in range(2) for action in ('download','delete')]
            stages={r.get('stage') for r in runtime}
            required={'artifact.ready','download.start','raw.saved','ending.skip','hls.start','zip.start','job.next'}
            refreshed={entry['stage'] for entry in table_updates if entry['stage']==entry['display']}
            actions_ok = calls==expected
            if save_fault:
                actions_ok = (all(calls.count([action,f'release{i}']) == 1 for i in range(2)
                                  for action in ('submit','download','delete'))
                    and fault['isolation'] and fault['overlay'] and fault['attempts'] >= 7
                    and {'save.deferred','save.summary','save.recovered'} <= stages
                    and not pipeline.deferred_ids)
            passed=(actions_ok and required<=stages and {'chat.send','generation.accepted','hls.start','COMPLETED'}<=refreshed and not errors and len(dialogs)==4
                    and all(j.state is JobState.COMPLETED and j.txt_move_status=='MOVED'
                            and j.safety_gate.remote_deletion_allowed and j.ending_result.startswith('SKIPPED') for j in jobs))
            window.grab().save(str(root/'runtime.png'))
            result=dict(passed=passed,calls=calls,runtime=runtime,dialogs=dialogs,errors=errors,table_updates=table_updates,fault=fault,
                        runtime_labels={key:label.text() for key,(_,label) in window.runtime_labels.items()},
                        jobs=[dict(id=j.id,state=j.state.value,ending=j.ending_result,hls=j.hls_result,txt=j.txt_move_status) for j in jobs])
            report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            timer.stop();window.close();app.exit(0 if passed else 9)
    window.show()
    timer=QTimer();timer.timeout.connect(tick);timer.start(100)
    QTimer.singleShot(200,begin)
    try:
        return app.exec()
    finally:
        os.replace = original_replace
