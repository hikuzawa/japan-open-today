"""データの読み込み。`data/sources/*.yaml`（情報源と巡回設定）と `data/records/*.jsonl`（事実）。

1 つの YAML エントリが sitemill の `Source`（巡回設定）と、このサービスの `spot` または
`transport`（表示用の設定）を兼ねる。akiya-atlas と同じ流儀。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from sitemill.models import Source
from sitemill.settings import Workspace
from sitemill.store.records import RecordStore

from japan_open_today.areas import area
from japan_open_today.schema import Route, Spot, TransportOperator


def load_entries(ws: Workspace) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(ws.sources_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for entry in data.get("sources", []) or []:
            entry.setdefault("_file", path.name)
            entries.append(entry)
    ids = [e.get("id") for e in entries]
    if len(ids) != len(set(ids)):
        raise ValueError("sources の id が重複している")
    return entries


def load_sources(ws: Workspace) -> list[Source]:
    return [Source.model_validate(e) for e in load_entries(ws)]


def spot_from_entry(entry: dict[str, Any]) -> Spot | None:
    """YAML の spot ブロックから、まだ事実の入っていない Spot を作る。"""
    block = entry.get("spot")
    if not block:
        return None
    payload = {
        "source_id": entry["id"],
        "official_url": entry["official_url"],
        "operator": entry["operator"],
        "operator_kind": entry.get("operator_kind", "unknown"),
        **block,
    }
    payload.setdefault("spot_id", entry["id"])
    try:
        spot = Spot.model_validate(payload)
    except ValidationError as e:
        raise ValueError(f"{entry['id']} の spot 設定が不正: {e}") from e
    area(spot.area)  # 未知のエリアはここで落とす
    return spot


def operator_from_entry(entry: dict[str, Any]) -> TransportOperator | None:
    block = entry.get("transport")
    if not block:
        return None
    payload = {
        "source_id": entry["id"],
        "official_url": entry["official_url"],
        "operator_id": block.get("operator_id", entry["id"]),
        "mode": block["mode"],
        "names": block.get("names", {}),
        "notice_url": block.get("notice_url"),
    }
    try:
        return TransportOperator.model_validate(payload)
    except ValidationError as e:
        raise ValueError(f"{entry['id']} の transport 設定が不正: {e}") from e


def routes_from_entry(entry: dict[str, Any]) -> list[Route]:
    """YAML に書いた航路・路線の枠（事実は抽出で埋める）。"""
    block = entry.get("transport") or {}
    out: list[Route] = []
    for row in block.get("routes", []) or []:
        payload = {
            "source_id": entry["id"],
            "operator_id": block.get("operator_id", entry["id"]),
            "mode": block["mode"],
            **row,
        }
        try:
            out.append(Route.model_validate(payload))
        except ValidationError as e:
            raise ValueError(f"{entry['id']} の routes 設定が不正: {e}") from e
    return out


def records_path(ws: Workspace, source_id: str) -> Path:
    return ws.records_dir / f"{source_id}.jsonl"


def load_records(ws: Workspace, source_id: str) -> list[dict[str, Any]]:
    return RecordStore(records_path(ws, source_id)).all()


@dataclass
class Dataset:
    """ページ生成が読む一式。"""

    sources: list[Source]
    spots: list[Spot]
    operators: list[TransportOperator]
    routes: list[Route]
    by_source: dict[str, Source] = field(default_factory=dict)
    spots_by_area: dict[str, list[Spot]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.by_source = {s.id: s for s in self.sources}
        self.spots_by_area = {}
        for spot in self.spots:
            self.spots_by_area.setdefault(spot.area, []).append(spot)

    @classmethod
    def load(cls, ws: Workspace) -> Dataset:
        """YAML の枠に、レコードとして保存された事実を重ねて読む。"""
        entries = load_entries(ws)
        sources = [Source.model_validate(e) for e in entries]
        spots: list[Spot] = []
        operators: list[TransportOperator] = []
        routes: list[Route] = []
        for entry in entries:
            stored = {
                r.get("spot_id") or r.get("route_id"): r for r in load_records(ws, entry["id"])
            }
            spot = spot_from_entry(entry)
            if spot is not None:
                record = stored.get(spot.spot_id)
                spots.append(
                    Spot.model_validate({**spot.model_dump(), **record}) if record else spot
                )
            operator = operator_from_entry(entry)
            if operator is not None:
                operators.append(operator)
            for route in routes_from_entry(entry):
                record = stored.get(route.route_id)
                routes.append(
                    Route.model_validate({**route.model_dump(), **record}) if record else route
                )
        return cls(sources=sources, spots=spots, operators=operators, routes=routes)

    def spots_in(self, area_slug: str) -> list[Spot]:
        return sorted(self.spots_by_area.get(area_slug, []), key=lambda s: s.spot_id)

    def routes_of(self, operator_id: str) -> list[Route]:
        return [r for r in self.routes if r.operator_id == operator_id]
