"""1 枚の写真が 2 つ以上の場所に使われていないかを検査する（公開前検査）。

写真はその場所を指す事実として出す。別の場所の写真を載せるのは、営業時間を取り違えるのと
同じ種類の誤りになる（ADR 0006 追記）。2026-09-13 に 12 の寺のページへ善通寺の写真が載り、
直した後も日次パイプラインのキャッシュが古い `assets.json` を書き戻して再発した。
取り込み側の歯止め（`tools/fetch_photos.py`）だけでは、取り込み以外の経路で戻ったときに
気づけないので、配置の前にもう一度数える。

同じ写真かどうかは `asset_id`（取得したバイト列のハッシュ）では決めない。Commons の
サムネイルは再生成でバイト列が変わり、同じファイルでも id が変わる。説明ページ
（`page_url`、無ければ `source_url`）で数える。

使い方:
    uv run python -m tools.check_assets     # 重複があれば一覧を出して終了コード 1
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from sitemill.assets import AssetStore
from sitemill.settings import Workspace


def shared_photos(root: Path) -> dict[str, list[str]]:
    """2 つ以上の場所に使われている写真 → 使っている場所の id（並びは id 順）。"""
    users: dict[str, set[str]] = defaultdict(set)
    if not root.is_dir():
        return {}
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        for asset in AssetStore(root, directory.name).load().values():
            if not asset.usable:
                continue
            users[asset.page_url or asset.source_url].add(directory.name)
    return {photo: sorted(ids) for photo, ids in users.items() if len(ids) > 1}


def main() -> int:
    ws = Workspace.open(Path.cwd())
    shared = shared_photos(ws.data_dir / "assets")
    if not shared:
        print("写真の重複: なし（1 枚の写真は 1 つの場所にだけ使われている）")
        return 0
    print(f"::error::1 枚の写真が複数の場所に使われている: {len(shared)} 件")
    for photo, ids in sorted(shared.items()):
        print(f"  {len(ids)} か所 {photo}")
        print(f"      {', '.join(ids)}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
