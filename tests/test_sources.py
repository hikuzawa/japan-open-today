"""情報源の設定（data/sources/*.yaml）が規約を守っているかの検査。

ここが守れていないと、巡回してはいけないサイトを巡回する・推測した値を公開する、といった
取り返しのつかない失敗になる。外部アクセスはしない。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml
from sitemill.models import CrawlPolicy, OperatorKind
from sitemill.service import check_crawl_gate, crawlable_operator_kinds
from sitemill.settings import Workspace

from japan_open_today.data import Dataset, load_entries, load_sources
from japan_open_today.operators import operator_problems
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
        # http のみの公式サイトは実際にある（地元の施設・古い自治体ページ）。
        # 求めるのは「根拠の在りかが URL として書かれていること」
        assert source.operator_evidence.url.startswith(("https://", "http://")), source.id
        assert source.operator_evidence.checked_on is not None, source.id
        assert source.operator_kind in allowed, (source.id, source.operator_kind)


def test_operator_names_and_quotes_make_sense(entries: list[dict]) -> None:
    """運営者の欄は名前、施設の公式は引用が運営者を名乗っている（ADR 0009 追記 2026-09-26）。

    書き込む道具（seed_spots）も同じ関数で止めるが、手編集や別の道具で戻ったものはここで捕まえる。
    同じ形の緩い引用が 3 回見つかった（観光協会 10 件、自治体 24 件、2026-09-26 の 42 件）。
    """
    problems = [(e["id"], p) for e in entries for p in operator_problems(e)]
    assert problems == []


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
            host = urlparse(page["url"]).netloc
            assert host in hosts, (entry["id"], page["url"])


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


def test_spot_names_are_japanese_or_an_evidenced_translation(ws: Workspace) -> None:
    """日本語名は必ずある。英語・繁体字は**公式表記か用語集があるときだけ**入れる（ADR 0005）。

    推測のローマ字は作らないので、訳が無い施設は日本語のまま出る。ここでは
    「訳が入っているなら出どころが宣言されている」ことだけを求める。
    """
    spots = Dataset.load(ws).spots
    for spot in spots:
        assert spot.names.get("ja"), spot.spot_id
        assert spot.names["ja"].text.strip(), spot.spot_id
        for locale in ("en", "zh-Hant"):
            entry = spot.names.get(locale)
            if entry is None:
                continue
            assert entry.text.strip(), (spot.spot_id, locale)
            assert entry.source in ("official", "glossary", "ja"), (spot.spot_id, locale)
    # 訳の無い施設が増えたことを見えるようにする（用語集を埋める作業の目安）
    translated = [s for s in spots if s.names.get("en")]
    assert translated, "英語名が 1 件も無いのは設定の取りこぼし"


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


def test_every_spot_declares_its_type(entries: list[dict]) -> None:
    """型が未判定の施設は充足率の指標から漏れる（ADR 0011）。推測はせず、宣言を求める。"""
    untyped = [
        e["spot"].get("spot_id", e["id"])
        for e in entries
        if e.get("spot") and e["spot"].get("spot_type", "unknown") == "unknown"
    ]
    assert not untyped, f"spot_type を宣言していない施設: {untyped}"


def test_open_air_places_do_not_claim_to_be_always_open(ws: Workspace) -> None:
    """屋外でも「常に開いている」とは言わない。原文に根拠がある場合だけ always_open（ADR 0004）。"""
    for spot in Dataset.load(ws).spots:
        if spot.spot_type != "open_air":
            continue
        for period in spot.hours:
            if period.always_open:
                quote = (period.evidence.quote if period.evidence else None) or ""
                assert quote, f"{spot.spot_id}: 根拠の引用なしに always_open にしている"


# --- エリアの割り当て -------------------------------------------------------


def test_every_kagawa_municipality_belongs_to_an_area() -> None:
    """香川の 8 市 9 町はすべてどこかのエリアに入る。入らないと住所から決められない。"""
    from japan_open_today.areas import AREAS, area_for_address

    cities = (
        "高松市",
        "丸亀市",
        "坂出市",
        "善通寺市",
        "観音寺市",
        "さぬき市",
        "東かがわ市",
        "三豊市",
        "土庄町",
        "小豆島町",
        "三木町",
        "直島町",
        "宇多津町",
        "綾川町",
        "琴平町",
        "多度津町",
        "まんのう町",
    )
    assigned = {m for a in AREAS for m in a.municipalities}
    assert set(cities) == assigned, set(cities) ^ assigned
    for city in cities:
        assert area_for_address(f"香川県{city}中央1-1") is not None, city


def test_islands_win_over_the_municipality_they_sit_in() -> None:
    """豊島は土庄町にある。市町を先に見ると小豆島に入ってしまう。"""
    from japan_open_today.areas import area_for_address

    assert area_for_address("香川県小豆郡土庄町豊島家浦") == "teshima"
    assert area_for_address("香川県小豆郡土庄町甲") == "shodoshima"
    assert area_for_address("香川県高松市女木町") == "megijima"
    assert area_for_address("香川県高松市屋島山上") == "takamatsu"
    # 県外・空文字は決めない（推測しない）
    assert area_for_address("岡山県玉野市") is None
    assert area_for_address("") is None


# --- 同じ場所を 2 回入れない -------------------------------------------------


def _norm_name(name: str) -> str:
    """「【紅葉スポット】寒霞渓」→「寒霞渓」。テーマ別の一覧は同じ場所を別名で載せる。"""
    name = re.sub(r"^【[^】]*】", "", name or "")
    return re.sub(r"[（(][^）)]*[）)]", "", name).strip()


def _addr_key(address: str) -> str:
    address = re.sub(r"〒?\d{3}-?\d{4}", "", address or "")
    address = address.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    return re.sub(r"[丁目番地号の\-ー－\s]", "", address)[:24]


def test_the_same_place_is_not_listed_twice(ws: Workspace) -> None:
    """観光協会の一覧は同じ場所をテーマ別に何度も載せる（「【紅葉スポット】寒霞渓」）。

    そのまま収録すると、同じ施設のページが 2 つでき、判定も 2 回数えられる。
    名前が同じもの、住所が同じで名前が包含関係にあるものは 1 件にする。
    """
    spots = Dataset.load(ws).spots
    by_name: dict[str, str] = {}
    for spot in spots:
        key = _norm_name(spot.name("ja"))
        assert key not in by_name, (spot.spot_id, by_name.get(key))
        by_name[key] = spot.spot_id
    by_addr: dict[str, tuple[str, str]] = {}
    for spot in spots:
        addr = _addr_key(spot.address.value or spot.address.quote or "")
        if not addr:
            continue
        found = by_addr.get(addr)
        name = _norm_name(spot.name("ja"))
        if found is not None:
            other_id, other_name = found
            assert not (name in other_name or other_name in name), (spot.spot_id, other_id)
        else:
            by_addr[addr] = (spot.spot_id, name)


# --- 用語集（多言語の固定訳。ADR 0005） -------------------------------------


def test_glossary_entries_declare_where_they_came_from(ws: Workspace) -> None:
    """訳は必ず由来つきで持つ。由来の無い訳は、後から誰も確かめられない。"""
    from japan_open_today.data import load_glossary

    glossary = load_glossary(ws)
    assert glossary, "用語集が空。多言語名が解決できていない"
    for locale, table in glossary.items():
        for ja_name, entry in table.items():
            assert entry.text.strip(), (locale, ja_name)
            assert entry.source in ("official", "glossary"), (locale, ja_name, entry.source)


def test_translations_are_not_japanese_and_not_simplified(ws: Workspace) -> None:
    """英語の欄に日本語、繁体字の欄に簡体字が入っていないこと。

    実際に、中文ページから「四国水族馆」（簡体字）を、英語ページから繁体字の欄に
    「TAKAMATSU ART MUSEUM」を入れかけた。字種で守る。
    """
    from japan_open_today.data import load_glossary

    kana = re.compile(r"[ぁ-んァ-ヶ]")
    simplified = re.compile(r"[馆术际标丰历产权义习书乐云价众优伟传伤]")
    for locale, table in load_glossary(ws).items():
        for ja_name, entry in table.items():
            assert not kana.search(entry.text), (locale, ja_name, entry.text)
            if locale == "zh-Hant":
                assert not simplified.search(entry.text), (locale, ja_name, entry.text)


def test_the_glossary_fills_only_the_gaps() -> None:
    """公式表記が入っている施設は用語集で上書きしない（ADR 0005 の優先順）。"""
    from japan_open_today.data import _fill_names
    from japan_open_today.schema import LocalizedName

    glossary = {
        "en": {"栗林公園": LocalizedName(text="Ritsurin Park", source="glossary")},
        "zh-Hant": {"栗林公園": LocalizedName(text="栗林公園", source="glossary")},
    }
    payload = {
        "names": {
            "ja": {"text": "栗林公園", "source": "ja"},
            "en": {"text": "Ritsurin Garden", "source": "official"},
        }
    }
    _fill_names(payload, glossary)
    # 公式表記はそのまま、空いていた繁体字だけ埋まる
    assert payload["names"]["en"]["text"] == "Ritsurin Garden"
    assert payload["names"]["en"]["source"] == "official"
    assert payload["names"]["zh-Hant"]["text"] == "栗林公園"
    assert payload["names"]["zh-Hant"]["source"] == "glossary"
    # 用語集に無い施設は日本語のまま（勝手な表記を作らない）
    other = {"names": {"ja": {"text": "父母ヶ浜", "source": "ja"}}}
    _fill_names(other, glossary)
    assert set(other["names"]) == {"ja"}
