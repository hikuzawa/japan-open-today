"""Klook の飛び先がまだ生きているかの週次点検（ADR 0012 追記、2026-09-22）。

商品は消えることがあり、Klook のページ構成が変わると目的地や検索結果の URL も切れる。
切れたまま広告を出すと、利用者は Klook の「ページが見つかりません」に着く。

**robots.txt が許すものだけを読む。** Klook の商品・目的地のページは素の HTTP では 403 で
拒まれ、検索結果（`*/search/*`）は robots.txt が全ボットに禁じている。そこで:

- robots.txt の `Sitemap:` → サイトマップの索引 → 商品・都市のサイトマップ、とリンクで辿り、
  対応表の URL がまだ載っているかを見る（このサイトの巡回用 UA で、3 秒ずつ空けて 4 回だけ）
- 香川の検索結果 URL は**取りに行かない**。人がブラウザで開いて確かめる項目として出す

出力は週次まとめ（Issue）に貼る Markdown。点検で落ちても週次の実行は止めない。

使い方: uv run python -m tools.check_klook
"""

from __future__ import annotations

import re
import time
from pathlib import Path

import httpx
from sitemill.settings import Workspace

from japan_open_today import klook

ROBOTS = "https://www.klook.com/robots.txt"
GAP_SECONDS = 3.0
_LOC = re.compile(r"<loc>([^<]+)</loc>")


def _get(client: httpx.Client, url: str) -> str | None:
    try:
        resp = client.get(url, timeout=60)
    except httpx.HTTPError:
        return None
    finally:
        time.sleep(GAP_SECONDS)
    return resp.text if resp.status_code == 200 else None


def _pick(index: str, name: str) -> str | None:
    """索引の中から、名前の一致するサイトマップの URL を選ぶ（URL は組み立てない）。"""
    return next((loc for loc in _LOC.findall(index) if loc.endswith(f"/{name}")), None)


def main() -> int:
    ws = Workspace.open(Path.cwd())
    lines = ["## Klook の飛び先の点検", ""]
    headers = {"User-Agent": ws.site.user_agent}
    with httpx.Client(headers=headers, follow_redirects=True) as client:
        robots = _get(client, ROBOTS)
        if robots is None:
            lines.append("- robots.txt を取得できなかった。今週は点検しない")
            print("\n".join(lines))
            return 0
        declared = [ln for ln in robots.splitlines() if ln.startswith("Sitemap:")]
        sitemap = declared[0].split(":", 1)[1].strip() if declared else None
        if "*/search/*" not in robots:
            lines.append(
                "- **robots.txt から検索結果の禁止（`*/search/*`）が消えた**。点検の方法を見直せる"
            )
        index = _get(client, sitemap) if sitemap else None
        if index is None:
            lines.append(f"- サイトマップの索引（{sitemap}）を取得できなかった。今週は点検しない")
            print("\n".join(lines))
            return 0
        found: dict[str, str] = {}
        for name in (
            "sitemap-experiences-activity-plain_ja.xml",
            "sitemap-city-plain_ja.xml",
        ):
            url = _pick(index, name)
            body = _get(client, url) if url else None
            if body is None:
                lines.append(f"- `{name}` を索引から辿れなかった（Klook の構成が変わった可能性）")
                continue
            found[name] = body

    listed = set()
    for body in found.values():
        listed.update(_LOC.findall(body))
    broken = []
    ok = 0
    for landing in klook.landings():
        url = landing.urls.get("ja")
        if not url or "search" in url:
            continue
        if not found:
            break
        if url in listed:
            ok += 1
        else:
            broken.append(landing)
    lines.append(f"- 商品・目的地のページ: {ok} 件がサイトマップに載っている")
    for landing in broken:
        lines.append(
            f"  - **サイトマップから消えた**: `{landing.id}`（{landing.title}）{landing.urls['ja']}"
            " — 対応表から外すか、別の商品を辿り直す"
        )
    search = klook.BY_ID[klook.DEFAULT].urls.get("ja")
    lines.append(
        "- 香川の検索結果ページ: **手で確かめる**（robots.txt が検索結果の自動取得を禁じているため"
        "、こちらからは開かない）。ブラウザで開いて、香川の商品が並ぶことを見る"
        + (f": {search}" if search else "（URL をまだ受け取っていない）")
    )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
