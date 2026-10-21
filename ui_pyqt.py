"""Presentation Layer / UI — PyQt6. Single-Page 800x600 Dark Mode.

Только сигналы в Service Layer (ThreadManager / ArchiveValidator / ImageCore),
никаких алгоритмов сжатия и валидации в UI — ТЗ п.1.4.
"""
from __future__ import annotations

import threading
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QFont, QIntValidator
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from archive_validator import (
    ValidationError,
    collect_from_folder,
    is_archive,
    validate_and_extract,
)
from config_manager import ConfigManager, get_cache_dir
from thread_manager import ThreadManager

# Палитра — ТЗ п.5.1
BG = "#1E1E1E"
PANEL = "#2D2D2D"
TEXT_MAIN = "#FFFFFF"
TEXT_SUB = "#E0E0E0"
ACCENT = "#007ACC"
ERROR = "#D32F2F"

# Текст подсказки — ТЗ п.5.2 (Верхняя зона)
HINT_TEXT = (
    "Перетащите сюда папку или архив с изображениями (.zip, .rar) "
    "или нажмите кнопку «Обзор»"
)

INTERP_ITEMS = ["Lanczos (Рекомендуется)", "Bicubic", "Bilinear"]


class DropFrame(QFrame):
    """Верхняя зона Drag-and-Drop с пунктирной рамкой — ТЗ п.5.2."""

    file_dropped = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        if event is not None and event.mimeData().hasUrls():
            event.acceptProposedAction()
        elif event is not None:
            event.ignore()

    def dropEvent(self, event: QDropEvent | None) -> None:
        if event is None:
            return
        urls = event.mimeData().urls()
        if urls:
            self.file_dropped.emit(urls[0].toLocalFile())
            event.acceptProposedAction()
        else:
            event.ignore()


class ImageConverterWindow(QWidget):
    # Сигналы из фоновых потоков в GUI-поток (потокобезопасно)
    sig_log = pyqtSignal(str)
    sig_progress = pyqtSignal(int, int)
    sig_status = pyqtSignal(str)
    sig_counter = pyqtSignal(str)
    sig_source = pyqtSignal(str)
    sig_start_enabled = pyqtSignal(bool)
    sig_busy = pyqtSignal(bool)
    sig_error = pyqtSignal(str)
    sig_drop_error = pyqtSignal()
    sig_beep = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PixelForge — Конвертер изображений")
        self.setFixedSize(800, 600)
        self.setAcceptDrops(True)
        self.setStyleSheet(
            f"background-color: {BG}; color: {TEXT_SUB}; font-family: 'Segoe UI';"
        )

        self.cfg = ConfigManager()
        self.tm = ThreadManager()
        self.source_files: list[Path] = []
        self.source_from_cache = False  # источник лежит в .cache_processing
        self.working = False
        self.cache_dir = get_cache_dir()

        self._build()
        self._connect_signals()
        self._apply_config_to_widgets()
        self.btn_start.setEnabled(False)
        self._log("PixelForge готов. Источник не выбран.")

    # ---------- построение ----------
    def _panel_style(self) -> str:
        return f"QFrame {{ background-color: {PANEL}; border-radius: 6px; }}"

    def _build(self):
        # Верхняя зона: импорт (Drag-and-Drop) — ТЗ п.5.2
        self.drop_frame = DropFrame(self)
        self.drop_frame.setGeometry(12, 12, 776, 156)
        self.drop_frame.setStyleSheet(
            f"QFrame {{ background-color: {PANEL}; border: 2px dashed {ACCENT}; border-radius: 6px; }}"
        )
        dl = QVBoxLayout(self.drop_frame)
        dl.setContentsMargins(14, 10, 14, 10)
        dl.addStretch(1)

        drow = QHBoxLayout()
        drow.setSpacing(16)
        # Иконка папки/архива по центру зоны — ТЗ п.5.2
        icon = QLabel("📁")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"font-size: 30px; color: {TEXT_MAIN}; border: none; background: transparent;")
        drow.addWidget(icon)

        self.source_label = QLabel(HINT_TEXT)
        self.source_label.setWordWrap(True)
        self.source_label.setStyleSheet(f"color: {TEXT_SUB}; border: none; background: transparent;")
        drow.addWidget(self.source_label, 1)

        # Кнопка «Обзор» справа от текста — ТЗ п.5.2
        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)
        self.btn_browse = QPushButton("Обзор")
        self.btn_browse.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_browse.setStyleSheet(
            f"QPushButton {{ background-color: {ACCENT}; color: white; border: none; "
            f"border-radius: 6px; padding: 6px; }} QPushButton:disabled {{ background-color: #555; }}"
        )
        self.btn_browse.clicked.connect(self.on_browse_source)
        btn_col.addWidget(self.btn_browse)
        self.btn_outdir = QPushButton("Папка вывода")
        self.btn_outdir.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_outdir.setStyleSheet(
            "QPushButton { background-color: #3a3a3a; color: white; border: none; "
            "border-radius: 6px; padding: 6px; }"
        )
        self.btn_outdir.clicked.connect(self.on_browse_output)
        btn_col.addWidget(self.btn_outdir)
        drow.addLayout(btn_col)

        dl.addLayout(drow)
        dl.addStretch(1)
        self.drop_frame.file_dropped.connect(self.load_source_str)

        # Средняя зона: панель настроек (слева) — ТЗ п.5.2
        self.settings = QFrame(self)
        self.settings.setGeometry(12, 176, 260, 274)
        self.settings.setStyleSheet(self._panel_style())
        sl = QVBoxLayout(self.settings)
        sl.setContentsMargins(12, 10, 12, 10)
        sl.setSpacing(3)
        title = QLabel("НАСТРОЙКИ")
        # Заголовки блоков: 14–16 пт — ТЗ п.5.1
        title.setStyleSheet(f"color: {TEXT_MAIN}; font-size: 14pt; font-weight: bold;")
        sl.addWidget(title)

        sl.addWidget(self._sub("Формат вывода"))
        self.fmt_box = QComboBox()
        self.fmt_box.addItems(["WebP", "JPEG"])
        self.fmt_box.currentTextChanged.connect(lambda _: self._persist())
        sl.addWidget(self.fmt_box)

        sl.addWidget(self._sub("Метод интерполяции"))
        self.interp_box = QComboBox()
        self.interp_box.addItems(INTERP_ITEMS)
        self.interp_box.currentTextChanged.connect(lambda _: self._persist())
        sl.addWidget(self.interp_box)

        qrow = QHBoxLayout()
        qrow.addWidget(self._sub("Качество"))
        qrow.addStretch(1)
        self.q_label = QLabel("80%")
        self.q_label.setStyleSheet(f"color: {TEXT_MAIN}; font-weight: bold;")
        qrow.addWidget(self.q_label)
        sl.addLayout(qrow)
        self.quality = QSlider(Qt.Orientation.Horizontal)
        self.quality.setRange(10, 100)
        self.quality.setValue(80)
        self.quality.valueChanged.connect(self._on_quality)
        sl.addWidget(self.quality)

        self.resize_check = QCheckBox("Изменить разрешение")
        self.resize_check.toggled.connect(self._on_resize_toggle)
        sl.addWidget(self.resize_check)
        wh = QHBoxLayout()
        self.w_entry = QLineEdit()
        self.w_entry.setPlaceholderText("Ширина")
        self.w_entry.setValidator(QIntValidator(1, 16384))
        self.w_entry.setMaximumWidth(110)
        self.w_entry.textChanged.connect(lambda _: self._persist())
        wh.addWidget(self.w_entry)
        self.h_entry = QLineEdit()
        self.h_entry.setPlaceholderText("Высота")
        self.h_entry.setValidator(QIntValidator(1, 16384))
        self.h_entry.setMaximumWidth(110)
        self.h_entry.textChanged.connect(lambda _: self._persist())
        wh.addWidget(self.h_entry)
        wh.addStretch(1)
        sl.addLayout(wh)
        self.keep_check = QCheckBox("Сохранять пропорции")
        self.keep_check.toggled.connect(lambda _: self._persist())
        sl.addWidget(self.keep_check)
        sl.addStretch(1)

        # Правая центральная часть: окно логов (Terminal Log) — ТЗ п.5.2
        log_frame = QFrame(self)
        log_frame.setGeometry(282, 176, 506, 274)
        log_frame.setStyleSheet(self._panel_style())
        ll = QVBoxLayout(log_frame)
        ll.setContentsMargins(12, 10, 12, 10)
        log_title = QLabel("ЖУРНАЛ ВЫВОДА")
        log_title.setStyleSheet(f"color: {TEXT_SUB}; font-size: 14pt; font-weight: bold;")
        ll.addWidget(log_title)
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setStyleSheet(
            f"background-color: #181818; color: {TEXT_SUB}; border: none; border-radius: 4px;"
        )
        ll.addWidget(self.log_box)

        # Нижняя зона: управление и мониторинг — ТЗ п.5.2
        self.progress = QProgressBar(self)
        self.progress.setGeometry(12, 466, 620, 16)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet(
            f"QProgressBar {{ background-color: #2D2D2D; border: none; border-radius: 4px; }}"
            f"QProgressBar::chunk {{ background-color: {ACCENT}; border-radius: 4px; }}"
        )
        # Счётчик под прогресс-баром — ТЗ п.5.2
        self.counter_label = QLabel("Обработано: 0 из 0 файлов (0% объектов)", self)
        self.counter_label.setGeometry(12, 488, 620, 20)

        self.btn_cancel = QPushButton("Прервать", self)
        self.btn_cancel.setGeometry(640, 458, 70, 30)
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.setStyleSheet(
            f"QPushButton {{ background-color: {ERROR}; color: white; border: none; border-radius: 6px; }}"
        )
        self.btn_cancel.clicked.connect(self.on_abort)
        self.btn_start = QPushButton("Старт", self)
        self.btn_start.setGeometry(716, 458, 72, 30)
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start.setStyleSheet(
            f"QPushButton {{ background-color: {ACCENT}; color: white; border: none; border-radius: 6px; }}"
            "QPushButton:disabled { background-color: #555; color: #aaa; }"
        )
        self.btn_start.clicked.connect(self.on_start)

        self.status_label = QLabel("Ожидание источника…", self)
        self.status_label.setGeometry(12, 512, 776, 20)
        self.out_label = QLabel("", self)
        self.out_label.setGeometry(12, 536, 776, 20)
        self._refresh_out_label()

    def _sub(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setStyleSheet(f"color: {TEXT_SUB};")
        return lab

    def _connect_signals(self):
        self.sig_log.connect(self._on_log)
        self.sig_progress.connect(self._on_progress)
        self.sig_status.connect(self.status_label.setText)
        self.sig_counter.connect(self.counter_label.setText)
        self.sig_source.connect(self.source_label.setText)
        self.sig_start_enabled.connect(self.btn_start.setEnabled)
        self.sig_busy.connect(self._on_busy)
        self.sig_error.connect(self._on_error)
        self.sig_drop_error.connect(self._flash_drop_error)
        self.sig_beep.connect(QApplication.beep)  # ТЗ п.5.3 — системный сигнал

    # ---------- helpers ----------
    def _interp_value(self) -> str:
        v = self.interp_box.currentText()
        if v.startswith("Bicubic"):
            return "Bicubic"
        if v.startswith("Bilinear"):
            return "Bilinear"
        return "Lanczos"

    def _apply_config_to_widgets(self):
        d = self.cfg.data
        self.fmt_box.setCurrentText(d.get("output_format", "WebP"))
        iv = d.get("interpolation", "Lanczos")
        self.interp_box.setCurrentText(
            {"Lanczos": INTERP_ITEMS[0], "Bicubic": "Bicubic", "Bilinear": "Bilinear"}.get(iv, INTERP_ITEMS[0])
        )
        try:
            self.quality.setValue(int(d.get("quality", 80)))
        except (TypeError, ValueError):
            pass
        self.q_label.setText(f"{self.quality.value()}%")
        self.resize_check.setChecked(bool(d.get("resize_enabled", False)))
        self.keep_check.setChecked(bool(d.get("keep_aspect", True)))
        self.w_entry.setText(str(d.get("width", 1920)))
        self.h_entry.setText(str(d.get("height", 1080)))
        self._on_resize_toggle()
        self._refresh_out_label()

    def _persist(self):
        try:
            w = int(self.w_entry.text() or 1920)
        except ValueError:
            w = 1920
        try:
            h = int(self.h_entry.text() or 1080)
        except ValueError:
            h = 1080
        self.cfg.update({
            "output_format": self.fmt_box.currentText(),
            "quality": int(self.quality.value()),
            "interpolation": self._interp_value(),
            "resize_enabled": bool(self.resize_check.isChecked()),
            "width": w, "height": h,
            "keep_aspect": bool(self.keep_check.isChecked()),
            "output_dir": self.cfg.get("output_dir", ""),
        })
        self._refresh_out_label()

    def _refresh_out_label(self):
        od = self.cfg.get("output_dir", "") or "— не выбрана (будет создана ./Converted) —"
        self.out_label.setText(f"Выходная папка: {od}")

    def _on_quality(self, v: int):
        self.q_label.setText(f"{v}%")
        self._persist()

    def _on_resize_toggle(self):
        enabled = self.resize_check.isChecked()
        self.w_entry.setEnabled(enabled)
        self.h_entry.setEnabled(enabled)

    # ---------- слоты сигналов ----------
    def _log(self, msg: str):
        self.sig_log.emit(msg)

    def _on_log(self, msg: str):
        self.log_box.append(msg)

    def _on_progress(self, done: int, total: int):
        frac = done / max(1, total)
        pct = int(frac * 100)
        self.progress.setRange(0, 100)
        self.progress.setValue(pct)
        # ТЗ п.5.2: «Обработано: X из Y файлов (Z% объектов)»
        self.sig_counter.emit(f"Обработано: {done} из {total} файлов ({pct}% объектов)")

    def _on_busy(self, busy: bool):
        # Этап валидации/конвертации: настройки заморожены — ТЗ п.5.3
        for w in (self.fmt_box, self.interp_box, self.quality, self.w_entry,
                  self.h_entry, self.resize_check, self.keep_check,
                  self.btn_browse, self.btn_outdir):
            w.setEnabled(not busy)
        if busy:
            self.progress.setRange(0, 0)  # бесконечная анимация «загрузка» — ТЗ п.5.3
        else:
            self.progress.setRange(0, 100)
            self._on_resize_toggle()  # поля W/H зависят от чекбокса

    def _on_error(self, msg: str):
        QMessageBox.critical(self, "Ошибка валидации", msg)

    def _flash_drop_error(self):
        self.drop_frame.setStyleSheet(
            f"QFrame {{ background-color: {PANEL}; border: 2px dashed {ERROR}; border-radius: 6px; }}"
        )
        QTimer.singleShot(600, lambda: self.drop_frame.setStyleSheet(
            f"QFrame {{ background-color: {PANEL}; border: 2px dashed {ACCENT}; border-radius: 6px; }}"
        ))

    # ---------- Drag-and-Drop на всё окно ----------
    def dragEnterEvent(self, event: QDragEnterEvent | None) -> None:
        if event is not None and event.mimeData().hasUrls():
            event.acceptProposedAction()
        elif event is not None:
            event.ignore()

    def dropEvent(self, event: QDropEvent | None) -> None:
        if event is None:
            return
        urls = event.mimeData().urls()
        if urls:
            self.load_source_str(urls[0].toLocalFile())
            event.acceptProposedAction()
        else:
            event.ignore()

    # ---------- источник ----------
    def load_source_str(self, s: str):
        self.load_source(Path(s))

    def on_browse_source(self):
        f, _ = QFileDialog.getOpenFileName(self, "Выберите архив", "", "Архивы (*.zip *.rar *.7z);;Все (*)")
        if f:
            self.load_source(Path(f))
            return
        d = QFileDialog.getExistingDirectory(self, "Выберите папку с изображениями")
        if d:
            self.load_source(Path(d))

    def on_browse_output(self):
        d = QFileDialog.getExistingDirectory(self, "Папка для результатов")
        if d:
            self.cfg.update({"output_dir": d})
            self._refresh_out_label()
            self._log(f"Папка вывода: {d}")

    def load_source(self, path: Path):
        if self.working:
            return
        self.sig_busy.emit(True)
        self.sig_start_enabled.emit(False)
        self.sig_status.emit("Запуск сквозной валидации…")
        threading.Thread(target=self._validate_bg, args=(path,), daemon=True).start()

    def _validate_bg(self, path: Path):
        try:
            if not path.exists():
                raise ValidationError(f"Путь не найден: {path}")
            if is_archive(path):
                self._log(f"Архивом загружен файл: {path.name}")
                self._log("Запуск сквозной валидации... проверка сигнатур")
                # Чистый кэш перед распаковкой — ТЗ п.1.5, п.2.3
                ConfigManager.clear_cache(self.cache_dir)
                files = validate_and_extract(path, self.cache_dir)
                self._log(f"Валидация успешна. Найдено {len(files)} изображений")
                self.source_from_cache = True
            elif path.is_dir():
                self._log(f"Папка загружена: {path.name}")
                files = collect_from_folder(path)
                if not files:
                    raise ValidationError("В папке нет изображений PNG/JPEG/BMP")
                self._log(f"Валидация успешна. Найдено {len(files)} изображений в папке")
                self.source_from_cache = False
            elif path.is_file():
                # Одиночный файл — валидация в сервисном слое (ТЗ п.1.4)
                from archive_validator import validate_single_image
                self._log(f"Загружен файл: {path.name}")
                files = [validate_single_image(path)]
                self._log("Валидация успешна. Найдено 1 изображение")
                self.source_from_cache = False
            else:
                raise ValidationError(f"Неподдерживаемый источник: {path}")
            self.source_files = files
            self.sig_source.emit(f"{path.name} — {len(files)} файл(ов) готово к конвертации")
            self.sig_status.emit("Валидация успешна. Нажмите «Старт».")
            self.sig_start_enabled.emit(True)
        except ValidationError as e:
            self._log(f"Ошибка: {e}. Процесс остановлен.")
            self.sig_drop_error.emit()
            self.sig_error.emit(str(e))
            self.sig_status.emit("Ошибка валидации.")
            self.source_files = []
            self.source_from_cache = False
            self.sig_start_enabled.emit(False)
        except Exception as e:  # noqa: BLE001
            self._log(f"Ошибка: {e}. Процесс остановлен.")
            # Любая нештатная ситуация: кэш стирается, источник сбрасывается (ТЗ п.2.3)
            ConfigManager.clear_cache(self.cache_dir)
            self.source_files = []
            self.source_from_cache = False
            self.sig_source.emit(HINT_TEXT)
            self.sig_start_enabled.emit(False)
        finally:
            self.sig_busy.emit(False)

    # ---------- старт / стоп ----------
    def _output_dir(self) -> Path:
        od = (self.cfg.get("output_dir", "") or "").strip()
        if od:
            return Path(od)
        if self.source_files:
            try:
                return self.source_files[0].parent / "Converted"
            except OSError:
                pass
        return Path.cwd() / "Converted"

    def on_start(self):
        if self.working or not self.source_files:
            return
        self._persist()
        self.working = True
        self.sig_busy.emit(True)
        self.sig_start_enabled.emit(False)
        self.sig_progress.emit(0, len(self.source_files))
        dst = self._output_dir()
        try:
            w = int(self.w_entry.text()) if self.resize_check.isChecked() else None
        except ValueError:
            w = None
        try:
            h = int(self.h_entry.text()) if self.resize_check.isChecked() else None
        except ValueError:
            h = None
        kwargs = {
            "output_format": self.fmt_box.currentText(),
            "quality": int(self.quality.value()),
            "resize_enabled": bool(self.resize_check.isChecked()),
            "width": w, "height": h,
            "keep_aspect": bool(self.keep_check.isChecked()),
            "interpolation": self._interp_value(),
        }
        self._log(f"Старт: формат={kwargs['output_format']} качество={kwargs['quality']}% "
                  f"интерполяция={kwargs['interpolation']} потоков={self.tm.max_workers}")
        threading.Thread(target=self._convert_bg,
                         args=(list(self.source_files), dst, kwargs),
                         daemon=True).start()

    def _convert_bg(self, files: list[Path], dst: Path, kwargs: dict):
        try:
            self.tm.start(
                files, dst, kwargs,
                on_progress=lambda d, t: self.sig_progress.emit(d, t),
                on_ok=lambda s, o: self._log(f"OK  {s.name} -> {o.name}"),
                on_error=lambda s, e: self._log(f"ОШИБКА {s.name}: {e}"),
            )
            if self.tm.aborted:
                self._log("Прервано пользователем. Очередь очищена.")
                self.sig_status.emit("Прервано.")
            else:
                self._log(f"Готово. Результаты: {dst}")
                self.sig_status.emit("Конвертация завершена.")
                # ТЗ п.5.3: по завершении — негромкий системный звуковой сигнал
                self.sig_beep.emit()
        finally:
            # ТЗ п.1.5: файлы кэша удаляются безвозвратно после сбора результатов
            ConfigManager.clear_cache(self.cache_dir)
            if self.source_from_cache:
                # Файлы из кэша уничтожены — нужна новая валидация для следующего цикла
                self.source_files = []
                self.source_from_cache = False
                self.sig_source.emit(HINT_TEXT)
                self._log("Кэш .cache_processing очищен. Для следующего цикла перетащите архив заново.")
            self.working = False
            self.sig_busy.emit(False)
            if self.source_files:
                self.sig_start_enabled.emit(True)

    def on_abort(self):
        if not self.working:
            return
        self.tm.abort()
        self._log("Abort: остановка пула, очистка очереди и кэша…")


def run() -> int:
    app = QApplication([])
    # Основной шрифт интерфейса: Segoe UI 10 пт — ТЗ п.5.1
    app.setFont(QFont("Segoe UI", 10))
    win = ImageConverterWindow()
    win.show()
    return app.exec()
