"""Zapret Control entry point for PyCharm (Windows + PySide6).

Adjust the constants under CONFIGURATION for your local installation.
F-04 STAGES a release: it does NOT replace the currently installed zapret.
"""
from __future__ import annotations

import sys
import re
import traceback
from pathlib import Path

from PySide6.QtCore import QFile, QObject, QThread, QTimer, Signal, Slot, Qt
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QApplication, QComboBox, QMessageBox, QPushButton, QInputDialog, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QPlainTextEdit, QFileDialog

from Zapret_backend.f05_warp import WarpController, WarpState
from Zapret_backend.f01_power import PowerController
from Zapret_backend.f02_strategy import strategies_in
from Zapret_backend.f03_auto_strategy import (
    Selection,
    choose_best,
    choose_best_verified,
    TestCancellation,
    TestCancelled,
    parse_report,
    analyze_stability,
)
from Zapret_backend.f04_update import install_release, latest_release
from Zapret_backend.version_store import latest_installation, version_key
from Zapret_backend.f06_ui import bind_filter_inspection
from Zapret_backend.f07_ui import bind_diagnostics
from Zapret_backend.f08_ui import bind_traffic

BASE = Path(__file__).resolve().parent

# ---------------- CONFIGURATION: edit these paths for your machine ----------------
VERSIONS_DIR = BASE / "Zapret_dir"
# This variable refers to the selected actual installation, not the versions container.
ZAPRET_DIR = latest_installation(VERSIONS_DIR)
# Do not guess a stop BAT. Set only if your installed build has a verified stop command.
STOP_FILE: Path | None = None
# F-04 requires an exact, trusted repository and ZIP asset name.
RELEASE_REPOSITORY = "Flowseal/zapret-discord-youtube"
RELEASE_ASSET_NAME = ""  # If empty, choose ZIP only when release contains exactly one ZIP asset.

DEFAULT_TEST_TYPE = "dpi"  # default: 2 (DPI checkers)
RESULT_TOKEN_RE = re.compile(r"(?<![\w])(?:FAIL|OK)(?![\w])", re.IGNORECASE)
# -----------------------------------------------------------------------------------


class Job(QObject):
    """One background job. No QWidget calls in the worker thread."""
    success = Signal(object)
    failure = Signal(str)
    progress = Signal(str)
    finished = Signal()

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    @Slot()
    def run(self):
        try:
            self.success.emit(self.fn())
        except Exception:
            self.failure.emit(traceback.format_exc())
        finally:
            self.finished.emit()


class ControlWindow(QObject):
    def __init__(self):
        super().__init__()
        loader = QUiLoader()
        ui_path = BASE / "zapret_frontend" / "main_window.ui"
        file = QFile(str(ui_path))
        if not file.open(QFile.ReadOnly):
            raise RuntimeError(f"Unable to open UI: {ui_path}: {file.errorString()}")
        try:
            self.window = loader.load(file)
            # В ControlWindow.__init__, после создания self.window:
            self.f06_inspection = bind_filter_inspection(
                self.window,
                lambda: ZAPRET_DIR
            )
            self.f07_ui = bind_diagnostics(
                self.window,
                lambda: ZAPRET_DIR
            )

            self.f08_ui = bind_traffic(self.window)
        finally:
            file.close()
        if self.window is None:
            raise RuntimeError(loader.errorString())
        style = BASE / "zapret_frontend" / "dark.qss"
        if style.is_file():
            self.window.setStyleSheet(style.read_text(encoding="utf-8"))

        def widget(name):
            obj = self.window.findChild(QObject, name)
            if obj is None:
                raise RuntimeError(f"Widget {name!r} missing from main_window.ui")
            return obj

        self.ui = {name: widget(name) for name in (
            "btnPower", "btnFindStrategy", "btnLoadStrategyReport", "btnStopStrategy", "btnApplyBest", "btnCheckUpdates", "btnInstallUpdate",
            "btnSettings", "btnLog", "cmbStrategies", "cmbHomeStrategy", "chkAutoMode", "chkBackupBeforeUpdate",
            "lblStatus", "lblStatusDescription", "lblCurrentStrategy", "lblCurrentMode", "lblRunningStrategy",
            "lblStrategyProgress", "progressStrategy", "tableStrategyResults", "txtTestOutput", "lblInstalledVersion", "lblAvailableVersion",
            "lblUpdateStatus", "lblSystemStatus", "lblHomeInfo",
            "lblWarpStatus", "lblWarpDetails", "btnWarpToggle"
        )}
        self.ui["lblCurrentStrategy"].hide()  # Replaced by home dropdown
        self.active_strategy: Path | None = None  # Confirmed launch in this GUI session
        self.power: PowerController | None = None
        self.selection: Selection | None = None
        self.current_strategy: Path | None = None
        self.latest: dict | None = None
        self.release_asset: str | None = None
        self.thread: QThread | None = None
        self.job: Job | None = None
        self.busy = False
        self.pending_auto_apply = False
        self.test_cancellation: TestCancellation | None = None
        self.testing_f03 = False
        # F-05: official WARP client, separate from F-01–F-04.
        self.warp = WarpController()
        self.warp_state = WarpState.UNKNOWN
        self.warp_thread: QThread | None = None
        self.warp_job: Job | None = None
        self.warp_busy = False
        self.configure_results_table()

        self.ui["btnWarpToggle"].clicked.connect(self.toggle_warp)
        self.ui["btnPower"].clicked.connect(self.toggle_power)
        self.ui["txtTestOutput"].setMaximumBlockCount(700)
        self.ui["btnFindStrategy"].clicked.connect(self.test_strategies)
        self.ui["btnLoadStrategyReport"].clicked.connect(self.load_strategy_report)
        self.ui["btnStopStrategy"].clicked.connect(self.stop_strategy_test)
        self.ui["btnApplyBest"].clicked.connect(self.apply_best)
        self.ui["btnCheckUpdates"].clicked.connect(self.check_updates)
        self.ui["btnInstallUpdate"].clicked.connect(self.install_update)
        self.ui["cmbStrategies"].currentIndexChanged.connect(self.pick_strategy)
        self.ui["cmbHomeStrategy"].currentIndexChanged.connect(self.pick_home_strategy)
        self.ui["chkAutoMode"].toggled.connect(self.update_mode_label)
        # Additional A-features not implemented yet: disable, rather than fake success.
        for name in ("btnSettings", "btnLog", "chkBackupBeforeUpdate"):
            self.ui[name].setEnabled(False)
            self.ui[name].setToolTip("Функция пока не реализована")
        self.ui["btnInstallUpdate"].setEnabled(False)
        self.ui["lblInstalledVersion"].setText(f"Установлена: {ZAPRET_DIR.name.removeprefix('zapret-discord-youtube-')}")
        self.ui["lblUpdateStatus"].setText("Установка пока означает загрузку версии в отдельную папку")
        self.populate_strategies()
        if self.ui["cmbStrategies"].count():
            self.pick_strategy(self.ui["cmbStrategies"].currentIndex())
        self.refresh()
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.refresh)
        self.status_timer.start(2000)
        self.warp_timer = QTimer(self)
        self.warp_timer.timeout.connect(self.refresh_warp_async)
        self.warp_timer.start(5000)
        self.refresh_warp_async()

    # F-05 deliberately has its own worker, so WARP status cannot freeze Qt.
    def run_warp_job(self, fn, *, action=False):
        if self.warp_thread is not None:
            return
        self.warp_busy = action
        self.ui["btnWarpToggle"].setEnabled(False)
        thread = QThread(self)
        job = Job(fn)
        self.warp_thread, self.warp_job = thread, job
        job.moveToThread(thread)
        thread.started.connect(job.run)
        job.success.connect(self.on_warp_status)
        job.failure.connect(self.on_warp_error)
        job.finished.connect(thread.quit)
        job.finished.connect(job.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self.warp_job_finished)
        thread.start()

    @Slot()
    def refresh_warp_async(self):
        if self.warp_thread is None:
            self.run_warp_job(self.warp.status)

    @Slot()
    def toggle_warp(self):
        if self.warp_thread is not None:
            return
        if self.warp_state == WarpState.NOT_INSTALLED:
            QMessageBox.information(self.window, "Cloudflare WARP",
                "Официальный Cloudflare WARP не найден. Установи его отдельно, затем перезапусти Zapret Control.")
            return
        if self.warp_state == WarpState.CONNECTED:
            self.run_warp_job(self.warp.disconnect, action=True)
        elif self.warp_state == WarpState.DISCONNECTED:
            self.run_warp_job(self.warp.connect, action=True)
        else:
            QMessageBox.information(self.window, "Cloudflare WARP",
                "Дождись завершения подключения или отключения WARP и повтори действие.")

    @Slot(object)
    def on_warp_status(self, status):
        self.warp_state = status.state
        labels = {
            WarpState.CONNECTED: "● WARP подключён",
            WarpState.DISCONNECTED: "○ WARP отключён",
            WarpState.CONNECTING: "◌ WARP подключается…",
            WarpState.DISCONNECTING: "◌ WARP отключается…",
            WarpState.NOT_INSTALLED: "⚠ WARP не установлен",
            WarpState.UNKNOWN: "⚠ Состояние WARP неизвестно",
        }
        self.ui["lblWarpStatus"].setText(labels.get(status.state, labels[WarpState.UNKNOWN]))
        reason = next((line.strip() for line in status.details.splitlines()
                       if line.strip().lower().startswith("reason:")), "")
        self.ui["lblWarpDetails"].setText(reason or "Официальный клиент Cloudflare WARP")
        self.ui["lblWarpStatus"].setProperty("warpState", status.state.value)
        widget = self.ui["lblWarpStatus"]
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        self.ui["btnWarpToggle"].setText("Отключить WARP" if status.state == WarpState.CONNECTED else "Подключить WARP")

    @Slot(str)
    def on_warp_error(self, details):
        self.warp_state = WarpState.UNKNOWN
        self.ui["lblWarpStatus"].setText("⚠ Ошибка управления WARP")
        self.ui["lblWarpDetails"].setText("Подробности в сообщении")
        QMessageBox.warning(self.window, "Cloudflare WARP", details[-3000:])

    @Slot()
    def warp_job_finished(self):
        self.warp_thread = None
        self.warp_job = None
        self.warp_busy = False
        self.ui["btnWarpToggle"].setEnabled(self.warp_state in (WarpState.CONNECTED, WarpState.DISCONNECTED))

    def configure_results_table(self):
        table: QTableWidget = self.ui["tableStrategyResults"]
        table.setColumnCount(8)
        table.setHorizontalHeaderLabels([
            "Стратегия", "OK", "FAIL", "BLOCKED", "UNSUP",
            "OK повтор", "Δ OK (п.п.)", "Стабильность"
        ])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setAlternatingRowColors(True)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 8):
            table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)

    def render_results(self, selection: Selection):
        table: QTableWidget = self.ui["tableStrategyResults"]
        table.setRowCount(0)
        best_name = selection.best.name if selection.best else None
        comparisons = {c.name: c for c in getattr(selection, "comparisons", ())}
        candidates = sorted(selection.candidates, key=lambda c: (-c.ok, c.errors, c.name.lower()))
        for row, candidate in enumerate(candidates):
            table.insertRow(row)
            is_best = candidate.name == best_name
            comparison = comparisons.get(candidate.name)
            if comparison:
                stability = "Нестабильна" if comparison.unstable else "Стабильна"
                repeated_ok = str(comparison.second.ok)
                delta = f"{comparison.delta_pp:+.1f}"
            else:
                stability = "Не перепроверена"
                repeated_ok = "—"
                delta = "—"
            if is_best:
                stability = "★ " + stability
            values = [
                candidate.name, str(candidate.ok), str(candidate.errors),
                str(candidate.blocked), str(candidate.unsupported),
                repeated_ok, delta, stability,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if 1 <= column <= 6:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if comparison and comparison.unstable:
                    item.setForeground(QColor("#f4c76a"))
                table.setItem(row, column, item)
            if is_best:
                table.selectRow(row)
        table.setToolTip(
            "Первый проход: OK, FAIL, BLOCKED, UNSUP. "
            "Повторный проход отображается для трёх лидеров; "
            "Δ OK — изменение доли успешных проверок в процентных пунктах."
        )

    def show(self):
        self.window.show()

    def notify_error(self, detail):
        self.ui["lblSystemStatus"].setText("Ошибка — подробности в сообщении")
        QMessageBox.critical(self.window, "Zapret Control — ошибка", detail[-5000:])

    def require_installation(self):
        if not ZAPRET_DIR.is_dir():
            raise FileNotFoundError(f"Не найдена папка zapret: {ZAPRET_DIR}\nИсправь ZAPRET_DIR в main.py")
        if not (ZAPRET_DIR / "utils" / "test zapret.ps1").is_file():
            raise FileNotFoundError("Нет utils/test zapret.ps1 — проверь версию zapret")

    def populate_strategies(self):
        box: QComboBox = self.ui["cmbStrategies"]
        box.blockSignals(True)
        box.clear()
        if ZAPRET_DIR.is_dir():
            for path in strategies_in(ZAPRET_DIR):
                if path.name.lower().startswith("general") and path.suffix.lower() == ".bat":
                    box.addItem(path.name, str(path))
        box.blockSignals(False)
        home: QComboBox = self.ui["cmbHomeStrategy"]
        home.blockSignals(True)
        home.clear()
        for i in range(box.count()):
            home.addItem(box.itemText(i), box.itemData(i))
        home.blockSignals(False)
        self.ui["lblStrategyProgress"].setText(f"Найдено BAT-стратегий: {box.count()}")

    @Slot(int)
    def pick_strategy(self, index):
        data = self.ui["cmbStrategies"].itemData(index)
        if data:
            self.current_strategy = Path(data)
            self.selection = None  # manual change invalidates previous winner
            self.ui["lblCurrentStrategy"].setText(self.current_strategy.name)
            home = self.ui["cmbHomeStrategy"]
            home_index = home.findData(str(self.current_strategy))
            if home_index >= 0:
                home.blockSignals(True)
                home.setCurrentIndex(home_index)
                home.blockSignals(False)

    @Slot(int)
    def pick_home_strategy(self, index):
        if self.busy:
            return
        selected = self.ui["cmbHomeStrategy"].itemData(index)
        if not selected:
            return
        strategy_box = self.ui["cmbStrategies"]
        strategy_index = strategy_box.findData(selected)
        if strategy_index >= 0:
            strategy_box.setCurrentIndex(strategy_index)
        # No implicit restart. The next F-01 start uses current_strategy.
        if self.power and self.power.is_running():
            self.ui["lblSystemStatus"].setText(
                "Новая стратегия выбрана. Для применения выключи и вновь включи zapret."
            )

    @Slot(bool)
    def update_mode_label(self, checked):
        self.ui["lblCurrentMode"].setText("Автоматический выбор по кнопке" if checked else "Ручной режим")
        self.refresh()

    def refresh(self):
        running = PowerController(ZAPRET_DIR / "general.bat").is_running()
        if not running:
            self.active_strategy = None
        active_text = (self.active_strategy.name if self.active_strategy else "профиль неизвестен") if running else "не запущена"
        self.ui["lblRunningStrategy"].setText(f"Сейчас работает: {active_text}")
        self.ui["lblStatus"].setText("● Zapret работает" if running else "○ Zapret отключён")
        self.ui["lblStatusDescription"].setText("Процесс winws.exe запущен" if running else "Обход блокировок не запущен")
        self.ui["btnPower"].setText("ВЫКЛЮЧИТЬ" if running else "ВКЛЮЧИТЬ")
        self.ui["btnPower"].setEnabled(not self.busy)
        self.ui["btnFindStrategy"].setEnabled(not self.busy)
        self.ui["btnLoadStrategyReport"].setEnabled(not self.busy)
        self.ui["btnStopStrategy"].setEnabled(self.busy and self.testing_f03 and self.test_cancellation is not None and not self.test_cancellation.event.is_set())
        self.ui["btnApplyBest"].setEnabled(not self.busy)
        self.ui["btnCheckUpdates"].setEnabled(not self.busy)
        self.ui["btnInstallUpdate"].setEnabled(not self.busy and self.latest is not None and bool(self.release_asset))
        self.ui["cmbStrategies"].setEnabled(not self.busy)
        self.ui["cmbHomeStrategy"].setEnabled(not self.busy)
        self.ui["progressStrategy"].setRange(0, 0 if self.busy else 1)
        if not self.busy:
            self.ui["progressStrategy"].setValue(0)

    def set_job(self, fn, on_success, message, on_failure=None, on_progress=None):
        if self.busy:
            return
        self.busy = True
        self.ui["lblSystemStatus"].setText(message)
        self.thread = QThread(self.window)
        self.job = Job(fn)
        self.job.moveToThread(self.thread)
        self.thread.started.connect(self.job.run)
        self.job.success.connect(on_success)
        self.job.failure.connect(on_failure or self.notify_error)
        if on_progress is not None:
            self.job.progress.connect(on_progress)
        self.job.finished.connect(self.thread.quit)
        self.job.finished.connect(self.job.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(self.end_job)
        self.refresh()
        self.thread.start()

    @Slot()
    def end_job(self):
        self.busy = False
        self.testing_f03 = False
        self.test_cancellation = None
        self.job = None
        self.thread = None
        self.refresh()
        if self.pending_auto_apply:
            self.pending_auto_apply = False
            self.apply_best()

    @Slot()
    def toggle_power(self):
        if self.busy:
            return
        try:
            if PowerController(ZAPRET_DIR / "general.bat").is_running():
                PowerController(ZAPRET_DIR / "general.bat").stop()
                self.active_strategy = None
                self.ui["lblSystemStatus"].setText("winws остановлен")
            else:
                strategy = self.current_strategy
                if strategy is None:
                    strategy = Path(self.ui["cmbStrategies"].currentData() or "")
                if not strategy.is_file():
                    raise FileNotFoundError("Сначала выбери BAT-стратегию")
                self.power = PowerController(strategy, STOP_FILE)
                self.power.start()
                self.active_strategy = strategy  # start() verifies that winws actually appeared
                self.ui["lblSystemStatus"].setText(f"Запуск: {strategy.name}")
                self.ui["lblCurrentStrategy"].setText(strategy.name)
        except Exception:
            self.notify_error(traceback.format_exc())
        self.refresh()

    @Slot()
    def stop_strategy_test(self):
        if not self.testing_f03 or self.test_cancellation is None:
            return
        self.pending_auto_apply = False
        self.test_cancellation.cancel()
        self.ui["lblSystemStatus"].setText("Прерывание тестирования…")
        self.ui["lblStrategyProgress"].setText("Останавливаем штатный тестер…")
        self.refresh()

    @Slot(str)
    def on_test_error(self, details):
        if 'TestCancelled' in details or (self.test_cancellation and self.test_cancellation.event.is_set()):
            self.pending_auto_apply = False
            self.selection = None
            self.ui["lblStrategyProgress"].setText("Тестирование остановлено пользователем")
            self.ui["lblSystemStatus"].setText("F-03 отменён. Автоприменение отключено.")
        else:
            self.pending_auto_apply = False
            self.selection = None
            self.notify_error(details)

    def append_colored_test_line(self, line: str):
        """Color OK and FAIL tokens in the live log; display-only, no backend changes."""
        output = self.ui["txtTestOutput"]
        cursor = output.textCursor()
        cursor.movePosition(QTextCursor.End)
        if not output.document().isEmpty():
            cursor.insertBlock()

        normal = QTextCharFormat()
        normal.setForeground(QColor("#e6edf3"))
        ok = QTextCharFormat()
        ok.setForeground(QColor("#3fb950"))
        fail = QTextCharFormat()
        fail.setForeground(QColor("#ff6565"))
        fail.setFontWeight(700)

        start = 0
        for match in RESULT_TOKEN_RE.finditer(line):
            cursor.insertText(line[start:match.start()], normal)
            cursor.insertText(match.group(), ok if match.group().upper() == "OK" else fail)
            start = match.end()
        cursor.insertText(line[start:], normal)
        output.setTextCursor(cursor)
        output.ensureCursorVisible()

    @Slot(str)
    def on_test_progress(self, line):
        """Run on Qt's GUI thread; do not manipulate widgets from F-03 worker."""
        self.append_colored_test_line(line)
        if line.strip():
            self.ui["lblStrategyProgress"].setText(line.strip()[:170])
        # Some upstream builds print per-config summaries before the final report.
        # Render such rows immediately. Never infer the final winner here.
        from Zapret_backend.f03_auto_strategy import STANDARD, DPI
        match = STANDARD.search(line) or DPI.search(line)
        if match:
            parts = match.groups()
            name = parts[0].strip()
            table = self.ui["tableStrategyResults"]
            row = next((i for i in range(table.rowCount())
                        if table.item(i, 0) and table.item(i, 0).text() == name), -1)
            if row < 0:
                row = table.rowCount()
                table.insertRow(row)
            # STANDARD: name, OK, ERR, UNSUP, PingOK, Fail
            # DPI: name, OK, FAIL, UNSUP, BLOCKED
            if len(parts) == 6:
                values = [name, parts[1], parts[2], "—", parts[3], "—", "—", "Промежуточный"]
            else:
                values = [name, parts[1], parts[2], parts[4], parts[3], "—", "—", "Промежуточный"]
            for col, value in enumerate(values):
                table.setItem(row, col, QTableWidgetItem(str(value)))

    @Slot()
    def load_strategy_report(self):
        """Read a saved Flowseal analytics TXT without running the tester.

        A single imported report is unverified and must never auto-apply.
        """
        if self.busy:
            QMessageBox.information(self.window, "F-03", "Дождись завершения текущего тестирования.")
            return
        initial_dir = ZAPRET_DIR / "utils" / "test results"
        if not initial_dir.is_dir():
            initial_dir = ZAPRET_DIR
        filename, _ = QFileDialog.getOpenFileName(
            self.window, "Загрузить отчёт Flowseal", str(initial_dir),
            "Отчёты (*.txt);;Все файлы (*)"
        )
        if not filename:
            return
        report = Path(filename)
        try:
            raw = report.read_bytes()
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("Отчёт больше 8 МБ; проверь выбранный файл")
            content = None
            for encoding in ("utf-8-sig", "cp1251", "cp866"):
                try:
                    content = raw.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            if content is None:
                raise ValueError("Не удалось определить кодировку TXT")
            if "=== ANALYTICS ===" not in content or "Best strategy:" not in content:
                raise ValueError("Не найден блок ANALYTICS или строка Best strategy")
            first = parse_report(content, ZAPRET_DIR, report)
            if not first.candidates:
                raise ValueError("В отчёте нет результатов проверок стратегий")
            result = analyze_stability(first, None)
            self.pending_auto_apply = False
            self.on_tested(result)
            self.pending_auto_apply = False
            self.ui["txtTestOutput"].setPlainText(content[-45000:])
            self.ui["lblStrategyProgress"].setText(
                f"Загружен отчёт: {report.name}; стратегий: {len(first.candidates)}; "
                "стабильность не подтверждена"
            )
            self.ui["lblSystemStatus"].setText(
                "Результат загружен из TXT. Автоприменение отключено; "
                "повторная проверка не выполнялась."
            )
        except Exception as exc:
            self.pending_auto_apply = False
            QMessageBox.warning(self.window, "Импорт результатов F-03", str(exc))

    @Slot()
    def test_strategies(self):
        try:
            self.require_installation()
            if self.power and self.power.is_running():
                raise RuntimeError("Перед штатными тестами выключи процесс, запущенный GUI")
            types = ["2 — DPI checkers (TCP 16-20 freeze)", "1 — Standard tests (HTTP/ping)"]
            selected_type, ok = QInputDialog.getItem(
                self.window, "Тип тестирования", "Выбери тип проверки:", types, 0, False
            )
            if not ok:
                return
            test_type = "dpi" if selected_type.startswith("2") else "standard"

            modes = ["1 — Все конфигурации (полное)", "2 — Выбранная конфигурация (частичное)"]
            selected_mode, ok = QInputDialog.getItem(
                self.window, "Объём тестирования", "Какие конфигурации проверить?", modes, 0, False
            )
            if not ok:
                return
            run_mode = "all" if selected_mode.startswith("1") else "select"
            selected_names = ()
            if run_mode == "select":
                available = [self.ui["cmbStrategies"].itemText(i) for i in range(self.ui["cmbStrategies"].count())]
                if not available:
                    raise RuntimeError("Нет доступных стратегий для частичного тестирования")
                picked, ok = QInputDialog.getItem(
                    self.window, "Выбор конфигурации", "Проверить BAT-конфигурацию:", available, 0, False
                )
                if not ok:
                    return
                selected_names = (picked,)

            self.selection = None
            self.pending_auto_apply = False
            self.test_cancellation = TestCancellation()
            self.testing_f03 = True
            self.ui["tableStrategyResults"].setRowCount(0)
            self.ui["txtTestOutput"].clear()
            self.set_job(
                lambda: choose_best_verified(ZAPRET_DIR, test_type=test_type, run_mode=run_mode,
                                    selected_names=selected_names, cancellation=self.test_cancellation,
                                    progress=self.job.progress.emit),
                self.on_tested, "Штатный тестер проверяет стратегии (может занять время)…",
                on_failure=self.on_test_error, on_progress=self.on_test_progress
            )
        except Exception:
            self.testing_f03 = False
            self.test_cancellation = None
            self.notify_error(traceback.format_exc())

    @Slot(object)
    def on_tested(self, selection):
        if self.test_cancellation is not None and self.test_cancellation.event.is_set():
            self.pending_auto_apply = False
            self.ui["lblSystemStatus"].setText("Тестирование остановлено; результат не применён")
            return
        self.selection = selection
        self.render_results(selection)
        best = selection.best.name if selection.best else "подходящая стратегия не найдена"
        verified = bool(getattr(selection, "safe_auto_apply", False))
        warning = getattr(selection, "warning", "Подтверждение стабильности отсутствует")
        self.ui["lblStrategyProgress"].setText(
            f"Проверено: {len(selection.candidates)}; лидер: {best}; "
            + ("повторная проверка подтверждена" if verified else f"предупреждение: {warning}")
        )
        self.ui["lblSystemStatus"].setText(
            "Тест завершён; автоматическое применение разрешено" if verified else
            "Тест завершён; автоприменение запрещено: " + warning
        )
        if selection.best:
            index = self.ui["cmbStrategies"].findData(str(selection.best))
            if index >= 0:
                # Do NOT invalidate selection via currentIndexChanged
                combo = self.ui["cmbStrategies"]
                combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(False)
            self.current_strategy = selection.best
            self.ui["lblCurrentStrategy"].setText(selection.best.name)
            home = self.ui["cmbHomeStrategy"]
            home_index = home.findData(str(selection.best))
            if home_index >= 0:
                home.blockSignals(True)
                home.setCurrentIndex(home_index)
                home.blockSignals(False)
            if self.ui["chkAutoMode"].isChecked():
                if verified:
                    self.pending_auto_apply = True
                    self.ui["lblSystemStatus"].setText(
                        "Стратегия прошла повторную проверку — автоматическое применение…"
                    )
                else:
                    self.pending_auto_apply = False
                    self.ui["lblSystemStatus"].setText(
                        "Стратегия не прошла проверку стабильности. "
                        "Автоматическое применение отменено."
                    )
        elif self.ui["chkAutoMode"].isChecked():
            self.ui["lblSystemStatus"].setText("Нет успешной стратегии: автоматическое применение отменено")

    @Slot()
    def apply_best(self):
        if not self.selection or not self.selection.best:
            QMessageBox.information(self.window, "Стратегия", "Сначала нажми «Проверить стратегии».")
            return
        if PowerController(ZAPRET_DIR / "general.bat").is_running():
            QMessageBox.warning(self.window, "Стратегия", "Отключи ранее запущенную стратегию перед применением другой.")
            return
        # Applying the winner requires stopping any already-running winws first.
        self.current_strategy = self.selection.best
        self.toggle_power()

    @Slot()
    def check_updates(self):
        if self.busy:
            return
        self.set_job(lambda: latest_release(RELEASE_REPOSITORY), self.on_release,
                     "Проверка GitHub Releases…")

    @Slot(object)
    def on_release(self, release):
        self.latest = release
        tag = release.get("tag_name", "не указана")
        self.ui["lblAvailableVersion"].setText(f"Последний релиз: {tag}")
        assets = [a.get("name", "") for a in release.get("assets", []) if a.get("name", "").lower().endswith(".zip")]
        self.release_asset = RELEASE_ASSET_NAME or (assets[0] if len(assets) == 1 else None)
        self.ui["lblUpdateStatus"].setText(
            "ZIP выбран: " + self.release_asset if self.release_asset else
            "Не удалось автоматически выбрать ZIP: укажи RELEASE_ASSET_NAME")
        self.refresh()
        self.ui["lblSystemStatus"].setText("Проверка релиза завершена")

    @Slot()
    def install_update(self):
        if not self.latest or not self.release_asset:
            QMessageBox.information(self.window, "Обновления", "Сначала укажи RELEASE_ASSET_NAME и проверь обновления.")
            return
        answer = QMessageBox.question(self.window, "Загрузка обновления",
                                      "Скачать и распаковать релиз в отдельную папку?\nТекущий zapret не будет заменён.")
        if answer != QMessageBox.Yes:
            return
        self.set_job(lambda: install_release(RELEASE_REPOSITORY, self.release_asset, VERSIONS_DIR),
                     self.on_installed, "Загрузка и распаковка обновления…")

    @Slot(object)
    def on_installed(self, target):
        self.ui["lblUpdateStatus"].setText(f"Версия добавлена: {target.name}")
        self.ui["lblSystemStatus"].setText("Новая версия будет выбрана при следующем запуске приложения")


def main():
    app = QApplication(sys.argv)
    controller = ControlWindow()
    controller.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
