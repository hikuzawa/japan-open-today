"""数え上げと情報源づくりの判定（S6-3 ①。ADR 0009・0011）。外部アクセスはしない。"""

from __future__ import annotations

from tools.collect_spots import CLOCK, FEE_AMOUNT, _info_table, _skip_reason
from tools.seed_spots import THIRD_PARTY, _category, _operator_kind, _operator_name, _slug_from


def test_the_skip_rule_looks_at_the_name_and_the_category_not_the_body() -> None:
    """美術館の本文には「体験コーナー」「カフェ」が普通に出てくる。本文で除くと施設が消える。"""
    museum = (
        "香川県中部(瀬戸大橋など) 博物館・資料館・展示館 猪熊弦一郎現代美術館 "
        "館内には体験コーナーとカフェがあり、ワークショップも開催しています。"
    )
    assert _skip_reason("猪熊弦一郎現代美術館", museum) is None
    # 分類の見出しが「うどん」なら対象外（ADR 0011）
    assert _skip_reason("山越うどん", "香川県中部 うどん 山越うどん 釜玉の店") == "restaurant"
    assert _skip_reason("藍染体験工房", "香川県東部 体験 藍染体験工房") == "experience"
    assert _skip_reason("ホテル○○", "香川県西部 宿泊 ホテル○○") == "lodging"


def test_the_info_table_is_split_by_its_headings() -> None:
    text = (
        "香川県東部 温泉・癒し、スパ つばさ山温泉 説明が入る 基本情報 住所 〒769-2901 "
        "香川県東かがわ市引田991-16 電話番号 0879-33-2532 営業時間 10:00~22:00(最終受付21:30) "
        "定休日 年中無休(臨時休館あり) 料金 大人500円 アクセス JR引田駅より徒歩15分 "
        "駐車場 約30台 周辺観光情報 その先は無視する"
    )
    info = _info_table(text)
    assert info["住所"].startswith("〒769-2901")
    assert info["営業時間"] == "10:00~22:00(最終受付21:30)"
    assert info["定休日"].startswith("年中無休")
    assert "周辺観光情報" not in info["駐車場"]
    # 型の判定に使う形（営業時間か料金があれば gated）
    assert CLOCK.search(info["営業時間"])
    assert FEE_AMOUNT.search(info["料金"])


def test_a_place_with_neither_hours_nor_fee_is_open_air() -> None:
    info = _info_table("基本情報 住所 香川県三豊市仁尾町 アクセス 車で10分 周辺観光情報")
    assert not CLOCK.search(info.get("営業時間", ""))
    assert not FEE_AMOUNT.search(info.get("料金", ""))


def test_third_party_sites_are_not_primary_sources() -> None:
    for host in ("www.jalan.net", "www.tripadvisor.jp", "www.instagram.com", "www.booking.com"):
        assert THIRD_PARTY.search(host), host
    for host in ("www.shikokumura.or.jp", "www.city.takamatsu.kagawa.jp", "ritsuringarden.jp"):
        assert not THIRD_PARTY.search(host), host


def test_the_identifier_comes_from_the_operators_own_spelling() -> None:
    """ローマ字名をこちらで考えない（ADR 0005）。公式サイトのホスト名・パスを使う。"""
    assert _slug_from("https://www.shikokumura.or.jp/", "123") == "shikokumura"
    assert _slug_from("https://ritsuringarden.jp/hours/", "9") == "ritsuringarden"
    # 公式サイトが無く観光協会のページを一次情報にする場合は、その id を使う
    assert _slug_from("https://www.my-kagawa.jp/point/298", "298") == "p298"
    assert _slug_from("https://www.my-kagawa.jp/", "298") == "p298"
    # 借りているホスティングのホスト名は施設を表さないので、パスを使う
    assert _slug_from("https://r.goope.jp/new-yashima-aq", "1") == "new-yashima-aq"


def test_the_category_is_read_from_the_name() -> None:
    assert _category("香川県立ミュージアム") == "museum"
    assert _category("金刀比羅宮") == "shrine_temple"
    assert _category("丸亀城") == "castle"
    assert _category("栗林公園") == "park"
    assert _category("塩江温泉") == "onsen"
    assert _category("瀬戸大橋") == "other"


def test_the_operator_kind_follows_the_host_then_the_quote() -> None:
    assert _operator_kind("www.pref.kagawa.lg.jp", "Copyright Kagawa") == "prefecture"
    assert _operator_kind("www.city.takamatsu.kagawa.jp", "Copyright") == "municipality"
    assert _operator_kind("example.or.jp", "指定管理者 株式会社なにか") == "municipality_affiliated"
    assert _operator_kind("example.or.jp", "公益社団法人 香川県観光協会") == "tourism_association"
    assert _operator_kind("example.co.jp", "株式会社なにか") == "facility_official"


def test_the_operator_name_is_taken_from_the_quote() -> None:
    assert _operator_name("公益財団法人 福武財団 が運営しています") == "公益財団法人 福武財団"
    assert _operator_name("指定管理者: 株式会社さぬき") == "株式会社さぬき"
    assert _operator_name("Copyright City of Takamatsu").startswith("Copyright")


def test_a_check_in_time_means_lodging_even_without_the_word_hotel() -> None:
    """「トレスタ白山」のように、名前からは宿と分からない宿がある。"""
    from tools.collect_spots import CHECK_IN

    assert CHECK_IN.search("宿泊はチェックイン15:00、チェックアウト10:00")
    assert not CHECK_IN.search("9:00~17:00(入館は16:30まで)")


def test_a_page_without_the_info_table_is_unknown_not_open_air() -> None:
    """表が読めないのは「屋外だから」ではない。読めていないだけなので unknown にする。

    新屋島水族館がこれで、営業時間の無いページから open_air と決めてしまっていた。
    """
    assert _info_table("説明だけのページ。基本情報の表が無い。") == {}


def test_the_name_check_ignores_full_width_and_half_width_brackets() -> None:
    """一覧は「屋島（山上）」、ページは「屋島(山上)」。そのまま比べると全部外れる。"""
    from sitemill.diff.normalize import squash

    assert squash("屋島（山上）") in squash("屋島 屋島(山上) やしま・さんじょう 基本情報")
    assert squash("金刀比羅宮") not in squash("つばさ山温泉 香川県東かがわ市引田")


def test_stored_rows_can_be_redecided_without_fetching_again() -> None:
    """規則を直したときに数百件を取り直さないための道。判定に本文が要らないものだけ。"""
    from tools.collect_spots import Candidate, redecide_from_info

    hotel = Candidate(
        point_id="1",
        name="トレスタ白山",
        url="https://example.jp/1",
        area="香川県中部",
        spot_type="gated",
        info={"営業時間": "チェックイン15:00、チェックアウト10:00"},
    )
    assert redecide_from_info(hotel) is True
    assert hotel.spot_type == "skip"

    aquarium = Candidate(
        point_id="2",
        name="新屋島水族館",
        url="https://example.jp/2",
        area="高松市周辺",
        spot_type="open_air",
        info={},
    )
    assert redecide_from_info(aquarium) is True
    assert aquarium.spot_type == "unknown"

    # 対象外と判定したものは触らない（本文を見ないと決められないため）
    skipped = Candidate(
        point_id="3",
        name="山越うどん",
        url="https://example.jp/3",
        area="香川県中部",
        spot_type="skip",
        reason="restaurant",
    )
    assert redecide_from_info(skipped) is False
    assert skipped.spot_type == "skip"
