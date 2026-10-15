"""Connect the read-only F-06 inspection button to an existing loaded Qt window."""
from pathlib import Path
from PySide6.QtWidgets import QPushButton, QPlainTextEdit, QMessageBox
from .f06_filters import FilterManager


def format_inspection(data: dict) -> str:
    names = {'domains': 'Домены', 'excludes': 'Исключённые домены',
             'ips': 'IP/подсети (IPSet)', 'ip_excludes': 'Исключённые IP'}
    lines = [f"Установка: {data['installation']}", '', 'СОХРАНЁННЫЕ СПИСКИ']
    for kind, label in names.items():
        info = data['lists'][kind]
        lines.append(f"{label}: {info['count']} записей" if info['exists'] else f"{label}: файл отсутствует")
        if info['entries']:
            lines.extend(f"    {x}" for x in info['entries'][:12])
            if len(info['entries']) > 12:
                lines.append(f"    ... ещё {len(info['entries'])-12}")
    game = data['game_filter']
    lines += ['', 'РЕЖИМЫ',
              f"Game Filter: {game['mode']} (TCP {game['tcp']}; UDP {game['udp']})",
              f"IPSet: {data['ipset']['mode']} (резервная копия: {'есть' if data['ipset']['backup_exists'] else 'нет'})"]
    if data['warnings']:
        lines += ['', 'ПРЕДУПРЕЖДЕНИЯ'] + [f"! {w}" for w in data['warnings']]
    lines += ['', 'Проверка только читает сохранённые файлы; не показывает текущие параметры уже запущенного winws.exe.']
    return '\n'.join(lines)


def bind_filter_inspection(window, zapret_dir):
    """Call once after QUiLoader.load, while ControlWindow is being initialized.

    zapret_dir: Path/string to the selected actual zapret installation folder.
    Returns callback to keep alive; Qt signal retains it as well.
    """
    button = window.findChild(QPushButton, 'btnFilterInspect')
    viewer = window.findChild(QPlainTextEdit, 'txtFilterCurrentSettings')
    if button is None or viewer is None:
        raise RuntimeError('F-06: кнопка или область результатов отсутствует в main_window.ui')

    def inspect():
        try:
            root = zapret_dir() if callable(zapret_dir) else zapret_dir
            data = FilterManager(Path(root)).inspect_saved_settings()
            viewer.setPlainText(format_inspection(data))
        except Exception as exc:
            viewer.setPlainText(f'Ошибка проверки: {exc}')
            QMessageBox.warning(window, 'F-06: проверка настроек', str(exc))

    button.clicked.connect(inspect)
    return inspect
