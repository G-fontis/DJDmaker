import pytest

from djd_maker.core.settings import AppSettings


def test_required_defaults() -> None:
    settings = AppSettings()
    settings.validate()
    assert settings.first_notebook_check_seconds == 600
    assert settings.notebook_poll_seconds == 120
    assert settings.audio_tail_padding_seconds == 0.5
    assert settings.ffmpeg_concurrency == 1


@pytest.mark.parametrize("concurrency", [0, 3])
def test_unsafe_concurrency_is_rejected(concurrency) -> None:
    with pytest.raises(ValueError):
        AppSettings(ffmpeg_concurrency=concurrency).validate()


def test_old_four_second_trim_is_rejected() -> None:
    with pytest.raises(ValueError):
        AppSettings(audio_tail_padding_seconds=4.0).validate()

