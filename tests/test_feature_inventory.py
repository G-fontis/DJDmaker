from pathlib import Path
import re


INVENTORY = Path(__file__).parents[1] / "docs" / "v013-stability-feature-inventory.md"


def _rows() -> list[tuple[str, str]]:
    rows = []
    for line in INVENTORY.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\| ((?:GNB|DVG|HLS)-\d+) \|.*\| ([ABCD]) \|", line)
        if match:
            rows.append((match.group(1), match.group(2)))
    return rows


def test_feature_inventory_is_complete_and_has_no_required_feature_unclassified():
    rows = _rows()
    assert len(rows) == 26
    assert len({item_id for item_id, _classification in rows}) == len(rows)
    assert set(classification for _item_id, classification in rows) <= set("ABC")
    assert not [item_id for item_id, classification in rows if classification == "D"]
