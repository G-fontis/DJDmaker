from hashlib import sha256
from types import SimpleNamespace
from zipfile import ZipFile, ZIP_STORED

from djd_maker.core.artifact_ownership import owned_zip


def fixture(tmp_path):
    source = tmp_path / 'lesson.txt'
    source.write_bytes(b'original source')
    output = tmp_path / 'lesson.zip'
    directory = tmp_path / '.lesson.hls-fixture'
    directory.mkdir()
    playlist = directory / 'playlist.m3u8'
    playlist.write_text('#EXTM3U\n#EXTINF:6,\nsegment00000.ts\n#EXT-X-ENDLIST\n')
    (directory / 'segment00000.ts').write_bytes(b'original media')
    with ZipFile(output, 'w', compression=ZIP_STORED) as archive:
        for path in directory.iterdir():
            archive.write(path, path.name)
    video = tmp_path / 'lesson.mp4'
    video.write_bytes(b'original video')
    job = SimpleNamespace(id='a', script_name='lesson', source_path=str(source),
        source_sha256=sha256(source.read_bytes()).hexdigest(), zip_path=str(output),
        raw_path=str(video), edited_path=str(video), duplicate_of_job_id=None,
        hls_checkpoint_directory=str(directory), hls_source_sha256=sha256(video.read_bytes()).hexdigest(),
        output_zip_sha256=sha256(output.read_bytes()).hexdigest())
    return job, output


def test_owned_zip_hash_accepts_correct_output(tmp_path):
    job, output = fixture(tmp_path)
    assert owned_zip(job, output, [job])


def test_owned_zip_missing_source_digest_blocks(tmp_path):
    job, output = fixture(tmp_path)
    job.source_sha256 = None
    assert not owned_zip(job, output, [job])


def test_owned_zip_foreign_replacement_blocks_even_valid_zip(tmp_path):
    job, output = fixture(tmp_path)
    with ZipFile(output, 'w') as archive:
        archive.writestr('playlist.m3u8', '#EXTM3U\n#EXTINF:6,\nsegment00000.ts\n#EXT-X-ENDLIST\n')
        archive.writestr('segment00000.ts', b'foreign media')
    assert not owned_zip(job, output, [job])


def test_owned_zip_other_owner_blocks(tmp_path):
    job, output = fixture(tmp_path)
    other = SimpleNamespace(**vars(job))
    other.id = 'b'
    assert not owned_zip(job, output, [job, other])


def test_owned_zip_source_changed_blocks(tmp_path):
    job, output = fixture(tmp_path)
    from pathlib import Path
    Path(job.source_path).write_bytes(b'changed source')
    assert not owned_zip(job, output, [job])


def test_owned_zip_hls_content_binding_accepts(tmp_path):
    job, output = fixture(tmp_path)
    job.output_zip_sha256 = None
    assert owned_zip(job, output, [job])


def test_owned_zip_hls_foreign_segment_blocks(tmp_path):
    job, output = fixture(tmp_path)
    job.output_zip_sha256 = None
    from pathlib import Path
    (Path(job.hls_checkpoint_directory) / 'segment00000.ts').write_bytes(b'foreign')
    assert not owned_zip(job, output, [job])


def test_owned_zip_hls_source_changed_blocks(tmp_path):
    job, output = fixture(tmp_path)
    job.output_zip_sha256 = None
    from pathlib import Path
    Path(job.edited_path).write_bytes(b'foreign')
    assert not owned_zip(job, output, [job])


def test_owned_zip_missing_source_with_durable_hash_accepts(tmp_path):
    job, output = fixture(tmp_path)
    from pathlib import Path
    Path(job.source_path).unlink()
    assert owned_zip(job, output, [job])


def test_owned_zip_no_content_binding_blocks(tmp_path):
    job, output = fixture(tmp_path)
    job.output_zip_sha256 = None
    job.hls_checkpoint_directory = None
    assert not owned_zip(job, output, [job])
