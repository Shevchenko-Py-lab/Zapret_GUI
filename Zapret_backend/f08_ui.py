"""F-08 Qt binding: thread-based sampling and lightweight built-in Qt graph."""
from __future__ import annotations
from collections import deque
import threading
from PySide6.QtCore import QThread, Signal, Qt, QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget, QLabel, QComboBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QApplication
from .f08_traffic import TrafficMonitor


class TrafficChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.down = deque(maxlen=90)
        self.up = deque(maxlen=90)
        self.setMinimumHeight(190)

    def add_sample(self, down, up):
        self.down.append(down)
        self.up.append(up)
        self.update()

    def clear(self):
        self.down.clear()
        self.up.clear()
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        width, height = self.width(), self.height()
        left, top, right, bottom = 45, 12, width-12, height-30
        if right <= left or bottom <= top:
            return
        peak = max([1.0, *self.down, *self.up])
        p.setPen(QPen(QColor('#3a475c'), 1))
        for i in range(5):
            y = top + (bottom-top)*i/4
            p.drawLine(left, round(y), right, round(y))
        p.setPen(QColor('#9aaabe'))
        p.drawText(3, top+10, f'{peak/1048576:.1f}M')
        p.drawText(5, bottom, '0')
        p.drawText(left, height-8, '90 секунд')
        p.drawText(max(left, right-85), height-8, 'сейчас')
        for data, color in [(self.down, '#5cc7ff'), (self.up, '#ffb86b')]:
            if len(data) < 2:
                continue
            pts = QPolygonF([QPointF(right - (len(data)-1-i)*(right-left)/89, bottom - v/peak*(bottom-top)) for i,v in enumerate(data)])
            p.setPen(QPen(QColor(color), 2))
            p.drawPolyline(pts)
        p.end()


class _TrafficWorker(QThread):
    sampled = Signal(object)
    connections_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, interface=None, parent=None):
        super().__init__(parent)
        self.interface = interface
        self.stop_event = threading.Event()

    def run(self):
        try:
            monitor = TrafficMonitor(self.interface)
            n = 0
            while not self.stop_event.is_set():
                self.sampled.emit(monitor.sample())
                if n % 4 == 0:
                    self.connections_ready.emit(monitor.connections(limit=100))
                n += 1
                self.stop_event.wait(1.0)
        except Exception as exc:
            self.failed.emit(str(exc))

    def stop(self):
        self.stop_event.set()


def _size(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(n) < 1024 or unit == 'TB':
            return f'{n:.1f} {unit}'
        n /= 1024


class TrafficUI:
    def __init__(self, window):
        self.window = window
        def widget(cls, name):
            value = window.findChild(cls, name)
            if value is None:
                raise RuntimeError(f'F-08: не найден виджет {name}')
            return value
        self.start = widget(QPushButton, 'btnTrafficStart')
        self.pause = widget(QPushButton, 'btnTrafficPause')
        self.clear = widget(QPushButton, 'btnTrafficClear')
        self.combo = widget(QComboBox, 'cmbTrafficInterface')
        self.labels = {name: widget(QLabel, name) for name in ('lblTrafficDownload','lblTrafficUpload','lblTrafficTotalDown','lblTrafficTotalUp')}
        self.table = widget(QTableWidget, 'tableTrafficConnections')
        placeholder = widget(QWidget, 'trafficChartPlaceholder')
        message = window.findChild(QLabel, 'lblTrafficChartPlaceholder')
        if message is not None:
            message.hide()
        self.chart = TrafficChart(placeholder)
        existing_layout = placeholder.layout()
        if existing_layout is None:
            existing_layout = QVBoxLayout(placeholder)
        existing_layout.addWidget(self.chart)
        self.worker = None
        self.workers = []  # Retain stopped worker until QThread actually finishes.
        self.pause.setEnabled(False)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(['PID','Локальный адрес','Удалённый адрес','Статус'])
        self.combo.clear()
        self.combo.addItem('Все интерфейсы', None)
        for name in TrafficMonitor.interfaces():
            self.combo.addItem(name, name)
        self.start.clicked.connect(self.start_monitor)
        self.pause.clicked.connect(self.pause_monitor)
        self.clear.clicked.connect(self.clear_monitor)
        self.combo.currentIndexChanged.connect(self.change_interface)
        app = QApplication.instance()
        if app:
            app.aboutToQuit.connect(self.shutdown)

    def start_monitor(self):
        if self.worker is not None and self.worker.isRunning():
            return
        interface = self.combo.currentData()
        worker = _TrafficWorker(interface, self.window)
        self.worker = worker
        self.workers.append(worker)
        worker.sampled.connect(self.update_sample)
        worker.connections_ready.connect(self.update_connections)
        worker.failed.connect(self.show_error)
        worker.finished.connect(lambda w=worker: self._finished(w))
        self.start.setEnabled(False)
        self.pause.setEnabled(True)
        self.combo.setEnabled(False)
        worker.start()

    def _finished(self, worker):
        if worker in self.workers:
            self.workers.remove(worker)
        if self.worker is worker:
            self.worker = None
            self.start.setEnabled(True)
            self.pause.setEnabled(False)
            self.combo.setEnabled(True)

    def pause_monitor(self):
        if self.worker is not None:
            self.worker.stop()
        self.pause.setEnabled(False)

    def change_interface(self):
        self.chart.clear()
        self.table.setRowCount(0)
        self.clear_rates()

    def clear_rates(self):
        for name, caption in [('lblTrafficDownload','Загрузка'),('lblTrafficUpload','Отдача'),('lblTrafficTotalDown','Получено'),('lblTrafficTotalUp','Отправлено')]:
            self.labels[name].setText(f'{caption}: —')

    def clear_monitor(self):
        self.chart.clear()
        self.table.setRowCount(0)
        self.clear_rates()

    def update_sample(self, sample):
        if self.worker is None or self.worker.stop_event.is_set():
            return
        self.labels['lblTrafficDownload'].setText('Загрузка: ' + _size(sample.download_bps) + '/s')
        self.labels['lblTrafficUpload'].setText('Отдача: ' + _size(sample.upload_bps) + '/s')
        self.labels['lblTrafficTotalDown'].setText('Получено: ' + _size(sample.received_bytes))
        self.labels['lblTrafficTotalUp'].setText('Отправлено: ' + _size(sample.sent_bytes))
        self.chart.add_sample(sample.download_bps, sample.upload_bps)

    def update_connections(self, connections):
        if self.worker is None or self.worker.stop_event.is_set():
            return
        self.table.setRowCount(len(connections))
        for row, item in enumerate(connections):
            for col, key in enumerate(('pid','local','remote','status')):
                cell = QTableWidgetItem(str(item.get(key) or '—'))
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, col, cell)

    def show_error(self, message):
        self.labels['lblTrafficDownload'].setText('Ошибка: ' + message)
        if self.worker is not None:
            self.worker.stop()

    def shutdown(self):
        for worker in list(self.workers):
            worker.stop()
        for worker in list(self.workers):
            worker.wait(7000)


def bind_traffic(window):
    return TrafficUI(window)
