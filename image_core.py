"""Service Layer — Image Processing Core (Pillow). Ресэмплинг + сжатие."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

# ТЗ п.2.1: три метода интерполяции, Lanczos — по умолчанию
_RESAMPLING = getattr(Image, "Resampling", Image)  # Pillow>=9.1
INTERPOLATION_MAP = {
    "Bilinear": _RESAMPLING.BILINEAR,
    "Bicubic": _RESAMPLING.BICUBIC,
    "Lanczos": _RESAMPLING.LANCZOS,
}

OUTPUT_EXT = {"WebP": ".webp", "JPEG": ".jpg"}


def compute_target_size(
    orig_w: int,
    orig_h: int,
    req_w: int | None,
    req_h: int | None,
    keep_aspect: bool,
) -> tuple[int, int]:
    """Пересчёт размера с сохранением пропорций (aspect ratio) — ТЗ п.3."""
    if not req_w and not req_h:
        return orig_w, orig_h
    if keep_aspect:
        if req_w and req_h:
            scale = min(req_w / orig_w, req_h / orig_h)
            return max(1, int(orig_w * scale)), max(1, int(orig_h * scale))
        if req_w:
            scale = req_w / orig_w
            return req_w, max(1, int(orig_h * scale))
        scale = req_h / orig_h  # type: ignore[operator]
        return max(1, int(orig_w * scale)), req_h  # type: ignore[return-value]
    return int(req_w or orig_w), int(req_h or orig_h)


def convert_image(
    src_path: str | Path,
    dst_dir: str | Path,
    output_format: str = "WebP",
    quality: int = 80,
    resize_enabled: bool = False,
    width: int | None = None,
    height: int | None = None,
    keep_aspect: bool = True,
    interpolation: str = "Lanczos",
) -> Path:
    """Конвертация одного файла. Бросает исключение при битом файле (caller логирует)."""
    src = Path(src_path)
    dst_dir = Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)

    fmt = "WebP" if output_format not in ("WebP", "JPEG") else output_format
    resample = INTERPOLATION_MAP.get(interpolation, Image.LANCZOS)
    quality = max(10, min(100, int(quality)))

    with Image.open(src) as im:
        im.load()  # раннее выявление битых файлов
        orig_w, orig_h = im.size

        if resize_enabled:
            tw, th = compute_target_size(orig_w, orig_h, width, height, keep_aspect)
            if (tw, th) != (orig_w, orig_h):
                im = im.resize((tw, th), resample=resample)

        ext = OUTPUT_EXT[fmt]
        dst = dst_dir / (src.stem + ext)
        # Избегаем коллизий имён при пакетной обработке
        i = 1
        while dst.exists():
            dst = dst_dir / f"{src.stem}_{i}{ext}"
            i += 1

        save_kwargs: dict = {"quality": quality}
        if fmt == "WebP":
            # RGBA сохраняется корректно; метод 6 = лучшее сжатие
            save_kwargs.update({"method": 6})
            if im.mode in ("P",):
                im = im.convert("RGBA")
            im.save(dst, "WEBP", **save_kwargs)
        else:  # JPEG — без альфа-канала
            if im.mode in ("RGBA", "LA", "PA", "P"):
                bg = Image.new("RGB", im.size, (255, 255, 255))
                alpha = im.convert("RGBA").split()[-1] if "A" in im.getbands() else None
                if alpha is not None:
                    bg.paste(im.convert("RGB"), mask=alpha)
                    im = bg
                else:
                    im = im.convert("RGB")
            elif im.mode != "RGB":
                im = im.convert("RGB")
            im.save(dst, "JPEG", optimize=True, **save_kwargs)
        return dst
