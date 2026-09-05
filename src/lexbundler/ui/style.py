"""Central semantic palette and narrow stylesheet for LexBundler's UI."""

from dataclasses import dataclass

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QWidget


@dataclass(frozen=True, slots=True)
class SemanticColors:
    """Named visual roles; a future light theme can provide another mapping."""

    window_background: str
    sidebar_background: str
    workspace_background: str
    panel_background: str
    content_background: str
    content_alternate: str
    hover_background: str
    border: str
    primary_text: str
    secondary_text: str
    disabled_text: str
    accent: str
    accent_hover: str
    accent_text: str
    success: str
    success_hover: str
    warning: str


DARK_COLORS = SemanticColors(
    window_background="#20242a",
    sidebar_background="#252a31",
    workspace_background="#2b3038",
    panel_background="#323841",
    content_background="#1d2127",
    content_alternate="#22272e",
    hover_background="#39414c",
    border="#454d58",
    primary_text="#f2f4f7",
    secondary_text="#b9c0ca",
    disabled_text="#727a85",
    accent="#4f8fe8",
    accent_hover="#65a0f0",
    accent_text="#ffffff",
    success="#55b87a",
    success_hover="#62c988",
    warning="#d8a84e",
)


def _dark_palette(colors: SemanticColors) -> QPalette:
    palette = QPalette()
    active = QPalette.ColorGroup.Active
    inactive = QPalette.ColorGroup.Inactive
    disabled = QPalette.ColorGroup.Disabled
    roles = {
        QPalette.ColorRole.Window: colors.window_background,
        QPalette.ColorRole.WindowText: colors.primary_text,
        QPalette.ColorRole.Base: colors.content_background,
        QPalette.ColorRole.AlternateBase: colors.content_alternate,
        QPalette.ColorRole.Text: colors.primary_text,
        QPalette.ColorRole.Button: colors.panel_background,
        QPalette.ColorRole.ButtonText: colors.primary_text,
        QPalette.ColorRole.ToolTipBase: colors.panel_background,
        QPalette.ColorRole.ToolTipText: colors.primary_text,
        QPalette.ColorRole.Highlight: colors.accent,
        QPalette.ColorRole.HighlightedText: colors.accent_text,
        QPalette.ColorRole.Link: colors.accent,
        QPalette.ColorRole.Mid: colors.secondary_text,
        QPalette.ColorRole.Midlight: colors.border,
        QPalette.ColorRole.BrightText: colors.warning,
    }
    for group in (active, inactive):
        for role, value in roles.items():
            palette.setColor(group, role, QColor(value))
    for role, value in roles.items():
        palette.setColor(disabled, role, QColor(value))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text,
                 QPalette.ColorRole.ButtonText):
        palette.setColor(disabled, role, QColor(colors.disabled_text))
    return palette


def _stylesheet(c: SemanticColors) -> str:
    return f"""
QMainWindow {{ background: {c.window_background}; color: {c.primary_text}; }}
QWidget#corpusExplorerPane {{ background: {c.sidebar_background}; }}
QWidget#resourceWorkspace, QWidget#resourcePage, QWidget#resourceHeader,
QWidget#overviewTab, QWidget#transcriptTab,
QWidget#alignmentTab, QWidget#reviewTab, QWidget#assetsTab {{
    background: {c.workspace_background};
}}
QLabel {{ color: {c.primary_text}; background: transparent; }}
QLabel#explorerHeading, QLabel[sectionHeading="true"], QLabel[cardTitle="true"] {{
    color: {c.secondary_text}; font-size: 11px; font-weight: 600;
}}
QLabel[primaryValue="true"] {{ color: {c.primary_text}; font-size: 15px; font-weight: 600; }}
QLabel[muted="true"], QLabel#resourceBreadcrumb, QLabel#reviewTiming,
QLabel#proposedTiming, QLabel#approvedTiming {{ color: {c.secondary_text}; }}
QLabel[status="success"] {{ color: {c.success}; font-weight: 600; }}
QLabel[status="attention"] {{ color: {c.warning}; font-weight: 600; }}
QLabel#resourceTitle {{ color: {c.primary_text}; font-size: 20px; font-weight: 600; }}
QLabel#reviewText {{ color: {c.primary_text}; font-weight: 600; }}

QTreeView#corpusExplorerTree {{
    color: {c.primary_text}; background: transparent; border: 0; outline: 0;
    selection-background-color: {c.accent}; selection-color: {c.accent_text};
}}
QTreeView#corpusExplorerTree::item {{ min-height: 28px; padding: 2px 5px; }}
QTreeView#corpusExplorerTree::item:hover {{ background: {c.hover_background}; }}
QTreeView#corpusExplorerTree::item:selected {{
    background: {c.accent}; color: {c.accent_text};
}}
QFrame#explorerFooter {{ border-top: 1px solid {c.border}; }}

QTabWidget#resourceTabs::pane {{
    border: 0; border-top: 1px solid {c.border};
    background: {c.workspace_background}; top: -1px;
}}
QTabWidget#resourceTabs::tab-bar {{ alignment: left; left: 4px; }}
QTabWidget#resourceTabs QTabBar {{
    background: {c.workspace_background};
}}
QTabWidget#resourceTabs QTabBar::tab {{
    color: {c.secondary_text}; background: transparent;
    border: 0; border-bottom: 2px solid transparent;
    min-height: 24px; padding: 7px 15px 6px 15px; margin: 0;
}}
QTabWidget#resourceTabs QTabBar::tab:hover {{
    color: {c.primary_text}; background: {c.hover_background};
}}
QTabWidget#resourceTabs QTabBar::tab:selected {{
    color: {c.primary_text}; background: transparent;
    border-bottom: 2px solid {c.accent}; font-weight: 600;
}}

QFrame[card="true"], QFrame#reviewDetailFrame {{
    border: 1px solid {c.border}; border-radius: 6px;
    background: {c.panel_background};
}}
QFrame[choicePanel="true"] {{
    border: 1px solid {c.border}; border-radius: 5px;
    background: {c.panel_background};
}}
QDialog, QStackedWidget#addResourcePages {{
    color: {c.primary_text}; background: {c.workspace_background};
}}
QLineEdit {{
    color: {c.primary_text}; background: {c.content_alternate};
    border: 1px solid {c.border}; border-radius: 4px; padding: 5px 8px;
    selection-background-color: {c.accent}; selection-color: {c.accent_text};
}}
QRadioButton {{ color: {c.primary_text}; spacing: 7px; }}
QRadioButton:checked {{ color: {c.primary_text}; font-weight: 600; }}
QFrame#reviewListFrame {{
    border: 1px solid {c.border}; border-radius: 6px;
    background: {c.content_background};
}}
QScrollArea {{ background: {c.workspace_background}; border: 0; }}
QScrollArea > QWidget > QWidget {{ background: {c.workspace_background}; }}

QTableView, QTableWidget, QListView {{
    color: {c.primary_text}; background: {c.content_background};
    alternate-background-color: {c.content_alternate};
    border: 1px solid {c.border}; gridline-color: {c.border}; outline: 0;
    selection-background-color: {c.accent}; selection-color: {c.accent_text};
}}
QTableView::item:hover, QTableWidget::item:hover, QListView::item:hover {{
    background: {c.hover_background};
}}
QHeaderView::section {{
    color: {c.secondary_text}; background: {c.panel_background};
    border: 0; border-right: 1px solid {c.border};
    border-bottom: 1px solid {c.border}; padding: 5px 7px; font-weight: 600;
}}
QTableCornerButton::section {{ background: {c.panel_background}; border: 0; }}

QProgressBar {{
    min-height: 6px; max-height: 6px; border: 0;
    border-radius: 3px; background: {c.content_background};
}}
QProgressBar::chunk {{ background: {c.accent}; border-radius: 3px; }}

QPushButton {{
    color: {c.primary_text}; background: {c.panel_background};
    border: 1px solid {c.border}; border-radius: 4px; padding: 5px 11px;
}}
QPushButton:hover {{ background: {c.hover_background}; }}
QPushButton:pressed {{ background: {c.content_alternate}; }}
QPushButton:disabled {{ color: {c.disabled_text}; background: {c.workspace_background}; }}
QPushButton[primaryAction="true"] {{
    color: {c.accent_text}; background: {c.accent}; border-color: {c.accent};
    font-weight: 600; padding: 6px 16px;
}}
QPushButton[primaryAction="true"]:hover {{ background: {c.accent_hover}; }}
QPushButton[successAction="true"] {{
    color: {c.accent_text}; background: {c.success}; border-color: {c.success};
    font-weight: 600; padding: 6px 16px;
}}
QPushButton[successAction="true"]:hover {{ background: {c.success_hover}; }}
QPushButton[compact="true"] {{ padding: 3px 8px; }}

QComboBox {{
    color: {c.primary_text}; background: {c.content_alternate};
    border: 1px solid {c.border}; border-radius: 4px; padding: 4px 8px;
}}
QComboBox:disabled {{ color: {c.disabled_text}; }}
QSplitter::handle {{ background: {c.border}; }}
QStatusBar {{ color: {c.secondary_text}; background: {c.window_background}; }}
QMenuBar, QMenu {{ color: {c.primary_text}; background: {c.window_background}; }}
QMenu::item:selected {{ background: {c.accent}; color: {c.accent_text}; }}
"""


WORKBENCH_STYLESHEET = _stylesheet(DARK_COLORS)


def apply_workbench_style(widget: QWidget) -> None:
    """Apply LexBundler's centralized dark palette and presentation rules."""
    widget.setPalette(_dark_palette(DARK_COLORS))
    widget.setStyleSheet(WORKBENCH_STYLESHEET)


def emphasized_font(widget: QWidget, *, point_delta: int = 0) -> QFont:
    font = QFont(widget.font())
    font.setPointSize(max(1, font.pointSize() + point_delta))
    font.setWeight(QFont.Weight.DemiBold)
    return font
