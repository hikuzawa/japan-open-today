"""運営主体の根拠の確かめ直し（sitemill ADR 0027）。通信は respx でモックする。"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from sitemill.fetch.client import PoliteClient
from sitemill.recheck import RecheckTarget
from sitemill.settings import Workspace

from japan_open_today.service import service

ROOT = Path(__file__).resolve().parents[1]
UA = "japan-open-today-test/0.1"


@pytest.fixture(scope="module")
def ws() -> Workspace:
    return Workspace.open(ROOT)


def target(ws: Workspace, key: str) -> RecheckTarget:
    return next(t for t in service.recheck_targets(ws) if t.key == key)


def test_every_crawled_source_with_evidence_is_a_target(ws: Workspace) -> None:
    targets = service.recheck_targets(ws)
    assert len(targets) >= 280
    assert all(t.checked_on is not None for t in targets)  # 確認日の無い根拠は無い


def client() -> PoliteClient:
    return PoliteClient(UA, jitter=0.0, sleep=lambda s: None, default_delay=0.0)


@respx.mock
def test_the_quote_still_on_the_page_holds(ws: Workspace) -> None:
    respx.get("https://www.my-kagawa.jp/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://www.my-kagawa.jp/organization").mock(
        return_value=httpx.Response(
            200, html="<body><p>公益社団法人 香川県観光協会 について</p></body>"
        )
    )
    with client() as c:
        assert service.recheck_one(ws, target(ws, "kagawa-p2970"), c) == ("ok", "")


@respx.mock
def test_a_missing_quote_fails(ws: Workspace) -> None:
    respx.get("https://www.my-kagawa.jp/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://www.my-kagawa.jp/organization").mock(
        return_value=httpx.Response(200, html="<body><p>サイトの改装中です</p></body>")
    )
    with client() as c:
        result, reason = service.recheck_one(ws, target(ws, "kagawa-p2970"), c)
    assert result == "fail" and "根拠の引用がページに無い" in reason


@respx.mock
def test_a_site_that_drops_overseas_connections_is_unreachable_not_failed(ws: Workspace) -> None:
    respx.get("https://www.my-kagawa.jp/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://www.my-kagawa.jp/organization").mock(
        side_effect=httpx.ConnectTimeout("timed out")
    )
    with client() as c:
        result, _ = service.recheck_one(ws, target(ws, "kagawa-p2970"), c)
    assert result == "unreachable"
