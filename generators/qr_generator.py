"""QRコードジェネレーター - 代理店固有トラッキングURLのQRコードを生成"""
import logging
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

QR_SIZE = 300
LABEL_HEIGHT = 40
TOTAL_HEIGHT = QR_SIZE + LABEL_HEIGHT


class QRGenerator:
    def generate(self, url: str, affiliate_id: str, output_path: Path) -> Path:
        """
        300×340px QRコードを生成（下部にURL短縮テキスト付き）。
        書籍内CTAページに埋め込む用。
        """
        output_path.parent.mkdir(parents=True, exist_ok=True)

        qr = qrcode.QRCode(
            version=None,  # auto-fit
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(url)
        qr.make(fit=True)

        qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        qr_img = qr_img.resize((QR_SIZE, QR_SIZE), Image.LANCZOS)

        # ラベル用キャンバスを下に追加
        canvas = Image.new("RGB", (QR_SIZE, TOTAL_HEIGHT), "white")
        canvas.paste(qr_img, (0, 0))

        draw = ImageDraw.Draw(canvas)
        # システムフォントを使用（環境依存を避けるためデフォルトフォント）
        try:
            font = ImageFont.truetype("arial.ttf", 14)
        except (IOError, OSError):
            font = ImageFont.load_default()

        # URLを短く表示（トラッキングURLの末尾部分）
        display_url = url.replace("https://", "").replace("http://", "")
        if len(display_url) > 35:
            display_url = display_url[:32] + "..."

        # 中央揃えでラベルを描画
        bbox = draw.textbbox((0, 0), display_url, font=font)
        text_width = bbox[2] - bbox[0]
        x = (QR_SIZE - text_width) // 2
        draw.text((x, QR_SIZE + 10), display_url, fill="black", font=font)

        output_path_png = output_path.with_suffix(".png")
        canvas.save(output_path_png, "PNG", optimize=True)
        logger.info("QRコード生成完了: %s", output_path_png)
        return output_path_png

    def generate_with_label(self, url: str, label: str, output_path: Path) -> Path:
        """カスタムラベル付きQRコード（書籍内の任意の箇所用）"""
        output_path.parent.mkdir(parents=True, exist_ok=True)

        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=3)
        qr.add_data(url)
        qr.make(fit=True)

        qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        qr_size = 250
        qr_img = qr_img.resize((qr_size, qr_size), Image.LANCZOS)

        label_h = 50
        canvas = Image.new("RGB", (qr_size, qr_size + label_h), "white")
        canvas.paste(qr_img, (0, 0))

        draw = ImageDraw.Draw(canvas)
        try:
            font = ImageFont.truetype("arial.ttf", 13)
        except (IOError, OSError):
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), label, font=font)
        text_width = bbox[2] - bbox[0]
        x = max(0, (qr_size - text_width) // 2)
        draw.text((x, qr_size + 12), label, fill="#1a1a2e", font=font)

        output_path_png = output_path.with_suffix(".png")
        canvas.save(output_path_png, "PNG")
        return output_path_png
