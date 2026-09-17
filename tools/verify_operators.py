"""運営主体の根拠を、情報源ごとに調べ直す（docs/data-issues.md の 1）。

`operator_kind` が根拠なしで決まっていた情報源について、公式ページと、そこからリンクで辿れる
「会社概要・運営者情報・サイトについて」のページを読み、運営主体を名乗る記述を集める。
**URL は組み立てない**。辿るのはページ内のリンクだけ（同じホストに限る）。

集めるもの:
- 法人格つきの名前（公益財団法人〜、株式会社〜、宗教法人〜 など）と、その前後の文
- 「運営: 〜」「指定管理者: 〜」「管理: 〜」の明記
- 著作権表記（Copyright / ©）
- 会社概要の表の「社名・名称・団体名・運営」の行

判定はこの道具ではしない。集めた材料を `data/runs/operator-verification.json` に残し、
人（またはこのセッション）が 1 件ずつ読んで決める。

使い方:
    uv run python -m tools.verify_operators --ids kagawa-mitoyo-kanko,kagawa-88shikokuhenro
    uv run python -m tools.verify_operators --weak-facility-official
    uv run python -m tools.verify_operators --kind municipality
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser
from sitemill.clock import jst_now
from sitemill.fetch.client import PoliteClient
from sitemill.settings import Workspace

from japan_open_today.data import load_entries
from tools.seed_spots import LEGAL_FORM, MUNICIPAL_SELF

ABOUT = re.compile(
    r"会社概要|会社案内|運営者|運営会社|運営団体|運営について|サイトについて|このサイトについて|"
    r"団体概要|法人概要|組織概要|概要|協会について|私たちについて|アクセス・お問い合わせ|"
    r"お問い合わせ|about|company|profile|organization|corporate",
    re.I,
)
STATEMENTS = (
    (
        "法人名",
        re.compile(r"[^。\n]{0,20}" + LEGAL_FORM + r"\s*[^\s、。，,（()「」]{2,30}[^。\n]{0,20}"),
    ),
    (
        "運営の明記",
        re.compile(r"(?:運営|指定管理者|管理)(?:者|主体|会社|団体|元)?\s*[：:は]\s*[^。\n]{2,60}"),
    ),
    ("著作権表記", re.compile(r"(?:Copyright|©|\(C\))[^。\n]{0,80}", re.I)),
    (
        "表の名称",
        re.compile(
            r"(?:社名|会社名|法人名|団体名|名称|運営)\s*[：:]?\s*" + LEGAL_FORM + r"[^\s、。]{2,30}"
        ),
    ),
    ("自治体", MUNICIPAL_SELF),
)
WEAK = re.compile(
    LEGAL_FORM + r"|指定管理者|観光協会|観光交流局|観光振興|市役所|町役場|県庁|[市町村]$"
)


def _text(html: str) -> str:
    """ページ全体の文字列。運営主体の名乗りはフッターにあることが多く、本文だけでは落ちる。"""
    body = HTMLParser(html).body
    return " ".join((body.text(separator=" ") if body else "").split())


def _statements(text: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for label, pattern in STATEMENTS:
        for m in pattern.finditer(text):
            quote = " ".join(m.group(0).split())[:140]
            if quote not in seen:
                seen.add(quote)
                found.append({"kind": label, "quote": quote})
            if len(found) >= 12:
                return found
    return found


def _about_links(html: str, base: str) -> list[tuple[str, str]]:
    host = urlparse(base).netloc
    out: list[tuple[str, str]] = []
    for a in HTMLParser(html).css("a"):
        label = " ".join((a.text() or "").split())
        href = (a.attributes.get("href") or "").split("#")[0]
        if not href or not ABOUT.search(label + " " + href):
            continue
        url = urljoin(base, href)
        if urlparse(url).netloc != host or url == base:
            continue
        if (label, url) not in out:
            out.append((label, url))
    return out[:4]


def examine(client: PoliteClient, entry: dict[str, Any]) -> dict[str, Any]:
    official = entry["official_url"]
    evidence = (entry.get("operator_evidence") or {}).get("url") or ""
    row: dict[str, Any] = {
        "id": entry["id"],
        "name": entry["name"],
        "operator": entry.get("operator", ""),
        "operator_kind": entry.get("operator_kind", ""),
        "official_url": official,
        "old_evidence": entry.get("operator_evidence") or {},
        "pages": [],
    }
    visited: set[str] = set()
    queue: list[tuple[str, str]] = [("公式ページ", official)]
    if evidence and evidence != official:
        queue.append(("これまでの根拠のページ", evidence))
    while queue and len(row["pages"]) < 6:
        label, url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        res = client.get(url)
        page: dict[str, Any] = {"label": label, "url": url, "status": res.status}
        if not res.ok:
            page["error"] = res.error or f"HTTP {res.status}"
            row["pages"].append(page)
            continue
        page["final_url"] = res.final_url
        title = HTMLParser(res.text).css_first("title")
        page["title"] = " ".join((title.text() if title else "").split())[:120]
        text = _text(res.text)
        page["statements"] = _statements(text)
        page["tail"] = text[-240:]
        row["pages"].append(page)
        if label == "公式ページ":
            queue.extend(_about_links(res.text, res.final_url))
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", default="", help="調べる情報源の id（カンマ区切り）")
    parser.add_argument("--kind", default="", help="この operator_kind の情報源をすべて調べる")
    parser.add_argument(
        "--weak-facility-official",
        action="store_true",
        help="facility_official のうち、引用にも名前にも運営主体らしい語が無いものを調べる",
    )
    parser.add_argument("--out", type=Path, default=Path("data/runs/operator-verification.json"))
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    entries = load_entries(ws)
    wanted = {i.strip() for i in args.ids.split(",") if i.strip()}
    targets = []
    for entry in entries:
        quote = (entry.get("operator_evidence") or {}).get("quote") or ""
        if entry["id"] in wanted:
            targets.append(entry)
        elif args.kind and entry.get("operator_kind") == args.kind:
            targets.append(entry)
        elif (
            args.weak_facility_official
            and entry.get("operator_kind") == "facility_official"
            and not WEAK.search(quote)
            and not WEAK.search(entry.get("operator") or "")
        ):
            targets.append(entry)
    print(f"== 調べる情報源 {len(targets)} 件 ==")

    rows = []
    ua = f"{ws.site.user_agent} operator verification"
    with PoliteClient(ua, default_delay=3.0, jitter=1.0, timeout=30.0) as client:
        for n, entry in enumerate(targets, 1):
            row = examine(client, entry)
            rows.append(row)
            got = sum(len(p.get("statements", [])) for p in row["pages"])
            print(
                f"  {n:2}/{len(targets)} {entry['id']:32} ページ {len(row['pages'])} / 記述 {got}"
            )
        requests = client.request_count
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {"checked_at": jst_now().isoformat(), "requests": requests, "sources": rows},
            ensure_ascii=False,
            indent=2,
            default=str,  # yaml の checked_on は date で来る
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(f"→ {args.out}（{requests} リクエスト）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
