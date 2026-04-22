"""情報収集ソース定義 - 海外・国内AIニュースソース"""
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class NewsSource:
    name: str
    url: str
    trust_score: float  # 0.0-1.0
    category: str
    language: Literal["en", "ja", "zh"]
    freshness_hours: int = 72


# 最高信頼度 (trust_score=1.0): 査読済み・公式機関・大手メディア
TIER_1_SOURCES: list[NewsSource] = [
    NewsSource("arXiv AI", "https://arxiv.org/rss/cs.AI", 1.0, "AI研究", "en"),
    NewsSource("MIT Technology Review", "https://www.technologyreview.com/feed/", 1.0, "AI技術", "en"),
    NewsSource("Wired AI", "https://www.wired.com/feed/tag/artificial-intelligence/rss", 1.0, "AI技術", "en"),
    NewsSource("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/", 1.0, "AIビジネス", "en"),
    NewsSource("Anthropic Blog", "https://www.anthropic.com/rss.xml", 1.0, "AI開発", "en"),
    NewsSource("OpenAI Blog", "https://openai.com/blog/rss.xml", 1.0, "AI開発", "en"),
    NewsSource("Google AI Blog", "https://blog.research.google/atom.xml", 1.0, "AI研究", "en"),
]

# 高信頼度 (trust_score=0.85): 信頼性の高い業界メディア・コミュニティ
TIER_2_SOURCES: list[NewsSource] = [
    NewsSource("HackerNews", "https://hnrss.org/frontpage", 0.85, "テック全般", "en"),
    NewsSource("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", 0.85, "AIビジネス", "en"),
    NewsSource("VentureBeat AI", "https://venturebeat.com/category/ai/feed/", 0.85, "AIビジネス", "en"),
    NewsSource("a16z Blog", "https://a16z.com/feed/", 0.85, "AIスタートアップ", "en"),
    NewsSource("Product Hunt", "https://www.producthunt.com/feed", 0.85, "新AIツール", "en"),
    NewsSource("Ben's Bites", "https://bensbites.beehiiv.com/feed", 0.85, "AI副業活用", "en"),
    NewsSource("AI Tool Report", "https://aitoolreport.beehiiv.com/feed", 0.85, "AIツール", "en"),
]

# 参考 (trust_score=0.7): アジア圏・日本語・コミュニティ系
TIER_3_SOURCES: list[NewsSource] = [
    NewsSource("TechNode (中国AI英語版)", "https://technode.com/feed/", 0.7, "アジアAI", "en"),
    NewsSource("e27 (東南アジア)", "https://e27.co/feed/", 0.7, "アジアAIビジネス", "en"),
    NewsSource("KrASIA", "https://kr-asia.com/feed", 0.7, "アジアテック", "en"),
    NewsSource("Zenn トレンド", "https://zenn.dev/feed", 0.7, "日本AI開発", "ja"),
    NewsSource("Qiita トレンド", "https://qiita.com/trend.atom", 0.7, "日本AI開発", "ja"),
    NewsSource("note AI", "https://note.com/hashtag/AI.rss", 0.7, "日本AI副業", "ja"),
]

ALL_SOURCES: list[NewsSource] = TIER_1_SOURCES + TIER_2_SOURCES + TIER_3_SOURCES

# 7つのトピックカテゴリ（書籍テーマ）
TOPIC_CATEGORIES: list[str] = [
    "AI副業・フリーランスで収入を得る方法",
    "ChatGPT・Claude徹底活用術2026年版",
    "画像生成AIで副業を始める完全ガイド",
    "AIライティングで月収を上げる実践術",
    "AI自動化ツールで時間を生み出す方法",
    "海外最新AIツール活用術：日本人が知らない副業法",
    "AI収益化事例集：初心者が実際に稼いだ方法",
]

# 7日周期トピックローテーション（weekday 0=月曜日）
# 同じ日には同じトピックが選ばれる → 代理店全員で統一された書籍シリーズになる
DAILY_TOPIC_ROTATION: dict[int, str] = {
    0: TOPIC_CATEGORIES[0],  # 月
    1: TOPIC_CATEGORIES[1],  # 火
    2: TOPIC_CATEGORIES[2],  # 水
    3: TOPIC_CATEGORIES[3],  # 木
    4: TOPIC_CATEGORIES[4],  # 金
    5: TOPIC_CATEGORIES[5],  # 土
    6: TOPIC_CATEGORIES[6],  # 日
}

# Amazon KDPカテゴリマッピング（トピック → KDPカテゴリ）
KDP_CATEGORY_MAP: dict[str, tuple[str, str]] = {
    TOPIC_CATEGORIES[0]: ("Business & Money > Personal Finance > Entrepreneurship", "Computers & Technology > AI & Machine Learning"),
    TOPIC_CATEGORIES[1]: ("Computers & Technology > AI & Machine Learning", "Business & Money > Career Guides"),
    TOPIC_CATEGORIES[2]: ("Arts & Photography > Digital Art", "Business & Money > Personal Finance > Entrepreneurship"),
    TOPIC_CATEGORIES[3]: ("Reference > Writing, Research & Publishing Guides", "Business & Money > Career Guides"),
    TOPIC_CATEGORIES[4]: ("Business & Money > Management & Leadership > Management Science", "Computers & Technology > AI & Machine Learning"),
    TOPIC_CATEGORIES[5]: ("Computers & Technology > AI & Machine Learning", "Business & Money > Personal Finance > Entrepreneurship"),
    TOPIC_CATEGORIES[6]: ("Business & Money > Personal Finance > Entrepreneurship", "Biographies & Memoirs > Leaders & Notable People > Rich & Famous"),
}
