"""Official config only, for opt-in isolated acceptance directories."""
import shutil
from pathlib import Path


def initialize_fixture_assets(root: Path) -> None:
    from .preflight import application_root
    destination = root / 'config'
    destination.mkdir(parents=True, exist_ok=True)
    for name in ('default-settings.json', 'download-quality-profile.json'):
        target = destination / name
        if not target.exists():
            shutil.copy2(application_root() / 'config' / name, target)
