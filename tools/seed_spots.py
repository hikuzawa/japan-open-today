"""数え上げた施設を `data/sources/kagawa.yaml` の情報源にする（S6-3 ①。ADR 0009・0011）。

`tools.collect_spots` が作った `data/runs/kagawa-inventory.json` の `gated`（ゲートのある施設）を
受け取り、1 件ずつ次を確かめてから情報源の候補にする。

  1. **一次情報の URL を辿って確定する**（観光協会のページの「公式サイト」リンク）。
     URL を組み立てない。第三者サイト（じゃらん・SNS など）は一次情報にしない（ADR 0001）
  2. 取得できるか（robots.txt が拒否していないか）
  3. **そのページに施設の名前が出てくるか**（別の施設の URL を掴んでいないか。ADR 0009 追記）
  4. 運営主体の根拠（Copyright・運営・指定管理者・法人名）が取れるか。取れなければ
     会社概要・運営者情報のページを 1 枚だけ辿る。それでも取れなければ `policy: pending`
     としてレビュー行列に回す（ADR 0009）
  5. 住所からエリアを決める（決まらなければ候補から外す。推測しない）

`spot_id` は**運営者自身のローマ字表記**（公式サイトのホスト名・パス）から作る。ローマ字名を
こちらで考えて付けることはしない（ADR 0005）。公式サイトが無い施設は観光協会のページを
一次情報にし、`p<観光協会の id>` を使う。

使い方:
    uv run python -m tools.seed_spots --limit 20
    uv run python -m tools.seed_spots --apply
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser
from sitemill.clock import jst_now
from sitemill.diff.normalize import page_text
from sitemill.fetch.client import PoliteClient
from sitemill.settings import Workspace

from japan_open_today.areas import area_for_address
from japan_open_today.data import load_entries

# 第三者のサイト。一次情報にしない（ADR 0001）
THIRD_PARTY = re.compile(
    r"(?:^|\.)(?:jalan|rurubu|tripadvisor|instagram|facebook|twitter|x|youtube|booking|"
    r"agoda|klook|asoview|tabiiro|navitime|jorudan|google|amazon|rakuten)\.",
    re.I,
)
# 運営主体の根拠になりやすい記述（tools.collect_sources と同じ考え方）
OPERATOR_PATTERNS = (
    re.compile(r"指定管理者[^。\n]{0,60}"),
    re.compile(r"運営[：:\s][^。\n]{0,60}"),
    re.compile(
        r"(?:公益財団法人|一般財団法人|公益社団法人|一般社団法人|株式会社|有限会社|"
        r"特定非営利活動法人)\s*[^\s、。（()]{2,30}"
    ),
    re.compile(r"(?:Copyright|©|\(C\))[^。\n]{0,80}", re.I),
    re.compile(r"(?:主催|事務局|管理)[：:\s][^。\n]{0,60}"),
)
ABOUT_LINK = re.compile(r"会社概要|運営|概要|について|組織|財団|about|company|profile")
# 上から順に見る。「公園」は「園」で終わるので、庭園より先に park を見る
CATEGORY_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("museum", re.compile(r"美術館|博物館|資料館|記念館|展示館|水族館|文書館|ミュージアム")),
    ("shrine_temple", re.compile(r"神社|神宮|大社|寺$|寺院|観音$|宮$|札所")),
    ("castle", re.compile(r"城$|城跡|城址|陣屋")),
    ("park", re.compile(r"公園|広場|ビーチ|浜$|湖$|池$|滝$")),
    ("garden", re.compile(r"庭園|植物園|花園")),
    ("onsen", re.compile(r"温泉|湯$|スパ")),
    ("viewpoint", re.compile(r"展望|タワー|山$|峠|岬|灯台|ロープウェイ")),
)
# ホストの種類から運営主体の種別を当てる（引用が取れたときだけ使う）
# 市町のサイトは lg.jp のほかに city.<市>.kagawa.jp / town.<町>.kagawa.jp の形もある
# （高松市は city.takamatsu.kagawa.jp、琴平町は town.kotohira.kagawa.jp）
KIND_BY_HOST = (
    (re.compile(r"pref\.kagawa(?:\.lg)?\.jp$"), "prefecture"),
    (re.compile(r"(?:^|\.)(?:city|town)\.[^.]+\.(?:kagawa\.)?(?:lg\.)?jp$"), "municipality"),
    (re.compile(r"\.lg\.jp$"), "municipality"),
    (re.compile(r"my-kagawa\.jp$"), "tourism_association"),
)


@dataclass
class Seed:
    point_id: str
    name: str
    source_url: str
    official_url: str
    area: str = ""
    category: str = "other"
    spot_id: str = ""
    operator: str = ""
    operator_kind: str = "unknown"
    operator_quote: str = ""
    operator_evidence_url: str = ""
    policy: str = "crawl"
    note: str = ""
    ok: bool = False
    info: dict[str, str] = field(default_factory=dict)


def _plain(html: str) -> str:
    return " ".join(page_text(html).split())


# 借りているホスティングのホスト名は施設を表さない（r.goope.jp/new-yashima-aq など）。
# この場合はパスの先頭を使う
SHARED_HOSTS = re.compile(
    r"(?:goope|jimdo(?:free)?|wixsite|shopinfo|hp\.gogo|webnode|weebly|"
    r"wordpress|blogspot|amebaownd|hatenablog|fc2|sakura\.ne|my-kagawa)"
)


def _slug_from(url: str, point_id: str) -> str:
    """運営者自身のローマ字表記から識別子を作る（こちらで訳さない。ADR 0005）。"""
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    label = host.split(".")[0]
    path = [seg for seg in parsed.path.split("/") if seg and not seg.isdigit()]
    if SHARED_HOSTS.search(host) or len(label) < 3 or label in ("web", "home", "info"):
        first = path[0] if path else ""
        # 「point」「spot」のような一般語は施設を表さない。観光協会の id を使う
        label = f"p{point_id}" if first in ("", "point", "spot", "attraction") else first
    label = re.sub(r"[^a-z0-9-]+", "-", label.lower()).strip("-")[:40]
    return label or f"p{point_id}"


def _category(name: str) -> str:
    for category, pattern in CATEGORY_RULES:
        if pattern.search(name):
            return category
    return "other"


def _operator_quote(text: str) -> str:
    for pattern in OPERATOR_PATTERNS:
        m = pattern.search(text)
        if m is not None:
            return " ".join(m.group(0).split())[:120]
    return ""


def _operator_kind(host: str, quote: str) -> str:
    for pattern, kind in KIND_BY_HOST:
        if pattern.search(host):
            return kind
    if re.search(r"観光協会|観光交流局|観光振興", quote):
        return "tourism_association"
    if re.search(r"指定管理者", quote):
        return "municipality_affiliated"
    return "facility_official"


def _operator_name(quote: str) -> str:
    """引用から運営者の名前を切り出す。取れなければ引用をそのまま使う。"""
    m = re.search(
        r"(?:公益財団法人|一般財団法人|公益社団法人|一般社団法人|株式会社|有限会社|"
        r"特定非営利活動法人)\s*[^\s、。（()]{2,30}",
        quote,
    )
    if m is not None:
        return " ".join(m.group(0).split())
    m = re.search(r"(?:指定管理者|運営|管理)[：:\s]+([^\s、。（()]{2,30})", quote)
    if m is not None:
        return m.group(1)
    return quote[:60]


def build_seed(client: PoliteClient, cand: dict[str, Any]) -> Seed:
    official = (cand.get("official_url") or "").strip()
    if official and THIRD_PARTY.search(urlparse(official).netloc):
        official = ""  # 第三者サイトは一次情報にしない
    source_url = official or cand["url"]
    seed = Seed(
        point_id=cand["point_id"],
        name=cand["name"],
        source_url=source_url,
        official_url=official or cand["url"],
        info=cand.get("info") or {},
    )
    address = seed.info.get("住所", "")
    area = area_for_address(address)
    if area is None:
        seed.note = f"住所からエリアを決められない（{address[:40]}）"
        return seed
    seed.area = area
    seed.category = _category(seed.name)
    seed.spot_id = _slug_from(source_url, seed.point_id)

    res = client.get(source_url)
    if res.blocked:
        seed.policy = "link_only"
        seed.note = f"robots.txt が拒否している（{res.error}）"
        return seed
    if not res.ok:
        seed.note = f"取得できない（status={res.status}）"
        return seed
    text = _plain(res.text)
    if seed.name not in text:
        # 別の施設のページを掴んでいる。屋島に温泉の URL を当てた事故と同じ形
        seed.note = f"ページに「{seed.name}」が出てこない"
        return seed
    host = urlparse(res.final_url).netloc
    quote = _operator_quote(text)
    evidence_url = res.final_url
    if not quote:
        # 2 段目: 会社概要・運営者情報のページを 1 枚だけ辿る
        for a in HTMLParser(res.text).css("a"):
            label = " ".join((a.text() or "").split())
            href = a.attributes.get("href") or ""
            if not href or not ABOUT_LINK.search(label + href):
                continue
            about = urljoin(res.final_url, href.split("#")[0])
            if urlparse(about).netloc != host:
                continue
            r2 = client.get(about)
            if r2.ok:
                quote = _operator_quote(_plain(r2.text))
                if quote:
                    evidence_url = r2.final_url
                    break
    if not quote:
        seed.policy = "pending"
        seed.note = "運営主体の根拠が取れない（レビュー行列）"
        return seed
    seed.operator_quote = quote
    seed.operator_evidence_url = evidence_url
    seed.operator_kind = _operator_kind(host, quote)
    seed.operator = _operator_name(quote)
    seed.ok = True
    return seed


def _map_query(seed: Seed) -> str:
    """地図の検索語。郵便番号は落として「市町＋施設名」にする。"""
    address = seed.info.get("住所", "")
    address = re.sub(r"〒?\d{3}-?\d{4}", "", address).strip()
    city = re.search(r"香川県[^\s0-9０-９]{2,8}?[市町]", address)
    return f"{city.group(0) if city else '香川県'} {seed.name}".strip()


def _yaml_block(seed: Seed, source_id: str) -> str:
    q = seed.operator_quote.replace('"', "'")
    lines = [
        f"  - id: {source_id}",
        f"    name: {seed.name}",
        f"    operator: {seed.operator}",
        f"    operator_kind: {seed.operator_kind}",
        "    operator_evidence:",
        f'      quote: "{q}"',
        f"      url: {seed.operator_evidence_url}",
        f"      checked_on: {jst_now().date().isoformat()}",
        f"    policy: {seed.policy}",
        f"    official_url: {seed.source_url}",
        "    pages:",
        f"      - url: {seed.source_url}",
        "        kind: spot_detail",
        f"    allow_hosts: [{urlparse(seed.source_url).netloc}]",
        "    max_pages: 8",
        "    spot:",
        f"      spot_id: {seed.spot_id}",
        "      spot_type: gated  # 営業時間または料金の記載あり（観光協会の一覧）",
        f"      area: {seed.area}",
        f"      category: {seed.category}",
        "      names:",
        f"        ja: {{text: {seed.name}, source: ja}}",
        f"      map_query: {_map_query(seed)}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=Path("data/runs/kagawa-inventory.json"))
    parser.add_argument("--out", type=Path, default=Path("data/runs/spot-seeds.json"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--apply", action="store_true", help="kagawa.yaml に追記する")
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    have_ids = {e["id"] for e in load_entries(ws)}
    have_hosts = {h for e in load_entries(ws) for h in (e.get("allow_hosts") or [])}

    todo = [c for c in inventory["candidates"] if c.get("spot_type") == "gated"]
    if args.limit:
        todo = todo[: args.limit]
    print(f"== ゲートのある施設 {len(todo)} 件を情報源にする ==")

    seeds: list[Seed] = []
    ua = f"{ws.site.user_agent} spot seeding"
    with PoliteClient(ua, default_delay=3.0, jitter=1.0, timeout=30.0) as client:
        for n, cand in enumerate(todo, 1):
            seed = build_seed(client, cand)
            host = urlparse(seed.source_url).netloc
            if host in have_hosts:
                seed.ok = False
                seed.note = f"すでに収録済みのホスト（{host}）"
            seeds.append(seed)
            mark = "OK " if seed.ok else "NG "
            print(f"  {n:3}/{len(todo)} {mark}{seed.name[:20]:22} {seed.area:14} {seed.note[:44]}")
        requests = client.request_count

    ok = [s for s in seeds if s.ok]
    pending = [s for s in seeds if s.policy == "pending"]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "built_at": jst_now().isoformat(),
                "requests": requests,
                "ok": len(ok),
                "pending": len(pending),
                "seeds": [asdict(s) for s in seeds],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(f"\n採用 {len(ok)} 件 / レビュー待ち {len(pending)} 件 / {requests} リクエスト")

    if args.apply and ok:
        path = ws.sources_dir / "kagawa.yaml"
        text = path.read_text(encoding="utf-8")
        blocks = ["", "  # --- S6-3 ①: ゲートのある施設（観光協会の一覧から。ADR 0011）"]
        used = set(have_ids)
        for seed in ok:
            source_id = f"kagawa-{seed.spot_id}"
            suffix = 2
            while source_id in used:
                source_id = f"kagawa-{seed.spot_id}-{suffix}"
                suffix += 1
            used.add(source_id)
            blocks.append(_yaml_block(seed, source_id))
        path.write_text(text.rstrip() + "\n" + "\n".join(blocks), encoding="utf-8")
        print(f"kagawa.yaml に {len(ok)} 件を追記した")
    elif args.apply:
        print("追記するものが無い")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
