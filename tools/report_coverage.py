"""抽出できた項目の充足率と、「今日行けるか」の判定の内訳を数える（S5、ADR 0011）。

指標は**型別**に見る（ADR 0011）。全体をひとつの数字にすると、対象の広げ方で動くだけで
改善の指標にならない。

  - ゲートのある施設（`gated`）: 営業時間が取れているか
  - 屋外の場所（`open_air`）: 利用者が行動を決められる状態か（判定が出ているか、
    「時間の定めが記載されていない」と明示できているか）

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
from japan_open_today.pages import no_hours_stated
from japan_open_today.verdict import spot_verdict, spot_week

SPOT_FIELDS = ("hours", "closures", "fees", "address", "phone", "reservation_required")
ROUTE_FIELDS = ("first_departure", "last_departure", "duration_minutes", "fares", "service_days")


def _has(spot: Any, field: str) -> bool:
    value = getattr(spot, field, None)
    if field == "reservation_required":
        return value not in (None, "unknown")
    if field == "closures":
        # 「年中無休」は規則 0 件だが、定休日が無いと**分かっている**（ADR 0011）
        return bool(value) or bool(getattr(spot, "closes_never", False))
    if hasattr(value, "ok"):
        return bool(value.ok)
    return bool(value)


def _settled(spot: Any, verdict: Any) -> bool:
    """屋外の場所について、利用者が行動を決められる状態か（ADR 0011）。

    open / closed と出せているか、または「時間の定めが記載されていない」と明示できていれば
    決められる。取得が途切れている・矛盾しているといった理由の unknown は決められない。
    """
    if verdict.state is not DayState.unknown:
        return True
    return no_hours_stated(spot)


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
    verdicts_by_id: dict[str, Any] = {}
    field_counts: collections.Counter[str] = collections.Counter()
    states: collections.Counter[str] = collections.Counter()
    reasons: collections.Counter[str] = collections.Counter()
    unknown_reasons: collections.Counter[str] = collections.Counter()

    for spot in ds.spots:
        present = [f for f in SPOT_FIELDS if _has(spot, f)]
        field_counts.update(present)
        verdict = spot_verdict(spot, today, holidays=holidays, stale_after_days=stale_after)
        verdicts_by_id[spot.spot_id] = verdict
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
                "spot_type": spot.spot_type,
                "settled": _settled(spot, verdict),
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
    gated = [s for s in ds.spots if s.spot_type == "gated"]
    open_air = [s for s in ds.spots if s.spot_type == "open_air"]
    untyped = [s for s in ds.spots if s.spot_type == "unknown"]
    gated_hours = [s for s in gated if s.hours]
    settled = [s for s in open_air if _settled(s, verdicts_by_id[s.spot_id])]

    print("== 型別の指標（ADR 0011）==")
    if gated:
        rate = len(gated_hours) / len(gated)
        print(f"  ゲートのある施設の営業時間  {len(gated_hours):3}/{len(gated)}  {rate:5.0%}")
    if open_air:
        rate = len(settled) / len(open_air)
        print(f"  屋外の場所の判定確定率      {len(settled):3}/{len(open_air)}  {rate:5.0%}")
    print(f"  型が未判定の施設            {len(untyped):3}/{total}")

    print(f"\n== 施設 {total} 件の項目充足率（参考）==")
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
        codes = ",".join(row["today_reasons"])[:34]
        mark = "" if row["settled"] else "  ←未確定"
        print(
            f"  {row['spot_id']:16} {row['spot_type']:9} {row['today']:8} {codes:36} {week}{mark}"
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "today": today.isoformat(),
                "spots": spot_rows,
                "routes": route_rows,
                "by_type": {
                    "gated": {"total": len(gated), "hours": len(gated_hours)},
                    "open_air": {"total": len(open_air), "settled": len(settled)},
                    "untyped": len(untyped),
                },
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
