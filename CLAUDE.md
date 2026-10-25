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
src/translater/app.py          composition root, связывание Qt и рабочих потоков, загрузка компонентов
src/translater/components.py   закреплённые компоненты (NVIDIA-колёса PyPI, модели HF), загрузка с докачкой и SHA-256
src/translater/inference/      process.py — RPC к процессу моделей; worker.py — сам процесс; speech.py — Whisper и перевод
src/translater/audio/          capture.py (WASAPI, только Windows), vad.py, segmenter.py, resample.py
src/translater/incoming.py     IncomingRunner: звук -> фразы -> процесс моделей -> субтитры
src/translater/selftest.py     Translater.exe --self-test <отчёт>: проверка упаковки в CI
src/translater/win32/          ctypes: RegisterHotKey, GetAsyncKeyState, приоритет, мьютекс
src/translater/ui/             главное окно, трей, форма настроек, захват клавиш, слежение за клавишей записи
.github/workflows/release.yml  push в main -> тесты; новая версия -> PyInstaller, тег и архив в Releases
```

## Инварианты

- Executor моделей однопоточный: через него идут загрузка, выгрузка и инференс на GPU.
- Модели только в процессе `inference/worker.py`: основной процесс не импортирует ctranslate2/faster_whisper.
  Процесс завершается, когда выгружена последняя модель, — так освобождается видеопамять CUDA-контекста.
- cuDNN обязан совпадать с `cudnn64_9.dll` из колеса ctranslate2 (сейчас 9.10.2.21); при обновлении ctranslate2
  сверить версию и хеши в `components.py`.
- Остановка режима (`runner.stop`) — вне блокировки контроллера: runner ждёт executor, колбэки которого берут блокировку.
- Колбэки контроллера приходят из рабочих потоков; в UI — только через сигналы `_Bridge`.
- Ядро (`config`, `models`, `controller`) не импортирует Qt и Win32 — тесты идут на любой ОС.
- Новая модель — `ModelSpec` в `MODEL_SPECS` и имя в `MODE_REQUIREMENTS`; новый режим работы — `ModeRunner`.

## Команды

```powershell
python -m pytest
python -m translater.doctor
python -m translater
```

Версия — только `version` в `pyproject.toml`; тег `v<версия>` создаёт workflow, вручную теги не отправлять.
