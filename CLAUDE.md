# CLAUDE.md

Translater — локальный переводчик голосового чата для игр, только Windows. Python 3.14 (поддерживается 3.12–3.14), PySide6. Описание и этапы —
[README.md](README.md). Язык UI и документации — русский, идентификаторы — английские.

## Правила

- Не делать `git commit` и `git push` без явного разрешения пользователя.
- Без больших комментариев и TODO. Готовые библиотеки вместо своей логики.
- Win32-вызовы и форматы сверять с официальной документацией Microsoft.
- Ничего не устанавливать в систему пользователя (драйверы, службы) без разрешения.
- Не эмулировать ввод в игру и не внедряться в её процесс.
- В журнал не писать тексты фраз и звук.

## Структура

```text
src/translater/config.py       AppConfig (JSON), валидация, пути
src/translater/models.py       ModelManager: ленивая загрузка, счётчик ссылок, выгрузка по таймеру
src/translater/controller.py   ModeController: IDLE -> LOADING -> ACTIVE для режимов incoming/outgoing
src/translater/app.py          composition root, связывание Qt и рабочих потоков
src/translater/win32/          ctypes: RegisterHotKey, GetAsyncKeyState, приоритет, мьютекс
src/translater/ui/             главное окно, трей, форма настроек, захват клавиш, слежение за клавишей записи
.github/workflows/release.yml  тег vX.Y.Z -> тесты, PyInstaller, архив в Releases
```

## Инварианты

- Executor моделей однопоточный: через него идут загрузка, выгрузка и инференс на GPU.
- Колбэки контроллера приходят из рабочих потоков; в UI — только через сигналы `_Bridge`.
- Ядро (`config`, `models`, `controller`) не импортирует Qt и Win32 — тесты идут на любой ОС.
- Новая модель — `ModelSpec` в `MODEL_SPECS` и имя в `MODE_REQUIREMENTS`; новый режим работы — `ModeRunner`.

## Команды

```powershell
python -m pytest
python -m translater.doctor
python -m translater
```

Версия — только `version` в `pyproject.toml`; тег релиза обязан совпадать с ней.
