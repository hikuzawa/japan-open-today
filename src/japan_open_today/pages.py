"""ページ生成（ADR 0003）。3 言語ぶんを同じデータから描画する。

事実は構造化データから、文言はロケールのカタログから。ここで文章を作らない（ADR 0005）。
判定（開いているか）は sitemill が計算し、ここは表示のための材料に詰め替えるだけ。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sitemill.assets import Asset, AssetStore
from sitemill.clock import jst_today
from sitemill.embeds.maps import maps_place_embed
from sitemill.i18n import LocaleConfig
from sitemill.i18n.catalog import Catalog, load_catalogs
from sitemill.jpcal import HolidayCalendar
from sitemill.models import OperatorInfo, Page, PageMeta, SourceLink, TrustSignals
from sitemill.openstatus import DayState, DayVerdict
from sitemill.settings import Workspace

from japan_open_today import affiliates
from japan_open_today.areas import AREAS, area
from japan_open_today.data import Dataset
from japan_open_today.schema import Spot
from japan_open_today.verdict import route_verdict, spot_verdict, spot_week

WEEK_DAYS = 7
# Google Maps が使う言語コード。ロケール名とは違う（繁体字は zh-TW）
MAPS_LANGUAGE = {"ja": "ja", "en": "en", "zh-Hant": "zh-TW"}


def _locales(ws: Workspace) -> list[LocaleConfig]:
    return ws.site.locale_list


def _alternates(ws: Workspace, rel: str) -> dict[str, str]:
    """同じ内容の各言語版の url_path。自分自身も含める（sitemill ADR 0016）。"""
    return {lc.code: lc.url_path(rel) for lc in _locales(ws)}


def _path(locale: LocaleConfig, rel: str) -> str:
    return locale.dist_path(f"{rel}index.html" if rel.endswith("/") or not rel else rel)


def _assets(ws: Workspace) -> dict[str, list[Asset]]:
    root = ws.data_dir / "assets"
    out: dict[str, list[Asset]] = {}
    if not root.is_dir():
        return out
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        # 実体のあるものだけを出す。画像の実体は git 管理外なので、日次の実行で取り直せなかった
        # ときは手元に無い（tools/fetch_assets.py）。記録だけを見て <img> を出すと本番で
        # 404 になる。写真が無くても成立する作りなので、その 1 件を落とすほうが正しい
        usable = [
            a
            for a in AssetStore(root, directory.name).load().values()
            if a.usable and a.local_path.is_file()
        ]
        if usable:
            out[directory.name] = usable
    return out


def _trust(
    ws: Workspace,
    *,
    now: datetime,
    sources: list[SourceLink],
    count: int,
    contact: str = "",
) -> TrustSignals:
    return TrustSignals(
        updated_at=now,
        sources=sources,
        operator=OperatorInfo(
            name=ws.site.operator.name,
            # 連絡先はロケールごとに差し替えられる。お問い合わせフォームは 1 つだが、
            # 「返信の言語」を埋めた prefill 付きの URL を言語ごとに持つ
            # （tools/contact_form の setup() が出力する）。無ければ site.toml の 1 本
            contact=contact or ws.site.operator.contact,
            contact_label=ws.site.operator.contact_label,
        ),
        record_count=count,
    )


def _spot_sources(spot: Spot) -> list[SourceLink]:
    fetched = None
    if spot.hours_fetched_at:
        try:
            fetched = datetime.fromisoformat(spot.hours_fetched_at)
        except ValueError:
            fetched = None
    return [SourceLink(label=spot.name("ja"), url=spot.official_url, fetched_at=fetched)]


def _website_node(ws: Workspace, locale: LocaleConfig) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": ws.site.name,
        "url": ws.site.base_url + locale.url_path(""),
        "inLanguage": locale.lang,
    }


def _spot_node(
    ws: Workspace, spot: Spot, locale: LocaleConfig, verdict: DayVerdict
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "TouristAttraction",
        "name": spot.name(locale.code),
        "url": ws.site.base_url + locale.url_path(spot.path()),
        "sameAs": spot.official_url,
    }
    if spot.address.ok:
        node["address"] = spot.address.value
    if spot.phone.ok:
        node["telephone"] = spot.phone.value
    if verdict.periods:
        node["openingHours"] = [
            f"{r.start.strftime('%H:%M')}-{r.end.strftime('%H:%M')}" for r in verdict.periods
        ]
    return node


def _summary(verdicts: list[DayVerdict]) -> dict[str, int]:
    counts = {"open": 0, "closed": 0, "unknown": 0}
    for v in verdicts:
        counts[v.state.value] += 1
    return counts


def _wording(ws: Workspace) -> dict[str, Catalog]:
    """画面の文言カタログ。`<head>` の文言（説明文・ページ名）もここから採る。

    `site.toml` の `description` は 1 つしか持てないので、そのままだと英語ページと
    繁体字ページに日本語の説明文が出る。検索結果に出るのはこの文で、台湾や英語圏の
    利用者が読むのもこの文なので、ロケール別のカタログから採る（ADR 0005）。
    """
    return load_catalogs(
        ws.i18n_dir,
        [loc.code for loc in ws.site.locales],
        default_code=ws.site.default_locale.code,
    )


def _say(words: dict[str, Catalog], locale: LocaleConfig, key: str, default: str = "") -> str:
    catalog = words.get(locale.code)
    if catalog is None or not catalog.has(key):
        return default
    return catalog.get(key)


def build_pages(ws: Workspace, ds: Dataset, *, now: datetime) -> list[Page]:
    today = jst_today(now)
    holidays = HolidayCalendar.load(ws.data_dir / "reference" / "syukujitsu.csv")
    stale_after = ws.site.crawl.stale_after_days
    assets = _assets(ws)
    locales = _locales(ws)
    words = _wording(ws)

    verdicts = {
        s.spot_id: spot_verdict(s, today, holidays=holidays, stale_after_days=stale_after)
        for s in ds.spots
    }
    weeks = {
        s.spot_id: spot_week(
            s, today, days=WEEK_DAYS, holidays=holidays, stale_after_days=stale_after
        )
        for s in ds.spots
    }
    route_states = {
        r.route_id: route_verdict(r, today, holidays=holidays, stale_after_days=stale_after)
        for r in ds.routes
    }
    counts = _summary(list(verdicts.values()))
    all_sources = [SourceLink(label=s.name("ja"), url=s.official_url) for s in ds.spots[:6]]
    pages: list[Page] = []

    for locale in locales:
        contact = _say(words, locale, "contact.url")
        # --- トップ -----------------------------------------------------
        rows = [
            _spot_row(ws, spot, locale, verdicts[spot.spot_id], assets)
            for spot in sorted(ds.spots, key=lambda s: (s.area, s.spot_id))
        ]
        pages.append(
            Page(
                meta=PageMeta(
                    # トップの <title> は「<タグライン> | <サイト名>」。サイト名を 2 回
                    # 並べても検索結果で情報が増えない（英語版が「Japan Open Today | Japan
                    # Open Today」になっていた）
                    title=_say(words, locale, "site.tagline", ws.site.name),
                    description=_say(words, locale, "site.description", ws.site.description),
                    path=_path(locale, ""),
                    locale=locale.code,
                    alternates=_alternates(ws, ""),
                    structured_data=[_website_node(ws, locale)],
                    priority=1.0,
                    changefreq="daily",
                ),
                template="index.html",
                context={
                    "today": today,
                    "counts": counts,
                    "rows": rows,
                    "areas": [a for a in AREAS if a.slug in ds.spots_by_area],
                    "spot_total": len(ds.spots),
                },
                trust=_trust(
                    ws, now=now, sources=all_sources, count=len(ds.spots), contact=contact
                ),
            )
        )

        # --- エリア -----------------------------------------------------
        for a in AREAS:
            spots = ds.spots_in(a.slug)
            if not spots:
                continue
            rel = f"areas/{a.slug}/"
            pages.append(
                Page(
                    meta=PageMeta(
                        title=a.name(locale.code),
                        description=_say(words, locale, "site.description", ws.site.description),
                        path=_path(locale, rel),
                        locale=locale.code,
                        alternates=_alternates(ws, rel),
                        structured_data=[_website_node(ws, locale)],
                        changefreq="daily",
                    ),
                    template="area.html",
                    context={
                        "today": today,
                        "area": a,
                        "rows": [
                            _spot_row(ws, s, locale, verdicts[s.spot_id], assets) for s in spots
                        ],
                        "counts": _summary([verdicts[s.spot_id] for s in spots]),
                        "offers": affiliates.offers_for("area-stay"),
                    },
                    trust=_trust(
                        ws,
                        now=now,
                        sources=[SourceLink(label=s.name("ja"), url=s.official_url) for s in spots],
                        count=len(spots),
                        contact=contact,
                    ),
                )
            )

        # --- 施設 -------------------------------------------------------
        for spot in ds.spots:
            rel = spot.path()
            verdict = verdicts[spot.spot_id]
            nearby = _nearby_open(ws, ds, spot, locale, verdicts, assets)
            pages.append(
                Page(
                    meta=PageMeta(
                        title=spot.name(locale.code),
                        description=_say(words, locale, "site.description", ws.site.description),
                        path=_path(locale, rel),
                        locale=locale.code,
                        alternates=_alternates(ws, rel),
                        structured_data=[_spot_node(ws, spot, locale, verdict)],
                        changefreq="daily",
                        priority=0.8,
                    ),
                    template="spot.html",
                    context={
                        "today": today,
                        "spot": spot,
                        "verdict": verdict,
                        "week": weeks[spot.spot_id],
                        "fees": spot.fees,
                        "photo": (assets.get(spot.spot_id) or [None])[0],
                        "map": maps_place_embed(
                            spot.map_query or spot.name("ja"),
                            api_key=ws.secrets.google_maps_embed_key,
                            title=spot.name(locale.code),
                            language=MAPS_LANGUAGE.get(locale.code, locale.code),
                        ),
                        "area": area(spot.area),
                        "nearby": nearby,
                        "closures_label": _closures_label(spot),
                        "no_hours_stated": no_hours_stated(spot),
                        "offers": affiliates.offers_for("spot-tickets"),
                        "week_all_unknown": all(
                            v.state is DayState.unknown for v in weeks[spot.spot_id]
                        ),
                    },
                    trust=_trust(
                        ws, now=now, sources=_spot_sources(spot), count=1, contact=contact
                    ),
                )
            )

        # --- 交通 -------------------------------------------------------
        rel = "transport/"
        pages.append(
            Page(
                meta=PageMeta(
                    title=_say(words, locale, "nav.transport", "Transport"),
                    description=_say(words, locale, "site.description", ws.site.description),
                    path=_path(locale, rel),
                    locale=locale.code,
                    alternates=_alternates(ws, rel),
                    structured_data=[_website_node(ws, locale)],
                    changefreq="daily",
                ),
                template="transport.html",
                context={
                    "today": today,
                    "operators": [
                        {
                            "operator": op,
                            "routes": [
                                {"route": r, "verdict": route_states[r.route_id]}
                                for r in ds.routes_of(op.operator_id)
                            ],
                        }
                        for op in ds.operators
                    ],
                },
                trust=_trust(
                    ws,
                    now=now,
                    sources=[
                        SourceLink(label=op.name("ja"), url=op.official_url) for op in ds.operators
                    ],
                    count=len(ds.routes),
                    contact=contact,
                ),
            )
        )

        # --- 運営者情報・データについて ---------------------------------
        for rel, template, key, title in (
            ("about/", "about.html", "nav.about", "About"),
            ("data/", "data.html", "nav.data", "Data"),
            # プライバシーポリシー。実際にしていること（フォーム・AI・解析・地図の埋め込み）を
            # 3 言語で出す。アフィリエイトの審査でも求められる
            ("privacy/", "privacy.html", "nav.privacy", "Privacy"),
        ):
            pages.append(
                Page(
                    meta=PageMeta(
                        title=_say(words, locale, key, title),
                        description=_say(words, locale, "site.description", ws.site.description),
                        path=_path(locale, rel),
                        locale=locale.code,
                        alternates=_alternates(ws, rel),
                        structured_data=[_website_node(ws, locale)],
                        priority=0.3,
                        changefreq="monthly",
                    ),
                    template=template,
                    context={
                        "today": today,
                        "sources": ds.sources,
                        "spots": ds.spots,
                        "routes": ds.routes,
                        "assets": assets,
                        "holidays": holidays,
                        "user_agent": ws.site.user_agent,
                        # そのロケールの prefill 付き URL（無ければ site.toml の素の URL）
                        "contact": contact or ws.site.operator.contact,
                        # 地図を埋め込むのは鍵があるときだけ。無いときは外部リンクなので、
                        # プライバシーポリシーの書き方も変わる
                        "has_maps": bool(ws.secrets.google_maps_embed_key),
                        "analytics": bool(ws.secrets.cf_web_analytics_token),
                        # 広告はまだ 1 枠も出していない（ADR 0006）。出す日にここが True になる
                        "ads_live": False,
                    },
                    trust=_trust(
                        ws, now=now, sources=all_sources, count=len(ds.spots), contact=contact
                    ),
                )
            )

    # 広告の転送ページ（ADR 0012）。契約前は案件が 0 件なので 1 枚も出ない。
    # ロケールごとに 1 枚出す（英語の利用者に日本語の転送ページを見せない）
    locale_pairs = tuple((lc.code, lc.path) for lc in locales)
    for target in affiliates.go_targets(locale_pairs):
        locale = ws.site.locale(target.locale)
        rel = f"go/{target.offer.id}/{target.placement.id}/"
        pages.append(
            Page(
                meta=PageMeta(
                    title=_say(words, locale, "affiliate.go_heading", "広告"),
                    description="",
                    path=_path(locale, rel),
                    locale=locale.code,
                    noindex=True,  # sitemap にも出さない
                ),
                template="go.html",
                context={"today": today, "offer": target.offer},
                trust=_trust(ws, now=now, sources=all_sources, count=len(ds.spots)),
            )
        )

    # 404 は既定ロケールだけ（noindex なので各言語版は要らない）
    default = ws.site.default_locale
    pages.append(
        Page(
            meta=PageMeta(
                title="404",
                path="404.html",
                noindex=True,
                locale=default.code,
            ),
            template="404.html",
            context={"today": today},
            trust=_trust(ws, now=now, sources=all_sources, count=len(ds.spots)),
        )
    )
    return pages


def _nearby_open(
    ws: Workspace,
    ds: Dataset,
    spot: Spot,
    locale: LocaleConfig,
    verdicts: dict[str, DayVerdict],
    assets: dict[str, list[Asset]],
    limit: int = 3,
) -> list[dict[str, Any]]:
    """その日「開いていると分かっている」近くの施設（ADR 0003 追記）。

    不明のページに次の一手を置くために使う。同じエリアを優先し、足りなければ他のエリアから足す。
    **判定が open のものだけ**を出す。不明の施設を並べても、不明が増えるだけで役に立たない。
    """

    def pick(spots: list[Spot]) -> list[Spot]:
        return [
            s
            for s in spots
            if s.spot_id != spot.spot_id and verdicts[s.spot_id].state is DayState.open
        ]

    chosen = pick(ds.spots_in(spot.area))
    if len(chosen) < limit:
        others = [s for s in pick(ds.spots) if s.area != spot.area]
        chosen = [*chosen, *others]
    return [_spot_row(ws, s, locale, verdicts[s.spot_id], assets) for s in chosen[:limit]]


def _spot_row(
    ws: Workspace,
    spot: Spot,
    locale: LocaleConfig,
    verdict: DayVerdict,
    assets: dict[str, list[Asset]],
) -> dict[str, Any]:
    """一覧に出すカード 1 枚。"""
    return {
        "spot": spot,
        "name": spot.name(locale.code),
        "url": locale.url_path(spot.path()),
        "verdict": verdict,
        "area": area(spot.area),
        "photo": (assets.get(spot.spot_id) or [None])[0],
    }


def search_index(ws: Workspace, ds: Dataset, *, now: datetime | None = None) -> dict[str, Any]:
    """ロケールごとの検索索引（ADR 0003）。判定は生成時点の値を入れる。"""
    today = jst_today(now)
    holidays = HolidayCalendar.load(ws.data_dir / "reference" / "syukujitsu.csv")
    stale_after = ws.site.crawl.stale_after_days
    out: dict[str, Any] = {}
    for locale in _locales(ws):
        rows = []
        for spot in ds.spots:
            verdict = spot_verdict(spot, today, holidays=holidays, stale_after_days=stale_after)
            rows.append(
                {
                    "id": spot.spot_id,
                    "name": spot.name(locale.code),
                    "area": spot.area,
                    "category": spot.category,
                    "url": locale.url_path(spot.path()),
                    "state": verdict.state.value,
                    "fee": next(
                        (
                            f.amount.value
                            for f in spot.fees
                            if f.category == "adult" and f.amount.ok
                        ),
                        None,
                    ),
                }
            )
        out[f"{locale.code}.json"] = {"generated_for": today.isoformat(), "spots": rows}
    return out


def no_hours_stated(spot: Spot) -> bool:
    """「時間の定めが無い屋外の場所」か（ADR 0011）。

    砂浜や境内は閉まる時間が無く、公式ページも時間を書かない。これを「不明」と出すのは
    事実に合わず、利用者も行動を決められない。**時間の定めが記載されていない**ことを
    そのまま伝える（開いていると断定はしない）。

    条件は「屋外と宣言されている」「時間も定休日規則も取れていない」「告知も出ていない」。
    告知（臨時の立入禁止など）があるときは、その告知を出すほうが先なのでこの表示はしない。
    """
    return (
        spot.spot_type == "open_air" and not spot.hours and not spot.closures and not spot.notices
    )


def _closures_label(spot: Spot) -> str | None:
    """定休日の表示に使う文言のキー。規則が無くても「年中無休」は事実なので出す。"""
    if spot.closes_never:
        return "closures.always_open"
    return None


def state_label_key(state: DayState) -> str:
    return f"state.{state.value}"


def week_dates(today: date, days: int = WEEK_DAYS) -> list[date]:
    return [today + timedelta(days=i) for i in range(days)]
