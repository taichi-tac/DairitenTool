"""情報収集エージェント - 海外・国内AIニュースを並列取得してフィルタリング"""
import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import feedparser
import httpx
from dateutil import parser as dateparser

from config.sources_config import NewsSource, ALL_SOURCES

logger = logging.getLogger(__name__)

# 72時間より古い記事は除外（AI情報は鮮度が命）
DEFAULT_FRESHNESS_HOURS = 72


@dataclass
class Article:
    title: str
    url: str
    summary: str
    published_at: datetime
    source: str
    trust_score: float
    language: str
    relevance_score: float = 0.0

    def title_hash(self) -> str:
        """タイトル先頭50文字のMD5ハッシュ（重複排除用）"""
        key = self.title[:50].lower().strip()
        return hashlib.md5(key.encode()).hexdigest()


class IngestionAgent:
    def __init__(self, max_articles: int = 50, top_k: int = 20):
        self.max_articles = max_articles
        self.top_k = top_k

    async def _fetch_one(self, source: NewsSource, client: httpx.AsyncClient) -> list[Article]:
        """1ソースをフェッチ。失敗はskip_and_log（パイプラインを止めない）"""
        try:
            resp = await client.get(source.url, timeout=15.0, follow_redirects=True)
            resp.raise_for_status()
            feed = feedparser.parse(resp.text)
            articles = []
            for entry in feed.entries[:20]:
                try:
                    pub_raw = entry.get("published", entry.get("updated", ""))
                    pub_dt = dateparser.parse(pub_raw) if pub_raw else datetime.now(timezone.utc)
                    if pub_dt.tzinfo is None:
                        pub_dt = pub_dt.replace(tzinfo=timezone.utc)
                    articles.append(Article(
                        title=entry.get("title", "").strip(),
                        url=entry.get("link", ""),
                        summary=entry.get("summary", "")[:500],
                        published_at=pub_dt,
                        source=source.name,
                        trust_score=source.trust_score,
                        language=source.language,
                    ))
                except Exception as e:
                    logger.warning("記事パース失敗 source=%s title=%s err=%s", source.name, entry.get("title", ""), e)
            return articles
        except Exception as e:
            # ソース1件の失敗はパイプライン全体を止めない
            logger.warning("ソース取得失敗 source=%s url=%s err=%s", source.name, source.url, e)
            return []

    async def fetch_all(self, sources: list[NewsSource]) -> list[Article]:
        """全ソースを最大10並列でフェッチ"""
        semaphore = asyncio.Semaphore(10)

        async def bounded_fetch(source: NewsSource, client: httpx.AsyncClient) -> list[Article]:
            async with semaphore:
                return await self._fetch_one(source, client)

        async with httpx.AsyncClient(headers={"User-Agent": "KDP-Pipeline/1.0"}) as client:
            tasks = [bounded_fetch(s, client) for s in sources]
            results = await asyncio.gather(*tasks)

        all_articles: list[Article] = []
        for batch in results:
            all_articles.extend(batch)
        return all_articles

    def filter_fresh(self, articles: list[Article], hours: int = DEFAULT_FRESHNESS_HOURS) -> list[Article]:
        """指定時間以内の記事のみ返す"""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        fresh = [a for a in articles if a.published_at >= cutoff]
        logger.info("鮮度フィルタ: %d件 → %d件 (cutoff=%s)", len(articles), len(fresh), cutoff.isoformat())
        return fresh

    def dedupe(self, articles: list[Article]) -> list[Article]:
        """タイトルハッシュで重複排除（同ニュースが複数ソースに載る場合は信頼度高い方を残す）"""
        seen: dict[str, Article] = {}
        for article in articles:
            h = article.title_hash()
            if h not in seen or article.trust_score > seen[h].trust_score:
                seen[h] = article
        deduped = list(seen.values())
        logger.info("重複排除: %d件 → %d件", len(articles), len(deduped))
        return deduped

    def score_relevance(self, articles: list[Article], topic: str) -> list[Article]:
        """トピックキーワードとのマッチ数でrelevance_scoreを設定"""
        ai_keywords = ["AI", "artificial intelligence", "ChatGPT", "Claude", "LLM", "GPT",
                       "machine learning", "automation", "副業", "freelance", "income", "side hustle",
                       "tool", "generate", "prompt", "workflow", "productivity"]
        topic_words = topic.lower().split()

        for article in articles:
            text = (article.title + " " + article.summary).lower()
            # AIキーワードマッチ数（基本スコア）
            ai_matches = sum(1 for kw in ai_keywords if kw.lower() in text)
            # トピック単語マッチ数（追加スコア）
            topic_matches = sum(1 for w in topic_words if len(w) > 3 and w in text)
            # 信頼度スコアも加味（0.7-1.0の信頼度を0.0-0.3のボーナスとして換算）
            trust_bonus = (article.trust_score - 0.7) * 1.5
            article.relevance_score = min(5.0, ai_matches * 0.3 + topic_matches * 0.5 + trust_bonus)
        return articles

    async def run(self, topic: str, date: str) -> list[Article]:
        """メイン実行: fetch → filter → dedupe → score → 上位top_k件返却"""
        logger.info("情報収集開始 topic=%s date=%s", topic, date)
        articles = await self.fetch_all(ALL_SOURCES)
        articles = self.filter_fresh(articles)
        articles = self.dedupe(articles)
        articles = self.score_relevance(articles, topic)
        articles.sort(key=lambda a: a.relevance_score, reverse=True)
        result = articles[:self.top_k]
        logger.info("情報収集完了: %d件取得", len(result))
        return result
