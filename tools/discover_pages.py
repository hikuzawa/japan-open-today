"""営業時間・料金・告知の下層ページを発見する（S6。ADR 0009）。

充足率がこのサービスの価値そのものなので、ここが一番効く。S5 では 12 施設のうち 5 件しか
営業時間が取れず、その原因の多くは「時間は下層ページにあるのに seed していない」ことだった。

やること:
  1. すでに巡回したページ（生 HTML のキャッシュ）からリンクを集める
  2. ラベルと URL の語で種別（spot_hours / spot_fees / spot_access / notice）に振り分ける
  3. **候補を取得して中身を確かめる**。時間の候補なら時刻の書き方が実際にあるか、
     料金なら金額があるか。無ければ捨てる
  4. 確かめられたものだけを提案として書き出し、`--apply` で kagawa.yaml に足す

「リンクのラベルがそれらしい」だけで seed にしない。ラベルだけで足すと、時間の載っていない
ページを毎日取りに行って、いつまでも unknown のままになる。

使い方:
    uv run python -m tools.discover_pages            # 提案だけ
    uv run python -m tools.discover_pages --apply    # kagawa.yaml に足す
"""

from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

from sitemill.clock import jst_now
from sitemill.diff.normalize import page_text
from sitemill.fetch.client import PoliteClient
from sitemill.fetch.links import extract_links
from sitemill.settings import Workspace
from sitemill.store.raw import RawCache

from japan_open_today.data import load_entries

# 種別ごとの、リンクのラベル・URL に出る語
KIND_KEYWORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "spot_hours",
        re.compile(
            r"開[園館場]時間|営業時間|開[園館場]日|利用案内|入[園館場]案内|ご利用案内|"
            r"開館情報|営業案内|hours|opening"
            # ロープウェイ・遊覧船が施設そのものの場合、時間は乗り物の名前と「時刻表」の側にある
            # （寒霞渓の開館時間は /ropeway/ の「時刻表」から辿る。第1フェーズの交通とは別で、
            #   ここでの対象はその施設に入るための唯一の乗り物である）
            r"|時刻表|運行時間|運転時間|運航時間|ropeway|cablecar|timetable"
        ),
    ),
    (
        "spot_fees",
        re.compile(r"入[園館場]料|観覧料|拝観料|料金|チケット|券|admission|fee|ticket|price"),
    ),
    ("notice", re.compile(r"お知らせ|新着|重要|ニュース|notice|news|topics|information")),
    ("spot_access", re.compile(r"アクセス|交通|行き方|access")),
)
# 中身の確認。ここに一致しなければ、その種別のページとして採らない
VERIFY: dict[str, re.Pattern[str]] = {
    # 「9:00〜17:00」「午前9時」「入園自由」のいずれか
    # 「10:00〜17:00」「午前9時から午後5時まで」「入園自由」。
    # 自治体サイトは午前・午後で書くことが多く、これを外すと時間のページを見落とす
    "spot_hours": re.compile(
        r"(?:午前|午後)?\s*\d{1,2}\s*[:時]\s*\d{0,2}\s*分?\s*(?:〜|~|-|‐|–|から)\s*"
        r"(?:午前|午後)?\s*\d{1,2}\s*[:時]"
        r"|入[園館場]自由|24\s*時間"
    ),
    "spot_fees": re.compile(r"\d[\d,]*\s*円|無料"),
    "notice": re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*日|\d{4}[/.-]\d{1,2}[/.-]\d{1,2}"),
    "spot_access": re.compile(r"分|徒歩|バス|電車|フェリー|車"),
}
# 個別の記事ページは seed にしない。索引ページだけを採る。
# 記事を毎日取りに行っても、その記事が古くなるだけで施設の時間は分からない。
ARTICLE_LIKE = re.compile(
    r"/\d{4}/\d{2}/|entry-\d+|post-\d+|/\d{4,}(?:\.html)?/?$|\.pdf$"
    r"|/articles?_?\d*/|article\.html$|/event/|/events/|/campaign"
    r"|/news/|/topics/|/oshirase|\d{4,}-\d+\.html"  # 個別のお知らせ記事
    r"|%[0-9a-f]{2}%[0-9a-f]{2}%[0-9a-f]{2}"  # 記事の題名を符号化した長い URL
    r"|/sp/",  # スマートフォン版の複製
    re.I,
)
# ラベルが似ているだけの無関係なページ。「ウェブアクセシビリティ方針」を
# アクセス案内として拾ってしまう類
LABEL_EXCLUDE = re.compile(r"アクセシビリティ|プライバシー|個人情報|サイトマップ|著作権|RSS")
# 「…のお知らせ」「…の変更」は告知。**通常の開館時間のページにはしない**。
# 臨時の時間変更を通常の開館時間として取り込むと、恒常的な時間を上書きしてしまう
# （ベネッセの「シルバーウィーク期間のアート施設開館時間変更のお知らせ」で実際に起きた）。
NOTICE_ONLY_LABEL = re.compile(r"お知らせ|変更|臨時|中止|delay|cancel")
# 施設の時間を語る言葉。時刻がこの近くに無ければ、その施設の開館時間ではない
HOURS_CONTEXT = re.compile(
    r"開[園館場]時間|営業時間|開[園館場]|入[園館場]|利用時間|受付時間"
    # 乗り物が施設そのものの場合の言い方
    r"|運行時間|運転時間|運航時間|始発|終発|上り|下り"
)
# 役所の窓口時間。自治体サイトのフッターにほぼ必ずあり、施設の時間と間違える
OFFICE_HOURS = re.compile(r"開庁時間|執務時間|窓口(?:の)?時間")
# 料金を語る言葉
FEE_CONTEXT = re.compile(r"入[園館場]料|観覧料|拝観料|利用料|料金|大人|小人|中学生|高校生")
CONTEXT_WINDOW = 60
MAX_CANDIDATES_PER_KIND = 2


class _Budget:
    """総リクエスト数の上限。上限 0 は無制限。

    情報源が増えると候補の取得だけで数百リクエストになるため、走らせる前に上限を置く。
    """

    def __init__(self, limit: int) -> None:
        # 「上限なし」と「使い切った」を同じ 0 で表すと区別できないので、旗を分けて持つ
        self._unlimited = limit <= 0
        self._left = limit
        self._lock = Lock()

    def take(self) -> bool:
        if self._unlimited:
            return True
        with self._lock:
            if self._left <= 0:
                return False
            self._left -= 1
            return True


@dataclass
class Proposal:
    source_id: str
    kind: str
    url: str
    label: str
    found_on: str
    verified: bool = False
    detail: str = ""


@dataclass
class SourceResult:
    source_id: str
    name: str
    existing_kinds: list[str] = field(default_factory=list)
    proposals: list[Proposal] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _hosts(entry: dict[str, Any]) -> set[str]:
    return set(entry.get("allow_hosts") or [])


def _candidates(
    entry: dict[str, Any], raw: RawCache, shared_urls: frozenset[str] = frozenset()
) -> list[tuple[str, str, str, str]]:
    """(kind, url, label, found_on) の候補。巡回済みの HTML からリンクを集める。

    `shared_urls` は他の情報源が既に seed している URL。1 枚で複数施設を扱うページなので、
    1 施設の seed には**しない**（ADR 0010）。ベネッセの /news/ を 3 施設に seed した結果、
    李禹煥美術館と家プロジェクトの休館が豊島美術館の休館として公開されていた。
    """
    hosts = _hosts(entry)
    have = {p["kind"] for p in entry.get("pages", []) or []}
    seen_urls = {p["url"] for p in entry.get("pages", []) or []}
    out: list[tuple[str, str, str, str]] = []
    per_kind: dict[str, int] = {}
    for page in entry.get("pages", []) or []:
        html = raw.load_text(entry["id"], page["url"])
        if not html:
            continue
        for link in extract_links(html, page["url"]):
            if hosts and not any(f"//{h}/" in link.url for h in hosts):
                continue
            if link.url in seen_urls or ARTICLE_LIKE.search(link.url):
                continue
            if link.url in shared_urls:
                continue  # 他の施設も使っているページ。共有ページとして別に扱う
            if any(LABEL_EXCLUDE.search(lb) for lb in link.labelled()):
                continue
            # 同じページが複数のラベルで張られていることがある。全部を手がかりにする
            haystack = " ".join((*link.labelled(), link.url.lower()))
            for kind, pattern in KIND_KEYWORDS:
                if kind in have or not pattern.search(haystack):
                    continue
                if per_kind.get(kind, 0) >= MAX_CANDIDATES_PER_KIND:
                    continue
                per_kind[kind] = per_kind.get(kind, 0) + 1
                seen_urls.add(link.url)
                label = next((lb for lb in link.labelled() if pattern.search(lb)), link.text)
                out.append((kind, link.url, label[:40], page["url"]))
                break
    return out


def _near(text: str, match: re.Match[str], context: re.Pattern[str]) -> str | None:
    """一致箇所の前後に、その種別を語る言葉があるか。あればその前後の文字列を返す。"""
    start = max(0, match.start() - CONTEXT_WINDOW)
    window = text[start : match.end() + CONTEXT_WINDOW]
    return " ".join(window.split()) if context.search(window) else None


# 中身で判定する順序。上にあるものが優先（時間が載っているなら時間のページとして扱う）
CONTENT_ORDER = ("spot_hours", "spot_fees", "notice", "spot_access")


def _content_kind(text: str) -> tuple[str | None, str]:
    """ページの**中身**から種別を決める。返り値は (種別, 根拠の抜粋)。

    ラベルは提案にすぎない。香川県立ミュージアムの利用案内は「休館日のお知らせ」という
    ラベルで張られていて、ラベルだけ見ると告知ページに分類される。中身は
    「利用案内 開館時間 休館日 観覧料」なので、開館時間のページとして扱うべき。
    """
    for kind in CONTENT_ORDER:
        pattern = VERIFY[kind]
        context = {"spot_hours": HOURS_CONTEXT, "spot_fees": FEE_CONTEXT}.get(kind)
        for match in pattern.finditer(text):
            if context is None:
                window = text[max(0, match.start() - 30) : match.end() + 50]
                return kind, " ".join(window.split())[:90]
            around = _near(text, match, context)
            if around is None:
                continue
            if kind == "spot_hours" and OFFICE_HOURS.search(around):
                continue  # 役所の開庁時間。施設の開館時間ではない
            return kind, around[:90]
    return None, ""


def verify(client: PoliteClient, proposal: Proposal, *, have: set[str]) -> None:
    """候補を取得し、中身から種別を決め直す。

    形（時刻・金額）だけでは足りない。自治体サイトのフッターには必ず「開庁時間 8:30〜17:15」が
    あり、形だけ見ると全ページが「開館時間あり」になる（香川県立ミュージアムで実際に起きた）。
    時刻の**近くに施設の時間を語る言葉がある**ことまで確かめる。
    """
    res = client.get(proposal.url)
    if not res.ok:
        proposal.detail = f"取得できない（status={res.status}）"
        return
    text = page_text(res.text)
    kind, detail = _content_kind(text)
    if kind is None:
        proposal.detail = f"どの種別の中身も見つからない（本文 {len(text)} 字）"
        return
    if NOTICE_ONLY_LABEL.search(proposal.label) and kind in ("spot_hours", "spot_fees"):
        # 告知の記事。通常の時間・料金のページとしては採らない
        proposal.detail = f"「{proposal.label[:20]}」は告知。通常の{kind}としては採らない"
        return
    if kind != proposal.kind:
        proposal.detail = f"ラベルは {proposal.kind} だが中身は {kind}: {detail}"
        proposal.kind = kind
    else:
        proposal.detail = detail
    if proposal.kind in have:
        proposal.detail = f"{proposal.kind} はすでに seed がある: {proposal.detail}"
        return
    proposal.verified = True


def apply_to_yaml(path: Path, proposals: list[Proposal]) -> int:
    """確かめられた提案を kagawa.yaml の pages に足す。

    YAML を読み込んで書き戻すと**コメントが消える**。このファイルのコメントは
    「なぜこの情報源をこう扱うか」（robots が取れない、共有ページを外した理由）を持っていて、
    消すと判断の理由が失われる。行として差し込む。
    """
    lines = path.read_text(encoding="utf-8").split(chr(10))
    by_source: dict[str, list[Proposal]] = {}
    for p in proposals:
        if p.verified:
            by_source.setdefault(p.source_id, []).append(p)
    added = 0
    # 後ろの source から入れる（前に入れると行番号がずれる）
    for source_id in sorted(by_source, key=lambda sid: -_source_line(lines, sid)):
        start_at = _source_line(lines, source_id)
        if start_at < 0:
            continue
        pages_at = next(
            (i for i in range(start_at, len(lines)) if lines[i].strip() == "pages:"), None
        )
        if pages_at is None:
            continue
        end_at = pages_at + 1
        while end_at < len(lines) and lines[end_at].startswith("      "):
            end_at += 1
        block: list[str] = []
        for p in by_source[source_id]:
            block.append(f"      - url: {p.url}")
            block.append(f"        kind: {p.kind}  # 発見（S6）: {p.label}")
            added += 1
        lines[end_at:end_at] = block
    if added:
        path.write_text(chr(10).join(lines), encoding="utf-8", newline=chr(10))
    return added


def _source_line(lines: list[str], source_id: str) -> int:
    for i, line in enumerate(lines):
        if line.strip() == f"- id: {source_id}":
            return i
    return -1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="kagawa.yaml に足す")
    parser.add_argument("--out", type=Path, default=Path("data/runs/page-discovery.json"))
    parser.add_argument("--only", default="", help="情報源 id をカンマ区切りで絞る")
    parser.add_argument("--workers", type=int, default=6, help="並列数（ホストごとの間隔は守る）")
    parser.add_argument("--max-requests", type=int, default=0, help="総リクエスト数の上限")
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    raw = RawCache(ws.raw_dir)
    all_entries = load_entries(ws)
    entries = [e for e in all_entries if e.get("policy") == "crawl" and e.get("spot")]
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        entries = [e for e in entries if e["id"] in wanted]
    results: list[SourceResult] = []
    budget = _Budget(max(0, args.max_requests))
    ua = f"{ws.site.user_agent} page discovery"
    with PoliteClient(ua, default_delay=3.0, jitter=1.0, timeout=30.0) as client:

        def run_entry(entry: dict[str, Any]) -> SourceResult:
            result = SourceResult(
                source_id=entry["id"],
                name=entry["name"],
                existing_kinds=sorted({p["kind"] for p in entry.get("pages", []) or []}),
            )
            have = set(result.existing_kinds)
            shared = frozenset(
                p["url"]
                for other in all_entries
                if other["id"] != entry["id"]
                for p in other.get("pages", []) or []
            )
            for kind, url, label, found_on in _candidates(entry, raw, shared):
                proposal = Proposal(
                    source_id=entry["id"], kind=kind, url=url, label=label, found_on=found_on
                )
                if not budget.take():
                    proposal.detail = "リクエスト上限に達したので確かめていない"
                    result.proposals.append(proposal)
                    break
                verify(client, proposal, have=have)
                if proposal.verified:
                    have.add(proposal.kind)  # 同じ種別を 2 つ足さない
                result.proposals.append(proposal)
            if not result.proposals:
                result.notes.append("候補のリンクが無い")
            ok = sum(1 for p in result.proposals if p.verified)
            print(
                f"{result.source_id:24} 既存 {','.join(result.existing_kinds) or '-':34}"
                f" 候補 {len(result.proposals)} / 確認済み {ok}",
                flush=True,
            )
            for p in result.proposals:
                mark = "OK " if p.verified else "NG "
                print(f"    {mark}{p.kind:12} {p.label[:20]:22} {p.url[:70]}")
                print(f"        {p.detail[:100]}")
            return result

        # 情報源ごとに並列化する。ホストごとの間隔は PoliteClient が守る（巡回と同じ考え方）
        if args.workers > 1 and len(entries) > 1:
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                results = list(pool.map(run_entry, entries))
        else:
            results = [run_entry(entry) for entry in entries]
        requests = client.request_count

    proposals = [p for r in results for p in r.proposals]
    verified = [p for p in proposals if p.verified]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "discovered_at": jst_now().isoformat(),
                "requests": requests,
                "results": [asdict(r) for r in results],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(f"\n候補 {len(proposals)} / 確認済み {len(verified)}（{requests} リクエスト）")
    if args.apply:
        added = apply_to_yaml(ws.sources_dir / "kagawa.yaml", proposals)
        print(f"kagawa.yaml に {added} 件の seed を追加した")
    else:
        print("--apply を付けると kagawa.yaml に足す")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
