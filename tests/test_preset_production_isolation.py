from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_no_test_or_acceptance_prompt_is_available_to_production_runtime():
    production = ROOT / "src" / "djd_maker"
    files = [
        path
        for path in production.rglob("*.py")
        if "testing" not in path.parts and "packaging" not in path.parts
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in files)
    forbidden = (
        "PRESET_A_STABILITY_READBACK",
        "PRESET_B_STABILITY_READBACK",
        "PRESET_C_STABILITY_READBACK",
        "preset body A",
        "preset body B",
        "acceptance prompt",
        "sample prompt",
        "debug prompt",
    )
    assert not [marker for marker in forbidden if marker in combined]
    assert "from djd_maker.testing" not in combined
    assert "import djd_maker.testing" not in combined


def test_production_adapter_has_no_legacy_generation_prompt_fallback():
    adapter = (
        ROOT / "src" / "djd_maker" / "adapters" / "notebook.py"
    ).read_text(encoding="utf-8")
    pipeline = (
        ROOT / "src" / "djd_maker" / "orchestration" / "pipeline.py"
    ).read_text(encoding="utf-8")
    assert "job.generation_prompt or" not in adapter
    assert "require_preset_body_snapshot" in adapter
    assert "self.dom.start_video_generation_from_chat(prompt)" in adapter
    assert "self.dom.start_video_generation(prompt)" not in adapter
    assert 'raise RuntimeError("PRESET_NOT_SELECTED")' in pipeline
