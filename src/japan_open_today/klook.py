# ruff: noqa: E501  (URL は途中で切らない)
"""Klook の飛び先の対応表（ADR 0012 追記、2026-09-22）。

施設ページの「宿・体験」枠から Klook のどのページへ送るかを、施設ごとに決める。

**URL は推測で組み立てない。** Klook の robots.txt は全ボットに `*/search/*` を禁じているので、
検索結果は辿らない（素の HTTP は 403 で拒まれもする）。代わりに Klook 自身のサイトマップ
（robots.txt が `Sitemap:` で示すもの）→ 目的地・商品のページ → そのページの hreflang、と
**リンクだけで**辿ったものを置く。英語・繁体字の URL も、各ページの `<link rel=alternate>` から取った。

飛び先は 3 段で決める（運営者が決めた。2026-09-22）。

1. **施設の名前が商品名にそのまま出る商品**があれば、その商品（3 文字以上の施設名が商品名に
   含まれること。巡回の `spot_detail` の門と同じ考え方）。ツアーが施設を訪ねるだけのものも
   あるので、文言は「〇〇を訪ねるツアー・体験」にして、入場券のようには見せない
2. 無ければ、直島町の施設は Klook の「香川郡」の目的地ページ（直島のツアーが並ぶ。文言は
   「直島のツアー」）、高松市の施設（女木島・男木島を含む）は「高松市」の目的地ページ
3. それ以外は、運営者がブラウザで取った「香川」の検索結果ページ

地中美術館のように Klook に商品が無い施設へ「予約」を出さないこと。Klook の地中美術館の
ページに載っていた商品は、岡山の夜光虫ツアー 1 件だけだった（2026-09-22）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

# 提携 ID。公開の広告リンクに載る値で、秘密ではない（2026-09-22 承認）
AID = "135769"
# 計測されるのは www.klook.com だけ。短縮形の s.klook.com は計測されない
HOST = "www.klook.com"
CHECKED_ON = "2026-09-22"
# このサイトのロケール → Klook の hreflang で取った URL の言語
LOCALES = ("ja", "en", "zh-Hant")


@dataclass(frozen=True)
class Landing:
    """飛び先 1 つ。`urls` はロケールごとの URL（aid を付ける前の、辿って得た形のまま）。"""

    id: str
    label_key: str  # i18n の affiliate.<label_key>
    urls: dict[str, str] = field(default_factory=dict)
    title: str = ""  # Klook 側の表題（日本語）。対応の根拠
    found_via: str = ""  # どこから辿ったか
    spots: tuple[str, ...] = ()  # 施設の商品（段 1）のとき、対応する施設
    areas: tuple[str, ...] = ()  # エリアの飛び先（段 2）のとき、対象のエリア

    @property
    def complete(self) -> bool:
        """全ロケールの URL が揃っているか。揃うまで公開しない。"""
        return all(self.urls.get(code) for code in LOCALES)

    def url(self, locale: str) -> str:
        """計測付きの URL。無いロケールは日本語の URL に落とす。"""
        return with_aid(self.urls.get(locale) or self.urls["ja"])


def with_aid(url: str) -> str:
    """`?aid=` を付ける（すでにクエリがあれば `&aid=`）。手で書かない。"""
    if not url:
        return url
    query = parse_qs(urlsplit(url).query)
    if query.get("aid") == [AID]:
        return url
    return f"{url}{'&' if urlsplit(url).query else '?'}aid={AID}"


def has_aid(url: str) -> bool:
    return parse_qs(urlsplit(url).query).get("aid") == [AID]


SITEMAP_ACTIVITY = "https://www.klook.com/sitemap-experiences-activity-plain_ja.xml"
SITEMAP_CITY = "https://www.klook.com/sitemap-city-plain_ja.xml"

# 段 1: 施設の名前が商品名に出る商品
PRODUCTS: tuple[Landing, ...] = (
    Landing(
        id="chichibugahama-sunset-shuttle",
        label_key="klook_product",
        title="【香川・琴平/三豊】琴平発⇔父母ヶ浜夕日シャトルバス",
        found_via=SITEMAP_ACTIVITY,
        spots=("chichibugahama",),
        urls={
            "ja": "https://www.klook.com/ja/activity/168270-kagawa-mitoyo-kotohira-chichibugahama-sunset-shuttle-bus/",
            "en": "https://www.klook.com/activity/168270-kagawa-mitoyo-kotohira-chichibugahama-sunset-shuttle-bus/",
            "zh-Hant": "https://www.klook.com/zh-TW/activity/168270-kagawa-mitoyo-kotohira-chichibugahama-sunset-shuttle-bus/",
        },
    ),
    Landing(
        id="takamatsu-charter-konpira-aquarium",
        label_key="klook_product",
        title="高松チャーター一日観光：金刀比羅宮/高屋神社/鳴門の渦潮/四国水族館（旅程は自由に組み合わせ可能）",
        found_via=SITEMAP_ACTIVITY,
        spots=("konpira", "shikoku-aquarium"),
        urls={
            "ja": "https://www.klook.com/ja/activity/90937-takamatsu-departure-1-day-customize-charter-car-tour/",
            "en": "https://www.klook.com/activity/90937-takamatsu-departure-1-day-customize-charter-car-tour/",
            "zh-Hant": "https://www.klook.com/zh-TW/activity/90937-takamatsu-departure-1-day-customize-charter-car-tour/",
        },
    ),
)

# 段 2・3: 商品が無い施設の飛び先
FALLBACKS: tuple[Landing, ...] = (
    Landing(
        id="naoshima-tours",
        label_key="klook_naoshima",
        # Klook の「香川郡」（県ではなく郡。直島町を含む）。並ぶのは直島のツアーがほとんど
        title="香川郡（Klook の目的地ページ）",
        found_via=SITEMAP_CITY,
        areas=("naoshima",),  # 直島町
        urls={
            "ja": "https://www.klook.com/ja/destination/c31301-kagawa/",
            "en": "https://www.klook.com/destination/c31301-kagawa/",
            "zh-Hant": "https://www.klook.com/zh-TW/destination/c31301-kagawa/",
        },
    ),
    Landing(
        id="takamatsu",
        label_key="klook_takamatsu",
        title="高松市（Klook の目的地ページ）",
        found_via=SITEMAP_CITY,
        areas=("takamatsu", "megijima"),  # 高松市。女木島・男木島は高松市
        urls={
            "ja": "https://www.klook.com/ja/destination/c15969-takamatsu/",
            "en": "https://www.klook.com/destination/c15969-takamatsu/",
            "zh-Hant": "https://www.klook.com/zh-TW/destination/c15969-takamatsu/",
        },
    ),
    Landing(
        id="kagawa",
        label_key="klook_kagawa",
        # 運営者がブラウザで取った「香川」の検索結果。robots.txt が検索を禁じているので、
        # こちらからは取りに行かない（週次の点検でも開かず、手で確かめる項目として出す）。
        # **URL を受け取るまで空。空のあいだは Klook の案件全体を公開しない**
        title="香川の検索結果（運営者がブラウザで取った URL）",
        found_via="運営者のブラウザ",
        urls={},
    ),
)
BY_ID = {landing.id: landing for landing in (*PRODUCTS, *FALLBACKS)}
DEFAULT = "kagawa"


def landing_for(spot_id: str, area: str) -> Landing:
    """その施設の飛び先。商品 → エリア → 香川の順に選ぶ。"""
    for product in PRODUCTS:
        if spot_id in product.spots:
            return product
    for fallback in FALLBACKS:
        if area in fallback.areas:
            return fallback
    return BY_ID[DEFAULT]


def landings() -> tuple[Landing, ...]:
    return (*PRODUCTS, *FALLBACKS)


def ready() -> bool:
    """すべての飛び先が全ロケール揃っているか。1 つでも欠ければ公開しない。"""
    return all(landing.complete for landing in landings())
