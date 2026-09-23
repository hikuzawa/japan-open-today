"""ページ生成（ADR 0003）。3 言語ぶんを同じデータから描画する。

事実は構造化データから、文言はロケールのカタログから。ここで文章を作らない（ADR 0005）。
判定（開いているか）は sitemill が計算し、ここは表示のための材料に詰め替えるだけ。
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Any

from sitemill.assets import Asset, AssetStore
from sitemill.clock import jst_today
from sitemill.embeds.maps import maps_place_embed
from sitemill.i18n import LocaleConfig
from sitemill.i18n.catalog import Catalog, load_catalogs
from sitemill.i18n.format import fmt_for, format_time, format_time_range
from sitemill.jpcal import HolidayCalendar
from sitemill.models import OperatorInfo, Page, PageMeta, SourceLink, TrustSignals
from sitemill.openstatus import DayState, DayVerdict
from sitemill.settings import Workspace

from japan_open_today import affiliates
from japan_open_today.areas import AREAS, area
from japan_open_today.data import Dataset
from japan_open_today.schema import Spot
from japan_open_today.verdict import (
    hours_unrepresentable,
    route_verdict,
    spot_verdict,
    spot_week,
)

WEEK_DAYS = 7
# Google Maps が使う言語コード。ロケール名とは違う（繁体字は zh-TW）
MAPS_LANGUAGE = {"ja": "ja", "en": "en", "zh-Hant": "zh-TW"}
# 施設ページに出す「同じエリアのほかの施設」の数（固定のリンク。クロールの経路にする）
AREA_LINKS = 6


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


def _summary(spots: list[Spot], verdicts: dict[str, DayVerdict]) -> dict[str, int]:
    """その日の内訳。「不明」を 2 つに割る。

    屋外で時間の定めが無い場所（砂浜・境内）と、公式ページに記載が無い施設は、利用者に
    とって意味が違う。前者は「いつでも行ける」に近く、後者は「分からない」。
    まとめて「不明」と出すと、分からない施設が実態より多く見える（281 件中 138 → 18）。
    """
    counts = {"open": 0, "no_hours": 0, "unknown": 0, "closed": 0}
    for spot in spots:
        verdict = verdicts[spot.spot_id]
        if verdict.state is DayState.unknown and no_hours_stated(spot):
            counts["no_hours"] += 1
        else:
            counts[verdict.state.value] += 1
    return counts


def _routes_for(ds: Dataset, area_slug: str, states: dict[str, Any]) -> list[dict[str, Any]]:
    """そのエリアに来る航路・路線（`Route.areas` の宣言で決まる）。

    島の施設を見ている人が、そこへ渡る船に辿り着けるようにする。無宣言の路線は出さない
    （着発地の文字列から推測すると、別の島の船を出す）。
    """
    return [{"route": r, "verdict": states[r.route_id]} for r in ds.routes if area_slug in r.areas]


# トップに出す「今日開いている施設」の上限。全件はエリアページで見る
TOP_OPEN = 24


def _open_today(rows: list[Any], limit: int = TOP_OPEN) -> list[Any]:
    """開いている施設を、エリアを順に回しながら拾う。

    エリア順にそのまま切ると、先頭のエリアだけで埋まる（高松で 24 件）。
    1 件ずつ回して混ぜると、島も県西部も入口に出る。
    """
    by_area: dict[str, list[Any]] = {}
    for row in rows:
        if row["verdict"].state is DayState.open:
            by_area.setdefault(row["area"].slug, []).append(row)
    out: list[Any] = []
    while len(out) < limit and any(by_area.values()):
        for slug in list(by_area):
            if by_area[slug]:
                out.append(by_area[slug].pop(0))
            if len(out) >= limit:
                break
    return out


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
    counts = _summary(ds.spots, verdicts)
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
                    # トップの問いは「今日どこへ行けるか」。答えから並べ、全件はエリアへ送る
                    "rows": _open_today(rows),
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
                        "counts": _summary(spots, verdicts),
                        "routes": _routes_for(ds, a.slug, route_states),
                        "offers": affiliates.offers_for("area-stay"),
                        "ad_links": [
                            affiliates.slot_link(o, "area-stay", locale.path)
                            for o in affiliates.offers_for("area-stay")
                        ],
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
                        "area_links": _area_links(ds, spot, locale),
                        "area_total": len(ds.spots_in(spot.area)),
                        "hours": _hours_display(spot, words, locale),
                        "notices": _current_notices(spot, today),
                        "closures_label": _closures_label(spot),
                        "no_hours_stated": no_hours_stated(spot),
                        "offers": affiliates.offers_for("spot-tickets"),
                        # 施設ごとに飛び先を選ぶ（Klook。klook.landing_for）
                        "ad_links": [
                            affiliates.slot_link(o, "spot-tickets", locale.path, spot)
                            for o in affiliates.offers_for("spot-tickets")
                        ],
                        "routes": _routes_for(ds, spot.area, route_states),
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
                        # 名前を analytics にすると、ビルドの共通変数（解析の埋め込み）を
                        # ページの文脈で上書きしてしまい、<head> に "False" が出る
                        "analytics_on": bool(ws.secrets.cf_web_analytics_token),
                        # 広告を 1 枠でも出していれば開示文に切り替える（ADR 0006）。
                        # 案件が公開の条件（規約・計測 URL・飛び先）を満たした日に自動で True になる
                        "ads_live": bool(affiliates.active_offers()),
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
        rel = target.rel
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
                context={
                    "today": today,
                    "offer": target.offer,
                    "target_url": target.target_url,
                    "has_landing": target.landing is not None,
                    # 計測ビーコンが出ないときは待たずに転送する（待っても数えられない）
                    "analytics_on": bool(ws.secrets.cf_web_analytics_token),
                },
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


def _area_links(
    ds: Dataset, spot: Spot, locale: LocaleConfig, limit: int = AREA_LINKS
) -> list[dict[str, str]]:
    """同じエリアのほかの施設への**固定の**リンク（その日の判定に左右されない）。

    「近くで開いている施設」は今日開いている所だけなので、日によって変わり、空の日もある。
    クロールの経路としては安定しないので、別に置く。同じ種類を先に、あとは日本語名の順。
    """
    others = [s for s in ds.spots_in(spot.area) if s.spot_id != spot.spot_id]
    others.sort(key=lambda s: (s.category != spot.category, s.name("ja"), s.spot_id))
    return [{"name": s.name(locale.code), "url": locale.url_path(s.path())} for s in others[:limit]]


def _current_notices(spot: Spot, today: date) -> list[dict[str, Any]]:
    """今日以降に効く告知（臨時休業・臨時開館・時間の変更）。終わった告知は出さない。

    日付は告知の期間をそのまま出す（「明日から」のような相対表現にしない）。原文の引用を必ず添える。
    """
    rows = []
    for notice in sorted(spot.notices, key=lambda n: (n.span.start, n.span.end)):
        if notice.span.end < today:
            continue
        rows.append(
            {
                "kind": notice.kind.value,
                "start": notice.span.start,
                "end": notice.span.end,
                "reason": notice.reason or "",
                "quote": notice.evidence.quote if notice.evidence else "",
            }
        )
    return rows


def _hours_structurable(spot: Spot) -> bool:
    """規則を条件つきの時刻として出してよいか。

    1 つの期間に時間帯が複数あると、どの時間帯がいつのものかが構造化データから消えている
    （栗林公園は月ごとの 12 通りが 1 つの期間にある）。同じ条件の期間が並ぶと、どちらが何の
    時間か分からない（金刀比羅宮の「参拝時間」と「宝物館」）。そういう施設は原文だけを出す。

    「第 2・4 水曜日」のような第 n 週の指定は、曜日の選び方で表せず「毎週水曜」に落ちている
    （木太町の盆栽の施設）。原文に第 n 週があれば、構造化した行は出さない。
    """
    if hours_unrepresentable(spot):
        return False
    keys = []
    for p in spot.hours:
        if not p.always_open and len(p.ranges) != 1:
            return False
        season = p.season
        keys.append(
            (
                p.days.weekdays,
                p.days.include_holidays,
                p.days.exclude_holidays,
                (season.start_month, season.start_day, season.end_month, season.end_day)
                if season
                else None,
            )
        )
    return len(keys) == len(set(keys))


def _hours_display(spot: Spot, words: dict[str, Catalog], locale: LocaleConfig) -> dict | None:
    """通常の営業時間（規則）。日付で変わらない事実として、事実の表に出す。

    今日の判定と週の帯は日付から決まる表示なので、規則そのものは別に書く。原文の引用は必ず添える。
    """
    if not spot.hours:
        return None
    fmt = fmt_for(locale.code)

    def say(key: str, **params: object) -> str:
        catalog = words.get(locale.code)
        return catalog.get(key, **params) if catalog is not None else key

    def month_day(month: int, day: int, *, end: bool = False) -> str:
        mon = fmt.months[month - 1][:3] if fmt.months else str(month)
        # 「〜6月」は月末まで。読み取りは 31 日として持っているので、6 月 31 日と出さない
        if end and day >= calendar.monthrange(2024, month)[1]:
            return say("hours.month_end", m=month, mon=mon)
        return say("hours.month_day", m=month, d=day, mon=mon)

    def weekdays(days: tuple[int, ...]) -> str:
        runs: list[list[int]] = []
        for d in sorted(days):
            if runs and d == runs[-1][-1] + 1:
                runs[-1].append(d)
            else:
                runs.append([d])
        parts = [
            say("hours.day_range", a=fmt.weekdays[r[0]], b=fmt.weekdays[r[-1]])
            if len(r) >= 3
            else say("hours.day_join").join(fmt.weekdays[d] for d in r)
            for r in runs
        ]
        return say("hours.day_join").join(parts)

    lines: list[dict[str, str]] = []
    if _hours_structurable(spot):
        periods = sorted(
            spot.hours,
            key=lambda p: (p.season.start_month, p.season.start_day) if p.season else (0, 0),
        )
        for p in periods:
            condition = []
            if p.season:
                condition.append(
                    say(
                        "hours.season",
                        start=month_day(p.season.start_month, p.season.start_day),
                        end=month_day(p.season.end_month, p.season.end_day, end=True),
                    )
                )
            days = []
            if p.days.weekdays:
                days.append(weekdays(p.days.weekdays))
            if p.days.include_holidays:
                days.append(say("hours.holidays"))
            if days:
                condition.append(say("hours.day_join").join(days))
            if p.days.exclude_holidays:
                condition.append(say("hours.except_holidays"))
            if p.always_open:
                time_text = say("hours.always_open")
            else:
                r = p.ranges[0]
                time_text = format_time_range(r.start, r.end, locale.code)
                if r.last_entry:
                    last = format_time(r.last_entry, locale.code)
                    time_text += say("hours.last_entry", time=last)
            lines.append({"condition": " ".join(condition), "time": time_text})
    quotes: list[str] = []
    for p in spot.hours:
        quote = " ".join((p.evidence.quote or "").split()) if p.evidence else ""
        if quote and quote not in quotes:
            quotes.append(quote)
    return {"lines": lines, "quotes": quotes}


def _spot_row(
    ws: Workspace,
    spot: Spot,
    locale: LocaleConfig,
    verdict: DayVerdict,
    assets: dict[str, list[Asset]],
) -> dict[str, Any]:
    """一覧に出すカード 1 枚。"""
    photo = (assets.get(spot.spot_id) or [None])[0]
    return {
        "spot": spot,
        "name": spot.name(locale.code),
        "url": locale.url_path(spot.path()),
        "verdict": verdict,
        "area": area(spot.area),
        "photo": photo,
        "photo_url": _card_photo_url(ws, photo),
    }


def _card_photo_url(ws: Workspace, photo: Asset | None) -> str:
    """カードに出す写真の URL。小さい変種（tools/sync_assets）があればそちらを使う。

    カードの枠は 170〜290px 幅なので、本体（長辺 900px）を読むと 1 枚 100KB 以上が無駄になる。
    変種がまだ作られていないときは本体に落とす（存在しないファイルを指さない）。
    """
    if photo is None:
        return ""
    suffix = photo.local_path.suffix
    card = f"{photo.asset_id}-card{suffix}"
    if (ws.static_dir / "assets" / card).is_file():
        return f"/static/assets/{card}"
    return f"/static/assets/{photo.asset_id}{suffix}"


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
