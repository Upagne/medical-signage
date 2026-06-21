#!/usr/bin/env python3
# =============================================================
# build_html.py — 要約結果からサイネージ用 index.html を生成する（Step 3）
#
# 流れ:
#   1. articles_summarized.json（Step2の出力）を読む
#   2. cards_per_page 件ずつ「ページ」に分割
#   3. カテゴリ色やラベルを付与して templates/signage.html に流し込む
#   4. プロジェクト直下に index.html を出力（GitHub Pages公開対象）
#
# フォールバック（必須要件）:
#   入力JSONが無い/壊れている等で生成できない場合は、既存の index.html を
#   上書きせずに保持し、画面が白紙にならないようにする。
#
# 単体実行: python src/build_html.py
# =============================================================

import os
import sys
import json
import datetime

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.yaml")
TEMPLATE_DIR = os.path.join(PROJECT_ROOT, "templates")
# 入力は要約済みを優先、無ければ要約前（articles.json）でも表示はできる
INPUT_SUMMARIZED = os.path.join(PROJECT_ROOT, "articles_summarized.json")
INPUT_RAW = os.path.join(PROJECT_ROOT, "articles.json")
OUTPUT_PATH = os.path.join(PROJECT_ROOT, "index.html")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_articles():
    """要約済みJSONを優先して読み込む。無ければ要約前を使う。"""
    path = INPUT_SUMMARIZED if os.path.exists(INPUT_SUMMARIZED) else INPUT_RAW
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def chunk(items, size):
    """リストを size 件ずつに分割する（ページ分割用）。"""
    return [items[i:i + size] for i in range(0, len(items), size)]


def build():
    cfg = load_config()
    dcfg = cfg["display"]
    colors = dcfg.get("category_colors", {})

    data = load_articles()
    articles = data.get("articles", [])

    # テンプレートに渡しやすい形へ整形（色・種別ラベル・要約の穴埋め）
    cards = []
    for a in articles:
        cards.append({
            "category": a.get("category", "その他"),
            "kind": a.get("kind", "文献"),  # Step4でニュースは "ニュース" が入る
            "color": colors.get(a.get("category", ""), "#636363"),
            "title": a.get("title", ""),
            # 要約が無ければAbstract冒頭で代替（白紙を避ける）
            "summary": a.get("summary") or (a.get("abstract", "")[:180]),
            "authors": a.get("authors", ""),
            "journal": a.get("journal", ""),
            "year": a.get("year", ""),
            "doi": a.get("doi", ""),
            "url": a.get("url", ""),  # ニュースの記事URL（DOIが無い場合の出典リンク）
        })

    pages = chunk(cards, dcfg.get("cards_per_page", 4))

    # 各ページの表示秒を文字量から算出する。
    #   秒 = ページ内の(タイトル+要約)文字数合計 ÷ chars_per_second
    #   min_seconds〜max_seconds の範囲に収める。
    cps = dcfg.get("chars_per_second", 7)
    min_s = dcfg.get("min_seconds", 12)
    max_s = dcfg.get("max_seconds", 30)
    page_seconds = []
    for page in pages:
        chars = sum(len(c["title"]) + len(c["summary"]) for c in page)
        sec = chars / cps if cps else min_s
        sec = max(min_s, min(max_s, round(sec)))  # クランプ
        page_seconds.append(sec)

    # 更新日時（JSON生成時刻があれば使い、無ければ現在時刻）
    gen = data.get("generated_at")
    if gen:
        updated_at = datetime.datetime.fromisoformat(gen).strftime("%Y-%m-%d %H:%M")
    else:
        updated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),  # XSS/タグ崩れ防止
    )
    # 向き(orientation)に応じてアスペクト比とグリッド列数を決める。
    # portrait=縦長(4:3モニタ縦置き=画面比3:4)、landscape=横長(4:3)。
    orientation = dcfg.get("orientation", "portrait")
    if orientation == "landscape":
        aspect_ratio = "4 / 3"
        grid_columns = "1fr 1fr"   # 横長は2列
    else:
        aspect_ratio = "3 / 4"
        grid_columns = "1fr"       # 縦長は1列

    template = env.get_template("signage.html")
    html = template.render(
        title=dcfg.get("title", "医学情報ダイジェスト"),
        # 画面高に追従するフォント指定（例: "2.4vh"）
        base_font_css="{}vh".format(dcfg.get("base_font_vh", 2.4)),
        page_seconds=page_seconds,  # ページ毎の表示秒（文字量から算出）
        aspect_ratio=aspect_ratio,
        grid_columns=grid_columns,
        updated_at=updated_at,
        pages=pages,
    )

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[完了] {len(cards)}件 / {len(pages)}ページを {OUTPUT_PATH} に生成しました。", file=sys.stderr)


def main():
    try:
        build()
    except Exception as e:
        # 生成失敗時は既存 index.html を上書きしない（フォールバック）
        if os.path.exists(OUTPUT_PATH):
            print(f"[警告] 生成失敗のため既存のindex.htmlを保持します: {e}", file=sys.stderr)
            sys.exit(0)  # 既存があるならエラー終了にしない（運用継続）
        else:
            print(f"[エラー] 生成失敗かつ既存index.htmlも無し: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
