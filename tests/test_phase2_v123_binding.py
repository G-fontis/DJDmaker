from dataclasses import replace
import subprocess

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from djd_maker.core.models import Job, JobState
from djd_maker.gui.dialogs import JobDetailDialog
from test_phase2_hud import hud_window
from test_gui import _drain_until


@pytest.mark.parametrize('key', ['job','notebook','phase','stage','decision','next_action','outcome','attempt','elapsed','count'])
def test_hud_v123_runtime_fields(hud_window,key):
    record = dict(job_id=hud_window.jobs[0].id,job='現在の台本',notebook='https://notebook.google.com/notebook/test',
                  phase='動画生成開始フェーズ',stage='chat.send',decision='Source Ready',next_action='返信待ち',
                  outcome='生成中',attempt='1/3',elapsed=5,processed=1,total=3)
    hud_window._apply_runtime_status({'runtime':record})
    label = hud_window.runtime_labels[key][1]
    assert label.isVisibleTo(hud_window)
    assert '－' not in label.text()


@pytest.mark.parametrize('stage', ['phase.a','phase.b'])
def test_hud_v123_phase_switch(hud_window,stage):
    phase='動画生成開始フェーズ' if stage=='phase.a' else '動画回収・変換フェーズ'
    hud_window._apply_runtime_status({'runtime':{'phase':phase,'stage':stage}})
    assert phase in hud_window.runtime_labels['phase'][1].text()
    assert ('remote完成' if stage=='phase.a' else 'RAW') in hud_window.phase_counts_label.text()


@pytest.mark.parametrize('stage',['chat.send','generation.accepted','reservation.complete','download.start','raw.saved','ending.skip','hls.start','zip.start'])
def test_hud_v123_incremental_colored_row(hud_window,stage):
    job = hud_window.jobs[0]
    snapshot=replace(job,state=JobState.UPLOADING,presentation_stage=stage,presentation_revision=1)
    hud_window.controller.publish_job(snapshot)
    _drain_until(lambda:next(j for j in hud_window.jobs if j.id==job.id).presentation_stage==stage)
    row=next(r for r in range(hud_window.job_table.rowCount()) if hud_window.job_table.item(r,0).data(Qt.ItemDataRole.UserRole)==job.id)
    assert hud_window.job_table.item(row,5).foreground().color().name() != '#000000'


def test_hud_v123_backend_identical_to_official_main():
    subprocess.run(['git','diff','--exit-code','f35ba70815822d8f253b66da6f71e6d35f889ab2','--',
                    'src/djd_maker/adapters','src/djd_maker/core','src/djd_maker/orchestration','src/djd_maker/media'],check=True,capture_output=True)


def test_hud_v123_preserves_backend_dispatch_counts(hud_window):
    counts = '生成開始済: 2/100 / 予約済: 3 / 残り: 95'
    hud_window._apply_runtime_status({'runtime': {'phase': '動画生成開始フェーズ', 'phase_counts': counts}})
    hud_window.update_job(replace(hud_window.jobs[0], presentation_revision=2))
    assert counts in hud_window.phase_counts_label.text()
    assert 'remote完成:' in hud_window.phase_counts_label.text()


def test_hud_v123_completed_steps_are_done(hud_window):
    hud_window._update_pipeline_steps(replace(hud_window.jobs[0], state=JobState.COMPLETED))
    assert all(step.state == 'done' for step in hud_window.pipeline_steps.values())


def test_hud_v123_detail_exposes_checkpoint_fields():
    QApplication.instance() or QApplication([])
    dialog=JobDetailDialog(Job('one.txt',presentation_phase='COLLECT_LOCAL',presentation_stage='raw.saved'))
    try:
        text=' '.join(x.text() for x in dialog.findChildren(QLabel))
        assert all(name in text for name in ['Phase','Stage','Source','Preset','Reply','TXT move','COLLECT_LOCAL','raw.saved'])
    finally:dialog.close()


@pytest.mark.parametrize('stage',['stop.requested','stop.complete','pause','resume'])
def test_hud_v123_control_status_updates(hud_window,stage):
    job=hud_window.jobs[0]
    hud_window._apply_runtime_status({'runtime':{'job_id':job.id,'job':job.script_name,'stage':stage}})
    row=next(r for r in range(hud_window.job_table.rowCount()) if hud_window.job_table.item(r,0).data(Qt.ItemDataRole.UserRole)==job.id)
    assert hud_window.job_table.item(row,5).text().startswith('－ ')
