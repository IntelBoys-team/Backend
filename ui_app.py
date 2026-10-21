"""Presentation Layer / UI — Single-Page 800x600 Dark Mode. Только сигналы в Service Layer."""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from pathlib import Path
import os

try:
    import customtkinter as ctk
    HAS_CTK = True
except ImportError:
    HAS_CTK = False

from config_manager import ConfigManager, get_cache_dir
from archive_validator import (
    collect_from_folder, is_archive, validate_and_extract, ValidationError,
)
from thread_manager import ThreadManager

# Палитра — ТЗ п.5.1
BG = "#1E1E1E"
PANEL = "#2D2D2D"
TEXT_MAIN = "#FFFFFF"
TEXT_SUB = "#E0E0E0"
ACCENT = "#007ACC"
ERROR = "#D32F2F"
OK_GREEN = "#2E7D32"

# Текст подсказки — ТЗ п.5.2 (Верхняя зона)
HINT_TEXT = (
    "Перетащите сюда папку или архив с изображениями (.zip, .rar) "
    "или нажмите кнопку «Обзор»"
)

# Заголовки блоков: 14–16 пт, интерфейс: 10–12 пт — ТЗ п.5.1
FONT_UI = ("Segoe UI", 11)
FONT_TITLE = ("Segoe UI", 14, "bold")


class ImageConverterApp:
    def __init__(self):
        if not HAS_CTK:
            raise RuntimeError("Установите customtkinter: pip install -r requirements.txt")
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # TkinterDnD для настоящей Drag-and-Drop зоны (опционально).
        # CTk + DnD: корень-наследник обоих классов, иначе CTk-виджеты в обычном Tk ломают темы.
        self._dnd_files = None
        try:
            from tkinterdnd2 import TkinterDnD, DND_FILES  # type: ignore

            class _DnDCTk(ctk.CTk, TkinterDnD.DnDWrapper):  # type: ignore
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, **kwargs)
                    self.TkdndVersion = TkinterDnD._require(self)

            self.root = _DnDCTk()
            self._dnd_files = DND_FILES
        except Exception:
            self.root = ctk.CTk()
            self._dnd_files = None

        self.root.title("PixelForge — Конвертер изображений")
        self.root.geometry("800x600")
        self.root.resizable(False, False)
        try:
            self.root.configure(fg_color=BG)
        except Exception:
            try:
                self.root.configure(bg=BG)
            except Exception:
                pass

        self.cfg = ConfigManager()
        self.tm = ThreadManager()
        self.source_files: list[Path] = []
        self.source_from_cache = False  # источник лежит в .cache_processing
        self.source_label_text = tk.StringVar(value=HINT_TEXT)
        self.working = False
        self.cache_dir = get_cache_dir()
        self._status_var = tk.StringVar(value="Ожидание источника…")
        self._counter_var = tk.StringVar(value="Обработано: 0 из 0 файлов (0% объектов)")

        self._build()
        self._apply_config_to_widgets()
        self._set_start_enabled(False)

    # ---------- построение ----------
    def _build(self):
        # Верхняя зона: Drag-and-Drop (фиксированная высота по ТЗ, иначе CTk
        # растит кадр по содержимому — pack_propagate(False), ТЗ п.5.2)
        self.drop_frame = ctk.CTkFrame(self.root, fg_color=PANEL, border_width=2,
                                       border_color=ACCENT, width=776, height=156)
        self.drop_frame.place(x=12, y=12)

        drow = ctk.CTkFrame(self.drop_frame, fg_color="transparent")
        drow.pack(expand=True, fill="both", padx=14, pady=12)
        # Иконка папки/архива по центру зоны — ТЗ п.5.2
        ctk.CTkLabel(drow, text="📁", font=("Segoe UI", 30), text_color=TEXT_MAIN,
                     width=48).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(drow, textvariable=self.source_label_text,
                     font=FONT_UI, text_color=TEXT_SUB, wraplength=360,
                     justify="left").pack(side="left", fill="both", expand=True, padx=(0, 10))
        # Кнопка «Обзор» справа от текста — ТЗ п.5.2
        btn_col = ctk.CTkFrame(drow, fg_color="transparent")
        btn_col.pack(side="right")
        self.btn_browse = ctk.CTkButton(btn_col, text="Обзор", width=140, fg_color=ACCENT,
                                        command=self.on_browse_source)
        self.btn_browse.pack(pady=(0, 6))
        self.btn_outdir = ctk.CTkButton(btn_col, text="Папка вывода", width=140, fg_color="#3a3a3a",
                                        command=self.on_browse_output)
        self.btn_outdir.pack()
        self.drop_frame.pack_propagate(False)  # фикс. высота зоны импорта

        if self._dnd_files:
            try:
                self.drop_frame.drop_target_register(self._dnd_files)  # type: ignore[attr-defined]
                self.drop_frame.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore[attr-defined]
            except Exception:
                pass

        # Средняя зона: настройки (слева) — ТЗ п.5.2.
        # pack_propagate(False): CTk иначе растит кадр по содержимому и панель
        # вылезает на прогресс-бар.
        self.settings = ctk.CTkFrame(self.root, fg_color=PANEL, width=260, height=314)
        self.settings.place(x=12, y=176)
        self.settings.pack_propagate(False)

        ctk.CTkLabel(self.settings, text="НАСТРОЙКИ", font=FONT_TITLE,
                     text_color=TEXT_MAIN).pack(anchor="w", padx=12, pady=(8, 4))

        ctk.CTkLabel(self.settings, text="Формат вывода", font=("Segoe UI", 10),
                     text_color=TEXT_SUB, height=20).pack(anchor="w", padx=12, pady=(4, 0))
        self.fmt_var = tk.StringVar(value="WebP")
        self.fmt_box = ctk.CTkOptionMenu(self.settings, values=["WebP", "JPEG"], variable=self.fmt_var,
                                         command=lambda _: self._persist())
        self.fmt_box.pack(fill="x", padx=12, pady=2)

        ctk.CTkLabel(self.settings, text="Метод интерполяции", font=("Segoe UI", 10),
                     text_color=TEXT_SUB, height=20).pack(anchor="w", padx=12, pady=(4, 0))
        self.interp_var = tk.StringVar(value="Lanczos")
        self.interp_box = ctk.CTkOptionMenu(
            self.settings, values=["Lanczos (Рекомендуется)", "Bicubic", "Bilinear"],
            variable=self.interp_var, command=lambda _: self._persist())
        self.interp_box.pack(fill="x", padx=12, pady=2)

        qrow = ctk.CTkFrame(self.settings, fg_color="transparent", height=24)
        qrow.pack_propagate(False)
        qrow.pack(fill="x", padx=12, pady=(6, 0))
        ctk.CTkLabel(qrow, text="Качество", font=("Segoe UI", 10), text_color=TEXT_SUB).pack(side="left")
        self.q_label = ctk.CTkLabel(qrow, text="80%", font=("Segoe UI", 10, "bold"), text_color=TEXT_MAIN)
        self.q_label.pack(side="right")
        self.quality = ctk.CTkSlider(self.settings, from_=10, to=100, number_of_steps=90,
                                     command=self._on_quality)
        self.quality.pack(fill="x", padx=12, pady=4)

        self.resize_var = tk.BooleanVar(value=False)
        self.keep_var = tk.BooleanVar(value=True)
        self.resize_check = ctk.CTkCheckBox(self.settings, text="Изменить разрешение",
                                            variable=self.resize_var, font=("Segoe UI", 10),
                                            command=self._on_resize_toggle)
        self.resize_check.pack(anchor="w", padx=12, pady=(6, 0))
        wh = ctk.CTkFrame(self.settings, fg_color="transparent", height=32)
        wh.pack_propagate(False)
        wh.pack(fill="x", padx=12, pady=4)
        self.w_entry = ctk.CTkEntry(wh, placeholder_text="Ширина", width=90, height=28)
        self.w_entry.pack(side="left", padx=(0, 6))
        self.h_entry = ctk.CTkEntry(wh, placeholder_text="Высота", width=90, height=28)
        self.h_entry.pack(side="left")
        self.keep_check = ctk.CTkCheckBox(self.settings, text="Сохранять пропорции",
                                          variable=self.keep_var, font=("Segoe UI", 10),
                                          command=lambda: self._persist())
        self.keep_check.pack(anchor="w", padx=12, pady=4)

        # Лог
        log_frame = ctk.CTkFrame(self.root, fg_color=PANEL, width=506, height=314)
        log_frame.place(x=282, y=176)
        log_frame.pack_propagate(False)
        ctk.CTkLabel(log_frame, text="ЖУРНАЛ ВЫВОДА", font=FONT_TITLE,
                     text_color=TEXT_SUB).pack(anchor="w", padx=12, pady=(8, 4))
        self.log_box = ctk.CTkTextbox(log_frame, font=("Consolas", 10), text_color=TEXT_SUB,
                                      fg_color="#181818", wrap="word")
        self.log_box.pack(fill="both", expand=True, padx=12, pady=(0, 10))
        self.log_box.configure(state="disabled")

        # Нижняя зона: прогресс + кнопки — ТЗ п.5.2
        self.btn_cancel = ctk.CTkButton(self.root, text="Прервать", width=70, height=30, fg_color=ERROR,
                                        hover_color="#8f1d1d", command=self.on_abort)
        self.btn_cancel.place(x=640, y=494)
        self.btn_start = ctk.CTkButton(self.root, text="Старт", width=72, height=30, fg_color=ACCENT,
                                       command=self.on_start)
        self.btn_start.place(x=716, y=494)

        self.progress = ctk.CTkProgressBar(self.root, progress_color=ACCENT, width=620, height=14)
        self.progress.place(x=12, y=500)
        self.progress.set(0)
        # Счётчик под прогресс-баром — ТЗ п.5.2
        ctk.CTkLabel(self.root, textvariable=self._counter_var, font=FONT_UI,
                     text_color=TEXT_SUB, width=620).place(x=12, y=522)

        ctk.CTkLabel(self.root, textvariable=self._status_var, font=FONT_UI,
                     text_color=TEXT_SUB, width=776).place(x=12, y=546)
        self.out_var = tk.StringVar(value="")
        ctk.CTkLabel(self.root, textvariable=self.out_var, font=FONT_UI,
                     text_color=TEXT_SUB, width=776).place(x=12, y=570)
        self._refresh_out_label()

    # ---------- helpers ----------
    def _interp_value(self) -> str:
        v = self.interp_var.get()
        if v.startswith("Bicubic"):
            return "Bicubic"
        if v.startswith("Bilinear"):
            return "Bilinear"
        return "Lanczos"

    def _apply_config_to_widgets(self):
        d = self.cfg.data
        self.fmt_var.set(d.get("output_format", "WebP"))
        iv = d.get("interpolation", "Lanczos")
        self.interp_var.set({"Lanczos": "Lanczos (Рекомендуется)",
                             "Bicubic": "Bicubic", "Bilinear": "Bilinear"}.get(iv, "Lanczos (Рекомендуется)"))
        try:
            self.quality.set(int(d.get("quality", 80)))
        except Exception:
            pass
        self.q_label.configure(text=f"{int(self.quality.get())}%")
        self.resize_var.set(bool(d.get("resize_enabled", False)))
        self.keep_var.set(bool(d.get("keep_aspect", True)))
        self.w_entry.delete(0, "end"); self.w_entry.insert(0, str(d.get("width", 1920)))
        self.h_entry.delete(0, "end"); self.h_entry.insert(0, str(d.get("height", 1080)))
        self._on_resize_toggle(persist=False)
        self._refresh_out_label()

    def _persist(self):
        try:
            w = int(self.w_entry.get() or 1920)
        except ValueError:
            w = 1920
        try:
            h = int(self.h_entry.get() or 1080)
        except ValueError:
            h = 1080
        self.cfg.update({
            "output_format": self.fmt_var.get(),
            "quality": int(self.quality.get()),
            "interpolation": self._interp_value(),
            "resize_enabled": bool(self.resize_var.get()),
            "width": w, "height": h,
            "keep_aspect": bool(self.keep_var.get()),
            "output_dir": self.cfg.get("output_dir", ""),
        })
        self._refresh_out_label()

    def _refresh_out_label(self):
        od = self.cfg.get("output_dir", "") or "— не выбрана (будет создана ./Converted) —"
        self.out_var.set(f"Выходная папка: {od}")

    def _on_quality(self, v):
        self.q_label.configure(text=f"{int(float(v))}%")
        self._persist()

    def _on_resize_toggle(self, persist: bool = True):
        st = "normal" if self.resize_var.get() else "disabled"
        self.w_entry.configure(state=st)
        self.h_entry.configure(state=st)
        if persist:
            self._persist()

    def log(self, msg: str):
        def _do():
            self.log_box.configure(state="normal")
            self.log_box.insert("end", msg + "\n")
            self.log_box.see("end")
            self.log_box.configure(state="disabled")
        self.root.after(0, _do)

    def _set_start_enabled(self, enabled: bool):
        self.root.after(0, lambda: self.btn_start.configure(state="normal" if enabled else "disabled"))

    def _set_busy(self, busy: bool):
        def _do():
            # Этап валидации/конвертации: настройки заморожены — ТЗ п.5.3
            s = "disabled" if busy else "normal"
            for w in (self.fmt_box, self.interp_box, self.quality, self.resize_check,
                      self.keep_check, self.w_entry, self.h_entry,
                      self.btn_browse, self.btn_outdir):
                try:
                    w.configure(state=s)
                except Exception:
                    pass
            if busy:
                self.progress.configure(mode="indeterminate")
                try:
                    self.progress.start()
                except Exception:
                    pass
            else:
                try:
                    self.progress.stop()
                except Exception:
                    pass
                self.progress.configure(mode="determinate")
                self._on_resize_toggle(persist=False)  # поля W/H зависят от чекбокса
        self.root.after(0, _do)

    def _flash_drop_error(self):
        def _do():
            try:
                self.drop_frame.configure(border_color=ERROR)
                self.root.after(600, lambda: self.drop_frame.configure(border_color=ACCENT))
            except Exception:
                pass
        self.root.after(0, _do)

    # ---------- источник ----------
    def _on_drop(self, event):
        raw = self.root.tk.splitlist(event.data)
        if raw:
            self.load_source(Path(raw[0].strip("{}")))

    def on_browse_source(self):
        # Сначала пробуем файл-архив, иначе папку
        f = filedialog.askopenfilename(title="Выберите архив",
                                       filetypes=[("Архивы", "*.zip *.rar *.7z"), ("Все", "*.*")])
        if f:
            self.load_source(Path(f))
            return
        d = filedialog.askdirectory(title="Выберите папку с изображениями")
        if d:
            self.load_source(Path(d))

    def on_browse_output(self):
        d = filedialog.askdirectory(title="Папка для результатов")
        if d:
            self.cfg.update({"output_dir": d})
            self._refresh_out_label()
            self.log(f"Папка вывода: {d}")

    def load_source(self, path: Path):
        if self.working:
            return
        self._set_busy(True)
        self._set_status("Запуск сквозной валидации…")
        threading.Thread(target=self._validate_bg, args=(path,), daemon=True).start()

    def _set_status(self, text: str):
        """Потокобезопасная установка статуса из фонового потока."""
        self.root.after(0, lambda t=text: self._status_var.set(t))

    def _validate_bg(self, path: Path):
        try:
            self.root.after(0, lambda: self._set_start_enabled(False))
            if not path.exists():
                raise ValidationError(f"Путь не найден: {path}")
            if is_archive(path):
                self.log(f"Архивом загружен файл: {path.name}")
                self.log("Запуск сквозной валидации... проверка сигнатур")
                # Чистый кэш перед распаковкой — ТЗ п.1.5, п.2.3
                from config_manager import ConfigManager as CM
                CM.clear_cache(self.cache_dir)
                files = validate_and_extract(path, self.cache_dir)
                self.log(f"Валидация успешна. Найдено {len(files)} изображений")
                self.source_from_cache = True
            elif path.is_dir():
                self.log(f"Папка загружена: {path.name}")
                files = collect_from_folder(path)
                if not files:
                    raise ValidationError("В папке нет изображений PNG/JPEG/BMP")
                self.log(f"Валидация успешна. Найдено {len(files)} изображений в папке")
                self.source_from_cache = False
            elif path.is_file():
                # Одиночный файл — валидация в сервисном слое (ТЗ п.1.4)
                from archive_validator import validate_single_image
                self.log(f"Загружен файл: {path.name}")
                files = [validate_single_image(path)]
                self.log("Валидация успешна. Найдено 1 изображение")
                self.source_from_cache = False
            else:
                raise ValidationError(f"Неподдерживаемый источник: {path}")
            self.source_files = files
            self.root.after(0, lambda: self.source_label_text.set(
                f"{path.name} — {len(files)} файл(ов) готово к конвертации"))
            self._set_status("Валидация успешна. Нажмите «Старт».")
            self._set_start_enabled(True)
        except ValidationError as e:
            self.log(f"Ошибка: {e}. Процесс остановлен.")
            self._flash_drop_error()
            self.root.after(0, lambda m=str(e): messagebox.showerror("Ошибка валидации", m))
            self._set_status("Ошибка валидации.")
            self.source_files = []
            self.source_from_cache = False
            self._set_start_enabled(False)
        except Exception as e:
            self.log(f"Ошибка: {e}. Процесс остановлен.")
            # Любая нештатная ситуация: кэш стирается, источник сбрасывается (ТЗ п.2.3)
            from config_manager import ConfigManager as CM
            CM.clear_cache(self.cache_dir)
            self.source_files = []
            self.source_from_cache = False
            self.root.after(0, lambda: self.source_label_text.set(HINT_TEXT))
            self._set_start_enabled(False)
        finally:
            self._set_busy(False)

    # ---------- старт / стоп ----------
    def _output_dir(self) -> Path:
        od = (self.cfg.get("output_dir", "") or "").strip()
        if od:
            return Path(od)
        # рядом с источником либо ./Converted
        if self.source_files:
            try:
                return self.source_files[0].parent / "Converted"
            except Exception:
                pass
        return Path.cwd() / "Converted"

    def on_start(self):
        if self.working or not self.source_files:
            return
        self._persist()
        self.working = True
        self._set_busy(True)
        self._set_start_enabled(False)
        self.progress.set(0)
        dst = self._output_dir()
        try:
            w = int(self.w_entry.get()) if self.resize_var.get() else None
        except ValueError:
            w = None
        try:
            h = int(self.h_entry.get()) if self.resize_var.get() else None
        except ValueError:
            h = None
        kwargs = {
            "output_format": self.fmt_var.get(),
            "quality": int(self.quality.get()),
            "resize_enabled": bool(self.resize_var.get()),
            "width": w, "height": h,
            "keep_aspect": bool(self.keep_var.get()),
            "interpolation": self._interp_value(),
        }
        self.log(f"Старт: формат={kwargs['output_format']} качество={kwargs['quality']}% "
                 f"интерполяция={kwargs['interpolation']} потоков={self.tm.max_workers}")
        total = len(self.source_files)
        threading.Thread(target=self._convert_bg, args=(list(self.source_files), dst, kwargs, total),
                         daemon=True).start()

    def _convert_bg(self, files, dst, kwargs, total):
        def on_progress(done, tot):
            frac = done / max(1, tot)
            pct = int(frac * 100)
            # ТЗ п.5.2: «Обработано: X из Y файлов (Z% объектов)»
            self.root.after(0, lambda d=done, t=tot, p=pct: (
                self.progress.configure(mode="determinate"),
                self.progress.set(p / 100),
                self._counter_var.set(f"Обработано: {d} из {t} файлов ({p}% объектов)"),
            ))
        def on_ok(src, out):
            self.log(f"OK  {src.name} -> {out.name}")
        def on_error(src, err):
            self.log(f"ОШИБКА {src.name}: {err}")
        def on_finished(done, tot):
            pass
        try:
            self.tm.start(files, dst, kwargs, on_progress=on_progress,
                          on_ok=on_ok, on_error=on_error, on_finished=on_finished)
            if self.tm.aborted:
                self.log("Прервано пользователем. Очередь очищена.")
                self._set_status("Прервано.")
            else:
                self.log(f"Готово. Результаты: {dst}")
                self._set_status("Конвертация завершена.")
                # ТЗ п.5.3: по завершении — негромкий системный звуковой сигнал
                self.root.after(0, self.root.bell)
                os.startfile(dst)
        finally:
            # ТЗ п.1.5: файлы кэша удаляются безвозвратно после сбора результатов
            from config_manager import ConfigManager as CM
            os.startfile(dst)
            CM.clear_cache(self.cache_dir)
            if self.source_from_cache:
                # Файлы из кэша уничтожены — нужна новая валидация для следующего цикла
                self.source_files = []
                self.source_from_cache = False
                self.root.after(0, lambda: self.source_label_text.set(HINT_TEXT))
                self.log("Кэш .cache_processing очищен. Для следующего цикла перетащите архив заново.")
            self.working = False
            self._set_busy(False)
            # Разблокировать для следующего цикла — ТЗ п.5.3
            if self.source_files:
                self._set_start_enabled(True)

    def on_abort(self):
        if not self.working:
            return
        self.tm.abort()
        self.log("Abort: остановка пула, очистка очереди и кэша…")

    def run(self):
        self.log("PixelForge готов. Источник не выбран.")
        self.root.mainloop()
