#!/usr/bin/env python3
import sys

from PyQt5 import QtCore, QtGui, QtWidgets

from common import *
from sprites_tab import SpritesTab
from link_battle_table_tab import LinkBattleTableTab
from partner_table_tab import PartnerTableTab
from friend_table_tab import FriendTableTab
from names_tab import NamesTab
from sounds_tab import SoundsTab
from reduce_color_count_tab import ReduceColorCountTab
from map_areas_tab import MapAreasTab

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
    app.setStyleSheet("""
        QScrollBar:vertical {
            background: #2b2b2b;
            width: 14px;
            margin: 0px;
        }
        QScrollBar::handle:vertical {
            background: #6ec6ff;        /* light blue */
            min-height: 24px;
            border-radius: 6px;
        }
        QScrollBar::handle:vertical:hover {
            background: #42a5f5;        /* slightly darker blue on hover */
        }
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {
            height: 0px;
        }

        QScrollBar:horizontal {
            background: #2b2b2b;
            height: 14px;
            margin: 0px;
        }
        QScrollBar::handle:horizontal {
            background: #6ec6ff;        /* light blue */
            min-width: 24px;
            border-radius: 6px;
        }
        QScrollBar::handle:horizontal:hover {
            background: #42a5f5;        /* darker on hover */
        }
        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal {
            width: 0px;
        }
        """)

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Digimon BIN Tool (Sprites & Digimon Stats)")
        self.resize(1100, 700)

        tabs = QtWidgets.QTabWidget()
        self.sprites_tab = SpritesTab(self)
        self.reduce_color_tab = ReduceColorCountTab(self)
        self.names_tab = NamesTab(self)
        self.partner_tab = PartnerTableTab(self)
        self.friend_tab = FriendTableTab(self)
        self.link_battle_tab = LinkBattleTableTab(self)
        self.map_areas_tab = MapAreasTab(self)
        self.sounds_tab = SoundsTab(self)

        tabs.addTab(self.sprites_tab, "Sprites")
        tabs.addTab(self.reduce_color_tab, "Reduce Color Count")
        tabs.addTab(self.names_tab, "Names")
        tabs.addTab(self.partner_tab, "Partner Table")
        tabs.addTab(self.friend_tab, "Friend Table")
        tabs.addTab(self.link_battle_tab, "Link Battle Table")
        tabs.addTab(self.map_areas_tab, "Map Areas")
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
