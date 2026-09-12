"""施設ごとの Wikimedia Commons カテゴリを、リンクを辿って確定する（S4）。

**カテゴリ名は推測しない。** 辿る順序:
  1. 施設の公式サイト → Commons カテゴリへのリンク（あれば最強。公式が示している）
  2. Wikipedia 日本語版の記事 → 「ウィキメディア・コモンズ」のリンク
  3. 見つからなければ `commons_category: ""`（カテゴリなし）として記録する

記事の URL は施設名から組み立てるが、**そこから先は必ずリンクを辿る**ので、カテゴリ名そのものを
当てにいくことはない。記事が無ければ「カテゴリなし」になる。

Commons の robots.txt は `/wiki/Special:` と `/w/` を禁じているので検索は使えない（ADR 0020）。

使い方（japan-open-today のルートで）:
    uv run python tools/resolve_commons.py [--out data/runs/commons-categories.json]
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote

from sitemill.assets.commons import category_name, fetch_category_links
from sitemill.clock import jst_now
from sitemill.fetch.client import PoliteClient
from sitemill.settings import Workspace

from tools.probe_assets import SPOTS  # 対象施設の一覧（S4 で kagawa.yaml に移す）

WIKIPEDIA_JA = "https://ja.wikipedia.org/wiki/{}"
# 記事名が施設名と違うもの。記事の**場所**を人が指定するだけで、カテゴリ名は辿って決める
ARTICLE_OVERRIDES = {
    "yashima": "屋島",
    "naoshima-sento": "直島bankART",
    "24hitomi": "二十四の瞳映画村",
    "shikokumura": "四国村",
    "kmuseum": "香川県立ミュージアム",
    "tamamo": "高松城 (讃岐国)",
    "benesse-house": "ベネッセハウス",
    "chichu": "地中美術館",
    "teshima-art": "豊島美術館",
    "konpira": "金刀比羅宮",
    "ritsurin": "栗林公園",
    "kankakei": "寒霞渓",
    "chichibugahama": "父母ヶ浜",
}


@dataclass
class Resolution:
    spot_id: str
    name: str
    category: str = ""
    category_url: str = ""
    resolved_from: str = ""  # official / wikipedia / none
    tried: list[str] = field(default_factory=list)
    note: str = ""


def resolve(client: PoliteClient, spot: object) -> Resolution:
    out = Resolution(spot_id=spot.spot_id, name=spot.name)  # type: ignore[attr-defined]

    # 1. 公式サイト
    official = spot.official_url  # type: ignore[attr-defined]
    out.tried.append(official)
    links, error = fetch_category_links(client, official)
    if links:
        out.category_url, out.resolved_from = links[0], "official"
        out.category = category_name(links[0])
        return out
    if error:
        out.note = f"公式サイト: {error}"

    # 2. Wikipedia 日本語版の記事
    title = ARTICLE_OVERRIDES.get(out.spot_id, out.name)
    article = WIKIPEDIA_JA.format(quote(title.replace(" ", "_")))
    out.tried.append(article)
    links, error = fetch_category_links(client, article)
    if links:
        out.category_url, out.resolved_from = links[0], "wikipedia"
        out.category = category_name(links[0])
        return out
    out.resolved_from = "none"
    out.note = (out.note + " / " if out.note else "") + (
        f"Wikipedia: {error}" if error else "Wikipedia: コモンズへのリンクが無い"
    )
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("data/runs/commons-categories.json"))
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    ua = f"{ws.site.user_agent} commons category resolver"
    rows: list[Resolution] = []
    with PoliteClient(ua, default_delay=3.0, jitter=1.0, timeout=30.0) as client:
        for spot in SPOTS:
            row = resolve(client, spot)
            rows.append(row)
            print(
                f"{row.spot_id:16} {row.resolved_from:10} {row.category or '（カテゴリなし）'}",
                flush=True,
            )
        requests = client.request_count

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "resolved_at": jst_now().isoformat(),
                "requests": requests,
                "rows": [asdict(r) for r in rows],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    found = sum(1 for r in rows if r.category)
    print(f"\n確定 {found}/{len(rows)} 件 → {args.out}（{requests} リクエスト）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
