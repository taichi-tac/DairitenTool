"""EPUBビルダー - Pandocを使ってMarkdown章をEPUB3に変換"""
import hashlib
import logging
import os
import subprocess
import tempfile
import yaml
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

AI_DISCLOSURE = """

---

## 本書について

本書はAI（人工知能）を活用して執筆され、編集・品質確認を経て出版されています。
これはAI活用の透明性確保のためのご案内です（Amazon KDP AI開示要件準拠）。

"""

EPUB_CSS = """
body { font-family: "Hiragino Sans", "Yu Gothic", sans-serif; line-height: 1.8; font-size: 1em; }
h1 { font-size: 1.8em; margin-top: 2em; border-bottom: 2px solid #333; padding-bottom: 0.3em; }
h2 { font-size: 1.4em; margin-top: 1.5em; color: #1a1a2e; }
h3 { font-size: 1.2em; margin-top: 1.2em; }
blockquote { border-left: 4px solid #ccc; padding-left: 1em; color: #555; }
code { background: #f4f4f4; padding: 0.2em 0.4em; border-radius: 3px; font-family: monospace; }
ul, ol { padding-left: 1.5em; }
li { margin: 0.4em 0; }
"""


@dataclass
class Affiliate:
    id: str             # "AF001"
    name: str
    display_name: str
    tracking_url: str   # https://yourcourse.jp/r/AF001
    email: str
    kdp_pen_name: str


class EpubBuilder:
    def __init__(self) -> None:
        self._check_pandoc()

    def _check_pandoc(self) -> None:
        """Pandocがインストールされているか確認（Shift Left原則: 早期失敗）"""
        result = subprocess.run(["pandoc", "--version"], capture_output=True, timeout=10)
        if result.returncode != 0:
            raise ValueError(
                "Pandocがインストールされていません。\n"
                "インストール: https://pandoc.org/installing.html"
            )

    def build(self, chapters: list, book_plan, affiliate: Affiliate,
              output_dir: Path) -> Path:
        """
        Markdown章リスト → EPUB3ファイルを生成して返す。
        CTAプレースホルダーを affiliate.tracking_url に置換する。
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        output_epub = output_dir / f"book_{affiliate.id}.epub"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            try:
                # 1. 全章をMarkdownとして結合
                combined_md = self._combine_chapters(chapters, book_plan, affiliate)

                # 2. CTAプレースホルダーを代理店固有URLに置換
                combined_md = combined_md.replace(
                    "[COURSE_CTA_PLACEHOLDER]",
                    f"[詳細はこちら]({affiliate.tracking_url})",
                )

                # 3. AI開示テキスト追加
                combined_md += AI_DISCLOSURE

                # 4. ファイル書き込み
                md_path = tmp / "book.md"
                md_path.write_text(combined_md, encoding="utf-8")

                # 5. EPUBメタデータYAML
                meta_path = tmp / "meta.yaml"
                meta_path.write_text(
                    yaml.dump(self._create_metadata(book_plan, affiliate), allow_unicode=True),
                    encoding="utf-8",
                )

                # 6. CSS
                css_path = tmp / "style.css"
                css_path.write_text(EPUB_CSS, encoding="utf-8")

                # 7. Pandoc実行
                self._run_pandoc(md_path, output_epub, meta_path, css_path)

                # 8. ファイルサイズ確認（最低100KB）
                size = output_epub.stat().st_size
                if size < 100_000:
                    logger.warning("EPUB サイズが小さすぎます: %d bytes (affiliate=%s)", size, affiliate.id)

                logger.info("EPUB生成完了: %s (%.1fKB)", output_epub, size / 1024)
                return output_epub

            except Exception:
                # tmpは自動削除されるが、部分的なepubが残る場合があるので削除
                output_epub.unlink(missing_ok=True)
                raise

    def _combine_chapters(self, chapters: list, book_plan, affiliate: Affiliate) -> str:
        """章リストを1つのMarkdownドキュメントに結合"""
        lines = [f"# {book_plan.topic}\n", f"**{book_plan.subtitle}**\n\n"]
        lines.append(f"著者: {affiliate.kdp_pen_name}\n\n---\n\n")

        for chapter in sorted(chapters, key=lambda c: c.number):
            lines.append(chapter.content)
            lines.append("\n\n---\n\n")

        return "\n".join(lines)

    def _create_metadata(self, book_plan, affiliate: Affiliate) -> dict:
        return {
            "title": book_plan.topic,
            "subtitle": book_plan.subtitle,
            "author": affiliate.kdp_pen_name,
            "language": "ja",
            "date": book_plan.date,
            "description": f"{book_plan.target_reader}向けのAI副業実践ガイド。",
            "rights": f"Copyright {book_plan.date[:4]} {affiliate.kdp_pen_name}",
        }

    def _run_pandoc(self, input_md: Path, output_epub: Path,
                    metadata_yaml: Path, css_path: Path) -> None:
        """Pandocを実行（returncode != 0 は RuntimeError）"""
        cmd = [
            "pandoc", str(input_md),
            "-o", str(output_epub),
            "--epub-metadata", str(metadata_yaml),
            "--css", str(css_path),
            "--toc",
            "--toc-depth=2",
            "--epub-chapter-level=1",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error("Pandoc失敗: stderr=%s", result.stderr)
            raise RuntimeError(f"Pandoc failed (code={result.returncode}): {result.stderr}")
