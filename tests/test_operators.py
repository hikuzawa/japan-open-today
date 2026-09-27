"""運営者の欄と根拠の引用の検査（ADR 0009 追記、2026-09-26）。外部アクセスはしない。"""

from __future__ import annotations

from typing import Any

import pytest

from japan_open_today.operators import operator_problems


def entry(operator: str, quote: str, kind: str = "facility_official", **extra: Any) -> dict:
    evidence = {"quote": quote, "url": "https://a.example/", **extra.pop("evidence", {})}
    return {
        "operator": operator,
        "operator_kind": kind,
        "operator_evidence": evidence,
        "official_url": "https://a.example/",
        **extra,
    }


@pytest.mark.parametrize(
    "operator",
    [
        "株式会社」が設立した",  # 引用符の片割れ
        "公益財団法人福武財団による棚田の復元が行われました",  # 文
        "一般社団法人に移行し",
        "株式会社 沿革",  # 見出しの文字列
        "Copyright © 2020 Kagawa Prefectural Gove",  # 著作権表記
        # ページ題名
        "三霞洞渓谷（みかどけいこく） - Manno まんのう魅力発信ポータルサイト - まんのう町",
        "菊池寛記念館｜高松市",
        "公益社団法人瀬戸内海環境保全協会×四国水族館",  # 共催の表記
        "株式会社",  # 法人格だけ
    ],
)
def test_fragments_are_not_names(operator: str) -> None:
    problems = operator_problems(entry(operator, operator, kind="municipality"))
    assert any("名前でない" in p for p in problems), operator


@pytest.mark.parametrize(
    "operator",
    [
        "高松市",
        "公益社団法人香川県観光協会",
        "株式会社ベネッセコーポレーション・公益財団法人福武財団",
        "一般財団法人ことなみ振興公社",
        "金刀比羅宮",
        "NPO法人直島町観光協会",
    ],
)
def test_real_names_pass(operator: str) -> None:
    assert operator_problems(entry(operator, operator)) == []


def test_a_facility_official_needs_its_name_in_the_quote() -> None:
    """引用が運営者を名乗っていなければ、施設の公式とは読まない。"""
    weak = entry("公益財団法人四国民家博物館", "公益財団法人に認定されました。")
    assert operator_problems(weak) == [
        "施設の公式なのに、根拠の引用に運営者の名前が無い: 公益財団法人四国民家博物館"
    ]
    spaced = entry("公益財団法人福武財団", "…と公益財団法人 福武財団が展開しているアート活動")
    assert operator_problems(spaced) == []  # 空白の有無は問わない


def test_two_operators_must_both_be_named() -> None:
    both = "株式会社ベネッセホールディングスと公益財団法人 福武財団が展開している"
    assert (
        operator_problems(entry("株式会社ベネッセホールディングス・公益財団法人福武財団", both))
        == []
    )
    assert operator_problems(
        entry(
            "株式会社ベネッセホールディングス・公益財団法人福武財団",
            "株式会社ベネッセホールディングス",
        )
    )


def test_a_romanised_name_is_declared_and_checked() -> None:
    """「(C) KOTOHIRA-GU」は金刀比羅宮の名乗り。表記を書き、その表記が引用にあるかを見る。"""
    ok = entry("金刀比羅宮", "(C) KOTOHIRA-GU", evidence={"name_in_quote": "KOTOHIRA-GU"})
    assert operator_problems(ok) == []
    wrong = entry("金刀比羅宮", "(C) KOTOHIRA-GU", evidence={"name_in_quote": "ZENTSUJI"})
    assert operator_problems(wrong) == ["name_in_quote の表記が引用に無い: ZENTSUJI"]
    assert operator_problems(entry("金刀比羅宮", "(C) KOTOHIRA-GU"))  # 表記が無ければ通さない


def test_a_site_without_a_legal_name_needs_a_written_reason() -> None:
    """法人名がサイトに無く、サイトが施設自身のものと確かめたときだけ operator_note で通す。"""
    bare = entry("道の駅ことひき", "ミュージアム&パーク 住所 観音寺市有明町3-37")
    assert operator_problems(bare)
    noted = dict(bare, operator_note="運営する法人名はサイトに無い。サイトは施設自身のもの")
    assert operator_problems(noted) == []


def test_other_kinds_are_judged_by_host_not_by_the_quote() -> None:
    """自治体・観光協会はホストで種別が決まる。英字の著作権表記でも欄が名前なら通す。"""
    city = entry("高松市", "Copyright © Takamatsu City, All rights reserved.", kind="municipality")
    assert operator_problems(city) == []
    assert operator_problems(entry("", "", kind="facility_official")) == [
        "施設の公式なのに運営者の名前が無い"
    ]


def sourced(kind: str, operator: str, quote: str, url: str, **extra: Any) -> dict:
    """種別をホストで決める情報源（巡回先・公式 URL つき）。"""
    return {
        "operator": operator,
        "operator_kind": kind,
        "operator_evidence": {"quote": quote, "url": url},
        "official_url": "https://www.city.takamatsu.kagawa.jp/museum/",
        "pages": [{"url": "https://www.city.takamatsu.kagawa.jp/museum/index.html"}],
        "allow_hosts": ["www.city.takamatsu.kagawa.jp"],
        **extra,
    }


CITY = "https://www.city.takamatsu.kagawa.jp/"


def test_a_municipal_quote_must_be_the_municipality_naming_itself() -> None:
    """高松市の 4 館は、別の施設の指定管理者の話を引用していた（2026-09-26）。"""
    other_facility = sourced(
        "municipality",
        "高松市",
        "サンポートホール高松の指定管理者である公益財団法人高松市文化芸術財団の事務局主幹をお招きし",
        CITY + "bunsin_platoform.html",
    )
    problems = operator_problems(other_facility)
    assert len(problems) == 1
    assert problems[0].startswith(
        "自治体・県の引用が自らの名乗りでない: サンポートホール高松の指定管理者"
    )
    for quote in (
        "Copyright © Takamatsu City, All rights reserved.",
        "緊急連絡先 さぬき市教育委員会 生涯学習課 電話0879-26-9974",
        "〒762-8601 香川県坂出市室町二丁目3番5号 坂出市役所",
        "高松市美術館美術課によって管理・運営されております",
    ):
        assert operator_problems(sourced("municipality", "高松市", quote, CITY)) == [], quote


def test_a_managed_facility_may_quote_its_designated_manager() -> None:
    managed = sourced(
        "municipality_affiliated",
        "公益財団法人かがわ水と緑の財団",
        "現在は、公益財団法人かがわ水と緑の財団が指定管理者として管理を行っています",
        CITY,
    )
    assert operator_problems(managed) == []


def test_the_evidence_must_sit_on_the_source_s_own_site() -> None:
    elsewhere = sourced(
        "municipality", "高松市", "Copyright © Takamatsu City", "https://kongozen.jp/"
    )
    assert operator_problems(elsewhere) == ["根拠がこの情報源のサイトに無い: kongozen.jp"]
    # www. の有無は同じサイト
    bare = sourced(
        "municipality", "高松市", "Copyright © Takamatsu City", "https://city.takamatsu.kagawa.jp/x"
    )
    assert operator_problems(bare) == []


def test_a_tourism_page_is_run_by_the_association_not_the_listed_facility() -> None:
    """観光協会のページを巡回しているのに、載っている施設の運営会社を運営者にしていた。"""
    listed = sourced(
        "tourism_association", "株式会社レオマユニティー", "株式会社レオマユニティー", CITY
    )
    assert operator_problems(listed) == [
        "観光協会の引用・欄に協会の名前が無い: 株式会社レオマユニティー"
    ]
    ok = sourced(
        "tourism_association", "公益社団法人香川県観光協会", "公益社団法人香川県観光協会", CITY
    )
    assert operator_problems(ok) == []


def test_an_exception_needs_a_reason_and_must_still_be_needed() -> None:
    elsewhere = dict(
        sourced(
            "municipality", "高松市", "Copyright © Takamatsu City", "https://88shikokuhenro.jp/"
        ),
    )
    with_reason = dict(
        elsewhere,
        operator_check_exceptions={
            "evidence_on_site": "寺に公式サイトが無く、加盟する霊場会のページ"
        },
    )
    assert operator_problems(with_reason) == []
    blank = dict(elsewhere, operator_check_exceptions={"evidence_on_site": "  "})
    assert operator_problems(blank) == [
        "理由の無い例外: evidence_on_site",
        "根拠がこの情報源のサイトに無い: 88shikokuhenro.jp",
    ]
    unknown = dict(with_reason, operator_check_exceptions={"anything": "理由"})
    assert "知らない規則の例外: anything" in operator_problems(unknown)[0]
    stale = sourced(
        "municipality",
        "高松市",
        "Copyright © Takamatsu City",
        CITY,
        operator_check_exceptions={"evidence_on_site": "昔は別サイトだった"},
    )
    assert operator_problems(stale) == ["もう当てはまらない例外: evidence_on_site（外しても通る）"]
