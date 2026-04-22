"""カバー画像プロンプトビルダー - トピックカテゴリに応じてDALL-E 3プロンプトを生成"""
from dataclasses import dataclass

from agents.topic_planner import BookPlan
from config.sources_config import TOPIC_CATEGORIES


@dataclass
class CoverSpec:
    dalle_prompt: str
    width: int = 1600
    height: int = 2560    # KDP推奨縦長比率
    title_text: str = ""
    subtitle_text: str = ""
    background_color: str = "#1a1a2e"  # フォールバック色


# テキストを含まないスタイルテンプレート（Pillowで後から重ねる）
_STYLE_TEMPLATES: dict[str, str] = {
    TOPIC_CATEGORIES[0]: (
        "Professional business book cover background, Japanese modern style, "
        "vibrant blue and gold gradient, abstract digital network lines, "
        "laptop and coins floating in space, futuristic glow effects, "
        "no text, no letters, clean minimalist, 4K ultra quality"
    ),
    TOPIC_CATEGORIES[1]: (
        "AI technology book cover background, ChatGPT style, "
        "teal and white gradient, neural network patterns, "
        "glowing chat bubbles floating, digital brain illustration, "
        "no text, no letters, modern clean design, photorealistic, 4K"
    ),
    TOPIC_CATEGORIES[2]: (
        "Digital art creation book cover background, creative studio feel, "
        "purple and pink gradient, colorful paint splashes transforming into pixels, "
        "artistic canvas with AI circuit patterns, vibrant colors, "
        "no text, no letters, inspirational aesthetic, 4K"
    ),
    TOPIC_CATEGORIES[3]: (
        "Writing and content creation book cover background, "
        "warm gold and cream gradient, fountain pen transforming into digital keyboard, "
        "floating paragraphs and code symbols, elegant professional design, "
        "no text, no letters, sophisticated minimal, 4K"
    ),
    TOPIC_CATEGORIES[4]: (
        "Automation and productivity book cover background, "
        "dark navy and electric blue gradient, interconnected gears and flowcharts, "
        "robotic hands with digital interface, futuristic efficiency theme, "
        "no text, no letters, tech professional, 4K"
    ),
    TOPIC_CATEGORIES[5]: (
        "Global AI tools book cover background, world map with glowing connections, "
        "blue and silver gradient, international city skylines with digital overlays, "
        "satellite and data streams, global tech theme, "
        "no text, no letters, modern international style, 4K"
    ),
    TOPIC_CATEGORIES[6]: (
        "Success story book cover background, "
        "warm orange and gold gradient, upward trending graph, "
        "Japanese person working on laptop in modern cafe setting, "
        "achievement and growth symbols, optimistic hopeful mood, "
        "no text, no letters, lifestyle professional, 4K"
    ),
}

# デフォルトテンプレート（カテゴリ不明時）
_DEFAULT_TEMPLATE = (
    "Professional business book cover background, Japanese style, "
    "bright blue and gold gradient, AI technology theme, "
    "abstract digital patterns, clean minimalist design, "
    "no text, no letters, 4K ultra quality"
)


class CoverPromptBuilder:
    def build(self, book_plan: BookPlan) -> CoverSpec:
        """トピックカテゴリからDALL-E 3プロンプトとカバースペックを生成"""
        template = _STYLE_TEMPLATES.get(book_plan.topic_category, _DEFAULT_TEMPLATE)

        # トピック固有のキーワードをプロンプトに追加
        topic_keywords = " ".join(book_plan.keywords[:3])
        dalle_prompt = f"{template}, themed around: {topic_keywords}"

        # 背景色をカテゴリで変える（Pillowフォールバック用）
        bg_colors = {
            TOPIC_CATEGORIES[0]: "#1a1a2e",
            TOPIC_CATEGORIES[1]: "#0d4f8c",
            TOPIC_CATEGORIES[2]: "#2d1b69",
            TOPIC_CATEGORIES[3]: "#7b5e00",
            TOPIC_CATEGORIES[4]: "#0a2e4a",
            TOPIC_CATEGORIES[5]: "#1a2e4a",
            TOPIC_CATEGORIES[6]: "#7b3500",
        }
        bg_color = bg_colors.get(book_plan.topic_category, "#1a1a2e")

        return CoverSpec(
            dalle_prompt=dalle_prompt,
            width=1600,
            height=2560,
            title_text=book_plan.topic,
            subtitle_text=book_plan.subtitle,
            background_color=bg_color,
        )
