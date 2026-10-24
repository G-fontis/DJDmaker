import pytest
from djd_maker.packaging.cleanup_plan import classify


@pytest.mark.parametrize('path,category',[
 ('dist/DJDmaker_Ver2.6/DJDmaker.exe','B'),('dist/DJDmaker_Ver2.6.zip','B'),
 ('dist/DJDmaker_Ver2.5.zip','C'),('dist/DJDmaker_Ver2.5/_internal/a.dll','C'),
 ('work/old-copy/_internal/a.dll','C'),('build/pyinstaller/out.pkg','C'),
 ('src/djd_maker/__pycache__/a.pyc','C'),('.pytest_cache/v/cache/nodeids','C'),
 ('system/jobs/job.json','A'),('system/settings.json','A'),('system/presets.json','A'),
 ('work/job/download/raw.mp4','A'),('work/test/completed_mp4/a.mp4','A'),
 ('work/case/profile/Default/Cookies','A'),('work/case/browser/chrome-profile/state','A'),
 ('work/test/output/result.zip','A'),('work/result.zip','A'),('input/script.txt','A'),
 ('work/test/_internal/user.zip','A'),
 ('docs/handoff.md','A'),('.venv/Lib/__pycache__/a.pyc','A'),('.git/objects/data','A'),
 ('work/pc-migration-fresh-clone/_internal/a.dll','A'),('unknown.dat','D'),
 ('work/mystery/data.json','D'),('../outside.exe','D'),
])
def test_cleanup_classification_protects_data(path,category):
    assert classify(path)[0]==category


def test_git_tracked_file_always_protected_even_if_named_generated():
    path='work/test/_internal/a.dll'
    assert classify(path,{path})[0]=='A'
    for protected in ('.git','.venv','node_modules'):
        assert classify(f'work/copy/_internal/{protected}/data')[0]=='A'
