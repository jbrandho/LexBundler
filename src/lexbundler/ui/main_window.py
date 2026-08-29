"""LexBundler's main application window."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow, QVBoxLayout, QWidget


class MainWindow(QMainWindow):
    """Top-level window for the LexBundler desktop application."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("LexBundler")
        self.resize(900, 600)

        title = QLabel("LexBundler")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        description = QLabel("Build, align, and analyze linguistic corpora.")
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout()
        layout.addStretch()
        layout.addWidget(title)
        layout.addWidget(description)
        layout.addStretch()

        central_widget = QWidget()
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)

