"""Opt-in real Qt diagnostic/log acceptance on an isolated application root."""
import os,json,time,traceback
from pathlib import Path


def run_v213_smoke(root,report):
    if os.environ.get('DJD_PACKAGING_SMOKE')!='1':return 3
    from datetime import datetime,timezone
    from djd_maker.core.diagnostics import EventJournal,normalize,occurrence
    from djd_maker.core.models import Job,JobState
    from djd_maker.core.repositories import JobRepository
    from djd_maker.gui.app import build_desktop
    from djd_maker.gui.dialogs import LogDialog
    from PySide6.QtWidgets import QMessageBox
    from .acceptance_assets import initialize_fixture_assets
    root,report=Path(root).resolve(),Path(report).resolve();root.mkdir(parents=True,exist_ok=False)
    result={'passed':False,'checks':[],'production_writes':0,'browser_launches':0}
    dialogs=[];old=QMessageBox.critical;QMessageBox.critical=lambda *a,**kw:dialogs.append('critical')
    window=service=None
    try:
        initialize_fixture_assets(root)
        for name in ('input','raw_files','output','work'):(root/name).mkdir(exist_ok=True)
        repo=JobRepository(root/'system/jobs')
        job=Job(str(root/'fixture.txt'),state=JobState.FAILED,error_code='NOTEBOOK_STATE_UNKNOWN',failure_class='FATAL_FAILED')
        journal=EventJournal(root/'logs')
        try:raise RuntimeError('fixture: 状態確認に失敗しました password=demo-secret https://test.invalid/file?signature=demo-signature')
        except RuntimeError as exc:job.error_details=journal.exception(exc,job,'Notebook状態確認')
        job.error_message=job.error_details['safe_error_message'];repo.save(job)
        before=repo.require(job.id).to_dict()
        moment=datetime(2026,11,19,5,44,18,tzinfo=timezone.utc)
        error=normalize({**occurrence(moment),'job_id':job.id,'stage':'job.result','outcome':'FAILED_WITH_REASON'},job=job)
        journal.append(error)
        journal.append(normalize({**occurrence(moment),'stage':'system.ready','message':'システム確認完了'},new=True))
        journal.append({'timestamp':'2026-11-18T05:00:00+00:00','message':'legacy timestamp'})
        journal.append({'message':'legacy without timestamp'})
        journal.append(normalize({'stage':'capability.refresh'},new=True))
        app,window,service=build_desktop(root);app.setQuitOnLastWindowClosed(False)
        deadline=time.monotonic()+12
        while window.controller.busy and time.monotonic()<deadline:app.processEvents();time.sleep(.02)
        for phase in ('PHASE1','PHASE2'):
            assert window.switch_gui(phase);window.show();app.processEvents();window.show_logs();app.processEvents()
            dialog=window._log_dialog
            rows=[[dialog.model.item(r,c).text() for c in range(6)] for r in range(dialog.model.rowCount())]
            assert all(row[0] for row in rows)
            target=next(r for r in rows if job.id in r[1] and r[4]=='ERROR')
            assert target[0]=='2026-11-19 14:44:18' and '状態確認' in target[5] and '診断ID' in target[5]
            assert any(r[1]=='システム' and r[0]=='2026-11-19 14:44:18' for r in rows)
            assert any(r[0]=='時刻不明（旧記録）' for r in rows)
            assert any(r[0]=='2026-11-18 14:00:00' for r in rows)
            dialog.filter_edits[3].setText('ERROR')
            assert dialog.proxy.rowCount()>=1
            dialog.table.setCurrentIndex(dialog.proxy.index(0,0));app.processEvents()
            text=dialog.details.toPlainText()
            assert '工程: Notebook状態確認' in text
            assert 'traceback' in text and 'RuntimeError' in text
            assert 'demo-secret' not in text and 'demo-signature' not in text
            dialog.grab().save(str(report.with_name(report.stem+'-'+phase+'.png')))
            dialog.filter_edits[3].clear()
            count=dialog.proxy.rowCount();dialog.details_checkbox.setChecked(True)
            assert dialog.proxy.rowCount()>count;dialog.details_checkbox.setChecked(False)
            dialog.hide()
            result['checks'].append(phase+'_job_system_jst_legacy_details_filters_redaction')
        reloaded=LogDialog(window);reloaded.append_records(EventJournal(root/'logs').recent())
        assert any(reloaded.model.item(r,0).text()=='2026-11-19 14:44:18' for r in range(reloaded.model.rowCount()))
        result['checks'].append('restart_persistence_original_occurrence_and_diagnostic_reference')
        start=time.monotonic()
        reloaded.append_records([normalize({'stage':'capability.refresh','message':str(i)},new=True) for i in range(20000)])
        elapsed=time.monotonic()-start
        assert reloaded.model.rowCount()==5000 and elapsed<8
        result.update(large_log_seconds=elapsed,display_limit=5000)
        result['checks'].append('20000_events_bounded_model_responsive')
        assert repo.require(job.id).resume_checkpoint==before['resume_checkpoint']
        assert repo.require(job.id).id==job.id
        assert not dialogs
        result.update(passed=True,critical_dialogs=0)
    except Exception:result['error']=traceback.format_exc()
    finally:
        if service is not None:service.shutdown()
        if window is not None:window.hide();window.deleteLater()
        QMessageBox.critical=old
        report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return 0 if result['passed'] else 9
