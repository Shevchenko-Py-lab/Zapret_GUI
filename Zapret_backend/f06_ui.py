"""F-06 UI: inspect saved settings and import TXT files into user lists."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QPlainTextEdit
from .f06_filters import FilterManager, validate_entry, _atomic_write

WIDGETS = {
    'domains': ('btnFilterDomainsImport', 'tableFilterDomains'),
    'excludes': ('btnFilterExcludesImport', 'tableFilterExcludes'),
    'ips': ('btnFilterIPsImport', 'tableFilterIPs'),
    'ip_excludes': ('btnFilterIPExcludesImport', 'tableFilterIPExcludes'),
}


def read_txt(path):
    data = path.read_bytes()
    if len(data) > 8 * 1024 * 1024:
        raise ValueError('Файл больше 8 МБ. Разбей список на части.')
    for enc in ('utf-8-sig', 'utf-16', 'cp1251', 'cp866'):
        try:
            result = data.decode(enc)
            if '\x00' not in result:
                return result
        except UnicodeError:
            pass
    raise ValueError('Не удалось прочитать кодировку TXT')


class FilterImportUI:
    def __init__(self, window, get_root):
        self.window, self.get_root = window, get_root
        self.tables = {}
        for kind, (button_name, table_name) in WIDGETS.items():
            btn = window.findChild(QPushButton, button_name)
            table = window.findChild(QTableWidget, table_name)
            if btn is None or table is None:
                raise RuntimeError('Не найден элемент: ' + button_name + ' / ' + table_name)
            self.tables[kind] = table
            btn.clicked.connect(lambda checked=False, k=kind: self.import_txt(k))
        reload_btn = window.findChild(QPushButton, 'btnFilterReload')
        if reload_btn:
            reload_btn.clicked.connect(self.refresh)
        inspect_button = window.findChild(QPushButton, "btnFilterInspect")
        self.viewer = window.findChild(QPlainTextEdit, "txtFilterCurrentSettings")
        if inspect_button is None or self.viewer is None:
            raise RuntimeError("F-06: элементы проверки настроек не найдены")
        inspect_button.clicked.connect(self.inspect)
        self.refresh()

    def inspect(self):
        try:
            data = self.manager().inspect_saved_settings()
            self.viewer.setPlainText(format_inspection(data))
        except Exception as exc:
            self.viewer.setPlainText(f"Ошибка проверки: {exc}")
            QMessageBox.warning(self.window, "F-06: проверка настроек", str(exc))

    def manager(self):
        root = self.get_root() if callable(self.get_root) else self.get_root
        return FilterManager(Path(root))

    def refresh(self):
        try:
            manager = self.manager()
            for kind, table in self.tables.items():
                entries = manager.read_entries(kind)
                table.setRowCount(len(entries))
                for i, entry in enumerate(entries):
                    for col, value in enumerate((entry, manager.path_for(kind).name)):
                        cell = QTableWidgetItem(value)
                        cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                        table.setItem(i, col, cell)
            # No automatic resizing: large lists would be expensive to render.
        except Exception as exc:
            QMessageBox.warning(self.window, 'F-06: чтение списков', str(exc))

    def import_txt(self, kind):
        filename, _ = QFileDialog.getOpenFileName(
            self.window, 'Импорт TXT', '', 'Текстовые файлы (*.txt);;Все файлы (*)'
        )
        if not filename:
            return
        try:
            manager = self.manager()
            fresh_raw = []
            errors = []
            for number, line in enumerate(read_txt(Path(filename)).splitlines(), 1):
                value = line.strip()
                if not value or value.startswith('#'):
                    continue
                try:
                    fresh_raw.append(validate_entry(kind, value))
                except ValueError as exc:
                    errors.append(f'Строка {number}: {exc}')
                    if len(errors) == 8:
                        break
            if errors:
                raise ValueError('Неверные записи:\n' + '\n'.join(errors))
            if kind == 'ips' and manager.ipset_mode() != 'loaded':
                raise ValueError('Импорт IP доступен только в режиме IPSet Loaded.')
            existing = {s.lower() for s in manager.read_entries(kind)}
            additions = []
            for item in fresh_raw:
                if item.lower() not in existing:
                    existing.add(item.lower())
                    additions.append(item)
            if not additions:
                QMessageBox.information(self.window, 'Импорт TXT', 'Новых записей нет.')
                return
            answer = QMessageBox.question(
                self.window, 'Подтвердите импорт',
                f'Добавить {len(additions)} новых записей из {Path(filename).name}?\n'
                'Существующие записи будут сохранены.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            target = manager.path_for(kind)
            old = target.read_text(encoding='utf-8-sig') if target.exists() else ''
            separator = '\n' if old and not old.endswith(('\n', '\r')) else ''
            _atomic_write(target, old + separator + '\n'.join(additions) + '\n')
            self.refresh()
            QMessageBox.information(self.window, 'Импорт TXT',
                f'Добавлено: {len(additions)}. Для применения перезапустите Zapret.')
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self.window, 'Ошибка импорта TXT', str(exc))



def format_inspection(data: dict) -> str:
    names = {
        "domains": "Домены", "excludes": "Исключённые домены",
        "ips": "IP/подсети (IPSet)", "ip_excludes": "Исключённые IP",
    }
    lines = [f"Установка: {data['installation']}", "", "СОХРАНЁННЫЕ СПИСКИ"]
    for kind, label in names.items():
        info = data["lists"][kind]
        lines.append(f"{label}: {info['count']} записей" if info["exists"] else f"{label}: файл отсутствует")
        if info["entries"]:
            lines.extend(f"    {entry}" for entry in info["entries"][:12])
            if len(info["entries"]) > 12:
                lines.append(f"    ... ещё {len(info['entries']) - 12}")
    game = data["game_filter"]
    lines.extend([
        "", "РЕЖИМЫ",
        f"Game Filter: {game['mode']} (TCP {game['tcp']}; UDP {game['udp']})",
        f"IPSet: {data['ipset']['mode']} (резервная копия: {'есть' if data['ipset']['backup_exists'] else 'нет'})",
    ])
    if data["warnings"]:
        lines.extend(["", "ПРЕДУПРЕЖДЕНИЯ"] + [f"! {warning}" for warning in data["warnings"]])
    lines.extend(["", "Проверка только читает сохранённые файлы; не показывает параметры уже запущенного winws.exe."])
    return "\n".join(lines)


def bind_filter_inspection(window, zapret_dir):
    """Single F-06 binding: saved-settings inspection + TXT import/reload."""
    return FilterImportUI(window, zapret_dir)
