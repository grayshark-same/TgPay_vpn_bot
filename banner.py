import io
import os
from PIL import Image, ImageDraw, ImageFont

BANNERS_DIR = os.path.dirname(os.path.abspath(__file__))

# Координаты прямоугольника "ВАШ КОД" для каждого баннера (x0, y0, x1, y1)
# и цвет заливки (подобран по фону блока)
BANNER_CONFIG = {
    1: {"rect": (460, 1020, 1090, 1140), "fill": (5, 10, 45), "font_size": 82},
    2: {"rect": (160, 1040, 1090, 1170), "fill": (4, 8, 30), "font_size": 90},
    3: {"rect": (310, 820, 940, 920), "fill": (6, 12, 52), "font_size": 90},
}


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/app/fonts/bold.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def generate_banner(banner_num: int, promo_code: str) -> io.BytesIO:
    path = os.path.join(BANNERS_DIR, f"banner{banner_num}.jpg")
    img = Image.open(path).convert("RGB")
    draw = ImageDraw.Draw(img)

    cfg = BANNER_CONFIG[banner_num]
    x0, y0, x1, y1 = cfg["rect"]
    fill = cfg["fill"]
    font_size = cfg["font_size"]

    # Перекрыть "ВАШ КОД"
    draw.rectangle([x0, y0, x1, y1], fill=fill)

    # Нарисовать промокод по центру блока
    font = _load_font(font_size)
    cx = (x0 + x1) // 2
    cy = (y0 + y1) // 2
    draw.text((cx, cy), promo_code, font=font, fill=(255, 255, 255), anchor="mm")

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    buf.seek(0)
    return buf
