#!/usr/bin/env python3
"""
Detach Shared Palettes tab for Digimon BIN Tool.

Allocation-aware safety used by this version:
- Palette banks are protected only while they belong to the current palette
  allocation. The next distinct ImageDef.palette_start_index marks the start of
  the next allocation.
- Within that allocation, banks 0..15 are protected using the same stepping as
  update_palette.py / replace_sprites.py:
      physical_word = palette_start_index * 4 + bank * colors_per_bank
- This protects real alternate banks such as image 1460 bank 1 at word 2516
  and image 1438 bank 1 at word 13564, without incorrectly reserving nonexistent
  banks far beyond their allocation.
- Any unused tail after the last valid bank and before the next palette start is
  considered genuinely free.
- A moved image copies its complete valid bank block before ImageDef is changed.
- If no allocation-aware safe destination exists, the BIN is not modified.
- Optional sacrifice mode lets the selected image deliberately reuse the physical
  palette allocation of another image. This is allowed only when the selected
  image fits completely inside that allocation, so corruption is limited to
  images sharing the sacrificed palette start and never spills into the next
  palette allocation.

Apply Target, Auto-Detach, and Use Sacrificed Image Palette write directly back
to the loaded BIN and then reload it from disk so both tables reflect the actual
saved file.
"""

import os
import struct
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

from PyQt5 import QtCore, QtWidgets

import update_palette as up


@dataclass
class PaletteInfo:
    image_index: int
    palette_start: int
    base_word: int

    # Current sprite-attribute usage only. This is what is shown as
    # Required Words / Physical Usage in the first table.
    required_words: int
    colors_per_bank: Tuple[int, ...]
    relative_intervals: Tuple[Tuple[int, int], ...]
    absolute_intervals: Tuple[Tuple[int, int], ...]

    # Strict safety reservation. This covers every in-range bank 0..15
    # that update_palette.py / replace_sprites.py can address for the image.
    protected_intervals: Tuple[Tuple[int, int], ...]
    protected_words: int

    # Physical allocation owned by this palette_start_index. It ends at the
    # next distinct palette_start_index (or the end of the palette table).
    allocation_end_word: int
    allocation_words: int

    sprite_count: int


def _merge_intervals(intervals: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    cleaned = sorted(
        (max(0, int(a)), max(0, int(b)))
        for a, b in intervals
        if b > a
    )
    if not cleaned:
        return []

    out = [list(cleaned[0])]
    for a, b in cleaned[1:]:
        if a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])

    return [(a, b) for a, b in out]


def _intervals_overlap(a0: int, a1: int, b0: int, b1: int) -> bool:
    return a0 < b1 and b0 < a1


def _fmt_ranges(intervals: Tuple[Tuple[int, int], ...]) -> str:
    if not intervals:
        return "-"

    parts = []
    for a, b in intervals:
        if b > a:
            parts.append(f"{a}-{b - 1}")

    return ", ".join(parts) if parts else "-"


class DetachSharedPalettesTab(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.bin_path: Optional[str] = None
        self.data: Optional[bytearray] = None

        self.pkg_off = 0
        self.offs = None
        self.images = []
        self.sprites = []
        self.num_palette_words = 0

        self.palette_infos: Dict[int, PaletteInfo] = {}
        self.same_start_map: Dict[int, List[int]] = {}

        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)

        intro = QtWidgets.QLabel(
            "Inspect palette assignments and detach images that currently share a palette. "
            "Alternate banks are protected only inside the physical allocation belonging to "
            "each palette start (up to the next distinct palette start). This protects real "
            "banks used by other images without treating nonexistent banks as occupied. "
            "You can also deliberately reuse another image's palette allocation when you "
            "accept corrupting that image. Changes are written directly to the loaded BIN."
        )
        intro.setWordWrap(True)
        root.addWidget(intro)

        file_row = QtWidgets.QHBoxLayout()

        self.bin_edit = QtWidgets.QLineEdit()
        self.bin_edit.setReadOnly(True)
        self.bin_edit.setPlaceholderText("Choose a D-3 or Digivice BIN...")

        self.load_btn = QtWidgets.QPushButton("Load BIN...")

        file_row.addWidget(QtWidgets.QLabel("BIN:"))
        file_row.addWidget(self.bin_edit, 1)
        file_row.addWidget(self.load_btn)

        root.addLayout(file_row)

        filter_row = QtWidgets.QHBoxLayout()

        self.filter_edit = QtWidgets.QLineEdit()
        self.filter_edit.setPlaceholderText(
            "Filter by image index, palette start, colors, shared image..."
        )

        self.shared_only = QtWidgets.QCheckBox("Only shared palette starts")
        self.summary_label = QtWidgets.QLabel("No BIN loaded.")

        filter_row.addWidget(QtWidgets.QLabel("Filter:"))
        filter_row.addWidget(self.filter_edit, 1)
        filter_row.addWidget(self.shared_only)
        filter_row.addWidget(self.summary_label)

        root.addLayout(filter_row)

        self.image_table = QtWidgets.QTableWidget()
        self.image_table.setColumnCount(7)
        self.image_table.setHorizontalHeaderLabels([
            "Image",
            "Palette Start",
            "Base Words",
            "Required Words",
            "Physical Usage",
            "Colors",
            "Shared Start With",
        ])
        self.image_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.image_table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.image_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.image_table.setAlternatingRowColors(True)
        self.image_table.verticalHeader().setVisible(False)
        self.image_table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.image_table, 3)

        controls_box = QtWidgets.QGroupBox("Change Selected Image Palette")
        controls = QtWidgets.QGridLayout(controls_box)

        self.selected_label = QtWidgets.QLabel("No image selected.")
        self.selected_label.setWordWrap(True)

        self.target_spin = QtWidgets.QSpinBox()
        self.target_spin.setRange(0, 65535)
        self.target_spin.setEnabled(False)

        self.first_safe_btn = QtWidgets.QPushButton("Use First Safe Range")
        self.first_safe_btn.setEnabled(False)

        self.apply_btn = QtWidgets.QPushButton("Apply Target")
        self.apply_btn.setEnabled(False)

        self.auto_btn = QtWidgets.QPushButton("Auto-Detach Selected Images")
        self.auto_btn.setEnabled(False)

        self.validation_label = QtWidgets.QLabel("")
        self.validation_label.setWordWrap(True)

        self.sacrifice_spin = QtWidgets.QSpinBox()
        self.sacrifice_spin.setRange(0, 65535)
        self.sacrifice_spin.setEnabled(False)

        self.sacrifice_btn = QtWidgets.QPushButton("Use Sacrificed Image Palette")
        self.sacrifice_btn.setEnabled(False)

        self.sacrifice_label = QtWidgets.QLabel("")
        self.sacrifice_label.setWordWrap(True)

        controls.addWidget(self.selected_label, 0, 0, 1, 5)
        controls.addWidget(QtWidgets.QLabel("New palette_start_index:"), 1, 0)
        controls.addWidget(self.target_spin, 1, 1)
        controls.addWidget(self.first_safe_btn, 1, 2)
        controls.addWidget(self.apply_btn, 1, 3)
        controls.addWidget(self.auto_btn, 1, 4)
        controls.addWidget(self.validation_label, 2, 0, 1, 5)

        controls.addWidget(QtWidgets.QLabel("Sacrifice image index:"), 3, 0)
        controls.addWidget(self.sacrifice_spin, 3, 1)
        controls.addWidget(self.sacrifice_btn, 3, 2, 1, 2)
        controls.addWidget(self.sacrifice_label, 4, 0, 1, 5)

        root.addWidget(controls_box)

        free_box = QtWidgets.QGroupBox("Safe Free Ranges for Selected Image")
        free_layout = QtWidgets.QVBoxLayout(free_box)

        free_help = QtWidgets.QLabel(
            "These ranges protect every valid bank 0–15 that fits inside each image's "
            "current palette allocation. Unused allocation tails can therefore appear as "
            "safe free space. Double-click a row to use its First Safe Palette Start. "
            "Palette Start values are ImageDef values; Base Word = Palette Start × 4."
        )
        free_help.setWordWrap(True)
        free_layout.addWidget(free_help)

        self.free_table = QtWidgets.QTableWidget()
        self.free_table.setColumnCount(5)
        self.free_table.setHorizontalHeaderLabels([
            "First Safe Palette Start",
            "Last Safe Palette Start",
            "Free Words",
            "Free Words Range",
            "Possible Aligned Starts",
        ])
        self.free_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.free_table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.free_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.free_table.verticalHeader().setVisible(False)
        self.free_table.horizontalHeader().setStretchLastSection(True)
        free_layout.addWidget(self.free_table)

        root.addWidget(free_box, 2)

        self.load_btn.clicked.connect(self.choose_bin)
        self.filter_edit.textChanged.connect(self.apply_filter)
        self.shared_only.toggled.connect(self.apply_filter)
        self.image_table.itemSelectionChanged.connect(self.on_image_selection_changed)
        self.target_spin.valueChanged.connect(self.validate_target)
        self.first_safe_btn.clicked.connect(self.use_first_safe)
        self.apply_btn.clicked.connect(self.apply_target)
        self.auto_btn.clicked.connect(self.auto_detach_selected)
        self.sacrifice_spin.valueChanged.connect(self.validate_sacrifice_target)
        self.sacrifice_btn.clicked.connect(self.use_sacrificed_image_palette)
        self.free_table.cellDoubleClicked.connect(self.use_free_range_row)

    # ------------------------------------------------------------------
    # Load / parse / refresh
    # ------------------------------------------------------------------
    def choose_bin(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open Digimon BIN",
            "",
            "BIN files (*.bin);;All files (*.*)",
        )
        if path:
            self.load_bin(path)

    def _parse_loaded_bytes(self, raw: bytes):
        data = bytearray(raw)
        pkg_off, block, offs = up.robust_scan(data)
        images, sprites, _palettes_off = up.parse(block, offs)

        self.data = data
        self.pkg_off = pkg_off
        self.offs = offs
        self.images = images
        self.sprites = sprites
        self.num_palette_words = (offs[3] - offs[2]) // 2

        if self.images:
            self.sacrifice_spin.setMaximum(len(self.images) - 1)

        self.rebuild_analysis()

    def load_bin(self, path: str):
        try:
            with open(path, "rb") as f:
                raw = f.read()

            self._parse_loaded_bytes(raw)
            self.bin_path = path
            self.bin_edit.setText(path)
            self.populate_image_table()

        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                "Load failed",
                f"Could not load palette information:\n\n{e}",
            )

    def _write_loaded_bin(self):
        """Atomically overwrite the currently loaded BIN."""
        if self.data is None or not self.bin_path:
            raise RuntimeError("No BIN is loaded.")

        tmp_path = self.bin_path + ".palette_tmp"

        try:
            with open(tmp_path, "wb") as f:
                f.write(self.data)
                f.flush()
                os.fsync(f.fileno())

            os.replace(tmp_path, self.bin_path)

        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def _reload_from_disk_and_refresh(self, reselect_images: Optional[List[int]] = None):
        """Reload the BIN that was actually written, then refresh both tables."""
        if not self.bin_path:
            raise RuntimeError("No BIN is loaded.")

        with open(self.bin_path, "rb") as f:
            raw = f.read()

        self._parse_loaded_bytes(raw)
        self.populate_image_table()

        if reselect_images:
            self._reselect_images(reselect_images)
        else:
            self.free_table.setRowCount(0)

        self.on_image_selection_changed()

    def rebuild_analysis(self):
        self.palette_infos = {}

        # Distinct palette starts define physical allocation boundaries.
        # Example from D3:
        #   image 1460 starts at word 2452, next palette starts at 2964.
        #   With 64 colors/bank, banks 0..7 fit in that 512-word allocation.
        #
        #   image 1438 starts at word 13548, next palette starts at 14060.
        #   With 16 colors/bank, banks 0..15 consume only 256 words, leaving
        #   13804..14059 as a genuinely unused 256-word tail.
        unique_starts = sorted({
            int(idef.palette_start_index)
            for idef in self.images
        })

        next_base_by_start: Dict[int, int] = {}
        for pos, palette_start in enumerate(unique_starts):
            if pos + 1 < len(unique_starts):
                next_base = unique_starts[pos + 1] * 4
            else:
                next_base = self.num_palette_words

            next_base_by_start[palette_start] = min(
                self.num_palette_words,
                max(0, next_base),
            )

        for image_index, idef in enumerate(self.images):
            start_sprite = idef.sprite_start_index

            if image_index + 1 < len(self.images):
                end_sprite = self.images[image_index + 1].sprite_start_index
            else:
                end_sprite = len(self.sprites)

            start_sprite = max(0, min(start_sprite, len(self.sprites)))
            end_sprite = max(start_sprite, min(end_sprite, len(self.sprites)))
            sprs = self.sprites[start_sprite:end_sprite]

            rel_intervals = []
            colors_set = set()

            for s in sprs:
                colors = 1 << int(s.bpp)
                bank = int(s.attr_bank)

                rel_start = bank * colors
                rel_end = rel_start + colors

                rel_intervals.append((rel_start, rel_end))
                colors_set.add(colors)

            rel_merged = tuple(_merge_intervals(rel_intervals))
            required_words = max((b for _a, b in rel_merged), default=0)

            palette_start = int(idef.palette_start_index)
            base_word = palette_start * 4

            abs_intervals = tuple(
                (base_word + a, base_word + b)
                for a, b in rel_merged
            )

            allocation_limit = next_base_by_start.get(
                palette_start,
                self.num_palette_words,
            )
            allocation_limit = max(
                base_word,
                min(self.num_palette_words, allocation_limit),
            )

            # ALLOCATION-AWARE PROTECTION:
            # Protect banks 0..15 only while they fit before the next distinct
            # palette_start_index. Do not allow a bank family to spill into the
            # next explicit palette allocation.
            protected = []

            for colors in colors_set:
                for bank in range(16):
                    a = base_word + bank * colors
                    b = a + colors

                    if a >= allocation_limit:
                        break

                    # If the allocation boundary cuts through a theoretical bank,
                    # protect the remaining words up to that boundary rather than
                    # treating them as free.
                    b = min(b, allocation_limit)

                    if b > a:
                        protected.append((a, b))

            protected_merged = tuple(_merge_intervals(protected))

            if protected_merged:
                protected_words = max(
                    b for _a, b in protected_merged
                ) - base_word
            else:
                protected_words = 0

            self.palette_infos[image_index] = PaletteInfo(
                image_index=image_index,
                palette_start=palette_start,
                base_word=base_word,
                required_words=required_words,
                colors_per_bank=tuple(sorted(colors_set)),
                relative_intervals=rel_merged,
                absolute_intervals=abs_intervals,
                protected_intervals=protected_merged,
                protected_words=protected_words,
                allocation_end_word=allocation_limit,
                allocation_words=max(0, allocation_limit - base_word),
                sprite_count=len(sprs),
            )

        self.same_start_map = {}
        for idx, info in self.palette_infos.items():
            self.same_start_map.setdefault(info.palette_start, []).append(idx)

        shared_rows = sum(
            1
            for _idx, info in self.palette_infos.items()
            if len(self.same_start_map.get(info.palette_start, [])) > 1
        )
        shared_groups = sum(
            1
            for members in self.same_start_map.values()
            if len(members) > 1
        )

        self.summary_label.setText(
            f"{len(self.images)} images | {self.num_palette_words} palette words | "
            f"{shared_groups} shared-start groups ({shared_rows} images) | "
            f"allocation-aware bank protection"
        )

    # ------------------------------------------------------------------
    # Tables
    # ------------------------------------------------------------------
    def populate_image_table(self):
        self.image_table.setSortingEnabled(False)
        self.image_table.setRowCount(len(self.palette_infos))

        for row, image_index in enumerate(sorted(self.palette_infos)):
            info = self.palette_infos[image_index]

            same = [
                x
                for x in self.same_start_map.get(info.palette_start, [])
                if x != image_index
            ]

            colors_text = (
                ",".join(str(x) for x in info.colors_per_bank)
                if info.colors_per_bank
                else "-"
            )

            values = [
                str(image_index),
                str(info.palette_start),
                str(info.base_word),
                str(info.required_words),
                _fmt_ranges(info.absolute_intervals),
                colors_text,
                ",".join(str(x) for x in same) if same else "-",
            ]

            for col, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setData(QtCore.Qt.UserRole, image_index)

                if col in (0, 1, 2, 3):
                    item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

                self.image_table.setItem(row, col, item)

        self.image_table.resizeColumnsToContents()
        self.image_table.setSortingEnabled(True)
        self.apply_filter()
        self.on_image_selection_changed()

    def apply_filter(self):
        needle = self.filter_edit.text().strip().lower()
        only_shared = self.shared_only.isChecked()

        for row in range(self.image_table.rowCount()):
            first = self.image_table.item(row, 0)
            if first is None:
                continue

            image_index = int(first.data(QtCore.Qt.UserRole))
            info = self.palette_infos.get(image_index)

            if info is None:
                self.image_table.setRowHidden(row, True)
                continue

            same = self.same_start_map.get(info.palette_start, [])
            if only_shared and len(same) <= 1:
                self.image_table.setRowHidden(row, True)
                continue

            if needle:
                row_text = " ".join(
                    self.image_table.item(row, c).text().lower()
                    for c in range(self.image_table.columnCount())
                    if self.image_table.item(row, c) is not None
                )
                self.image_table.setRowHidden(row, needle not in row_text)
            else:
                self.image_table.setRowHidden(row, False)

    def selected_image_indices(self) -> List[int]:
        indexes = self.image_table.selectionModel().selectedRows()
        out = []

        for model_index in indexes:
            item = self.image_table.item(model_index.row(), 0)
            if item is None:
                continue

            idx = int(item.data(QtCore.Qt.UserRole))
            if idx not in out:
                out.append(idx)

        return sorted(out)

    def on_image_selection_changed(self):
        selected = self.selected_image_indices()

        has_one = len(selected) == 1
        has_any = len(selected) >= 1 and self.data is not None

        self.target_spin.setEnabled(has_one)
        self.first_safe_btn.setEnabled(has_one)
        self.apply_btn.setEnabled(has_one)
        self.auto_btn.setEnabled(has_any)
        self.sacrifice_spin.setEnabled(has_one)
        self.sacrifice_btn.setEnabled(has_one)

        if not has_one:
            if selected:
                self.selected_label.setText(
                    f"{len(selected)} images selected. Use “Auto-Detach Selected Images” "
                    f"to detach only images for which a fully bank-safe destination exists."
                )
            else:
                self.selected_label.setText("No image selected.")

            self.validation_label.setText("")
            self.sacrifice_label.setText("")
            self.free_table.setRowCount(0)
            return

        idx = selected[0]
        info = self.palette_infos[idx]

        same = [
            x
            for x in self.same_start_map.get(info.palette_start, [])
            if x != idx
        ]

        colors_text = (
            ",".join(str(x) for x in info.colors_per_bank)
            if info.colors_per_bank
            else "-"
        )

        self.selected_label.setText(
            f"Image {idx}: palette_start_index={info.palette_start}, "
            f"base word={info.base_word}, required words={info.required_words}, "
            f"colors={colors_text}, protected allocation span={info.protected_words} words. "
            f"Shared start with: {same if same else 'none'}."
        )

        self.target_spin.blockSignals(True)
        self.target_spin.setValue(info.palette_start)
        self.target_spin.blockSignals(False)

        self.populate_free_ranges(idx)
        self.validate_target()
        self.validate_sacrifice_target()

    # ------------------------------------------------------------------
    # Strict occupancy / safe range calculations
    # ------------------------------------------------------------------
    def occupied_intervals(self) -> List[Tuple[int, int]]:
        """
        Return physical palette words that belong to valid bank storage.

        The selected image is intentionally not excluded: moving an image into
        one of its own existing alternate banks would still destroy that bank.
        """
        intervals = []

        for info in self.palette_infos.values():
            intervals.extend(info.protected_intervals)

        return _merge_intervals(intervals)

    def free_ranges_for(self, image_index: int) -> List[Tuple[int, int, int, int]]:
        """
        Returns:
            (
                free_start_word,
                free_end_word_exclusive,
                first_palette_start,
                last_palette_start,
            )

        The destination must be large enough for the selected image's complete
        valid bank block inside its current physical allocation, not merely the
        bank referenced by its current sprite attributes.
        """
        info = self.palette_infos[image_index]
        need = info.protected_words

        if need <= 0:
            return []

        occupied = self.occupied_intervals()

        free = []
        cursor = 0

        for a, b in occupied:
            if cursor < a:
                free.append((cursor, a))
            cursor = max(cursor, b)

        if cursor < self.num_palette_words:
            free.append((cursor, self.num_palette_words))

        safe = []

        for a, b in free:
            first_base = ((a + 3) // 4) * 4
            latest_base = b - need
            last_base = (latest_base // 4) * 4

            if first_base <= last_base:
                safe.append((a, b, first_base // 4, last_base // 4))

        return safe

    def populate_free_ranges(self, image_index: int):
        ranges = self.free_ranges_for(image_index)
        self.free_table.setRowCount(len(ranges))

        for row, (a, b, first_psi, last_psi) in enumerate(ranges):
            count = last_psi - first_psi + 1

            values = [
                str(first_psi),
                str(last_psi),
                str(b - a),
                f"{a}-{b - 1}",
                str(count),
            ]

            for col, value in enumerate(values):
                item = QtWidgets.QTableWidgetItem(value)
                item.setData(QtCore.Qt.UserRole, first_psi)

                if col in (0, 1, 2, 4):
                    item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

                self.free_table.setItem(row, col, item)

        self.free_table.resizeColumnsToContents()

    def conflicting_images(self, image_index: int, target_psi: int) -> List[int]:
        """
        Return every image whose valid allocated bank storage overlaps the
        destination block required by image_index.

        The selected image itself is intentionally included. A move into one of
        its own current alternate-bank areas is not safe.
        """
        info = self.palette_infos[image_index]

        start = int(target_psi) * 4
        end = start + info.protected_words

        conflicts = []

        for other_idx, other in self.palette_infos.items():
            hit = any(
                _intervals_overlap(start, end, a, b)
                for a, b in other.protected_intervals
            )

            if hit:
                conflicts.append(other_idx)

        return sorted(conflicts)

    def validate_target_value(self, image_index: int, target_psi: int) -> Tuple[bool, str]:
        info = self.palette_infos[image_index]

        if info.required_words <= 0 or info.protected_words <= 0:
            return False, "This image has no palette usage to move."

        if not (0 <= target_psi <= 65535):
            return False, "palette_start_index must be between 0 and 65535."

        if target_psi == info.palette_start:
            return True, "Current palette location (no change)."

        start = target_psi * 4
        end = start + info.protected_words

        if end > self.num_palette_words:
            return (
                False,
                f"Out of range: preserving this image's allocated bank block requires "
                f"words {start}-{end - 1}, but the palette table ends at word "
                f"{self.num_palette_words - 1}.",
            )

        conflicts = self.conflicting_images(image_index, target_psi)
        if conflicts:
            shown = ", ".join(str(x) for x in conflicts[:20])
            extra = "" if len(conflicts) <= 20 else f" … +{len(conflicts) - 20} more"

            return (
                False,
                f"Unsafe: the destination block {start}-{end - 1} overlaps "
                f"allocated bank storage of image(s): {shown}{extra}.",
            )

        return (
            True,
            f"Safe: image {image_index} can use palette_start_index {target_psi}. "
            f"Its complete allocated bank block will occupy words {start}-{end - 1}.",
        )

    def validate_target(self):
        selected = self.selected_image_indices()
        if len(selected) != 1:
            self.validation_label.setText("")
            return

        idx = selected[0]
        ok, message = self.validate_target_value(idx, self.target_spin.value())
        self.validation_label.setText(message)

        info = self.palette_infos[idx]
        self.apply_btn.setEnabled(
            ok and self.target_spin.value() != info.palette_start
        )

    # ------------------------------------------------------------------
    # Deliberate palette sacrifice
    # ------------------------------------------------------------------
    def sacrifice_target_details(
        self,
        source_image_index: int,
        sacrifice_image_index: int,
    ) -> Tuple[bool, str, List[int]]:
        """
        Validate intentionally reusing another image's palette allocation.

        This deliberately allows overlap with the sacrificed palette allocation,
        but never allows the selected image's copied palette block to extend past
        the next distinct palette_start_index.

        Returns:
            (ok, message, affected_images)

        affected_images contains every image sharing the sacrificed palette start,
        because all of them will see the overwritten palette data.
        """
        if source_image_index not in self.palette_infos:
            return False, "Selected image is invalid.", []

        if sacrifice_image_index not in self.palette_infos:
            return False, "Sacrifice image index is invalid.", []

        if source_image_index == sacrifice_image_index:
            return False, "The selected image cannot sacrifice itself.", []

        source = self.palette_infos[source_image_index]
        victim = self.palette_infos[sacrifice_image_index]

        if source.required_words <= 0 or source.protected_words <= 0:
            return False, "The selected image has no palette data to move.", []

        if source.palette_start == victim.palette_start:
            affected = sorted(self.same_start_map.get(victim.palette_start, []))
            return (
                False,
                f"Image {source_image_index} already uses palette_start_index "
                f"{victim.palette_start}, the same palette allocation as image "
                f"{sacrifice_image_index}.",
                affected,
            )

        needed = source.protected_words
        capacity = victim.allocation_words

        affected = sorted(self.same_start_map.get(victim.palette_start, []))

        if capacity <= 0:
            return (
                False,
                f"Image {sacrifice_image_index}'s palette allocation has no usable space.",
                affected,
            )

        if needed > capacity:
            return (
                False,
                f"Cannot use image {sacrifice_image_index}'s palette space: selected image "
                f"{source_image_index} needs {needed} words, but the sacrificed allocation "
                f"contains only {capacity} words "
                f"({victim.base_word}-{victim.allocation_end_word - 1}).",
                affected,
            )

        affected_text = ", ".join(str(x) for x in affected) if affected else str(sacrifice_image_index)

        return (
            True,
            f"Allowed with intentional corruption: image {source_image_index} will move to "
            f"palette_start_index {victim.palette_start} and overwrite words "
            f"{victim.base_word}-{victim.base_word + needed - 1}. "
            f"Image(s) whose colors may be corrupted: {affected_text}.",
            affected,
        )

    def validate_sacrifice_target(self):
        selected = self.selected_image_indices()

        if len(selected) != 1:
            self.sacrifice_label.setText("")
            self.sacrifice_btn.setEnabled(False)
            return

        source_idx = selected[0]
        victim_idx = self.sacrifice_spin.value()

        ok, message, _affected = self.sacrifice_target_details(
            source_idx,
            victim_idx,
        )

        self.sacrifice_label.setText(message)
        self.sacrifice_btn.setEnabled(ok)

    def use_sacrificed_image_palette(self):
        selected = self.selected_image_indices()
        if len(selected) != 1:
            return

        source_idx = selected[0]
        victim_idx = self.sacrifice_spin.value()

        ok, message, affected = self.sacrifice_target_details(
            source_idx,
            victim_idx,
        )

        if not ok:
            QtWidgets.QMessageBox.warning(
                self,
                "Cannot use sacrificed palette",
                message + "\n\nThe BIN was not modified.",
            )
            return

        source = self.palette_infos[source_idx]
        victim = self.palette_infos[victim_idx]

        affected_text = ", ".join(str(x) for x in affected)

        answer = QtWidgets.QMessageBox.warning(
            self,
            "Confirm palette sacrifice",
            f"You are intentionally allowing palette corruption.\n\n"
            f"Selected image: {source_idx}\n"
            f"Sacrifice image: {victim_idx}\n"
            f"Destination Palette Start: {victim.palette_start}\n"
            f"Destination allocation: {victim.base_word}-"
            f"{victim.allocation_end_word - 1} "
            f"({victim.allocation_words} words)\n"
            f"Words that will be overwritten: {victim.base_word}-"
            f"{victim.base_word + source.protected_words - 1}\n\n"
            f"Image(s) that may have their colors corrupted: {affected_text}\n\n"
            f"The selected image's old palette is not erased. Only its ImageDef will "
            f"be redirected to the sacrificed allocation after its palette data is copied.\n\n"
            f"Continue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )

        if answer != QtWidgets.QMessageBox.Yes:
            return

        before = bytes(self.data)

        try:
            old_psi, new_psi = self._move_image_palette_to_sacrificed_allocation(
                source_idx,
                victim_idx,
            )

            self._write_loaded_bin()
            self._reload_from_disk_and_refresh([source_idx])

            QtWidgets.QMessageBox.information(
                self,
                "Sacrificed palette applied",
                f"Image {source_idx} palette_start_index changed from "
                f"{old_psi} to {new_psi}.\n\n"
                f"The palette allocation belonging to image {victim_idx} was deliberately "
                f"overwritten. Affected image(s): {affected_text}.",
            )

        except Exception as e:
            try:
                self._parse_loaded_bytes(before)
                self.populate_image_table()
                self._reselect_images([source_idx])
            except Exception:
                pass

            QtWidgets.QMessageBox.critical(
                self,
                "Palette sacrifice failed",
                str(e),
            )

    def _move_image_palette_to_sacrificed_allocation(
        self,
        source_image_index: int,
        sacrifice_image_index: int,
    ) -> Tuple[int, int]:
        """
        Copy the selected image's valid palette block onto the sacrificed image's
        allocation, then redirect only the selected ImageDef to that palette start.

        The sacrificed image's ImageDef is deliberately left unchanged, so it
        continues to point at the now-overwritten palette and may display wrong
        colors as requested.
        """
        if self.data is None or self.offs is None:
            raise RuntimeError("No BIN is loaded.")

        ok, message, _affected = self.sacrifice_target_details(
            source_image_index,
            sacrifice_image_index,
        )
        if not ok:
            raise RuntimeError(message)

        source = self.palette_infos[source_image_index]
        victim = self.palette_infos[sacrifice_image_index]

        old_psi = source.palette_start
        new_psi = victim.palette_start

        old_base = source.base_word
        new_base = victim.base_word
        copy_words = source.protected_words

        if old_base < 0 or old_base + copy_words > self.num_palette_words:
            raise RuntimeError(
                f"Source palette block {old_base}-{old_base + copy_words - 1} "
                f"is outside the palette table."
            )

        if copy_words > victim.allocation_words:
            raise RuntimeError(
                f"Destination allocation is too small: {copy_words} words required, "
                f"{victim.allocation_words} available."
            )

        palettes_off = self.offs[2]
        img_defs_off = self.offs[0]

        src_byte = self.pkg_off + palettes_off + old_base * 2
        dst_byte = self.pkg_off + palettes_off + new_base * 2
        byte_count = copy_words * 2

        # Snapshot before writing in case source/destination happen to overlap.
        palette_snapshot = bytes(
            self.data[src_byte:src_byte + byte_count]
        )

        if len(palette_snapshot) != byte_count:
            raise RuntimeError(
                "Could not read the complete source palette block."
            )

        # Deliberately overwrite the victim's palette allocation.
        self.data[dst_byte:dst_byte + byte_count] = palette_snapshot

        # Redirect only the selected image.
        image_def_palette_field = (
            self.pkg_off
            + img_defs_off
            + source_image_index * 6
            + 4
        )

        self.data[
            image_def_palette_field:image_def_palette_field + 2
        ] = struct.pack("<H", new_psi)

        self.images[source_image_index].palette_start_index = new_psi

        return old_psi, new_psi

    # ------------------------------------------------------------------
    # Apply changes
    # ------------------------------------------------------------------
    def first_safe_palette_start(self, image_index: int) -> Optional[int]:
        for _a, _b, first_psi, _last_psi in self.free_ranges_for(image_index):
            ok, _message = self.validate_target_value(image_index, first_psi)
            if ok:
                return first_psi

        return None

    def use_first_safe(self):
        selected = self.selected_image_indices()
        if len(selected) != 1:
            return

        idx = selected[0]
        target = self.first_safe_palette_start(idx)

        if target is None:
            info = self.palette_infos[idx]

            QtWidgets.QMessageBox.warning(
                self,
                "No allocation-safe range",
                f"No allocation-safe destination exists for image {idx}.\n\n"
                f"This image needs {info.protected_words} contiguous palette words to "
                f"preserve all valid banks inside its current palette allocation without "
                f"overwriting another image's bank storage.\n\n"
                f"The BIN was not modified.",
            )
            return

        self.target_spin.setValue(target)

    def use_free_range_row(self, row: int, _column: int):
        item = self.free_table.item(row, 0)
        if item is None:
            return

        psi = item.data(QtCore.Qt.UserRole)
        if psi is not None:
            self.target_spin.setValue(int(psi))

    def apply_target(self):
        selected = self.selected_image_indices()
        if len(selected) != 1:
            return

        idx = selected[0]
        target = self.target_spin.value()

        ok, message = self.validate_target_value(idx, target)
        if not ok:
            QtWidgets.QMessageBox.warning(
                self,
                "Unsafe palette target",
                message + "\n\nThe BIN was not modified.",
            )
            return

        if target == self.palette_infos[idx].palette_start:
            return

        before = bytes(self.data)

        try:
            old, new = self._move_image_palette(idx, target)
            self._write_loaded_bin()
            self._reload_from_disk_and_refresh([idx])

            QtWidgets.QMessageBox.information(
                self,
                "Palette detached",
                f"Image {idx} palette_start_index changed from {old} to {new}.\n\n"
                f"The complete allocated bank block was copied to the new safe location, "
                f"and the loaded BIN was updated directly.",
            )

        except Exception as e:
            try:
                self._parse_loaded_bytes(before)
                self.populate_image_table()
                self._reselect_images([idx])
            except Exception:
                pass

            QtWidgets.QMessageBox.critical(
                self,
                "Palette move failed",
                str(e),
            )

    def auto_detach_selected(self):
        selected = self.selected_image_indices()
        if not selected:
            return

        before = bytes(self.data)

        moved = []
        already_private = []
        failed = []

        for idx in selected:
            info = self.palette_infos[idx]
            current_members = self.same_start_map.get(info.palette_start, [])

            if len(current_members) <= 1:
                already_private.append(idx)
                continue

            target = self.first_safe_palette_start(idx)

            if target is None:
                failed.append(
                    (
                        idx,
                        f"no allocation-safe range large enough "
                        f"({info.protected_words} words required)",
                    )
                )
                continue

            try:
                old, new = self._move_image_palette(idx, target)
                moved.append((idx, old, new))

                # Rebuild after every move so later selections see the new
                # protected bank reservation.
                self.rebuild_analysis()

            except Exception as e:
                failed.append((idx, str(e)))

        try:
            if moved:
                self._write_loaded_bin()

            # If nothing moved, this simply reloads the unchanged BIN.
            self._reload_from_disk_and_refresh(
                [x[0] for x in moved] or selected
            )

        except Exception as e:
            try:
                self._parse_loaded_bytes(before)
                self.populate_image_table()
                self._reselect_images(selected)
            except Exception:
                pass

            QtWidgets.QMessageBox.critical(
                self,
                "Auto-detach failed",
                str(e),
            )
            return

        lines = []

        if moved:
            lines.append("Moved and saved directly to the loaded BIN:")
            lines.extend(
                f"  Image {idx}: {old} -> {new}"
                for idx, old, new in moved
            )

        if already_private:
            if lines:
                lines.append("")

            lines.append(
                "Kept in place (already private after detaching the others):"
            )
            lines.extend(f"  Image {idx}" for idx in already_private)

        if failed:
            if lines:
                lines.append("")

            lines.append("Not moved — BIN data for these images was left unchanged:")
            lines.extend(
                f"  Image {idx}: {reason}"
                for idx, reason in failed
            )

        QtWidgets.QMessageBox.information(
            self,
            "Auto-detach complete",
            "\n".join(lines) if lines else "No images were changed.",
        )

    def _move_image_palette(self, image_index: int, target_psi: int) -> Tuple[int, int]:
        if self.data is None or self.offs is None:
            raise RuntimeError("No BIN is loaded.")

        ok, message = self.validate_target_value(image_index, target_psi)
        if not ok:
            raise RuntimeError(message)

        info = self.palette_infos[image_index]
        old_psi = info.palette_start

        if target_psi == old_psi:
            return old_psi, target_psi

        old_base = old_psi * 4
        new_base = target_psi * 4

        # Copy the entire valid bank block inside this palette allocation,
        # not only the bank currently referenced by sprite attributes.
        copy_words = info.protected_words

        if copy_words <= 0:
            raise RuntimeError("This image has no protected palette data to move.")

        if old_base < 0 or old_base + copy_words > self.num_palette_words:
            raise RuntimeError(
                f"Source bank block {old_base}-{old_base + copy_words - 1} "
                f"is outside the palette table."
            )

        if new_base < 0 or new_base + copy_words > self.num_palette_words:
            raise RuntimeError(
                f"Destination bank block {new_base}-{new_base + copy_words - 1} "
                f"is outside the palette table."
            )

        palettes_off = self.offs[2]
        img_defs_off = self.offs[0]

        src_byte = self.pkg_off + palettes_off + old_base * 2
        dst_byte = self.pkg_off + palettes_off + new_base * 2
        byte_count = copy_words * 2

        palette_snapshot = bytes(
            self.data[src_byte:src_byte + byte_count]
        )

        if len(palette_snapshot) != byte_count:
            raise RuntimeError(
                "Could not read the complete source bank block."
            )

        self.data[dst_byte:dst_byte + byte_count] = palette_snapshot

        image_def_palette_field = (
            self.pkg_off
            + img_defs_off
            + image_index * 6
            + 4
        )

        self.data[
            image_def_palette_field:image_def_palette_field + 2
        ] = struct.pack("<H", target_psi)

        self.images[image_index].palette_start_index = target_psi

        return old_psi, target_psi

    def _reselect_images(self, image_indices: List[int]):
        wanted = set(image_indices)
        self.image_table.clearSelection()

        for row in range(self.image_table.rowCount()):
            item = self.image_table.item(row, 0)
            if item is None:
                continue

            idx = int(item.data(QtCore.Qt.UserRole))
            if idx in wanted:
                self.image_table.selectRow(row)
