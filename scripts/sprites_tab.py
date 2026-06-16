from PyQt5 import QtCore, QtGui, QtWidgets
import os
import shutil
from typing import List, Tuple, Optional

from common import *

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
        self.bin_type_combo = NoWheelComboBox()
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
        self.range_combo = NoWheelComboBox()
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

        script_name = "replace_sprites.py"
        script_path = os.path.join(SCRIPT_DIR, script_name)

        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.critical(
                self,
                "Missing script",
                f"{script_name} not found in scripts folder."
            )
            return

        bin_info = BIN_TYPES.get(self.current_bin_type_key, {})
        package_offset = bin_info.get("sprite_package_offset")

        script_args = [
            self.current_bin_path,
            "--input-dir", self.input_sprites_dir,
            "--out", self.current_bin_path,
        ]

        if package_offset:
            script_args += ["--package-offset", package_offset]

        desc = "Replace sprites"
        dlg_prog = ProgressDialog(desc, self)

        worker = InternalScriptWorker(
            script_name=script_name,
            script_args=script_args,
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
