"""画像の 4 経路を実際に走らせて、何件採れるかを数える（S3 の先行計測。ADR 0006）。

経路（sitemill ADR 0020）:
  1. 自治体・観光協会のフリー素材 — 規約ページをホワイトリスト判定にかける
  2. Wikimedia Commons の CC BY / CC0 — カテゴリページ → ファイルページ → ライセンス欄
  3. 公式 SNS の埋め込み — 施設の公式サイトから公式アカウントを探す
  4. Google Maps の埋め込み — 鍵があれば埋め込み、無ければ外部リンク

robots.txt は PoliteClient が守る。Commons は `/w/api.php` と `/wiki/Special:` が禁止なので
API 検索は使わない（カテゴリ経由のみ）。

使い方（japan-open-today のルートで）:
    uv run python tools/probe_assets.py [--limit N] [--out 出力先.json]

対象施設の一覧はここに直書きしている。S4 で `data/sources/kagawa.yaml` に移し、
全件の計測はそこから読む。
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sitemill.assets import AssetPolicy, AssetStore, fetch_asset
from sitemill.assets.commons import fetch_category_files, fetch_file
from sitemill.assets.terms import fetch_terms
from sitemill.clock import jst_now
from sitemill.embeds.maps import maps_place_embed
from sitemill.fetch.client import PoliteClient
from sitemill.settings import Workspace

UA_SUFFIX = "japan-open-today asset probe"


@dataclass(frozen=True)
class Spot:
    """計測対象の施設。commons は Commons のカテゴリ名（無ければ空）。"""

    spot_id: str
    name: str
    area: str
    official_url: str
    commons_category: str = ""
    map_query: str = ""


def spots_from_sources() -> tuple[Spot, ...]:
    """`data/sources/kagawa.yaml` から計測対象を読む（S4 以降）。

    commons_category は tools/resolve_commons.py が**リンクを辿って確定**したもので、
    空なら「カテゴリなし」として扱う（推測は入れない）。
    """
    from japan_open_today.data import Dataset

    ws = Workspace.open(Path.cwd())
    return tuple(
        Spot(
            spot_id=s.spot_id,
            name=s.name("ja"),
            area=s.area,
            official_url=s.official_url,
            commons_category=s.commons_category,
            map_query=s.map_query or s.name("ja"),
        )
        for s in Dataset.load(ws).spots
    )


SPOTS: tuple[Spot, ...] = spots_from_sources()

# 経路 1: 素材・規約ページを探す起点（自治体・観光協会）
TERMS_SITES: tuple[tuple[str, str], str] = (  # type: ignore[assignment]
    (("kagawa-pref", "香川県"), "https://www.pref.kagawa.lg.jp/"),
    (("kagawa-kanko", "香川県観光協会（うどん県旅ネット）"), "https://www.my-kagawa.jp/"),
    (("takamatsu", "高松市"), "https://www.city.takamatsu.kagawa.jp/"),
    (("shodoshima", "小豆島観光協会"), "https://shodoshima.or.jp/"),
    (("mitoyo", "三豊市観光交流局"), "https://www.mitoyo-kanko.com/"),
)
TERMS_KEYWORDS = re.compile(
    r"著作権|利用規約|このサイト|サイトポリシー|フリー素材|写真素材|画像の利用|素材集"
)
SNS_PATTERNS = {
    "youtube": re.compile(r"https?://(?:www\.)?youtube\.com/(?:@|channel/|c/|user/)[\w.-]+", re.I),
    "youtube_video": re.compile(r"https?://(?:www\.)?youtube\.com/watch\?v=([\w-]{6,20})", re.I),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/[\w.]+", re.I),
    "x": re.compile(r"https?://(?:www\.)?(?:twitter|x)\.com/[\w]+", re.I),
}
COMMONS_CATEGORY = "https://commons.wikimedia.org/wiki/Category:{}"


@dataclass
class SpotResult:
    spot_id: str
    name: str
    adopted: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    sns: dict[str, str] = field(default_factory=dict)
    map_embed: bool = False
    map_link: str = ""
    notes: list[str] = field(default_factory=list)


def probe_terms(client: PoliteClient) -> list[dict[str, Any]]:
    """経路 1。トップページから規約・素材ページを探し、ホワイトリスト判定にかける。"""
    out: list[dict[str, Any]] = []
    for (site_id, credit_name), top in TERMS_SITES:
        res = client.get(top)
        if not res.ok:
            out.append(
                {"site": site_id, "url": top, "result": "取得できない", "detail": str(res.status)}
            )
            continue
        candidates: list[str] = []
        for m in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', res.text, re.S | re.I):
            href, label = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
            if TERMS_KEYWORDS.search(label) or TERMS_KEYWORDS.search(href):
                url = href if href.startswith("http") else top.rstrip("/") + "/" + href.lstrip("/")
                if url not in candidates:
                    candidates.append(url)
        if not candidates:
            out.append({"site": site_id, "url": top, "result": "規約ページのリンクが見つからない"})
            continue
        for url in candidates[:2]:
            verdict, error = fetch_terms(client, url, credit_name=credit_name)
            if verdict is None:
                out.append({"site": site_id, "url": url, "result": "取得できない", "detail": error})
                continue
            out.append(
                {
                    "site": site_id,
                    "url": url,
                    "result": "採用" if verdict.allowed else "不採用",
                    "detail": verdict.reason,
                    "license": verdict.verdict.license_id.value
                    if verdict.verdict.license_id
                    else None,
                }
            )
    return out


def probe_commons(client: PoliteClient, spot: Spot, result: SpotResult, *, files: int) -> None:
    """経路 2。カテゴリ → ファイルページ → ライセンス欄。"""
    if not spot.commons_category:
        result.notes.append("Commons のカテゴリ名を用意していない")
        return
    category = COMMONS_CATEGORY.format(spot.commons_category.replace(" ", "_"))
    urls, error = fetch_category_files(client, category, limit=files)
    if error is not None:
        result.notes.append(f"Commons: {error}（{category}）")
        return
    if not urls:
        result.notes.append(f"Commons: カテゴリにファイルが無い（{category}）")
        return
    for page_url in urls[:files]:
        page, error = fetch_file(client, page_url)
        if page is None:
            result.rejected.append({"route": "commons", "url": page_url, "reason": error})
            continue
        row = {
            "route": "commons",
            "url": page_url,
            "image_url": page.image_url,
            "author": page.author,
            "license": page.verdict.license_id.value if page.verdict.license_id else None,
            "licenses_found": list(page.licenses_found),
            "reason": page.verdict.reason,
            "credit_text": page.credit_text,
        }
        (result.adopted if page.verdict.allowed and page.image_url else result.rejected).append(row)


def probe_sns(client: PoliteClient, spot: Spot, result: SpotResult) -> None:
    """経路 3。公式サイトから公式 SNS アカウントを探す。"""
    res = client.get(spot.official_url)
    if not res.ok:
        result.notes.append(f"公式サイトを取得できない（status={res.status}）")
        return
    for name, pattern in SNS_PATTERNS.items():
        m = pattern.search(res.text)
        if m:
            result.sns[name] = m.group(0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=len(SPOTS))
    parser.add_argument(
        "--files", type=int, default=4, help="1 施設あたり見る Commons のファイル数"
    )
    parser.add_argument("--out", type=Path, default=Path("data/runs/asset-probe.json"))
    parser.add_argument("--skip-terms", action="store_true")
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    policy = AssetPolicy()
    ua = f"{ws.site.user_agent} {UA_SUFFIX}"
    spots = SPOTS[: args.limit]
    results: list[SpotResult] = []
    terms: list[dict[str, Any]] = []

    with PoliteClient(ua, default_delay=3.0, jitter=1.0, timeout=30.0) as client:
        if not args.skip_terms:
            terms = probe_terms(client)
        for spot in spots:
            result = SpotResult(spot_id=spot.spot_id, name=spot.name)
            probe_sns(client, spot, result)
            probe_commons(client, spot, result, files=args.files)
            embed = maps_place_embed(
                spot.map_query or spot.name,
                api_key=ws.secrets.google_maps_embed_key,
                title=spot.name,
            )
            result.map_embed = embed.embeddable
            result.map_link = embed.link_url
            # 採用できたものだけ実際に取得して保存する
            store = AssetStore(ws.data_dir / "assets", spot.spot_id)
            for row in list(result.adopted):
                try:
                    page, _ = fetch_file(client, row["url"])
                    if page is None or page.image_url is None:
                        continue
                    target = page.usable_url()
                    if target is None:
                        continue
                    asset = fetch_asset(
                        target,
                        page.verdict,
                        policy,
                        store,
                        client=client,
                        credit_text=page.credit_text,
                        author=page.author,
                        page_url=page.page_url,
                        alt_text=spot.name,
                        tags=[spot.area],
                    )
                    row["asset_id"] = asset.asset_id
                    row["byte_size"] = asset.byte_size
                except Exception as e:  # noqa: BLE001 - 計測なので理由を残して続ける
                    row["download_error"] = str(e)
                    result.adopted.remove(row)
                    result.rejected.append(row)
            results.append(result)
            print(
                f"{spot.spot_id:16} 採用 {len(result.adopted)} / 却下 {len(result.rejected)}"
                f" / SNS {','.join(result.sns) or '-'}"
                f" / 地図 {'埋込' if result.map_embed else 'リンク'}",
                flush=True,
            )
        requests = client.request_count

    payload = {
        "probed_at": jst_now().isoformat(),
        "requests": requests,
        "maps_key_present": bool(ws.secrets.google_maps_embed_key),
        "terms": terms,
        "spots": [asdict(r) for r in results],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    print(f"\n書き出し: {args.out}（{requests} リクエスト）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
