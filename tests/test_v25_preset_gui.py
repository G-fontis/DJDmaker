from djd_maker.core.generation_settings import GenerationSettings
from djd_maker.core.runtime_operation import operation_text
from djd_maker.gui.dialogs import PresetDialog
from djd_maker.testing.studio_fixture import BODY


def test_preset_editor_retains_body_and_exposes_structured_style(qtbot):
    dialog = PresetDialog(name='existing', prompt_text=BODY)
    qtbot.addWidget(dialog)
    assert dialog.values() == ('existing', BODY)
    assert dialog.structured_settings() == GenerationSettings().to_dict()
    dialog.style_combo.setCurrentIndex(dialog.style_combo.findData('anime'))
    assert dialog.structured_settings()['visual_style'] == 'anime'
    assert dialog.values() == ('existing', BODY)


def test_extra_instruction_editor_never_defaults_to_paper(qtbot):
    dialog = PresetDialog(name='additional', prompt_text=BODY+'\nextra instruction')
    qtbot.addWidget(dialog)
    assert dialog.structured_settings() is None
    assert dialog.values()[1].endswith('extra instruction')


def test_studio_runtime_labels_include_actual_style():
    assert operation_text('STYLE_SELECTED', {'visual_style':'ペーパー クラフト'}) == 'スタイル：ペーパー クラフト'
    assert operation_text('VIDEO_TYPE_SELECTED') == '動画形式：説明動画'
