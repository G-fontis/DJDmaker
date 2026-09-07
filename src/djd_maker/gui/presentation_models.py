"""Shared domain-to-presentation conversion for both GUI layouts."""
from dataclasses import dataclass
from djd_maker.core.models import JobState
from .viewmodels import state_display, job_stage_texts


@dataclass(frozen=True)
class JobViewModel:
    id: str
    name: str
    stages: tuple
    state: str
    progress: int
    timeline: tuple
    started: str

    @classmethod
    def from_job(cls, job):
        order = ('auth','preflight','notebook','credit','ending','hls','zip')
        index = {JobState.WAITING:0, JobState.UPLOADING:2, JobState.RAW_READY:4,
                 JobState.ENDING:4, JobState.HLS_ENCODING:5, JobState.ZIPPING:6,
                 JobState.RESERVED_WAITING_CREDIT_RESET:3}.get(job.state,2)
        timeline = tuple((key, 'done' if job.state is JobState.COMPLETED or n < index else
            ('error' if job.state is JobState.FAILED else 'active') if n == index else 'waiting') for n,key in enumerate(order))
        return cls(job.id,job.script_name,tuple(job_stage_texts(job)),state_display(job),
                   int(max(0,min(100,job.progress_percent))),timeline, job.generation_started_at or '－')


@dataclass(frozen=True)
class CreditLimitViewModel:
    active: bool
    text: str

    @classmethod
    def from_status(cls, state):
        if not state.get('active'):
            return cls(False,'AI使用量上限: 未検出')
        seconds=state.get('remaining_seconds')
        remaining = '時刻確認必要' if seconds is None else f'{seconds//3600:02}:{seconds//60%60:02}:{seconds%60:02}'
        return cls(True,f"AI LIMIT / CLOUD PAUSED — Notebook再開予定: {state.get('cloud_resume_at') or '時刻確認必要'} / 残り: {remaining} / ローカル処理を優先")
