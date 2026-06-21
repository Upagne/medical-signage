#!/usr/bin/env python3
# =============================================================
# summarize.py — Claude API で各文献を日本語200字以内に要約する（Step 2）
#
# 流れ:
#   1. articles.json（Step1の出力）を読む
#   2. 各記事のタイトル+Abstractを Claude(Haiku) に渡して日本語要約
#   3. 結果を summary フィールドに追加して articles_summarized.json に保存
#   4. 確認用に標準出力へ要約を表示し、推定トークン数・概算コストを出す
#
# 単体実行: python src/summarize.py
# 事前準備: .env または環境変数 ANTHROPIC_API_KEY が必要
# =============================================================

import os
import sys
import json
import time

import yaml

# anthropic SDK（pip install anthropic）。未インストールなら親切にエラー表示。
try:
    import anthropic
except ImportError:
    print("anthropic ライブラリが未インストールです。`pip install -r requirements.txt` を実行してください。", file=sys.stderr)
    sys.exit(1)

# .env を読み込む（python-dotenv）。無くても環境変数があれば動く。
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.yaml")
INPUT_PATH = os.path.join(PROJECT_ROOT, "articles.json")
NEWS_PATH = os.path.join(PROJECT_ROOT, "news.json")
OUTPUT_PATH = os.path.join(PROJECT_ROOT, "articles_summarized.json")

# --- 要約プロンプト（ハルシネーション対策を明記。後で編集しやすいよう定数化）---
# 創作を禁じ、原Abstractの事実のみを反映させる指示を強めにかけている。
SUMMARY_SYSTEM_PROMPT = """あなたは医学文献を診療放射線技師向けに要約する専門アシスタントです。
【最重要・字数制限】要約は必ず日本語{max_chars}字以内。これは画面表示の物理的制約であり厳守。
{max_chars}字に収めるため、最も重要な1〜2点（結論と臨床的意義）だけに絞り、細かい数値の羅列は省く。
以下のルールも厳格に守る。
- 与えられたタイトルとAbstractに書かれた事実だけを使う。原文に無い情報を絶対に加えない。
- 数値・固有名詞・単位・薬剤名・デバイス名・略語を改変しない。固有名詞は原綴りのまま残してよい。
- 代表的な数値は1〜2個まで。すべての測定値を列挙しない。
- 推測や一般論で補わない。不明な点は要約に含めない。
- ですます調を使わず簡潔な体言止め/常体。技師の実務（線量・画質・安全・手技）に役立つ要点を優先。
- 【重要】Abstractが空・極端に短い・雑誌情報のみで内容が不十分な場合は、謝罪や弁解を一切書かず、
  タイトルを自然な日本語の見出しに訳した一文だけを出力する（タイトルに無い内容は決して足さない）。
要約本文のみを出力し、前置きや「要約:」などのラベルは付けない。出力前に{max_chars}字以内か必ず確認する。"""

# 本文が薄い記事用：タイトルの和訳だけを出す（創作を完全に防ぐ）。
# Abstractが無い/極端に短い場合にこちらを使う。
TITLE_ONLY_SYSTEM_PROMPT = """次の医学論文・記事のタイトルを、自然で簡潔な日本語の見出しに翻訳してください。
- 翻訳のみを出力。タイトルに書かれていない背景・方法・結果・結論を絶対に足さない。
- 数値・固有名詞・薬剤名・デバイス名は改変しない。固有名詞は原綴り併記可。
- 前置きやラベルを付けず、見出し文だけを出力する。"""

# Abstractをこの文字数以上持つ記事だけ「内容要約」する。未満はタイトル和訳に切替。
MIN_ABSTRACT_CHARS = 200

# ユーザーメッセージのテンプレート（記事ごとに差し込む）
USER_TEMPLATE = """タイトル: {title}

Abstract:
{abstract}"""


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def enforce_limit(text, max_chars):
    """字数上限を確実に守る安全網。
    プロンプトだけだとモデルが超過しがちなので、最後の手段として機械的に切る。
    途中で切れて意味が壊れないよう、上限以内に収まる最後の「。」までで止める。
    「。」が見つからなければ上限で切って「…」を付ける。
    """
    if len(text) <= max_chars:
        return text
    head = text[:max_chars]
    last_period = head.rfind("。")
    if last_period >= max_chars * 0.5:  # 半分以上の位置に句点があればそこで切る
        return head[:last_period + 1]
    return head.rstrip() + "…"


def summarize_one(client, model, system_prompt, max_tokens, title, abstract, max_retries):
    """1記事を要約して文字列を返す。失敗時は空文字を返す（白紙化を避ける）。"""
    user_msg = USER_TEMPLATE.format(title=title, abstract=abstract)
    for attempt in range(max_retries):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_msg}],
            )
            # 入力・出力トークン数を返す（コスト集計に使う）
            text = "".join(block.text for block in resp.content if block.type == "text").strip()
            return text, resp.usage.input_tokens, resp.usage.output_tokens
        except anthropic.APIStatusError as e:
            # 429(レート制限)/5xx などは待って再試行
            wait = 2 ** attempt
            print(f"    API一時エラー({e.status_code}) リトライ {attempt+1}/{max_retries}（{wait}秒待機）", file=sys.stderr)
            time.sleep(wait)
        except anthropic.APIError as e:
            print(f"    APIエラー: {e}", file=sys.stderr)
            break
    return "", 0, 0


def main():
    cfg = load_config()
    scfg = cfg["summarize"]

    # APIキーの存在確認（無ければ何をすべきか案内して終了）
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("環境変数 ANTHROPIC_API_KEY が見つかりません。", file=sys.stderr)
        print("プロジェクト直下に .env を作り `ANTHROPIC_API_KEY=sk-ant-...` を記載してください。", file=sys.stderr)
        sys.exit(1)

    # 入力（Step1の文献）を読む
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    articles = data["articles"]

    # ニュース（Step4の出力）があれば合流させる。無ければ文献だけで継続。
    if os.path.exists(NEWS_PATH):
        with open(NEWS_PATH, "r", encoding="utf-8") as f:
            news = json.load(f)
        articles.extend(news.get("articles", []))
        print(f"[合流] ニュース {news.get('count', 0)} 件を追加（文献と合わせて要約）", file=sys.stderr)

    client = anthropic.Anthropic()  # APIキーは環境変数から自動で読まれる
    model = scfg["model"]
    max_tokens = scfg.get("max_tokens", 400)
    max_retries = scfg.get("max_retries", 3)
    max_chars = scfg.get("max_chars", 200)
    system_prompt = SUMMARY_SYSTEM_PROMPT.format(max_chars=max_chars)

    total_in = 0
    total_out = 0
    for i, a in enumerate(articles, 1):
        abstract = (a.get("abstract") or "").strip()
        # Abstractが十分にあれば内容要約、薄ければタイトル和訳に切替（創作防止）。
        if len(abstract) >= MIN_ABSTRACT_CHARS:
            prompt = system_prompt
            mode = "要約"
        else:
            prompt = TITLE_ONLY_SYSTEM_PROMPT
            mode = "題名訳"  # 本文不足のためタイトルのみ和訳
            abstract = abstract or "(Abstractなし)"
        print(f"[{i}/{len(articles)}][{mode}] {a['title'][:45]}...", file=sys.stderr)
        summary, in_tok, out_tok = summarize_one(
            client, model, prompt, max_tokens, a["title"], abstract, max_retries
        )
        # 字数上限を機械的に保証（プロンプト超過分を文末で安全に切る）
        if summary:
            summary = enforce_limit(summary, max_chars)
        a["summary"] = summary
        total_in += in_tok
        total_out += out_tok
        # コンソールに確認表示
        print(f"    → {summary}\n", file=sys.stderr)

    # 保存
    data["summarized_count"] = sum(1 for a in articles if a.get("summary"))
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # --- コスト概算 ---
    # 【要確認】Haikuの単価は変動するため、最新値は料金ページで確認すること。
    # ここでは目安として「入力 $1.00 / 出力 $5.00（100万トークンあたり）」と仮置き。
    in_price, out_price = 1.00, 5.00  # USD / 1M tokens（暫定・要確認）
    cost = total_in / 1_000_000 * in_price + total_out / 1_000_000 * out_price
    print("=" * 50, file=sys.stderr)
    print(f"[完了] {data['summarized_count']}/{len(articles)} 件を要約 → {OUTPUT_PATH}", file=sys.stderr)
    print(f"  入力 {total_in:,} tok / 出力 {total_out:,} tok", file=sys.stderr)
    print(f"  概算コスト ≈ ${cost:.4f}（単価は暫定・要確認）", file=sys.stderr)


if __name__ == "__main__":
    main()
