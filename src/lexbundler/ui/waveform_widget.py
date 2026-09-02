"""Small native waveform view with immutable and editable boundary markers."""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from lexbundler.application.waveform import WaveformWindow


class WaveformWidget(QWidget):
    boundaryMoved = Signal(str, int)

    _LEFT = 28
    _RIGHT = 16
    _TOP = 22
    _BOTTOM = 24

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(170)
        self.setMouseTracking(True)
        self._waveform: WaveformWindow | None = None
        self._window_start_ms = 0
        self._window_end_ms = 1
        self._speech_start_ms: int | None = None
        self._speech_end_ms: int | None = None
        self._proposed_start_ms: int | None = None
        self._proposed_end_ms: int | None = None
        self._dragging: str | None = None
        self._message = "Select aligned audio to display a waveform."

    @property
    def speech_bounds(self) -> tuple[int | None, int | None]:
        return self._speech_start_ms, self._speech_end_ms

    @property
    def proposed_bounds(self) -> tuple[int | None, int | None]:
        return self._proposed_start_ms, self._proposed_end_ms

    def set_context(
        self, *, start_ms: int, end_ms: int, speech_start_ms: int,
        speech_end_ms: int, proposed_start_ms: int, proposed_end_ms: int,
    ) -> None:
        self._window_start_ms = start_ms
        self._window_end_ms = max(start_ms + 1, end_ms)
        self._speech_start_ms = speech_start_ms
        self._speech_end_ms = speech_end_ms
        self.set_provisional_bounds(proposed_start_ms, proposed_end_ms)

    def set_waveform(self, waveform: WaveformWindow) -> None:
        self._waveform = waveform
        self._window_start_ms = waveform.start_ms
        self._window_end_ms = waveform.end_ms
        self._message = ""
        self.update()

    def set_loading(self) -> None:
        self._waveform = None
        self._message = "Loading waveform…"
        self.update()

    def set_unavailable(self, message: str) -> None:
        self._waveform = None
        self._message = message
        self.update()

    def clear(self) -> None:
        self._waveform = None
        self._speech_start_ms = None
        self._speech_end_ms = None
        self._proposed_start_ms = None
        self._proposed_end_ms = None
        self._message = "Waveform unavailable."
        self.update()

    def set_provisional_bounds(self, start_ms: int, end_ms: int) -> None:
        self._proposed_start_ms = max(self._window_start_ms, min(start_ms, self._window_end_ms - 1))
        self._proposed_end_ms = min(
            self._window_end_ms, max(end_ms, self._proposed_start_ms + 1)
        )
        self.update()

    def time_to_x(self, milliseconds: int) -> float:
        width = max(1, self.width() - self._LEFT - self._RIGHT)
        fraction = (milliseconds - self._window_start_ms) / (
            self._window_end_ms - self._window_start_ms
        )
        return self._LEFT + max(0.0, min(1.0, fraction)) * width

    def x_to_time(self, x: float) -> int:
        width = max(1, self.width() - self._LEFT - self._RIGHT)
        fraction = max(0.0, min(1.0, (x - self._LEFT) / width))
        return round(
            self._window_start_ms
            + fraction * (self._window_end_ms - self._window_start_ms)
        )

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        graph = QRectF(
            self._LEFT, self._TOP,
            max(1, self.width() - self._LEFT - self._RIGHT),
            max(1, self.height() - self._TOP - self._BOTTOM),
        )
        painter.setPen(QPen(self.palette().mid().color(), 1))
        painter.drawRect(graph)
        center = graph.center().y()
        if self._waveform is None:
            painter.drawText(graph, Qt.AlignmentFlag.AlignCenter, self._message)
        else:
            if self._proposed_start_ms is not None and self._proposed_end_ms is not None:
                selected = QRectF(
                    self.time_to_x(self._proposed_start_ms), graph.top(),
                    self.time_to_x(self._proposed_end_ms) - self.time_to_x(self._proposed_start_ms),
                    graph.height(),
                )
                painter.fillRect(selected, QColor(70, 130, 180, 45))
            painter.setPen(QPen(self.palette().text().color(), 1))
            count = len(self._waveform.buckets)
            for index, bucket in enumerate(self._waveform.buckets):
                x = graph.left() + (index + 0.5) * graph.width() / count
                painter.drawLine(
                    QPointF(x, center - bucket.maximum * graph.height() / 2),
                    QPointF(x, center - bucket.minimum * graph.height() / 2),
                )
        self._paint_markers(painter, graph)
        painter.setPen(self.palette().text().color())
        painter.drawText(2, self.height() - 5, f"{self._window_start_ms / 1000:.3f}")
        end_text = f"{self._window_end_ms / 1000:.3f} s"
        painter.drawText(self.width() - 70, self.height() - 5, end_text)

    def _paint_markers(self, painter: QPainter, graph: QRectF) -> None:
        painter.setPen(QPen(QColor("#7755aa"), 2, Qt.PenStyle.DashLine))
        for value, label in (
            (self._speech_start_ms, "MFA start"),
            (self._speech_end_ms, "MFA end"),
        ):
            if value is not None:
                x = self.time_to_x(value)
                painter.drawLine(QPointF(x, graph.top()), QPointF(x, graph.bottom()))
                painter.drawText(QPointF(x + 3, 14), label)
        painter.setPen(QPen(QColor("#176b45"), 3, Qt.PenStyle.SolidLine))
        for value, label in (
            (self._proposed_start_ms, "Proposed start"),
            (self._proposed_end_ms, "Proposed end"),
        ):
            if value is not None:
                x = self.time_to_x(value)
                painter.drawLine(QPointF(x, graph.top()), QPointF(x, graph.bottom()))
                triangle = QPolygonF([
                    QPointF(x - 6, graph.top()), QPointF(x + 6, graph.top()),
                    QPointF(x, graph.top() + 9),
                ])
                painter.setBrush(QColor("#176b45"))
                painter.drawPolygon(triangle)
                painter.drawText(QPointF(x + 3, graph.bottom() - 4), label)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._proposed_start_ms is None or self._proposed_end_ms is None:
            return
        distances = {
            "start": abs(event.position().x() - self.time_to_x(self._proposed_start_ms)),
            "end": abs(event.position().x() - self.time_to_x(self._proposed_end_ms)),
        }
        handle = min(distances, key=distances.get)
        if distances[handle] <= 12:
            self._dragging = handle
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging is None:
            return
        value = self.x_to_time(event.position().x())
        if self._dragging == "start" and self._proposed_end_ms is not None:
            value = min(value, self._proposed_end_ms - 1)
        elif self._dragging == "end" and self._proposed_start_ms is not None:
            value = max(value, self._proposed_start_ms + 1)
        self.boundaryMoved.emit(self._dragging, value)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._dragging is not None:
            self._dragging = None
            event.accept()
