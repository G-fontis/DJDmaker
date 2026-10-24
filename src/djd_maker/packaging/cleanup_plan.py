"""Conservative, read-only cleanup classification. Never deletes files."""
from pathlib import PurePosixPath
import re

MEDIA={'.mp4','.m4v','.mkv','.mov','.webm','.mp3','.wav','.m3u8','.ts'}
PRIVATE={'system','config','browser','chrome-profile','profile','fixture-profile','browser-profile',
         'default','user data','user-data','credentials','secrets','input','raw','raw_files','user-raw',
         'completed_mp4','hls','ending','endings','output'}


def classify(path, tracked=(), current='2.6'):
    """Return category, reason, recoverable_from for one project-relative file."""
    p=PurePosixPath(path);parts=p.parts;lower=[x.casefold() for x in parts];name=p.name.casefold()
    if p.is_absolute() or '..' in parts or not parts:return 'D','unsafe path',None
    if path in tracked:return 'A','Git tracked',None
    if any(x in {'.git','.venv','node_modules'} for x in lower):return 'A','history/development environment',None
    if lower[0]=='dist' and len(parts)>1 and (parts[1]==f'DJDmaker_Ver{current}' or
            parts[1]==f'DJDmaker_Ver{current}.zip' or (f'Ver{current}' in parts[1] and 'sha' in name)):
        return 'B','current official release',None
    if any(x in PRIVATE for x in lower) or any(x in name for x in ('cookies','login data','credential','secret')):
        return 'A','profile/runtime/production/user data',None
    if any(any(word in x for word in ('migration','handoff','dropbox','newpc','pre_restore','backup')) for x in lower):
        return 'A','migration/handoff/backup evidence',None
    if p.suffix.lower() in MEDIA:return 'A','RAW/completed/Ending/HLS media',None
    if '__pycache__' in lower or '.pytest_cache' in lower or '.mypy_cache' in lower or name.endswith('.pyc'):
        return 'C','regenerable Python/test cache','installed Python and source'
    if lower[0] in {'src','tests','docs','packaging'} or name.startswith(('readme','development_rules','requirements','pyproject')):
        return 'A','source/tests/docs/build definitions',None
    old=re.fullmatch(r'DJDmaker_Ver(\d+)\.(\d+)(?:\.zip|(?:[._-].*sha.*)|)',p.parts[1],re.I) if lower[0]=='dist' and len(parts)>1 else None
    if old and tuple(map(int,old.groups())) < tuple(map(int,current.split('.'))):
        if p.suffix.lower()=='.zip' or 'sha' in name or '_internal' in lower or name=='djdmaker.exe' or (name in {'ffmpeg.exe','ffprobe.exe'}):
            return 'C','old release binary/distribution','Git source/history + reviewed build dependencies'
    if lower[0]=='build' and len(parts)>1 and lower[1]=='pyinstaller':
        if p.suffix.lower()=='.zip' and name!='base_library.zip':
            return 'A','unverified ZIP; retained even inside generated directories',None
        return 'C','PyInstaller generated intermediate','source + packaging/DJDMaker.spec'
    if p.suffix.lower()=='.zip' and name!='base_library.zip':
        return 'A','user/unknown ZIP; no extension-only deletion',None
    if lower[0]=='work' and len(parts)>1 and lower[1]!='v27':
        if '_internal' in lower or name in {'djdmaker.exe','ffmpeg.exe','ffprobe.exe'}:
            return 'C','extracted acceptance program/dependencies','current release or Git history rebuild'
        if p.suffix.lower() in {'.png','.jpg','.jpeg'} and re.match(r'v(?:1|2[0-5])(?:[-_0-9])',lower[1]):
            return 'C','old acceptance screenshot','existing text evidence + repeat acceptance'
    if p.suffix.lower()=='.zip':return 'A','user/unknown ZIP; no extension-only deletion',None
    return 'D','unknown or retained small evidence',None
