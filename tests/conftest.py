"""Shared pytest fixtures for Qt tests."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapplication() -> QApplication:
    """Provide one headless QApplication for the test session."""
    app = QApplication.instance() or QApplication([])
    return app
