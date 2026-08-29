from PySide6.QtWidgets import QApplication

from lexbundler.ui.main_window import MainWindow


def test_main_window_can_be_constructed(qapplication: QApplication) -> None:
    window = MainWindow()

    assert window.windowTitle() == "LexBundler"
    assert window.centralWidget() is not None

    window.close()

