"""写真は 1 枚につき 1 つの場所（ADR 0006 追記）。外部アクセスはしない。"""

from __future__ import annotations

import json
from pathlib import Path

from tools.check_assets import shared_photos

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "data" / "assets"


def test_no_committed_photo_is_used_for_two_places() -> None:
    """コミット済みのデータで、別の場所の写真を載せていない。

    2026-09-13 に 12 の寺へ善通寺の写真が載り、直した夜に日次のキャッシュが書き戻した。
    """
    assert shared_photos(ASSETS) == {}


def _record() -> dict:
    """実在の記録を 1 件借りる（項目の形をテストで作り直さないため）。"""
    for path in sorted(ASSETS.glob("*/assets.json")):
        assets = json.loads(path.read_text(encoding="utf-8"))["assets"]
        if assets:
            return assets[0]
    raise AssertionError("data/assets に記録が 1 件も無い")


def _place(root: Path, place: str, record: dict) -> None:
    (root / place).mkdir(parents=True)
    (root / place / "assets.json").write_text(
        json.dumps({"source_id": place, "assets": [record]}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_a_rethumbnailed_copy_is_still_the_same_photo(tmp_path: Path) -> None:
    """Commons のサムネイルは再生成でバイト列（= asset_id）が変わる。説明ページで数える。"""
    record = _record()
    _place(tmp_path, "temple-a", record)
    _place(tmp_path, "temple-b", record | {"asset_id": "0" * 16, "byte_size": 1})
    assert shared_photos(tmp_path) == {record["page_url"]: ["temple-a", "temple-b"]}


def test_one_photo_for_one_place_passes(tmp_path: Path) -> None:
    _place(tmp_path, "temple-a", _record())
    assert shared_photos(tmp_path) == {}
