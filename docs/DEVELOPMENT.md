# Разработка

## Окружение

Нужны Windows, Git и Python 3.14 (поддерживаются 3.12–3.14). Ядро (`config`, `models`, `controller`, звук, фильтры)
тестируется на любой ОС. Захват звука, горячие клавиши, SAPI и CUDA работают только в Windows.

```powershell
git clone https://github.com/EXTRBXY/translater
cd translater
py -3.14 -m venv .venv
.venv\Scripts\pip install -e .[dev]
```

| Команда | Что делает |
|---|---|
| `.venv\Scripts\python -m translater` | Запуск из исходников. |
| `.venv\Scripts\python -m pytest` | Тесты. |
| `.venv\Scripts\python -m translater.doctor` | Отчёт об окружении: Windows, захват процесса, версии пакетов, GPU. |
| `ruff check src tests` | Линтер. Правила заданы в `pyproject.toml`, сам ruff ставится отдельно (`pip install ruff`). |

## Сборка у себя

Один раз: `git clone https://github.com/EXTRBXY/translater`.

Дальше для каждой сборки закройте программу и запустите `build.bat` в корне репозитория (двойной щелчок). Он:

1. делает `git pull --ff-only` и останавливается, если обновиться не удалось (например, есть локальные изменения);
2. запускает `tools\build.ps1`, который:
   - создаёт `.venv` на Python 3.14, если его нет;
   - ставит зависимости `.[dev,build]` (первый раз ~0,5 ГБ с PyPI, дальше только изменения);
   - прогоняет тесты (`-SkipTests` их пропускает: `build.bat -SkipTests`);
   - собирает `dist\Translater\Translater.exe` через PyInstaller (`--onedir --windowed`);
   - запускает самопроверку exe (`--self-test`, таймаут 3 мин) и при ошибке выводит отчёт и журналы.
3. Окно остаётся открытым до нажатия клавиши.

Тот же `tools\build.ps1` запускает GitHub Actions, поэтому локальная сборка и релиз совпадают.

`.ps1` и `.bat` хранятся с концами строк CRLF (`.gitattributes`). `build.ps1` сохранён в UTF-8 с BOM, иначе Windows
PowerShell 5.1 неправильно читает кириллицу. `build.bat` переключает консоль на UTF-8 (`chcp 65001`).

## Тесты

| Файл | Что проверяет |
|---|---|
| `test_config.py` | Чтение, запись и валидация настроек, откат отдельных полей, конфликты клавиш. |
| `test_models_and_controller.py` | Счётчик ссылок и выгрузка моделей, состояния режимов. |
| `test_components.py` | Загрузка с докачкой, проверка SHA-256, распаковка. |
| `test_audio_and_filters.py` | Нарезка на фразы, ресемплинг, фильтры галлюцинаций и повторов, WAV. |
| `test_incoming_and_process.py` | Входящий runner, RPC и перезапуск процесса моделей. |
| `test_outgoing.py` | Запись, обработка, воспроизведение по рации. |
| `test_speech_model.py` | `SpeechRecognizer` на настоящей модели whisper-tiny (см. ниже). |

`test_speech_model.py` запускается, только если переменная `TRANSLATER_WHISPER_TEST_MODEL` указывает на папку с моделью
CTranslate2 Whisper, а речь для него синтезируется голосом Windows. В CI шаг «Whisper model for tests» скачивает
`Systran/faster-whisper-tiny` по закреплённому коммиту. Тест сверяет результат с `WhisperModel.transcribe` и проверяет,
что кодировщик вызывается один раз. Без переменной тест пропускается.

Тестовые подделки объявляются в самих тестовых файлах. Модели, звук и Win32 в остальных тестах заменены подделками.

## CI и релиз

- `.github/workflows/release.yml` запускается при каждом push в `main`: скачивает whisper-tiny, затем
  `tools\build.ps1 -Python python` (тесты, сборка, самопроверка). Если релиза `v<версия>` ещё нет, workflow
  упаковывает zip и создаёт релиз с тегом на этот коммит.
- Релиз: поднять `version` в `pyproject.toml` (единственное место версии), добавить запись в `docs/CHANGELOG.md` и
  отправить в `main`. Тег вручную не создавать.
- `.github/workflows/manifest.yml` (запуск вручную, параметр `repos`) выводит закреплённый коммит, размеры и SHA-256
  файлов репозиториев Hugging Face (`tools/hf_manifest.py`).

## Частые изменения

### Новая настройка

1. `config.py`: поле `AppConfig` со значением по умолчанию, `to_json`, чтение в `load_config` с откатом на значение
   по умолчанию, проверка в `validate` (сообщение на русском), при необходимости диапазон `*_RANGE`.
2. `ui/settings.py`: виджет, подсказка, строка формы, поле в `_save`.
3. Использование в `app.py`. Читайте `self._config` в момент использования (через лямбду), тогда изменение действует
   сразу после сохранения.
4. Тест в `test_config.py`: запись и чтение, откат неверного значения.
5. Таблица «Настройки» в `README.md`.

### Обновление компонентов

1. Запустить workflow «Model manifest» с нужными репозиториями и перенести коммит, размеры и SHA-256 в
   `components.py`. Колёса NVIDIA: URL, SHA-256 и размер со страницы файла на PyPI.
2. cuDNN должен совпадать с `cudnn64_9.dll` из колеса ctranslate2 (сейчас 9.10.2.21).
3. Изменённый компонент программа сама предложит скачать заново. В его папке лежит маркер с отпечатком манифеста
   (`Component.fingerprint`), и при несовпадении компонент считается неустановленным. Новая версия встаёт в ту же
   папку.

### Обновление faster-whisper или ctranslate2

`SpeechRecognizer` вызывает `WhisperModel.generate_segments`, `encode` и `feature_extractor`, а не только публичный
`transcribe`. После обновления сверьте код `transcribe` в новой версии и проверьте, что `test_speech_model.py` в CI
прошёл (а не был пропущен). Для ctranslate2 — также версию cuDNN (см. выше).

### Новая модель или режим

- Модель: компонент в `components.py`, имя и обработчик в `inference/worker.py`, связь в `_MODEL_COMPONENTS`,
  `MODE_COMPONENTS` и `MODE_MODELS` в `app.py`.
- Режим: значение `Mode` в `controller.py`, runner с методами `start`/`stop` (протокол `ModeRunner`), регистрация в
  `ModeController` и горячая клавиша в `HotkeyConfig`.

## Диагностика

- Журналы: `%LOCALAPPDATA%\Translater\logs\translater.log` (основной процесс) и `worker.log` (процесс моделей).
  Входящий режим раз в 10 с пишет уровень звука и пиковую вероятность речи. Каждая фраза пишет время обработки.
  Тексты фраз и звук в журнал не попадают и попадать не должны.
- Звук игры: пункт трея «Сохранить последние 60 с звука игры» → `logs\game-audio-*.wav` (16 кГц, моно).
  Это тот звук, который получил детектор речи.
- `Translater.exe --self-test report.json` проверяет, что в сборке есть все зависимости и что запускается процесс
  моделей.
