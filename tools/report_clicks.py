"""広告の転送ページが何回開かれたかを、飛び先ごとに数える（ADR 0012 追記、2026-09-23）。

転送ページ（`/go/<案件>/<枠>/<飛び先>/`）は 1 枚につき 1 回、Cloudflare Web Analytics の
ビーコンを出す。その表示数を Cloudflare の GraphQL API から取り、**飛び先ごと・言語ごと**に
並べる。Klook の「探す」導線（商品が無い施設）が押されているかを見て、枠を絞るかを判断する。

必要なもの（`.env` / CI の Secrets）:

- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN` に **Account Analytics: Read** の権限（配置用の権限だけでは 403 になる）

ビーコンは Cloudflare が応答に自動で挿し込む形にしてあり、サイトに鍵を置かない
（`CF_WEB_ANALYTICS_TOKEN` は登録しない）。**絞り込みは `siteTag` ではなくホスト名**で行う。
2026-09-23 に調べたところ、本番の HTML に挿し込まれている `data-cf-beacon` の token
（`f2319ef6…`）にはイベントが 1 件も無く、実データは別の site（`a8bcebea…`）に入っていた。
同じホスト名の Web Analytics の登録が 2 つあるためで、ホスト名で絞ればどちらでも取れる。

運営者・開発の**動作確認で開いた分**は `data/affiliates/verification_clicks.json` に記録してあり、
期間が重なるものを差し引いて出す（利用者のクリックと混ぜない）。

鍵や権限が足りないときは、何を足せばよいかを書いて 0 で終わる（週次を止めない）。

使い方: uv run python -m tools.report_clicks [--days 7]
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from sitemill.settings import Workspace

from japan_open_today import affiliates, klook

ENDPOINT = "https://api.cloudflare.com/client/v4/graphql"
VERIFICATION = Path("data/affiliates/verification_clicks.json")
QUERY = """
query($account: string!, $host: string!, $since: Time!, $until: Time!) {
  viewer {
    accounts(filter: {accountTag: $account}) {
      rumPageloadEventsAdaptiveGroups(
        filter: {requestHost: $host, datetime_geq: $since, datetime_leq: $until}
        limit: 500
        orderBy: [count_DESC]
      ) {
        count
        dimensions { requestPath }
      }
    }
  }
}
"""
NEED = (
    "- クリック数は出せなかった。`CLOUDFLARE_API_TOKEN` に **Account Analytics: Read** が"
    "要る（配置用の権限だけでは読めない）。権限を足したトークンと、CI・手元の値が同じかも見る。"
    "当面は Cloudflare の Web Analytics の画面で `/go/` のページ別表示数を見る"
)


def fetch(token: str, account: str, host: str, days: int) -> dict[str, int] | None:
    """パスごとの表示数。取れなければ None。"""
    until = datetime.now(UTC).replace(microsecond=0)
    since = until - timedelta(days=days)
    try:
        resp = httpx.post(
            ENDPOINT,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "query": QUERY,
                "variables": {
                    "account": account,
                    "host": host,
                    "since": since.isoformat().replace("+00:00", "Z"),
                    "until": until.isoformat().replace("+00:00", "Z"),
                },
            },
            timeout=60,
        )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    body = resp.json()
    if body.get("errors") or not (body.get("data") or {}).get("viewer"):
        return None
    accounts = body["data"]["viewer"].get("accounts") or []
    if not accounts:
        return None
    return {
        row["dimensions"]["requestPath"]: row["count"]
        for row in accounts[0]["rumPageloadEventsAdaptiveGroups"]
    }


def verification_clicks(root: Path, days: int) -> dict[tuple[str, str], int]:
    """動作確認で開いた分（飛び先・言語ごと）。期間に入るものだけ返す。"""
    path = root / VERIFICATION
    if not path.is_file():
        return {}
    since = (datetime.now(UTC) - timedelta(days=days)).date()
    out: dict[tuple[str, str], int] = defaultdict(int)
    for event in json.loads(path.read_text(encoding="utf-8")).get("events", []):
        if date.fromisoformat(event["date"]) >= since:
            out[(event["landing"], event["locale"])] += int(event["count"])
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7, help="さかのぼる日数")
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    lines = [f"## 広告のクリック（転送ページの表示、直近 {args.days} 日）", ""]
    locales = tuple((lc.code, lc.path) for lc in ws.site.locale_list)
    targets = affiliates.go_targets(locales)
    if not targets:
        lines.append("- 公開中の案件が無い")
        print("\n".join(lines))
        return 0

    secrets = ws.secrets
    counts = None
    token, account = secrets.cloudflare_api_token, secrets.cloudflare_account_id
    host = urlsplit(ws.site.base_url).hostname or ""
    if token and account and host:
        counts = fetch(token, account, host, args.days)
    if counts is None:
        lines.append(NEED)
        print("\n".join(lines))
        return 0

    by_landing: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for target in targets:
        name = target.landing.id if target.landing else "（飛び先なし）"
        by_landing[name][target.locale] += counts.get(target.url_path, 0)

    # 動作確認で開いた分を差し引く（期間に入るものだけ）
    checks = verification_clicks(ws.root, args.days)
    for (name, locale), n in checks.items():
        if name in by_landing:
            by_landing[name][locale] = max(0, by_landing[name].get(locale, 0) - n)
    checked = sum(checks.values())

    codes = [code for code, _ in locales]
    lines.append("| 飛び先 | 種類 | " + " | ".join(codes) + " | 合計 |")
    lines.append("|---|---|" + "---:|" * (len(codes) + 1))
    total = 0
    for landing in klook.landings():
        row = by_landing.get(landing.id, {})
        kind = "施設の商品" if landing.spots else ("エリア" if landing.areas else "香川の検索")
        n = sum(row.values())
        total += n
        cells = " | ".join(str(row.get(code, 0)) for code in codes)
        lines.append(f"| `{landing.id}` | {kind} | {cells} | **{n}** |")
    lines += [
        "",
        f"- 合計 **{total}** 回（動作確認の {checked} 回を除いた数）。"
        "「香川の検索」と「エリア」は、商品が無い施設からの導線",
        "- 2 週間見て「香川の検索」がほぼ 0 なら、枠を商品のある施設と直島・高松に絞る"
        "（質の低い送客は規約 4.5(b) で停止されうる）",
    ]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
