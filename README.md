# medical-signage — 放射線部 医学情報サイネージ

医学論文（PubMed/E-utilities）と総合医学誌ニュース（RSS）を週次で自動収集し、
Claude で日本語要約して、サイネージ用 `index.html` を生成・公開するシステム。

## 構成

```
src/fetch_pubmed.py   文献取得（E-utilities, カテゴリ別）
src/fetch_news.py     ニュース取得（総合医学誌RSS 5誌）
src/summarize.py      Claude APIで日本語要約（文献+ニュース合流）
src/build_html.py     index.html 生成（サイネージ表示）
config.yaml           検索式・RSS・表示設定（ここを編集して調整）
templates/signage.html  表示テンプレート
.github/workflows/update.yml  週次自動実行
```

## ローカル実行

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# APIキーを設定（.env はGit管理外）
cp .env.example .env
# .env を編集して ANTHROPIC_API_KEY=sk-ant-... を記載

# 順に実行
python src/fetch_pubmed.py    # → articles.json
python src/fetch_news.py      # → news.json
python src/summarize.py       # → articles_summarized.json
python src/build_html.py      # → index.html
```

`index.html` をブラウザで開いて確認。表示端末は Chrome を kiosk で全画面表示：

```bash
google-chrome --kiosk "file:///path/to/index.html"
# または公開後の Pages URL を kiosk で開く
```

## GitHub での自動運用（週次・月曜9時JST）

1. GitHubで空リポジトリを作成（例 `medical-signage`）。
2. このフォルダを push（下記「初回push」参照）。
3. **Secrets登録**: リポジトリ Settings → Secrets and variables → Actions →
   - `ANTHROPIC_API_KEY`（必須）
   - `NCBI_API_KEY`（任意。あればPubMed頻度上限が緩和）
4. **Pages有効化**: Settings → Pages → Source = "Deploy from a branch" →
   Branch = `main` / フォルダ = `/ (root)` → Save。
   数分後 `https://<ユーザー名>.github.io/medical-signage/` で公開。
5. 動作確認: Actions タブ → "Update signage" → "Run workflow" で手動実行。

### 初回push

```bash
git init
git add .
git commit -m "Initial commit: medical signage"
git branch -M main
git remote add origin https://github.com/<ユーザー名>/medical-signage.git
git push -u origin main
```

## 調整ポイント（config.yaml）

- 文献カテゴリの件数・検索式・取得期間 … `pubmed.categories`
- ニュースのフィードURL・件数 … `news`
- 1ページ件数・フォント・色・ページ送り速度 … `display`
- 要約モデル・字数 … `summarize`

## 設計上の注意

- 秘密情報はコードに書かず Secrets / `.env`。
- 表示はタイトル＋自作要約＋出典リンクに留め、Abstract全文は転載しない。
- 本文が薄い記事はタイトル和訳のみ（創作=ハルシネーション防止）。
- 生成失敗時は前回の `index.html` を保持（画面の白紙化を防ぐ）。
