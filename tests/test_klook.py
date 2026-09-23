"""Klook の飛び先（ADR 0012 追記、2026-09-22）。外部アクセスはしない。"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from japan_open_today import ad_check, affiliates, klook

LOCALES = (("ja", ""), ("en", "en"), ("zh-Hant", "zh-hant"))
SEARCH = "https://www.klook.com/ja/search/result/?query=%E9%A6%99%E5%B7%9D"


@pytest.mark.parametrize(
    ("spot_id", "area", "expected"),
    [
        ("chichibugahama", "mitoyo", "chichibugahama-sunset-shuttle"),  # 商品名に「父母ヶ浜」
        ("konpira", "kotohira", "takamatsu-charter-konpira-aquarium"),  # 商品名に「金刀比羅宮」
        ("chichu", "naoshima", "naoshima-tours"),  # Klook に商品が無い。直島町 → 香川郡
        ("ritsurin", "takamatsu", "takamatsu"),  # 高松市
        ("ogijima-x", "megijima", "takamatsu"),  # 女木島・男木島は高松市
        ("kankakei", "shodoshima", "kagawa"),  # それ以外 → 香川の検索結果
    ],
)
def test_each_spot_gets_the_narrowest_honest_landing(
    spot_id: str, area: str, expected: str
) -> None:
    assert klook.landing_for(spot_id, area).id == expected


def test_chichu_is_never_sent_to_a_booking() -> None:
    """地中美術館の予約は公式だけ。Klook の地中美術館のページには無関係な商品しか無かった。"""
    landing = klook.landing_for("chichu", "naoshima")
    assert landing.label_key == "klook_naoshima"
    assert "chichu" not in {s for product in klook.PRODUCTS for s in product.spots}


def test_every_known_url_is_on_the_tracked_host() -> None:
    """計測されるのは www.klook.com だけ（s.klook.com は計測されない）。"""
    for landing in klook.landings():
        for url in landing.urls.values():
            assert url.startswith(f"https://{klook.HOST}/"), url


def test_the_aid_is_added_by_code() -> None:
    assert klook.with_aid("https://www.klook.com/ja/destination/c15969-takamatsu/") == (
        "https://www.klook.com/ja/destination/c15969-takamatsu/?aid=135769"
    )
    assert klook.with_aid(SEARCH).endswith("&aid=135769")
    assert klook.with_aid(klook.with_aid(SEARCH)) == klook.with_aid(SEARCH)  # 二重に付けない
    assert klook.has_aid(klook.with_aid(SEARCH))


def test_publishing_needs_the_terms_and_every_landing(monkeypatch: pytest.MonkeyPatch) -> None:
    """公開の条件は「規約を写した日」と「全ロケールの飛び先」。どちらが欠けても出さない。"""
    offer = next(o for o in affiliates.OFFERS if o.id == "klook-tickets")
    assert offer.ready  # 2026-09-23 に両方そろった
    assert affiliates.ASPS["klook"].terms_checked_on == "2026-09-23"
    assert all(landing.complete for landing in klook.landings())

    no_terms = replace(affiliates.ASPS["klook"], terms_checked_on="")
    monkeypatch.setitem(affiliates.ASPS, "klook", no_terms)
    assert not offer.ready  # 規約を写す前には戻せない

    monkeypatch.setitem(affiliates.ASPS, "klook", affiliates.ASPS["klook"])
    half = replace(klook.BY_ID["kagawa"], urls={"ja": SEARCH})
    assert not replace(offer, landings=(half,)).ready  # 英語・繁体字が欠けたら出さない


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> affiliates.Offer:
    """規約と検索結果 URL が入った状態を作る（公開の直前の形）。"""
    kagawa = replace(klook.BY_ID["kagawa"], urls={"ja": SEARCH, "en": SEARCH, "zh-Hant": SEARCH})
    landings = tuple(kagawa if landing.id == "kagawa" else landing for landing in klook.landings())
    monkeypatch.setattr(klook, "BY_ID", {**klook.BY_ID, "kagawa": kagawa})
    monkeypatch.setattr(klook, "FALLBACKS", tuple(x for x in landings if not x.spots))
    offer = replace(
        next(o for o in affiliates.OFFERS if o.id == "klook-tickets"),
        url=kagawa.url("ja"),
        landings=landings,
    )
    monkeypatch.setattr(affiliates, "OFFERS", (offer,))
    monkeypatch.setitem(
        affiliates.ASPS, "klook", replace(affiliates.ASPS["klook"], terms_checked_on="2026-09-22")
    )
    return offer


def test_one_transfer_page_per_landing_and_locale(live: affiliates.Offer) -> None:
    targets = affiliates.go_targets(LOCALES)
    assert len(targets) == len(klook.landings()) * len(LOCALES)
    paths = {t.url_path for t in targets}
    assert "/go/klook-tickets/spot-tickets/naoshima-tours/" in paths
    assert "/zh-hant/go/klook-tickets/spot-tickets/chichibugahama-sunset-shuttle/" in paths
    for t in targets:
        assert klook.has_aid(t.target_url), t.target_url
    en = next(t for t in targets if t.locale == "en" and t.landing.id == "takamatsu")
    assert en.target_url == "https://www.klook.com/destination/c15969-takamatsu/?aid=135769"


def _page(href: str) -> str:
    return (
        "<html><body><main><p data-ad-notice>広告を含みます</p>"
        f'<a href="{href}" rel="sponsored noopener">Klook</a></main></body></html>'
    )


def _go(url: str) -> str:
    return (
        '<html><head><meta name="robots" content="noindex"></head><body><main>'
        f'<p data-ad-notice>広告</p><a href="{url.replace("&", "&amp;")}" '
        'rel="sponsored nofollow noopener">進む</a></main></body></html>'
    )


def _dist(tmp_path: Path, *, drop_aid: str = "") -> Path:
    dist = tmp_path / "dist"
    pages: dict[str, str] = {}
    for _code, prefix in LOCALES:
        head = f"{prefix}/" if prefix else ""
        pages[f"{head}spots/naoshima/chichu/index.html"] = _page(
            f"/{head}go/klook-tickets/spot-tickets/naoshima-tours/"
        )
        pages[f"{head}spots/mitoyo/chichibugahama/index.html"] = _page(
            f"/{head}go/klook-tickets/spot-tickets/chichibugahama-sunset-shuttle/"
        )
    for t in affiliates.go_targets(LOCALES):
        url = t.target_url
        if t.landing.id == drop_aid:
            url = url.split("?aid=")[0].split("&aid=")[0]
        pages[t.url_path.strip("/") + "/index.html"] = _go(url)
    for rel, html in pages.items():
        path = dist / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
    offer = affiliates.OFFERS[0]
    (dist / "_redirects").write_text(f"/go/klook-tickets {offer.url} 302\n", encoding="utf-8")
    return dist


def test_the_check_passes_when_each_spot_points_at_one_landing(
    tmp_path: Path, live: affiliates.Offer
) -> None:
    problems, _ = ad_check.check(_dist(tmp_path), LOCALES)
    assert problems == []


def test_a_transfer_page_without_the_aid_is_caught(tmp_path: Path, live: affiliates.Offer) -> None:
    problems, _ = ad_check.check(_dist(tmp_path, drop_aid="takamatsu"), LOCALES)
    assert any("aid=135769 が無い" in p for p in problems)


def test_the_brand_is_kept_out_of_search_facing_text(
    tmp_path: Path, live: affiliates.Offer
) -> None:
    """題名・説明文・見出し・構造化データに Klook の名前を入れない（規約 5.2(a) の SEO）。"""
    dist = _dist(tmp_path)
    page = dist / "spots/naoshima/chichu/index.html"
    page.write_text(
        "<html><head><title>地中美術館の Klook 予約</title></head><body><main>"
        '<p data-ad-notice>広告</p><a href="/go/klook-tickets/spot-tickets/naoshima-tours/" '
        'rel="sponsored noopener">Klook</a></main></body></html>',
        encoding="utf-8",
    )
    problems, _ = ad_check.check(dist, LOCALES)
    assert any("<title> に Klook の名前が入っている" in p for p in problems)
