"""Application entry point for LexBundler."""

import sys
from collections.abc import Sequence

from PySide6.QtWidgets import QApplication

from lexbundler.application.project_service import ProjectService
from lexbundler.persistence.sqlite import SQLiteProjectStoreFactory
from lexbundler.ui.main_window import MainWindow


def main(argv: Sequence[str] | None = None) -> int:
    """Create and run the LexBundler Qt application."""
    app = QApplication(list(argv) if argv is not None else sys.argv)
    app.setApplicationName("LexBundler")
    project_service = ProjectService(SQLiteProjectStoreFactory())
    window = MainWindow(project_service)
    window.show()
    return app.exec()
