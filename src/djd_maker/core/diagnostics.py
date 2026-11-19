"""Sanitized occurrence records and append-only diagnostic journals."""
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import re
import threading
import traceback
from uuid import uuid4,uuid5,NAMESPACE_URL

JST = timezone(timedelta(hours=9), 'Asia/Tokyo')
DEBUG_STAGES = {'job.start','job.result','job.next','capability.refresh','scheduler.task','download.transfer','download.lifecycle'}
SECRET_KEY = re.compile(r'(?i)^(?:authorization|cookie|set-cookie|password|passwd|secret|oauth.*token|access_token|refresh_token|api[ _-]?key|token)$')
_JOURNAL_LOCKS={}


def safe_text(value):
    text = str(value).replace('\x00','�')
    text = re.sub(r'https?://[^\s<>"\']+', lambda m: '[URL REDACTED]' if '?' in m[0] or '@' in m[0] else m[0], text)
    text = re.sub(r'(?im)((?:authorization|cookie|set-cookie)\s*:\s*)[^\r\n]+',r'\1[REDACTED]',text)
    text = re.sub(r'''(?ix)(["']?\b(?:authorization|cookie|set-cookie|access[_-]?token|refresh[_-]?token|oauth[\s_-]?token|token|api[\s_-]?key|password|passwd|secret)["']?\s*[:=]\s*)(?:\[REDACTED\]|"[^"]*"|'[^']*'|[^\s,;\}\]]+)''',r'\1[REDACTED]',text)
    return text


def redact(value):
    if isinstance(value,dict):return {k:'[REDACTED]' if SECRET_KEY.match(str(k)) else redact(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [redact(v) for v in value]
    return safe_text(value) if isinstance(value,str) else value


def occurrence(now=None):
    moment=now or datetime.now(timezone.utc)
    if moment.utcoffset() is None:raise ValueError('event timestamp must be timezone-aware')
    return dict(event_id=uuid4().hex,timestamp_utc=moment.astimezone(timezone.utc).isoformat(),
                timestamp_local=moment.astimezone(JST).isoformat())


def display_time(record):
    value=next((record[k] for k in ('timestamp_utc','timestamp_local','timestamp') if record.get(k) is not None and record.get(k)!=''),None)
    if isinstance(value,datetime):dt=value
    elif isinstance(value,(int,float)):
        try:dt=datetime.fromtimestamp(value,timezone.utc)
        except (ValueError,OverflowError,OSError):return '時刻不明（旧記録）'
    else:
        try:dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        except ValueError:
            # A legacy HH:mm string has no date/timezone; preserve, do not invent.
            return str(value) if value and re.fullmatch(r'\d{2}:\d{2}(?::\d{2})?',str(value)) else '時刻不明（旧記録）'
    if dt.utcoffset() is None:return str(value)+'（旧記録・TZ不明）'
    return dt.astimezone(JST).strftime('%Y-%m-%d %H:%M:%S')


def normalize(record, *, new=False, job=None, run_id=None):
    from .runtime_operation import operation_text
    raw=dict(record);runtime=raw.get('runtime') or {}
    value={**runtime,**raw}
    if new:
        candidate=next((value[k] for k in ('timestamp_utc','timestamp_local','timestamp') if value.get(k) is not None),None)
        try:
            moment=candidate if isinstance(candidate,datetime) else datetime.fromtimestamp(candidate,timezone.utc) if isinstance(candidate,(int,float)) else datetime.fromisoformat(str(candidate).replace('Z','+00:00'))
            if moment.utcoffset() is None:moment=None
        except (ValueError,TypeError,OverflowError,OSError):moment=None
        stamp=occurrence(moment)
        value.setdefault('event_id',stamp['event_id'])
        value.update(timestamp_utc=stamp['timestamp_utc'],timestamp_local=stamp['timestamp_local'])
    value.setdefault('event_id',uuid4().hex)
    value.setdefault('job_id',None)
    if not value.get('job_id'):value['job_id']=None
    if job is not None and value['job_id']==job.id:
        if not value.get('job_no'):value['job_no']=job.job_no
        if not value.get('job_title'):value['job_title']=job.script_name
        if not value.get('job'):value['job']=job.script_name
    value.setdefault('job_no',None);value.setdefault('job_title',value.get('job') or value.get('script_name'))
    if value.get('job_title') in {'－','-'}:value['job_title']=None
    if not value.get('engine'):value['engine']='DJDmaker'
    value.setdefault('stage','system')
    value.setdefault('event_code',value['stage'])
    value.setdefault('level','INFO')
    failed=value.get('outcome') in {'FAILED','FAILED_WITH_REASON'} or value.get('decision')=='FATAL_FAILED'
    if failed:value['level']='ERROR'
    elif (value['stage'] in DEBUG_STAGES or str(value['stage']).startswith('browser-')) and value['level']=='INFO':value['level']='DEBUG'
    if not value.get('message') or value['message']==value['stage']:
        value['message']=operation_text(value['stage'],value)
        if value['message']==value['stage']:value['message']='システムの状態を更新しました。('+value['stage']+')'
        decision=value.get('decision')
        if decision and decision!='－':value['message']+=' / '+operation_text(decision)
    if failed and job is not None:
        details=job_error(job,value['stage'])
        value.update(error_details=details,error_code=details['error_code'],message=error_summary(job,details))
    details=value.get('error_details') or {}
    for key in ('error_code','exception_type','traceback_reference'):
        if key in details:value.setdefault(key,details[key])
    value.setdefault('run_id',run_id)
    value.setdefault('correlation_id',value.get('operation_id') or value.get('run_id'))
    value['timestamp']=display_time(value)
    return redact(value)


@dataclass(frozen=True)
class ErrorDetails:
    error_code:str
    error_category:str
    stage:str
    exception_type:str|None
    safe_error_message:str
    retryable:bool
    attempt_count:int
    retry_exhausted:bool
    recovery_block_reason:str
    next_action:str
    diagnostic_id:str
    traceback_reference:str|None=None


def job_error(job,stage,exc=None):
    existing=getattr(job,'error_details',None) or {}
    if exc is None and existing.get('stage'):stage=existing['stage']
    name=type(exc).__name__ if exc is not None else ''
    inferred={'BrowserProfileLocked':'BROWSER_PROFILE_LOCKED','BrowserConnectionError':'BROWSER_CONNECTION_ERROR',
        'BrowserStartError':'BROWSER_CONNECTION_ERROR','JobStateSaveError':'JOB_STATE_SAVE_ERROR',
        'CloudLimitReached':'CREDIT_LIMIT','TimeoutError':'NOTEBOOK_STATE_UNKNOWN'}.get(name)
    code=job.error_code or existing.get('error_code') or inferred or 'UNKNOWN_INTERNAL_ERROR'
    category=code
    for markers,name in ((('PROFILE','LOCKED'),'BROWSER_PROFILE_LOCKED'),(('BROWSER','CONNECTION'),'BROWSER_CONNECTION_ERROR'),
        (('METADATA','NOTEBOOK','REMOTE_STATE'),'NOTEBOOK_STATE_UNKNOWN'),(('SOURCE','UPLOAD'),'SOURCE_UPLOAD_ERROR'),
        (('CREDIT','QUOTA'),'CREDIT_LIMIT'),(('QUALITY','VERIFY'),'DOWNLOAD_QUALITY_ERROR'),(('DOWNLOAD',),'DOWNLOAD_ERROR'),
        (('FFMPEG',),'FFMPEG_ERROR'),(('SAVE','SEQUENCE'),'JOB_STATE_SAVE_ERROR'),(('GENERATION','STUDIO'),'GENERATION_ERROR')):
        if any(k in code for k in markers):category=name;break
    attempts=job.attempt_by_stage.get(stage,job.attempt_by_stage.get('scheduler.recovery',0))
    exhausted=job.failure_class in {'TERMINAL_FAILED','RECOVERABLE_EXHAUSTED'} or 'EXHAUSTED' in code
    blocked=job.failure_class in {'FATAL_FAILED','OUTPUT_BLOCKED'} or exhausted
    cause=safe_text(str(exc) if exc is not None else job.error_message or '原因を特定できませんでした。')
    reason='再試行の上限に到達しました。' if exhausted else '保存された状態を確認する必要があります。' if blocked else '既存の再試行方針に従って再確認できます。'
    action='診断IDと詳細ログを確認してください。'
    if code=='NOTEBOOK_RESUME_METADATA_MISSING':
        if stage in {'FAILED','RECOVERY_PENDING','job.result','FATAL_FAILED','job.start','状態確認','STOPPED:UPLOADING'}:
            stage='Notebook識別情報確認'
        cause='再開対象のNotebook IDまたはURLが保存されていません。'
        reason='既存Notebookを一意に特定できないため、二重作成を防いで自動再開を保留しています。'
        action='既存NotebookのID/URLとこのJobの対応を確認してください。新規作成を繰り返さないでください。'
    elif code=='UNKNOWN_INTERNAL_ERROR':
        if not cause.startswith('原因を特定できませんでした'):cause='原因を特定できませんでした。元の例外: '+cause
    elif not blocked:action='接続・ファイルの状態が回復すると既存の再試行方針で再確認します。'
    return asdict(ErrorDetails(code,category,stage,type(exc).__name__ if exc is not None else existing.get('exception_type'),
        cause,not blocked,attempts,exhausted,reason,action,existing.get('diagnostic_id') or uuid5(NAMESPACE_URL,job.id+'|'+code+'|'+str(job.error_message)).hex,
        existing.get('traceback_reference')))


def error_summary(job,details):
    return (f'No. {job.job_no or "－"} {job.script_name} / 工程: {details["stage"]} / '
        f'{details["error_code"]}: {details["safe_error_message"]}\n'
        f'{details["recovery_block_reason"]} {details["next_action"]} 診断ID: {details["diagnostic_id"]}')


class EventJournal:
    def __init__(self,directory):
        self.directory=Path(directory)
        self._lock=_JOURNAL_LOCKS.setdefault(str(self.directory.resolve()),threading.RLock())
    def append(self,record):
        with self._lock:
            self.directory.mkdir(parents=True,exist_ok=True)
            with (self.directory/'events.jsonl').open('a',encoding='utf8') as stream:
                stream.write(json.dumps(redact(record),ensure_ascii=False,default=str)+'\n')
    def recent(self,limit=5000):
        path=self.directory/'events.jsonl'
        if not path.exists():return []
        if limit<=0:return []
        records=[]
        with self._lock,path.open('rb') as stream:
            stream.seek(0,2);position=stream.tell();blocks=[];lines=0
            while position and lines<=limit:
                size=min(position,65536);position-=size;stream.seek(position)
                block=stream.read(size);blocks.append(block);lines+=block.count(b'\n')
            tail=b''.join(reversed(blocks)).splitlines()
            for line in tail[-limit:]:
                try:records.append(json.loads(line))
                except ValueError:continue  # Preserve a torn legacy line on disk.
        return records
    def exception(self,exc,job,stage):
        original_job=job
        if job is None:
            from .models import Job
            job=Job('システム',id='')
        details=job_error(job,stage,exc)
        details['diagnostic_id']=uuid4().hex
        reference='diagnostics/'+details['diagnostic_id']+'.json'
        details['traceback_reference']=reference
        frames=traceback.extract_tb(exc.__traceback__)
        record=normalize({**occurrence(),'level':'DEBUG','stage':stage,'job_id':job.id or None,
            'event_code':'EXCEPTION_DIAGNOSTIC','error_details':details,
            'exception_type':type(exc).__name__,'exception_message':str(exc),
            'traceback':''.join(traceback.format_exception(exc)),
            'module':frames[-1].filename if frames else None,'function':frames[-1].name if frames else None,
            'call_path':[dict(module=f.filename,function=f.name,line=f.lineno) for f in frames]},job=original_job)
        with self._lock:
            path=self.directory/reference;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(redact(record),ensure_ascii=False,indent=2),encoding='utf8')
        self.append(record)
        return details
