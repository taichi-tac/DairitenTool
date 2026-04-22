"""
ローカルテストスクリプト
実行: python test_pipeline.py

必要なもの:
  - .env に ANTHROPIC_API_KEY を設定
  - pip install -r requirements.txt
  - Pandoc インストール済み (winget install JohnMacFarlane.Pandoc)
"""
import asyncio
import os
import sys
from pathlib import Path

# Windowsコンソールの文字化け対策
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

# プロジェクトルートをPythonパスに追加
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

if not os.environ.get("ANTHROPIC_API_KEY"):
    print("ERROR: .env に ANTHROPIC_API_KEY が設定されていません")
    sys.exit(1)


async def main():
    print("=" * 50)
    print("KDP パイプライン ローカルテスト")
    print("=" * 50)

    # ── STEP 1: 情報収集 ────────────────────────────────
    print("\n[1/4] 海外AIニュース収集中...")
    from agents.ingestion_agent import IngestionAgent
    agent = IngestionAgent(top_k=10)
    articles = await agent.run("ChatGPT・Claude徹底活用術2026年版", "2026-04-22")
    print(f"      → {len(articles)} 件収集完了")
    for a in articles[:3]:
        print(f"        - [{a.source}] {a.title[:60]}")

    # ── STEP 2: 書籍プラン生成 ───────────────────────────
    print("\n[2/4] 書籍プラン生成中 (Claude Haiku)...")
    from agents.topic_planner import TopicPlanner
    planner = TopicPlanner()
    plan = planner.plan("2026-04-22", articles)
    print(f"      → タイトル: {plan.topic}")
    print(f"      → 章数: {len(plan.chapters)}")
    print(f"      → キーワード: {', '.join(plan.keywords[:3])}...")

    # ── STEP 3: 第1章だけ生成（全章生成はコストがかかるため） ──
    print("\n[3/4] 第1章を生成中 (Claude Sonnet)...")
    from generators.chapter_generator import ChapterGenerator
    gen = ChapterGenerator()
    chapter = gen.generate_chapter(plan.chapters[0], plan, articles)
    print(f"      → タイトル: {chapter.title}")
    print(f"      → 文字数: {chapter.word_count:,}")
    print(f"      → アクションセクション: {'あり' if chapter.has_action_steps else 'なし'}")
    print(f"      → ツール推奨: {'あり' if chapter.has_tool_recommendations else 'なし'}")

    # ── STEP 4: 品質ゲート ───────────────────────────────
    print("\n[4/4] 品質ゲート実行中...")
    from quality.gates import QualityGates
    gates = QualityGates()
    result = gates.constitutional_review(chapter)
    print(f"      → Constitutional Review: {'PASS' if result.severity == 'ok' else result.severity.upper()}")
    if result.violations:
        for v in result.violations[:2]:
            print(f"        ! {v}")

    score = await gates.score(chapter)
    print(f"      → 品質スコア: {score.overall}/5.0 ({'PASS' if score.passed else 'FAIL'})")
    print(f"        正確性={score.accuracy} 読みやすさ={score.readability} 独自性={score.originality}")
    print(f"        実用性={score.value} ブランド安全={score.brand_safety}")

    # ── 結果サマリー ─────────────────────────────────────
    print("\n" + "=" * 50)
    print("テスト完了！")
    print(f"  書籍タイトル : {plan.topic}")
    print(f"  第1章文字数  : {chapter.word_count:,} 文字")
    print(f"  品質スコア   : {score.overall}/5.0")
    print("=" * 50)

    if score.overall >= 4.2:
        print("\n品質ゲート通過。本番パイプライン実行の準備ができています。")
    else:
        print(f"\n品質スコアが基準(4.2)を下回っています: {score.overall}")
        print(f"改善コメント: {score.feedback}")


if __name__ == "__main__":
    asyncio.run(main())
