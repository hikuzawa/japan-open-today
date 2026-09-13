"""市町のフリー素材（画像の経路 1）を当たる（ADR 0006 の経路 1。sitemill ADR 0020）。

S3 で県・高松市・観光協会の 3 か所を見たときは、いずれも「無断転載禁止」で 0 件だった。
残り 14 市町を当たって、**公的機関が商用可で観光写真を公開しているか**を実測で確かめる。

やること:
  1. 県のサイトの「県内市町のホームページ」から、17 市町のリンクを**辿って**集める
     （URL は推測しない。県のリンク集に無い市町は「辿れなかった」として記録する）
  2. 各サイトのトップから、著作権・利用規約・フリー素材らしいリンクを拾う
  3. 拾ったページを `sitemill.assets.terms` の判定にかける
     （CC BY / CC0 / 政府標準利用規約 2.0 の明示があり、かつ「申請が必要」等が無いものだけ採用）

採用が 0 件なら、それは実測の結論であって作業の失敗ではない。ADR に残して打ち切る。

使い方:
    uv run python -m tools.probe_municipal_photos
    uv run python -m tools.probe_municipal_photos --limit-hosts 3
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from sitemill.assets.terms import fetch_terms
from sitemill.clock import jst_now
from sitemill.fetch.client import PoliteClient
from sitemill.fetch.links import extract_links
from sitemill.settings import Workspace

from japan_open_today.data import load_entries

PREF_TOP = "https://www.pref.kagawa.lg.jp/"
# 県のサイトで市町の一覧に辿り着くためのリンクの文字列
MUNICIPAL_INDEX = re.compile(r"県内市町|市町のホームページ|市町一覧|市町リンク|県内の市町")
# 市町のサイトのホスト（lg.jp と city./town. の形）
MUNICIPAL_HOST = re.compile(r"^(?:www\.)?(?:city|town)\.[^.]+\.(?:kagawa\.)?(?:lg\.)?jp$")
# 規約・素材のページらしいリンク
TERMS_LABEL = re.compile(
    r"著作権|利用規約|サイトポリシー|このサイトについて|免責|"
    r"フリー素材|写真素材|画像の利用|素材集|フォトライブラリ|写真提供|観光写真|photo",
    re.I,
)
# /adv/ は広告バナーの中継（丸亀市。転送が循環して取得できない）
SKIP_URL = re.compile(r"\.pdf$|\.jpe?g$|\.png$|/search|/cgi|/adv/", re.I)
SITEMAP_LABEL = re.compile(r"サイトマップ|サイトの使い方|このサイトの使い方")
MAX_TERMS_PAGES = 4


@dataclass
class HostResult:
    host: str
    name: str = ""
    top: str = ""
    pages: list[dict[str, str]] = field(default_factory=list)
    adopted: int = 0
    note: str = ""


def municipal_links(client: PoliteClient) -> dict[str, str]:
    """県のサイトから市町のリンクを辿る。{ホスト: URL}。"""
    found: dict[str, str] = {}
    res = client.get(PREF_TOP)
    if not res.ok:
        return found
    indexes: list[str] = []
    for link in extract_links(res.text, res.final_url):
        if any(MUNICIPAL_INDEX.search(label) for label in link.labelled()):
            indexes.append(link.url)
    for index_url in indexes[:3]:
        page = client.get(index_url)
        if not page.ok:
            continue
        for link in extract_links(page.text, page.final_url):
            host = urlparse(link.url).netloc.lower()
            if MUNICIPAL_HOST.search(host):
                found.setdefault(host, f"https://{host}/")
    return found


def hosts_from_data(ws: Workspace) -> dict[str, str]:
    """すでに一次情報として辿った URL に出てくる市町のホスト。"""
    out: dict[str, str] = {}
    for entry in load_entries(ws):
        urls = [entry.get("official_url", "")] + [p["url"] for p in (entry.get("pages") or [])]
        for url in urls:
            host = urlparse(url or "").netloc.lower()
            if MUNICIPAL_HOST.search(host):
                out.setdefault(host, f"https://{host}/")
    return out


def probe_host(client: PoliteClient, host: str, top: str) -> HostResult:
    out = HostResult(host=host, top=top)
    res = client.get(top)
    if not res.ok:
        res = client.get(top.replace("https://", "http://"))
        if not res.ok:
            out.note = f"トップを取得できない（status={res.status}）"
            return out
    candidates: list[tuple[str, str]] = []
    for link in extract_links(res.text, res.final_url):
        if urlparse(link.url).netloc.lower() != host or SKIP_URL.search(link.url):
            continue
        for label in link.labelled():
            if TERMS_LABEL.search(label):
                candidates.append((" ".join(label.split())[:30], link.url))
                break
    seen: set[str] = set()
    for label, url in candidates:
        if url in seen or len(out.pages) >= MAX_TERMS_PAGES:
            continue
        seen.add(url)
        verdict, error = fetch_terms(client, url, credit_name=host)
        if verdict is None:
            out.pages.append(
                {"label": label, "url": url, "result": "取得できない", "why": error or ""}
            )
            continue
        out.pages.append(
            {
                "label": label,
                "url": url,
                "result": "採用" if verdict.allowed else "不採用",
                "why": verdict.reason[:80],
            }
        )
        if verdict.allowed:
            out.adopted += 1
    if not candidates:
        # トップに無いサイトがある。サイトマップを 1 枚だけ辿ってもう一度探す
        # （「見つからなかった」の重みを、見た範囲とともに残すため）
        sitemap = next(
            (
                link.url
                for link in extract_links(res.text, res.final_url)
                if urlparse(link.url).netloc.lower() == host
                and any(SITEMAP_LABEL.search(label) for label in link.labelled())
            ),
            "",
        )
        if sitemap:
            page = client.get(sitemap)
            if page.ok:
                for link in extract_links(page.text, page.final_url):
                    if urlparse(link.url).netloc.lower() != host or SKIP_URL.search(link.url):
                        continue
                    for label in link.labelled():
                        if TERMS_LABEL.search(label):
                            verdict, error = fetch_terms(client, link.url, credit_name=host)
                            out.pages.append(
                                {
                                    "label": " ".join(label.split())[:30],
                                    "url": link.url,
                                    "result": "不採用" if verdict else "取得できない",
                                    "why": (verdict.reason[:80] if verdict else (error or "")),
                                    "found_via": "サイトマップ",
                                }
                            )
                            if verdict and verdict.allowed:
                                out.pages[-1]["result"] = "採用"
                                out.adopted += 1
                            break
                    if len(out.pages) >= MAX_TERMS_PAGES:
                        break
            out.note = "トップに無し。サイトマップから探した"
        else:
            out.note = "規約・素材らしいリンクが見つからない（トップとサイトマップ）"
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-hosts", type=int, default=0)
    parser.add_argument("--out", type=Path, default=Path("data/runs/municipal-photo-terms.json"))
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    ua = f"{ws.site.user_agent} photo terms probe"
    rows: list[HostResult] = []
    with PoliteClient(ua, default_delay=2.5, jitter=0.5, timeout=30.0) as client:
        hosts = municipal_links(client)
        print(f"== 県のリンク集から市町のサイト {len(hosts)} 件 ==")
        merged = dict(hosts)
        for host, top in hosts_from_data(ws).items():
            merged.setdefault(host, top)
        extra = len(merged) - len(hosts)
        if extra:
            print(f"   収録済みの情報源から {extra} 件を追加")
        # 県と観光協会も対象に入れる（S3 で見たが、記録を 1 つにまとめる）
        merged.setdefault("www.pref.kagawa.lg.jp", PREF_TOP)
        merged.setdefault("www.my-kagawa.jp", "https://www.my-kagawa.jp/")
        targets = sorted(merged.items())
        if args.limit_hosts:
            targets = targets[: args.limit_hosts]
        print(f"== 当たるサイト {len(targets)} 件 ==")
        for n, (host, top) in enumerate(targets, 1):
            row = probe_host(client, host, top)
            rows.append(row)
            mark = f"採用 {row.adopted}" if row.adopted else "採用なし"
            print(f"  {n:3}/{len(targets)} {host:34} 候補 {len(row.pages)} 件 / {mark}", flush=True)
            for page in row.pages:
                print(f"        {page['result']} {page['label'][:16]:18} {page['why'][:52]}")
            if row.note:
                print(f"        {row.note}")
        requests = client.request_count

    adopted = sum(r.adopted for r in rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "probed_at": jst_now().isoformat(),
                "requests": requests,
                "hosts": len(rows),
                "adopted": adopted,
                "rows": [asdict(r) for r in rows],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(f"\n採用 {adopted} 件 / サイト {len(rows)} 件（{requests} リクエスト）→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
