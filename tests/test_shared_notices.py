"""1 枚で複数施設を扱うお知らせページの切り分け（ADR 0010）。

ここを間違えると、開いている施設のページに他館の「休館」が出る。実際に起きた:
ベネッセの /news/ を 3 施設の notice として seed していたため、李禹煥美術館と
家プロジェクト「きんざ」の臨時休館が豊島美術館の休館として公開されていた。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sitemill.settings import Workspace

from japan_open_today.data import Dataset, load_entries
from japan_open_today.ingest import _covered_spots, match_facility

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 9, 12, 21, 10, tzinfo=UTC)


@pytest.fixture(scope="module")
def ws() -> Workspace:
    return Workspace.open(ROOT)


@pytest.fixture(scope="module")
def shared_entry(ws: Workspace) -> dict:
    entry = next((e for e in load_entries(ws) if e.get("covers")), None)
    assert entry is not None, "共有ページの情報源が無いので、この検査が意味を持たない"
    return entry


@pytest.fixture(scope="module")
def covered(ws: Workspace, shared_entry: dict) -> list:
    return _covered_spots(ws, shared_entry)


def test_a_notice_is_attributed_only_to_the_facility_it_names(covered: list) -> None:
    assert {s.spot_id for s in covered} >= {"chichu", "teshima-art"}
    assert match_facility("地中美術館", covered).spot_id == "chichu"
    assert match_facility("豊島美術館", covered).spot_id == "teshima-art"
    # 別名（公式ページに出る短い書き方）
    assert match_facility("ベネッセハウス", covered).spot_id == "benesse-house"


def test_a_facility_outside_covers_is_dropped(covered: list) -> None:
    """李禹煥美術館・家プロジェクト「きんざ」・犬島精錬所美術館は収録していない。

    「同じ運営者の別の館」を近いものに寄せてはいけない。名指しが一致しなければ捨てる。
    """
    for name in (
        "李禹煥美術館",
        "家プロジェクト「きんざ」",
        "犬島精錬所美術館",
        "ヴァレーギャラリー",
    ):
        assert match_facility(name, covered) is None, name


def test_a_notice_without_a_facility_name_is_dropped(covered: list) -> None:
    """「全館」「豊島」のような書き方では施設が決まらない。全施設に当てはめない。"""
    assert match_facility("", covered) is None
    assert match_facility("全館", covered) is None
    assert match_facility("アート施設", covered) is None


def test_an_ambiguous_name_is_dropped() -> None:
    """同じ表記に複数の施設が当たるときは決められない。片方に寄せない。"""

    class _Name:
        def __init__(self, text: str) -> None:
            self.text = text

    class _Spot:
        def __init__(self, spot_id: str, name: str, aliases: list[str]) -> None:
            self.spot_id = spot_id
            self.names = {"ja": _Name(name)}
            self.aliases = aliases

    spots = [
        _Spot("a", "ベネッセハウス ミュージアム", ["ベネッセハウス"]),
        _Spot("b", "ベネッセハウス パーク", ["ベネッセハウス"]),
    ]
    assert match_facility("ベネッセハウス", spots) is None
    # 完全一致が 1 つに絞れるなら決まる
    assert match_facility("ベネッセハウス パーク", spots).spot_id == "b"


def test_shared_notices_reach_the_spot_that_another_source_owns(ws: Workspace) -> None:
    """共有ページの記録は別ファイルに入る。施設のページに届いていることを確かめる。"""
    ds = Dataset.load(ws)
    by_id = {s.spot_id: s for s in ds.spots}
    shared_urls = {"https://benesse-artsite.jp/calendar/", "https://benesse-artsite.jp/news/"}

    def from_shared(spot_id: str) -> list:
        return [
            n
            for n in by_id[spot_id].notices
            if n.evidence is not None and n.evidence.source_url in shared_urls
        ]

    assert from_shared("chichu"), "地中美術館の休館が共有ページから届いていない"
    # 他館の告知が混ざっていないこと（これが本来の目的）
    for spot_id in ("benesse-house", "teshima-art"):
        for notice in from_shared(spot_id):
            quote = (notice.evidence.quote or "") if notice.evidence else ""
            assert "李禹煥" not in quote and "きんざ" not in quote, spot_id


def test_no_spot_source_seeds_a_page_that_another_source_also_seeds(ws: Workspace) -> None:
    """施設を持つ情報源が共有ページを seed していないこと。

    seed し直すと同じ事故が起きる。発見側でも弾いているが、設定そのものを検査する。
    """
    entries = load_entries(ws)
    owners: dict[str, list[str]] = {}
    for entry in entries:
        for page in entry.get("pages", []) or []:
            owners.setdefault(page["url"], []).append(entry["id"])
    for url, ids in owners.items():
        if len(ids) < 2:
            continue
        # 複数の情報源が同じ URL を持つのは、施設ごとの案内が 1 ページに同居する場合だけ。
        # お知らせ・カレンダーの共有は shared_notice で扱う
        kinds = {
            page["kind"]
            for entry in entries
            for page in entry.get("pages", []) or []
            if page["url"] == url
        }
        assert "notice" not in kinds, f"{url} を notice として複数の施設が seed している: {ids}"


def test_expired_and_unseeded_notices_are_dropped(ws: Workspace) -> None:
    """情報源から外した URL 由来の告知は記録からも消える（そうでないとページに出続ける）。"""
    from japan_open_today.ingest import finalize_records

    counts = finalize_records(ws, now=NOW)
    assert counts["records"] > 0
    seeded = {p["url"] for e in load_entries(ws) for p in e.get("pages", []) or []}
    for spot in Dataset.load(ws).spots:
        for notice in spot.notices:
            url = notice.evidence.source_url if notice.evidence else None
            assert url is None or url in seeded, f"{spot.spot_id}: {url}"


# --- seed した URL が別の施設のものだったとき -------------------------------


def test_a_detail_page_that_does_not_name_the_spot_is_refused(ws: Workspace, tmp_path) -> None:
    """屋島に温泉の URL を seed していた事故を止める門（ADR 0009 追記）。

    施設の素性を決めるページなのに、その施設の名前がどこにも出てこないなら、
    別の施設の URL を seed している。取り込まない。
    """
    from sitemill.store.raw import RawCache

    from japan_open_today.ingest import _page_names_the_spot

    spot = next(s for s in Dataset.load(ws).spots if s.spot_id == "yashima")
    cache = RawCache(tmp_path)
    other = "<html><body><h1>つばさ山温泉</h1><p>香川県東かがわ市引田991-16</p></body></html>"
    right = "<html><body><h1>屋島(山上)</h1><p>香川県高松市屋島山上</p></body></html>"

    class _WS:
        raw_dir = tmp_path

    cache.save("kagawa-yashima", "https://example.jp/onsen", other.encode(), {"encoding": "utf-8"})
    cache.save(
        "kagawa-yashima", "https://example.jp/yashima", right.encode(), {"encoding": "utf-8"}
    )
    assert _page_names_the_spot(_WS(), "kagawa-yashima", "https://example.jp/onsen", spot) is False
    assert _page_names_the_spot(_WS(), "kagawa-yashima", "https://example.jp/yashima", spot) is True
    # 生 HTML が無いときは判定しない（抽出は生 HTML が無ければ走らない）
    assert _page_names_the_spot(_WS(), "kagawa-yashima", "https://example.jp/none", spot) is True


def test_a_name_split_across_the_page_still_matches(ws: Workspace, tmp_path) -> None:
    """公式は「史跡高松城跡」と「玉藻公園」を離して書く。語がすべてあれば同じ施設とする。"""
    from sitemill.store.raw import RawCache

    from japan_open_today.ingest import _page_names_the_spot

    spot = next(s for s in Dataset.load(ws).spots if s.spot_id == "tamamo")
    cache = RawCache(tmp_path)
    html = "<html><body><h1>史跡高松城跡</h1><p>玉藻公園の開園時間</p></body></html>"
    cache.save("kagawa-tamamo", "https://example.jp/tamamo", html.encode(), {"encoding": "utf-8"})

    class _WS:
        raw_dir = tmp_path

    assert _page_names_the_spot(_WS(), "kagawa-tamamo", "https://example.jp/tamamo", spot) is True


# --- 告知の年と、別の施設の告知（2026-09-17） ------------------------------------


def test_a_notice_naming_another_place_is_not_this_places_notice() -> None:
    """運営会社のお知らせページは別の施設の告知も並べる。名指しが別の施設なら取り込まない。"""
    from japan_open_today.ingest import about_another_place

    assert (
        about_another_place("太龍寺ロープウェー運休", ["雲辺寺ロープウェイ"])
        == "太龍寺ロープウェー"
    )
    assert about_another_place("地中美術館の長期メンテナンス休館", ["地中美術館"]) is None
    assert about_another_place("丸亀美術館絵画館の休館", ["中津万象園・丸亀美術館"]) is None
    assert about_another_place("臨時休館のお知らせ", ["川島猪熊邸"]) is None  # 名指しが無ければ残す


def test_stored_notices_are_reread_from_their_quotes() -> None:
    """去年の告知（令和7年・曜日が今年と合わない）を今年の休業にしない。引用から読み直す。"""
    from tools.reparse_notices import reparse

    def notice(quote: str, reason: str, start: str, end: str) -> dict:
        return {
            "kind": "closed",
            "reason": reason,
            "span": {"start": start, "end": end},
            "evidence": {"quote": quote},
        }

    kept, changes = reparse(
        [
            notice(
                "R7年11月11日(火)~11月20日(木)", "臨時休館のお知らせ", "2026-11-11", "2026-11-20"
            ),
            notice("10月1日(水)", "ロープウェイ運休", "2026-10-01", "2026-10-01"),
            notice("9/16(水)~9/18(金)", "太龍寺ロープウェー運休", "2026-09-16", "2026-09-18"),
            notice("2026年10月1日(水)", "臨時休業", "2026-10-01", "2026-10-01"),
        ],
        ["雲辺寺ロープウェイ"],
    )
    assert [(n["span"]["start"], n["span"]["end"]) for n in kept] == [
        ("2025-11-11", "2025-11-20"),
        ("2025-10-01", "2025-10-01"),
    ]
    assert any("太龍寺" in c for c in changes)
    assert any("weekday_mismatch" in c for c in changes)  # 年と曜日が食い違うものは値にしない
