#!/usr/bin/env python3
# =============================================================
# fetch_pubmed.py — NCBI E-utilities で医学文献を取得する（Step 1）
#
# 流れ:
#   1. config.yaml を読む（検索式・件数・期間はここから）
#   2. カテゴリごとに esearch でPMID（論文ID）一覧を取る
#   3. PMIDをまとめて efetch で書誌情報（タイトル/著者/雑誌/年/DOI/Abstract）を取る
#   4. PMIDで重複排除（最初にヒットしたカテゴリを採用）
#   5. 合計上限まで絞って articles.json に保存
#
# 単体実行: python src/fetch_pubmed.py
# =============================================================

import os
import sys
import json
import time
import datetime
import xml.etree.ElementTree as ET  # efetchが返すXMLを解析する標準ライブラリ

import requests
import yaml

# --- 定数 ---------------------------------------------------
# E-utilities のエンドポイント（NCBIが公開している公式API）
ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

# このスクリプトの場所を基準に config.yaml と出力先のパスを決める
# （どのディレクトリから実行しても動くようにするため）
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.yaml")
OUTPUT_PATH = os.path.join(PROJECT_ROOT, "articles.json")


def load_config():
    """config.yaml を読み込んで dict（辞書）で返す。"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_common_params(cfg):
    """全リクエスト共通のパラメータを作る。
    tool と email を付けると、NCBIが利用者を識別でき、行儀の良いアクセスになる。
    APIキーがあれば付ける（無料登録で 3→10 req/秒に緩和）。
    """
    params = {
        "tool": cfg["pubmed"].get("tool_name", "medical-signage"),
        "email": cfg["pubmed"].get("email", ""),
    }
    # 環境変数にAPIキーがあれば使う（無くても動く）
    api_key = os.environ.get("NCBI_API_KEY")
    if api_key:
        params["api_key"] = api_key
    return params


def esearch(query, retmax, window_days, common_params):
    """検索式を投げてPMID（論文ID）のリストを返す。

    query       : config.yaml の検索式
    retmax      : 取得したい最大件数
    window_days : 「過去何日分か」。検索式末尾に reldate/datetype で期間を絞る
    """
    params = dict(common_params)  # 共通パラメータをコピーして使う
    params.update({
        "db": "pubmed",
        "term": query,
        "retmax": retmax,
        "retmode": "json",
        # reldate=過去N日, datetype=dp は出版日(date of publication)基準で絞る指定
        "reldate": window_days,
        "datetype": "dp",
        "sort": "date",  # 新しい順
    })
    resp = requests.get(ESEARCH_URL, params=params, timeout=30)
    resp.raise_for_status()  # HTTPエラー(404等)ならここで例外を投げる
    data = resp.json()
    # 検索結果のPMID一覧。ヒット0件なら空リストになる
    return data.get("esearchresult", {}).get("idlist", [])


def _text(node):
    """XMLノードのテキストを安全に取り出す（None回避）。"""
    if node is None:
        return ""
    # itertext()で子要素内のテキストも連結（<i>等のタグ混在に対応）
    return "".join(node.itertext()).strip()


def parse_article(article_el):
    """efetchが返す <PubmedArticle> 1件分を辞書に変換する。
    欠損項目（DOI無し等）は空文字にして表示崩れを防ぐ。
    """
    medline = article_el.find("MedlineCitation")
    art = medline.find("Article") if medline is not None else None
    if art is None:
        return None

    # --- PMID ---
    pmid = _text(medline.find("PMID"))

    # --- タイトル ---
    title = _text(art.find("ArticleTitle"))

    # --- 雑誌名 ---
    journal = _text(art.find("Journal/Title"))

    # --- 発行年 ---
    # PubDateは Year が無く MedlineDate（"2024 Jan-Feb"等）の場合もある
    year = _text(art.find("Journal/JournalIssue/PubDate/Year"))
    if not year:
        medline_date = _text(art.find("Journal/JournalIssue/PubDate/MedlineDate"))
        year = medline_date[:4] if medline_date else ""

    # --- 著者（最大3名＋ et al）---
    authors = []
    for au in art.findall("AuthorList/Author"):
        last = _text(au.find("LastName"))
        initials = _text(au.find("Initials"))
        if last:
            authors.append(f"{last} {initials}".strip())
    if len(authors) > 3:
        author_str = ", ".join(authors[:3]) + ", et al."
    else:
        author_str = ", ".join(authors)

    # --- DOI ---
    # ELocationID の EIdType="doi" を探す。無ければ ArticleIdList からも探す
    doi = ""
    for eloc in art.findall("ELocationID"):
        if eloc.get("EIdType") == "doi":
            doi = _text(eloc)
            break
    if not doi:
        for aid in article_el.findall("PubmedData/ArticleIdList/ArticleId"):
            if aid.get("IdType") == "doi":
                doi = _text(aid)
                break

    # --- Abstract（複数段落を結合）---
    abstract_parts = [_text(ab) for ab in art.findall("Abstract/AbstractText")]
    abstract = " ".join(p for p in abstract_parts if p)

    return {
        "pmid": pmid,
        "title": title,
        "authors": author_str,
        "journal": journal,
        "year": year,
        "doi": doi,
        "abstract": abstract,
    }


def efetch(pmids, common_params):
    """PMIDのリストをまとめて投げ、書誌情報の辞書リストを返す。"""
    if not pmids:
        return []
    params = dict(common_params)
    params.update({
        "db": "pubmed",
        "id": ",".join(pmids),  # カンマ区切りで一括取得（リクエスト数を節約）
        "retmode": "xml",
        "rettype": "abstract",
    })
    resp = requests.get(EFETCH_URL, params=params, timeout=60)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)  # XMLを解析

    results = []
    for article_el in root.findall("PubmedArticle"):
        parsed = parse_article(article_el)
        if parsed:
            results.append(parsed)
    return results


def main():
    cfg = load_config()
    pubmed_cfg = cfg["pubmed"]
    common = build_common_params(cfg)
    interval = pubmed_cfg.get("request_interval_sec", 0.4)
    default_window = pubmed_cfg.get("default_window_days", 7)
    total_limit = pubmed_cfg.get("total_limit", 20)

    collected = []          # 最終的に集める論文（順序維持）
    seen_pmids = set()      # 重複排除用：既に採用したPMID

    # カテゴリ順に処理（順序が優先度になる＝先のカテゴリが重複時に勝つ）
    for cat in pubmed_cfg["categories"]:
        name = cat["name"]
        count = cat["count"]
        window = cat.get("window_days", default_window)
        query = cat["query"]

        print(f"[検索] {name}（{count}件 / 過去{window}日）...", file=sys.stderr)

        try:
            # esearch は少し多めに取る（重複排除で減るぶんの余裕を持たせる）
            pmids = esearch(query, retmax=count * 2, window_days=window, common_params=common)
            time.sleep(interval)  # 頻度制限を守るための待機

            if not pmids:
                print(f"  → 0件（このカテゴリはスキップして継続）", file=sys.stderr)
                continue

            articles = efetch(pmids, common_params=common)
            time.sleep(interval)
        except requests.RequestException as e:
            # 通信エラーは致命傷にせず、そのカテゴリだけ飛ばして継続
            print(f"  → 取得失敗: {e} （スキップして継続）", file=sys.stderr)
            continue

        # このカテゴリで採用した件数
        added = 0
        for a in articles:
            if added >= count:
                break
            if a["pmid"] in seen_pmids:
                continue  # 他カテゴリで既出（PMIDで重複排除）
            a["category"] = name  # 表示でラベル分けするためカテゴリ名を付ける
            collected.append(a)
            seen_pmids.add(a["pmid"])
            added += 1

        print(f"  → {added}件 採用", file=sys.stderr)

    # 合計上限で切る
    collected = collected[:total_limit]

    # メタ情報を付けてJSON保存
    output = {
        "generated_at": datetime.datetime.now().astimezone().isoformat(),
        "count": len(collected),
        "articles": collected,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n[完了] 合計 {len(collected)} 件を {OUTPUT_PATH} に保存しました。", file=sys.stderr)


if __name__ == "__main__":
    main()
