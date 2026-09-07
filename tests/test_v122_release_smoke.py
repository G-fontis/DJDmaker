from djd_maker.packaging.sequential_smoke import run_sequential_smoke


def test_release_smoke_requires_explicit_opt_in(monkeypatch, tmp_path):
    monkeypatch.delenv('DJD_PACKAGING_SMOKE', raising=False)
    root = tmp_path/'runtime'
    assert run_sequential_smoke(root, tmp_path/'report.json') == 3
    assert not root.exists()
