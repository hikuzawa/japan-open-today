"""施設ごとの Wikimedia Commons カテゴリを、リンクを辿って確定する（S4。S8 で全件に広げた）。

**カテゴリ名は推測しない。** 辿る順序:
  1. 施設の公式サイト → Commons カテゴリへのリンク（あれば最強。公式が示している）
  2. Wikipedia 日本語版の記事 → 「ウィキメディア・コモンズ」のリンク
  3. 見つからなければ `commons_category: ""`（カテゴリなし）として記録する

記事の URL は施設名から組み立てるが、**そこから先は必ずリンクを辿る**ので、カテゴリ名そのものを
当てにいくことはない。ただし施設名だけで記事を引くと**別の場所の記事に当たる**
（「城山」「本島」「広島」のような一般的な名前が香川県内にある）。そこで、記事を取り込む前に
次を確かめる（ADR 0009 追記の「名前がページに出てくること」と同じ考え方）。

  - 曖昧さ回避のページではないこと
  - 本文に「香川」と、その施設の所在地の市町名が出てくること

どちらかでも欠ければ、その記事は使わない（推測で当てない）。

Commons の robots.txt は `/wiki/Special:` と `/w/` を禁じているので検索は使えない（ADR 0020）。

使い方（japan-open-today のルートで）:
    uv run python -m tools.resolve_commons                    # 見るだけ
    uv run python -m tools.resolve_commons --apply            # kagawa.yaml に書く
    uv run python -m tools.resolve_commons --limit 20
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import quote

from selectolax.parser import HTMLParser
from sitemill.assets.commons import category_name, fetch_category_links, find_category_links
from sitemill.clock import jst_now
from sitemill.diff.normalize import page_text, squash
from sitemill.fetch.client import PoliteClient
from sitemill.settings import Workspace

from japan_open_today.data import Dataset

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


@dataclass(frozen=True)
class _Target:
    """解決に使う施設の値だけを取り出したもの（`Spot` は名前がメソッドなのでそのまま渡せない）。"""

    spot_id: str
    name: str
    official_url: str
    address: str

    @classmethod
    def of(cls, spot: object) -> _Target:
        return cls(
            spot_id=spot.spot_id,  # type: ignore[attr-defined]
            name=spot.name("ja"),  # type: ignore[attr-defined]
            official_url=spot.official_url,  # type: ignore[attr-defined]
            address=spot.address.value or spot.address.quote or "",  # type: ignore[attr-defined]
        )


@dataclass
class Resolution:
    spot_id: str
    name: str
    category: str = ""
    category_url: str = ""
    resolved_from: str = ""  # official / wikipedia / none
    tried: list[str] = field(default_factory=list)
    note: str = ""


MUNICIPALITY = re.compile(r"[^\s　]{1,6}?[市町村](?![立営])")
# 記事が張っている Commons カテゴリが、その場所ではなく**話題**のことがある
# （滝宮天満宮の記事は {{Commonscat|Shintō}} を張っていて、神道全体のカテゴリに飛ぶ）。
# 施設の写真が並んでいる保証が無いので採らない
GENERIC_CATEGORIES = {
    "shinto",
    "shintō",
    "shinto shrines",
    "buddhism",
    "buddhist temples in japan",
    "japan",
    "kagawa prefecture",
    "shikoku",
    "seto inland sea",
    "torii",
    "shrines in japan",
}
DISAMBIGUATION = re.compile(r"曖昧さ回避|に関する記事の一覧")


def _municipality(address: str) -> str:
    """所在地から市町名を取る（「香川県高松市栗林町…」→「高松市」）。"""
    text = (address or "").replace("香川県", "")
    m = MUNICIPALITY.search(text)
    return m.group(0) if m else ""


def article_is_about(html: str, name: str, municipality: str) -> str:
    """その記事がこの施設の記事か。使えないときは理由を返す（使えるなら空文字）。

    施設名だけで記事を引くと別の場所に当たる。香川県内には「城山」「本島」「広島」のような
    一般的な名前の場所があり、同名の記事は全国にある。
    """
    text = page_text(html)
    heading = HTMLParser(html).css_first("h1")
    title = " ".join((heading.text() if heading else "").split())
    if DISAMBIGUATION.search(title) or DISAMBIGUATION.search(text[:400]):
        return "曖昧さ回避のページ"
    if "香川" not in text[:4000]:
        return "本文に「香川」が出てこない"
    if municipality and municipality not in text[:4000]:
        return f"本文に所在地の市町（{municipality}）が出てこない"
    if squash(name) not in squash(title) and squash(title) not in squash(name):
        return f"見出しが施設名と違う（{title[:24]}）"
    return ""


def resolve(client: PoliteClient, spot: _Target) -> Resolution:
    out = Resolution(spot_id=spot.spot_id, name=spot.name)

    # 1. 公式サイト
    official = spot.official_url
    out.tried.append(official)
    links, error = fetch_category_links(client, official)
    if links:
        out.category_url, out.resolved_from = links[0], "official"
        out.category = category_name(links[0])
        return out
    if error:
        out.note = f"公式サイト: {error}"

    # 2. Wikipedia 日本語版の記事。**記事がこの施設のものか確かめてから**リンクを辿る
    title = ARTICLE_OVERRIDES.get(out.spot_id, out.name)
    article = WIKIPEDIA_JA.format(quote(title.replace(" ", "_")))
    out.tried.append(article)
    res = client.get(article)
    if not res.ok:
        out.resolved_from = "none"
        out.note = (out.note + " / " if out.note else "") + (
            f"Wikipedia: 記事が無い（status={res.status}）"
        )
        return out
    municipality = _municipality(spot.address)
    mismatch = article_is_about(res.text, out.name, municipality)
    if mismatch:
        out.resolved_from = "none"
        out.note = (out.note + " / " if out.note else "") + f"Wikipedia: {mismatch}"
        return out
    for link in find_category_links(res.text):
        category = category_name(link)
        if category.lower() in GENERIC_CATEGORIES:
            out.note = (out.note + " / " if out.note else "") + (
                f"Wikipedia: 話題のカテゴリ（{category}）なので採らない"
            )
            continue
        out.category_url, out.resolved_from = link, "wikipedia"
        out.category = category
        return out
    out.resolved_from = "none"
    if "話題のカテゴリ" not in out.note:
        out.note = (out.note + " / " if out.note else "") + "Wikipedia: コモンズへのリンクが無い"
    return out


def _save(out: Path, rows: list[Resolution], requests: int) -> None:
    """記録を**足す**。前に確定したものを消さない（確定の由来はここが唯一の記録）。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.is_file():
        stored = json.loads(out.read_text(encoding="utf-8")).get("rows", [])
        fresh = {r.spot_id for r in rows}
        rows = [Resolution(**r) for r in stored if r["spot_id"] not in fresh] + rows
    out.write_text(
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


def _apply(ws: Workspace, rows: list[Resolution]) -> int:
    """確定したカテゴリを kagawa.yaml の spot に書く（すでにある行は触らない）。"""
    path = ws.sources_dir / "kagawa.yaml"
    lines = path.read_text(encoding="utf-8").split("\n")
    added = 0
    for row in rows:
        if not row.category:
            continue
        at = next(
            (i for i, ln in enumerate(lines) if ln.strip() == f"spot_id: {row.spot_id}"),
            -1,
        )
        if at < 0:
            continue
        end = next((i for i in range(at + 1, len(lines)) if not lines[i].startswith("      ")), at)
        block = lines[at:end]
        if any(ln.strip().startswith("commons_category:") for ln in block):
            continue
        indent = " " * (len(lines[at]) - len(lines[at].lstrip()))
        note = "公式サイトのリンク" if row.resolved_from == "official" else "Wikipedia 記事のリンク"
        lines.insert(end, f'{indent}commons_category: "{row.category}"  # {note}から確定')
        added += 1
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"kagawa.yaml に commons_category を {added} 件書いた")
    return added


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("data/runs/commons-categories.json"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--apply", action="store_true", help="kagawa.yaml に書く")
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    ds = Dataset.load(ws)
    # すでにカテゴリの決まっている施設は触らない（確定済みを取り直さない）
    todo = [s for s in ds.spots if not s.commons_category]
    if args.limit:
        todo = todo[: args.limit]
    print(f"== Commons カテゴリの未確定 {len(todo)} 件 ==")
    ua = f"{ws.site.user_agent} commons category resolver"
    rows: list[Resolution] = []
    requests = 0
    with PoliteClient(ua, default_delay=3.0, jitter=1.0, timeout=30.0) as client:
        for n, spot in enumerate(todo, 1):
            row = resolve(client, _Target.of(spot))
            rows.append(row)
            print(
                f"  {n:3}/{len(todo)} {row.spot_id:20} {row.resolved_from:10} "
                f"{row.category or '（カテゴリなし）'}",
                flush=True,
            )
            if n % 20 == 0:
                _save(args.out, rows, client.request_count)
        requests = client.request_count

    _save(args.out, rows, requests)
    found = sum(1 for r in rows if r.category)
    print(f"\n確定 {found}/{len(rows)} 件 → {args.out}（{requests} リクエスト）")
    if args.apply and found:
        _apply(ws, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
