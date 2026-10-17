"""Explicit Studio settings and lossless, allowlisted legacy preset mapping."""
from dataclasses import asdict, dataclass


class UnsupportedGenerationPreset(ValueError):
    pass


STYLE_LABELS = {'paper_craft': 'ペーパークラフト', 'anime': 'アニメ',
                'whiteboard': 'ホワイトボード', 'kawaii': 'カワイイ'}


@dataclass(frozen=True, slots=True)
class GenerationSettings:
    video_type: str = 'explainer'
    language: str = 'ja'
    visual_style: str = 'paper_craft'

    def __post_init__(self):
        if self.video_type != 'explainer' or self.language != 'ja' or self.visual_style not in STYLE_LABELS:
            raise UnsupportedGenerationPreset('GENERATION_SETTINGS_UNSUPPORTED: 設定値を確認してください')

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {'video_type', 'language', 'visual_style'}:
            raise UnsupportedGenerationPreset('GENERATION_SETTINGS_INVALID: 構造化設定が不正です')
        return cls(**value)


def map_legacy_preset(body: str) -> GenerationSettings:
    # Match the entire audited template, never discard unknown instructions.
    lines = [line.strip() for line in body.replace('\r\n', '\n').splitlines() if line.strip()]
    prefix = ['以下の内容で動画生成を開始してください。', '・ソースが読み込み終わっていること', '・説明動画', '・日本語']
    for style, label in STYLE_LABELS.items():
        if lines == prefix + ['・' + label]:
            return GenerationSettings(visual_style=style)
    raise UnsupportedGenerationPreset(
        'GENERATION_PRESET_MAPPING_REQUIRED: 追加指示を無視せず、Studio設定への対応を確認してください')
