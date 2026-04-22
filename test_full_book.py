"""
フル書籍生成テスト（11章 → EPUB → KDP ZIP）
実行: python test_full_book.py

出力: output/test_book/ に以下が生成される
  - book_TEST01.epub      （KDPにアップロードするファイル）
  - KDP_TEST01.zip        （EPUBを含む代理店配布パッケージ）
  - upload_guide.html     （代理店向けKDPアップロード手順）

トラッキングURL: https://yourcourse.jp?ref=test01
  → Googleアナリティクスで ref=test01 の流入を確認するだけでOK
"""
import asyncio
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

if not os.environ.get("ANTHROPIC_API_KEY"):
    print("ERROR: .env に ANTHROPIC_API_KEY が設定されていません")
    sys.exit(1)

CURRENT_DATE = "2026-04-22"
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "D:/cursor/DairitenTool/output")) / "test_book"

# テスト用代理店（DBなし・ハードコード）
TEST_AFFILIATE_ID = "TEST01"
TEST_TRACKING_URL = f"{os.environ.get('COURSE_BASE_URL', 'https://yourcourse.jp')}?ref={TEST_AFFILIATE_ID.lower()}"


async def main():
    print("=" * 55)
    print("フル書籍生成テスト（11章 + EPUB + KDP ZIP）")
    print("=" * 55)
    print(f"  出力先: {OUTPUT_DIR}")
    print(f"  トラッキングURL: {TEST_TRACKING_URL}")
    print()

    # ── STEP 1: ニュース収集 ────────────────────────────────
    print("[1/5] 海外AIニュース収集中...")
    from agents.ingestion_agent import IngestionAgent
    agent = IngestionAgent(top_k=10)
    articles = await agent.run("ChatGPT・Claude徹底活用術2026年版", CURRENT_DATE)
    print(f"      → {len(articles)} 件収集完了")

    # ── STEP 2: 書籍プラン生成 ──────────────────────────────
    print("\n[2/5] 書籍プラン生成中 (Claude Haiku)...")
    from agents.topic_planner import TopicPlanner
    planner = TopicPlanner()
    plan = planner.plan(CURRENT_DATE, articles)
    print(f"      → タイトル : {plan.topic}")
    print(f"      → 章数    : {len(plan.chapters)}")

    # ── STEP 3: 全11章生成 ─────────────────────────────────
    print(f"\n[3/5] 全{len(plan.chapters)}章を生成中 (Claude Sonnet・最大3章並列)...")
    print("      ※ APIコストがかかります。完了まで約5〜10分かかります")
    from generators.chapter_generator import ChapterGenerator
    gen = ChapterGenerator()
    chapters = await gen.generate_all(plan, articles)
    total_chars = sum(c.word_count for c in chapters)
    print(f"      → {len(chapters)}章 完了 / 合計 {total_chars:,} 文字")
    for c in chapters:
        print(f"        第{c.number:2d}章 [{c.word_count:,}文字] {c.title[:40]}")

    # ── STEP 4: EPUB生成 ────────────────────────────────────
    print("\n[4/5] EPUB生成中 (Pandoc)...")
    from generators.epub_builder import Affiliate, EpubBuilder
    affiliate = Affiliate(
        id=TEST_AFFILIATE_ID,
        name="テスト代理店",
        display_name="テスト太郎",
        tracking_url=TEST_TRACKING_URL,
        email="test@example.com",
        kdp_pen_name="AI副業研究所",
    )
    builder = EpubBuilder()
    epub_path = builder.build(chapters, plan, affiliate, OUTPUT_DIR)
    epub_size_kb = epub_path.stat().st_size / 1024
    print(f"      → {epub_path.name} ({epub_size_kb:.0f}KB)")

    # ── STEP 5: KDP ZIPパッケージ生成 ──────────────────────
    print("\n[5/5] KDP ZIPパッケージ生成中...")
    from publishers.kdp_package_builder import KdpPackageBuilder
    pkg_builder = KdpPackageBuilder()
    package = pkg_builder.build_variant(
        master_epub=epub_path,
        cover_image=None,        # 表紙は後日DALL-E生成（今は省略）
        book_plan=plan,
        affiliate=affiliate,
        output_dir=OUTPUT_DIR,
    )
    zip_size_kb = package.zip_path.stat().st_size / 1024
    print(f"      → {package.zip_path.name} ({zip_size_kb:.0f}KB)")
    print(f"      → SHA256: {package.sha256[:16]}...")

    # ── 完了サマリー ────────────────────────────────────────
    print("\n" + "=" * 55)
    print("完了！")
    print(f"  書籍タイトル  : {plan.topic}")
    print(f"  合計文字数    : {total_chars:,} 文字")
    print(f"  EPUB          : {epub_path}")
    print(f"  KDP ZIP       : {package.zip_path}")
    print(f"  トラッキングURL: {TEST_TRACKING_URL}")
    print("=" * 55)
    print()
    print("次のステップ:")
    print("  1. ZIPを解凍し、upload_guide.html を開く")
    print("  2. ガイドに従って book_TEST01.epub を KDPにアップロード")
    print("  3. 出版後、書籍内URLクリックを Googleアナリティクスで確認")
    print(f"     → ref=test01 の流入が来ていれば成功")


if __name__ == "__main__":
    asyncio.run(main())
