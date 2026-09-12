"""情報源の設定（data/sources/*.yaml）が規約を守っているかの検査。

ここが守れていないと、巡回してはいけないサイトを巡回する・推測した値を公開する、といった
取り返しのつかない失敗になる。外部アクセスはしない。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from sitemill.models import CrawlPolicy, OperatorKind
from sitemill.service import check_crawl_gate, crawlable_operator_kinds
from sitemill.settings import Workspace

from japan_open_today.data import Dataset, load_entries, load_sources
from japan_open_today.service import service
from japan_open_today.spec import spec_for_kind

ROOT = Path(__file__).resolve().parents[1]
# ADR 0009: レビュー待ちがこれを超えたら、人に配る前に発見側を直す
REVIEW_LIMIT = 10


@pytest.fixture(scope="module")
def ws() -> Workspace:
    return Workspace.open(ROOT)


@pytest.fixture(scope="module")
def entries(ws: Workspace) -> list[dict]:
    return load_entries(ws)


def test_sources_load(ws: Workspace) -> None:
    sources = load_sources(ws)
    assert len(sources) >= 10
    assert len({s.id for s in sources}) == len(sources)


def test_crawlable_sources_have_evidence_and_an_official_operator(ws: Workspace) -> None:
    """巡回するなら運営主体の根拠（引用・URL・確認日）が要る（ADR 0009）。"""
    allowed = crawlable_operator_kinds(service)
    for source in load_sources(ws):
        if not source.crawlable:
            continue
        assert source.operator_evidence is not None, source.id
        assert source.operator_evidence.quote.strip(), source.id
        assert source.operator_evidence.url.startswith("https://"), source.id
        assert source.operator_evidence.checked_on is not None, source.id
        assert source.operator_kind in allowed, (source.id, source.operator_kind)


def test_crawl_gate_passes(ws: Workspace) -> None:
    check_crawl_gate(service, load_sources(ws))


def test_unresolved_operators_are_pending_not_link_only(ws: Workspace) -> None:
    """判定できていないものを link_only にすると、レビューに回すべき案件が見えなくなる。"""
    for source in load_sources(ws):
        if source.operator_kind is OperatorKind.unknown:
            assert source.policy is CrawlPolicy.pending, source.id
            assert not source.crawlable, source.id


def test_pending_sources_record_what_was_tried(entries: list[dict]) -> None:
    """人に回すときは、試した情報源と失敗の理由を必ず添える（ADR 0009）。"""
    for entry in entries:
        if entry.get("policy") != "pending":
            continue
        assert entry.get("notes", "").strip(), entry["id"]


def test_review_queue_is_small_enough(ws: Workspace) -> None:
    """レビュー待ちが 10 件を超えたら発見側の作り込み不足とみなす（ADR 0009）。

    超えたときは、人に配る前に発見・判定側を直す。
    """
    candidates = service.review_candidates(ws)
    assert len(candidates) <= REVIEW_LIMIT, [c["label"] for c in candidates]


def test_every_seed_page_kind_has_an_extraction_spec(entries: list[dict]) -> None:
    """仕様の無い種別を巡回しても、静かに何も取れないだけで気づけない。"""
    for entry in entries:
        for page in entry.get("pages", []) or []:
            assert spec_for_kind(page["kind"]) is not None, (entry["id"], page["kind"])


def test_seed_hosts_are_allowed(entries: list[dict]) -> None:
    """allow_hosts の外に seed があると、意図しないホストへ出ていく。"""
    for entry in entries:
        hosts = entry.get("allow_hosts")
        if not hosts:
            continue
        for page in entry.get("pages", []) or []:
            assert any(f"//{h}/" in page["url"] for h in hosts), (entry["id"], page["url"])


def test_notice_pages_exist_for_spots_that_can_have_them(entries: list[dict]) -> None:
    """告知は毎日取る対象（ADR 0007）。種別が notice のページが 1 つは要る施設を数える。"""
    crawlable = [e for e in entries if e.get("policy") == "crawl"]
    with_notice = [
        e for e in crawlable if any(p["kind"] == "notice" for p in e.get("pages", []) or [])
    ]
    # 全件には無い（お知らせの索引ページが無いサイトがある）。0 件ではないことを確かめる
    assert with_notice, "告知ページを持つ情報源が 1 つも無い"


# --- 施設・交通のデータ -----------------------------------------------------


def test_spots_and_routes_load(ws: Workspace) -> None:
    ds = Dataset.load(ws)
    assert len(ds.spots) >= 10
    assert ds.operators and ds.routes
    assert len({s.spot_id for s in ds.spots}) == len(ds.spots)


def test_every_spot_has_three_language_names(ws: Workspace) -> None:
    """英語・繁体字が無いページは日本語のまま出す。推測のローマ字は作らない（ADR 0005）。"""
    for spot in Dataset.load(ws).spots:
        assert spot.names.get("ja"), spot.spot_id
        for locale in ("en", "zh-Hant"):
            entry = spot.names.get(locale)
            assert entry is not None and entry.text.strip(), (spot.spot_id, locale)
            assert entry.source in ("official", "glossary", "ja"), (spot.spot_id, locale)


def test_commons_categories_are_resolved_not_guessed(ws: Workspace) -> None:
    """カテゴリは辿って確定したものだけ。確定できなかったものは空にする（推測を入れない）。"""
    resolved = json.loads(
        (ROOT / "data" / "runs" / "commons-categories.json").read_text(encoding="utf-8")
    )
    by_id = {r["spot_id"]: r for r in resolved["rows"]}
    for spot in Dataset.load(ws).spots:
        if not spot.commons_category:
            continue
        row = by_id.get(spot.spot_id)
        assert row is not None, spot.spot_id
        assert row["category"] == spot.commons_category, spot.spot_id
        assert row["resolved_from"] in ("official", "wikipedia"), spot.spot_id
        assert row["category_url"], spot.spot_id


def test_areas_are_known(ws: Workspace) -> None:
    from japan_open_today.areas import BY_SLUG

    for spot in Dataset.load(ws).spots:
        assert spot.area in BY_SLUG, (spot.spot_id, spot.area)


def test_facts_are_not_written_by_hand(ws: Workspace) -> None:
    """開館時間・料金は巡回と抽出でしか入らない。YAML に手で書いていないことを確かめる。"""
    raw = (ROOT / "data" / "sources" / "kagawa.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    for entry in data["sources"]:
        block = entry.get("spot") or {}
        for forbidden in ("hours", "closures", "fees", "notices", "address", "phone"):
            assert forbidden not in block, (entry["id"], forbidden)


def test_registered_assets_are_usable(ws: Workspace) -> None:
    """ページに出せるのはライセンス判定を通った資産だけ（sitemill ADR 0020）。"""
    for asset in service.assets(ws):
        assert asset.usable, asset.asset_id
        assert asset.credit_text or asset.author, asset.asset_id
        assert asset.page_url, asset.asset_id
