"""F-07 Qt binding; uses the existing Diagnostics backend without changing Windows."""
from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import QPushButton, QPlainTextEdit, QTableWidget, QTableWidgetItem, QFileDialog, QMessageBox
from PySide6.QtGui import QColor
from .f07_diagnostics import Diagnostics


class _DiagnosticsWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, directory, parent=None):
        super().__init__(parent)
        self.directory = directory

    def run(self):
        try:
            self.completed.emit(Diagnostics(self.directory).run())
        except Exception as exc:
            self.failed.emit(str(exc))


class DiagnosticsUI:
    def __init__(self, window, zapret_dir):
        self.window, self.zapret_dir = window, zapret_dir
        def widget(cls, name):
            result = window.findChild(cls, name)
            if result is None:
                raise RuntimeError(f'F-07: не найден виджет {name}')
            return result
        self.start = widget(QPushButton, 'btnDiagnosticsStart')
        self.stop = widget(QPushButton, 'btnDiagnosticsStop')
        self.export = widget(QPushButton, 'btnDiagnosticsExport')
        self.table = widget(QTableWidget, 'tableDiagnostics')
        self.log = widget(QPlainTextEdit, 'txtDiagnosticsLog')
        self.worker = None
        self.results = []
        self.cancel_requested = False
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(['Проверка', 'Результат', 'Комментарий'])
        self.stop.setEnabled(False)
        self.export.setEnabled(False)
        self.start.clicked.connect(self.start_checks)
        self.stop.clicked.connect(self.cancel_checks)
        self.export.clicked.connect(self.export_report)

    def start_checks(self):
        if self.worker is not None and self.worker.isRunning():
            return
        self.cancel_requested = False
        self.results = []
        self.table.setRowCount(0)
        self.log.setPlainText('Запуск системных проверок…')
        self.start.setEnabled(False)
        self.stop.setEnabled(True)
        self.export.setEnabled(False)
        root = self.zapret_dir() if callable(self.zapret_dir) else self.zapret_dir
        self.worker = _DiagnosticsWorker(Path(root), self.window)
        self.worker.completed.connect(self.show_results)
        self.worker.failed.connect(self.show_error)
        self.worker.finished.connect(self.finish_checks)
        self.worker.start()

    def cancel_checks(self):
        # Diagnostics.run() is synchronous. Allow in-flight OS commands to finish,
        # but discard results instead of force-killing a thread.
        self.cancel_requested = True
        self.stop.setEnabled(False)
        self.log.appendPlainText('Остановка запрошена; ожидаем завершения текущих проверок…')

    def show_results(self, results):
        if self.cancel_requested:
            self.log.appendPlainText('Диагностика отменена. Результаты не применялись.')
            return
        self.results = list(results)
        colors = {'ok': '#55ca8a', 'warning': '#f4c76a', 'error': '#f07d7d', 'unknown': '#aeb9cb'}
        labels = {'ok': 'OK', 'warning': 'Внимание', 'error': 'Ошибка', 'unknown': 'Неизвестно'}
        for index, result in enumerate(self.results):
            self.table.insertRow(index)
            values = [result.title, labels.get(result.status, result.status), result.details]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if col == 1:
                    item.setForeground(QColor(colors.get(result.status, '#aeb9cb')))
                self.table.setItem(index, col, item)
        self.table.resizeColumnsToContents()
        self.log.setPlainText('\n'.join(f'[{r.status.upper()}] {r.title}: {r.details}' for r in self.results))
        self.export.setEnabled(bool(self.results))

    def show_error(self, message):
        self.log.appendPlainText('Ошибка: ' + message)
        QMessageBox.warning(self.window, 'Диагностика', message)

    def finish_checks(self):
        self.start.setEnabled(True)
        self.stop.setEnabled(False)

    def export_report(self):
        if not self.results:
            return
        path, _ = QFileDialog.getSaveFileName(self.window, 'Экспорт диагностики', 'zapret_diagnostics.json', 'JSON (*.json)')
        if not path:
            return
        try:
            Diagnostics.export_json(self.results, path)
            self.log.appendPlainText(f'Отчёт сохранён: {path}')
        except Exception as exc:
            QMessageBox.warning(self.window, 'Экспорт диагностики', str(exc))


def bind_diagnostics(window, zapret_dir):
    return DiagnosticsUI(window, zapret_dir)
