"""施設の公式サイトから、運営主体の根拠と巡回する seed ページの候補を集める（S4。ADR 0009）。

集めるもの:
  - 運営主体の根拠になる記述（Copyright / 運営 / 指定管理者 / 事務局）の引用
  - 巡回対象の候補リンク（営業時間・料金・アクセス・お知らせ）と、その種別
  - 公式 SNS へのリンク（経路③は保留だが、施設ページからリンクは置く）

ここは**候補を出すだけ**。`data/sources/kagawa.yaml` に何を書くかは人（と AI）が確認して決める。
運営主体が判定できないものはレビュー行列に回す（ADR 0009）。

使い方: uv run python -m tools.collect_sources [--out data/runs/source-candidates.json]
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

from sitemill.assets.terms import fetch_terms
from sitemill.clock import jst_now
from sitemill.fetch.client import PoliteClient
from sitemill.settings import Workspace

from tools.probe_assets import SNS_PATTERNS, SPOTS

# 運営主体の根拠になりやすい記述
OPERATOR_PATTERNS = (
    re.compile(r"(?:Copyright|©|\(C\))[^。\n]{0,80}", re.I),
    re.compile(r"指定管理者[^。\n]{0,60}"),
    re.compile(r"運営[：:\s][^。\n]{0,60}"),
    # 法人名。「公益財団法人 福武財団」のように**空白が入る**書き方があるので、
    # 法人格のあとの空白を許す（許さないと一致に失敗して「根拠なし」と誤判定する）
    re.compile(
        r"(?:公益財団法人|一般財団法人|公益社団法人|一般社団法人|株式会社)\s*[^\s、。（()]{2,30}"
    ),
    re.compile(r"(?:主催|事務局|管理)[：:\s][^。\n]{0,60}"),
)
# 巡回する seed ページの候補。ラベルか URL がこれに当たれば候補にする
SEED_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("notice", re.compile(r"お知らせ|新着|ニュース|重要|news|notice|info(?:rmation)?/?$|topics")),
    ("spot_hours", re.compile(r"開館|営業時間|開園|利用案内|営業案内|hours|open")),
    ("spot_fees", re.compile(r"料金|観覧料|入館料|入園料|チケット|price|fee|admission|ticket")),
    ("spot_access", re.compile(r"アクセス|交通|access|map|行き方")),
    ("timetable", re.compile(r"時刻表|ダイヤ|運航|運行|timetable|schedule")),
)
# 運営主体が書かれていそうなページ（トップで根拠が取れないときの 2 段目）
ABOUT_PATTERNS = re.compile(
    r"会社概要|運営|について|概要|組織|財団|法人|about|company|profile|corporate"
)
# 素材・規約のページ（経路①の再確認）
TERMS_PATTERNS = re.compile(r"著作権|利用規約|サイトポリシー|このサイト|写真|素材|copyright|policy")
_TEXT = re.compile(r"<[^>]+>")


@dataclass
class Candidate:
    spot_id: str
    name: str
    official_url: str
    status: int = 0
    host: str = ""
    operator_quotes: list[str] = field(default_factory=list)
    operator_evidence_url: str = ""
    seeds: list[dict[str, str]] = field(default_factory=list)
    sns: dict[str, str] = field(default_factory=dict)
    terms: list[dict[str, str]] = field(default_factory=list)
    note: str = ""


def _plain(html: str) -> str:
    return " ".join(_TEXT.sub(" ", html).split())


def collect(client: PoliteClient, spot: object) -> Candidate:
    url = spot.official_url  # type: ignore[attr-defined]
    out = Candidate(spot_id=spot.spot_id, name=spot.name, official_url=url)  # type: ignore[attr-defined]
    res = client.get(url)
    out.status = res.status
    if not res.ok:
        out.note = f"取得できない（{res.error or res.status}）"
        return out
    out.host = urlparse(res.final_url).netloc
    _operator_quotes(_plain(res.text), out)
    if out.operator_quotes:
        out.operator_evidence_url = res.final_url

    seen: set[str] = set()
    for m in re.finditer(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', res.text, re.S | re.I):
        href, label = m.group(1), _plain(m.group(2))[:60]
        target = urljoin(res.final_url, href)
        if urlparse(target).netloc != out.host or target in seen:
            continue
        for kind, pattern in SEED_KINDS:
            if pattern.search(label) or pattern.search(href.lower()):
                seen.add(target)
                out.seeds.append({"kind": kind, "url": target, "label": label})
                break
        if len(out.seeds) >= 20:
            break

    for name, pattern in SNS_PATTERNS.items():
        m = pattern.search(res.text)
        if m:
            out.sns[name] = m.group(0)

    # 2 段目: トップで根拠が取れなければ、概要・規約のページを見る
    links = _labeled_links(res.text, res.final_url, out.host)
    if not out.operator_quotes:
        for target, _label in [x for x in links if ABOUT_PATTERNS.search(x[1] + x[0].lower())][:2]:
            sub = client.get(target)
            if not sub.ok:
                continue
            _operator_quotes(_plain(sub.text), out)
            if out.operator_quotes:
                out.operator_evidence_url = sub.final_url
                break
    # 経路①: 素材・規約ページのライセンス判定（S3 で到達できなかった分の再確認）
    for target, label in [x for x in links if TERMS_PATTERNS.search(x[1] + x[0].lower())][:2]:
        verdict, error = fetch_terms(client, target, credit_name=spot.name)  # type: ignore[attr-defined]
        out.terms.append(
            {
                "url": target,
                "label": label,
                "result": "取得できない"
                if verdict is None
                else ("採用" if verdict.allowed else "不採用"),
                "detail": error or (verdict.reason if verdict else ""),
            }
        )
    return out


def _operator_quotes(text: str, out: Candidate) -> None:
    for pattern in OPERATOR_PATTERNS:
        for m in pattern.finditer(text):
            quote = " ".join(m.group(0).split())[:160]
            if quote and quote not in out.operator_quotes:
                out.operator_quotes.append(quote)
            if len(out.operator_quotes) >= 6:
                return


def _labeled_links(html: str, base: str, host: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for m in re.finditer(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', html, re.S | re.I):
        target = urljoin(base, m.group(1))
        if urlparse(target).netloc != host or target in seen:
            continue
        seen.add(target)
        out.append((target, _plain(m.group(2))[:60]))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=Path("data/runs/source-candidates.json"))
    args = parser.parse_args()
    ws = Workspace.open(Path.cwd())
    ua = f"{ws.site.user_agent} source collector"
    rows: list[Candidate] = []
    with PoliteClient(ua, default_delay=3.0, jitter=1.0, timeout=30.0) as client:
        for spot in SPOTS:
            row = collect(client, spot)
            rows.append(row)
            kinds = ",".join(sorted({s["kind"] for s in row.seeds})) or "-"
            print(
                f"{row.spot_id:16} status={row.status:3} 根拠 {len(row.operator_quotes)}"
                f" / seed {len(row.seeds)}（{kinds}）/ 規約 {len(row.terms)}",
                flush=True,
            )
        requests = client.request_count
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "collected_at": jst_now().isoformat(),
                "requests": requests,
                "rows": [asdict(r) for r in rows],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(f"\n書き出し: {args.out}（{requests} リクエスト）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
