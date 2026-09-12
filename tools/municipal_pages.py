"""市町の観光ページを、屋外の場所の一次情報として足す（S6-3 ②。ADR 0011）。

屋外の場所（砂浜・境内・山上）は施設の公式サイトを持たないことが多い。一方で、**市町の
観光ページには開放時間や駐車場の案内が載っている**ことがある。金刀比羅宮の参拝時間が
公式サイトに無く観光協会の一覧にあったのと同じ形で、市町側にしか無い情報がある。

やること:
  1. 市町のサイトの入口を集める。**URL は推測しない**。すでに一次情報として辿った URL
     （`data/sources/kagawa.yaml` と数え上げの `official_url`）から、市町のホストだけを取る
  2. 各ホストの入口から「観光」の見出しを辿り、リンクの文字列と URL を索引にする（2 階層）
  3. 収録済みの施設名と索引を突き合わせ、一致したページを取得して**中身を確かめる**
     （その施設の名前があるか、時間・料金の記載があるか）
  4. 確かめられたものだけを seed にする

使い方:
    uv run python -m tools.municipal_pages            # 提案だけ
    uv run python -m tools.municipal_pages --apply
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

from selectolax.parser import HTMLParser
from sitemill.clock import jst_now
from sitemill.diff.normalize import page_text, squash
from sitemill.fetch.client import PoliteClient
from sitemill.fetch.links import extract_links
from sitemill.settings import Workspace

from japan_open_today.data import Dataset, load_entries

# 市町のサイトのホスト（lg.jp と city./town. の形。ADR 0009 の追記どおり推測はしない）
# **市町のサイトだけ**。`.lg.jp` で括ると県の施設（kmuseum.pref.kagawa.lg.jp）まで入り、
# 美術館の展示ページを屋島の情報源にしかけた
MUNICIPAL_HOST = re.compile(r"^(?:www\.)?(?:city|town)\.[^.]+\.(?:kagawa\.)?(?:lg\.)?jp$")
TOURISM_LABEL = re.compile(r"観光|見どころ|名所|自然|文化財|史跡|公園|スポット|施設案内|施設一覧")
# 索引に入れない（記事・申請・議会など）
SKIP_URL = re.compile(r"/(?:news|topics|koho|gikai|nyusatsu|shinsei|saiyo|boshu)/|\.pdf$", re.I)
HOURS = re.compile(r"\d{1,2}\s*[:時]\s*\d{0,2}|24\s*時間|終日|入[園場]自由|開放")
# 役所の窓口時間。市町のページのフッターにほぼ必ずあり、施設の時間と間違える
# （粟井神社のページで「開庁時間 月曜から金曜の午前8時30分から」を拾った）
OFFICE_HOURS = re.compile(r"開庁時間|執務時間|窓口(?:の)?時間|受付時間.{0,10}午前8時30分")
MAX_PER_HOST = 3  # 入口から辿る「観光」の索引の数


@dataclass
class Proposal:
    spot_id: str
    name: str
    url: str
    label: str
    kind: str = "spot_hours"
    verified: bool = False
    detail: str = ""


def municipal_hosts(ws: Workspace, inventory: dict) -> list[str]:
    """すでに辿った URL から市町のホストだけを集める（推測しない）。"""
    urls: list[str] = []
    for entry in load_entries(ws):
        urls.append(entry.get("official_url", ""))
        urls += [p["url"] for p in (entry.get("pages") or [])]
        urls.append((entry.get("operator_evidence") or {}).get("url", ""))
    urls += [c.get("official_url") or "" for c in inventory.get("candidates", [])]
    hosts: list[str] = []
    for url in urls:
        host = urlparse(url or "").netloc.lower()
        if host and MUNICIPAL_HOST.search(host) and host not in hosts:
            hosts.append(host)
    return hosts


def index_host(client: PoliteClient, host: str) -> dict[str, str]:
    """そのホストの「観光」まわりのリンクを {文字列: URL} にする。"""
    index: dict[str, str] = {}
    top = f"https://{host}/"
    res = client.get(top)
    if not res.ok:
        res = client.get(f"http://{host}/")
        if not res.ok:
            return index
    sections: list[str] = []
    for link in extract_links(res.text, res.final_url):
        if urlparse(link.url).netloc != host or SKIP_URL.search(link.url):
            continue
        for label in link.labelled():
            if TOURISM_LABEL.search(label):
                if link.url not in sections:
                    sections.append(link.url)
                index.setdefault(label.strip(), link.url)
    for section in sections[:MAX_PER_HOST]:
        page = client.get(section)
        if not page.ok:
            continue
        for link in extract_links(page.text, page.final_url):
            if urlparse(link.url).netloc != host or SKIP_URL.search(link.url):
                continue
            for label in link.labelled():
                label = label.strip()
                if 2 <= len(label) <= 40:
                    index.setdefault(label, link.url)
    return index


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=Path("data/runs/kagawa-inventory.json"))
    parser.add_argument("--out", type=Path, default=Path("data/runs/municipal-pages.json"))
    parser.add_argument("--apply", action="store_true", help="kagawa.yaml に足す")
    parser.add_argument("--limit-hosts", type=int, default=0)
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    ds = Dataset.load(ws)
    entries = {e["id"]: e for e in load_entries(ws)}
    seeded = {p["url"] for e in entries.values() for p in (e.get("pages") or [])}
    # 時間の取れていない屋外の場所と、時間の取れていない施設を対象にする
    targets = [s for s in ds.spots if not s.hours]
    print(f"== 営業時間が取れていない施設 {len(targets)} 件に、市町のページを当てる ==")

    hosts = municipal_hosts(ws, inventory)
    if args.limit_hosts:
        hosts = hosts[: args.limit_hosts]
    print(f"   市町のホスト {len(hosts)} 件: {', '.join(hosts[:8])}")

    proposals: list[Proposal] = []
    ua = f"{ws.site.user_agent} municipal pages"
    with PoliteClient(ua, default_delay=2.0, jitter=0.5, timeout=30.0) as client:
        index: dict[str, str] = {}
        for host in hosts:
            found = index_host(client, host)
            print(f"   {host}: リンク {len(found)} 件")
            for label, url in found.items():
                index.setdefault(label, url)
        for spot in targets:
            name = spot.name("ja")
            # ラベルは**その施設の名前と一致**することを求める。含むだけだと、
            # 「屋島」が美術館の展示名に含まれていて展示ページを掴む
            hit = next(
                (url for label, url in index.items() if squash(label) == squash(name)),
                None,
            )
            if hit is None or hit in seeded:
                continue
            proposal = Proposal(spot_id=spot.spot_id, name=name, url=hit, label=name)
            res = client.get(hit)
            if not res.ok:
                proposal.detail = f"取得できない（status={res.status}）"
            else:
                text = page_text(res.text)
                heading = HTMLParser(res.text).css_first("h1") or HTMLParser(res.text).css_first(
                    "title"
                )
                title = " ".join((heading.text() if heading else "").split())
                if squash(name) not in squash(title):
                    # 見出しがその施設のページであること。本文に名前があるだけでは、
                    # 別の対象のページ（展示・イベント）を掴む
                    proposal.detail = f"見出しが施設のページではない（{title[:40]}）"
                else:
                    found = None
                    for m in HOURS.finditer(text):
                        window = text[max(0, m.start() - 40) : m.end() + 40]
                        if OFFICE_HOURS.search(window):
                            continue  # 役所の窓口時間。施設の時間ではない
                        found = (m, " ".join(window.split()))
                        break
                    if found is None:
                        proposal.detail = "施設の時間の記載が見つからない（役所の窓口時間は除く）"
                    else:
                        proposal.verified = True
                        proposal.detail = found[1]
            proposals.append(proposal)
            mark = "OK " if proposal.verified else "NG "
            print(f"   {mark}{name[:18]:20} {hit[:56]}")
            print(f"       {proposal.detail[:90]}")
        requests = client.request_count

    verified = [p for p in proposals if p.verified]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "collected_at": jst_now().isoformat(),
                "requests": requests,
                "hosts": hosts,
                "proposals": [asdict(p) for p in proposals],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(f"\n候補 {len(proposals)} / 確認済み {len(verified)}（{requests} リクエスト）")
    if args.apply and verified:
        _apply(ws, ds, verified)
    return 0


def _apply(ws: Workspace, ds: Dataset, verified: list[Proposal]) -> None:
    """確かめたページを、その施設の情報源に足す（allow_hosts も広げる）。"""
    from tools.add_listing_hours import _extend_allow_hosts, _source_line

    source_of = {s.spot_id: s.source_id for s in ds.spots}
    path = ws.sources_dir / "kagawa.yaml"
    lines = path.read_text(encoding="utf-8").split("\n")
    by_source: dict[str, list[Proposal]] = {}
    for proposal in verified:
        source_id = source_of.get(proposal.spot_id)
        if source_id:
            by_source.setdefault(source_id, []).append(proposal)
    added = 0
    for source_id in sorted(by_source, key=lambda sid: -_source_line(lines, sid)):
        start = _source_line(lines, source_id)
        if start < 0:
            continue
        pages_at = next((i for i in range(start, len(lines)) if lines[i].strip() == "pages:"), None)
        if pages_at is None:
            continue
        end = pages_at + 1
        while end < len(lines) and lines[end].startswith("      "):
            end += 1
        block: list[str] = []
        for proposal in by_source[source_id]:
            block.append(f"      - url: {proposal.url}")
            block.append(
                f"        kind: {proposal.kind}  # 市町のページ（S6-3 ②）: {proposal.detail[:50]}"
            )
            added += 1
        lines[end:end] = block
        _extend_allow_hosts(lines, start, {urlparse(p.url).netloc for p in by_source[source_id]})
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"kagawa.yaml に {added} 件の seed を追加した")


if __name__ == "__main__":
    raise SystemExit(main())
