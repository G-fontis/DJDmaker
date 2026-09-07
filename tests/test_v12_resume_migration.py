from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from zipfile import ZipFile

import pytest

from djd_maker.adapters.replies import ReplyKind, classify_reply, resolve_reset
from djd_maker.core.models import Job, JobState
from djd_maker.core.repositories import JobRepository
from djd_maker.core.job_migration import migrate_jobs
from djd_maker.core.completed_txt import reconcile_completed_txt

NOW = datetime(2026, 9, 7, 14, tzinfo=timezone(timedelta(hours=9)))


@pytest.mark.parametrize("text,minutes", [
    ("申し訳ありません。現在、全体的なクォータが少なくなっており、説明動画の生成機能がクォータ不足のためご利用いただけない状態です。", None),
    ("現在、動画生成機能の利用制限に達しているため、ペーパークラフト風の日本語説明動画の作成を開始することができません。約2時間25分後に回復する予定です。", 145),
    ("説明動画の生成機能がクォータ制限のため、一時的にご利用いただけない状態。約2時間23分後に解除される予定。", 143),
])
def test_quota_patterns(text, minutes):
    result = classify_reply(text, now=NOW)
    assert result.kind is ReplyKind.QUOTA_EXHAUSTED
    assert result.score >= 5
    assert result.reset_at == (NOW + timedelta(minutes=minutes) if minutes else None)


def test_resource_quota_reply_with_short_video_alternative():
    # Sanitized form of the previously unclassified live reply: an alternative
    # short video does not mean the requested explanation video was accepted.
    text = ("説明動画を生成するためのリソースが一時的に不足しているため、動画の生成を開始することができません。"
            "このリソース（クォータ）は約2時間後に回復する予定です。"
            "ショート動画形式であれば生成が可能です。")
    result = classify_reply(text, now=NOW)
    assert result.kind is ReplyKind.QUOTA_EXHAUSTED
    assert result.reset_at == NOW + timedelta(hours=2)
    assert result.score >= 5
    assert "2時間後" in result.matched_terms


@pytest.mark.parametrize("text", ["クォータ不足ではありません。動画を生成できます。", "生成できませんでした", "開始できませんでしたが再試行しています", "上限について解説動画の作成を開始しました。"])
def test_quota_false_positive(text):
    assert classify_reply(text, now=NOW).kind is not ReplyKind.QUOTA_EXHAUSTED


def test_generation_context_and_relative_priority():
    assert classify_reply("解説動画の作成を開始しました。", now=NOW).kind is ReplyKind.GENERATION_ACCEPTED
    assert classify_reply("ソースの解析を開始しました。", now=NOW).kind is ReplyKind.OTHER_FAILURE
    assert classify_reply("", now=NOW).kind is ReplyKind.NO_RESPONSE
    value, conflict = resolve_reset("16:30にクレジットが回復。約2時間25分後", NOW)
    assert value.hour == 16 and value.minute == 30 and not conflict
    explicit = NOW + timedelta(hours=4)
    assert resolve_reset("約2時間25分後", NOW, explicit) == (explicit, True)


def test_explicit_reset_timezone_is_not_reinterpreted_as_hhmm():
    explicit = datetime(2026, 9, 8, 1, tzinfo=timezone.utc)
    assert resolve_reset("リセット日時 2026-09-08T01:00:00+00:00", NOW) == (explicit, False)


def test_malformed_explicit_reset_uses_valid_relative_time():
    assert resolve_reset("リセット 2026-99-99T99:00+00:00 約25分後", NOW) == (NOW + timedelta(minutes=25), False)


def test_migration_175_jobs_preserves_identity_and_metadata(tmp_path):
    repo = JobRepository(tmp_path / "system" / "jobs")
    originals = {}
    for i in range(175):
        job = Job(f"TAX{i}.txt", id=f"job{i}", state=JobState.COMPLETED if i < 75 else JobState.FAILED,
                  notebook_id=f"nb{i}", notebook_url=f"https://notebook.google.com/notebook/nb{i}",
                  error_code=None if i < 75 else ["SOURCE_UPLOAD_FAILED", "PRESET_SEND_FAILED", "QUOTA_ERROR", "PRESET_RESPONSE_TIMEOUT", "FATAL_ERROR"][i % 5])
        repo.save(job)
        originals[job.id] = job.to_dict()
    result = migrate_jobs(tmp_path / "system")
    assert result["migrated"] == 175
    assert len(repo.list()) == 175
    assert sum(j.state is JobState.COMPLETED for j in repo.list()) == 75
    for job in repo.list():
        for key, value in originals[job.id].items():
            if key not in {"resume_schema_version", "failure_class"}:
                assert job.to_dict()[key] == value
        backup = tmp_path / "system/migration-backups/resume-v2/jobs" / f"{job.id}.json"
        assert json.loads(backup.read_text(encoding="utf-8"))["job"] == originals[job.id]
    assert migrate_jobs(tmp_path / "system")["migrated"] == 0


def completed(repo, tmp_path):
    source = tmp_path / "input" / "TAX2.txt"
    source.parent.mkdir()
    source.write_text("原稿", encoding="utf-8")
    archive = tmp_path / "TAX2.zip"
    with ZipFile(archive, "w") as stream:
        stream.writestr("playlist.m3u8", "#EXTM3U")
    job = Job(str(source), state=JobState.COMPLETED, zip_path=str(archive), source_sha256=sha256(source.read_bytes()).hexdigest())
    repo.save(job)
    return job, source, archive


def test_completed_txt_move_collision_and_retry(tmp_path):
    repo = JobRepository(tmp_path / "system/jobs")
    job, source, archive = completed(repo, tmp_path)
    raw = tmp_path / "raw"
    raw.mkdir()
    destination = raw / source.name
    destination.write_text("別内容", encoding="utf-8")
    assert reconcile_completed_txt(repo, raw) == 0
    assert source.exists() and repo.require(job.id).state is JobState.COMPLETED
    assert repo.require(job.id).txt_move_status == "TXT_MOVE_COLLISION"
    destination.write_bytes(source.read_bytes())
    assert reconcile_completed_txt(repo, raw) == 1
    assert not source.exists() and archive.exists()
    assert reconcile_completed_txt(repo, raw) == 0


def test_delete_completed_preserves_artifacts_and_rejects_mixed(tmp_path):
    repo = JobRepository(tmp_path / "system/jobs")
    job, source, archive = completed(repo, tmp_path)
    other = Job("pending.txt")
    repo.save(other)
    with pytest.raises(ValueError):
        repo.delete_completed([job.id, other.id])
    assert len(repo.list()) == 2
    repo.delete_completed([job.id])
    assert repo.get(job.id) is None
    assert JobRepository(repo.directory).get(job.id) is None
    assert archive.exists() and source.exists()


def test_txt_move_permission_failure_keeps_completed_and_can_retry(tmp_path, monkeypatch):
    from pathlib import Path
    repo = JobRepository(tmp_path / "system/jobs")
    job, source, archive = completed(repo, tmp_path)
    unlink = Path.unlink
    def fail_source(self, *args, **kwargs):
        if self == source:
            raise PermissionError("simulated source sharing violation")
        return unlink(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', fail_source)
    assert reconcile_completed_txt(repo, tmp_path / 'raw') == 0
    current = repo.require(job.id)
    assert current.state is JobState.COMPLETED
    assert current.txt_move_status == 'TXT_MOVE_PENDING'
    assert source.exists() and archive.exists()
    monkeypatch.setattr(Path, 'unlink', unlink)
    assert reconcile_completed_txt(repo, tmp_path / 'raw') == 1
    assert repo.require(job.id).state is JobState.COMPLETED
    assert archive.exists()


def test_delete_completed_from_175_preserves_all_media_and_remaining_jobs(tmp_path):
    import time
    repo = JobRepository(tmp_path / 'system/jobs')
    jobs = [Job(f'TAX{i}.txt', id=f'j{i}', state=JobState.COMPLETED if i < 75 else JobState.FAILED) for i in range(175)]
    for job in jobs:
        repo.save(job)
    sentinels = [tmp_path / name for name in ('raw.mp4','output.zip','playlist.m3u8','segment.ts','lesson.txt')]
    for path in sentinels:
        path.write_bytes(b'keep')
    started = time.perf_counter()
    repo.delete_completed([j.id for j in jobs[:75]])
    assert time.perf_counter() - started < 15
    assert {j.id for j in repo.list()} == {j.id for j in jobs[75:]}
    assert all(p.read_bytes() == b'keep' for p in sentinels)
    assert all(repo.get(j.id) is None for j in jobs[:75])
