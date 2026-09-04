"""Qt Model/View pane for navigating the project source hierarchy."""

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QLabel, QPushButton, QTreeView, QVBoxLayout, QWidget,
)

from lexbundler.application.project_explorer_service import (
    ExplorerNode, ExplorerTree, ResourceIdentity,
)


class _TreeItem:
    def __init__(self, node: ExplorerNode, parent: "_TreeItem | None" = None) -> None:
        self.node = node
        self.parent = parent
        self.children = tuple(_TreeItem(child, self) for child in node.children)

    def row(self) -> int:
        return 0 if self.parent is None else self.parent.children.index(self)


class CorpusExplorerModel(QAbstractItemModel):
    ResourceRole = Qt.ItemDataRole.UserRole + 1
    NodeKeyRole = Qt.ItemDataRole.UserRole + 2
    IsResourceRole = Qt.ItemDataRole.UserRole + 3

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._root: _TreeItem | None = None

    def replace(self, tree: ExplorerTree | None) -> None:
        self.beginResetModel()
        self._root = _TreeItem(tree.project) if tree else None
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        item = self._item(parent)
        return len(item.children) if item else 0

    def columnCount(self, _parent=QModelIndex()) -> int:  # noqa: N802
        return 1

    def index(self, row, column, parent=QModelIndex()):
        item = self._item(parent)
        if item is None or column != 0 or not 0 <= row < len(item.children):
            return QModelIndex()
        return self.createIndex(row, column, item.children[row])

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        parent = index.internalPointer().parent
        if parent is None:
            return QModelIndex()
        return self.createIndex(parent.row(), 0, parent)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        node = index.internalPointer().node
        if role == Qt.ItemDataRole.DisplayRole:
            return node.label
        if role == self.ResourceRole:
            return node.resource
        if role == self.NodeKeyRole:
            return node.key
        if role == self.IsResourceRole:
            return node.is_resource
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def root_index(self) -> QModelIndex:
        return self.index(0, 0)

    def first_resource_index(self) -> QModelIndex:
        def visit(parent=QModelIndex()):
            for row in range(self.rowCount(parent)):
                index = self.index(row, 0, parent)
                if index.data(self.IsResourceRole):
                    return index
                found = visit(index)
                if found.isValid():
                    return found
            return QModelIndex()

        return visit()

    def _item(self, index: QModelIndex) -> _TreeItem | None:
        if index.isValid():
            return index.internalPointer()
        if self._root is None:
            return None
        # The invisible Qt root exposes the project node as its one child.
        return _InvisibleRoot(self._root)


class _InvisibleRoot:
    def __init__(self, project: _TreeItem) -> None:
        self.children = (project,)


class CorpusExplorerPane(QWidget):
    resourceSelected = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("corpusExplorerPane")
        self.setMinimumWidth(210)
        self.model = CorpusExplorerModel(self)
        self.tree = QTreeView(objectName="corpusExplorerTree")
        self.tree.setModel(self.model)
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(17)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(False)
        self.empty_label = QLabel(objectName="explorerEmptyState")
        self.empty_label.setWordWrap(True)
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.empty_label.setProperty("muted", True)
        self.add_button = QPushButton("Add Resource", objectName="addResourceButton")
        self.add_button.setEnabled(False)
        self.add_button.setToolTip("Resource import will be added in a future milestone.")

        heading = QLabel("CORPUS", objectName="explorerHeading")
        footer = QFrame(objectName="explorerFooter")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(12, 12, 12, 12)
        footer_layout.addWidget(self.add_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 14, 8, 0)
        layout.setSpacing(8)
        layout.addWidget(heading)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.empty_label, 1)
        layout.addWidget(footer)
        self.tree.selectionModel().currentChanged.connect(self._selection_changed)
        self.clear()

    def populate(self, tree: ExplorerTree) -> None:
        self.model.replace(tree)
        empty = tree.resource_count == 0
        self.empty_label.setText(
            "No resources yet.\n\nAdd your first audio, transcript, or text "
            "resource to begin." if empty else ""
        )
        self.tree.setVisible(not empty)
        self.tree.expandAll()
        selected = self.model.first_resource_index()
        if selected.isValid():
            self.tree.setCurrentIndex(selected)

    def clear(self) -> None:
        self.model.replace(None)
        self.tree.setVisible(False)
        self.empty_label.setText("Open a project to browse corpus resources.")
        self.resourceSelected.emit(None)

    def _selection_changed(self, index: QModelIndex, _previous: QModelIndex) -> None:
        resource: ResourceIdentity | None = index.data(self.model.ResourceRole)
        self.resourceSelected.emit(
            resource if index.data(self.model.IsResourceRole) else None
        )
