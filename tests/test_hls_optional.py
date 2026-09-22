from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from djd_maker.core.models import Job, JobState
from djd_maker.core.settings import AppSettings
from djd_maker.core.repositories import SettingsRepository, JobRepository
from djd_maker.core.final_mp4 import SKIPPED, owned_mp4, output_target, queue_hls_from_mp4
from djd_maker.core.artifact_ownership import _digest
from djd_maker.orchestration.task_discovery import final_discovery
from djd_maker.gui.viewmodels import job_stage_texts, summarize_jobs
from djd_maker.testing.fake_notebook import FakeNotebookAdapter
from test_pipeline import coordinator, MemoryJobs, full_gate, Validator


def setup_run(tmp_path, *, enabled=False, ending=False, state=JobState.RAW_READY):
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = tmp_path / '日本語 台本.txt'
    source.write_text('test source', encoding='utf-8')
    raw = tmp_path / 'raw.mp4'
    raw.write_bytes(b'validated video fixture')
    job = Job(str(source), state=state, raw_path=str(raw), safety_gate=full_gate(),
              source_sha256=_digest(source), hls_zip_enabled=enabled)
    if state in {JobState.ENDING, JobState.HLS_ENCODING, JobState.ZIPPING}:
        job.edited_path = str(raw)
    jobs = MemoryJobs(job)
    remote = FakeNotebookAdapter({})
    pipeline = coordinator(tmp_path, jobs, remote)
    pipeline._run_hls_zip_enabled = enabled
    if not ending:
        pipeline.paths = replace(pipeline.paths, ending_video=None)
    if not enabled:
        pipeline.hls = Mock()
        pipeline.hls.convert_validate_and_zip.side_effect = AssertionError('OFF must not convert')
        pipeline.hls.resume_validated.side_effect = AssertionError('OFF must not resume ZIP')
    return pipeline, jobs, job


def test_default_and_legacy_settings():
    assert AppSettings().hls_zip_enabled is True
    assert Job.from_dict({'source_path':'a.txt','state':'WAITING'}).hls_zip_enabled is True


@pytest.mark.parametrize('value', [False, True])
def test_settings_restart(tmp_path, value):
    path = tmp_path / '日本語 空白' / 'settings.json'
    SettingsRepository(path).save(AppSettings(hls_zip_enabled=value))
    assert SettingsRepository(path).load().hls_zip_enabled is value


@pytest.mark.parametrize('value', ['false', 0, None])
def test_invalid_setting(value):
    with pytest.raises(ValueError):
        AppSettings(hls_zip_enabled=value).validate()


@pytest.mark.parametrize('ending', [False, True])
@pytest.mark.parametrize('state', [JobState.RAW_READY, JobState.ENDING, JobState.HLS_ENCODING, JobState.ZIPPING])
def test_off_complete_no_hls_zip_and_restart(tmp_path, ending, state):
    pipeline, jobs, job = setup_run(tmp_path, ending=ending, state=state)
    raw_hash = _digest(job.raw_path)
    pipeline.run_cycle()
    saved = jobs.get(job.id)
    assert saved.state is JobState.COMPLETED, saved.error_message
    assert saved.hls_result == saved.zip_result == SKIPPED
    assert saved.txt_move_status == 'MOVED'
    assert owned_mp4(saved, output_target(saved, pipeline.paths.output_directory), jobs)
    assert pipeline.hls.mock_calls == []
    assert _digest(job.raw_path) == raw_hash
    assert not list(pipeline.paths.output_directory.rglob('*.zip'))
    assert not list(pipeline.paths.output_directory.rglob('*.m3u8'))
    assert not list(pipeline.paths.output_directory.rglob('*.ts'))
    assert '設定によりスキップ' in job_stage_texts(saved)[2]
    assert summarize_jobs([saved]).mp4_complete == 1
    from datetime import UTC, datetime
    assert final_discovery(jobs.list(), datetime.now(UTC))['complete']
    pipeline.run_cycle()
    assert jobs.get(job.id).state is JobState.COMPLETED
    assert pipeline.hls.mock_calls == []


def test_on_unchanged(tmp_path):
    pipe, jobs, job = setup_run(tmp_path, enabled=True)
    pipe.run_cycle()
    saved = jobs.get(job.id)
    assert saved.state is JobState.COMPLETED
    assert Path(saved.zip_path).is_file()
    assert saved.final_mp4_path is None


def test_existing_zip_hls_preserved_off(tmp_path):
    pipe, jobs, job = setup_run(tmp_path)
    root = pipe.paths.output_directory
    root.mkdir()
    zip_file = root / (job.script_name + '.zip')
    zip_file.write_bytes(b'legacy zip must remain')
    playlist = root / 'legacy.m3u8'
    playlist.write_bytes(b'legacy playlist')
    pipe.run_cycle()
    assert jobs.get(job.id).state is JobState.COMPLETED
    assert zip_file.read_bytes() == b'legacy zip must remain'
    assert playlist.read_bytes() == b'legacy playlist'


def test_never_overwrite_unowned_mp4(tmp_path):
    pipe, jobs, job = setup_run(tmp_path)
    target = output_target(job, pipe.paths.output_directory)
    target.parent.mkdir(parents=True)
    target.write_bytes(b'unowned')
    pipe.run_cycle()
    assert jobs.get(job.id).state is JobState.FAILED
    assert target.read_bytes() == b'unowned'
    assert pipe.hls.mock_calls == []


def test_same_raw_output_directory(tmp_path):
    pipe, jobs, job = setup_run(tmp_path)
    pipe.paths = replace(pipe.paths, output_directory=tmp_path, raw_directory=tmp_path)
    pipe.run_cycle()
    saved = jobs.get(job.id)
    assert saved.state is JobState.COMPLETED
    assert Path(saved.final_mp4_path) != Path(saved.raw_path)


def test_raw_gate_required(tmp_path):
    pipe, jobs, job = setup_run(tmp_path)
    job.safety_gate.ffprobe_ok = False
    jobs.save(job)
    pipe.run_cycle()
    assert jobs.get(job.id).state is JobState.FAILED
    assert pipe.hls.mock_calls == []


def test_explicit_on_reuses_completed_mp4(tmp_path):
    pipe, jobs, job = setup_run(tmp_path)
    pipe.run_cycle()
    saved = jobs.get(job.id)
    mp4_hash = _digest(saved.final_mp4_path)
    queue_hls_from_mp4(saved, pipe.paths.output_directory, Validator(), jobs)
    from test_pipeline import Hls
    pipe.hls = Hls()
    pipe._run_hls_zip_enabled = True
    pipe.begin_run()
    pipe.run_cycle()
    saved = jobs.get(job.id)
    assert saved.state is JobState.COMPLETED
    assert Path(saved.zip_path).is_file()
    assert _digest(saved.final_mp4_path) == mp4_hash
    assert not saved.notebook_id


def test_multiple_jobs_snapshot_once(tmp_path):
    pipe, jobs, first = setup_run(tmp_path)
    second_source = tmp_path / 'second.txt'
    second_source.write_text('second', encoding='utf-8')
    second_raw = tmp_path / 'second.mp4'
    second_raw.write_bytes(b'second raw')
    second = Job(str(second_source), state=JobState.RAW_READY, raw_path=str(second_raw),
                 safety_gate=full_gate(), source_sha256=_digest(second_source))
    jobs.save(second)
    pipe.begin_run()
    assert all(not j.hls_zip_enabled for j in jobs.list())
    # An unrelated Settings object can change without changing the run config.
    settings = AppSettings(hls_zip_enabled=False)
    settings.hls_zip_enabled = True
    pipe.run_cycle()
    assert all(j.state is JobState.COMPLETED and not j.hls_zip_enabled for j in jobs.list())
    assert pipe.hls.mock_calls == []


def test_completed_off_stays_completed_when_setting_on(tmp_path):
    pipe, jobs, job = setup_run(tmp_path)
    pipe.run_cycle()
    pipe._run_hls_zip_enabled = True
    pipe.begin_run()
    pipe.run_cycle()
    assert not jobs.get(job.id).hls_zip_enabled
    assert pipe.hls.mock_calls == []


@pytest.mark.parametrize('phase', ['PHASE1','PHASE2'])
@pytest.mark.parametrize('active', [False, True])
def test_shared_dialog_and_selection_preserved(phase, active):
    from test_gui import _app
    from djd_maker.gui.dialogs import SettingsDialog
    app = _app()
    dialog = SettingsDialog(AppSettings(gui_type=phase, hls_zip_enabled=False), processing=active)
    assert not dialog.hls_zip_checkbox.isChecked()
    assert dialog.hls_zip_checkbox.isEnabled() is not active
    assert dialog.value().hls_zip_enabled is False
    assert dialog.value().gui_type == phase
    dialog.close()


def test_restart_job_json_retains_off(tmp_path):
    repo = JobRepository(tmp_path / 'jobs')
    job = Job('source.txt', state=JobState.ENDING, hls_zip_enabled=False)
    repo.save(job)
    repo.recover_interrupted()
    assert not JobRepository(tmp_path / 'jobs').require(job.id).hls_zip_enabled


def test_corrupt_mp4_not_adopted(tmp_path):
    pipe, jobs, job = setup_run(tmp_path)
    pipe.run_cycle()
    saved = jobs.get(job.id)
    Path(saved.final_mp4_path).write_bytes(b'corrupted')
    assert not owned_mp4(saved, output_target(saved, pipe.paths.output_directory), jobs)
    with pytest.raises(ValueError):
        queue_hls_from_mp4(saved, pipe.paths.output_directory, Validator(), jobs)


def test_publish_failure_preserves_raw_and_no_partial_final(tmp_path, monkeypatch):
    pipe, jobs, job = setup_run(tmp_path)
    def fail(*args):
        raise PermissionError('sharing violation fixture')
    monkeypatch.setattr('djd_maker.core.final_mp4.os.link', fail)
    pipe.run_cycle()
    saved = jobs.get(job.id)
    assert saved.state is JobState.FAILED
    assert Path(saved.raw_path).read_bytes() == b'validated video fixture'
    assert not output_target(saved, pipe.paths.output_directory).exists()
    assert not list(pipe.paths.output_directory.rglob('.*.mp4'))


def test_crash_after_publish_before_completed_recovers_without_conversion(tmp_path):
    from djd_maker.core.final_mp4 import publish_mp4
    pipe, jobs, job = setup_run(tmp_path, state=JobState.ENDING)
    publish_mp4(job, pipe.paths.output_directory, Validator(), jobs, jobs.save)
    assert jobs.get(job.id).state is JobState.ENDING
    pipe.run_cycle()
    assert jobs.get(job.id).state is JobState.COMPLETED
    assert pipe.hls.mock_calls == []


def test_save_failure_before_publish_is_isolated(tmp_path):
    from djd_maker.core.repositories import JobStateSaveError
    pipe, jobs, job = setup_run(tmp_path)
    save = jobs.save
    def fail_intent(value):
        if value.final_mp4_path:
            raise JobStateSaveError('fixture intent failure')
        save(value)
    jobs.save = fail_intent
    pipe.run_cycle()
    assert job.id in pipe.deferred_ids
    assert not output_target(job, pipe.paths.output_directory).exists()
    assert pipe.hls.mock_calls == []


def test_missing_final_resumes_without_cloud(tmp_path):
    pipe, jobs, job = setup_run(tmp_path)
    pipe.run_cycle()
    saved = jobs.get(job.id)
    Path(saved.final_mp4_path).unlink()
    pipe.run_cycle()
    saved = jobs.get(job.id)
    assert saved.state is JobState.COMPLETED
    assert Path(saved.final_mp4_path).is_file()
    assert not saved.notebook_id


def test_settings_legacy_document_defaults_on(tmp_path):
    import json
    path = tmp_path / 'settings.json'
    repo = SettingsRepository(path)
    repo.save(AppSettings())
    value = json.loads(path.read_text(encoding='utf-8'))
    value['settings'].pop('hls_zip_enabled')
    path.write_text(json.dumps(value), encoding='utf-8')
    assert repo.load().hls_zip_enabled


def test_off_legacy_zip_is_not_counted_as_new_zip_completion(tmp_path):
    pipe, jobs, job = setup_run(tmp_path)
    job.zip_path = str(tmp_path / 'legacy.zip')
    jobs.save(job)
    pipe.run_cycle()
    saved = jobs.get(job.id)
    assert '設定によりスキップ' in job_stage_texts(saved)[2]
    assert summarize_jobs([saved]).zip_complete == 0
    assert summarize_jobs([saved]).mp4_complete == 1


def test_off_completed_duplicate_is_archived_only_after_mp4_verification(tmp_path):
    pipe, _, job = setup_run(tmp_path)
    jobs = JobRepository(tmp_path/'system/jobs')
    jobs.save(job)
    pipe.jobs = jobs
    pipe.run_cycle()
    saved = jobs.require(job.id)
    assert saved.state is JobState.COMPLETED
    duplicate = Job(saved.source_path, source_sha256=saved.source_sha256)
    jobs.save(duplicate)
    from djd_maker.core.completed_reconciliation import reconcile_completed_duplicates
    report = reconcile_completed_duplicates(jobs, pipe.paths.output_directory)
    assert report['removed'] == [duplicate.id]
    assert Path(saved.final_mp4_path).is_file()
    assert Path(saved.raw_path).is_file()


def test_job_snapshot_serialization_shape_and_mutable_isolation():
    from dataclasses import asdict
    job = Job('a.txt', hls_zip_enabled=False, attempt_by_stage={'retry':2}, safety_gate=full_gate())
    expected = asdict(job)
    expected['state'] = job.state.value
    result = job.to_dict()
    assert result == expected
    result['attempt_by_stage']['retry'] = 999
    result['safety_gate']['ffprobe_ok'] = False
    assert job.attempt_by_stage['retry'] == 2
    assert job.safety_gate.ffprobe_ok


def test_corrupted_completed_mp4_blocks_without_overwrite_or_generation(tmp_path):
    pipe, jobs, job = setup_run(tmp_path)
    pipe.run_cycle()
    saved = jobs.get(job.id)
    target = Path(saved.final_mp4_path)
    target.write_bytes(b'changed by another writer')
    pipe.run_cycle()
    blocked = jobs.get(job.id)
    assert blocked.state is JobState.FAILED
    assert blocked.failure_class == 'OUTPUT_BLOCKED'
    assert target.read_bytes() == b'changed by another writer'
    assert pipe.hls.mock_calls == []


def test_off_completion_flag_without_final_metadata_recovers_from_raw(tmp_path):
    pipe, jobs, job = setup_run(tmp_path)
    job.state = JobState.COMPLETED
    jobs.save(job)
    pipe.run_cycle()
    saved = jobs.get(job.id)
    assert saved.state is JobState.COMPLETED
    assert saved.final_mp4_path and Path(saved.final_mp4_path).is_file()
