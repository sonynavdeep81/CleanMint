"""
main.py — CleanMint entry point
"""

import sys
from pathlib import Path

# Ensure the project root is on the path
sys.path.insert(0, str(Path(__file__).parent))

from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow
from config.settings import settings


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("CleanMint")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("CleanMint")

    # High-DPI is handled automatically in PyQt6

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
