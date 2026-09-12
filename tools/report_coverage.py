"""抽出できた項目の充足率と、「今日行けるか」の判定の内訳を数える（S5）。

「不明」が多いと価値が下がるので、**なぜ不明なのか**を根拠コードごとに数える。
出力は data/runs/coverage.json。

使い方: uv run python -m tools.report_coverage [--days 7]
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import Any

from sitemill.clock import jst_today
from sitemill.jpcal import HolidayCalendar
from sitemill.openstatus import DayState
from sitemill.settings import Workspace

from japan_open_today.data import Dataset
from japan_open_today.verdict import spot_verdict, spot_week

SPOT_FIELDS = ("hours", "closures", "fees", "address", "phone", "reservation_required")
ROUTE_FIELDS = ("first_departure", "last_departure", "duration_minutes", "fares", "service_days")


def _has(spot: Any, field: str) -> bool:
    value = getattr(spot, field, None)
    if field == "reservation_required":
        return value not in (None, "unknown")
    if hasattr(value, "ok"):
        return bool(value.ok)
    return bool(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--out", type=Path, default=Path("data/runs/coverage.json"))
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    ds = Dataset.load(ws)
    holidays = HolidayCalendar.load(ws.data_dir / "reference" / "syukujitsu.csv")
    today = jst_today()
    stale_after = ws.site.crawl.stale_after_days

    spot_rows: list[dict[str, Any]] = []
    field_counts: collections.Counter[str] = collections.Counter()
    states: collections.Counter[str] = collections.Counter()
    reasons: collections.Counter[str] = collections.Counter()
    unknown_reasons: collections.Counter[str] = collections.Counter()

    for spot in ds.spots:
        present = [f for f in SPOT_FIELDS if _has(spot, f)]
        field_counts.update(present)
        verdict = spot_verdict(spot, today, holidays=holidays, stale_after_days=stale_after)
        states[verdict.state.value] += 1
        for r in verdict.reasons:
            reasons[r.code.value] += 1
        if verdict.state is DayState.unknown:
            unknown_reasons[verdict.reasons[0].code.value if verdict.reasons else "none"] += 1
        week = spot_week(
            spot, today, days=args.days, holidays=holidays, stale_after_days=stale_after
        )
        spot_rows.append(
            {
                "spot_id": spot.spot_id,
                "name": spot.name("ja"),
                "fields": present,
                "missing": [f for f in SPOT_FIELDS if f not in present],
                "today": verdict.state.value,
                "today_reasons": [r.code.value for r in verdict.reasons],
                "week": collections.Counter(v.state.value for v in week),
            }
        )

    route_rows = []
    route_fields: collections.Counter[str] = collections.Counter()
    for route in ds.routes:
        present = [f for f in ROUTE_FIELDS if _has(route, f)]
        route_fields.update(present)
        route_rows.append(
            {
                "route_id": route.route_id,
                "operator": route.operator_id,
                "fields": present,
                "missing": [f for f in ROUTE_FIELDS if f not in present],
            }
        )

    total = len(ds.spots)
    print(f"== 施設 {total} 件の項目充足率 ==")
    for field in SPOT_FIELDS:
        n = field_counts[field]
        print(f"  {field:22} {n:3}/{total}  {n / total:5.0%}")
    print(f"\n== 今日（{today}）の判定 ==")
    for state, n in states.most_common():
        print(f"  {state:8} {n:3}/{total}  {n / total:5.0%}")
    print("\n== unknown の理由（先頭の根拠）==")
    for code, n in unknown_reasons.most_common():
        print(f"  {n:3}  {code}")
    print("\n== 根拠コードの出現（全施設・今日）==")
    for code, n in reasons.most_common():
        print(f"  {n:3}  {code}")
    print(f"\n== 航路・路線 {len(ds.routes)} 件 ==")
    for field in ROUTE_FIELDS:
        n = route_fields[field]
        print(f"  {field:22} {n:3}/{len(ds.routes)}")
    print("\n== 施設ごと ==")
    for row in spot_rows:
        week = dict(row["week"])
        codes = ",".join(row["today_reasons"])[:40]
        print(f"  {row['spot_id']:16} {row['today']:8} {codes:42} {week}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "today": today.isoformat(),
                "spots": spot_rows,
                "routes": route_rows,
                "field_counts": dict(field_counts),
                "states": dict(states),
                "unknown_reasons": dict(unknown_reasons),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
