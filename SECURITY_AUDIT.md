# Аудит безопасности

Проверен исходный репозиторий [greatredo/wardogs-navigator](https://github.com/greatredo/wardogs-navigator),
коммит `e251cd65880203720a4fefa1b6cc4400c988aafa` (версия 0.7.4), 4 ноября 2026 года, до любых правок.
Прочитан весь код Python и PowerShell (`main.py`, `wardogs_nav/`, `wardogs_audio/`, `tools/`, `build.ps1`,
`启动导航.cmd`), плюс выполнен поиск по шаблонам: сеть, `ctypes`/WinAPI, `subprocess`, `eval`/`exec`,
`pickle`/`marshal`, base64/zlib, автозапуск, загрузка кода.

**Итог: вредоносного или подозрительного поведения не найдено.** Готовые сборки из Releases
(`WardogsNavigator-Windows-x64.zip` и др.) в этом форке не используются и не запускаются; программа
запускается из исходников или собирается своим `build.ps1` через PyInstaller из PyPI-зависимостей.

## Что делает программа

| Область | Как устроено | Оценка |
|---|---|---|
| Доступ к игре | Только снимок области экрана через `mss` (`worker.py`, `app.py`). Нет `OpenProcess`, `ReadProcessMemory`, хуков, DLL-инъекций, чтения файлов игры. | Безопасно |
| Ввод в игру | Никакой отправки нажатий/мыши (`SendInput`, `keybd_event`, `PostMessage` в чужие окна — отсутствуют). | Безопасно |
| Горячие клавиши | `RegisterHotKey` + опрос `GetAsyncKeyState` **только для назначенных клавиш и модификаторов**, только когда включено (`hotkeys.py`). Нажатия никуда не пишутся. Это не кейлоггер. | Безопасно |
| Оверлеи | Окна PySide6 поверх игры: прозрачны для мыши, `SetWindowPos(HWND_TOPMOST)`, `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` — чтобы собственный оверлей не попадал в снимок мини-карты (`overlay.py`). | Безопасно |
| Самотест | `diagnostics.py` шлёт `WM_HOTKEY` только в **свой** поток (`PostThreadMessageW` на `GetCurrentThreadId`). | Безопасно |
| Внешние процессы | Только системный `powershell.exe -NoProfile -ExecutionPolicy Bypass -File` со скриптами из репозитория: `windows_speech.ps1` (SAPI-синтез в WAV) и `windows_ocr.ps1` (Windows OCR координат). Обмен JSON по stdin/stdout, неизвестные операции отклоняются. | Безопасно |
| Сеть в программе | **В исходном коде нет ни одного сетевого запроса во время работы.** | Безопасно |
| Сеть в утилитах | `tools/fetch_clean_map.py` (запускается вручную разработчиком) качает публичные тайлы карт с `raw.githubusercontent.com/apollyon-sys/wardogs-calculator` и URL из его конфига. Программа его не вызывает. | Допустимо |
| Файлы | Настройки и проекты пишутся в `%LOCALAPPDATA%\WardogsNavigator`; временные WAV — во временный каталог. Автозапуска, реестра, планировщика нет. | Безопасно |
| Динамический код | `eval`/`exec`/`compile` отсутствуют (`dialog.exec()`/`app.exec()` — методы Qt). `__import__('PySide6.QtCore')` в самотесте — обычный импорт. Обфускации, base64/zlib-блобов нет. | Безопасно |
| Десериализация | `np.load(..., allow_pickle=False)` — pickle запрещён. JSON — через `json`. | Безопасно |

## Двоичные файлы в репозитории

Исполняемых бинарников (`.exe`, `.dll`, `.pyd`, `.so`, `.onnx`, архивов) в репозитории **нет**. Есть только данные:

- `assets/**/*.png` — карты и пример мини-карты (проверены как PNG).
- `assets/**/features.npz`, `assets/ozeti-features.npz` — zip-архивы NumPy с двумя массивами `points.npy`
  и `descriptors.npy` (признаки ORB для локализации). Загружаются без pickle.
- `wardogs_audio/assets/default/*.wav` — 112 файлов PCM 16 бит, моно, 24 кГц: китайский офлайн-голос,
  сгенерированный Kokoro-82M (см. `SOURCES.md`). Модель в репозиторий не входит.

Ссылки на внешние модели (`kokoro-v1.0.onnx`) есть только в `tools/make_default_voice_pack.py`
и документации; этот скрипт в форке не используется.

## Изменения форка, влияющие на безопасность

- Добавлен **единственный сетевой вызов**: `wardogs_audio/speechkit.py` → `https://tts.api.cloud.yandex.net/tts/v3/utteranceSynthesis`.
  Выполняется только если задан ключ SpeechKit; отправляется только текст фразы подсказки.
  Ответ кэшируется в `%LOCALAPPDATA%\WardogsNavigator\speechkit-cache`, повторно не запрашивается.
- Ключ читается из переменной `YANDEX_SPEECHKIT_API_KEY` или из `%LOCALAPPDATA%\WardogsNavigator\speechkit.json`
  (вне репозитория; `speechkit.json` также в `.gitignore`). В репозитории ключей нет.

## Как перепроверить

```powershell
git grep -nE "socket|urllib|requests|http|ctypes|subprocess|eval\(|exec\(|pickle|base64|zlib|SendInput|ReadProcessMemory"
```
