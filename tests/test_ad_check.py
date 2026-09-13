"""広告掲載の検査（ADR 0012）。契約前でも、案件が入った状態を作って検査を確かめる。

akiya-atlas で踏んだ問題は移植の時点で入っている（`&` のエスケープ、計測 URL 未設定の案件を
素通しにしない、広告表記の位置）。ここではそれが**このサイトの形**（枠が数百ページに出る、
転送ページがロケールごとにある）でも効くことを固定する。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from japan_open_today import ad_check, affiliates

LOCALES = (("ja", ""), ("en", "en"))
# 計測 URL は & を 2 つ以上含む形にする。HTML では &amp; になるので、戻さずに比べると
# 「宣言していない広告リンク」として誤検出する
TRACK_URL = "https://af.example.com/click?a_id=1&p_id=2&pl_id=3"


@pytest.fixture
def ready(monkeypatch: pytest.MonkeyPatch) -> affiliates.Offer:
    """契約済みの案件が 1 件ある状態にする。"""
    asp = replace(affiliates.ASPS["klook"], allowed_link_hosts=("af.example.com",))
    offer = replace(
        affiliates.OFFERS[0],
        url=TRACK_URL,
        advertiser="例の会社",
        name="例の案件",
    )
    monkeypatch.setitem(affiliates.ASPS, "klook", asp)
    monkeypatch.setattr(affiliates, "OFFERS", (offer,))
    return offer


def _dist(tmp_path: Path, pages: dict[str, str], redirects: str = "") -> Path:
    dist = tmp_path / "dist"
    for rel, html in pages.items():
        path = dist / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
    (dist / "_redirects").write_text(redirects, encoding="utf-8")
    return dist


def _spot_page(
    *, notice: bool = True, rel_attr: str = "sponsored noopener", prefix: str = ""
) -> str:
    head = f"/{prefix}" if prefix else ""
    return (
        "<html><body><main>"
        + ('<p data-ad-notice>広告を含みます</p>' if notice else "")
        + f'<a href="{head}/go/klook-tickets/spot-tickets/" rel="{rel_attr}">入場券</a>'
        + "</main></body></html>"
    )


def _go_page(url: str = TRACK_URL) -> str:
    # 生成された HTML では & が &amp; になる。これを戻して比べられるかを見る
    escaped = url.replace("&", "&amp;")
    return (
        '<html><head><meta name="robots" content="noindex"></head><body><main>'
        '<p data-ad-notice>広告を含みます</p>'
        f'<a href="{escaped}" rel="sponsored nofollow noopener">進む</a>'
        "</main></body></html>"
    )


def _whole_site(**kwargs) -> dict[str, str]:
    return {
        "spots/takamatsu/a/index.html": _spot_page(**kwargs),
        "en/spots/takamatsu/a/index.html": _spot_page(prefix="en", **kwargs),
        "go/klook-tickets/spot-tickets/index.html": _go_page(),
        "en/go/klook-tickets/spot-tickets/index.html": _go_page(),
    }


REDIRECTS = f"/go/klook-tickets {TRACK_URL} 302\n"


def test_a_correct_placement_passes(tmp_path: Path, ready: affiliates.Offer) -> None:
    dist = _dist(tmp_path, _whole_site(), REDIRECTS)
    problems, summary = ad_check.check(dist, LOCALES)
    assert problems == []
    assert "転送ページ 2 枚" in summary


def test_an_escaped_tracking_url_is_not_read_as_undeclared(
    tmp_path: Path, ready: affiliates.Offer
) -> None:
    """転送ページの href は &amp; になる。戻さずに比べると「宣言していないリンク」になる。"""
    pages = _whole_site()
    assert "&amp;" in pages["go/klook-tickets/spot-tickets/index.html"]
    problems, _ = ad_check.check(_dist(tmp_path, pages, REDIRECTS), LOCALES)
    assert problems == []


def test_a_missing_ad_notice_is_caught(tmp_path: Path, ready: affiliates.Offer) -> None:
    dist = _dist(tmp_path, _whole_site(notice=False), REDIRECTS)
    problems, _ = ad_check.check(dist, LOCALES)
    assert any("広告表記（data-ad-notice）が無い" in p for p in problems)


def test_a_notice_after_the_link_is_caught(tmp_path: Path, ready: affiliates.Offer) -> None:
    pages = _whole_site()
    pages["spots/takamatsu/a/index.html"] = (
        "<html><body><main>"
        '<a href="/go/klook-tickets/spot-tickets/" rel="sponsored">入場券</a>'
        "<p data-ad-notice>広告を含みます</p></main></body></html>"
    )
    problems, _ = ad_check.check(_dist(tmp_path, pages, REDIRECTS), LOCALES)
    assert any("最初の広告リンクより後ろ" in p for p in problems)


def test_a_link_without_sponsored_is_caught(tmp_path: Path, ready: affiliates.Offer) -> None:
    dist = _dist(tmp_path, _whole_site(rel_attr="noopener"), REDIRECTS)
    problems, _ = ad_check.check(dist, LOCALES)
    assert any("sponsored が無い" in p for p in problems)


def test_a_direct_asp_link_outside_the_go_page_is_caught(
    tmp_path: Path, ready: affiliates.Offer
) -> None:
    pages = _whole_site()
    pages["areas/takamatsu/index.html"] = (
        f'<html><body><main><a href="{TRACK_URL}">宿</a></main></body></html>'
    )
    problems, _ = ad_check.check(_dist(tmp_path, pages, REDIRECTS), LOCALES)
    assert any("直リンク" in p for p in problems)


def test_a_page_of_the_placement_without_the_link_is_caught(
    tmp_path: Path, ready: affiliates.Offer
) -> None:
    """枠は接頭辞に一致する全ページに出る。1 枚でも欠ければ落とす。"""
    pages = _whole_site()
    pages["spots/takamatsu/b/index.html"] = "<html><body><main>広告のない施設</main></body></html>"
    problems, _ = ad_check.check(_dist(tmp_path, pages, REDIRECTS), LOCALES)
    assert any("リンクが出ていないページ" in p for p in problems)


def test_an_asp_without_its_rules_copied_is_not_let_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """許可ホストを写していない ASP に計測 URL を入れたら、公開させない。"""
    offer = replace(affiliates.OFFERS[0], url=TRACK_URL)
    monkeypatch.setattr(affiliates, "OFFERS", (offer,))  # ASPS["klook"] は許可ホストが空のまま
    problems, _ = ad_check.check(_dist(tmp_path, _whole_site(), REDIRECTS), LOCALES)
    assert any("許可ホストが未設定" in p for p in problems)


def test_a_redirect_to_another_host_is_caught(tmp_path: Path, ready: affiliates.Offer) -> None:
    dist = _dist(tmp_path, _whole_site(), "/go/klook-tickets https://elsewhere.example/ 302\n")
    problems, _ = ad_check.check(dist, LOCALES)
    assert any("宛先ホスト" in p for p in problems)


def test_nothing_is_published_before_any_contract() -> None:
    """契約前は案件 0 件。枠も転送ページも出ない（ダミーリンクを置かない。ADR 0006）。"""
    assert affiliates.active_offers() == []
    assert affiliates.go_targets(LOCALES) == []
    assert affiliates.redirects() == []
    for placement in affiliates.PLACEMENTS:
        assert affiliates.offers_for(placement.id) == []
