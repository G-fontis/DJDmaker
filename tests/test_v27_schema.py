import json
from datetime import datetime,timedelta,UTC
import pytest
from djd_maker.core.cloud_limit import CloudLimitGate,LimitObservation
from djd_maker.core.repositories import SCHEMA_VERSION,UnsupportedSchemaError,MalformedJsonError,JobRepository
from djd_maker.core.models import Job

NOW=datetime(2026,10,24,tzinfo=UTC)


@pytest.mark.parametrize('version',[None,0,SCHEMA_VERSION])
def test_schema_missing_legacy_current_normalized_without_rewriting_input(tmp_path,version):
    path=tmp_path/'cloud-limit.json'
    payload={'kind':'cloud_limit','active':True,'message':'saved','custom_field':'preserved'}
    if version is not None:payload['schema_version']=version
    path.write_text(json.dumps(payload),encoding='utf8');before=path.read_bytes()
    gate=CloudLimitGate(path,clock=lambda:NOW)
    assert path.read_bytes()==before
    assert 'schema_version' not in gate.state and 'kind' not in gate.state
    gate.defer_recheck()
    saved=json.loads(path.read_text(encoding='utf8'))
    assert saved['schema_version']==SCHEMA_VERSION and saved['custom_field']=='preserved'


def test_schema_version_existing_payload_not_passed_twice(tmp_path):
    path=tmp_path/'cloud-limit.json';gate=CloudLimitGate(path,clock=lambda:NOW)
    gate.block(LimitObservation('limit',NOW+timedelta(hours=1),True))
    restarted=CloudLimitGate(path,clock=lambda:NOW)
    restarted.state.update(schema_version=SCHEMA_VERSION,kind='cloud_limit')
    restarted.defer_recheck()
    assert json.loads(path.read_text(encoding='utf8'))['schema_version']==SCHEMA_VERSION


@pytest.mark.parametrize('version',[2,999,-1,'1',True])
def test_future_invalid_schema_never_downgraded_or_recovered_from_backup(tmp_path,version):
    path=tmp_path/'cloud-limit.json'
    path.write_text(json.dumps({'kind':'cloud_limit','schema_version':version,'active':True}),encoding='utf8')
    path.with_suffix('.json.bak').write_text(json.dumps({'schema_version':SCHEMA_VERSION,'kind':'cloud_limit','active':False}),encoding='utf8')
    before=path.read_bytes()
    with pytest.raises(UnsupportedSchemaError):CloudLimitGate(path)
    assert path.read_bytes()==before


def test_future_in_memory_payload_not_silently_overwritten(tmp_path):
    path=tmp_path/'cloud-limit.json';gate=CloudLimitGate(path)
    gate.release();before=path.read_bytes()
    gate.state['schema_version']=999
    with pytest.raises(UnsupportedSchemaError):gate.defer_recheck()
    assert path.read_bytes()==before


def test_future_schema_written_after_gate_load_is_preserved_on_save(tmp_path):
    path=tmp_path/'cloud-limit.json';gate=CloudLimitGate(path)
    path.write_text(json.dumps({'schema_version':999,'kind':'cloud_limit','active':True}),encoding='utf8')
    before=path.read_bytes()
    with pytest.raises(UnsupportedSchemaError):gate.release()
    assert path.read_bytes()==before


def test_repository_save_never_overwrites_future_document(tmp_path):
    from djd_maker.core.repositories import QueueRepository
    path=tmp_path/'queue.json';repo=QueueRepository(path)
    path.write_text(json.dumps({'schema_version':999,'kind':'queue','job_ids':['future']}),encoding='utf8')
    before=path.read_bytes()
    with pytest.raises(UnsupportedSchemaError):repo.save(['current'])
    assert path.read_bytes()==before


def test_wrong_document_kind_not_discarded(tmp_path):
    gate=CloudLimitGate(tmp_path/'cloud-limit.json')
    gate.state['kind']='job'
    with pytest.raises(MalformedJsonError):gate._save()


def test_reloaded_gate_repeated_saves_and_release_keep_state(tmp_path):
    path=tmp_path/'cloud-limit.json'
    gate=CloudLimitGate(path,clock=lambda:NOW)
    gate.block(LimitObservation('limit',NOW+timedelta(hours=1),True))
    for _ in range(3):
        gate=CloudLimitGate(path,clock=lambda:NOW);gate.defer_recheck()
        assert gate.blocked
    gate.release();assert not CloudLimitGate(path).blocked


def test_pipeline_restore_recheck_serialization_no_duplicate_keyword(tmp_path):
    from test_pipeline import coordinator,MemoryJobs
    from djd_maker.testing.fake_notebook import FakeNotebookAdapter
    path=tmp_path/'cloud-limit.json'
    gate=CloudLimitGate(path,clock=lambda:NOW)
    gate.block(LimitObservation('limit',NOW-timedelta(minutes=10),True))
    restored=CloudLimitGate(path,clock=lambda:NOW)
    class Remote(FakeNotebookAdapter):
        def recheck_cloud_limit(self,url):return False
    pipe=coordinator(tmp_path,MemoryJobs(),Remote({}));pipe.cloud_limit=restored
    pipe._refresh_capabilities()
    assert json.loads(path.read_text(encoding='utf8'))['schema_version']==SCHEMA_VERSION


def test_job_save_load_schema_unchanged(tmp_path):
    repo=JobRepository(tmp_path/'jobs');job=Job('source.txt');repo.save(job)
    assert repo.require(job.id).id==job.id
    payload=json.loads((tmp_path/'jobs'/f'{job.id}.json').read_text(encoding='utf8'))
    assert payload['schema_version']==SCHEMA_VERSION
