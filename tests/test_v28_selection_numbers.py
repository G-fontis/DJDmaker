import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import pytest
from PySide6.QtCore import Qt
from djd_maker.core.commands import CommandId, BUTTON_COMMANDS, REQUIRED_COMMANDS, validate_payload, InterfaceError
from djd_maker.core.models import Job, JobState
from djd_maker.core.repositories import JobRepository
from test_gui import _window


@pytest.mark.parametrize('gui', ['PHASE1', 'PHASE2'])
@pytest.mark.parametrize('count', [0, 1, 6, 100])
def test_select_all_clear_all_visible_jobs(tmp_path, gui, count):
    jobs = [Job(f'{i}.txt', job_no=i+1, state=list(JobState)[i % len(JobState)]) for i in range(count)]
    window, *_ = _window(tmp_path, jobs)
    if gui == 'PHASE2':
        window.switch_gui(gui)
    before = [j.to_dict() for j in jobs]
    window.select_all_button.click()
    assert window._checked_job_ids == {j.id for j in jobs}
    assert all(window.job_table.item(r,6).checkState() == Qt.CheckState.Checked for r in range(count))
    window.clear_all_button.click()
    assert not window._checked_job_ids
    assert [j.to_dict() for j in jobs] == before
    assert window.last_command == CommandId.CLEAR_ALL
    window.close()


def test_select_all_only_visible_checkable_rows_and_session_switch(tmp_path):
    jobs = [Job(f'{i}.txt', job_no=i+1) for i in range(4)]
    w,*_ = _window(tmp_path,jobs)
    hidden=w.job_table.item(0,6).data(Qt.ItemDataRole.UserRole)
    w.job_table.hideRow(0)
    disabled=w.job_table.item(1,6)
    disabled_id=disabled.data(Qt.ItemDataRole.UserRole)
    disabled.setFlags(Qt.ItemFlag.ItemIsSelectable)
    w.select_all_button.click()
    assert w._checked_job_ids == {j.id for j in jobs}-{hidden,disabled_id}
    selected=set(w._checked_job_ids)
    service=w.selection_service
    w.switch_gui('PHASE2')
    assert w.selection_service is service and w._checked_job_ids==selected
    w.close()


def test_selection_commands_share_contract():
    for name, cmd in [('select_all_button',CommandId.SELECT_ALL),('clear_all_button',CommandId.CLEAR_ALL)]:
        assert BUTTON_COMMANDS[name]==cmd and cmd in REQUIRED_COMMANDS
        validate_payload(cmd,{})
        with pytest.raises(InterfaceError):validate_payload(cmd,{'state':'COMPLETED'})


def test_select_all_works_with_save_retry(tmp_path):
    jobs=[Job('a.txt',state=JobState.COMPLETED),Job('b.txt',state=JobState.DOWNLOAD_VERIFY_FAILED)]
    w,*_=_window(tmp_path,jobs);calls=[]
    w._manual_save_retry=lambda ids:calls.append(ids)
    before=[j.to_dict() for j in jobs]
    w.select_all_button.click();w.dispatch_command(CommandId.SAVE_RETRY)
    assert calls==[sorted(j.id for j in jobs)] and [j.to_dict() for j in jobs]==before
    w.close()


def legacy(repo, job):
    repo._document(job.id).save(repo._envelope(job))


def test_startup_duplicate_missing_migration_changes_number_only(tmp_path):
    repo=JobRepository(tmp_path/'jobs')
    jobs=[Job(f'{i}.txt',id=f'job{i}',job_no=10 if i<4 else None,
              created_at=f'2026-01-0{i+1}T00:00:00+00:00',notebook_id=f'nb{i}',notebook_url=f'https://fixture/{i}',
              raw_path=f'raw/{i}.mp4',edited_path=f'work/{i}.mp4',zip_path=f'out/{i}.zip',resume_checkpoint='HLS_ENCODING') for i in range(5)]
    for job in reversed(jobs):legacy(repo,job)
    before={p.name:json.loads(p.read_text(encoding='utf8')) for p in repo.directory.glob('*.json')}
    changed=repo.ensure_job_numbers()
    assert len(changed)==4 and repo.require('job0').job_no==10
    assert [repo.require(j.id).job_no for j in jobs]==[10,11,12,13,14]
    for p in repo.directory.glob('*.json'):
        after=json.loads(p.read_text(encoding='utf8'));after['job'].pop('job_no')
        original=before[p.name];original['job'].pop('job_no')
        assert after==original
    assert JobRepository(repo.directory).ensure_job_numbers()=={}
    assert repo._number_document().load()['next_job_no']==15


def test_concurrent_import_job_numbers_unique(tmp_path):
    def save(i):
        j=Job(f'{i}.txt');JobRepository(tmp_path/'jobs').save(j);return j.job_no
    with ThreadPoolExecutor(max_workers=8) as pool: numbers=list(pool.map(save,range(30)))
    assert len(set(numbers))==30
    assert sorted(numbers)==list(range(1,31))


def test_same_identity_concurrent_import_keeps_one_number(tmp_path):
    def save(i):
        j=Job('same.txt',id='same');JobRepository(tmp_path/'jobs').save(j);return j.job_no
    with ThreadPoolExecutor(max_workers=8) as pool:numbers=list(pool.map(save,range(20)))
    assert numbers==[1]*20


def test_new_import_cannot_supply_duplicate_or_change_existing_number(tmp_path):
    repo=JobRepository(tmp_path/'jobs');a=Job('a.txt',job_no=10);b=Job('b.txt',job_no=10)
    repo.save(a);repo.save(b)
    assert (a.job_no,b.job_no)==(1,2)
    a.job_no=2;repo.save(a)
    assert a.job_no==1


def test_deleted_number_not_reused_and_remaining_not_renumbered(tmp_path):
    repo=JobRepository(tmp_path/'jobs');a=Job('a.txt');b=Job('b.txt',state=JobState.COMPLETED)
    repo.save(a);repo.save(b);repo.delete_completed([b.id])
    c=Job('c.txt');JobRepository(repo.directory).save(c)
    assert (a.job_no,b.job_no,c.job_no)==(1,2,3)
    assert repo.require(a.id).job_no==1


def test_sorted_partial_update_uses_persistent_number(tmp_path):
    jobs=[Job(f'{i}.txt',job_no=10+i) for i in range(4)]
    w,*_=_window(tmp_path,jobs)
    for order in [Qt.SortOrder.DescendingOrder,Qt.SortOrder.AscendingOrder]*2:
        w.job_table.sortItems(0,order)
        for job in jobs:w.update_job(replace(job,presentation_revision=1))
        displayed={w.job_table.item(r,0).data(Qt.ItemDataRole.UserRole):w.job_table.item(r,0).text() for r in range(4)}
        assert displayed=={j.id:str(j.job_no) for j in jobs}
    w.close()
