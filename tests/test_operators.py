"""運営者の欄と根拠の引用の検査（ADR 0009 追記、2026-09-26）。外部アクセスはしない。"""

from __future__ import annotations

from typing import Any

import pytest

from japan_open_today.operators import operator_problems


def entry(operator: str, quote: str, kind: str = "facility_official", **extra: Any) -> dict:
    evidence = {"quote": quote, "url": "https://a.example/", **extra.pop("evidence", {})}
    return {"operator": operator, "operator_kind": kind, "operator_evidence": evidence, **extra}


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
    assert operator_problems(entry(operator, operator, kind="municipality")) == []


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
