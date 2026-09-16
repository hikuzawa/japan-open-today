"""採用済みの画像で、手元に実体が無いものを出典から取り直す（日次の実行用）。

画像の実体は git 管理外で、コミットするのは `assets.json`（出典・ライセンス・取得日）だけ
（ADR 0020）。Actions の実行機は毎回まっさらなので、キャッシュが切れると実体が無い状態で
ビルドすることになる。そのままだと本番で画像が 404 になるので、ここで取り直す。

守ること:
- **ライセンスの判定はやり直さない**。ここで扱うのは、すでに判定を通って `assets.json` に
  記録された画像だけ。判定していない URL には触らない
- **内容が変わっていたら、判定からやり直す**。`asset_id` は取得したバイト列の SHA-256 なので、
  取り直した内容の id が記録と一致するとは限らない（Commons のサムネイルは元のファイルが
  同じでも再生成でバイト列が変わる。四国村の写真で実際に起きた）。一致しないときは
  **記録された説明ページを読み直してライセンスを判定し直し**、通ったものだけを新しい
  `asset_id` で登録し直す（ADR 0020 の「判定 → 取得」の順を崩さない）。
  説明ページが無い・判定が通らないものは置かずに報告する
- 取れなくても止めない。実体の無い画像はページに出ない作りなので、写真が減るだけで済む

使い方:
    uv run python -m tools.fetch_assets            # 足りないものを取り直す
    uv run python -m tools.fetch_assets --check    # 取りに行かず、足りない数だけ数える
"""

from __future__ import annotations

import argparse
from pathlib import Path

from sitemill.assets import AssetPolicy, AssetStore, asset_id_for, fetch_asset
from sitemill.assets.commons import fetch_file
from sitemill.fetch.client import PoliteClient
from sitemill.settings import Workspace


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="取りに行かず、数えるだけ")
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    root = ws.data_dir / "assets"
    policy = AssetPolicy()
    # (画像, その画像を持つ場所の store)。id だけで store を引くと、同じ画像を複数の場所が
    # 持っていたときに最後の場所しか更新されない（2026-09-16 に 12 か所で起きた）
    missing = []
    total = 0
    for directory in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        store = AssetStore(root, directory.name)
        for asset in store.load().values():
            if not asset.usable:
                continue
            total += 1
            if not asset.local_path.is_file():
                missing.append((asset, store))
    print(f"== 採用済み {total} 件 / 実体が無い {len(missing)} 件 ==")
    if args.check or not missing:
        return 0

    ok = changed = failed = 0
    ua = f"{ws.site.user_agent} asset refetch"
    with PoliteClient(ua, default_delay=2.0, jitter=0.5, timeout=30.0) as client:
        for asset, store in missing:
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
                # バイト列が変わっている。説明ページからライセンスを判定し直して登録し直す
                if _rejudge(asset, store, policy, client=client):
                    ok += 1
                else:
                    changed += 1
                continue
            asset.local_path.parent.mkdir(parents=True, exist_ok=True)
            asset.local_path.write_bytes(res.content)
            ok += 1
    print(f"取り直し {ok} 件 / 採らなかった {changed} 件 / 取得できない {failed} 件")
    return 0


def _rejudge(asset, store: AssetStore, policy: AssetPolicy, *, client) -> bool:
    """出典の説明ページを読み直して判定し、通ったら新しい id で登録し直す。

    Commons のサムネイルは元のファイルが同じでも再生成でバイト列が変わる。バイト列の
    不一致だけで捨てると写真が減り続けるが、判定を省いて採ると ADR 0020 を崩す。
    **判定からやり直す**のが正しい。判定が通らなければ採らない。
    """
    if not asset.page_url:
        print(f"  見送り {asset.asset_id} 内容が変わっているが説明ページの記録が無い")
        return False
    page, error = fetch_file(client, asset.page_url)
    if page is None or page.image_url is None:
        print(f"  見送り {asset.asset_id} 説明ページを読めない（{error or '画像が無い'}）")
        return False
    if not page.verdict.allowed:
        print(f"  見送り {asset.asset_id} 判定が通らない（{page.verdict.reason}）")
        return False
    target = page.usable_url()
    if target is None:
        print(f"  見送り {asset.asset_id} 使える URL が無い")
        return False
    fresh = fetch_asset(
        target,
        page.verdict,
        policy,
        store,
        client=client,
        credit_text=page.credit_text,
        author=page.author,
        page_url=page.page_url,
        alt_text=asset.alt_text,
        tags=asset.tags,
    )
    remaining = {k: v for k, v in store.load().items() if k != asset.asset_id}
    store.save(remaining.values())
    print(f"  再判定 {asset.asset_id} → {fresh.asset_id}（{page.verdict.label}）")
    return True


if __name__ == "__main__":
    raise SystemExit(main())
