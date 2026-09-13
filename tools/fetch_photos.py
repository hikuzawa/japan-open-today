"""Commons のカテゴリが決まった施設に、写真を 1 枚ずつ採る（ADR 0006・sitemill ADR 0020）。

`tools.resolve_commons` が**リンクを辿って確定した**カテゴリだけを入口にする。カテゴリから
ファイルページを辿り、ライセンス欄を読んで判定を通ったものだけを取得する。判定の前に画像を
取りに行かない（「判定 → 取得」の順。ADR 0020）。

守ること:
- カテゴリ名も画像の URL も**推測しない**。カテゴリページに並んでいるファイルページを辿る
- ライセンスが説明できないものは採らない。CC BY / CC0 などホワイトリストに一致した証拠が要る
- 1 施設 1 枚。最初に判定を通った 1 枚で止める（ページに出すのは 1 枚なので、
  取る必要のない画像は取りに行かない）
- **同じ画像を 2 つの施設に使わない**。写真はその場所を指すものなので、別の場所の写真が
  並ぶと嘘になる（実測: 12 の寺がどれも善通寺の写真、男木島と女木島が同じ古地図）
- 画像の実体は git 管理外。`assets.json`（出典・ライセンス・取得日）だけをコミットする

使い方:
    uv run python -m tools.fetch_photos --limit 20     # 少しだけ試す
    uv run python -m tools.fetch_photos
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote

from sitemill.assets import AssetPolicy, AssetStore, fetch_asset
from sitemill.assets.commons import fetch_category_files, fetch_file
from sitemill.clock import jst_now
from sitemill.fetch.client import PoliteClient
from sitemill.settings import Workspace

from japan_open_today.data import Dataset

CATEGORY_URL = "https://commons.wikimedia.org/wiki/Category:{}"
# 1 施設で見るファイルページの上限。カテゴリの先頭から順に見て、通った 1 枚で止める
MAX_FILES = 8


@dataclass
class Taken:
    spot_id: str
    name: str
    category: str
    asset_id: str = ""
    license: str = ""
    page_url: str = ""
    tried: int = 0
    note: str = ""
    rejected: list[str] = field(default_factory=list)


def take_one(
    client: PoliteClient,
    store: AssetStore,
    spot,
    policy: AssetPolicy,
    used: set[str] | None = None,
) -> Taken:
    """その施設のカテゴリから、判定を通った最初の 1 枚を採る。

    `used` には他の施設がすでに使っている画像の id を渡す。同じ画像は採らない。
    """
    out = Taken(spot_id=spot.spot_id, name=spot.name("ja"), category=spot.commons_category)
    # カテゴリ名は確定済みのものをそのまま使う（ここで組み立てるのは URL の形だけ）
    url = CATEGORY_URL.format(quote(spot.commons_category.replace(" ", "_")))
    files, error = fetch_category_files(client, url, limit=MAX_FILES)
    if error:
        out.note = error
        return out
    if not files:
        out.note = "カテゴリにファイルが無い"
        return out
    for page_url in files[:MAX_FILES]:
        out.tried += 1
        page, err = fetch_file(client, page_url)
        if page is None:
            out.rejected.append(f"{page_url}: {err or '読めない'}")
            continue
        if not page.verdict.allowed:
            out.rejected.append(f"{page.page_url}: {page.verdict.reason}")
            continue
        target = page.usable_url()
        if target is None:
            out.rejected.append(f"{page.page_url}: 使える URL が無い")
            continue
        try:
            asset = fetch_asset(
                target,
                page.verdict,
                policy,
                store,
                client=client,
                credit_text=page.credit_text,
                author=page.author,
                page_url=page.page_url,
                alt_text=spot.name("ja"),
                tags=[spot.area],
            )
        except Exception as e:  # noqa: BLE001 - 理由を残して次の候補へ
            out.rejected.append(f"{page.page_url}: {e}")
            continue
        if used is not None and asset.asset_id in used:
            # 他の施設がすでに使っている画像。その場所を指していないので採らない
            out.rejected.append(f"{page.page_url}: 他の施設で使っている画像")
            remaining = {k: v for k, v in store.load().items() if k != asset.asset_id}
            store.save(remaining.values())
            continue
        if used is not None:
            used.add(asset.asset_id)
        out.asset_id = asset.asset_id
        out.license = page.verdict.label
        out.page_url = page.page_url
        return out
    out.note = "判定を通る画像が無かった"
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", type=Path, default=Path("data/runs/photos.json"))
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    ds = Dataset.load(ws)
    root = ws.data_dir / "assets"
    todo = []
    for spot in ds.spots:
        if not spot.commons_category:
            continue
        store = AssetStore(root, spot.spot_id)
        if any(a.usable for a in store.load().values()):
            continue  # すでに写真がある
        todo.append(spot)
    if args.limit:
        todo = todo[: args.limit]
    print(f"== 写真の無い施設のうち、カテゴリが決まっている {len(todo)} 件 ==")

    policy = AssetPolicy()
    # すでにどこかの施設が使っている画像。同じものを別の施設に使わない
    used = {
        a.asset_id
        for d in (p for p in root.iterdir() if p.is_dir())
        for a in AssetStore(root, d.name).load().values()
        if a.usable
    }
    rows: list[Taken] = []
    ua = f"{ws.site.user_agent} commons photos"
    with PoliteClient(ua, default_delay=3.0, jitter=1.0, timeout=60.0) as client:
        for n, spot in enumerate(todo, 1):
            row = take_one(client, AssetStore(root, spot.spot_id), spot, policy, used)
            rows.append(row)
            mark = "OK " if row.asset_id else "NG "
            print(
                f"  {n:3}/{len(todo)} {mark}{row.name[:20]:22} {row.license or row.note[:30]}",
                flush=True,
            )
            if n % 10 == 0:
                _save(args.out, rows, client.request_count)
        requests = client.request_count

    _save(args.out, rows, requests)
    got = sum(1 for r in rows if r.asset_id)
    print(f"\n採用 {got}/{len(rows)} 件（{requests} リクエスト）→ {args.out}")
    return 0


def _save(out: Path, rows: list[Taken], requests: int) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "collected_at": jst_now().isoformat(),
                "requests": requests,
                "rows": [asdict(r) for r in rows],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    raise SystemExit(main())
