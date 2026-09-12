"""採用した画像を data/assets/ から static/assets/ に複写する（S5）。

`data/assets/` は取得物のローカルキャッシュで、`static/` はビルドが dist へ複写する置き場。
画像の実体はどちらも git 管理外にして、`assets.json`（出典・ライセンス・取得日）だけをコミットする。
日次の実行では extract のあと build の前にこれを走らせる。

使い方: uv run python -m tools.sync_assets
"""

from __future__ import annotations

import shutil
from pathlib import Path

from sitemill.assets import AssetStore
from sitemill.settings import Workspace


def main() -> int:
    ws = Workspace.open(Path.cwd())
    root = ws.data_dir / "assets"
    dest = ws.static_dir / "assets"
    dest.mkdir(parents=True, exist_ok=True)
    copied = skipped = 0
    for directory in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        for asset in AssetStore(root, directory.name).load().values():
            if not asset.usable:
                skipped += 1  # ライセンスを説明できないものは出さない
                continue
            if not asset.local_path.is_file():
                skipped += 1
                continue
            target = dest / asset.local_path.name
            if not target.exists() or target.stat().st_size != asset.local_path.stat().st_size:
                shutil.copy2(asset.local_path, target)
            copied += 1
    print(f"複写 {copied} 件 / 見送り {skipped} 件 → {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
