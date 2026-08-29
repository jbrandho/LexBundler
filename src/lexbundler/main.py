"""Application entry point for LexBundler."""

import sys
from collections.abc import Sequence

from PySide6.QtWidgets import QApplication

from lexbundler.ui.main_window import MainWindow


def main(argv: Sequence[str] | None = None) -> int:
    """Create and run the LexBundler Qt application."""
    app = QApplication(list(argv) if argv is not None else sys.argv)
    app.setApplicationName("LexBundler")
    window = MainWindow()
    window.show()
    return app.exec()
