"""採用済みの画像で、手元に実体が無いものを出典から取り直す（日次の実行用）。

画像の実体は git 管理外で、コミットするのは `assets.json`（出典・ライセンス・取得日）だけ
（ADR 0020）。Actions の実行機は毎回まっさらなので、キャッシュが切れると実体が無い状態で
ビルドすることになる。そのままだと本番で画像が 404 になるので、ここで取り直す。

守ること:
- **ライセンスの判定はやり直さない**。ここで扱うのは、すでに判定を通って `assets.json` に
  記録された画像だけ。判定していない URL には触らない
- **内容が変わっていたら採らない**。`asset_id` は取得したバイト列の SHA-256 なので、
  取り直した内容の id が記録と一致することを確かめる。一致しなければ「別の画像に
  差し替わった」ということなので、置かずに報告する（差分でレビューする）
- 取れなくても止めない。実体の無い画像はページに出ない作りなので、写真が減るだけで済む

使い方:
    uv run python -m tools.fetch_assets            # 足りないものを取り直す
    uv run python -m tools.fetch_assets --check    # 取りに行かず、足りない数だけ数える
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sitemill.assets import AssetPolicy, AssetStore, asset_id_for
from sitemill.fetch.client import PoliteClient
from sitemill.settings import Workspace


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="取りに行かず、数えるだけ")
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    root = ws.data_dir / "assets"
    policy = AssetPolicy()
    missing = []
    total = 0
    for directory in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        store = AssetStore(root, directory.name)
        for asset in store.load().values():
            if not asset.usable:
                continue
            total += 1
            if not asset.local_path.is_file():
                missing.append(asset)
    print(f"== 採用済み {total} 件 / 実体が無い {len(missing)} 件 ==")
    if args.check or not missing:
        return 0

    ok = changed = failed = 0
    ua = f"{ws.site.user_agent} asset refetch"
    with PoliteClient(ua, default_delay=2.0, jitter=0.5, timeout=30.0) as client:
        for asset in missing:
            res = client.get(asset.source_url)
            if not res.ok:
                failed += 1
                print(f"  NG {asset.asset_id} 取得できない（status={res.status}）")
                continue
            content_type = (res.headers.get("content-type") or "").split(";")[0].strip().lower()
            if not policy.content_type_ok(content_type):
                failed += 1
                print(f"  NG {asset.asset_id} 扱わない種類（{content_type or '不明'}）")
                continue
            got = asset_id_for(res.content)
            if got != asset.asset_id:
                changed += 1
                print(f"  差替 {asset.asset_id} 出典の画像が変わっている（今の内容は {got}）")
                continue
            asset.local_path.parent.mkdir(parents=True, exist_ok=True)
            asset.local_path.write_bytes(res.content)
            ok += 1
    print(f"取り直し {ok} 件 / 内容が変わっていた {changed} 件 / 取得できない {failed} 件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
