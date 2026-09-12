# 人間側で必要な作業（2026-09-12 起票）

AI が全自動で回すための前提として、アカウント作成や鍵の登録など人にしかできない作業を並べる。
上から順に済ませると、`uv run sitemill run` → 日次の GitHub Actions → Cloudflare Pages 公開までがつながる。

## 今すぐ必要（縦断パイプラインを完走させるため）
1. **Anthropic API キー**を `japan-open-today/.env` に書く（`ANTHROPIC_API_KEY=`）。
   これが無いと `sitemill extract` は「.env に何を書くか」を表示して止まる。
   抽出モデルは `site.toml` の `[llm] model`（既定 `claude-haiku-4-5`）で変えられる。
   akiya-atlas の `.env` の鍵をそのまま流用してもよいが、サービスごとに分けると使用量が切り分けられる。
2. ~~**GitHub リポジトリ**を作成し push する~~ → S0 で作成済み（private: `hikuzawa/japan-open-today`）。

## 公開までに必要
3. **ドメイン `japan-open-today.com`** を取得する。取得後は `site.toml` の `base_url` の 1 行を差し替えるだけでよい
   （canonical / hreflang / sitemap / robots はそこから決まる）。当面は Cloudflare Pages の既定ホストで動く。
4. **Cloudflare アカウント**と API トークン（Account > Cloudflare Pages: Edit）・アカウント ID。
   akiya-atlas で発行済みのものを流用できる。Pages プロジェクト `japan-open-today` は CI が「無ければ作成」する。
   **注意**: `japan-open-today.pages.dev` が第三者に使われている場合、既定ホストは
   `japan-open-today-<4文字>.pages.dev` になる（akiya-atlas で実際に起きた）。CI の出力で確認して `base_url` を直す。
5. **GitHub Secrets** を `hikuzawa/japan-open-today` に登録する（下表）。
6. **Cloudflare Web Analytics** を有効にしてビーコントークンを取得し、`CF_WEB_ANALYTICS_TOKEN` に入れる
   （無くても動く。計測タグが出ないだけ）。
7. **Google Maps Platform**: Maps Embed API を有効にし、HTTP リファラで公開ホストに制限したキーを
   `GOOGLE_MAPS_EMBED_KEY` に入れる（無い間は地図は外部リンクにフォールバックする）。
8. **運営者名と連絡手段**を決め、`site.toml` の `[operator]` に書く（現在は「準備中」）。全ページのフッターと `/about/` に出る。
   連絡手段は akiya-atlas の `tools/contact_form/`（Apps Script でフォームを作る）と同じ方式が使える。
9. 公開後: **Google Search Console** の登録（メタタグ方式なら `GOOGLE_SITE_VERIFICATION`、ファイル方式なら `verification/` に置く）。
10. 公開後: **www と pages.dev から apex への 301**。Pages の `_redirects` はドメイン単位のリダイレクトに非対応なので、
    Cloudflare ダッシュボードのアカウントレベル「Bulk Redirects」で行う（手順は akiya-atlas の `docs/human-tasks.md` 9 と同じ）。

## 収益化のために必要
11. **アフィリエイトの申込み**（審査に日数がかかるので早めに）。
    - **Klook**（アクティビティ・入場券）: 施設ページの枠に使う
    - **Agoda** / **Booking.com**: エリアページの宿泊枠に使う
    契約が済むまで CTA は「準備中」表示で、ダミーリンクは置かない（ADR 0006）。
12. 各 ASP の掲載ルール（「広告」表記、ロゴの使用条件など）に合わせてテンプレートの文言を確認する。

## 任意・後で
13. Street View を使う場合は Geocoding API の有効化（所在地から緯度経度を得るため）。
14. 公式 SNS（YouTube / Instagram / X）の埋め込みを使う場合、Instagram と X は oEmbed の利用登録が要る。

## GitHub Secrets の登録コマンド
値は画面に出さず、ファイルや標準入力から流し込む。登録後は `gh secret list --repo hikuzawa/japan-open-today` で確認する。

| Secret | 値の発行場所 | 登録コマンド |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic Console → API Keys | `grep '^ANTHROPIC_API_KEY=' .env \| cut -d= -f2- \| gh secret set ANTHROPIC_API_KEY --repo hikuzawa/japan-open-today` |
| `CLOUDFLARE_API_TOKEN` | Cloudflare → My Profile → API Tokens → テンプレート「Cloudflare Pages — Edit」 | `gh secret set CLOUDFLARE_API_TOKEN --repo hikuzawa/japan-open-today` |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare → Workers & Pages の概要ページ右側「Account ID」 | `gh secret set CLOUDFLARE_ACCOUNT_ID --repo hikuzawa/japan-open-today` |
| `GOOGLE_MAPS_EMBED_KEY`（任意） | Google Cloud Console → Credentials。Maps Embed API のみ、HTTP リファラで制限 | `gh secret set GOOGLE_MAPS_EMBED_KEY --repo hikuzawa/japan-open-today` |
| `CF_WEB_ANALYTICS_TOKEN`（任意） | Cloudflare → Web Analytics → サイト追加 → スニペット内の token | `gh secret set CF_WEB_ANALYTICS_TOKEN --repo hikuzawa/japan-open-today` |

注意: `.env.example` には値を書かない（git にコミットされる）。値は `.env` だけに置く。

## 決めてほしいこと（S0 時点）
- **日本語と繁体字の表示名**（ロゴ横とタイトルに使う短い表記）。案は S0 の報告に出した。
  決まったら `i18n/<locale>.yaml` に入れる（`site.toml` の `name` は正式名「Japan Open Today」に固定）。

## AI 側で次に行う作業（人の作業を待たずに進められるもの）
- S1: sitemill の多言語・巡回ゲート・ページ種別の一般化
- S2: 営業時間・定休日のパーサ、祝日、「今日開いているか」の判定エンジン
- S3: 画像ライセンスの 4 経路を実装し、香川の対象施設で何件写真が付くか計測
- S4〜S5: 香川の施設・交通を 10 件ほど手で登録して縦断パイプラインを完走させる
- S6: 香川全域の自動発見とレビュー行列
