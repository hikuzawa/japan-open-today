# ADR 0008: sitemill への依存モードと、機能の切り分け

- ステータス: 採用（2026-09-12）

## 背景
sitemill は akiya-atlas で本番稼働している共通エンジン。japan-open-today は 2 つ目の利用者。
エンジンに足すべきものと、サービス側に置くべきものの線を引く。判断基準は「他のサービスでも使うか」（sitemill ADR 0006）。

## 決定

### 依存モード
- 今フェーズは `../sitemill` への editable なパス依存（akiya-atlas ADR 0006 と同じ）。安定後に git タグ固定へ切り替える
- **akiya-atlas の CI は sitemill のタグ（`v0.1.0`）固定**なので、sitemill の main を変えても本番には影響しない。
  ただし akiya-atlas の**手元開発は path 依存**なので、**sitemill への変更は後方互換の追加だけにする**。
  区切りごとに sitemill と akiya-atlas の両方のテストを通す。akiya-atlas のコードは触らない

### sitemill に足すもの（汎用）
| 追加 | 内容 |
|---|---|
| `models/source.py` の緩和 | `SeedPage.kind` / `FollowRule.kind` を `str` に（`PageKind` は共通値の定数として残す）。`OperatorKind` に `prefecture` / `tourism_association` / `facility_official` / `transport_operator` を追加 |
| 巡回ゲートの二段化 | `Source` の検証は「crawl には根拠と pages が必要、`third_party` / `unknown` は不可」に一般化。さらに `Runtime.sources()` で**サービスが宣言した `crawlable_operator_kinds`** を検査する。**既定は現行の `{municipality, municipality_affiliated}`** なので、宣言しない akiya-atlas は現行どおり自治体のみに制限される |
| `i18n/` | ロケール定義（`site.toml` の `[[locales]]`）、用語カタログ、`t()` の Jinja グローバル、ロケール別の日付・時刻・数値・金額フィルタ |
| `PageMeta` 拡張 | `locale` と `alternates{locale: url_path}`。`head_meta` が hreflang と `x-default`・`og:locale` を出す。`preflight` が locale の有無と alternates の相互整合を検査 |
| `calendar/jp_holidays.py` | 内閣府「国民の祝日」CSV（政府標準利用規約 2.0＝ホワイトリスト適合）を取得日付きで読む。取得できないときは規則計算（固定日・ハッピーマンデー・春分秋分・振替休日・国民の休日）にフォールバック |
| `parse/jp/hours.py` ほか | 営業時間・定休日規則・日付範囲・所要時間の決定的パーサ。金額は既存の `parse_yen` を流用 |
| `openstatus/` | `resolve_day(...) -> DayVerdict{state, periods, reasons[]}`（ADR 0004） |
| 巡回間隔の例外 | `always_daily_kinds` と `stale_after_days`（ADR 0007・0004） |
| `assets/` の実装 | ライセンス通過画像の取得・保存・provenance・ビルド時検査（ADR 0006）。akiya-atlas ではインターフェースのみだった箇所 |

### サービス側に置くもの（固有）
情報源の一覧（`data/sources/kagawa.yaml`）、抽出スキーマとプロンプト、エリア区分、テンプレートと静的ファイル、
言語カタログと用語集、アフィリエイト設定、ページ生成、検索索引。

## 影響
- sitemill を変えたら sitemill のテストをすべて通す。akiya-atlas への反映はタグを上げるまで行われない
  （手順は `akiya-atlas/docs/runbook/operations.md` の 5 章）
- 「巡回できる運営主体」の最終的な担保は、`Source` の検証（`third_party` / `unknown` を拒否）と
  サービスの宣言（`crawlable_operator_kinds`）の二段で行う。japan-open-today は施設公式・観光協会・交通事業者を含める（ADR 0009）
