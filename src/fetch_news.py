#!/usr/bin/env python3
# =============================================================
# fetch_news.py — 総合医学誌のRSSからニュースを取得する（Step 4）
#
# 流れ:
#   1. config.yaml の news.feeds を読む
#   2. 各フィードに User-Agent を付けてアクセス（NEJM等のブロック対策）
#   3. 取得できた誌から per_feed 件ずつ拾う
#   4. 取得できなかった誌はスキップして継続（白紙化を防ぐ）
#   5. news.json に保存（summarize.py が文献と合流させて要約する）
#
# 単体実行: python src/fetch_news.py
# =============================================================

import os
import sys
import json
import datetime

import yaml
import feedparser

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.yaml")
OUTPUT_PATH = os.path.join(PROJECT_ROOT, "news.json")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def clean_html(text):
    """RSS本文に混じるHTMLタグをざっくり除去する（要約に渡しやすくする）。"""
    if not text:
        return ""
    import re
    text = re.sub(r"<[^>]+>", " ", text)        # タグを空白に
    text = re.sub(r"\s+", " ", text)            # 連続空白を1つに
    return text.strip()


def fetch_feed(name, url, user_agent, per_feed):
    """1フィードを取得して記事リストを返す。失敗時は空リスト。"""
    # feedparser は agent 引数でUser-Agentを差し替えられる
    parsed = feedparser.parse(url, agent=user_agent)

    # bozo=1 は解析に問題ありの印。HTTPエラーや非XMLのとき立つ。
    status = getattr(parsed, "status", None)
    if parsed.bozo and not parsed.entries:
        print(f"  [NG] {name}: 取得失敗 (status={status}, {parsed.bozo_exception})", file=sys.stderr)
        return []
    if not parsed.entries:
        print(f"  [NG] {name}: 記事0件 (status={status})", file=sys.stderr)
        return []

    print(f"  [OK] {name}: {len(parsed.entries)}件中 {per_feed}件採用 (status={status})", file=sys.stderr)

    items = []
    for entry in parsed.entries:
        if len(items) >= per_feed:
            break
        title = entry.get("title", "").strip()
        # ゴミ記事を除外：タイトルが空、または誌名そのもの（チャンネル名の混入）
        if not title or title.lower() == name.lower():
            continue
        # description/summary はタグ混じりのことがあるので除去
        summary_src = clean_html(entry.get("summary") or entry.get("description") or "")
        items.append({
            "category": "ニュース",            # 表示の色帯・ラベル用
            "kind": "ニュース",
            "title": title,
            "journal": name,                    # 出典誌名
            "url": entry.get("link", ""),       # 記事URL（DOIの代わりに表示）
            "year": "",
            "authors": "",
            "doi": "",
            # 要約の材料。RSS本文（抄録相当）。空でもタイトルだけで要約は可能。
            "abstract": summary_src,
        })
    return items


def main():
    cfg = load_config()
    ncfg = cfg["news"]
    ua = ncfg.get("user_agent", "")
    per_feed = ncfg.get("per_feed", 2)

    all_items = []
    print("[RSS取得] 各誌を検証しながら取得します...", file=sys.stderr)
    for feed in ncfg["feeds"]:
        items = fetch_feed(feed["name"], feed["url"], ua, per_feed)
        all_items.extend(items)

    output = {
        "generated_at": datetime.datetime.now().astimezone().isoformat(),
        "count": len(all_items),
        "articles": all_items,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n[完了] ニュース合計 {len(all_items)} 件を {OUTPUT_PATH} に保存しました。", file=sys.stderr)


if __name__ == "__main__":
    main()
