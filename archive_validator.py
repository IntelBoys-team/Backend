"""Service Layer — Archive Validator: потоковое чтение + сквозная валидация Magic Numbers."""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

# ТЗ п.2.3: разрешённые сигнатуры
MAGIC_PNG = b"\x89PNG"          # 89 50 4E 47
MAGIC_JPEG = b"\xff\xd8\xff"    # FF D8 FF
MAGIC_BMP = b"BM"               # 42 4D

SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".bmp"}
ARCHIVE_EXTS = {".zip", ".rar", ".7z"}

VALIDATION_ERROR_MSG = (
    "Ошибка валидации: архив содержит неподдерживаемые типы данных. "
    "Конвертация невозможна"
)


class ValidationError(Exception):
    pass


def detect_image_kind(header: bytes) -> str | None:
    if header.startswith(MAGIC_PNG):
        return "png"
    if header.startswith(MAGIC_JPEG):
        return "jpeg"
    if header.startswith(MAGIC_BMP):
        return "bmp"
    return None


def _is_supported_name(name: str) -> bool:
    return Path(name).suffix.lower() in SUPPORTED_EXTS


def iter_zip_members(archive: Path):
    """Потоковое чтение оглавления без полной распаковки — ТЗ п.2.3."""
    with zipfile.ZipFile(archive, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            yield info.filename


def read_zip_header(archive: Path, member: str, n: int = 4) -> bytes:
    with zipfile.ZipFile(archive, "r") as zf:
        with zf.open(member, "r") as f:
            return f.read(n)


def _validate_member_list(members: list[str], read_header) -> None:
    """Сквозная валидация: расширение + бинарная сигнатура. Любой чужой файл = стоп."""
    if not members:
        raise ValidationError(VALIDATION_ERROR_MSG + " (архив пуст)")
    bad: list[str] = []
    for name in members:
        if not _is_supported_name(name):
            bad.append(name)
            continue
        try:
            header = read_header(name)
        except Exception:
            bad.append(name + " (не читается)")
            continue
        if detect_image_kind(header) is None:
            bad.append(name + " (сигнатура не совпала)")
    if bad:
        preview = ", ".join(bad[:5])
        raise ValidationError(f"{VALIDATION_ERROR_MSG}: {preview}")


def validate_and_extract(archive: Path, cache_dir: Path) -> list[Path]:
    """Валидация + безопасная распаковка в кэш. При ошибке кэш стирается."""
    archive = Path(archive)
    cache_dir = Path(cache_dir)
    suffix = archive.suffix.lower()

    try:
        if suffix == ".zip":
            members = [m for m in iter_zip_members(archive)]
            _validate_member_list(members, lambda m: read_zip_header(archive, m))
            cache_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive, "r") as zf:
                zf.extractall(cache_dir)
        elif suffix == ".7z":
            try:
                import py7zr
            except ImportError as e:
                raise ValidationError("Для .7z установите зависимость py7zr") from e
            with py7zr.SevenZipFile(archive, "r") as zf:
                names = zf.getnames()
                members = [n for n in names if not n.endswith("/")]
                def _rh(n: str) -> bytes:
                    d = zf.read([n])
                    buf = d[n].read() if hasattr(d[n], "read") else bytes(d[n].getvalue())
                    return buf[:4]
                _validate_member_list(members, _rh)
                cache_dir.mkdir(parents=True, exist_ok=True)
                zf.extractall(cache_dir)
        elif suffix == ".rar":
            try:
                import rarfile
            except ImportError as e:
                raise ValidationError("Для .rar установите зависимость rarfile") from e
            with rarfile.RarFile(archive, "r") as rf:
                members = [i.filename for i in rf.infolist() if not i.is_dir()]
                def _rh2(n: str) -> bytes:
                    with rf.open(n) as f:
                        return f.read(4)
                _validate_member_list(members, _rh2)
                cache_dir.mkdir(parents=True, exist_ok=True)
                rf.extractall(cache_dir)
        else:
            raise ValidationError(f"Неподдерживаемый тип архива: {suffix}")
    except ValidationError:
        shutil.rmtree(cache_dir, ignore_errors=True)
        raise
    except zipfile.BadZipFile as e:
        shutil.rmtree(cache_dir, ignore_errors=True)
        raise ValidationError(f"{VALIDATION_ERROR_MSG} (архив повреждён)") from e

    # Собрать извлечённые изображения
    out = [p for p in cache_dir.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS]
    # Финальная проверка сигнатур на диске (защита от переименованных файлов)
    for p in list(out):
        try:
            if detect_image_kind(p.read_bytes()[:4]) is None:
                shutil.rmtree(cache_dir, ignore_errors=True)
                raise ValidationError(f"{VALIDATION_ERROR_MSG}: {p.name}")
        except OSError:
            pass
    if not out:
        shutil.rmtree(cache_dir, ignore_errors=True)
        raise ValidationError(VALIDATION_ERROR_MSG + " (изображения не найдены)")
    return sorted(out)


def validate_single_image(path: Path) -> Path:
    """Валидация одиночного файла (расширение + Magic Number) — ТЗ п.2.3.

    Вызывается из UI через сервисный слой, чтобы в Presentation Layer
    не было алгоритмов валидации — ТЗ п.1.4.
    """
    path = Path(path)
    if not path.is_file():
        raise ValidationError(f"Файл не найден: {path}")
    if path.suffix.lower() not in SUPPORTED_EXTS:
        raise ValidationError(VALIDATION_ERROR_MSG)
    try:
        header = path.read_bytes()[:4]
    except OSError as e:
        raise ValidationError(f"{VALIDATION_ERROR_MSG}: {path.name} не читается") from e
    if detect_image_kind(header) is None:
        raise ValidationError(f"{VALIDATION_ERROR_MSG}: {path.name}")
    return path


def collect_from_folder(folder: Path) -> list[Path]:
    """Сбор изображений из папки (рекурсивно). Неподдерживаемые пропускаются."""
    folder = Path(folder)
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS)


def is_archive(path: Path) -> bool:
    return Path(path).suffix.lower() in ARCHIVE_EXTS
