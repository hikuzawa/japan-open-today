"""足場の検査。site.toml が読めること、サービスの規約が守られていることを確かめる。

サービス実装（service.py）は S4 で入る。ここでは設定と方針の検査だけを行う。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sitemill.i18n import load_catalogs
from sitemill.settings import SiteConfig

import japan_open_today

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def site() -> SiteConfig:
    return SiteConfig.load(ROOT / "site.toml")


def test_version() -> None:
    assert japan_open_today.__version__


def test_site_config_loads(site: SiteConfig) -> None:
    assert site.id == "japan-open-today"
    assert site.name == "Japan Open Today"
    assert site.service == "japan_open_today.service:service"
    assert site.language == "ja"


def test_base_url_has_no_trailing_slash(site: SiteConfig) -> None:
    """基準 URL はここ 1 か所で差し替える。末尾のスラッシュは付けない（canonical が二重になる）。"""
    assert site.base_url.startswith("https://")
    assert not site.base_url.endswith("/")


def test_user_agent_points_at_the_site(site: SiteConfig) -> None:
    """巡回先から誰が来たのか分かるようにする（sitemill ADR 0003）。"""
    ua = site.user_agent
    assert site.id in ua and site.base_url in ua


def test_crawl_is_polite(site: SiteConfig) -> None:
    """ホストごとに間隔を空け、週 1 回は必ず取りに行く（ADR 0007）。"""
    assert site.crawl.default_delay_seconds >= 1.0
    assert site.crawl.max_interval_days <= 7


def test_the_operator_name_is_one_string_for_all_three_locales(site: SiteConfig) -> None:
    """運営者名は 3 言語共通の 1 つにする（2026-09-13 決定）。

    信頼ブロックの見出しが「運営者 / Operator / 營運者」とロケール別に出るので、名前の側に
    「運営」を足すと日本語ページで重なる。sitemill の `[operator] name` はロケール別に
    持てないので、和文を混ぜると英語・繁体字のページに日本語が 1 語残る。
    """
    assert site.operator.name == "Japan Open Today"
    # 連絡先はフォームができるまで「準備中」。ダミーの URL は置かない（ADR 0006）
    assert site.operator.contact == "準備中" or site.operator.contact.startswith("https://")


def test_three_locales_with_japanese_at_the_root(site: SiteConfig) -> None:
    """日本語をルート、英語と繁体字を接頭辞つきに置く（ADR 0005）。"""
    assert site.multilingual
    assert [(lc.code, lc.path) for lc in site.locale_list] == [
        ("ja", ""),
        ("en", "en"),
        ("zh-Hant", "zh-hant"),
    ]
    assert site.default_locale.code == "ja"  # hreflang の x-default が指す先
    assert site.locale("zh-Hant").lang == "zh-Hant"
    assert site.locale("zh-Hant").og == "zh_TW"


def test_display_names_come_from_the_catalogs(site: SiteConfig) -> None:
    """正式名は site.toml に 1 つ。各言語の短い表記はカタログで差し替える（ADR 0005）。"""
    catalogs = load_catalogs(ROOT / "i18n", [lc.code for lc in site.locale_list], default_code="ja")
    assert catalogs["ja"].get("site.name_short") == "今日行ける日本"
    assert catalogs["en"].get("site.name_short") == "Japan Open Today"
    assert catalogs["zh-Hant"].get("site.name_short") == "今天能去的日本"


def test_notice_pages_are_crawled_daily_and_freshness_has_a_floor(site: SiteConfig) -> None:
    """鮮度が武器なので、告知は毎日取り、取得が途切れたら判定を落とす（ADR 0004・0007）。"""
    assert site.crawl.always_daily_kinds == ("notice",)
    assert site.crawl.stale_after_days == 14


def test_env_example_has_no_values() -> None:
    """.env.example には値を書かない（git にコミットされる）。"""
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        key, _, value = line.partition("=")
        assert value == "", f"{key} に値が書かれている"


def test_adrs_are_numbered_without_gaps() -> None:
    """設計判断は ADR に残す。番号の重複と欠番を防ぐ。"""
    numbers = sorted(
        int(p.name[:4]) for p in (ROOT / "docs" / "adr").glob("[0-9][0-9][0-9][0-9]-*.md")
    )
    assert numbers == list(range(1, len(numbers) + 1))
