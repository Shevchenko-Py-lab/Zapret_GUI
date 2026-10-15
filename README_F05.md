# F-05 — Cloudflare WARP in PySide6

Обновлены main.py, zapret_frontend/main_window.ui, dark.qss. Добавлен Zapret_backend/f05_warp.py.
Остальные F-01–F-04 и папку Zapret_dir **не заменять**. Основа патча — доступная стабильная GUI v0.7. Если вы меняли main.py после неё, перенесите изменения через сравнение файлов PyCharm.

## PyCharm
1. Сохраните копии текущих main.py, .ui и .qss.
2. Перенесите четыре файла из архива с сохранением путей.
3. Убедитесь, что официальный Cloudflare WARP установлен и warp-cli status отвечает.
4. Run main.py. На главной появится отдельная карточка WARP.
5. Проверить: отключён → подключить → connecting → connected → отключить → disconnected.
6. Запустить/остановить Zapret: WARP не должен менять управление F-01.

Статус WARP опрашивается каждые 5 секунд вне GUI-потока. Кнопка блокируется до завершения каждой операции; подключение считается успешным только при сообщении Connected от warp-cli. F-03 не изменён.

Для дистрибутива пересоберите PyInstaller и installer.iss: модуль f05_warp должен попасть в exe, обновлённые .ui/.qss — в папку zapret_frontend. Сам Cloudflare WARP клиент в комплект не включён.
