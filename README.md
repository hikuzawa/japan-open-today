# Japan Open Today

香川県の観光施設と交通について、「今日・今週、行けるか」を日本語・英語・繁体字で提供する静的サイト。

**本番: <https://japan-open-today.com>**（日本語 / [English](https://japan-open-today.com/en/) / [繁體中文](https://japan-open-today.com/zh-hant/)）

施設 281 件・航路 4 路線を毎日 05:00 JST に巡回し、開館状況・運航状況を 3 言語・約 900 ページで出す。
共通エンジンは [sitemill](https://github.com/hikuzawa/sitemill)（このサービスはその 2 つ目の利用者）。

## 何をするか

```
巡回 → 差分検知 → 構造化（quote-then-parse）→ 3 言語で静的生成 → 公開前検査 → Cloudflare Pages
```

- **事実は機械抽出だけ**。LLM には値ではなく原文の引用を返させ、引用が本文に実在することを確かめてから、
  決定的パーサが値にする。推定値は混ぜない
- **「今日開いているか」は `open` / `closed` / `unknown` の 3 値**。定休日の規則・祝日・臨時休業の告知から
  計算し、根拠コード・原文の引用・一次情報リンク・取得日時を併記する。材料が無いか矛盾すれば `unknown` と出す
- **公式の説明文は転載しない**。紹介文は構造化した事実から各言語のテンプレートで組む
- 公式ページの取得が 14 日できていない施設は、規則上は開館日でも `unknown` に落とす
- 巡回するのは公式の情報源だけ（施設の公式サイト・県・市町・観光協会・交通事業者）。
  運営主体の根拠は `data/sources/kagawa.yaml` に引用と URL で残す

設計判断は [`docs/adr/`](docs/adr/) にある。

## 動かし方

```sh
uv sync
uv run pytest
uv run sitemill --site japan-open-today run   # crawl → extract → build
uv run python -m http.server -d dist 8000
```

鍵は `.env` に手で書く（`.env.example` を参照。値は探索しない）。`ANTHROPIC_API_KEY` が無ければ抽出は止まる。
人間側で必要な作業は [`docs/human-tasks.md`](docs/human-tasks.md)。

日次パイプライン（`.github/workflows/pipeline.yml`）は取得した記録を `data/` にコミットして配置する。
`sitemill-bot` のコミットはこれで、内容はデータだけ。

## 中身

| 場所 | 何が入っているか |
| --- | --- |
| `src/japan_open_today/` | サービス定義・抽出スキーマ・プロンプト・ページ生成・エリア区分・ASP 設定 |
| `data/sources/kagawa.yaml` | 施設と交通の一次設定（巡回 URL・運営主体の根拠・巡回方針） |
| `data/records/` | 抽出した事実と、根拠として残した短い引用（JSONL） |
| `data/state/` `data/runs/` `data/search/` | 巡回状態・実行レポート・Search Console の取り込み |
| `data/assets/*/assets.json` | 採用した写真の出典・作者・ライセンス（画像の実体は管理外） |
| `templates/` `static/` `i18n/` | ページの雛形・CSS・3 言語の文言カタログ |
| `tools/` | 一覧の数え上げ・多言語名の確定・写真の判定など、手で走らせる調査ツール |
| `tools/contact_form/` | お問い合わせフォーム（Google フォーム + Apps Script + AI 自動返信、3 言語） |

## データ・写真・地図の扱い

- `data/` にある事実（営業時間・定休日・料金・住所・電話など）は、**各一次情報の発行者に権利がある**。
  このリポジトリはそれを機械的に抽出・整理したものなので、再利用するときは各一次情報の規約を確認すること
- 写真は Wikimedia Commons の CC BY / CC0 / CC BY-SA と、明示的に商用可のフリー素材だけ。判定できない表記は不採用。
  **改変しない**（縮小のみ）。作者名・ライセンス名・出典ページへのリンクをページにクレジットとして出す
- 地図は Google Maps の埋め込み機能経由で、画像としては転載しない
- 実在の場所を AI 画像生成で描かない。図解は SVG のコード生成に限り「自動生成」と明記する

## お問い合わせ・訂正

掲載内容の誤りは、サイトの[お問い合わせフォーム](https://japan-open-today.com/about/)から知らせてほしい。
訂正の依頼は自動で `correction` ラベルの Issue になるが、**氏名・連絡先・問い合わせの原文は Issue に載せない**
（公開リポジトリなので。運営者への通知メールにだけ残る）。Issue に個人情報を書かないでほしい。

データ（`data/`）の手編集は受け付けない。値が違うときに直すのは情報源の設定か抽出側なので、
どのページのどの値が実際とどう違うかを教えてもらえれば、そちらを直す。

## 免責

公式サイトの記載を機械的に読み取った結果であり、正確性・最新性を保証しない。
訪問前に必ず各ページの一次情報リンクで確認すること。

## ライセンス

- コード（`src/` `tools/` `templates/` `static/` `tests/` `.github/`）: MIT。[LICENSE](LICENSE) を参照
- `data/` の事実情報: 各一次情報の発行者に帰属（MIT の対象ではない）
- 写真: `data/assets/*/assets.json` に記した各ライセンス（CC BY / CC0 / CC BY-SA）
