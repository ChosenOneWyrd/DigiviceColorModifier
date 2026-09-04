from PyQt5 import QtCore, QtGui, QtWidgets
import sys
import os
import time
import runpy
from typing import List, Tuple, Optional
import export_sprites as es
import update_palette as up

BIN_TYPES = {
    "D-3": {
        "max_sprite_index": 2115,
        "label": "D-3 25th Color Evolution",
        "sprite_package_offset": "0x1EF000",
    },
    "Digivice": {
        "max_sprite_index": 1578,
        "label": "Digivice 25th Color Evolution",
        "sprite_package_offset": "0x196000",
    },
    "D-ark": {
        "max_sprite_index": 2064,
        "label": "D-ark 25th Color Evolution",
        "sprite_package_offset": "0x280000",
    },
}

FORBIDDEN_CHARS = set("+-:<>?!~`'\"[]{}\\|@#$%^&*=,")

# --- PyInstaller/resource paths ---
if getattr(sys, "frozen", False):
    APP_DIR = sys._MEIPASS
    SCRIPT_DIR = os.path.join(APP_DIR, "scripts")
else:
    SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
    APP_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))

if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)


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
        old_cwd = os.getcwd()

        try:
            script_path = os.path.join(SCRIPT_DIR, self.script_name)

            sys.argv = [script_path] + self.script_args

            self.progress.emit(0.0, f"Running internal script: {self.script_name}")

            # Important: make relative CSV paths like d3_names_original.csv work
            os.chdir(SCRIPT_DIR)

            runpy.run_path(script_path, run_name="__main__")

            self.progress.emit(1.0, f"{self.desc} finished.")
            self.finished.emit(True, f"{self.desc} completed successfully.")

        except Exception as e:
            self.finished.emit(False, f"{self.desc} failed: {e}")

        finally:
            os.chdir(old_cwd)
            sys.argv = old_argv

class NoWheelComboBox(QtWidgets.QComboBox):
    def wheelEvent(self, event):
        if self.view().isVisible():
            super().wheelEvent(event)
        else:
            event.ignore()

class BusyDialog(QtWidgets.QDialog):
    """Spinner-style dialog for operations without reliable progress (digimon stats)."""
    def __init__(self, title: str, message: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)

        layout = QtWidgets.QVBoxLayout(self)

        # --- NEW: kindness.gif animation ---
        gif_path = os.path.join(APP_DIR, "kindness.gif")
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

class ProgressDialog(QtWidgets.QDialog):
    """Used where we *do* know real progress (sprites export)."""
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)

        self._start_time = time.monotonic()

        layout = QtWidgets.QVBoxLayout(self)

        # --- NEW: kindness.gif animation ---
        gif_path = os.path.join(APP_DIR, "kindness.gif")
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