"""Tkinter desktop client.

The window is unchanged from the original app; what sits behind the Process
button is now :class:`dataforge.core.dedupe.Deduplicator`, so the desktop, the
CLI and the web UI all share one matching engine.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk
import pandas as pd

from dataforge.core.dedupe import DedupeConfig, Deduplicator
from dataforge.core.io import load_table, save_table
from dataforge.logging import get_logger

logger = get_logger(__name__)

DARK_THEME = {
    "bg": "#2b2b2b",
    "fg": "white",
    "button_bg": "#3A7CA5",
    "button_text": "white",
    "button_hover": "#4A9FC7",
}
LIGHT_THEME = {
    "bg": "#f0f0f0",
    "fg": "black",
    "button_bg": "#3A7CA5",
    "button_text": "white",
    "button_hover": "#74B9FF",
}

DARK_TO_LIGHT = ["🌑", "🌒", "🌓", "🌔", "🌕"]
LIGHT_TO_DARK = ["🌕", "🌖", "🌗", "🌘", "🌑"]


class App(ctk.CTk):
    """The main application window."""

    def __init__(self) -> None:
        super().__init__()

        self.file_paths: list[str] = []
        self.current_theme = DARK_THEME
        self.current_phase_index = 0
        self.animating = False

        self.geometry("560x480+100+100")
        self.title("DataForge — Duplicate Detection")
        self.resizable(True, True)

        self.main_frame = ctk.CTkFrame(self, fg_color=self.current_theme["bg"])
        self.main_frame.pack(fill="both", expand=True)

        self.btn_toggle_theme = ctk.CTkButton(
            self.main_frame,
            text=DARK_TO_LIGHT[0],
            width=20,
            command=self.toggle_theme,
            fg_color=self.current_theme["button_bg"],
            text_color="white",
        )
        self.btn_toggle_theme.place(relx=0.95, rely=0.02, anchor="ne")

        self.content_frame = ctk.CTkFrame(self.main_frame, fg_color=self.current_theme["bg"])
        self.content_frame.pack(fill="both", expand=True, pady=(40, 0))

        self._build_widgets()
        self.apply_theme(self.current_theme)

    def _build_widgets(self) -> None:
        self.content_frame.grid_rowconfigure(0, weight=1)
        self.content_frame.grid_rowconfigure(8, weight=1)
        self.content_frame.grid_columnconfigure(0, weight=1)

        self.btn_select_files = self._button("Select Files", self.select_files, row=1)
        self.label_files = ctk.CTkLabel(self.content_frame, text="No files selected")
        self.label_files.grid(row=2, column=0, pady=(0, 10), sticky="n")

        self.label_threshold = ctk.CTkLabel(self.content_frame, text="Match threshold: 0.85")
        self.label_threshold.grid(row=3, column=0, pady=(10, 0), sticky="n")

        self.slider_threshold = ctk.CTkSlider(
            self.content_frame,
            from_=0.5,
            to=1.0,
            number_of_steps=50,
            width=200,
            command=self._on_threshold_change,
        )
        self.slider_threshold.set(0.85)
        self.slider_threshold.grid(row=4, column=0, pady=(5, 10), sticky="n")

        self.option_output_type = ctk.CTkComboBox(
            self.content_frame, values=["csv", "xlsx", "json", "parquet", "pdf"], width=150
        )
        self.option_output_type.set("xlsx")
        self.option_output_type.grid(row=5, column=0, pady=(10, 10), sticky="n")

        self.btn_process = self._button("Process", self.process_files, row=6)

        self.label_result = ctk.CTkLabel(self.content_frame, text="", wraplength=460)
        self.label_result.grid(row=7, column=0, pady=(10, 10), sticky="n")

    def _button(self, text: str, command, row: int) -> ctk.CTkButton:
        button = ctk.CTkButton(
            self.content_frame,
            text=text,
            command=command,
            width=180,
            fg_color=self.current_theme["button_bg"],
            hover_color=self.current_theme["button_hover"],
            text_color=self.current_theme["button_text"],
        )
        button.grid(row=row, column=0, pady=(10, 5), sticky="n")
        return button

    # -- theming ---------------------------------------------------------

    def apply_theme(self, theme: dict[str, str]) -> None:
        self.main_frame.configure(fg_color=theme["bg"])
        self.content_frame.configure(fg_color=theme["bg"])
        self.btn_toggle_theme.configure(fg_color=theme["button_bg"], text_color="white")

        for label in (self.label_files, self.label_result, self.label_threshold):
            label.configure(fg_color=theme["bg"], text_color=theme["fg"])
        for button in (self.btn_select_files, self.btn_process):
            button.configure(
                fg_color=theme["button_bg"],
                text_color=theme["button_text"],
                hover_color=theme["button_hover"],
            )

    def toggle_theme(self) -> None:
        if self.animating:
            return
        self.animating = True
        phases = DARK_TO_LIGHT if self.current_theme is DARK_THEME else LIGHT_TO_DARK
        for i, phase in enumerate(phases):
            self.after(i * 50, lambda p=phase: self.btn_toggle_theme.configure(text=p))
        self.after(len(phases) * 50, self._complete_toggle)

    def _complete_toggle(self) -> None:
        self.current_theme = LIGHT_THEME if self.current_theme is DARK_THEME else DARK_THEME
        self.apply_theme(self.current_theme)
        self.animating = False

    # -- actions ---------------------------------------------------------

    def _on_threshold_change(self, value: float) -> None:
        self.label_threshold.configure(text=f"Match threshold: {value:.2f}")

    def select_files(self) -> None:
        paths = filedialog.askopenfilenames(
            filetypes=[("Data files", "*.csv *.xlsx *.xls *.json *.parquet"), ("All files", "*.*")]
        )
        if paths:
            self.file_paths = list(paths)
            names = ", ".join(os.path.basename(p) for p in self.file_paths[:3])
            suffix = f" (+{len(self.file_paths) - 3} more)" if len(self.file_paths) > 3 else ""
            self.label_files.configure(text=f"{len(self.file_paths)} selected: {names}{suffix}")

    def _set_result(self, text: str, color: str) -> None:
        self.label_result.configure(text=text, text_color=color)

    def process_files(self) -> None:
        if not self.file_paths:
            self._set_result("Select at least one file first.", "red")
            return

        output_dir = filedialog.askdirectory(title="Choose an output folder")
        if not output_dir:
            self._set_result("Cancelled: no output folder chosen.", "red")
            return

        output_type = self.option_output_type.get()
        threshold = float(self.slider_threshold.get())
        paths = list(self.file_paths)

        self.btn_process.configure(state="disabled")
        self._set_result("Processing…", self.current_theme["fg"])
        threading.Thread(
            target=self._process_in_background,
            args=(paths, Path(output_dir), output_type, threshold),
            daemon=True,
        ).start()

    def _process_in_background(
        self, paths: list[str], output_dir: Path, output_type: str, threshold: float
    ) -> None:
        """Run the engine off the UI thread and post the result back to it."""
        try:
            frames = [load_table(p) for p in paths]
            combined = pd.concat(frames, ignore_index=True).fillna("")
            result = Deduplicator(config=DedupeConfig(threshold=threshold)).run(combined)

            output_path = output_dir / f"deduplicated.{output_type}"
            save_table(result.frame, output_path, output_type)

            summary = result.summary()
            message = (
                f"Done. {summary['duplicates_removed']} duplicates removed "
                f"({summary['input_rows']} → {summary['output_rows']} rows).\n"
                f"Saved to {output_path}"
            )
            self.after(0, lambda: self._finish(message, "green"))
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            logger.exception("Processing failed")
            # Bind the message now: Python unbinds `exc` when the except block
            # exits, and this lambda runs later on the UI thread.
            message = f"Error: {exc}"
            self.after(0, lambda: self._finish(message, "red"))

    def _finish(self, message: str, color: str) -> None:
        self._set_result(message, color)
        self.btn_process.configure(state="normal")
        self.file_paths = []
        self.label_files.configure(text="No files selected")


def run() -> None:
    """Entry point used by ``dataforge desktop``."""
    App().mainloop()


if __name__ == "__main__":
    run()
