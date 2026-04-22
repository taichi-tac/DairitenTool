"""章生成エージェント - Claude Sonnetで各章の本文をMarkdown形式で生成"""
import asyncio
import logging
import os
from dataclasses import dataclass

import anthropic

from agents.ingestion_agent import Article
from agents.topic_planner import BookPlan, ChapterOutline

logger = logging.getLogger(__name__)

# 生成品質を決めるシステムプロンプト（Constitutional Rules埋め込み）
SYSTEM_PROMPT = """あなたはAI副業の実践専門家であり、初心者向け書籍の著者です。

## 絶対ルール（1つでも違反した章は品質ゲートで却下されます）

RULE-001: 「〜が重要です」「〜が必要です」だけで終わる節は禁止
          → 必ず具体的なツール名・URL・実行手順まで書くこと

RULE-002: 数値・統計には根拠を示す
          → 出典がある場合: 「（出典: ○○）」
          → 出典不明の場合: 「〜とも言われています」「〜という報告もあります」
          → 出典なし断定（「AI副業市場は年率30%成長」等）は禁止

RULE-003: 講座CTAは第11章の末尾のみ、プレースホルダーを使う
          → [COURSE_CTA_PLACEHOLDER] のみ使用すること
          → 他の章でのセールス文は禁止

RULE-004: 海外情報は日本人向けに文脈変換する
          → ❌「米国ではXXXが人気です」
          → ✅「米国発のXXXは日本でも使えます。理由は〜だからです。実際の使い方は〜」

RULE-005: 読者が読み終えた後、今すぐ1ステップ踏み出せる内容にする
          → 各章の末尾に「今すぐできるアクション」セクションを必ず含める
          → アクションは1〜3個、具体的で5分以内に始められるもの

RULE-006: 最低2,500語
          → 「今すぐできるアクション」セクションを含む

## 文体ガイドライン
- 親しみやすく、でも誠実で根拠ある説明
- 専門用語は初出時に必ず説明
- 箇条書きと文章を適切に組み合わせる
- 実際の体験談・事例を織り交ぜる（具体的に）
"""


@dataclass
class Chapter:
    number: int
    title: str
    content: str            # Markdown形式
    word_count: int
    has_action_steps: bool
    has_tool_recommendations: bool


def _count_words(text: str) -> int:
    """日本語テキストの語数を文字数で近似（日本語1文字≒1語）"""
    return len(text.replace(" ", "").replace("\n", ""))


def _check_action_steps(content: str) -> bool:
    return "今すぐできるアクション" in content or "アクションステップ" in content


def _check_tool_recommendations(content: str) -> bool:
    tool_keywords = ["ChatGPT", "Claude", "Midjourney", "Stable Diffusion", "DALL-E",
                     "Canva", "Notion", "Zapier", "Make", "n8n", "Copilot", "Gemini"]
    return sum(1 for kw in tool_keywords if kw in content) >= 2


class ChapterGenerator:
    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def _build_prompt(self, outline: ChapterOutline, book_plan: BookPlan,
                      articles: list[Article]) -> str:
        key_points_str = "\n".join(f"- {p}" for p in outline.key_points)
        articles_str = "\n".join(
            f"- [{a.source}] {a.title}: {a.summary[:150]}"
            for a in articles[:5]
        )
        is_last_chapter = outline.number == len(book_plan.chapters)
        cta_instruction = ""
        if is_last_chapter:
            cta_instruction = """
## この章（第11章）のCTA
章の末尾に以下のプレースホルダーをそのまま配置してください:
[COURSE_CTA_PLACEHOLDER]
「より体系的に学びたい方は、こちらで詳細をご確認ください」という自然な文脈で。
"""
        return f"""## 書籍情報
タイトル: {book_plan.topic}
対象読者: {book_plan.target_reader}

## この章（第{outline.number}章）
タイトル: {outline.title}
目標文字数: {outline.target_words}語以上

## 必ず含めるポイント
{key_points_str}

## 参考にする最新ニュース（事例として活用してください）
{articles_str}
{cta_instruction}
## 出力形式
- Markdown形式（# タイトルから始める）
- 「今すぐできるアクション」セクションを末尾に含める
- 最低2,500語
"""

    def generate_chapter(self, outline: ChapterOutline, book_plan: BookPlan,
                         articles: list[Article]) -> Chapter:
        """1章を同期生成"""
        logger.info("第%d章生成中: %s", outline.number, outline.title)
        prompt = self._build_prompt(outline, book_plan, articles)

        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8000,
            temperature=0.7,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.content[0].text

        chapter = Chapter(
            number=outline.number,
            title=outline.title,
            content=content,
            word_count=_count_words(content),
            has_action_steps=_check_action_steps(content),
            has_tool_recommendations=_check_tool_recommendations(content),
        )
        logger.info("第%d章完了: %d文字", outline.number, chapter.word_count)
        return chapter

    async def generate_chapter_async(self, outline: ChapterOutline, book_plan: BookPlan,
                                      articles: list[Article]) -> Chapter:
        """asyncioのイベントループからブロッキングAPIを呼び出す"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.generate_chapter, outline, book_plan, articles)

    async def generate_all(self, book_plan: BookPlan, articles: list[Article]) -> list[Chapter]:
        """全11章を生成（最大3章並列、レート制限対策）"""
        semaphore = asyncio.Semaphore(3)  # Claude APIのレート制限を考慮

        async def bounded_generate(outline: ChapterOutline) -> Chapter:
            async with semaphore:
                return await self.generate_chapter_async(outline, book_plan, articles)

        tasks = [bounded_generate(outline) for outline in book_plan.chapters]
        chapters = await asyncio.gather(*tasks)
        # chapter.numberで昇順にソート（並列実行で順番がずれる場合がある）
        return sorted(chapters, key=lambda c: c.number)
