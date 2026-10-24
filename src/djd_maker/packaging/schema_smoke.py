"""Opt-in schema restart acceptance with isolated real JSON repositories."""
import json
import os
import traceback
from datetime import datetime,timedelta,UTC
from pathlib import Path


def run_schema_smoke(root,report):
    if os.environ.get('DJD_PACKAGING_SMOKE')!='1':return 3
    from djd_maker import __version__
    from djd_maker.core.cloud_limit import CloudLimitGate,LimitObservation
    from djd_maker.core.repositories import SCHEMA_VERSION,UnsupportedSchemaError,JobRepository
    from djd_maker.core.models import Job
    from djd_maker.media.validator import VideoValidator
    from djd_maker.media.raw_store import RawSafeStore
    from djd_maker.orchestration.pipeline import PipelineCoordinator,PipelinePaths
    from .preflight import application_root
    root,report=Path(root).resolve(),Path(report).resolve();root.mkdir(parents=True,exist_ok=False)
    result={'passed':False,'version':__version__,'checks':[]}
    now=datetime(2026,10,24,tzinfo=UTC)
    try:
        for version in (None,0,SCHEMA_VERSION):
            path=root/f'cloud-{version}.json'
            payload={'kind':'cloud_limit','active':True,'custom_value':'retained'}
            if version is not None:payload['schema_version']=version
            path.write_text(json.dumps(payload),encoding='utf8');before=path.read_bytes()
            gate=CloudLimitGate(path,clock=lambda:now)
            assert path.read_bytes()==before
            gate.defer_recheck()
            saved=json.loads(path.read_text(encoding='utf8'))
            assert saved['schema_version']==SCHEMA_VERSION and saved['custom_value']=='retained'
            CloudLimitGate(path,clock=lambda:now).defer_recheck()
            result['checks'].append(f'normalize_restart_{version}')
        future=root/'future.json';future.write_text(json.dumps({'schema_version':999,'kind':'cloud_limit','active':True}),encoding='utf8')
        before=future.read_bytes()
        try:CloudLimitGate(future)
        except UnsupportedSchemaError:pass
        else:raise AssertionError('Future schema accepted')
        assert future.read_bytes()==before
        result['checks'].append('future_preserved')
        late=root/'late-future.json'
        loaded=CloudLimitGate(late)
        late.write_text(json.dumps({'schema_version':999,'kind':'cloud_limit','active':True}),encoding='utf8')
        before=late.read_bytes()
        try:loaded.release()
        except UnsupportedSchemaError:pass
        else:raise AssertionError('Late future schema overwritten')
        assert late.read_bytes()==before
        result['checks'].append('future_after_load_preserved_on_save')
        path=root/'pipeline-cloud.json'
        gate=CloudLimitGate(path,clock=lambda:now)
        gate.block(LimitObservation('isolated fixture',now-timedelta(minutes=10),True))
        restored=CloudLimitGate(path,clock=lambda:now)
        repo=JobRepository(root/'jobs')
        class Remote:
            def recheck_cloud_limit(self,url):return False
        validator=VideoValidator(application_root()/'runtime/ffmpeg/ffprobe.exe')
        pipe=PipelineCoordinator(jobs=repo,notebook=Remote(),raw_store=RawSafeStore(validator),
            ending=None,hls=None,validator=validator,paths=PipelinePaths(root/'raw',root/'output',root/'work',None),cloud_limit=restored)
        pipe._refresh_capabilities()
        assert json.loads(path.read_text(encoding='utf8'))['schema_version']==SCHEMA_VERSION
        result['checks'].append('pipeline_reload_recheck_save')
        job=Job(str(root/'source.txt'));repo.save(job)
        assert repo.require(job.id).id==job.id
        result['checks'].append('job_save_load')
        result['passed']=True
    except Exception:result['error']=traceback.format_exc()
    report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    return 0 if result['passed'] else 9
