"""KDPパッケージビルダー - 代理店ごとにKDP提出用ZIPパッケージを生成"""
import hashlib
import json
import logging
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from generators.epub_builder import Affiliate, EpubBuilder
from generators.qr_generator import QRGenerator

logger = logging.getLogger(__name__)

# KDPアップロードガイド HTML（代理店向け手順書）
_UPLOAD_GUIDE_HTML = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>KDPアップロードガイド</title>
<style>
  body{font-family:"Hiragino Sans","Yu Gothic",sans-serif;max-width:800px;margin:40px auto;padding:0 20px;line-height:1.8;color:#333}
  h1{color:#1a1a2e;border-bottom:3px solid #1a73e8;padding-bottom:10px}
  h2{color:#1a73e8;margin-top:2em}
  ol li{margin:16px 0;padding-left:8px}
  .important{background:#fff3cd;border-left:4px solid #ff9800;padding:12px 16px;border-radius:4px;margin:16px 0}
  .tip{background:#e8f5e9;border-left:4px solid #4caf50;padding:12px 16px;border-radius:4px;margin:16px 0}
  code{background:#f4f4f4;padding:2px 6px;border-radius:3px;font-family:monospace}
  a{color:#1a73e8}
</style>
</head>
<body>
<h1>📚 KDPアップロードガイド</h1>
<p>このガイドに従って書籍をAmazon KDPに出版してください。<strong>所要時間：約10〜15分</strong></p>

<h2>ステップ1: KDPにログイン</h2>
<ol>
  <li><a href="https://kdp.amazon.co.jp" target="_blank">kdp.amazon.co.jp</a> にアクセス</li>
  <li>Amazonアカウントでログイン（KDPアカウントがない場合は無料登録）</li>
</ol>

<h2>ステップ2: 新しい本を作成</h2>
<ol>
  <li>「本の棚に追加」ボタンをクリック</li>
  <li>「Kindleの本」を選択</li>
  <li>「新しいKindleの本を作成」をクリック</li>
</ol>

<h2>ステップ3: 書籍の詳細を入力</h2>
<ol>
  <li>同梱の <code>metadata.json</code> を開く</li>
  <li>「タイトル」「サブタイトル」「著者名」「説明」をコピー＆ペースト</li>
  <li>「キーワード」を7つ入力（metadata.jsonの keywords を参照）</li>
  <li>カテゴリを2つ選択（metadata.jsonの category_1, category_2 を参照）</li>
</ol>

<div class="important">
<strong>⚠️ 重要：AIコンテンツの開示（必須）</strong><br>
「AIで生成したコンテンツが含まれますか？」という質問が表示されます。<br>
<strong>必ず「はい」を選択してください。</strong><br>
未選択はAmazon KDPポリシー違反となり、アカウント停止のリスクがあります。
</div>

<h2>ステップ4: コンテンツをアップロード</h2>
<ol>
  <li>「デジタルライツマネジメント(DRM)」は「有効にする」推奨</li>
  <li>「Kindleの本コンテンツ」で <code>book.epub</code> をアップロード</li>
  <li>プレビューでレイアウトを確認（日本語が正しく表示されることを確認）</li>
</ol>

<h2>ステップ5: 表紙をアップロード</h2>
<ol>
  <li>「既存の表紙画像をアップロード」を選択</li>
  <li><code>cover.jpg</code> をアップロード（2560×1600px推奨）</li>
</ol>

<h2>ステップ6: 価格設定</h2>
<div class="tip">
<strong>💡 推奨価格設定</strong><br>
<strong>無料配布（最大集客）:</strong> KDPセレクトに登録 → 無料キャンペーン（5日間/90日）を設定<br>
<strong>有料販売:</strong> 99円〜250円が一般的。ロイヤリティは35%（99-250円）または70%（251円〜）
</div>

<h2>ステップ7: 出版</h2>
<ol>
  <li>全項目を確認して「Kindleの本を出版する」をクリック</li>
  <li>審査は通常48〜72時間以内に完了</li>
  <li>出版承認後、書籍URLをSNS・ブログ等でシェアしてください</li>
</ol>

<h2>📊 あなたのトラッキング情報</h2>
<p>書籍内のリンクとQRコードには、あなた固有のトラッキングIDが埋め込まれています。
読者が書籍経由で講座に申し込んだ場合、自動的にあなたの実績として記録されます。</p>
<p>成果は <strong>代理店ポータル</strong> でリアルタイムに確認できます。</p>

<h2>❓ サポート</h2>
<p>ご不明な点は代理店担当者にお問い合わせください。</p>
</body>
</html>"""


@dataclass
class KdpMetadata:
    title: str
    subtitle: str
    author_pen_name: str
    description: str           # 400文字以内
    keywords: list[str]        # 最大7個
    category_1: str
    category_2: str
    language: str = "Japanese"
    is_public_domain: bool = False
    ai_content_disclosed: bool = True  # KDP必須：AIコンテンツ開示


@dataclass
class KdpPackage:
    affiliate_id: str
    zip_path: Path
    epub_path: Path
    metadata: KdpMetadata
    created_at: datetime
    sha256: str               # ZIPのSHA256（Immutable Artifact原則）


class KdpPackageBuilder:
    def __init__(self) -> None:
        self.epub_builder = EpubBuilder()
        self.qr_gen = QRGenerator()

    def build_variant(self, master_epub: Path, cover_image: Path | None,
                      book_plan, affiliate: Affiliate,
                      output_dir: Path) -> KdpPackage:
        """
        代理店1人分のKDP ZIPパッケージを生成。
        master_epubのCTAプレースホルダーは既にEpubBuilder.buildで置換済み。
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        pkg_name = f"{book_plan.date}_{affiliate.id}"
        pkg_dir = output_dir / pkg_name
        pkg_dir.mkdir(exist_ok=True)

        # QRコード生成
        qr_path = pkg_dir / f"qr_{affiliate.id}.png"
        self.qr_gen.generate(affiliate.tracking_url, affiliate.id, qr_path)

        # EPUBコピー（master_epubは代理店ごとにEpubBuilderが生成済み）
        epub_dst = pkg_dir / "book.epub"
        epub_dst.write_bytes(master_epub.read_bytes())

        # 表紙コピー
        if cover_image and cover_image.exists():
            cover_dst = pkg_dir / "cover.jpg"
            cover_dst.write_bytes(cover_image.read_bytes())

        # KDPメタデータ生成
        metadata = self._build_metadata(book_plan, affiliate)
        meta_path = pkg_dir / "metadata.json"
        meta_path.write_text(
            json.dumps(asdict(metadata), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # アップロードガイドHTML
        guide_path = pkg_dir / "upload_guide.html"
        guide_path.write_text(_UPLOAD_GUIDE_HTML, encoding="utf-8")

        # トラッキング情報JSON
        tracking_path = pkg_dir / "tracking_info.json"
        tracking_path.write_text(
            json.dumps({
                "affiliate_id": affiliate.id,
                "tracking_url": affiliate.tracking_url,
                "qr_code": str(qr_path.name),
                "note": "このURLとQRコードはあなた固有です。書籍内に埋め込まれています。",
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # ZIP圧縮
        zip_path = output_dir / f"{pkg_name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in pkg_dir.iterdir():
                zf.write(f, arcname=f"{pkg_name}/{f.name}")

        sha = self._sha256(zip_path)
        logger.info("KDPパッケージ生成完了: %s (sha256=%s...)", zip_path.name, sha[:8])

        return KdpPackage(
            affiliate_id=affiliate.id,
            zip_path=zip_path,
            epub_path=epub_dst,
            metadata=metadata,
            created_at=datetime.now(timezone.utc),
            sha256=sha,
        )

    def build_all_variants(self, cover_image: Path | None,
                           book_plan, chapters: list,
                           affiliates: list[Affiliate],
                           output_dir: Path) -> list[KdpPackage]:
        """最大5並列でバリアント生成"""
        packages: list[KdpPackage] = []

        def build_one(affiliate: Affiliate) -> KdpPackage:
            # 代理店ごとにEPUBを別途生成（CTAURLが異なるため）
            epub_path = self.epub_builder.build(chapters, book_plan, affiliate, output_dir / "epub")
            return self.build_variant(epub_path, cover_image, book_plan, affiliate, output_dir)

        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(build_one, aff): aff for aff in affiliates}
            for future in futures:
                aff = futures[future]
                try:
                    pkg = future.result()
                    packages.append(pkg)
                except Exception as e:
                    # 1代理店の失敗は他の代理店に影響しない
                    logger.error("代理店 %s のパッケージ生成失敗: %s", aff.id, e)

        return packages

    def _build_metadata(self, book_plan, affiliate: Affiliate) -> KdpMetadata:
        # 説明文は400文字以内（KDP制限）
        description = (
            f"本書は{book_plan.target_reader}向けのAI副業実践ガイドです。"
            f"{book_plan.subtitle}。"
            f"海外の最新AI情報をもとに、今すぐ実践できる具体的な方法を解説します。"
        )[:400]

        return KdpMetadata(
            title=book_plan.topic,
            subtitle=book_plan.subtitle,
            author_pen_name=affiliate.kdp_pen_name,
            description=description,
            keywords=book_plan.keywords[:7],
            category_1=book_plan.category_1,
            category_2=book_plan.category_2,
        )

    def _sha256(self, path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
