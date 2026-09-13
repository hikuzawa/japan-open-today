"""サービス固有の CLI。sitemill の CLI に足す形で使う（`uv run japan-open-today <コマンド>`）。

いまあるのは広告まわりの 2 つだけ（ADR 0012）。巡回・抽出・生成・配置は sitemill の CLI を
このディレクトリで実行する。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated

import typer
from sitemill import commands


def _register(app: typer.Typer) -> None:
    @app.command("ad-check")
    def ad_check_cmd(
        dist: Annotated[
            Path, typer.Option("--dist", help="検査する生成物のディレクトリ")
        ] = Path("dist"),
    ) -> None:
        """広告掲載の検査（ADR 0012）。広告表記の有無と位置、/go/ 経由、宣言との一致を確かめる。

        CI では build の直後に走る。1 件でも問題があれば 1 で終了してビルドを止める。
        """
        from japan_open_today import ad_check

        rt = commands.Runtime.open()
        problems, summary = ad_check.check(dist, ad_check.locales_of(rt.ws.site))
        if problems:
            typer.echo(f"広告掲載の検査に失敗（{len(problems)} 件）:", err=True)
            for msg in problems[:50]:
                typer.echo(f"  - {msg}", err=True)
            if len(problems) > 50:
                typer.echo(f"  ... 他 {len(problems) - 50} 件", err=True)
            raise typer.Exit(code=1)
        typer.echo(f"広告掲載の検査 OK: {summary}")

    @app.command("ad-urls")
    def ad_urls_cmd(
        offer: Annotated[
            str | None, typer.Option("--offer", help="案件 ID で絞り込む（例: klook-tickets）")
        ] = None,
        dist: Annotated[
            Path, typer.Option("--dist", help="読む生成物のディレクトリ")
        ] = Path("dist"),
    ) -> None:
        """ASP に届け出る掲載 URL の一覧を出す（反映後に人が提出する）。"""
        from japan_open_today import ad_check, affiliates

        rt = commands.Runtime.open()
        rows = ad_check.ad_urls(
            dist, rt.ws.site.base_url, ad_check.locales_of(rt.ws.site), offer_id=offer
        )
        if not rows:
            typer.echo("掲載中の広告はありません（計測 URL が入っていない、または未ビルド）")
            return
        current = ""
        for item, url in rows:
            if item.id != current:
                current = item.id
                asp = affiliates.asp_of(item)
                if asp is None:
                    where = "ASP の管理画面"
                elif asp.submit_label:
                    where = f"{asp.name} の{asp.submit_label}"
                else:
                    where = f"{asp.name}（掲載 URL の個別届け出は不要）"
                typer.echo("")
                head = f"{item.name or item.label}（{item.id} / プログラム {item.program_id}）"
                typer.echo(f"# {head}")
                typer.echo(f"# 提出先: {where}")
            typer.echo(url)


def main() -> None:
    from sitemill.cli import app

    root = Path(__file__).resolve().parents[2]
    if (root / "site.toml").is_file() and "--root" not in sys.argv:
        os.chdir(root)
    _register(app)
    app()
