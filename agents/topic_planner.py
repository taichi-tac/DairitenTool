"""トピック企画エージェント - Claude Haikuで本日の書籍構成を決定"""
import json
import logging
import os
from dataclasses import dataclass

import anthropic

from agents.ingestion_agent import Article
from config.sources_config import DAILY_TOPIC_ROTATION, KDP_CATEGORY_MAP

logger = logging.getLogger(__name__)


@dataclass
class ChapterOutline:
    number: int
    title: str
    key_points: list[str]
    target_words: int


@dataclass
class BookPlan:
    date: str
    topic: str
    subtitle: str
    target_reader: str
    chapters: list[ChapterOutline]   # 11章
    keywords: list[str]              # Amazon KDP用 最大7個
    category_1: str
    category_2: str
    estimated_word_count: int
    topic_category: str              # TOPIC_CATEGORIES のいずれか


def _get_topic_category(date_str: str) -> str:
    """日付から7日周期でトピックカテゴリを決定"""
    from datetime import date as date_cls
    d = date_cls.fromisoformat(date_str)
    return DAILY_TOPIC_ROTATION[d.weekday()]


def _articles_to_summary(articles: list[Article]) -> str:
    """記事リストを300文字以内のプロンプト用テキストに圧縮"""
    lines = []
    for a in articles[:10]:  # 上位10件のみ（コンテキスト節約）
        lines.append(f"- [{a.source}] {a.title[:80]}")
    return "\n".join(lines)


class TopicPlanner:
    def __init__(self) -> None:
        self.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def plan(self, date_str: str, articles: list[Article], topic_category: str | None = None) -> BookPlan:
        """Claude Haikuで日次書籍計画を生成"""
        if topic_category is None:
            topic_category = _get_topic_category(date_str)

        cat1, cat2 = KDP_CATEGORY_MAP.get(topic_category, (
            "Business & Money > Personal Finance > Entrepreneurship",
            "Computers & Technology > AI & Machine Learning",
        ))

        articles_summary = _articles_to_summary(articles)

        prompt = f"""あなたはAI副業書籍の企画編集者です。本日の電子書籍の構成を設計してください。

## 今日のテーマカテゴリ
{topic_category}

## 本日の最新ニュース（参考情報）
{articles_summary}

## 要件
- 対象読者: AI副業に興味があるが、まだ始めていない20〜40代の会社員
- 書籍タイトル: 魅力的で具体的（「月5万円」「初心者でも」等の数値・具体性を含む）
- 章数: ちょうど11章
- 各章の目標文字数: 2500〜3500語
- 第11章は「次のステップ」として行動喚起の章にする
- KDPキーワード: 日本語で7個（Amazon検索で見つかりやすいもの）

## 出力形式（JSONのみ、説明文なし）
{{
  "topic": "書籍タイトル",
  "subtitle": "サブタイトル",
  "target_reader": "ターゲット読者の説明（1文）",
  "chapters": [
    {{
      "number": 1,
      "title": "章タイトル",
      "key_points": ["ポイント1", "ポイント2", "ポイント3"],
      "target_words": 2800
    }}
  ],
  "keywords": ["キーワード1", "キーワード2", "キーワード3", "キーワード4", "キーワード5", "キーワード6", "キーワード7"]
}}"""

        logger.info("書籍計画生成中 date=%s category=%s", date_str, topic_category)
        response = self.client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        # JSONブロックが```json...```で囲まれている場合に対応
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.error("BookPlan JSONパース失敗: %s\nraw=%s", e, raw[:500])
            raise

        chapters = [
            ChapterOutline(
                number=c["number"],
                title=c["title"],
                key_points=c["key_points"],
                target_words=c.get("target_words", 2800),
            )
            for c in data["chapters"]
        ]

        plan = BookPlan(
            date=date_str,
            topic=data["topic"],
            subtitle=data["subtitle"],
            target_reader=data["target_reader"],
            chapters=chapters,
            keywords=data["keywords"][:7],
            category_1=cat1,
            category_2=cat2,
            estimated_word_count=sum(c.target_words for c in chapters),
            topic_category=topic_category,
        )
        logger.info("書籍計画完成: %s", plan.topic)
        return plan
