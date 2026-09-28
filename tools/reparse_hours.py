"""保存済みの営業時間を、原文の引用から読み直す（ネットワークにも LLM にも触れない）。

営業時間は抽出のときに引用から決めて記録に保存している。パーサを直しても、相手のページが
変わるまで古い値が残る。引用は記録に残っているので、それを読み直して値を決め直す
（quote-then-parse。値は引用からしか作らない）。

直したときに使う:
- 2026-09-28: 月ごとの表（「1月 7時00分~17時00分 / 2月 …」）を月ごとの季節として読むようにした
  （sitemill v0.7.10）。季節の無い 12 個の時間帯が、その日の時間として全部並んでいた（栗林公園）

**書き換えるのは、読み直すと季節が付くようになったものだけ**（`--apply`）。パーサのほかの変化で
値が変わるものは数えて出すだけにする（直した内容と関係の無い変化を黙って混ぜない）。

使い方:
    uv run python -m tools.reparse_hours           # 変わるものを数えるだけ
    uv run python -m tools.reparse_hours --apply   # 季節が付くものだけ記録を書き換える
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sitemill.parse.jp.hours import parse_opening_hours
from sitemill.settings import Workspace

from japan_open_today.data import load_entries, records_path


def reparse(hours: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """営業時間の並びを読み直す。返り値は (新しい並び, 変化の種類)。

    変化の種類: "" 変わらない / "seasoned" 季節が付いた / "other" それ以外の変化
    """
    by_quote: dict[str, list[dict[str, Any]]] = {}
    for period in hours:
        quote = (period.get("evidence") or {}).get("quote") or ""
        by_quote.setdefault(quote, []).append(period)
    out: list[dict[str, Any]] = []
    kind = ""
    for quote, old in by_quote.items():
        parsed, _note = parse_opening_hours(quote) if quote else (None, None)
        if not parsed:
            out += old
            continue
        evidence = old[0].get("evidence")
        new = [dict(p.model_dump(mode="json"), evidence=evidence) for p in parsed]
        if _shape(new) == _shape(old):
            out += old
            continue
        seasoned = all(p.get("season") is None for p in old) and all(
            p.get("season") is not None for p in new
        )
        if seasoned:
            kind = "seasoned"
            out += new
        else:
            kind = kind or "other"
            out += old
    return out, kind


def _shape(periods: list[dict[str, Any]]) -> list[Any]:
    """比べるための形（引用・取得日時は除く）。"""
    return [
        json.dumps({k: v for k, v in p.items() if k != "evidence"}, sort_keys=True) for p in periods
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="季節が付くものだけ記録を書き換える")
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    seasoned: list[str] = []
    other: list[str] = []
    for entry in load_entries(ws):
        path = records_path(ws, entry["id"])
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        changed = False
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            record = json.loads(line)
            if not record.get("hours"):
                continue
            new, kind = reparse(record["hours"])
            label = f"{entry['id']}（{record.get('spot_id') or record.get('route_id')}）"
            if kind == "seasoned":
                seasoned.append(label)
                if args.apply:
                    record["hours"] = new
                    lines[i] = json.dumps(record, ensure_ascii=False, sort_keys=True)
                    changed = True
            elif kind == "other":
                other.append(label)
        if changed:
            path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))

    print(f"月ごとの季節が付く: {len(seasoned)} 件" + ("（書き換えた）" if args.apply else ""))
    for label in seasoned:
        print(f"  - {label}")
    print(f"ほかの変化（書き換えない）: {len(other)} 件")
    for label in other:
        print(f"  - {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
