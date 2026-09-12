"""sitemill の Service 実装。データ・スキーマ・ページ構成はこちらが持つ（sitemill ADR 0006）。

ページ生成（`pages`）は S5 で入れる。ここまでで揃うのは「何を巡回し、何を抽出し、
どうレコードにするか」まで。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from sitemill.assets import Asset, AssetStore
from sitemill.extract import ExtractedItem, ExtractionSpec
from sitemill.models import OperatorKind, Page, Provenance, Redirect, Source
from sitemill.settings import Workspace
from sitemill.store.records import RecordStore

from japan_open_today.data import Dataset, load_sources, records_path, spot_from_entry
from japan_open_today.ingest import ingest_items, merge_record
from japan_open_today.spec import spec_for_kind

log = logging.getLogger(__name__)


class JapanOpenTodayService:
    id = "japan-open-today"

    # 巡回してよい運営主体（ADR 0009）。sitemill の既定（自治体だけ）より広い
    crawlable_operator_kinds = (
        OperatorKind.municipality,
        OperatorKind.municipality_affiliated,
        OperatorKind.prefecture,
        OperatorKind.tourism_association,
        OperatorKind.facility_official,
        OperatorKind.transport_operator,
    )

    def sources(self, ws: Workspace) -> list[Source]:
        return load_sources(ws)

    def extraction_spec(self, kind: str) -> ExtractionSpec | None:
        return spec_for_kind(kind)

    def ingest(
        self,
        ws: Workspace,
        *,
        source: Source,
        url: str,
        kind: str,
        items: Sequence[ExtractedItem],
        provenance: Provenance,
    ) -> dict[str, int]:
        store = RecordStore(records_path(ws, source.id))
        counts = ingest_items(
            ws, store=store, source=source, url=url, kind=kind, items=items, provenance=provenance
        )
        store.save()
        log.info("%s %s（%s）: %s", source.id, url, kind, counts)
        return counts

    def finalize(self, ws: Workspace, *, now: datetime) -> dict[str, int]:
        """期限切れの告知を落とし、鮮度の記録を更新する。"""
        from japan_open_today.ingest import finalize_records

        return finalize_records(ws, now=now)

    def pages(self, ws: Workspace, *, now: datetime) -> list[Page]:
        raise NotImplementedError("ページ生成は S5 で実装する")

    def search_index(self, ws: Workspace) -> Any:
        return None

    def redirects(self, ws: Workspace) -> list[Redirect]:
        return []

    def eval_dir(self, ws: Workspace) -> Path | None:
        return ws.fixtures_dir / "eval"

    def assets(self, ws: Workspace) -> list[Asset]:
        """登録済みの画像資産（sitemill ADR 0020）。ページに出せるのはここにあるものだけ。"""
        root = ws.data_dir / "assets"
        if not root.is_dir():
            return []
        out: list[Asset] = []
        for directory in sorted(p for p in root.iterdir() if p.is_dir()):
            out.extend(AssetStore(root, directory.name).load().values())
        return out

    def pii_policy(self, ws: Workspace) -> Any:
        """施設の公式電話は公開情報なので、公開前の個人情報検査では許可する。"""
        from sitemill.build.pii import allow_also, default_jp_gov_policy

        phones = [
            spot.phone.value
            for spot in Dataset.load(ws).spots
            if spot.phone.ok and spot.phone.value
        ]
        return allow_also(default_jp_gov_policy(), phones=phones)

    def review_candidates(self, ws: Workspace) -> list[dict[str, Any]]:
        """運営主体を判定できなかった情報源（ADR 0009）。10 件を超えたら発見側を直す。"""
        out: list[dict[str, Any]] = []
        from japan_open_today.data import load_entries

        for entry in load_entries(ws):
            if entry.get("policy") != "pending":
                continue
            spot = spot_from_entry(entry)
            out.append(
                {
                    "key": entry["id"],
                    "label": entry["name"],
                    "url": entry["official_url"],
                    "page_class": "spot" if spot is not None else "transport",
                    "operator_kind": entry.get("operator_kind", "unknown"),
                    "reason": " ".join((entry.get("notes") or "").split()),
                    "proposed_policy": "pending",
                }
            )
        return out


service = JapanOpenTodayService()

__all__ = ["JapanOpenTodayService", "merge_record", "service"]
