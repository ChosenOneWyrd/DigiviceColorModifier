#!/usr/bin/env python3
import os
import sys
import time
import shutil
import csv
import tempfile
from typing import List, Tuple, Optional

from PyQt5 import QtCore, QtGui, QtWidgets
import runpy

import export_sprites as es
import update_palette as up

# --- import your helper scripts as modules ---
if getattr(sys, 'frozen', False):
    # Running inside a bundled app (PyInstaller)
    SCRIPT_DIR = sys._MEIPASS
else:
    SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))

if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

BIN_TYPES = {
    "D-3": {
        "max_sprite_index": 2115,
        "label": "D-3 25th Color Evolution",
    },
    "Digivice": {
        "max_sprite_index": 1578,
        "label": "Digivice 25th Color Evolution",
    },
}

FORBIDDEN_CHARS = set("+-:<>?!~`'\"[]{}\\|@#$%^&*=,")


# ----------------- backend helpers -----------------

def export_sprites_gui(
    bin_path: str,
    out_dir: str,
    start: int = 0,
    end: Optional[int] = None,
    banks_str: str = "0-15",
    alpha_mode: str = "auto",
    palette_step: str = "colors",
    use_attr_palette: bool = False,
    progress_cb=None,
):
    """
    Re-implements export_sprites.main() in a callable form, with progress callback.

    progress_cb(fraction, message) where:
      - fraction: 0.0 .. 1.0
      - message: short status string
    """
    os.makedirs(out_dir, exist_ok=True)

    with open(bin_path, "rb") as f:
        data = f.read()

    pkg_off, parsed = es.scan_for_package(data)
    img_defs_offset, spr_defs_offset, palettes_offset, chars_offset, images, sprites, palette_words = parsed
    block = data[pkg_off:]

    num_images = len(images)
    if end is None or end > num_images:
        end = num_images
    start = max(0, min(start, num_images))
    end = max(start, end)

    # build bank list from string (e.g. "0-15" or "0,1,2")
    bank_list: List[int] = []
    banks_str = banks_str.strip()
    if "-" in banks_str:
        a, b = banks_str.split("-")
        bank_list = list(range(int(a), int(b) + 1))
    else:
        bank_list = [int(x) for x in banks_str.split(",") if x.strip() != ""]

    # compute total steps for progress
    total_steps = 0
    per_image_subs: List[int] = []
    for i in range(start, end):
        idef = images[i]
        sprites_per_sub = idef.width * idef.height
        if sprites_per_sub == 0:
            per_image_subs.append(0)
            continue
        if i + 1 < len(images):
            total_sprites_for_image = images[i + 1].sprite_start_index - idef.sprite_start_index
        else:
            total_sprites_for_image = len(sprites) - idef.sprite_start_index
        subimages = max(1, total_sprites_for_image // sprites_per_sub)
        per_image_subs.append(subimages)
        total_steps += subimages * len(bank_list)

    done_steps = 0
    for offset, i in enumerate(range(start, end)):
        idef = images[i]
        sprites_per_sub = idef.width * idef.height
        if sprites_per_sub == 0:
            continue
        subimages = per_image_subs[offset]
        for si in range(subimages):
            for bank in bank_list:
                img = es.compose_subimage(
                    block,
                    images,
                    sprites,
                    palette_words,
                    chars_offset,
                    image_index=i,
                    subimage_index=si,
                    bank=bank,
                    alpha_mode=alpha_mode,
                    palette_step_mode=palette_step,
                    use_attr_palette=use_attr_palette,
                )
                if img is None:
                    continue
                out_name = f"{i}_{si}_{bank}.png"
                out_path = os.path.join(out_dir, out_name)
                img.save(out_path)
                done_steps += 1
                if progress_cb and total_steps > 0:
                    progress_cb(done_steps / total_steps, f"Exported {out_name}")

    if progress_cb:
        progress_cb(1.0, f"Exported sprites {start}..{end - 1} to {out_dir}")


def update_palette_gui(
    bin_path: str,
    input_dir: str,
    out_path: str,
    alpha_mode: str = "inverted",
    set_sprite_bank: bool = False,
    dry_run: bool = False,
    progress_cb=None,
):
    """
    Wraps update_palette.main() behaviour with a progress callback.
    """
    jobs: List[Tuple[int, int, int, str]] = []
    for root, _, files in os.walk(input_dir):
        for fn in files:
            m = up.FNAME_RE.match(fn)
            if not m:
                continue
            idx = int(m.group(1))
            sub = int(m.group(2))
            bank = int(m.group(3))
            p = os.path.join(root, fn)
            jobs.append((idx, sub, bank, p))

    if not jobs:
        raise SystemExit(f"No files matching INDEX_SUBIMAGE_BANK.png found in {input_dir}")

    jobs.sort(key=lambda t: (t[0], t[1], t[2], t[3]))

    with open(bin_path, "rb") as f:
        data = bytearray(f.read())

    pkg_off, block, offs = up.robust_scan(data)
    images, sprites, palettes_off = up.parse(block, offs)

    total = len(jobs)
    for j, (idx, sub, bank, png_path) in enumerate(jobs, start=1):
        up.update_one(
            data,
            pkg_off,
            offs,
            images,
            sprites,
            idx,
            sub,
            png_path,
            bank,
            alpha_mode,
            set_sprite_bank,
            dry_run,
        )
        if progress_cb:
            progress_cb(j / total, f"Updated {os.path.basename(png_path)}")

    if dry_run:
        if progress_cb:
            progress_cb(1.0, f"[DRY] Processed {len(jobs)} file(s). No output written.")
    else:
        with open(out_path, "wb") as f:
            f.write(data)
        if progress_cb:
            progress_cb(1.0, f"[DONE] Updated {len(jobs)} palette bank(s). Wrote: {out_path}")


# ----------------- worker objects -----------------

class SpriteExportWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(float, str)
    finished = QtCore.pyqtSignal(bool, str)

    def __init__(self, bin_path, out_dir, start, end, banks_str, desc, parent=None):
        super().__init__(parent)
        self.bin_path = bin_path
        self.out_dir = out_dir
        self.start = start
        self.end = end
        self.banks_str = banks_str
        self.desc = desc

    @QtCore.pyqtSlot()
    def run(self):
        try:
            def cb(frac, msg):
                self.progress.emit(frac, msg)

            export_sprites_gui(
                self.bin_path,
                self.out_dir,
                start=self.start,
                end=self.end,
                banks_str=self.banks_str,
                alpha_mode="auto",
                palette_step="colors",
                use_attr_palette=False,
                progress_cb=cb,
            )
            self.finished.emit(True, self.desc + " completed.")
        except Exception as e:
            self.finished.emit(False, f"{self.desc} failed: {e}")


class PaletteWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(float, str)
    finished = QtCore.pyqtSignal(bool, str)

    def __init__(self, bin_path, input_dir, out_path, parent=None):
        super().__init__(parent)
        self.bin_path = bin_path
        self.input_dir = input_dir
        self.out_path = out_path

    @QtCore.pyqtSlot()
    def run(self):
        try:
            def cb(frac, msg):
                self.progress.emit(frac, msg)

            update_palette_gui(
                self.bin_path,
                self.input_dir,
                self.out_path,
                alpha_mode="inverted",
                set_sprite_bank=True,
                dry_run=False,
                progress_cb=cb,
            )
            self.finished.emit(True, "Palette update completed.")
        except Exception as e:
            self.finished.emit(False, f"Palette update failed: {e}")


class InternalScriptWorker(QtCore.QObject):
    """
    Executes a Python script INSIDE the frozen application's interpreter.
    Avoids subprocess deadlocks, missing Python interpreters, and missing stdout.
    """
    progress = QtCore.pyqtSignal(float, str)
    finished = QtCore.pyqtSignal(bool, str)

    def __init__(self, script_name: str, script_args: list, desc: str, parent=None):
        super().__init__(parent)
        self.script_name = script_name
        self.script_args = script_args
        self.desc = desc

    @QtCore.pyqtSlot()
    def run(self):
        old_argv = sys.argv
        try:
            # Build argv as if the script were run from command line
            sys.argv = [self.script_name] + self.script_args

            script_path = os.path.join(SCRIPT_DIR, self.script_name)

            self.progress.emit(0.0, f"Running internal script: {self.script_name}")

            # Run script in THIS interpreter, as __main__
            runpy.run_path(script_path, run_name="__main__")

            self.progress.emit(1.0, f"{self.desc} finished.")
            self.finished.emit(True, f"{self.desc} completed successfully.")
        except Exception as e:
            self.finished.emit(False, f"{self.desc} failed: {e}")
        finally:
            sys.argv = old_argv


# ----------------- dialogs -----------------

class ProgressDialog(QtWidgets.QDialog):
    """Used where we *do* know real progress (sprites export)."""
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)

        self._start_time = time.monotonic()

        layout = QtWidgets.QVBoxLayout(self)

        # --- NEW: kindness.gif animation ---
        gif_path = os.path.join(SCRIPT_DIR, "kindness.gif")
        self.gif_label = QtWidgets.QLabel()
        self.gif_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        if os.path.isfile(gif_path):
            self.movie = QtGui.QMovie(gif_path)
            self.gif_label.setMovie(self.movie)
            self.movie.start()
        else:
            self.gif_label.setText("[Missing kindness.gif]")

        # --- Existing widgets ---
        self.label = QtWidgets.QLabel("Starting...")
        self.label.setWordWrap(True)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 100)
        self.eta_label = QtWidgets.QLabel("Estimated time remaining: ...")

        # --- Add GIF ABOVE progress bar ---
        layout.addWidget(self.gif_label)
        layout.addWidget(self.label)
        layout.addWidget(self.bar)
        layout.addWidget(self.eta_label)

        self.resize(400, 200)

    @QtCore.pyqtSlot(float, str)
    def on_progress(self, fraction: float, message: str):
        fraction = max(0.0, min(1.0, float(fraction)))
        self.bar.setValue(int(fraction * 100))
        self.label.setText(message)

        if 0.0 < fraction < 1.0:
            elapsed = time.monotonic() - self._start_time
            if fraction > 0:
                remaining = elapsed * (1.0 - fraction) / fraction
                eta_text = f"Estimated time remaining: ~{int(remaining):d} s"
            else:
                eta_text = "Estimating..."
        elif fraction >= 1.0:
            eta_text = "Completed."
        else:
            eta_text = "Estimating..."
        self.eta_label.setText(eta_text)


class BusyDialog(QtWidgets.QDialog):
    """Spinner-style dialog for operations without reliable progress (digimon stats)."""
    def __init__(self, title: str, message: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)

        layout = QtWidgets.QVBoxLayout(self)

        # --- NEW: kindness.gif animation ---
        gif_path = os.path.join(SCRIPT_DIR, "kindness.gif")
        self.gif_label = QtWidgets.QLabel()
        self.gif_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        if os.path.isfile(gif_path):
            self.movie = QtGui.QMovie(gif_path)
            self.gif_label.setMovie(self.movie)
            self.movie.start()
        else:
            self.gif_label.setText("[Missing kindness.gif]")

        self.label = QtWidgets.QLabel(message)
        self.label.setWordWrap(True)

        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 0)  # busy spinner mode

        # --- Add GIF above everything ---
        layout.addWidget(self.gif_label)
        layout.addWidget(self.label)
        layout.addWidget(self.bar)

        # Size unchanged so main window does NOT resize
        self.setFixedSize(380, 200)


def clear_layout(layout: QtWidgets.QLayout):
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
        else:
            sub = item.layout()
            if sub is not None:
                clear_layout(sub)


# ----------------- Sprites tab -----------------

class SpritesTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.current_bin_type_key: Optional[str] = None
        self.current_bin_path: Optional[str] = None
        self.preview_dir: Optional[str] = None
        self.input_sprites_dir: Optional[str] = None

        self.range_list: List[Tuple[int, int]] = []

        self._build_ui()

    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)

        # Top: BIN selection + range/bank/preview
        top_box = QtWidgets.QGroupBox("BIN Selection & Preview Range")
        top_vlayout = QtWidgets.QVBoxLayout(top_box)

        row1 = QtWidgets.QHBoxLayout()
        self.bin_type_combo = QtWidgets.QComboBox()
        self.bin_type_combo.addItem("Select BIN type...")
        for key, info in BIN_TYPES.items():
            self.bin_type_combo.addItem(info["label"], key)

        self.bin_path_edit = QtWidgets.QLineEdit()
        self.bin_path_edit.setReadOnly(True)
        self.bin_browse_btn = QtWidgets.QPushButton("Select .bin file...")

        row1.addWidget(QtWidgets.QLabel("Type of .bin file:"))
        row1.addWidget(self.bin_type_combo)
        row1.addSpacing(20)
        row1.addWidget(QtWidgets.QLabel("Selected .bin:"))
        row1.addWidget(self.bin_path_edit)
        row1.addWidget(self.bin_browse_btn)

        row2 = QtWidgets.QHBoxLayout()
        self.range_combo = QtWidgets.QComboBox()
        self.bank_spin = QtWidgets.QSpinBox()
        self.bank_spin.setMinimum(0)
        self.bank_spin.setMaximum(9999)
        self.bank_spin.setValue(0)
        self.load_preview_btn = QtWidgets.QPushButton("Load Preview")

        row2.addWidget(QtWidgets.QLabel("Range:"))
        row2.addWidget(self.range_combo)
        row2.addSpacing(10)
        row2.addWidget(QtWidgets.QLabel("Bank:"))
        row2.addWidget(self.bank_spin)
        row2.addSpacing(10)
        row2.addWidget(self.load_preview_btn)
        row2.addStretch(1)

        # Hint text depending on bin type
        self.bin_hint_label = QtWidgets.QLabel("")
        self.bin_hint_label.setWordWrap(True)
        font = self.bin_hint_label.font()
        font.setPointSize(font.pointSize() - 1)
        self.bin_hint_label.setFont(font)

        top_vlayout.addLayout(row1)
        top_vlayout.addLayout(row2)
        top_vlayout.addWidget(self.bin_hint_label)

        main_layout.addWidget(top_box)

        # Middle: preview panel
        preview_box = QtWidgets.QGroupBox("Sprite Preview")
        preview_layout = QtWidgets.QVBoxLayout(preview_box)

        self.preview_scroll = QtWidgets.QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_container = QtWidgets.QWidget()
        self.preview_grid = QtWidgets.QGridLayout(self.preview_container)
        self.preview_grid.setContentsMargins(4, 4, 4, 4)
        self.preview_grid.setHorizontalSpacing(8)
        self.preview_grid.setVerticalSpacing(8)
        self.preview_scroll.setWidget(self.preview_container)

        preview_layout.addWidget(self.preview_scroll)

        main_layout.addWidget(preview_box, 1)

        # Bottom: export/import controls
        bottom_layout = QtWidgets.QHBoxLayout()
        main_layout.addLayout(bottom_layout)

        # Export controls
        export_box = QtWidgets.QGroupBox("Export Sprites")
        export_layout = QtWidgets.QVBoxLayout(export_box)
        self.export_btn = QtWidgets.QPushButton("Export Sprites (Current Range and Bank)")
        self.export_btn.setStyleSheet("background-color: #008000; color: white; font-weight: 600;font-size: 14pt;")
        self.export_all_btn = QtWidgets.QPushButton("Export All Sprites At Once?")
        self.export_all_btn.setStyleSheet("background-color: #960202; color: white; font-weight: 500;font-size: 14pt;")
        export_layout.addWidget(self.export_btn)
        export_layout.addWidget(self.export_all_btn)
        export_help = QtWidgets.QLabel(
            'Normal export uses the selected Range and Bank.\n'
            '"Export ALL Sprites At Once..." ignores Range/Bank and exports everything.'
        )
        export_help.setWordWrap(True)
        export_layout.addWidget(export_help)

        # Import controls
        import_box = QtWidgets.QGroupBox("Import Sprites")
        import_layout = QtWidgets.QVBoxLayout(import_box)

        path_layout = QtWidgets.QHBoxLayout()
        self.input_dir_edit = QtWidgets.QLineEdit()
        self.input_dir_browse = QtWidgets.QPushButton("Select Input Sprites Folder")
        path_layout.addWidget(self.input_dir_edit)
        path_layout.addWidget(self.input_dir_browse)

        self.update_palette_btn = QtWidgets.QPushButton("Update Palette from Input Folder")
        self.update_palette_btn.setStyleSheet("background-color: #0006b1; color: white; font-weight: 600;font-size: 14pt;")
        self.replace_sprites_btn = QtWidgets.QPushButton("Replace Sprites from Input Folder")
        self.replace_sprites_btn.setStyleSheet("background-color: #008000; color: white; font-weight: 600;font-size: 14pt;")

        import_layout.addLayout(path_layout)
        import_layout.addWidget(self.update_palette_btn)
        import_layout.addWidget(self.replace_sprites_btn)

        bottom_layout.addWidget(export_box)
        bottom_layout.addWidget(import_box)

        # Status label
        self.status_label = QtWidgets.QLabel("Ready.")
        self.status_label.setWordWrap(True)
        main_layout.addWidget(self.status_label)

        # connections
        self.bin_type_combo.currentIndexChanged.connect(self.on_bin_type_changed)
        self.bin_browse_btn.clicked.connect(self.on_select_bin_file)
        self.load_preview_btn.clicked.connect(self.on_load_preview_clicked)
        self.export_btn.clicked.connect(self.on_export_clicked)
        self.export_all_btn.clicked.connect(self.on_export_all_clicked)
        self.input_dir_browse.clicked.connect(self.on_select_input_dir)
        self.update_palette_btn.clicked.connect(self.on_update_palette_clicked)
        self.replace_sprites_btn.clicked.connect(self.on_replace_sprites_clicked)
        # Range change auto-preview
        self.range_combo.currentIndexChanged.connect(self.on_range_changed)

        # initialize range combo state
        self._update_range_combo()
        self._update_hint_label()

    # --- helpers ---

    def require_bin_selected(self) -> bool:
        if not self.current_bin_type_key:
            QtWidgets.QMessageBox.warning(self, "Type required", "Please select the type of .bin file first.")
            return False
        if not self.current_bin_path or not os.path.isfile(self.current_bin_path):
            QtWidgets.QMessageBox.warning(self, "BIN required", "Please select a valid .bin file.")
            return False
        return True

    def _make_ranges_for_current_type(self) -> List[Tuple[int, int]]:
        if not self.current_bin_type_key:
            return []
        max_idx = BIN_TYPES[self.current_bin_type_key]["max_sprite_index"]
        ranges: List[Tuple[int, int]] = []
        step = 50  # range size = 50
        start = 0
        while start <= max_idx:
            end = min(start + step - 1, max_idx)
            ranges.append((start, end))
            start += step
        return ranges

    def _update_range_combo(self):
        self.range_combo.blockSignals(True)
        self.range_combo.clear()
        self.range_list = []

        if not self.current_bin_type_key:
            self.range_combo.addItem("Select BIN type first")
            self.range_combo.setEnabled(False)
            self.load_preview_btn.setEnabled(False)
            self.bank_spin.setEnabled(False)
        else:
            self.range_combo.setEnabled(True)
            self.load_preview_btn.setEnabled(True)
            self.bank_spin.setEnabled(True)
            self.range_list = self._make_ranges_for_current_type()
            for (start, end) in self.range_list:
                self.range_combo.addItem(f"{start}-{end}", (start, end))
            if self.range_list:
                self.range_combo.setCurrentIndex(0)
        self.range_combo.blockSignals(False)

    def _update_hint_label(self):
        # Increase base font size using stylesheet instead of manual font operations
        self.bin_hint_label.setStyleSheet("font-size: 14pt; font-weight: bold;")

        if self.current_bin_type_key == "D-3":
            hint_text = (
                "<span style='color:red; font-weight:bold;font-size:14pt;'>RANGE HINT</span><br>"
                "items: 50-99, tamer: 100-299, partner small sprites: 299-500, "
                "partner big images: 500-699, friend small sprites: 699-1050, "
                "friend big images: 1050-1349, digimon attacks: 1349-1399."
            )
        elif self.current_bin_type_key == "Digivice":
            hint_text = (
                "<span style='color:red; font-weight:bold;font-size:14pt;'>RANGE HINT</span><br>"
                "items: 50-99, tamer: 100-249, partner small sprites: 249-450, "
                "partner big images: 450-649, friend small sprites: 649-999, "
                "friend big pictures: 999-1200, digimon attacks: 1200-1250."
            )
        else:
            hint_text = "<span style='color:red; font-weight:bold;font-size:14pt;'>RANGE HINT</span><br>Select type of .bin file."

        self.bin_hint_label.setText(hint_text)

    def _get_current_range(self) -> Tuple[int, int]:
        idx = self.range_combo.currentIndex()
        if idx < 0:
            return (0, 0)
        data = self.range_combo.itemData(idx)
        if not data:
            if self.range_list:
                return self.range_list[0]
            return (0, 0)
        return data

    def on_bin_type_changed(self, index: int):
        if index <= 0:
            self.current_bin_type_key = None
        else:
            self.current_bin_type_key = self.bin_type_combo.itemData(index)
        self._update_range_combo()
        self._update_hint_label()

    def on_select_bin_file(self):
        if not self.current_bin_type_key:
            QtWidgets.QMessageBox.warning(self, "Type required", "Please select the BIN type first.")
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select .bin file", "", "BIN files (*.bin);;All files (*)")
        if not path:
            return
        self.current_bin_path = path
        self.bin_path_edit.setText(path)
        self.status_label.setText("Loading sprites preview...")
        # Selecting .bin SHOULD auto export for selected range+bank and reload preview
        self.load_preview()

    def on_load_preview_clicked(self):
        if not self.require_bin_selected():
            return
        self.status_label.setText("Loading sprites preview...")
        self.load_preview()

    def on_range_changed(self, index: int):
        # Auto reload preview when range selection actually changes and a BIN is selected
        if not self.current_bin_path or not os.path.isfile(self.current_bin_path):
            return
        if not self.current_bin_type_key:
            return
        self.status_label.setText("Loading sprites preview for new range...")
        self.load_preview()

    def load_preview(self, start: Optional[int] = None, end: Optional[int] = None, bank: Optional[int] = None):
        if not self.require_bin_selected():
            return

        if start is None or end is None or bank is None:
            start_idx, end_idx = self._get_current_range()
            start = start_idx
            end = end_idx
            bank = self.bank_spin.value()

        if start is None or end is None or bank is None:
            QtWidgets.QMessageBox.warning(self, "Range/bank required", "Please select a valid range and bank.")
            return

        # recreate preview dir next to BIN
        base_dir = os.path.dirname(self.current_bin_path)
        self.preview_dir = os.path.join(base_dir, "_preview_sprites")
        if os.path.isdir(self.preview_dir):
            shutil.rmtree(self.preview_dir, ignore_errors=True)
        os.makedirs(self.preview_dir, exist_ok=True)

        desc = "Preview export"
        dlg = ProgressDialog("Generating Sprite Preview", self)
        worker = SpriteExportWorker(
            bin_path=self.current_bin_path,
            out_dir=self.preview_dir,
            start=int(start),
            end=int(end) + 1,  # end is exclusive
            banks_str=f"{int(bank)}-{int(bank)}",
            desc=desc,
        )
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        worker.progress.connect(dlg.on_progress)
        worker.finished.connect(lambda ok, msg: self._on_preview_finished(ok, msg, dlg, thread))

        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

    def _on_preview_finished(self, ok: bool, msg: str, dlg: ProgressDialog, thread: QtCore.QThread):
        dlg.accept()
        thread.quit()
        thread.wait()
        self.status_label.setText(msg)
        if ok:
            self.populate_preview_grid()
        else:
            QtWidgets.QMessageBox.critical(self, "Preview error", msg)

    def populate_preview_grid(self):
        clear_layout(self.preview_grid)
        if not self.preview_dir or not os.path.isdir(self.preview_dir):
            return

        files = [f for f in os.listdir(self.preview_dir) if f.lower().endswith(".png")]

        def key_fn(fn: str):
            name = os.path.splitext(fn)[0]
            parts = name.split("_")
            try:
                idx = int(parts[0])
                si = int(parts[1]) if len(parts) > 1 else 0
                bank = int(parts[2]) if len(parts) > 2 else 0
            except Exception:
                idx = si = bank = 0
            return (idx, si, bank)

        files.sort(key=key_fn)

        max_cols = 6
        row = 0
        col = 0
        thumb_size = 72

        for fn in files:
            full = os.path.join(self.preview_dir, fn)
            pix = QtGui.QPixmap(full)
            if not pix.isNull():
                pix = pix.scaled(
                    thumb_size,
                    thumb_size,
                    QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                    QtCore.Qt.TransformationMode.SmoothTransformation,
                )
            tile = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(tile)
            v.setContentsMargins(2, 2, 2, 2)
            v.setSpacing(2)
            img_lbl = QtWidgets.QLabel()
            img_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            img_lbl.setPixmap(pix)
            text_lbl = QtWidgets.QLabel(fn)
            text_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            v.addWidget(img_lbl)
            v.addWidget(text_lbl)

            self.preview_grid.addWidget(tile, row, col)
            col += 1
            if col >= max_cols:
                col = 0
                row += 1

        self.status_label.setText(f"Loaded {len(files)} preview sprite images.")

    # --- export sprites ---

    def on_export_clicked(self):
        if not self.require_bin_selected():
            return

        start_idx, end_idx = self._get_current_range()
        bank = self.bank_spin.value()

        # Export to a safe, writeable location (Desktop)
        export_dir = os.path.join(os.path.expanduser("~"), "Desktop", "exported_sprites")
        os.makedirs(export_dir, exist_ok=True)

        desc = f"Export sprites ({start_idx}-{end_idx}, bank {bank})"
        dlg_prog = ProgressDialog(desc, self)
        worker = SpriteExportWorker(
            bin_path=self.current_bin_path,
            out_dir=export_dir,
            start=int(start_idx),
            end=int(end_idx) + 1,
            banks_str=f"{int(bank)}-{int(bank)}",
            desc=desc,
        )
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        worker.progress.connect(dlg_prog.on_progress)
        worker.finished.connect(lambda ok, msg: self._on_export_finished(ok, msg, dlg_prog, thread))

        thread.started.connect(worker.run)
        thread.start()
        dlg_prog.exec()

    def _on_export_finished(self, ok: bool, msg: str, dlg: ProgressDialog, thread: QtCore.QThread):
        dlg.accept()
        thread.quit()
        thread.wait()
        self.status_label.setText(msg)
        if ok:
            QtWidgets.QMessageBox.information(
                self,
                "Sprites exported",
                'Sprites were exported to the "exported_sprites" folder on your Desktop. Please check your Desktop folder.',
            )
        else:
            QtWidgets.QMessageBox.critical(self, "Export error", msg)

    def on_export_all_clicked(self):
        if not self.require_bin_selected():
            return

        res = QtWidgets.QMessageBox.warning(
            self,
            "Export ALL sprites?",
            (
                "This will export ALL sprites for ALL banks.\n\n"
                "This may take a long time and create thousands of PNG files.\n\n"
                "Are you sure you want to continue?"
            ),
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
        )
        if res != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        # Export to a safe, writeable location (Desktop)
        export_dir = os.path.join(os.path.expanduser("~"), "Desktop", "exported_sprites")
        os.makedirs(export_dir, exist_ok=True)

        desc = "Export ALL sprites"
        dlg_prog = ProgressDialog(desc, self)
        worker = SpriteExportWorker(
            bin_path=self.current_bin_path,
            out_dir=export_dir,
            start=0,
            end=None,           # all indices
            banks_str="0-0",   # full bank range
            desc=desc,
        )
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        worker.progress.connect(dlg_prog.on_progress)
        worker.finished.connect(lambda ok, msg: self._on_export_finished(ok, msg, dlg_prog, thread))

        thread.started.connect(worker.run)
        thread.start()
        dlg_prog.exec()

    # --- import / palette / replace ---

    def on_select_input_dir(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select Input Sprites Folder")
        if not d:
            return
        self.input_sprites_dir = d
        self.input_dir_edit.setText(d)

    def require_input_dir(self) -> bool:
        if not self.input_sprites_dir or not os.path.isdir(self.input_sprites_dir):
            QtWidgets.QMessageBox.warning(
                self,
                "Input folder required",
                "Please select a valid input sprites folder first.",
            )
            return False
        return True

    def on_update_palette_clicked(self):
        if not (self.require_bin_selected() and self.require_input_dir()):
            return

        out_path = self.current_bin_path  # in-place

        dlg_prog = ProgressDialog("Updating Palette", self)
        worker = PaletteWorker(
            bin_path=self.current_bin_path,
            input_dir=self.input_sprites_dir,
            out_path=out_path,
        )
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        worker.progress.connect(dlg_prog.on_progress)
        worker.finished.connect(lambda ok, msg: self._on_palette_finished(ok, msg, dlg_prog, thread))

        thread.started.connect(worker.run)
        thread.start()
        dlg_prog.exec()

    def _on_palette_finished(self, ok: bool, msg: str, dlg: ProgressDialog, thread: QtCore.QThread):
        dlg.accept()
        thread.quit()
        thread.wait()
        self.status_label.setText(msg)
        if ok:
            QtWidgets.QMessageBox.information(self, "Palette updated", msg)
            # NOTE: no auto-preview reload (as requested).
        else:
            QtWidgets.QMessageBox.critical(self, "Palette update error", msg)

    def on_replace_sprites_clicked(self):
        if not (self.require_bin_selected() and self.require_input_dir()):
            return

        script_path = os.path.join(SCRIPT_DIR, "replace_sprites.py")
        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(self, "Missing script", "replace_sprites.py not found next to this GUI.")
            return

        desc = "Replace sprites"
        dlg_prog = ProgressDialog(desc, self)
        worker = InternalScriptWorker(
            script_name="replace_sprites.py",
            script_args=[
                self.current_bin_path,
                "--input-dir", self.input_sprites_dir,
                "--out", self.current_bin_path,
            ],
            desc=desc,
        )
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        worker.progress.connect(dlg_prog.on_progress)
        worker.finished.connect(lambda ok, msg: self._on_replace_finished(ok, msg, dlg_prog, thread))

        thread.started.connect(worker.run)
        thread.start()
        dlg_prog.exec()

    def _on_replace_finished(self, ok: bool, msg: str, dlg: ProgressDialog, thread: QtCore.QThread):
        dlg.accept()
        thread.quit()
        thread.wait()
        self.status_label.setText(msg)
        if ok:
            QtWidgets.QMessageBox.information(self, "Sprites replaced", msg)
            # NOTE: no auto-preview reload (as requested).
        else:
            QtWidgets.QMessageBox.critical(self, "Replace sprites error", msg)


# ----------------- Digimon Stats tab -----------------

class DigimonStatsTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.current_bin_type_key: Optional[str] = None
        self.current_bin_path: Optional[str] = None
        self.replace_map_path: Optional[str] = os.path.join(SCRIPT_DIR, "replace_map.csv")

        # for in-app editing
        self.name_caps: List[int] = []
        self.dv_stage_values: List[str] = []
        self.original_names: List[str] = []   # names from extract, used as base
        self._last_forbidden_rows: List[int] = []

        self._build_ui()

    def _build_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)

        # BIN selection
        top_box = QtWidgets.QGroupBox("BIN Selection")
        top_layout = QtWidgets.QHBoxLayout(top_box)

        self.bin_type_combo = QtWidgets.QComboBox()
        self.bin_type_combo.addItem("Select BIN type...")
        for key, info in BIN_TYPES.items():
            self.bin_type_combo.addItem(info["label"], key)

        self.bin_path_edit = QtWidgets.QLineEdit()
        self.bin_path_edit.setReadOnly(True)
        self.bin_browse_btn = QtWidgets.QPushButton("Select .bin file...")

        top_layout.addWidget(QtWidgets.QLabel("Type of .bin file:"))
        top_layout.addWidget(self.bin_type_combo)
        top_layout.addSpacing(20)
        top_layout.addWidget(QtWidgets.QLabel("Selected .bin:"))
        top_layout.addWidget(self.bin_path_edit)
        top_layout.addWidget(self.bin_browse_btn)

        main_layout.addWidget(top_box)

        # Export/import controls
        io_box = QtWidgets.QGroupBox("Digimon Data (CSV & In-App Editing)")
        io_layout = QtWidgets.QGridLayout(io_box)

        # Default CSV path on Desktop (avoids read-only app bundle)
        default_csv = os.path.join(os.path.expanduser("~"), "Desktop", "data.csv")
        self.export_csv_edit = QtWidgets.QLineEdit(default_csv)

        self.export_btn = QtWidgets.QPushButton("Export data to CSV")
        self.export_btn.setStyleSheet("background-color: #0006b1; color: white; font-weight: 600;font-size: 14pt;")

        self.import_btn = QtWidgets.QPushButton("Import data from CSV")
        self.import_btn.setStyleSheet("background-color: #0006b1; color: white; font-weight: 600;font-size: 14pt;")

        self.load_table_btn = QtWidgets.QPushButton("Load Data into Table")
        self.load_table_btn.setStyleSheet("background-color: #008000; color: white; font-weight: 600;font-size: 14pt;")
        self.save_edits_btn = QtWidgets.QPushButton("Save Edits to BIN")
        self.save_edits_btn.setStyleSheet("background-color: #008000; color: white; font-weight: 600;font-size: 14pt;")
        self.save_edits_btn.setEnabled(False)

        io_layout.addWidget(QtWidgets.QLabel("Export CSV path:"), 0, 0)
        io_layout.addWidget(self.export_csv_edit, 0, 1)
        io_layout.addWidget(self.export_btn, 0, 2)

        io_layout.addWidget(self.load_table_btn, 2, 0)
        io_layout.addWidget(self.save_edits_btn, 2, 1)
        io_layout.addWidget(self.import_btn, 2, 2)

        main_layout.addWidget(io_box)

        # Table for in-app editing
        self.table = QtWidgets.QTableWidget()
        self.table.setColumnCount(0)
        self.table.setRowCount(0)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked |
                                   QtWidgets.QAbstractItemView.EditTrigger.SelectedClicked |
                                   QtWidgets.QAbstractItemView.EditTrigger.AnyKeyPressed)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)

        main_layout.addWidget(self.table, 1)

        self.status_label = QtWidgets.QLabel("Ready.")
        self.status_label.setWordWrap(True)
        main_layout.addWidget(self.status_label)

        # connections
        self.bin_type_combo.currentIndexChanged.connect(self.on_bin_type_changed)
        self.bin_browse_btn.clicked.connect(self.on_select_bin_file)
        self.export_btn.clicked.connect(self.on_export_clicked)
        self.import_btn.clicked.connect(self.on_import_clicked)
        self.load_table_btn.clicked.connect(self.on_load_table_clicked)
        self.save_edits_btn.clicked.connect(self.on_save_edits_clicked)

    def on_bin_type_changed(self, index: int):
        if index <= 0:
            self.current_bin_type_key = None
        else:
            self.current_bin_type_key = self.bin_type_combo.itemData(index)

    def on_select_bin_file(self):
        if not self.current_bin_type_key:
            QtWidgets.QMessageBox.warning(self, "Type required", "Please select the BIN type first.")
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select .bin file", "", "BIN files (*.bin);;All files (*)")
        if not path:
            return
        self.current_bin_path = path
        self.bin_path_edit.setText(path)
        # Auto-trigger "Load Data into Table"
        self.on_load_table_clicked()

    def require_all(self) -> bool:
        if not self.current_bin_type_key:
            QtWidgets.QMessageBox.warning(self, "Type required", "Please select the BIN type first.")
            return False
        if not self.current_bin_path or not os.path.isfile(self.current_bin_path):
            QtWidgets.QMessageBox.warning(self, "BIN required", "Please select a valid .bin file.")
            return False
        if not self.replace_map_path or not os.path.isfile(self.replace_map_path):
            QtWidgets.QMessageBox.warning(
                self,
                "replace_map.csv required",
                "Please select a valid replace_map.csv file.",
            )
            return False
        return True

    # --- CSV export/import with BusyDialog ---

    def _short_status(self, msg: str) -> str:
        if len(msg) <= 80:
            return msg
        return msg[:77] + "..."

    def on_export_clicked(self):
        if not self.require_all():
            return

        out_csv = self.export_csv_edit.text().strip()
        if not out_csv:
            QtWidgets.QMessageBox.warning(self, "CSV path required", "Please specify an export CSV path.")
            return

        if self.current_bin_type_key == "D-3":
            script = "export_d3_data.py"
        else:
            script = "export_digivice_data.py"

        script_path = os.path.join(SCRIPT_DIR, script)
        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(self, "Missing script", f"{script} not found next to this GUI.")
            return

        desc = "Export data to CSV"
        self.status_label.setText("Running export...")
        self.export_btn.setEnabled(False)
        self.import_btn.setEnabled(False)

        dlg = BusyDialog("Export data to CSV", "Please wait...\nThis may take a while (8 - 15 mins).", self)
        worker = InternalScriptWorker(
            script_name=script,
            script_args=[
                self.current_bin_path,
                self.replace_map_path,
                out_csv,
            ],
            desc=desc,
        )
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        worker.progress.connect(lambda frac, msg: self.status_label.setText(self._short_status(msg)))

        def done(ok: bool, msg: str):
            dlg.accept()
            thread.quit()
            thread.wait()
            self.export_btn.setEnabled(True)
            self.import_btn.setEnabled(True)
            self.status_label.setText(self._short_status(msg))
            if ok:
                QtWidgets.QMessageBox.information(
                    self,
                    "Data Exported",
                    "Data was exported to data.csv on your Desktop.\n"
                    "Please check your Desktop folder."
                )
            else:
                QtWidgets.QMessageBox.critical(self, "Export data to CSV error", msg)

        worker.finished.connect(done)

        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

    def on_import_clicked(self):
        if not self.require_all():
            return

        # Select CSV using file dialog
        in_csv, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select data.csv",
            "",
            "CSV files (*.csv);;All files (*)"
        )
        if not in_csv:
            return

        if not os.path.isfile(in_csv):
            QtWidgets.QMessageBox.warning(self, "Invalid CSV", "Selected CSV file does not exist.")
            return

        if self.current_bin_type_key == "D-3":
            script = "import_d3_data.py"
        else:
            script = "import_digivice_data.py"

        script_path = os.path.join(SCRIPT_DIR, script)
        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(self, "Missing script", f"{script} not found next to this GUI.")
            return

        desc = "Import data from CSV"
        self.status_label.setText("Running import...")
        self.export_btn.setEnabled(False)
        self.import_btn.setEnabled(False)

        dlg = BusyDialog("Import data from CSV", "Please wait...\nThis may take a while (8 - 15 mins).", self)
        worker = InternalScriptWorker(
            script_name=script,
            script_args=[
                self.current_bin_path,
                in_csv,
                self.replace_map_path,
                "--out", self.current_bin_path,
            ],
            desc=desc,
        )
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        worker.progress.connect(lambda frac, msg: self.status_label.setText(self._short_status(msg)))

        def done(ok: bool, msg: str):
            dlg.accept()
            thread.quit()
            thread.wait()
            self.export_btn.setEnabled(True)
            self.import_btn.setEnabled(True)
            self.status_label.setText(self._short_status(msg))
            if ok:
                QtWidgets.QMessageBox.information(self, "Import data from CSV", msg)
                self.on_load_table_clicked()
            else:
                QtWidgets.QMessageBox.critical(self, "Import data from CSV error", msg)

        worker.finished.connect(done)

        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

    # --- in-app editing: Load table with BusyDialog ---

    def on_load_table_clicked(self):
        if not self.require_all():
            return

        if self.current_bin_type_key == "D-3":
            script = "export_d3_data.py"
        else:
            script = "export_digivice_data.py"

        script_path = os.path.join(SCRIPT_DIR, script)
        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(self, "Missing script", f"{script} not found next to this GUI.")
            return

        tmp_dir = tempfile.mkdtemp(prefix="digimon_gui_")
        tmp_csv = os.path.join(tmp_dir, "digimon_tmp.csv")

        desc = "Extract Digimon stats to table"
        self.status_label.setText("Extracting Digimon stats to load into table...")

        dlg = BusyDialog("Load Data into Table", "Please wait...\nThis may take a while (8 - 15 mins).", self)
        worker = InternalScriptWorker(
            script_name=script,
            script_args=[
                self.current_bin_path,
                self.replace_map_path,
                tmp_csv,
            ],
            desc=desc,
        )
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        worker.progress.connect(lambda frac, msg: self.status_label.setText(self._short_status(msg)))

        def finished(ok: bool, msg: str, csv_path=tmp_csv, temp_dir=tmp_dir):
            dlg.accept()
            thread.quit()
            thread.wait()
            if ok:
                try:
                    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                        reader = csv.DictReader(f)
                        rows = list(reader)
                    self.populate_table_from_rows(rows)
                    self.save_edits_btn.setEnabled(True)
                    self.status_label.setText("Digimon stats loaded into table.")
                except Exception as e:
                    QtWidgets.QMessageBox.critical(self, "CSV error", f"Failed to read temp CSV: {e}")
                    self.status_label.setText("CSV read error.")
            else:
                self.status_label.setText(self._short_status(msg))
                QtWidgets.QMessageBox.critical(self, "Extract error", msg)
            shutil.rmtree(temp_dir, ignore_errors=True)

        worker.finished.connect(finished)

        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

    def populate_table_from_rows(self, rows: List[dict]):
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        # Hide string_index column (col 0)
        self.table.setColumnHidden(0, True)
        self.name_caps = []
        self.dv_stage_values = []
        self.original_names = []
        self._last_forbidden_rows = []

        if not rows:
            return

        if self.current_bin_type_key == "D-3":
            headers = ["string_index", "DigimonName", "Stage", "Power", "Unknown1", "Unknown2"]
            pretty = ["string_index", "Name", "Stage", "Power", "Unknown1", "Unknown2"]
            self.table.setColumnCount(len(headers))
            self.table.setHorizontalHeaderLabels(pretty)

            self.table.setRowCount(len(rows))
            for r_idx, row in enumerate(rows):
                name = row.get("DigimonName", "")
                self.name_caps.append(len(name))
                self.original_names.append(name)

                for c_idx, key in enumerate(headers):
                    val = str(row.get(key, ""))
                    item = QtWidgets.QTableWidgetItem(val)
                    if key == "string_index":
                        item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                    self.table.setItem(r_idx, c_idx, item)

            # Hide Unknown1 and Unknown2 columns
            self.table.setColumnHidden(4, True)
            self.table.setColumnHidden(5, True)

        else:  # Digivice
            headers_all = ["string_index", "DigimonName", "Stage", "Power", "SlotIndex", "OffsetHex"]
            display_headers = ["string_index", "DigimonName", "Power", "SlotIndex", "OffsetHex"]
            pretty = ["string_index", "Name", "Power", "SlotIndex", "OffsetHex"]
            self.table.setColumnCount(len(display_headers))
            self.table.setHorizontalHeaderLabels(pretty)

            self.table.setRowCount(len(rows))
            for r_idx, row in enumerate(rows):
                name = row.get("DigimonName", "")
                self.name_caps.append(len(name))
                self.original_names.append(name)
                self.dv_stage_values.append(row.get("Stage", "0"))

                for c_idx, key in enumerate(display_headers):
                    val = str(row.get(key, ""))
                    item = QtWidgets.QTableWidgetItem(val)
                    if key in ("string_index", "SlotIndex", "OffsetHex"):
                        item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                    self.table.setItem(r_idx, c_idx, item)

            # Hide SlotIndex and OffsetHex columns for Digivice
            # display_headers index: 0=string_index,1=Name,2=Power,3=SlotIndex,4=OffsetHex
            self.table.setColumnHidden(3, True)
            self.table.setColumnHidden(4, True)

        self.table.resizeColumnsToContents()

    def on_save_edits_clicked(self):
        if not self.require_all():
            return
        if self.table.rowCount() == 0:
            QtWidgets.QMessageBox.information(self, "No data", "There is no table data to save.")
            return

        # Name rule:
        # - If forbidden(new): don't update name (use old), stats update.
        # - If len(new) > len(old): don't update name (use old), stats update.
        # - If len(new) < len(old): pad with '_' to match old length.
        # - If len(new) == len(old): use new as-is.

        def has_forbidden(s: str) -> bool:
            return any(c in FORBIDDEN_CHARS for c in s)

        self._last_forbidden_rows = []
        rows_out: List[dict] = []

        if self.current_bin_type_key == "D-3":
            idx_col = 0
            name_col = 1
            stage_col = 2
            power_col = 3
            u1_col = 4
            u2_col = 5

            for r in range(self.table.rowCount()):
                item_name = self.table.item(r, name_col)
                new_name = item_name.text() if item_name is not None else ""
                old_name = self.original_names[r] if r < len(self.original_names) else new_name

                # forbidden check
                if has_forbidden(new_name):
                    name_to_write = old_name
                    self._last_forbidden_rows.append(r + 1)
                else:
                    L_old = len(old_name)
                    L_new = len(new_name)
                    if L_new > L_old:
                        name_to_write = old_name  # don't update name
                    elif L_new < L_old:
                        name_to_write = new_name + ("_" * (L_old - L_new))
                    else:
                        name_to_write = new_name

                row_dict = {
                    "string_index": self.table.item(r, idx_col).text() if self.table.item(r, idx_col) else "",
                    "DigimonName": name_to_write,
                    "Stage": self.table.item(r, stage_col).text() if self.table.item(r, stage_col) else "",
                    "Power": self.table.item(r, power_col).text() if self.table.item(r, power_col) else "",
                    "Unknown1": self.table.item(r, u1_col).text() if self.table.item(r, u1_col) else "",
                    "Unknown2": self.table.item(r, u2_col).text() if self.table.item(r, u2_col) else "",
                }
                rows_out.append(row_dict)

        else:  # Digivice
            idx_col = 0
            name_col = 1
            power_col = 2
            slot_col = 3
            off_col = 4

            for r in range(self.table.rowCount()):
                new_name = self.table.item(r, name_col).text() if self.table.item(r, name_col) else ""
                old_name = self.original_names[r] if r < len(self.original_names) else new_name

                # Forbidden check
                if any(c in FORBIDDEN_CHARS for c in new_name):
                    name_to_write = old_name
                    self._last_forbidden_rows.append(r + 1)
                else:
                    if len(new_name) > len(old_name):
                        name_to_write = old_name
                    elif len(new_name) < len(old_name):
                        name_to_write = new_name + "_" * (len(old_name) - len(new_name))
                    else:
                        name_to_write = new_name

                stage_val = self.dv_stage_values[r] if r < len(self.dv_stage_values) else "0"

                rows_out.append({
                    "string_index": self.table.item(r, idx_col).text() if self.table.item(r, idx_col) else "",
                    "DigimonName": name_to_write,
                    "Stage": stage_val,
                    "Power": self.table.item(r, power_col).text() if self.table.item(r, power_col) else "",
                    "SlotIndex": self.table.item(r, slot_col).text() if self.table.item(r, slot_col) else "",
                    "OffsetHex": self.table.item(r, off_col).text() if self.table.item(r, off_col) else "",
                })

        # Write temp CSV for import script
        tmp_dir = tempfile.mkdtemp(prefix="digimon_gui_save_")
        tmp_csv = os.path.join(tmp_dir, "digimon_edit.csv")

        if self.current_bin_type_key == "D-3":
            fieldnames = ["string_index", "DigimonName", "Stage", "Power", "Unknown1", "Unknown2"]
            script = "import_d3_data.py"
        else:
            fieldnames = ["string_index", "DigimonName", "Stage", "Power", "SlotIndex", "OffsetHex"]
            script = "import_digivice_data.py"

        try:
            with open(tmp_csv, "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fieldnames)
                w.writeheader()
                for row in rows_out:
                    w.writerow(row)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "CSV error", f"Failed to write temp CSV: {e}")
            self.status_label.setText("Save CSV error.")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        script_path = os.path.join(SCRIPT_DIR, script)
        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(self, "Missing script", f"{script} not found next to this GUI.")
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return

        self.status_label.setText("Applying edits to BIN...")
        self.save_edits_btn.setEnabled(False)

        dlg = BusyDialog("Save Edits to BIN", "Please wait...\nThis may take a while (8 - 15 mins).", self)
        worker = InternalScriptWorker(
            script_name=script,
            script_args=[
                self.current_bin_path,
                tmp_csv,
                self.replace_map_path,
                "--out", self.current_bin_path,
            ],
            desc="Apply Data changes",
        )
        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        worker.progress.connect(lambda frac, msg: self.status_label.setText(self._short_status(msg)))

        def on_done(ok: bool, msg: str):
            dlg.accept()
            thread.quit()
            thread.wait()
            self.save_edits_btn.setEnabled(True)

            # Status line may mention names skipped rows (status only, not popup)
            if self._last_forbidden_rows:
                status_msg = (
                    msg
                    + " (Names skipped for rows: "
                    + ", ".join(str(r) for r in self._last_forbidden_rows)
                    + ")"
                )
            else:
                status_msg = msg

            self.status_label.setText(self._short_status(status_msg))

            # Popup should NOT include skipped-rows text
            if ok:
                QtWidgets.QMessageBox.information(self, "Data changes", msg)
                self.on_load_table_clicked()
            else:
                QtWidgets.QMessageBox.critical(self, "Data changes error", msg)

            shutil.rmtree(tmp_dir, ignore_errors=True)

        worker.finished.connect(on_done)

        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

# ----------------- NPC Names tab -----------------
class NPCNamesTab(DigimonStatsTab):
    """
    NPC Names tab — identical to DigimonStatsTab but uses:
      import_d3_npc_names.py
      export_d3_npc_names.py
      import_digivice_npc_names.py
      export_digivice_npc_names.py

    The CSV contains ONLY:
        string_index, name
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.replace_map_path = os.path.join(SCRIPT_DIR, "replace_map.csv")
        self.status_label.setText("NPC Names Ready.")

        # Change tab labels
        self.export_btn.setText("Export NPC Names to CSV")
        self.import_btn.setText("Import NPC Names from CSV")
        self.load_table_btn.setText("Load NPC Names into Table")
        self.save_edits_btn.setText("Save NPC Name Edits to BIN")

        # CSV default
        default_csv = os.path.join(os.path.expanduser("~"), "Desktop", "npc_names.csv")
        self.export_csv_edit.setText(default_csv)

    # --------------------------
    # Override script selection
    # --------------------------

    def _get_export_script(self):
        return "export_d3_npc_names.py" if self.current_bin_type_key == "D-3" else "export_digivice_npc_names.py"

    def _get_import_script(self):
        return "import_d3_npc_names.py" if self.current_bin_type_key == "D-3" else "import_digivice_npc_names.py"

    # --------------------------
    # Override export
    # --------------------------

    def on_export_clicked(self):
        if not self.require_all():
            return

        out_csv = self.export_csv_edit.text().strip()
        if not out_csv:
            QtWidgets.QMessageBox.warning(self, "CSV path required", "Please specify an export CSV path.")
            return

        script = self._get_export_script()
        script_path = os.path.join(SCRIPT_DIR, script)

        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(self, "Missing script", f"{script} not found.")
            return

        # Run script
        dlg = BusyDialog("Export NPC Names", "Please wait...\nThis may take a while (8 - 15 mins).", self)
        worker = InternalScriptWorker(
            script_name=script,
            script_args=[self.current_bin_path, self.replace_map_path, out_csv],
            desc="Export NPC names"
        )

        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        worker.finished.connect(lambda ok, msg: self._export_done(ok, msg, dlg, thread))
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

    def _export_done(self, ok, msg, dlg, thread):
        dlg.accept()
        thread.quit()
        thread.wait()

        if ok:
            QtWidgets.QMessageBox.information(
                self,
                "NPC Names Exported",
                "NPC names exported to npc_names.csv on your Desktop."
            )
        else:
            QtWidgets.QMessageBox.critical(self, "NPC Export Error", msg)

    # --------------------------
    # Override import
    # --------------------------

    def on_import_clicked(self):
        if not self.require_all():
            return

        csv_path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select npc_names.csv", "", "CSV files (*.csv)"
        )
        if not csv_path:
            return

        script = self._get_import_script()

        dlg = BusyDialog("Import NPC Names", "Please wait...\nThis may take a while (8 - 15 mins).", self)
        worker = InternalScriptWorker(
            script_name=script,
            script_args=[self.current_bin_path, csv_path, self.replace_map_path, "--out", self.current_bin_path],
            desc="Import NPC names"
        )

        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        worker.finished.connect(lambda ok, msg: self._import_done(ok, msg, dlg, thread))
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

    def _import_done(self, ok, msg, dlg, thread):
        dlg.accept()
        thread.quit()
        thread.wait()
        if ok:
            QtWidgets.QMessageBox.information(self, "NPC Names Imported", msg)
            self.on_load_table_clicked()
        else:
            QtWidgets.QMessageBox.critical(self, "NPC Import Error", msg)

    # --------------------------
    # Override table loader
    # --------------------------

    def on_load_table_clicked(self):
        if not self.require_all():
            return

        script = self._get_export_script()
        tmp_dir = tempfile.mkdtemp(prefix="npc_gui_")
        tmp_csv = os.path.join(tmp_dir, "npc_tmp.csv")

        dlg = BusyDialog("Load NPC Names", "Please wait...\nThis may take a while (8 - 15 mins).", self)
        worker = InternalScriptWorker(
            script_name=script,
            script_args=[self.current_bin_path, self.replace_map_path, tmp_csv],
            desc="Extract NPC names"
        )

        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        def finished(ok, msg):
            dlg.accept()
            thread.quit()
            thread.wait()

            if ok:
                try:
                    with open(tmp_csv, "r", encoding="utf-8-sig") as f:
                        rows = list(csv.DictReader(f))
                    self.populate_table(rows)
                except Exception as e:
                    QtWidgets.QMessageBox.critical(self, "NPC CSV Error", str(e))
            else:
                QtWidgets.QMessageBox.critical(self, "NPC Load Error", msg)

            shutil.rmtree(tmp_dir, ignore_errors=True)

        worker.finished.connect(finished)
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

    # --------------------------
    # Populate the table
    # --------------------------

    def populate_table(self, rows):
        self.table.clear()
        self.table.setRowCount(len(rows))
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["string_index", "Name"])
        self.table.setColumnHidden(0, True)  # hide string_index

        self.original_names = []
        self.name_caps = []

        for r_idx, row in enumerate(rows):
            idx = row["string_index"]
            name = row["name"]

            self.original_names.append(name)
            self.name_caps.append(len(name))

            idx_item = QtWidgets.QTableWidgetItem(idx)
            idx_item.setFlags(idx_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)

            self.table.setItem(r_idx, 0, idx_item)
            self.table.setItem(r_idx, 1, QtWidgets.QTableWidgetItem(name))

        self.table.resizeColumnsToContents()
        self.save_edits_btn.setEnabled(True)

    # --------------------------
    # Save edits to BIN
    # --------------------------

    def on_save_edits_clicked(self):
        if not self.require_all():
            return

        rows_out = []
        self._last_forbidden_rows = []

        for r in range(self.table.rowCount()):
            idx = self.table.item(r, 0).text()
            new_name = self.table.item(r, 1).text()
            old_name = self.original_names[r]

            if any(c in FORBIDDEN_CHARS for c in new_name):
                name_to_write = old_name
                self._last_forbidden_rows.append(r + 1)
            else:
                if len(new_name) > len(old_name):
                    name_to_write = old_name
                elif len(new_name) < len(old_name):
                    name_to_write = new_name + "_" * (len(old_name) - len(new_name))
                else:
                    name_to_write = new_name

            rows_out.append({"string_index": idx, "name": name_to_write})

        # write temp CSV
        tmp_dir = tempfile.mkdtemp(prefix="npc_save_")
        tmp_csv = os.path.join(tmp_dir, "npc_edit.csv")

        with open(tmp_csv, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["string_index", "name"])
            w.writeheader()
            w.writerows(rows_out)

        script = self._get_import_script()

        dlg = BusyDialog("Save NPC Edits", "Please wait...\nThis may take a while (8 - 15 mins).", self)
        worker = InternalScriptWorker(
            script_name=script,
            script_args=[self.current_bin_path, tmp_csv, self.replace_map_path, "--out", self.current_bin_path],
            desc="Apply NPC changes"
        )

        thread = QtCore.QThread(self)
        worker.moveToThread(thread)

        def finish(ok, msg):
            dlg.accept()
            thread.quit()
            thread.wait()
            if ok:
                QtWidgets.QMessageBox.information(self, "NPC Edits Saved", msg)
                self.on_load_table_clicked()
            else:
                QtWidgets.QMessageBox.critical(self, "NPC Save Error", msg)

            shutil.rmtree(tmp_dir, ignore_errors=True)

        worker.finished.connect(finish)
        thread.started.connect(worker.run)
        thread.start()
        dlg.exec()

# ----------------- Sounds Tab -----------------

class SoundsTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.current_bin_type_key: Optional[str] = None
        self.current_bin_path: Optional[str] = None
        self.input_sounds_dir: Optional[str] = None

        # Will be set when BIN type is chosen
        self.sound_map_csv: Optional[str] = None

        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        # -------- BIN selection --------
        bin_box = QtWidgets.QGroupBox("BIN Selection")
        bin_layout = QtWidgets.QHBoxLayout(bin_box)

        self.bin_type_combo = QtWidgets.QComboBox()
        self.bin_type_combo.addItem("Select BIN type...")
        for key, info in BIN_TYPES.items():
            self.bin_type_combo.addItem(info["label"], key)

        self.bin_path_edit = QtWidgets.QLineEdit()
        self.bin_path_edit.setReadOnly(True)
        self.bin_browse_btn = QtWidgets.QPushButton("Select .bin file")

        bin_layout.addWidget(QtWidgets.QLabel("Type:"))
        bin_layout.addWidget(self.bin_type_combo)
        bin_layout.addSpacing(20)
        bin_layout.addWidget(QtWidgets.QLabel("BIN File:"))
        bin_layout.addWidget(self.bin_path_edit)
        bin_layout.addWidget(self.bin_browse_btn)

        layout.addWidget(bin_box)

        # -------- Input folder --------
        input_box = QtWidgets.QGroupBox("Import Sounds")
        input_layout = QtWidgets.QVBoxLayout(input_box)

        h = QtWidgets.QHBoxLayout()
        self.sounds_dir_edit = QtWidgets.QLineEdit()
        self.sounds_dir_edit.setReadOnly(True)
        self.sounds_dir_btn = QtWidgets.QPushButton("Select input_sounds folder")
        h.addWidget(self.sounds_dir_edit)
        h.addWidget(self.sounds_dir_btn)
        
        self.import_btn = QtWidgets.QPushButton("Import Sounds into BIN")
        self.import_btn.setStyleSheet("background-color: #008000; color: white; font-weight:600; font-size:14pt;")

        input_layout.addLayout(h)
        input_layout.addWidget(self.import_btn)

        layout.addWidget(input_box)

        # -------- Export box --------
        export_box = QtWidgets.QGroupBox("Export Sounds")
        export_layout = QtWidgets.QVBoxLayout(export_box)

        self.export_btn = QtWidgets.QPushButton("Export Sounds to Desktop/exported_sounds")
        self.export_btn.setStyleSheet("background-color: #0006b1; color: white; font-weight:600; font-size:14pt;")

        export_layout.addWidget(self.export_btn)
        layout.addWidget(export_box)

        # -------- Status --------
        self.status_label = QtWidgets.QLabel("Ready.")
        layout.addWidget(self.status_label)

        # -------- Connections --------
        self.bin_type_combo.currentIndexChanged.connect(self.on_type_changed)
        self.bin_browse_btn.clicked.connect(self.on_pick_bin)
        self.sounds_dir_btn.clicked.connect(self.on_pick_sounds_dir)
        self.import_btn.clicked.connect(self.on_import_sounds)
        self.export_btn.clicked.connect(self.on_export_sounds)

    # --------------------------------------------------------------
    # Validation
    # --------------------------------------------------------------

    def require_bin(self):
        if not self.current_bin_type_key:
            QtWidgets.QMessageBox.warning(self, "Missing type", "Please select BIN type first.")
            return False
        if not self.current_bin_path:
            QtWidgets.QMessageBox.warning(self, "Missing BIN", "Please select a .bin file.")
            return False
        return True

    def require_sounds(self):
        if not self.input_sounds_dir or not os.path.isdir(self.input_sounds_dir):
            QtWidgets.QMessageBox.warning(self, "Missing sounds folder", "Please select input_sounds folder first.")
            return False
        return True

    # --------------------------------------------------------------
    # Events
    # --------------------------------------------------------------

    def on_type_changed(self, index):
        if index <= 0:
            self.current_bin_type_key = None
            self.sound_map_csv = None
            self.status_label.setText("Please select a BIN type.")
            return

        self.current_bin_type_key = self.bin_type_combo.itemData(index)

        # Pick the correct sound map CSV depending on BIN type
        if self.current_bin_type_key == "D-3":
            self.sound_map_csv = os.path.join(SCRIPT_DIR, "d3_sound_map.csv")
        elif self.current_bin_type_key == "Digivice":
            self.sound_map_csv = os.path.join(SCRIPT_DIR, "digivice_sound_map.csv")
        else:
            self.sound_map_csv = None

        # Optional: small status hint
        if self.sound_map_csv and os.path.isfile(self.sound_map_csv):
            self.status_label.setText(f"Using sound map: {os.path.basename(self.sound_map_csv)}")
        else:
            self.status_label.setText("Sound map CSV not found for this BIN type.")

    def on_pick_bin(self):
        if not self.current_bin_type_key:
            QtWidgets.QMessageBox.warning(self, "Missing type", "Please select BIN type first.")
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Select .bin", "", "BIN files (*.bin)")
        if path:
            self.current_bin_path = path
            self.bin_path_edit.setText(path)

    def on_pick_sounds_dir(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Select input_sounds folder")
        if d:
            self.input_sounds_dir = d
            self.sounds_dir_edit.setText(d)

    # --------------------------------------------------------------
    # Import Sounds
    # --------------------------------------------------------------

    def on_import_sounds(self):
        if not (self.require_bin() and self.require_sounds()):
            return

        script = "import_sounds.py"

        dlg = BusyDialog("Importing Sounds", "Working...\nThis may take a while.", self)

        worker = InternalScriptWorker(
            script_name=script,
            script_args=[
                self.current_bin_path,
                self.current_bin_path,
                self.sound_map_csv,
                self.input_sounds_dir,
            ],
            desc="Sound import"
        )

        t = QtCore.QThread(self)
        worker.moveToThread(t)

        worker.finished.connect(lambda ok, msg: self._import_done(ok, msg, dlg, t))

        t.started.connect(worker.run)
        t.start()
        dlg.exec()

    def _import_done(self, ok, msg, dlg, thread):
        dlg.accept()
        thread.quit()
        thread.wait()
        self.status_label.setText(msg)
        if ok:
            QtWidgets.QMessageBox.information(self, "Sounds Imported", msg)
        else:
            QtWidgets.QMessageBox.critical(self, "Sound Import Error", msg)

    # --------------------------------------------------------------
    # Export Sounds
    # --------------------------------------------------------------

    def on_export_sounds(self):
        if not self.require_bin():
            return

        out_dir = os.path.join(os.path.expanduser("~"), "Desktop", "exported_sounds")
        os.makedirs(out_dir, exist_ok=True)

        script = "export_sounds.py"

        dlg = BusyDialog("Exporting Sounds", "Working...\nThis may take a while.", self)

        worker = InternalScriptWorker(
            script_name=script,
            script_args=[
                self.current_bin_path,
                out_dir,
                self.sound_map_csv,
            ],
            desc="Sound export"
        )

        t = QtCore.QThread(self)
        worker.moveToThread(t)
        worker.finished.connect(lambda ok, msg: self._export_done(ok, msg, dlg, t))

        t.started.connect(worker.run)
        t.start()
        dlg.exec()

    def _export_done(self, ok, msg, dlg, thread):
        dlg.accept()
        thread.quit()
        thread.wait()
        self.status_label.setText(msg)
        if ok:
            QtWidgets.QMessageBox.information(
                self, "Sounds Exported",
                'Sounds were exported to "exported_sounds" on your Desktop.'
            )
        else:
            QtWidgets.QMessageBox.critical(self, "Sound Export Error", msg)

# ----------------- Main Window + Dark Palette -----------------

def apply_dark_palette(app: QtWidgets.QApplication):
    app.setStyle("Fusion")
    palette = QtGui.QPalette()

    base_color = QtGui.QColor(45, 45, 45)
    alt_base = QtGui.QColor(60, 60, 60)
    text_color = QtGui.QColor(220, 220, 220)
    disabled_text = QtGui.QColor(127, 127, 127)
    highlight = QtGui.QColor(64, 128, 255)

    palette.setColor(QtGui.QPalette.ColorRole.Window, base_color)
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, text_color)
    palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor(30, 30, 30))
    palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, alt_base)
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, text_color)
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, text_color)
    palette.setColor(QtGui.QPalette.ColorRole.Text, text_color)
    palette.setColor(QtGui.QPalette.ColorRole.Button, alt_base)
    palette.setColor(QtGui.QPalette.ColorRole.ButtonText, text_color)
    palette.setColor(QtGui.QPalette.ColorRole.BrightText, QtGui.QColor(255, 0, 0))
    palette.setColor(QtGui.QPalette.ColorRole.Highlight, highlight)
    palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, QtGui.QColor(0, 0, 0))

    # Disabled
    palette.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.Text, disabled_text)
    palette.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.ButtonText, disabled_text)

    app.setPalette(palette)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Digimon BIN Tool (Sprites & Digimon Stats)")
        self.resize(1100, 700)

        tabs = QtWidgets.QTabWidget()
        self.sprites_tab = SpritesTab(self)
        self.partner_tab = DigimonStatsTab(self)
        self.npc_tab = NPCNamesTab(self)
        self.sounds_tab = SoundsTab(self)

        tabs.addTab(self.sprites_tab, "Sprites")
        tabs.addTab(self.partner_tab, "Digimon Stats")
        tabs.addTab(self.npc_tab, "NPC Names")
        tabs.addTab(self.sounds_tab, "Sounds")

        # show Sprites tab by default
        tabs.setCurrentIndex(0)

        self.setCentralWidget(tabs)


def main():
    app = QtWidgets.QApplication(sys.argv)
    apply_dark_palette(app)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
