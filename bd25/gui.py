from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import cast

from .core import (
    ConversionCancelled,
    ConversionError,
    ConversionOptions,
    Converter,
    ENCODERS,
    EncoderChoice,
    detect_encoders,
    find_handbrake,
    find_tsmuxer,
)


BG = "#101417"
PANEL = "#192025"
FIELD = "#232c32"
TEXT = "#edf2f4"
MUTED = "#97a6ad"
ACCENT = "#efb23c"
ACCENT_ACTIVE = "#ffc75a"
ERROR = "#ef6a63"


class BD25App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("BD25 Forge")
        self.geometry("860x690")
        self.minsize(760, 620)
        self.configure(bg=BG)

        self._events: queue.Queue[tuple[str, object]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._converter: Converter | None = None
        self._encoder_map: dict[str, EncoderChoice] = {}

        handbrake = find_handbrake()
        tsmuxer = find_tsmuxer()
        self._handbrake = handbrake
        self._tsmuxer = tsmuxer
        self.source_var = tk.StringVar()
        self.destination_var = tk.StringVar()
        self.encoder_var = tk.StringVar()
        self.target_var = tk.StringVar(value="25.0")
        self.language_var = tk.StringVar(value="eng")
        self.title_var = tk.StringVar(value="Auto")
        self.forced_subs_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Ready")
        self.detail_var = tk.StringVar(value="Choose a source ISO to begin")
        self.progress_var = tk.DoubleVar(value=0)

        self._configure_style()
        self._build_ui()
        self._refresh_encoders()
        if not self._handbrake or not self._tsmuxer:
            self.status_var.set("RUNTIME MISSING")
            self.detail_var.set("Install a complete BD25 Forge application bundle")
            self.start_button.configure(state="disabled")
        self.after(100, self._drain_events)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
        style.configure("PanelMuted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("Section.TLabel", background=PANEL, foreground=ACCENT, font=("Segoe UI Semibold", 10))
        style.configure("Title.TLabel", foreground=TEXT, font=("Segoe UI Semibold", 25))
        style.configure("Subtitle.TLabel", foreground=MUTED, font=("Segoe UI", 10))
        style.configure("TEntry", fieldbackground=FIELD, foreground=TEXT, insertcolor=TEXT, bordercolor=FIELD, padding=8)
        style.configure("TCombobox", fieldbackground=FIELD, background=FIELD, foreground=TEXT, arrowcolor=TEXT, padding=7)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", TEXT)])
        style.configure("TCheckbutton", background=PANEL, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", PANEL)], foreground=[("active", TEXT)])
        style.configure("Browse.TButton", background=FIELD, foreground=TEXT, borderwidth=0, padding=(12, 8))
        style.map("Browse.TButton", background=[("active", "#303c43")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#17130a", borderwidth=0, padding=(22, 12), font=("Segoe UI Semibold", 10))
        style.map("Accent.TButton", background=[("active", ACCENT_ACTIVE), ("disabled", "#6f654f")])
        style.configure("Cancel.TButton", background=FIELD, foreground=TEXT, borderwidth=0, padding=(18, 12))
        style.map("Cancel.TButton", background=[("active", "#303c43")])
        style.configure("Horizontal.TProgressbar", troughcolor=FIELD, background=ACCENT, bordercolor=FIELD, lightcolor=ACCENT, darkcolor=ACCENT)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=(30, 24, 30, 24))
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)

        heading = ttk.Frame(root)
        heading.grid(row=0, column=0, sticky="ew", pady=(0, 20))
        ttk.Label(heading, text="BD25 FORGE", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            heading,
            text="Compress a movie-only Blu-ray ISO to fit a 25 GB disc",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        files = ttk.Frame(root, style="Panel.TFrame", padding=18)
        files.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        files.columnconfigure(0, weight=1)
        ttk.Label(files, text="SOURCE AND DESTINATION", style="Section.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 12))
        self._path_row(files, 1, "Source ISO", self.source_var, self._browse_source)
        self._path_row(files, 2, "Output ISO", self.destination_var, self._browse_destination)

        settings = ttk.Frame(root, style="Panel.TFrame", padding=18)
        settings.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        for column in range(4):
            settings.columnconfigure(column, weight=1 if column in (0, 1) else 0)
        ttk.Label(settings, text="ENCODE SETTINGS", style="Section.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 12))

        ttk.Label(settings, text="Encoder", style="Panel.TLabel").grid(row=1, column=0, sticky="w")
        ttk.Label(settings, text="Target (decimal GB)", style="Panel.TLabel").grid(row=1, column=1, sticky="w", padx=(12, 0))
        ttk.Label(settings, text="Audio language", style="Panel.TLabel").grid(row=1, column=2, sticky="w", padx=(12, 0))
        ttk.Label(settings, text="Title", style="Panel.TLabel").grid(row=1, column=3, sticky="w", padx=(12, 0))

        self.encoder_combo = ttk.Combobox(settings, textvariable=self.encoder_var, state="readonly", width=24)
        self.encoder_combo.grid(row=2, column=0, sticky="ew", pady=(5, 12))
        ttk.Entry(settings, textvariable=self.target_var, width=12).grid(row=2, column=1, sticky="ew", padx=(12, 0), pady=(5, 12))
        ttk.Entry(settings, textvariable=self.language_var, width=12).grid(row=2, column=2, sticky="ew", padx=(12, 0), pady=(5, 12))
        ttk.Entry(settings, textvariable=self.title_var, width=8).grid(row=2, column=3, sticky="ew", padx=(12, 0), pady=(5, 12))
        ttk.Checkbutton(
            settings,
            text="Detect and burn forced subtitles",
            variable=self.forced_subs_var,
        ).grid(row=3, column=0, columnspan=2, sticky="w")

        ttk.Label(
            settings,
            text="Integrated transcoder and Blu-ray authoring runtime",
            style="PanelMuted.TLabel",
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(14, 0))

        activity = ttk.Frame(root, style="Panel.TFrame", padding=18)
        activity.grid(row=3, column=0, sticky="nsew")
        activity.columnconfigure(0, weight=1)
        activity.rowconfigure(3, weight=1)
        status_row = ttk.Frame(activity, style="Panel.TFrame")
        status_row.grid(row=0, column=0, sticky="ew")
        ttk.Label(status_row, textvariable=self.status_var, style="Section.TLabel").pack(side="left")
        ttk.Label(status_row, textvariable=self.detail_var, style="Panel.TLabel").pack(side="right")
        ttk.Progressbar(activity, variable=self.progress_var, maximum=100).grid(row=1, column=0, sticky="ew", pady=(10, 12))

        log_frame = tk.Frame(activity, bg=FIELD, highlightthickness=0)
        log_frame.grid(row=3, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_frame,
            bg=FIELD,
            fg=MUTED,
            insertbackground=TEXT,
            selectbackground="#42515a",
            relief="flat",
            wrap="word",
            padx=10,
            pady=8,
            height=7,
            font=("Consolas", 9),
            state="disabled",
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        actions = ttk.Frame(root)
        actions.grid(row=4, column=0, sticky="e", pady=(14, 0))
        self.cancel_button = ttk.Button(actions, text="Cancel", style="Cancel.TButton", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=(0, 8))
        self.start_button = ttk.Button(actions, text="Build BD25 ISO", style="Accent.TButton", command=self._start)
        self.start_button.pack(side="left")

    def _path_row(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, callback: object) -> None:
        line = ttk.Frame(parent, style="Panel.TFrame")
        line.grid(row=row, column=0, sticky="ew", pady=(0, 8 if row == 1 else 0))
        line.columnconfigure(1, weight=1)
        ttk.Label(line, text=label, style="Panel.TLabel", width=12).grid(row=0, column=0, sticky="w")
        ttk.Entry(line, textvariable=variable).grid(row=0, column=1, sticky="ew")
        ttk.Button(line, text="Browse", style="Browse.TButton", command=callback).grid(row=0, column=2, padx=(8, 0))

    def _browse_source(self) -> None:
        filename = filedialog.askopenfilename(title="Choose Blu-ray ISO", filetypes=(("ISO images", "*.iso"), ("All files", "*.*")))
        if filename:
            self.source_var.set(filename)
            if not self.destination_var.get():
                source = Path(filename)
                self.destination_var.set(str(source.with_name(f"{source.stem}-BD25.iso")))

    def _browse_destination(self) -> None:
        filename = filedialog.asksaveasfilename(title="Save BD25 ISO", defaultextension=".iso", filetypes=(("ISO images", "*.iso"),))
        if filename:
            self.destination_var.set(filename)

    def _refresh_encoders(self) -> None:
        choices = detect_encoders(self._handbrake) if self._handbrake else ()
        if not choices:
            choices = (ENCODERS[-1],)
        self._encoder_map = {choice.label: choice for choice in choices}
        self.encoder_combo.configure(values=tuple(self._encoder_map))
        self.encoder_var.set(next(iter(self._encoder_map)))

    def _start(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        if not self._handbrake or not self._tsmuxer:
            messagebox.showerror(
                "Runtime missing",
                "This installation is incomplete. Reinstall the complete BD25 Forge application bundle.",
            )
            return
        try:
            target = float(self.target_var.get())
            title_text = self.title_var.get().strip()
            title = None if title_text.lower() in ("", "auto") else int(title_text)
            encoder = self._encoder_map[self.encoder_var.get()]
            destination = Path(self.destination_var.get()).expanduser().resolve()
            options = ConversionOptions(
                source=Path(self.source_var.get()).expanduser().resolve(),
                destination=destination,
                handbrake=self._handbrake,
                tsmuxer=self._tsmuxer,
                encoder=encoder,
                target_gb=target,
                language=self.language_var.get().strip().lower(),
                title=title,
                burn_forced_subtitles=self.forced_subs_var.get(),
            )
        except (ValueError, KeyError):
            messagebox.showerror("Invalid settings", "Target must be a number and title must be Auto or a title number.")
            return

        if options.source == destination:
            messagebox.showerror("Invalid output", "Source and output must be different files.")
            return
        if destination.exists():
            replace = messagebox.askyesno("Replace output?", f"The output already exists:\n\n{destination}\n\nReplace it?")
            if not replace:
                return
            try:
                destination.unlink()
            except OSError as exc:
                messagebox.showerror("Cannot replace output", str(exc))
                return

        self._clear_log()
        self.progress_var.set(0)
        self.status_var.set("STARTING")
        self.detail_var.set("Validating tools and source")
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self._converter = Converter(self._post_progress, self._post_log)
        self._worker = threading.Thread(target=self._run_conversion, args=(options,), daemon=True)
        self._worker.start()

    def _run_conversion(self, options: ConversionOptions) -> None:
        assert self._converter is not None
        try:
            output = self._converter.convert(options)
        except ConversionCancelled as exc:
            self._events.put(("cancelled", str(exc)))
        except (ConversionError, OSError) as exc:
            self._events.put(("error", str(exc)))
        except Exception as exc:
            self._events.put(("error", f"Unexpected error: {exc}"))
        else:
            self._events.put(("complete", output))

    def _post_progress(self, stage: str, fraction: float, message: str) -> None:
        self._events.put(("progress", (stage, fraction, message)))

    def _post_log(self, line: str) -> None:
        if line:
            self._events.put(("log", line))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self._events.get_nowait()
                if kind == "progress":
                    stage, fraction, message = cast(tuple[str, float, str], payload)
                    self.status_var.set(str(stage).upper())
                    self.detail_var.set(str(message))
                    self.progress_var.set(float(fraction) * 100)
                elif kind == "log":
                    self._append_log(str(payload))
                elif kind == "complete":
                    self._finish()
                    messagebox.showinfo("ISO complete", f"Created:\n\n{payload}")
                elif kind == "cancelled":
                    self.status_var.set("CANCELLED")
                    self.detail_var.set(str(payload))
                    self._finish()
                elif kind == "error":
                    self.status_var.set("FAILED")
                    self.detail_var.set(str(payload))
                    self._append_log(f"ERROR: {payload}")
                    self._finish()
                    messagebox.showerror("Conversion failed", str(payload))
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _cancel(self) -> None:
        if self._converter:
            self.detail_var.set("Stopping the current process")
            self.cancel_button.configure(state="disabled")
            self._converter.cancel()

    def _finish(self) -> None:
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self._converter = None

    def _on_close(self) -> None:
        if self._worker and self._worker.is_alive():
            close = messagebox.askyesno("Conversion running", "Cancel the conversion and close?")
            if not close:
                return
            if self._converter:
                self._converter.cancel()
        self.destroy()


def main() -> None:
    app = BD25App()
    app.mainloop()
