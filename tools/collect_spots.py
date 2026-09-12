"""香川全域の施設を、公的な一覧から数え上げて型を判定する（S6-3 ①。ADR 0009・0011）。

一次情報は公益社団法人香川県観光協会の「スポット・体験」一覧（my-kagawa.jp）。この一覧は
検索が GET で引けて、各スポットのページが同じ構造（基本情報: 住所・電話・営業時間・定休日・
料金・ウェブサイト）を持つため、**数え上げと型の判定**に使える。

やること:
  1. 一覧をエリアごとに辿って (id, 名前, 詳細 URL) を集める（`--enumerate`）
  2. 詳細ページを取得し、基本情報から型を決める（`--classify`）
     - 営業時間か料金の記載がある → `gated`（ゲートのある施設）
     - どちらも無い → `open_air`（屋外の場所）
     - 対象外（うどん店・直売所・体験・宿。ADR 0011）は `skip` として理由を残す
  3. 公式サイトへのリンクを**辿って**確定する（URL を組み立てない。ADR 0009 追記）

ここは候補を出すだけで、`data/sources/kagawa.yaml` に書くかは別の工程で決める。

使い方:
    uv run python -m tools.collect_spots --enumerate
    uv run python -m tools.collect_spots --classify --limit 40
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser
from sitemill.clock import jst_now
from sitemill.diff.normalize import page_text
from sitemill.fetch.client import PoliteClient
from sitemill.settings import Workspace

BASE = "https://www.my-kagawa.jp"
# 検索は GET で引ける。ページ送り（/attraction/digest/list/page:2/...）は 404 を返すため、
# **エリア × 大分類の組み合わせ**で 1 ページに収まる大きさに割って数え上げる
LIST_URL = (
    BASE + "/point/list?sortType={sort}&listStyle=list&area_l%5B%5D={area}"
    "&category_m%5B%5D={category}"
)
# 1 回の検索で返るのは 20 件。並び順を変えると別の 20 件が見えるので、両方を取る
SORTS = ("access", "name")
AREAS = {1: "高松市周辺", 2: "香川県東部", 3: "香川県中部", 4: "香川県西部", 5: "島"}
CATEGORIES = (14, 15, 16, 17, 49, 50)  # 検索フォームの大分類
RESULTS = ".searchResult"  # 検索結果のブロック（代表スポットのリンクを混ぜない）

# 第 1 フェーズの対象外（ADR 0011）。理由を残す
SKIP_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("restaurant", re.compile(r"うどん|そば|ラーメン|レストラン|食堂|カフェ|喫茶|居酒屋|酒蔵")),
    ("shop", re.compile(r"直売所|物産|土産|売店|ショップ|マーケット|商店")),
    ("experience", re.compile(r"体験|教室|ツアー|工房見学|クルーズ|レンタサイクル")),
    ("lodging", re.compile(r"ホテル|旅館|民宿|ゲストハウス|コテージ|キャンプ場|宿泊")),
    ("event", re.compile(r"まつり|祭り|フェス|花火|イベント")),
)
# 基本情報の見出し
INFO_KEYS = ("住所", "電話番号", "営業時間", "定休日", "料金", "アクセス", "駐車場")
FEE_AMOUNT = re.compile(r"\d[\d,]*\s*円|無料")
CLOCK = re.compile(r"\d{1,2}\s*[:時]\s*\d{0,2}|24\s*時間|終日")


@dataclass
class Candidate:
    point_id: str
    name: str
    url: str
    area: str
    spot_type: str = ""  # gated / open_air / skip / unknown
    reason: str = ""
    info: dict[str, str] = field(default_factory=dict)
    official_url: str | None = None


def _entries(html: str, base: str) -> list[tuple[str, str, str]]:
    """検索結果のブロックから (id, 名前, URL)。名前はリンクの文字列（最初の行）。

    ページのどこにでも「代表スポット」へのリンクがあるため、結果のブロックに限る。
    限らないと、どの検索でも同じ 10 件ほどが混ざる。
    """
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    tree = HTMLParser(html)
    block = tree.css_first(RESULTS)
    root = block or tree
    for a in root.css("a"):
        href = a.attributes.get("href") or ""
        m = re.search(r"/point/(\d+)", href)
        if m is None or m.group(1) in seen:
            continue
        text = " ".join((a.text() or "").split())
        if not text:
            continue
        name = text.split(" ")[0]
        seen.add(m.group(1))
        out.append((m.group(1), name, urljoin(base, href.split("#")[0])))
    return out


def enumerate_spots(client: PoliteClient) -> tuple[list[Candidate], list[str]]:
    """エリア × 大分類で数え上げる。返り値は (候補, 取りきれなかった組み合わせ)。"""
    found: dict[str, Candidate] = {}
    partial: list[str] = []
    for area, area_name in AREAS.items():
        for category in CATEGORIES:
            total = 0
            got: set[str] = set()
            for sort in SORTS:
                r = client.get(LIST_URL.format(area=area, category=category, sort=sort))
                if not r.ok:
                    print(f"  {area_name}/{category}/{sort}: 取得できない（status={r.status}）")
                    continue
                reported = re.search(r"([\d,]+)\s*件ありました", page_text(r.text))
                total = max(total, int(reported.group(1).replace(",", "")) if reported else 0)
                for pid, name, url in _entries(r.text, BASE):
                    got.add(pid)
                    if pid not in found:
                        found[pid] = Candidate(point_id=pid, name=name, url=url, area=area_name)
            if total > len(got):
                # 取りきれていないことを記録する（推測で埋めない）
                partial.append(f"{area_name}/大分類{category}: {total} 件のうち {len(got)} 件")
            print(f"  {area_name}/大分類{category}: {total:4} 件 → {len(got):3} 件取得")
    return list(found.values()), partial


def _info_table(text: str) -> dict[str, str]:
    """「基本情報 住所 … 電話番号 … 営業時間 …」を項目ごとに切る。"""
    body = text.split("基本情報", 1)[-1].split("周辺観光情報", 1)[0]
    positions: list[tuple[int, str]] = []
    for key in INFO_KEYS:
        i = body.find(key)
        if i >= 0:
            positions.append((i, key))
    positions.sort()
    out: dict[str, str] = {}
    for n, (start, key) in enumerate(positions):
        end = positions[n + 1][0] if n + 1 < len(positions) else len(body)
        out[key] = " ".join(body[start + len(key) : end].split())[:200]
    return out


def _skip_reason(name: str, text: str) -> str | None:
    head = name + " " + text[:400]
    for reason, pattern in SKIP_RULES:
        if pattern.search(head):
            return reason
    return None


def classify(client: PoliteClient, cand: Candidate) -> None:
    r = client.get(cand.url)
    if not r.ok:
        cand.spot_type = "unknown"
        cand.reason = f"取得できない（status={r.status}）"
        return
    text = " ".join(page_text(r.text).split())
    if cand.name and cand.name not in text:
        # 一覧の名前がページに無い。別の施設のページを指している（ADR 0009 追記）
        cand.spot_type = "unknown"
        cand.reason = "ページに一覧の名前が出てこない"
        return
    cand.info = _info_table(text)
    for a in HTMLParser(r.text).css("a"):
        if "公式" in " ".join((a.text() or "").split()):
            href = a.attributes.get("href") or ""
            if href.startswith("http") and "my-kagawa.jp" not in href:
                cand.official_url = href
                break
    skip = _skip_reason(cand.name, text)
    if skip:
        cand.spot_type = "skip"
        cand.reason = skip
        return
    hours = cand.info.get("営業時間", "")
    fee = cand.info.get("料金", "")
    if CLOCK.search(hours) or FEE_AMOUNT.search(fee):
        cand.spot_type = "gated"
        cand.reason = f"営業時間/料金の記載あり: {(hours or fee)[:60]}"
    else:
        cand.spot_type = "open_air"
        cand.reason = "営業時間・料金の記載が無い"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enumerate", action="store_true", help="一覧を数え上げる")
    parser.add_argument("--classify", action="store_true", help="詳細ページで型を決める")
    parser.add_argument("--limit", type=int, default=0, help="型の判定を先頭 N 件に絞る")
    parser.add_argument("--out", type=Path, default=Path("data/runs/kagawa-inventory.json"))
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    stored: dict[str, Any] = {}
    if args.out.is_file():
        stored = json.loads(args.out.read_text(encoding="utf-8"))
    cands = [Candidate(**row) for row in stored.get("candidates", [])]

    ua = f"{ws.site.user_agent} spot inventory"
    with PoliteClient(ua, default_delay=3.0, jitter=1.0, timeout=30.0) as client:
        partial: list[str] = list(stored.get("partial", []))
        if args.enumerate or not cands:
            print("== 一覧の数え上げ ==")
            cands, partial = enumerate_spots(client)
            print(f"  合計 {len(cands)} 件（重複を除く）")
            for line in partial:
                print(f"  ! 取りきれず: {line}")
        if args.classify:
            todo = [c for c in cands if not c.spot_type]
            if args.limit:
                todo = todo[: args.limit]
            print(f"== 型の判定（{len(todo)} 件）==")
            for n, cand in enumerate(todo, 1):
                classify(client, cand)
                print(
                    f"  {n:3}/{len(todo)} {cand.spot_type:9} {cand.name[:22]:24} {cand.reason[:50]}"
                )
        requests = client.request_count

    by_type: dict[str, int] = {}
    for cand in cands:
        by_type[cand.spot_type or "未判定"] = by_type.get(cand.spot_type or "未判定", 0) + 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "collected_at": jst_now().isoformat(),
                "requests": requests,
                "by_type": by_type,
                "partial": partial,
                "candidates": [asdict(c) for c in cands],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    print("\n== 型ごとの件数 ==")
    for key, n in sorted(by_type.items(), key=lambda kv: -kv[1]):
        print(f"  {key:9} {n:4}")
    print(f"（{requests} リクエスト、{args.out}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
