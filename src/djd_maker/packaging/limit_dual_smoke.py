"""Opt-in isolated real GUI/media verifier for limit, pause and both layouts."""
import json
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path


def run_gui_mode_smoke(root, report, target):
    """Verify selection and restart via separate EXE processes, isolated data only."""
    if os.environ.get('DJD_PACKAGING_SMOKE') != '1':
        return 3
    if target not in {'PHASE1', 'PHASE2'}:
        return 4
    from djd_maker.gui.app import build_desktop
    from djd_maker.core.repositories import SettingsRepository
    root, report = Path(root).resolve(), Path(report).resolve()
    root.mkdir(parents=True, exist_ok=True)
    app, window, service = build_desktop(root)
    initial = window.gui_type_switch.currentText()
    window.show()
    deadline = time.monotonic() + 10
    while window.controller.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    window.gui_type_switch.setCurrentText(target)
    app.processEvents()
    actual = window.gui_type_switch.currentText()
    saved = SettingsRepository(root/'system/settings.json').load().gui_type
    window.grab().save(str(report.with_suffix('.png')))
    passed = actual == target and saved == target
    window.close()
    service.shutdown()
    report.write_text(json.dumps(dict(passed=passed, initial=initial, target=target,
        actual=actual, saved=saved), ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if passed else 9


def run_limit_dual_smoke(root, report, *, video=None, observed_limit=None):
    if os.environ.get('DJD_PACKAGING_SMOKE') != '1':
        return 3
    from PySide6.QtCore import QTimer
    from djd_maker.gui.app import build_desktop
    from djd_maker.core.models import Job, JobState
    from djd_maker.core.cloud_limit import CloudLimitGate, LimitObservation
    from djd_maker.core.commands import BUTTON_COMMANDS, REQUIRED_COMMANDS
    from djd_maker.core.repositories import SettingsRepository
    from djd_maker.media.validator import VideoValidator, resolve_executable
    from djd_maker.media.raw_store import RawSafeStore
    from djd_maker.adapters.ending import EndingEngineAdapter
    from djd_maker.adapters.hls import HlsAdapter
    from djd_maker.orchestration.pipeline import PipelineCoordinator, PipelinePaths
    from djd_maker.packaging.preflight import application_root
    from djd_maker.packaging.portable_e2e import _make_fixture
    from djd_maker.adapters.browser import BrowserManager
    from djd_maker.adapters.usage_limit import UsageLimitDetector

    root, report = Path(root).resolve(), Path(report).resolve()
    if root.exists():
        raise FileExistsError('Fresh smoke directory required')
    root.mkdir(parents=True)
    tools=application_root()/'runtime/ffmpeg'
    ffmpeg=resolve_executable('ffmpeg', tools/'ffmpeg.exe' if (tools/'ffmpeg.exe').is_file() else None)
    ffprobe=resolve_executable('ffprobe', tools/'ffprobe.exe' if (tools/'ffprobe.exe').is_file() else None)
    fixture=root/'fixture.mp4'
    if video:
        shutil.copyfile(video,fixture)
    else:
        _make_fixture(ffmpeg,fixture,'blue',2)
    app,window,service=build_desktop(root)
    app.setQuitOnLastWindowClosed(False)
    preset=window.preset_repository.create('isolated smoke','never sent')
    window.preset_repository.select(preset.id)
    source=root/'input/limit-local.txt'
    source.parent.mkdir(parents=True,exist_ok=True)
    source.write_text('isolated acceptance only',encoding='utf-8')
    local=Job(str(source),id='limit-local',state=JobState.DOWNLOADING)
    service.jobs.save(local)
    downloaded=root/'work'/local.id/'download'/f'{local.script_name}.mp4'
    downloaded.parent.mkdir(parents=True)
    shutil.copyfile(fixture,downloaded)
    for index in range(100):
        service.jobs.save(Job(str(root/'input'/f'waiting-{index}.txt'),id=f'waiting-{index}'))
    gate=CloudLimitGate(root/'system/cloud-limit.json')
    if observed_limit:
        observed=json.loads(Path(observed_limit).read_text(encoding='utf-8'))['limit']
        gate.block(LimitObservation(observed['message'],datetime.fromisoformat(observed['cloud_blocked_until'])))
    else:
        # Exercise the bundled detector against real Chrome DOM without Google,
        # accounts, or credit consumption; this is explicitly fixture evidence.
        clock = lambda: datetime.now().astimezone()
        release = (clock()+timedelta(hours=1)).strftime('%H:%M')
        browser = BrowserManager(root/'limit-fixture-profile', headless=True)
        try:
            page = browser.start()
            page.set_content(f'<div role="alert">AI の使用量上限に達しました。{release}以降にすべての機能が利用可能になります。</div>'
                f'<chat-panel><query-box><textarea disabled placeholder="チャットは {release} まで無効になっています"></textarea>'
                '<button disabled aria-label="送信">送信</button></query-box></chat-panel>')
            observation, enabled = UsageLimitDetector(page, clock).observe()
            if observation is None or enabled or not observation.chat_disabled:
                raise RuntimeError('Bundled limit DOM detector did not recognize the fixture')
            gate.block(observation)
        finally:
            browser.stop()
    if gate.due:
        raise RuntimeError('Live deadline already passed; do not fabricate a live block')
    collection_source=root/'input/limit-collection.txt'
    collection_source.write_text('isolated collection fixture',encoding='utf-8')
    collection=Job(str(collection_source),id='limit-collection',state=JobState.WAITING_VIDEO,
        notebook_id='fixture',notebook_url='https://notebook.google.com/notebook/fixture',
        next_poll_at='2020-01-01T00:00:00+00:00')
    service.jobs.save(collection)
    collection_calls=[]
    class NoCloud:
        def inspect_status(self,job):
            assert job.id==collection.id
            assert service.jobs.get(local.id).state is JobState.COMPLETED
            collection_calls.append(['check',job.id])
            return 'READY'
        def download_artifact(self,job,destination):
            assert job.id==collection.id
            destination.parent.mkdir(parents=True,exist_ok=True)
            assert not destination.exists()
            shutil.copyfile(fixture,destination)
            collection_calls.append(['download',job.id])
            return destination
        def __getattr__(self,name):
            if name in {'persist_identity','download_during_limit_verified'}:
                raise AttributeError(name)
            def forbidden(*args,**kwargs):
                calls.append(name)
                raise AssertionError('Cloud action during limit: '+name)
            return forbidden
    calls=[];errors=[];events=[];commands=[];wait_times=[]
    long_wait=os.environ.get('DJD_PACKAGING_TEN_MINUTE_WAIT')=='1'
    validator=VideoValidator(ffprobe)
    pipe=PipelineCoordinator(jobs=service.jobs,notebook=NoCloud(),
        raw_store=RawSafeStore(validator,root/'raw_files'),ending=EndingEngineAdapter(ffmpeg,ffprobe,validator=validator),
        hls=HlsAdapter(ffmpeg,ffprobe),validator=validator,
        paths=PipelinePaths(root/'raw_files',root/'output',root/'work',None),
        scheduler=service.scheduler,generation_preset=preset,cloud_limit=gate)
    service.pipeline_factory=lambda:pipe
    original=service._runtime_update
    def observe(record):
        if record.get('stage')=='scheduler.wait': wait_times.append(time.monotonic())
        events.append(record.get('stage'));original(record)
    service._runtime_update=observe
    window.controller.operation_failed.disconnect(window._operation_failed)
    window.controller.operation_failed.connect(lambda op,msg:errors.append([op,msg]))
    window.show()
    deadline=time.monotonic()+5
    while window.controller.busy and time.monotonic()<deadline:
        app.processEvents()
        time.sleep(.01)
    app.processEvents()
    modes=[]
    for mode in ('PHASE1','PHASE2','PHASE1','PHASE2'):
        if not window.switch_gui(mode): raise RuntimeError('GUI switch rejected')
        app.processEvents()
        assert window.bound_commands==REQUIRED_COMMANDS
        assert all(getattr(window,attr).property('command_id')==cmd.value for attr,cmd in BUTTON_COMMANDS.items())
        assert SettingsRepository(root/'system/settings.json').load().gui_type==mode
        from PySide6.QtWidgets import QPushButton
        assert not any(button.text()=='再開' for button in window.findChildren(QPushButton))
        assert window.preset_combo.currentData()==preset.id
        assert window.preset_repository.selected().id==preset.id
        window.grab().save(str(root/f'{mode}.png'))
        modes.append(mode)
    stage=0;paused_at=None;waited_at=None;started=time.monotonic()
    def tick():
        nonlocal stage,paused_at,waited_at
        if time.monotonic()-started>(900 if long_wait else 180):
            errors.append(['timeout']);service.request_stop();stage=3
        if stage==0 and service.jobs.get(local.id).state is JobState.COMPLETED and service.jobs.get(collection.id).state is JobState.COMPLETED and wait_times:
            waited_at=time.monotonic();window.pause_button.click();commands.append(window.last_command);stage=1
        elif stage==1 and service.cancellation.paused.is_set():
            if paused_at is None: paused_at=time.monotonic()
            if time.monotonic()-paused_at>.5:
                window.grab().save(str(root/'PAUSED.png'))
                window.start_button.click();commands.append(window.last_command);stage=2
        elif stage==2 and not service.status()['paused'] and time.monotonic()-waited_at>2 and (not long_wait or len(wait_times)>=2 and wait_times[-1]-wait_times[0]>=600):
            window.stop_button.click();commands.append(window.last_command);stage=3
        if stage==3 and service._worker is None and not window.controller.busy:
            item=service.jobs.get(local.id)
            passed=(not calls and not errors and item.state is JobState.COMPLETED and item.safety_gate.remote_deletion_allowed
                and item.ending_result.startswith('SKIPPED') and item.hls_result=='PASS' and item.txt_move_status=='MOVED'
                and all(j.state is JobState.WAITING for j in service.jobs.list() if j.id not in {local.id,collection.id})
                and service.jobs.get(collection.id).state is JobState.COMPLETED
                and collection_calls==[['check',collection.id],['download',collection.id]]
                and (not long_wait or len(wait_times)>=2 and wait_times[-1]-wait_times[0]>=600)
                and gate.blocked and not gate.due and stage==3 and paused_at is not None)
            result=dict(passed=passed,modes=modes,commands=commands,cloud_calls=calls,errors=errors,events=events,
                local_state=item.state.value,raw_gate=item.safety_gate.remote_deletion_allowed,ending=item.ending_result,
                hls=item.hls_result,zip=item.zip_path,txt_move=item.txt_move_status,limit=gate.status(),queued=100)
            result['limit_evidence'] = 'prior_live_observation' if observed_limit else 'real_chrome_local_dom_fixture'
            result.update(collection_calls=collection_calls,collection_state=service.jobs.get(collection.id).state.value,
                wait_rescan_seconds=wait_times[-1]-wait_times[0] if len(wait_times)>1 else None,
                ten_minute_wait_requested=long_wait,preset_shared=True,resume_buttons=0)
            report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            timer.stop();window.close();app.exit(0 if passed else 9)
    timer=QTimer();timer.timeout.connect(tick);timer.start(100)
    QTimer.singleShot(200,window.start_processing)
    return app.exec()
