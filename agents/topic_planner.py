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

        current_year = date_str[:4]  # e.g. "2026"

        prompt = f"""あなたはAI副業書籍の企画編集者です。本日の電子書籍の構成を設計してください。

## 重要：現在の日付
本日は{date_str}です。現在の年は{current_year}年です。
章タイトルや書籍タイトルに年号を記載する場合は必ず{current_year}年を使用してください。
「2024年」「2025年」は過去の年であり、タイトルや本文への使用は禁止です。

## 今日のテーマカテゴリ
{topic_category}

## 本日の最新ニュース（{date_str}前後72時間以内の情報）
{articles_summary}

## 要件
- 対象読者: AI副業に興味があるが、まだ始めていない20〜40代の会社員
- 書籍タイトル: 魅力的で具体的（「月5万円」「初心者でも」等の数値・具体性を含む）
- 章数: ちょうど11章
- 各章の目標文字数: 2500〜3500語
- 第11章は「次のステップ」として行動喚起の章にする
- KDPキーワード: 日本語で7個（Amazon検索で見つかりやすいもの）

## 絶対に章テーマにしてはいけない手法

【規制・BAN対象リスクがある手法 → 絶対禁止】
- アダルト・成人向けコンテンツ（Amazon KDP利用規約違反）
- AI動画で再生数稼ぎ・広告収入狙い（YouTubeスパムポリシー違反、収益化停止リスク）
- AI生成コンテンツの大量投稿によるSEO操作（Googleペナルティ対象）
- Bot・自動フォロワー購入・自動いいね（各プラットフォーム利用規約違反）

【稼げないと広く知られている手法 → 推奨禁止】
- LINEスタンプ販売（月数百円が現実）
- ポイントサイト・アンケートモニター（時給換算で最低賃金以下）
- スマホ内職・データ入力（AI代替で単価崩壊）
- せどり・転売（初心者参入困難、規制強化）

これらは絶対に章タイトル・key_points・事例に含めないこと。

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
            max_tokens=4096,  # 11章分のJSONは2000では不足するため増量
            messages=[{"role": "user", "content": prompt}],
        )

        raw = response.content[0].text.strip()
        # ```json ... ``` ブロックの除去
        if "```" in raw:
            parts = raw.split("```")
            for part in parts:
                if part.startswith("json"):
                    raw = part[4:].strip()
                    break
                elif part.strip().startswith("{"):
                    raw = part.strip()
                    break

        # JSONの先頭・末尾を { } で切り出す（前後に余分なテキストがある場合）
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            raw = raw[start:end]

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
