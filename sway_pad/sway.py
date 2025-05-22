#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Sway-Pad is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

import curses
import locale
import toml
import os
import re
import queue
import shlex
import sys
import time
import pyperclip
import logging
import chardet
import unicodedata
import codecs
import traceback
import subprocess
import tempfile
import threading
import termios
import curses.ascii

from pygments import lex
from pygments.lexer import RegexLexer
from pygments.lexers import get_lexer_by_name, guess_lexer, TextLexer
from pygments.token import Token, Comment, Name, Punctuation
from wcwidth import wcwidth, wcswidth
from typing import Callable, Dict, Optional, List, Any


# Установка кодировки по умолчанию
def _set_default_encoding():
    """Устанавливает кодировку по умолчанию для Python 3.8 и выше."""
    # Эти переменные среды помогают обеспечить корректную работу ввода/вывода
    # с UTF-8 в разных окружениях, особенно важно для взаимодействия с subprocess.
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONLEGACYWINDOWSSTDIO", "1") # Для совместимости на Windows

_set_default_encoding()

# Функция для рекурсивного объединения словарей
def deep_merge(base: dict, override: dict) -> dict:
    """
    Рекурсивно объединяет словарь 'override' в словарь 'base', возвращая новый словарь.
    """
    result = dict(base)
    for k, v in override.items():
        if (
            k in result
            and isinstance(result[k], dict)
            and isinstance(v, dict)
        ):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# --- безопасный запуск внешних команд ---------------------------------------
def safe_run(cmd: list[str], cwd: str | None = None, **kwargs) -> subprocess.CompletedProcess:
    """ Функция для безопасного выполнения команд в subprocess"""
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        encoding='utf-8',
        errors='replace',
        **kwargs
    )


# Функция для получения иконки файла
def get_file_icon(filename: str, config: dict) -> str:
    """
    Возвращает иконку для файла на основе его расширения, согласно конфигурации.
    """
    if not filename:
        return config.get("file_icons", {}).get("text", "📝") # Иконка по умолчанию для новых файлов

    file_lower = filename.lower()
    # Получаем конфигурацию иконок и форматов, используя .get для безопасности
    file_icons = config.get("file_icons", {})
    supported_formats = config.get("supported_formats", {})

    if not file_icons or not supported_formats:
        return "📝" # Fallback для отсутствующей конфигурации

    # Проверяем каждое расширение в supported_formats
    for key, extensions in supported_formats.items():
        # Преобразуем extensions в кортеж для efficient endswith
        if isinstance(extensions, list):
             ext_tuple = tuple(f".{ext.lower()}" for ext in extensions)
             if file_lower.endswith(ext_tuple):
                 return file_icons.get(key, "📝") # Возвращаем иконку по ключу или дефолт

    # Если ни одно расширение не совпало, возвращаем иконку для текста
    return file_icons.get("text", "📝")


# Функция для получения информации о Git (ветка, имя пользователя, количество коммитов)
# Используется только для инициализации в __init__, асинхронная версия _fetch_git_info_async предпочтительнее.
def get_git_info(file_path: str) -> tuple[str, str, str]:
    """
    Синхронно получает базовую информацию о Git-репозитории.
    Используется ТОЛЬКО при инициализации.
    """
    repo_dir = os.path.dirname(os.path.abspath(file_path)) if file_path and os.path.exists(file_path) else os.getcwd()

    # Проверяем наличие .git без возбуждения исключения
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        logging.debug(f"No .git directory found in {repo_dir} for sync git info")
        return "", "", "0"

    branch = ""
    user_name = ""
    commits = "0"

    try:
        # 1. Определяем ветку
        try:
            result_branch = subprocess.run(
                ["git", "branch", "--show-current"],
                capture_output=True, text=True, check=True, cwd=repo_dir, encoding='utf-8', errors='replace'
            )
            branch = result_branch.stdout.strip() if result_branch.returncode == 0 else ""
        except subprocess.CalledProcessError:
            # fallback: git symbolic-ref (для старых версий git или detached HEAD)
            try:
                 result_branch = subprocess.run(
                    ["git", "symbolic-ref", "--short", "HEAD"],
                    capture_output=True, text=True, check=True, cwd=repo_dir, encoding='utf-8', errors='replace'
                )
                 branch = result_branch.stdout.strip() if result_branch.returncode == 0 else ""
            except subprocess.CalledProcessError:
                branch = "main" # Дефолт, если не удалось определить

        # 2. Грязный репозиторий ?
        try:
            result_dirty = subprocess.run(
                ["git", "status", "--short"],
                capture_output=True, text=True, check=True, cwd=repo_dir, encoding='utf-8', errors='replace'
            )
            if result_dirty.returncode == 0 and result_dirty.stdout.strip():
                branch += "*"
        except subprocess.CalledProcessError:
             logging.warning(f"Git status failed during sync info for {repo_dir}")
             pass # Не крашимся, если статус не сработал

        # 3. Имя пользователя
        try:
            result_user = subprocess.run(
                ["git", "config", "user.name"],
                capture_output=True, text=True, check=True, cwd=repo_dir, encoding='utf-8', errors='replace'
            )
            user_name = result_user.stdout.strip() if result_user.returncode == 0 else ""
        except subprocess.CalledProcessError:
             logging.warning(f"Git config user.name failed during sync info for {repo_dir}")
             user_name = ""

        # 4. Кол-во коммитов
        try:
            result_commits = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                capture_output=True, text=True, check=True, cwd=repo_dir, encoding='utf-8', errors='replace'
            )
            commits = result_commits.stdout.strip() if result_commits.returncode == 0 else "0"
        except subprocess.CalledProcessError:
            logging.warning(f"Git rev-list --count HEAD failed during sync info for {repo_dir}")
            commits = "0" # Дефолт

    except FileNotFoundError:
        logging.warning("Git executable not found during sync info")
        return "", "", "0"
    except Exception as e:
        logging.error(f"Unexpected error fetching sync git info for {repo_dir}: {e}")
        return "", "", "0"


    logging.debug(f"Fetched sync Git info: {(branch, user_name, commits)}")
    return branch, user_name, commits


# Загрузка конфигурации
def load_config() -> dict:
    """
    Загружает конфигурацию из 'config.toml', используя минимальные значения по умолчанию,
    если файл не найден или некорректен.
    """
    # Убедитесь, что минимальные дефолты содержат все необходимые секции и ключи
    minimal_default = {
        "colors": {
            "error": "red",
            "status": "bright_white", # curses.COLOR_WHITE + curses.A_BOLD будет использоваться
            "green": "green"          # Для Git-информации
        },
        # Font Family и Size в curses не напрямую, это скорее мета-информация или для GUI
        "fonts": {"font_family": "monospace", "font_size": 12},
        "keybindings": {
            "delete": "del",
            "paste": "ctrl+v",
            "copy": "ctrl+c",
            "cut": "ctrl+x",
            "undo": "ctrl+z",
            "redo": "shift+z", 
            "lint": "f4",
            "new_file": "f2",       
            "open_file": "ctrl+o",
            "save_file": "ctrl+s",
            "save_as": "f5",         
            "select_all": "ctrl+a",
            "quit": "ctrl+q",
            "goto_line": "ctrl+g",   
            "git_menu": "f9",
            "cancel_operation": "esc",
            "find": "ctrl+f", 
            "find_next": "f3",
            "search_and_replace": "f6",
            "help": "f1"     
        },
        "editor": {
            "use_system_clipboard": True,
            "default_new_filename": "new_file.py",
            "tab_size": 4,           
            "use_spaces": True
        },
        "file_icons": { 
            "python": "🐍",
            "toml": "❄️", # Добавлено сопоставление для toml
            "javascript": "📜", # js, mjs, cjs, jsx
            "typescript": "📑", # ts, tsx
            "php": "🐘", # PHP часто ассоциируют со слоном, 📑 уже занят
            "ruby": "♦️", # Иконка рубина, 🪀 - это йо-йо
            "css": "🎨",  # css
            "html": "🌐",  # html, htm
            "json": "📊",
            "yaml": "⚙️",  # yml, yaml
            "xml": "📰",   # 📑 занят, используем газету для структурированных данных
            "markdown": "📋", # md, markdown
            "text": "📝",     # txt, log, rst (добавим rst сюда)
            "shell": "💫",    # sh, bash, zsh, ksh, fish
            "dart": "🎯",     # Дартс для Dart, 📱 больше для Kotlin/Swift
            "go": "🐹",       # Талисман Go - суслик (gopher)
            "c": "🇨",        # Флаг C (просто буква C)
            "cpp": "🇨➕",     # C++
            "java": "☕",
            "julia": "🧮",
            "rust": "🦀",     # Талисман Rust - краб Феррис
            "csharp": "♯",    # Символ диеза
            "scala": "💎",
            "r": "📉",
            "swift": "🐦",     # Птичка для Swift
            "dockerfile": "🐳",
            "terraform": "🛠️", # Инструменты для Terraform, ⚙️ уже занят
            "jenkins": "🧑‍✈️",   # Пилот для Jenkins (старый логотип был дворецкий)
            "puppet": "🎎",    # Куклы для Puppet
            "saltstack": "🧂", # Солонка для SaltStack
            "git": "🔖",      # Файлы, связанные с Git (.gitignore, .gitattributes)
            "notebook": "📒", # .ipynb
            "diff": "↔️",     # Стрелки для diff, 📼 - видеокассета
            "makefile": "🛠️", # Makefile тоже инструменты
            "ini": "🔩",      # Болт для ini, ⚙️ занят
            "csv": "📊",      # Повтор для CSV, можно 🗂️ (картотека)
            "sql": "💾",      # Дискета для SQL, 📊 занят
            "graphql": "📈",
            "kotlin": "📱",
            "lua": "🌙",      # Луна для Lua
            "perl": "🐪",      # Верблюд для Perl
            "powershell": "💻", # PowerShell - консоль
            "nix": "❄️",      # nix файлы
            "image": "🖼️",    # jpg, jpeg, png, gif, bmp, svg, webp
            "audio": "🎵",    # mp3, wav, ogg, flac
            "video": "🎞️",    # mp4, mkv, avi, mov, webm
            "archive": "📦",  # zip, tar, gz, rar, 7z
            "font": "🖋️",     # ttf, otf, woff, woff2
            "binary": "⚙️",    # .exe, .dll, .so, .o, .bin, .app (если не подошло другое)
            "document": "📄",  # .doc, .docx, .odt, .pdf, .ppt, .pptx, .odp
            "folder": "📁",   # Иконка для директорий (не используется get_file_icon, но полезна для файловых менеджеров)
            "folder_open": "📂", # Аналогично
            "default": "❓"   # Иконка по умолчанию, если ничего не подошло
        },
        "supported_formats": { 
            "python": ["py", "pyw", "pyc", "pyd"], # pyc/pyd - бинарные, но связаны с python
            "toml": ["toml"],
            "javascript": ["js", "mjs", "cjs", "jsx"],
            "typescript": ["ts", "tsx", "mts", "cts"],
            "php": ["php", "php3", "php4", "php5", "phtml"],
            "ruby": ["rb", "rbw", "gemspec"],
            "css": ["css"],
            "html": ["html", "htm", "xhtml"],
            "json": ["json", "jsonc", "geojson", "webmanifest"],
            "yaml": ["yaml", "yml"],
            "xml": ["xml", "xsd", "xsl", "xslt", "plist", "rss", "atom", "csproj", "svg"], # SVG тоже XML
            "markdown": ["md", "markdown", "mdown", "mkd"],
            "text": ["txt", "log", "rst", "srt", "sub", "me", "readme"],
            "shell": ["sh", "bash", "zsh", "ksh", "fish", "command", "tool"],
            "dart": ["dart"],
            "go": ["go"],
            "c": ["c", "h"], # .h могут быть и для C++
            "cpp": ["cpp", "cxx", "cc", "hpp", "hxx", "hh", "inl", "tpp"],
            "java": ["java", "jar", "class"], # class/jar - бинарные
            "julia": ["jl"],
            "rust": ["rs", "rlib"],
            "csharp": ["cs"],
            "scala": ["scala", "sc"],
            "r": ["r", "rds", "rda"],
            "swift": ["swift"],
            "dockerfile": ["dockerfile"], # Имя файла часто "Dockerfile" без расширения
            "terraform": ["tf", "tfvars"],
            "jenkins": ["jenkinsfile", "groovy"], # Jenkinsfile часто groovy
            "puppet": ["pp"],
            "saltstack": ["sls"],
            "git": ["gitignore", "gitattributes", "gitmodules", "gitkeep"], # Имена файлов без расширений
            "notebook": ["ipynb"],
            "diff": ["diff", "patch"],
            "makefile": ["makefile", "mk", "mak"], # Имена файлов без расширений
            "ini": ["ini", "cfg", "conf", "properties", "editorconfig"],
            "csv": ["csv", "tsv"], # tsv тоже табличный
            "sql": ["sql"],
            "graphql": ["graphql", "gql"],
            "kotlin": ["kt", "kts"],
            "lua": ["lua"],
            "perl": ["pl", "pm", "t", "pod"],
            "powershell": ["ps1", "psm1", "psd1"],
            "nix": ["nix"],
            "image": ["jpg", "jpeg", "png", "gif", "bmp", "ico", "webp", "tiff", "tif", "heic", "heif"],
            "audio": ["mp3", "wav", "ogg", "flac", "aac", "m4a", "wma"],
            "video": ["mp4", "mkv", "avi", "mov", "webm", "flv", "wmv"],
            "archive": ["zip", "tar", "gz", "tgz", "bz2", "rar", "7z", "xz", "iso", "deb", "rpm", "pkg"],
            "font": ["ttf", "otf", "woff", "woff2", "eot"],
            "binary": ["exe", "dll", "so", "o", "bin", "app", "com", "msi", "dmg"],
            "document": ["doc", "docx", "odt", "rtf", "pdf", "ppt", "pptx", "odp", "xls", "xlsx", "ods", "epub", "mobi"]
            # 'folder' и 'folder_open' не используются в get_file_icon по расширению,
            # они больше для отображения в файловом менеджере.
        },
        "git": {
            "enabled": True # Включает/выключает интеграцию с Git
        },
         "settings": {
            "auto_save_interval": 1, # Интервал автосохранения в минутах (0 для отключения)
            "show_git_info": True # Отображать Git информацию в статус-баре
        }
    }

    config_path = "config.toml"
    user_config = {}

    # Пытаемся загрузить пользовательский конфиг
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                file_content = f.read()
                user_config = toml.loads(file_content)
                logging.debug(f"Loaded user config from {config_path}")
        except FileNotFoundError:
            logging.warning(f"Config file '{config_path}' not found. Using minimal defaults.")
        except toml.TomlDecodeError as e:
            logging.error(f"TOML parse error in {config_path}: {str(e)}")
            logging.error("Falling back to minimal defaults.")
        except Exception as e:
            logging.error(f"Unexpected error reading {config_path}: {str(e)}")
            logging.error("Falling back to minimal defaults.")
    else:
        logging.warning(f"Config file '{config_path}' not found. Using minimal defaults.")


    # Объединяем дефолты с пользовательской конфигурацией
    # Глубокое слияние сохраняет подсловари пользователя
    final_config = deep_merge(minimal_default, user_config)

    # Дополнительная проверка для ключевых секций (можно убрать если deep_merge всегда создает их)
    for key, default_section in minimal_default.items():
        if key not in final_config:
            final_config[key] = default_section
        elif isinstance(default_section, dict): # Убедимся, что все ключи из дефолтной секции есть
            for sub_key, sub_val in default_section.items():
                 if sub_key not in final_config[key]:
                      final_config[key][sub_key] = sub_val

    logging.debug("Final config loaded successfully")
    return final_config


# --- Настройка логгирования ------------------------------------------------
# Улучшенная конфигурация логгирования
log_file = "editor.log"
# Убедимся, что директория для лога существует, если указан путь
log_dir = os.path.dirname(log_file)
if log_dir and not os.path.exists(log_dir):
    try:
        os.makedirs(log_dir)
    except OSError as e:
        print(f"Error creating log directory {log_dir}: {e}", file=sys.stderr)
        # Если не можем создать директорию, используем временную
        log_file = os.path.join(tempfile.gettempdir(), "sway2_editor.log")
        print(f"Logging to temporary file: {log_file}", file=sys.stderr)

logging.basicConfig(
    filename=log_file,
    level=logging.DEBUG, # Уровень логгирования
    format="%(asctime)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)",
    force=True, # Переопределить существующие обработчики, если есть
)

logger = logging.getLogger(__name__)

# Логгер для событий клавиатуры (опционально)
KEY_LOGGER = logging.getLogger("sway2.keyevents")
KEY_LOGGER.propagate = False # Отключить передачу в корневой логгер
KEY_LOGGER.setLevel(logging.DEBUG)

# Обработчик для записи клавиш в отдельный файл, если включено
if os.environ.get("SWAY2_KEYTRACE", "").lower() in {"1", "true", "yes"}:
    try:
        from logging.handlers import RotatingFileHandler
        key_trace_file = "keytrace.log"
        key_trace_dir = os.path.dirname(key_trace_file)
        if key_trace_dir and not os.path.exists(key_trace_dir):
             os.makedirs(key_trace_dir)
        handler = RotatingFileHandler(key_trace_file, maxBytes=1_000_000, backupCount=3)
        handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s")) # Упрощенный формат
        KEY_LOGGER.addHandler(handler)
        KEY_LOGGER.propagate = True # Включить для записи
        KEY_LOGGER.info("Key tracing enabled.")
    except Exception as e:
        logging.error(f"Failed to set up key trace logging: {e}")
        KEY_LOGGER.disabled = True # Отключить, если возникла ошибка
else:
    KEY_LOGGER.addHandler(logging.NullHandler()) # Не писать никуда
    KEY_LOGGER.disabled = True


# --- Класс редактора --------------------------------------------------------
class SwayEditor:
    """The main class of the Sway-Pad editor."""
       
    def _set_status_message(self, 
                            message_for_statusbar: str, 
                            is_lint_status: bool = False, 
                            full_lint_output: Optional[str] = None,
                            activate_lint_panel_if_issues: bool = False):
        """
        Устанавливает статусное сообщение. 
        Для сообщений линтера также обновляет содержимое панели линтера.

        :param message_for_statusbar: Сообщение для отображения в статус-баре.
        :param is_lint_status: True, если это статусное сообщение от линтера.
        :param full_lint_output: Полный вывод линтера для панели (используется, если is_lint_status=True).
                                 Если None и is_lint_status=True, self.lint_panel_message не изменяется.
        :param activate_lint_panel_if_issues: Если True и is_lint_status=True, 
                                              панель линтера будет активирована, если full_lint_output 
                                              не является сообщением "Нет проблем".
        """
        if is_lint_status:
            # Обновляем содержимое панели линтера, если предоставлен полный вывод
            if full_lint_output is not None:
                self.lint_panel_message = full_lint_output
                logging.debug(f"Сообщение панели линтера обновлено: '{str(full_lint_output)[:100]}...'")
            
            # Сообщение для статус-бара, связанное с линтером
            # Его можно отображать иначе или в специальном месте, если это предусмотрено
            # Для простоты, пока будем логировать и предполагать, что статус-бар его как-то учтет
            # (например, _draw_status_bar() может проверить self.current_lint_statusbar_message)
            # self.current_lint_statusbar_message = f"[LINT] {message_for_statusbar}" 
            # Либо просто передаем его в общую очередь, как и обычные сообщения
            
            logging.debug(f"Статусное сообщение линтера: {message_for_statusbar}")
            # Поместим его в обычную очередь, чтобы оно отобразилось в статус-баре
            # Можно добавить префикс, чтобы визуально отличать
            try:
                # Можно добавить префикс, чтобы отличать сообщения линтера в статус-баре
                # self._msg_q.put_nowait(f"[Линтер] {message_for_statusbar}")
                self._msg_q.put_nowait(str(message_for_statusbar)) # Или без префикса
                if not hasattr(self, "_last_status_msg_sent"): self._last_status_msg_sent = None # Инициализация если нет
                self._last_status_msg_sent = message_for_statusbar # Обновляем последнее отправленное
            except queue.Full:
                logging.error("Очередь статусных сообщений переполнена (сообщение линтера).")
            except Exception as e:
                logging.error(f"Не удалось добавить статусное сообщение линтера в очередь: {e}")

            # Активируем панель, если есть что показать и activate_lint_panel_if_issues=True
            # и сообщение не является "Нет проблем"
            if activate_lint_panel_if_issues and self.lint_panel_message:
                # Простое условие "нет проблем" - можно сделать более надежным
                no_issues_messages = [
                    "Flake8: Нет проблем.", 
                    "Flake8: No issues found." # Добавим англ. вариант на всякий случай
                ]
                if self.lint_panel_message.strip() not in no_issues_messages:
                    self.lint_panel_active = True
                    # self.show_lint_panel() # Если show_lint_panel только меняет флаг, то это избыточно.
                                          # Если он делает что-то еще (например, фокусирует), то нужно.
                    logging.debug("Панель линтера активирована из-за наличия проблем.")
                else:
                    # Если проблем нет, панель можно деактивировать или оставить как есть
                    # self.lint_panel_active = False 
                    logging.debug("Проблем линтинга не найдено, панель не активируется (или деактивируется).")
            elif self.lint_panel_message and not self.lint_panel_active and is_lint_status:
                # Если это сообщение линтера, и оно не пустое, и панель еще не активна,
                # но activate_lint_panel_if_issues=False, то панель не активируем автоматически.
                # Это для случаев, когда мы просто хотим обновить сообщение, но не обязательно сразу показывать.
                pass

        else: # Обычное статусное сообщение
            if not hasattr(self, "_last_status_msg_sent"):
                self._last_status_msg_sent = None
            # Предотвращаем дублирование подряд идущих одинаковых сообщений
            if message_for_statusbar == self._last_status_msg_sent:
                return 
            try:
                self._msg_q.put_nowait(str(message_for_statusbar))
                self._last_status_msg_sent = message_for_statusbar # Обновляем для обычных сообщений
                logging.debug(f"Сообщение в очереди статуса: {message_for_statusbar}")
            except queue.Full:
                logging.error("Очередь статусных сообщений переполнена.")
            except Exception as e:
                logging.error(f"Не удалось добавить сообщение в очередь статуса: {e}")


    def __init__(self, stdscr):
        """
        Initialize the editor. Called from curses.wrapper().
        """
        # ─────────────── Настройка терминала: отключаем IXON/IXOFF и canonical mode ───────────────
        try:
            fd = sys.stdin.fileno()
            termios_attrs = termios.tcgetattr(fd)
            termios_attrs[0] &= ~(termios.IXON | termios.IXOFF)  # отключить Ctrl+S / Ctrl+Q (flow control)
            termios_attrs[3] &= ~termios.ICANON  # отключить canonical mode
            termios.tcsetattr(fd, termios.TCSANOW, termios_attrs)
            logging.debug("IXON/IXOFF and ICANON disabled – Ctrl+S/Q/Z now usable")
        except Exception as e:
            logging.warning("Couldn't set terminal attributes: %s", e)

        # ─────────────── Базовая инициализация curses ────────────────────
        self.stdscr = stdscr
        self.stdscr.keypad(True)  # Включаем поддержку спец.клавиш
        curses.raw()              # Немедленный ввод, отключает canonical mode
        curses.noecho()           # Не отображать ввод
        curses.curs_set(1)        # Видимый курсор

        # ─────────────── Настройки и буферы (Load config first) ───────────────
        try:
            self.config = load_config()
        except Exception as e:
            logging.error(f"Failed to load config: {e}. Using minimal defaults.")
            self.config = {
                "editor": {"use_system_clipboard": True, "tab_size": 4, "use_spaces": True},
                "keybindings": {},
                "git": {"enabled": True},
                "settings": {"auto_save_interval": 1, "show_git_info": True},
                "file_icons": {"text": "📝"},
                "supported_formats": {}
            }

        self.colors: dict[str, int] = {}
        self.init_colors()

        self.use_system_clipboard = self.config.get("editor", {}).get("use_system_clipboard", True)
        self.pyclip_available = self._check_pyclip_availability()
        if not self.pyclip_available and self.use_system_clipboard:
            logging.warning("pyclip unavailable. System clipboard disabled.")
            self.use_system_clipboard = False
        self.internal_clipboard = ""

        self._auto_save_thread = None
        self._auto_save_enabled = False
        self._auto_save_interval = self.config.get("settings", {}).get("auto_save_interval", 1)
        if not isinstance(self._auto_save_interval, (int, float)) or self._auto_save_interval < 0:
            logging.warning(f"Invalid auto_save_interval config value: {self._auto_save_interval}. Using default 1 min.")
            self._auto_save_interval = 1

        self.insert_mode = True
        self.status_message = ""
        self._msg_q = queue.Queue()
        self.lint_panel_message = None   # Сообщение панели линтера
        self.lint_panel_active = False   # Флаг активности панели линтера
        self.action_history = []
        self.undone_actions = []
        self._state_lock = threading.RLock()
        self._shell_cmd_q = queue.Queue()
        self._git_q = queue.Queue()
        self._git_cmd_q = queue.Queue()

        self.text = [""]
        self.cursor_x = 0
        self.cursor_y = 0
        self.scroll_top = 0
        self.scroll_left = 0
        self.modified = False
        self.encoding = "UTF-8"
        self.filename = None
        self.selection_start = None
        self.selection_end = None
        self.is_selecting = False

        self.search_term = ""
        self.search_matches = []
        self.current_match_idx = -1
        self.highlighted_matches = []

        self.git_info = ("", "", "0")
        self._last_git_filename = None
        self._lexer = None
        self._token_cache = {}

        self.visible_lines = 0
        self.last_window_size = (0, 0)
        self.drawer = DrawScreen(self)

        try:
            # Отладочная печать перед вызовом _load_keybindings
            print(f"DEBUG: About to call _load_keybindings. self.parse_key exists: {hasattr(self, 'parse_key')}")
            if hasattr(self, 'parse_key'):
                print(f"DEBUG: self.parse_key is: {self.parse_key}")
            else:
                print(f"DEBUG: self.parse_key DOES NOT EXIST before _load_keybindings!")
                # Дополнительно, можно посмотреть все атрибуты self, чтобы понять, что там есть
                # print(f"DEBUG: dir(self) before _load_keybindings: {dir(self)}")


            self.keybindings = self._load_keybindings() # Строка 452 в вашем логе
            print("DEBUG: _load_keybindings called successfully.")

        except Exception as e_init_kb:
            print(f"CRITICAL DEBUG: Error during keybinding setup in __init__: {e_init_kb}")
            import traceback
            traceback.print_exc() # Напечатает traceback прямо в консоль
            # Это поможет увидеть ошибку, даже если curses еще не инициализирован полностью
            # или если логгирование еще не работает как надо для этой стадии.
            raise # Перевыбрасываем ошибку, чтобы программа завершилась

        self.keybindings = self._load_keybindings()
        self.action_map = self._setup_action_map()

        self.set_initial_cursor_position()

        if self.config.get("git", {}).get("enabled", True):
            logging.debug("Git integration enabled, fetching initial sync info.")
            try:
                self.git_info = get_git_info(self.filename if self.filename else os.getcwd())
                logging.debug(f"Initial sync git info fetched: {self.git_info}")
            except Exception as e:
                self.git_info = ("", "", "0")
                logging.error(f"Failed to get initial sync Git info during init: {e}")
        else:
            self.git_info = ("", "", "0")
            logging.debug("Git integration disabled, git_info set to default.")

        try:
            locale.setlocale(locale.LC_ALL, "")
            logging.debug(f"Locale set to {locale.getlocale()}")
        except locale.Error as e:
            logging.error(f"Failed to set locale: {e}. May affect character width and encoding.")

        logging.info("SwayEditor initialized successfully")


    # ─────────────────────  Инициализация клавиатурных привязок  ─────────────────────
    def _load_keybindings(self) -> dict[str, int | str]: # Возвращаемый тип может быть просто dict[str, int] теперь
        """
        Читает раздел [keybindings] из config.toml и формирует
        словарь {action: key_code}.
        """
        defaults = {
            "delete":        "del",
            "paste":         "ctrl+v",
            "copy":          "ctrl+c",
            "cut":           "ctrl+x",
            "undo":          "ctrl+z",
            "redo":          "shift+z",
            "new_file":      "f2",
            "open_file":     "ctrl+o",
            "save_file":     "ctrl+s",
            "save_as":       "f5",
            "select_all":    "ctrl+a",
            "quit":          "ctrl+q", # Ctrl+Q может быть занят терминалом (XOFF)
            "goto_line":     "ctrl+g",
            "git_menu":      "f9",
            "help":          "f1",
            "find":          "ctrl+f",
            "find_next":     "f3",
            "search_and_replace": "f6",
            "cancel_operation": "esc",
            "tab":           "tab",
            "shift_tab":     "shift+tab",  # Для Shift+Tab (unindent)
            "lint":          "f4", # Переименовано из show_lint_panel, если действие 'lint'
            "comment_selected_lines": "ctrl+/",    # Комментирование
            "uncomment_selected_lines": "shift+/", # Раскомментирование
        }
        cfg = self.config.get("keybindings", {})
        kb: dict[str, int] = {}

        for action, def_key_str in defaults.items():
            key_str_config = cfg.get(action, def_key_str)

            if not key_str_config:
                logging.debug(f"Привязка для '{action}' отключена пользователем.")
                continue
            
            try:
                # _decode_keystring должен уметь парсить "ctrl+/" и "shift+/"
                # Для "shift+/" это будет ord('/') если Shift не меняет символ,
                # или ord('?') если Shift+/ дает '?'.
                # Если Shift+/ дает уникальный код, _decode_keystring должен его знать.
                code = self._decode_keystring(key_str_config) 
                kb[action] = code
            except ValueError as e:
                logging.error(f"Ошибка привязки клавиши [{action}]={key_str_config!r}: {e}")

        logging.debug(f"Загруженные пользовательские/дефолтные привязки (действие -> код): {kb}")
        return kb


    def _decode_keystring(self, key_str_config: str) -> int:
        if isinstance(key_str_config, int): 
            return key_str_config

        s_orig = key_str_config 
        key_str_lower = key_str_config.strip().lower() 
        
        if not key_str_lower:
            raise ValueError("Пустая строка для горячей клавиши")

        named_keys: dict[str, int] = {
            'f1': curses.KEY_F1,  'f2': curses.KEY_F2,  'f3': curses.KEY_F3,
            'f4': getattr(curses, 'KEY_F4', 268),  'f5': getattr(curses, 'KEY_F5', 269),  'f6': getattr(curses, 'KEY_F6', 270),
            'f7': getattr(curses, 'KEY_F7', 271),  'f8': getattr(curses, 'KEY_F8', 272),  'f9': getattr(curses, 'KEY_F9', 273),
            'f10': getattr(curses, 'KEY_F10', 274),'f11': getattr(curses, 'KEY_F11', 275),'f12': getattr(curses, 'KEY_F12', 276),
            'left': curses.KEY_LEFT,   'right': curses.KEY_RIGHT,
            'up': curses.KEY_UP,       'down': curses.KEY_DOWN,
            'home': curses.KEY_HOME,   'end': getattr(curses, 'KEY_END', 360), # Может быть KEY_LL
            'pageup': curses.KEY_PPAGE,  'pgup': curses.KEY_PPAGE,
            'pagedown': curses.KEY_NPAGE, 'pgdn': curses.KEY_NPAGE,
            'delete': curses.KEY_DC,   'del': curses.KEY_DC,
            'backspace': curses.KEY_BACKSPACE, # Обычно 263 в keypad(True)
            'insert': getattr(curses, 'KEY_IC', 331), # KEY_IC Insert Char
            'tab': ord('\t'), # curses.KEY_TAB (обычно 9)
            'enter': curses.KEY_ENTER, # Обычно 10, но KEY_ENTER более переносим
            'return': curses.KEY_ENTER,
            'space': ord(' '),
            'esc': 27, # curses.KEY_EXIT или 27
            'escape': 27,
            'shift+left': curses.KEY_SLEFT,
            'shift+right': curses.KEY_SRIGHT,
            'shift+up': getattr(curses, 'KEY_SR', 337), # Shift+Up (KEY_SPREVIOUS в некоторых)
            'shift+down': getattr(curses, 'KEY_SF', 336), # Shift+Down (KEY_SNEXT в некоторых)
            'shift+home': curses.KEY_SHOME,
            'shift+end': curses.KEY_SEND,
            'shift+pgup': getattr(curses, 'KEY_SPREVIOUS', 337), 
            'shift+pgdn': getattr(curses, 'KEY_SNEXT', 336),    
            'shift+tab': getattr(curses, 'KEY_BTAB', 353), # Back Tab (Shift+Tab)
            # Слеш как базовая клавиша
            '/': ord('/'), 
            # Если Shift+/ дает другой символ, например '?', его тоже можно добавить:
            '?': ord('?'),
        }

        if key_str_lower in named_keys:
            return named_keys[key_str_lower]

        parts = key_str_lower.split('+')
        base_key_str = parts[-1]    
        modifiers = set(parts[:-1]) 

        base_code: int
        if base_key_str in named_keys: 
            base_code = named_keys[base_key_str]
        elif len(base_key_str) == 1:   
            # Если есть Shift и это буква, то берем ord() от ЗАГЛАВНОЙ буквы
            if "shift" in modifiers and 'a' <= base_key_str <= 'z':
                base_code = ord(base_key_str.upper()) 
                modifiers.remove("shift") # Модификатор Shift уже учтен

            else:
                base_code = ord(base_key_str) 
        else:
            raise ValueError(f"Неизвестная базовая клавиша '{base_key_str}' в '{s_orig}'")

        if "ctrl" in modifiers:
            if 'a' <= chr(base_code).lower() <= 'z':
                char_val_for_ctrl = ord(chr(base_code).lower())
                final_code = char_val_for_ctrl - ord('a') + 1
                if "shift" in parts[:-1]: 
                    base_code = final_code | 0x100 
                else:
                    base_code = final_code
            elif base_code == ord('/'): # Для Ctrl+/
                pass # base_code уже ord('/')

        # Если это "shift+/" и не Ctrl
        elif "shift" in modifiers and base_code == ord('/'):
            pass # base_code остается ord('/')

        if "alt" in modifiers:
            base_code |= 0x200 
            logging.debug(f"Применен Alt-модификатор к '{s_orig}', результат кода: {base_code}")

        return base_code

    # ─────────────────────  Настройка сопоставления действий  ─────────────────────
    def _setup_action_map(self) -> dict[int, Callable[..., Any]]:
        action_method_map: dict[str, Callable] = {
            "open_file":  self.open_file,
            "save_file":  self.save_file,
            "save_as":    self.save_file_as,
            "new_file":   self.new_file,
            "git_menu":   self.integrate_git,
            "copy": self.copy,
            "cut":  self.cut,
            "paste": self.paste,
            "undo": self.undo,
            "redo": self.redo,
            "handle_home":     self.handle_home,
            "handle_end":      self.handle_end,
            "show_lint_panel": self.show_lint_panel, 
            "extend_selection_right": self.extend_selection_right,
            "extend_selection_left":  self.extend_selection_left,
            "select_to_home": self.select_to_home,
            "select_to_end":  self.select_to_end,
            "extend_selection_up":   self.extend_selection_up,
            "extend_selection_down": self.extend_selection_down,
            "find": self.find_prompt,
            "find_next": self.find_next,
            "search_and_replace": self.search_and_replace,
            "goto_line": self.goto_line,
            "help": self.show_help,
            "cancel_operation": self.cancel_operation,
            "select_all": self.select_all,
            "delete": self.handle_delete,
            "quit": self.exit_editor,
            "tab": self.handle_smart_tab,
            "shift_tab": self.handle_smart_unindent,
            "lint": self.run_lint_async, 
            "comment_selected_lines": self.do_comment_block,   # Связываем действие с методом
            "uncomment_selected_lines": self.do_uncomment_block, # Связываем действие с методом
        }

        final_map: dict[int, Callable] = {}

        for action_name, key_code_from_kb in self.keybindings.items(): # Переименовал для ясности
            method = action_method_map.get(action_name)
            if method:
                if key_code_from_kb is not None:
                    if key_code_from_kb in final_map and final_map[key_code_from_kb] != method:
                        logging.warning(
                            f"Конфликт привязки клавиш! Код {key_code_from_kb} для '{action_name}' "
                            f"(метод {method.__name__}) уже занят методом "
                            f"{final_map[key_code_from_kb].__name__}. "
                            f"Привязка для '{action_name}' будет перезаписана." # Изменено сообщение
                        )
                    final_map[key_code_from_kb] = method
                    logging.debug(f"Сопоставление из конфига: Действие '{action_name}' (код {key_code_from_kb}) -> Метод {method.__name__}")
            else:
                logging.warning(f"Действие '{action_name}' из keybindings не найдено в action_method_map.")
        
        # Встроенные «по умолчанию» curses-константы
        builtin_key_to_method: dict[int, Callable] = {
            curses.KEY_UP:      self.handle_up,
            curses.KEY_DOWN:    self.handle_down,
            curses.KEY_LEFT:    self.handle_left,
            curses.KEY_RIGHT:   self.handle_right,
            curses.KEY_PPAGE:   self.handle_page_up,
            curses.KEY_NPAGE:   self.handle_page_down,
            curses.KEY_BACKSPACE: self.handle_backspace, 
            # curses.KEY_DC: self.handle_delete, # Уже должно быть из self.keybindings['delete']
            curses.KEY_ENTER:   self.handle_enter,
            10:                 self.handle_enter,  # \n
            13:                 self.handle_enter,  # \r
            curses.KEY_SLEFT:   self.extend_selection_left,
            curses.KEY_SRIGHT:  self.extend_selection_right,
            getattr(curses, 'KEY_SR', 337): self.extend_selection_up, 
            getattr(curses, 'KEY_SF', 336): self.extend_selection_down,
            curses.KEY_SHOME:   self.select_to_home,
            curses.KEY_SEND:    self.select_to_end,
            # 27: self.cancel_operation, # Уже должно быть из self.keybindings['cancel_operation']
            getattr(curses, 'KEY_IC', 331): self.toggle_insert_mode,
            curses.KEY_RESIZE:  self.handle_resize,
            # getattr(curses, 'KEY_BTAB', 353): self.handle_smart_unindent, # Уже должно быть из self.keybindings['shift_tab']
        }
        
        # Применяем F4 для lint, если он не переопределен и действие lint существует
        f4_code = getattr(curses, 'KEY_F4', 268)
        if f4_code not in final_map and "lint" in action_method_map:
             final_map.setdefault(f4_code, action_method_map["lint"])
        elif f4_code not in final_map and "show_lint_panel" in action_method_map: # Fallback для старого имени
             final_map.setdefault(f4_code, action_method_map["show_lint_panel"])

        for code, method in builtin_key_to_method.items():
            final_map.setdefault(code, method)

        logging.debug(f"Итоговая карта действий (Код -> Метод): { {k: v.__name__ for k,v in final_map.items()} }")
        return final_map
    
 
    # ─────────────────────  Получение префикса комментария для текущего языка  ─────────────────────
    def get_line_comment_prefix(self) -> Optional[str]:
        """
        Возвращает префикс строчного комментария для текущего языка.
        Приоритет: информация из лексера Pygments, затем правила по альясам лексера.
        Возвращает None, если префикс не определен или язык использует блочные комментарии.
        """
        if self._lexer is None:
            self.detect_language()

        if not self._lexer:
            logging.warning("get_line_comment_prefix: Lexer is not defined.")
            return None

        lexer_instance = self._lexer # self._lexer - это экземпляр лексера
        lexer_name = lexer_instance.name.lower() # Основное имя лексера
        lexer_aliases = [alias.lower() for alias in lexer_instance.aliases] # Альянсы лексера
        
        logging.debug(f"get_line_comment_prefix: Determining for lexer '{lexer_name}', aliases: {lexer_aliases}")

        # 1. Попытка получить из атрибута (редко, но бывает)
        #    Этот атрибут не является стандартным для всех лексеров Pygments.
        if hasattr(lexer_instance, 'comment_single_prefix'): # Или другое имя, если такое есть
            prefix = getattr(lexer_instance, 'comment_single_prefix', None)
            if isinstance(prefix, str) and prefix:
                logging.info(f"Using comment prefix '{prefix}' from lexer attribute 'comment_single_prefix' for '{lexer_name}'")
                return prefix

        # 2. Анализ токенов лексера (более сложный, но потенциально точный путь)
        #    Это требует понимания внутренней структуры правил лексера.
        #    Многие лексеры наследуются от RegexLexer.
        #    Ищем правило для Comment.Single или Comment.Line.
        #    Это ЭКСПЕРИМЕНТАЛЬНЫЙ подход и может не работать для всех лексеров.
        if isinstance(lexer_instance, RegexLexer):
            try:
                # Проверяем основные группы токенов, где могут быть комментарии
                for state_name in ['root', 'comment', 'comments']: # Общие имена состояний
                    if state_name in lexer_instance.tokens:
                        rules = lexer_instance.tokens[state_name]
                        for rule in rules:
                            # Правило это кортеж: (regex, token_type, new_state) или (regex, token_type)
                            # Нас интересуют token_type == Comment.Single или Comment.Line
                            if len(rule) >= 2 and rule[1] in (Comment.Single, Comment.Line):
                                regex_pattern = rule[0]
                                # Попытка извлечь префикс из регулярного выражения.
                                # Это очень упрощенно и зависит от того, как написан regex.
                                # Пример: r'#.*$' -> '#', r'//.*$' -> '//'
                                # Это очень хрупко!
                                if isinstance(regex_pattern, str):
                                    if regex_pattern.startswith('#') and regex_pattern.endswith(('.*$', '.*?\n', '.*')):
                                        logging.info(f"Deduced comment prefix '# ' from lexer token rule for '{lexer_name}'")
                                        return "# "
                                    if regex_pattern.startswith('//') and regex_pattern.endswith(('.*$', '.*?\n', '.*')):
                                        logging.info(f"Deduced comment prefix '// ' from lexer token rule for '{lexer_name}'")
                                        return "// "
                                    if regex_pattern.startswith('--') and regex_pattern.endswith(('.*$', '.*?\n', '.*')):
                                        logging.info(f"Deduced comment prefix '-- ' from lexer token rule for '{lexer_name}'")
                                        return "-- "
                                    # ... другие популярные ...
            except Exception as e:
                logging.debug(f"Error trying to deduce comment prefix from lexer tokens for '{lexer_name}': {e}")
                pass # Не удалось, переходим к правилам по альясам

        # 3. Правила на основе альясов лексера (более надежный fallback)
        #    Используем set для быстрой проверки принадлежности.
        all_names_to_check = set([lexer_name] + lexer_aliases)

        # Языки с "#"
        hash_comment_langs = {'python', 'py', 'sage', 'cython', 
                              'ruby', 'rb', 'perl', 'pl', 
                              'bash', 'sh', 'zsh', 'ksh', 'fish', 'shell', 'ash',
                              'makefile', 'dockerfile', 'conf', 'cfg', 'ini', # ini/conf часто # или ;
                              'r', 'yaml', 'yml', 'toml', 
                              'gdscript', 'nim', 'julia', 'jl', 'cmake',
                              'tcl', 'awk', 'sed', 'powershell', 'ps1',
                              'gitconfig', 'gitignore', 'gitattributes', # Git-специфичные файлы
                              'sls', # SaltStack
                              'pp', # Puppet
                              'tf', 'tfvars' # Terraform
                             }
        if not all_names_to_check.isdisjoint(hash_comment_langs):
            # Если есть пересечение (т.е. хотя бы один из all_names_to_check есть в hash_comment_langs)
            # Особый случай для INI/CONF - может быть и ;
            if 'ini' in all_names_to_check or 'conf' in all_names_to_check:
                 # Если это INI/CONF, ; более каноничен, но # тоже встречается.
                 # Давайте пока для INI/CONF отдадим приоритет ';', если он будет ниже.
                 # Если дойдем досюда, то '# '
                 logging.info(f"Using comment prefix '# ' for lexer '{lexer_name}' (matched in hash_comment_langs, possibly ini/conf)")
                 return "# " # Можно сделать более сложную логику для ini/conf
            logging.info(f"Using comment prefix '# ' for lexer '{lexer_name}' (matched in hash_comment_langs)")
            return "# "

        # Языки с "//"
        slash_comment_langs = {'javascript', 'js', 'jsx', 'jsonc', # JSONC (JSON with comments)
                               'typescript', 'ts', 'tsx', 
                               'java', 'kotlin', 'kt', 
                               'c', 'cpp', 'cxx', 'cc', 'objective-c', 'objc', 'objective-c++', 'objcpp',
                               'c#', 'csharp', 'cs', 
                               'go', 'golang', 'swift', 'dart', 'rust', 'rs', 'scala', 
                               'groovy', 'haxe', 'pascal', 'objectpascal', 'delphi', 
                               'php', # PHP также может #, но // приоритетнее для строчных
                               'glsl', 'hlsl', 'shader', 
                               'd', 'vala', 'ceylon', 'crystal', 'chapel',
                               'processing'
                              }
        if not all_names_to_check.isdisjoint(slash_comment_langs):
            logging.info(f"Using comment prefix '// ' for lexer '{lexer_name}' (matched in slash_comment_langs)")
            return "// "
        
        # Языки с "--"
        double_dash_comment_langs = {'sql', 'plpgsql', 'tsql', 'mysql', 'postgresql', 'sqlite',
                                     'lua', 'haskell', 'hs', 'ada', 'vhdl', 'elm'}
        if not all_names_to_check.isdisjoint(double_dash_comment_langs):
            logging.info(f"Using comment prefix '-- ' for lexer '{lexer_name}' (matched in double_dash_comment_langs)")
            return "-- "
            
        # Языки с "%"
        percent_comment_langs = {'erlang', 'erl', 'prolog', 'plg', 'latex', 'tex', 
                                 'matlab', 'octave', 'scilab', 'postscript'}
        if not all_names_to_check.isdisjoint(percent_comment_langs):
            logging.info(f"Using comment prefix '% ' for lexer '{lexer_name}' (matched in percent_comment_langs)")
            return "% "

        # Языки с ";"
        semicolon_comment_langs = {'clojure', 'clj', 'lisp', 'common-lisp', 'elisp', 'emacs-lisp', 
                                   'scheme', 'scm', 'racket', 'rkt', 
                                   'autolisp', 'asm', 'nasm', 'masm', 'nix', # NixOS configuration
                                   'ini', 'properties', 'desktop' # .desktop files, .properties часто используют ; или #
                                  } 
        if not all_names_to_check.isdisjoint(semicolon_comment_langs):
            logging.info(f"Using comment prefix '; ' for lexer '{lexer_name}' (matched in semicolon_comment_langs)")
            return "; " # Для INI/properties это более канонично, чем #
            
        # Языки с "!" (Fortran)
        exclamation_comment_langs = {'fortran', 'f90', 'f95', 'f03', 'f08', 'f', 'for'}
        if not all_names_to_check.isdisjoint(exclamation_comment_langs):
            logging.info(f"Using comment prefix '! ' for lexer '{lexer_name}' (matched in exclamation_comment_langs)")
            return "! "

        # Языки с "REM" или "'" (Basic-подобные)
        rem_comment_langs = {'vb.net', 'vbnet', 'vbs', 'vbscript', 'basic', 'qbasic', 'freebasic', 'visual basic'}
        if not all_names_to_check.isdisjoint(rem_comment_langs):
            # VB.Net и VBScript используют одинарную кавычку '
            # Старый BASIC мог использовать REM
            logging.info(f"Using comment prefix '\' ' for lexer '{lexer_name}' (matched in rem_comment_langs)")
            return "' " 
            
        # Языки, где строчные комментарии нетипичны или используются блочные
        block_comment_only_langs = {'html', 'htm', 'xhtml', 'xml', 'xsd', 'xsl', 'xslt', 'plist', 'rss', 'atom', 'svg', 'vue', 'django', 'jinja', 'jinja2',
                                    'css', 'scss', 'less', 'sass', # Sass (SCSS синтаксис) может //, но чистый Sass нет.
                                    'json', # Стандартный JSON не поддерживает комментарии
                                    'markdown', 'md', 'rst', 
                                    'text', 'txt', 'plaintext', 'log',
                                    'bibtex', 'bib',
                                    'diff', 'patch'
                                   }
        if not all_names_to_check.isdisjoint(block_comment_only_langs):
            logging.info(f"Line comments are not typical or well-defined for lexer '{lexer_name}' (matched in block_comment_only_langs). Returning None.")
            return None
            
        # Если это TextLexer, то комментариев нет
        if isinstance(lexer_instance, TextLexer):
            logging.info(f"Lexer is TextLexer ('{lexer_name}'), no line comments. Returning None.")
            return None

        # Если мы дошли сюда, префикс не найден по известным правилам
        logging.warning(f"get_line_comment_prefix: No line comment prefix rule found for lexer '{lexer_name}' (aliases: {lexer_aliases}). Returning None.")
        return None


    def _determine_lines_to_toggle_comment(self) -> Optional[tuple[int, int]]:
        """Определяет диапазон строк для комментирования/раскомментирования."""
        if self.is_selecting and self.selection_start and self.selection_end:
            norm_range = self._get_normalized_selection_range()
            if not norm_range: return None
            start_coords, end_coords = norm_range
            
            start_y = start_coords[0]
            end_y = end_coords[0]
            
            if end_coords[1] == 0 and end_y > start_y:
                end_y -=1 # Не включаем строку, на которой выделение заканчивается в нулевой колонке, если это не единственная строка

            return start_y, end_y
        else:
            return self.cursor_y, self.cursor_y

#-Одна горячая клавиша: Пользователю нужно запомнить только одну комбинацию (например, Ctrl+/) для обеих операций. 
# Редактор сам решает, что делать. Это поведение распространено во многих современных IDE (VS Code, JetBrains IDEs и т.д.):
# Временно отключен и используем do_comment_block(self) и do_uncomment_block(self) - Явные действия)
    def toggle_comment_block(self):
            """
            Комментирует или раскомментирует выделенный блок строк или текущую строку.
            Определяет действие (комментировать/раскомментировать) автоматически.
            """
            comment_prefix = self.get_line_comment_prefix()
            if not comment_prefix:
                self._set_status_message("Line comments not supported for this language.")
                return

            line_range = self._determine_lines_to_toggle_comment()
            if line_range is None:
                self._set_status_message("No lines selected to comment/uncomment.")
                return
            
            start_y, end_y = line_range

            with self._state_lock:
                # Определяем, нужно комментировать или раскомментировать.
                # Если ХОТЯ БЫ ОДНА строка в диапазоне НЕ закомментирована (или закомментирована другим префиксом),
                # то комментируем ВЕСЬ блок.
                # Иначе (ВСЕ строки УЖЕ закомментированы ЭТИМ префиксом), то раскомментируем ВЕСЬ блок.
                
                all_lines_are_commented_with_this_prefix = True
                non_empty_lines_exist = False

                for y in range(start_y, end_y + 1):
                    if y >= len(self.text): continue
                    line = self.text[y]
                    if line.strip(): # Если строка не пустая (не только пробелы)
                        non_empty_lines_exist = True
                        # Проверяем, начинается ли НЕПУСТАЯ строка с отступа + префикса
                        stripped_line = line.lstrip()
                        if not stripped_line.startswith(comment_prefix.strip()): # Сравниваем без пробелов вокруг префикса
                            all_lines_are_commented_with_this_prefix = False
                            break 
                    # Пустые строки или строки только из пробелов не влияют на решение "все ли закомментированы",
                    # но они будут обработаны (закомментированы или оставлены как есть при раскомментировании).

                if not non_empty_lines_exist and (end_y > start_y or not self.text[start_y].strip()):
                    # Если все строки в диапазоне пустые или только из пробелов,
                    # или если выделена одна пустая строка, то комментируем.
                    action_to_perform = "comment"
                elif all_lines_are_commented_with_this_prefix:
                    action_to_perform = "uncomment"
                else:
                    action_to_perform = "comment"

                logging.debug(f"Toggle comment: Action decided: {action_to_perform} for lines {start_y}-{end_y}")

                if action_to_perform == "comment":
                    self.comment_lines(start_y, end_y, comment_prefix)
                else: # "uncomment"
                    self.uncomment_lines(start_y, end_y, comment_prefix)


#   def toggle_comment_block(self): podobny
    def do_comment_block(self):
        """Всегда комментирует выделенный блок или текущую строку."""
        comment_prefix = self.get_line_comment_prefix()
        if not comment_prefix:
            self._set_status_message("Line comments not supported for this language.")
            return

        line_range = self._determine_lines_to_toggle_comment()
        if line_range is None:
            self._set_status_message("No lines selected to comment.")
            return
        
        start_y, end_y = line_range
        logging.debug(f"do_comment_block: Commenting lines {start_y}-{end_y}")
        self.comment_lines(start_y, end_y, comment_prefix)

    def do_uncomment_block(self):
        """Всегда раскомментирует выделенный блок или текущую строку."""
        comment_prefix = self.get_line_comment_prefix()
        if not comment_prefix:
            # Сообщение не нужно, так как если комментирование не поддерживается,
            # то и раскомментирование тоже. do_comment_block уже покажет.
            # Можно добавить, если хочется явного сообщения для Shift+/
            self._set_status_message("Line comments not supported for this language (for uncomment).")
            return

        line_range = self._determine_lines_to_toggle_comment()
        if line_range is None:
            self._set_status_message("No lines selected to uncomment.")
            return
        
        start_y, end_y = line_range
        logging.debug(f"do_uncomment_block: Uncommenting lines {start_y}-{end_y}")
        self.uncomment_lines(start_y, end_y, comment_prefix)


    #  comment_lines, uncomment_lines, undo, redo остаются такими же, как в предыдущем ответе.
    def comment_lines(self, start_y: int, end_y: int, comment_prefix: str):
        with self._state_lock: 
            original_texts = {} 
            min_indent = float('inf')
            non_empty_lines_in_block_indices = []

            for y in range(start_y, end_y + 1):
                if y >= len(self.text): continue
                line = self.text[y]
                if line.strip(): 
                    non_empty_lines_in_block_indices.append(y)
                    indent_len = len(line) - len(line.lstrip())
                    min_indent = min(min_indent, indent_len)
            
            if not non_empty_lines_in_block_indices: 
                min_indent = 0 

            changes_for_undo = []
            selection_before_op = (self.is_selecting, self.selection_start, self.selection_end) if self.is_selecting else None

            # Сохраняем оригинальное состояние курсора, если нет выделения
            cursor_before_op_no_selection = (self.cursor_y, self.cursor_x) if not self.is_selecting else None


            new_selection_start_x_offset = len(comment_prefix)
            new_selection_end_x_offset = len(comment_prefix)


            for y in range(start_y, end_y + 1):
                if y >= len(self.text): continue
                
                original_texts[y] = self.text[y] 
                line_content = self.text[y]
                current_line_is_empty_or_whitespace = not line_content.strip()
                
                insert_pos = 0
                if current_line_is_empty_or_whitespace:
                    # Для пустых строк вставляем в начало (после существующих пробелов)
                    # или просто вставляем префикс, если строка абсолютно пустая.
                    insert_pos = len(line_content) - len(line_content.lstrip(' ')) # Позиция первого непробельного символа (или конец строки)
                else: 
                    insert_pos = min_indent

                self.text[y] = line_content[:insert_pos] + comment_prefix + line_content[insert_pos:]
                changes_for_undo.append({
                    "line_index": y, 
                    "original_text": original_texts[y], 
                    "new_text": self.text[y]
                })

            # Корректировка выделения и курсора
            if self.is_selecting and self.selection_start and self.selection_end:
                s_y, s_x = self.selection_start
                e_y, e_x = self.selection_end
                
                # Сдвигаем x координаты выделения. Если строка была пустой и стала "# ", x не меняется сильно.
                # Если строка имела отступ min_indent, то x сдвигается на len(comment_prefix).
                # Это упрощение. Точная корректировка сложна.
                # Пока что, если строка start_y была непустой, сдвигаем s_x.
                if start_y in non_empty_lines_in_block_indices or not self.text[s_y][:s_x].strip(): # Если до курсора нет текста или это непустая строка
                    self.selection_start = (s_y, s_x + new_selection_start_x_offset)
                
                if end_y in non_empty_lines_in_block_indices or not self.text[e_y][:e_x].strip():
                     self.selection_end = (e_y, e_x + new_selection_end_x_offset)
                
                self.cursor_y, self.cursor_x = self.selection_end
            elif cursor_before_op_no_selection: # Если нет выделения, корректируем только курсор
                # Если текущая строка была непустой, сдвигаем курсор
                if self.cursor_y in non_empty_lines_in_block_indices:
                    self.cursor_x += new_selection_start_x_offset
                # Если строка была пустой и стала "# ", курсор ставим после префикса
                elif not original_texts[self.cursor_y].strip():
                     self.cursor_x = len(comment_prefix)

            self.modified = True
            self.action_history.append({
                "type": "comment_block",
                "changes": changes_for_undo, 
                "comment_prefix": comment_prefix,
                "start_y": start_y, "end_y": end_y, 
                "selection_before": selection_before_op,
                "cursor_before_no_selection": cursor_before_op_no_selection,
                # Сохраняем состояние ПОСЛЕ для redo
                "selection_after": (self.is_selecting, self.selection_start, self.selection_end) if self.is_selecting else None,
                "cursor_after_no_selection": (self.cursor_y, self.cursor_x) if not self.is_selecting else None
            })
            self.undone_actions.clear()
            self._set_status_message(f"Commented lines {start_y+1}-{end_y+1}")


    def uncomment_lines(self, start_y: int, end_y: int, comment_prefix: str):
        with self._state_lock: 
            original_texts = {}
            changes_for_undo = []
            prefix_to_remove_stripped = comment_prefix.strip() 
            
            selection_before_op = (self.is_selecting, self.selection_start, self.selection_end) if self.is_selecting else None
            cursor_before_op_no_selection = (self.cursor_y, self.cursor_x) if not self.is_selecting else None
            
            max_removed_len_at_sel_start = 0
            max_removed_len_at_sel_end = 0


            for y in range(start_y, end_y + 1):
                if y >= len(self.text): continue
                
                original_texts[y] = self.text[y]
                line = self.text[y]
                
                lstripped_line = line.lstrip()
                indent_len = len(line) - len(lstripped_line)
                removed_this_line_len = 0

                if lstripped_line.startswith(prefix_to_remove_stripped):
                    len_to_check_for_space = len(prefix_to_remove_stripped)
                    
                    # Проверяем, нужно ли удалять пробел после префикса
                    remove_extra_space = False
                    if comment_prefix.endswith(' ') and not prefix_to_remove_stripped.endswith(' '):
                        if len(lstripped_line) > len_to_check_for_space and lstripped_line[len_to_check_for_space] == ' ':
                            remove_extra_space = True
                    
                    chars_to_actually_remove_from_lstripped = len_to_check_for_space + (1 if remove_extra_space else 0)
                    self.text[y] = line[:indent_len] + lstripped_line[chars_to_actually_remove_from_lstripped:]
                    removed_this_line_len = chars_to_actually_remove_from_lstripped
                    
                    changes_for_undo.append({
                        "line_index": y,
                        "original_text": original_texts[y],
                        "new_text": self.text[y]
                    })

                    if y == start_y: max_removed_len_at_sel_start = removed_this_line_len
                    if y == end_y: max_removed_len_at_sel_end = removed_this_line_len


            if changes_for_undo: 
                self.modified = True
                
                # Корректировка выделения и курсора
                if self.is_selecting and self.selection_start and self.selection_end:
                    s_y, s_x = self.selection_start
                    e_y, e_x = self.selection_end
                    self.selection_start = (s_y, max(0, s_x - max_removed_len_at_sel_start))
                    self.selection_end = (e_y, max(0, e_x - max_removed_len_at_sel_end))
                    self.cursor_y, self.cursor_x = self.selection_end
                elif cursor_before_op_no_selection:
                    self.cursor_x = max(0, self.cursor_x - max_removed_len_at_sel_start) # Используем удаление на текущей строке

                self.action_history.append({
                    "type": "uncomment_block",
                    "changes": changes_for_undo,
                    "comment_prefix": comment_prefix, 
                    "start_y": start_y, "end_y": end_y,
                    "selection_before": selection_before_op,
                    "cursor_before_no_selection": cursor_before_op_no_selection,
                    "selection_after": (self.is_selecting, self.selection_start, self.selection_end) if self.is_selecting else None,
                    "cursor_after_no_selection": (self.cursor_y, self.cursor_x) if not self.is_selecting else None
                })
                self.undone_actions.clear()
                self._set_status_message(f"Uncommented lines {start_y+1}-{end_y+1}")
            else:
                self._set_status_message(f"Nothing to uncomment in lines {start_y+1}-{end_y+1}")



        # ─────────────────────  Обработчик ввода  ─────────────────────
    def handle_input(self, key: int | str) -> None:
        """
        Обработчик всех нажатий клавиш.
        Поддерживает: Unicode-символы (включая китайский/польский/кириллицу),
        спец-клавиши, горячие клавиши, стрелки и др.
        """
        logging.debug("handle_input → key = %r (%s)", key, type(key).__name__)
        with self._state_lock:
            try:
                logging.debug("Received key code: %r", key)

                # ── 1. Unicode‑строка от get_wch() (печатаемый символ) ─────
                if isinstance(key, str) and len(key) == 1:
                    if wcswidth(key) > 0:
                        self.insert_text(key)
                    else:
                        self._set_status_message(f"Ignored zero‑width char: {repr(key)}")
                    return

                # ── 2. Горячая клавиша из action_map ───────────────────────
                if isinstance(key, int) and key in self.action_map:
                    self.action_map[key]() # Если key = 27, вызовется cancel_operation
                    return

                # ── 3. Спец‑клавиши и навигация ────────────────────────────
                if key in (curses.KEY_ENTER, 10, 13):
                    self.handle_enter()
                elif key in (curses.KEY_UP, 259, 450):
                    self.handle_up()
                elif key in (curses.KEY_DOWN, 258, 456):
                    self.handle_down()
                elif key in (curses.KEY_LEFT, 260, 452):
                    self.handle_left()
                elif key in (curses.KEY_RIGHT, 261, 454):
                    self.handle_right()
                elif key in (curses.KEY_BACKSPACE, 127, 8):
                    self.handle_backspace()
                elif key in (curses.KEY_DC, 330, 462):
                    self.handle_delete()
                elif key in (curses.KEY_HOME, 262, 449):
                    self.handle_home()
                elif key in (curses.KEY_END, 360, 455):
                    self.handle_end()
                elif key in (curses.KEY_PPAGE, 339, 451):
                    self.handle_page_up()
                elif key in (curses.KEY_NPAGE, 338, 457):
                    self.handle_page_down()
                elif key == curses.ascii.TAB:
                    self.handle_smart_tab()
                elif key == curses.KEY_SRIGHT:
                    self.extend_selection_right()
                elif key == curses.KEY_SLEFT:
                    self.extend_selection_left()
                elif key == curses.KEY_SHOME:
                    self.select_to_home()
                elif key == curses.KEY_SEND:
                    self.select_to_end()
                elif key in (curses.KEY_SR, 337):
                    self.extend_selection_up()
                elif key in (curses.KEY_SF, 336):
                    self.extend_selection_down()
                elif key == 26:  # Undo
                    self.undo()
                elif key == 90:  # Redo
                    self.redo()
                elif key == 331:  # Insert
                    self.toggle_insert_mode()
                elif key == 410:  # Resize
                    self.handle_resize()
                elif key in (curses.KEY_F4, 268):  # F4 lint
                    self.show_lint_panel() 
                elif isinstance(key, str) and key.startswith("\x1b"): # \x1b это Esc
                    if key == "\x1b[Z":  # Shift-Tab
                        self.handle_smart_tab()
                    else:
                        # Если get_wch() вернул "\x1b" как строку (а не int 27),
                        # то action_map[27] не сработает.
                        # Сюда попадет одиночный Esc, если он пришел как строка "\x1b"
                        self._set_status_message(f"Unhandled escape: {key!r}") 
                elif key == 27: # Это условие может быть избыточным, если 27 уже есть в action_map
                    self.cancel_operation()
                elif isinstance(key, int) and 32 <= key < 1114112 and key not in self.action_map:
                    try:
                        ch = chr(key)
                        if wcswidth(ch) > 0:
                            self.insert_text(ch)
                            return
                    except Exception:
                        logging.debug(f"Invalid ordinal: {key}")
                else:
                    KEY_LOGGER.debug("Unhandled key: %r", key)
                    self._set_status_message(f"Unhandled: {repr(key)}")

            except Exception:
                logging.exception("Input handler error")
                self._set_status_message("Input handler error (see log)")



    def draw_screen(self, *a, **kw):
        """Старое имя метода – делегируем новому DrawScreen."""
        return self.drawer.draw(*a, **kw)


    # ─────────────────────  Запуск flake8 в отдельном потоке  ─────────────────────
    def run_flake8_on_code(self, code_string: str, filename: Optional[str] = "<буфер>") -> None:
        """
        Запускает анализ Python-кода с помощью Flake8 в отдельном потоке.
        Результаты (ошибки/предупреждения или сообщение об их отсутствии)
        помещаются в self.lint_panel_message и отображаются в панели линтера.
        Краткий статус выводится в статус-бар.

        :param code_string: Исходный код Python для проверки.
        :param filename: Имя файла (используется для логирования и Flake8).
        """
        # Используем <буфер> если имя файла не предоставлено или None
        effective_filename_for_flake8 = filename if filename and filename != "noname" else "<буфер>"
        
        # Ограничение для больших файлов — для производительности
        # Проверяем размер строки, т.к. кодирование может быть тяжелым для очень больших строк
        if len(code_string) > 750_000: # Примерный лимит по символам, чтобы избежать долгого encode
            try:
                if len(code_string.encode('utf-8', errors='ignore')) > 1_000_000: # 1MB
                    msg = "Flake8: Файл слишком большой для анализа (макс. ~1MB)"
                    logging.warning(msg + f" (файл: {effective_filename_for_flake8})")
                    self._set_status_message(
                        message_for_statusbar=msg, 
                        is_lint_status=True, 
                        full_lint_output=msg, 
                        activate_lint_panel_if_issues=True
                    )
                    return
            except Exception as e:
                logging.warning(f"Не удалось проверить размер файла для flake8: {e}")
                # Продолжаем, но с осторожностью

        tmp_file_path: Optional[str] = None

        try:
            # Создаем временный файл с суффиксом .py, чтобы flake8 его правильно распознал
            # delete=False, т.к. flake8 должен иметь возможность открыть его по имени
            with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8', errors='replace') as tmp:
                tmp_file_path = tmp.name
                tmp.write(code_string)
            logging.debug(f"Код для flake8 записан во временный файл: {tmp_file_path}")
        except Exception as e:
            logging.exception(f"Не удалось создать временный файл для flake8: {e}")
            msg = "Flake8: Ошибка создания временного файла"
            self._set_status_message(
                message_for_statusbar=msg, 
                is_lint_status=True, 
                full_lint_output=f"{msg}\n{e}", 
                activate_lint_panel_if_issues=True
            )
            return

        # Внутренняя функция, которая будет выполняться в отдельном потоке
        def _run_flake8_thread():
            nonlocal tmp_file_path # Указываем, что tmp_file_path из внешней области видимости
            
            full_output_for_panel: str
            short_message_for_statusbar: str
            show_panel_on_issues: bool = True # По умолчанию показываем панель, если есть проблемы

            try:
                # Собираем команду для запуска flake8
                # Используем sys.executable для запуска модуля flake8 из текущего окружения
                # Передаем имя временного файла flake8. Если бы мы передавали имя оригинального файла,
                # flake8 мог бы попытаться найти файлы конфигурации относительно него.
                # Для анализа содержимого буфера, передача имени временного файла с --isolated лучше.
                # Если filename реальный, flake8 может его использовать для некоторых правил (например, first-party import)
                # Но для анализа содержимого, которое может отличаться от сохраненного файла, tmp_file_path надежнее.
                # Флаг --stdin-display-name можно использовать, если передавать код через stdin,
                # но раз мы используем файл, то tmp_file_path будет в выводе flake8.
                
                # flake8 может использовать filename для отображения, если он передан,
                # но анализировать будет tmp_file_path.
                # Чтобы вывод был более понятным, если filename это "<буфер>",
                # можно использовать --stdin-display-name (если бы читали из stdin).
                # Для файлового анализа, flake8 сам использует имя файла.
                # Если мы хотим, чтобы в выводе flake8 было имя нашего файла (или <буфер>),
                # а не имя временного файла, это сложнее без stdin.
                # Пока оставим анализ tmp_file_path.
                
                flake8_cmd = [
                    sys.executable, "-m", "flake8",
                    "--isolated",  # Игнорировать пользовательские/проектные конфиги flake8
                    f"--max-line-length={self.config.get('flake8', {}).get('max_line_length', 88)}",
                    # Можно добавить другие настраиваемые параметры flake8 из self.config
                    # например, --select, --ignore
                    # select_codes = self.config.get('flake8', {}).get('select')
                    # if select_codes: flake8_cmd.extend(["--select", select_codes])
                    # ignore_codes = self.config.get('flake8', {}).get('ignore')
                    # if ignore_codes: flake8_cmd.extend(["--ignore", ignore_codes])
                    tmp_file_path  # Файл для анализа
                ]
                logging.debug(f"Выполнение команды flake8: {' '.join(shlex.quote(str(c)) for c in flake8_cmd)}")

                process = subprocess.run(
                    flake8_cmd,
                    capture_output=True, # Захватываем stdout и stderr
                    text=True,           # Декодируем вывод как текст (обычно utf-8 по умолчанию для text=True)
                    check=False,         # Не выбрасывать исключение при ненулевом коде возврата
                    timeout=self.config.get('flake8', {}).get('timeout', 20), # Таймаут для команды
                    encoding='utf-8',    # Явно указываем кодировку для вывода
                    errors='replace'     # Как обрабатывать ошибки декодирования
                )

                raw_stdout = process.stdout.strip()
                raw_stderr = process.stderr.strip()

                if raw_stderr:
                    logging.warning(f"Flake8 stderr для {tmp_file_path} (файл редактора: {effective_filename_for_flake8}): {raw_stderr}")

                if process.returncode == 0:
                    # Код 0: flake8 не нашел проблем (или все отфильтровано)
                    full_output_for_panel = f"Flake8 ({effective_filename_for_flake8}): Нет проблем."
                    short_message_for_statusbar = "Flake8: Нет проблем."
                    show_panel_on_issues = False # Не показываем панель, если нет проблем
                else:
                    # Ненулевой код возврата: есть проблемы или ошибка flake8
                    if raw_stdout: # flake8 вернул список проблем
                        # Заменяем имя временного файла на имя файла в редакторе для лучшей читаемости
                        # Flake8 обычно выводит <tmp_file_path>:<line>:<col>: <error_code> <message>
                        # Мы хотим заменить <tmp_file_path> на effective_filename_for_flake8
                        # Это нужно делать аккуратно, чтобы не заменить части кода, если они похожи на имя файла.
                        # Имя tmp_file_path обычно содержит случайные символы.
                        # Упрощенная замена (может потребовать более надежного regex, если имена файлов сложные):
                        # Заменяем только если tmp_file_path не None и присутствует в строке
                        if tmp_file_path:
                            full_output_for_panel = raw_stdout.replace(tmp_file_path + ":", effective_filename_for_flake8 + ":")
                        else:
                            full_output_for_panel = raw_stdout
                        
                        lines = full_output_for_panel.splitlines()
                        num_issues = len(lines)
                        first_issue_preview = lines[0][:70] + "..." if lines and len(lines[0]) > 70 else (lines[0] if lines else "")
                        short_message_for_statusbar = f"Flake8: {num_issues} проблем. ({first_issue_preview})"
                    elif raw_stderr: # Нет stdout, но есть stderr - вероятно, ошибка конфигурации flake8 или самого flake8
                        full_output_for_panel = f"Flake8 ({effective_filename_for_flake8}) ошибка выполнения:\n{raw_stderr}"
                        short_message_for_statusbar = "Flake8: Ошибка выполнения."
                    else: # Ненулевой код, но нет вывода - странная ситуация
                        full_output_for_panel = f"Flake8 ({effective_filename_for_flake8}): Неизвестная ошибка (код {process.returncode}), вывод пуст."
                        short_message_for_statusbar = f"Flake8: Неизвестная ошибка (код {process.returncode})."
                
            except FileNotFoundError:
                # Это произойдет, если sys.executable или flake8 не найдены
                msg = "Flake8: Не найден (установите 'pip install flake8')"
                logging.error(msg)
                full_output_for_panel = msg
                short_message_for_statusbar = msg
                show_panel_on_issues = True
            except subprocess.TimeoutExpired:
                msg = f"Flake8 ({effective_filename_for_flake8}): Превышено время ожидания команды."
                logging.warning(msg)
                full_output_for_panel = msg
                short_message_for_statusbar = msg
                show_panel_on_issues = True
            except Exception as e:
                logging.exception(f"Ошибка при выполнении flake8 для {tmp_file_path} (файл редактора: {effective_filename_for_flake8}): {e}")
                msg_short = f"Flake8 ошибка: {str(e)[:60]}..."
                msg_full = f"Flake8 ({effective_filename_for_flake8}) внутренняя ошибка:\n{e}"
                full_output_for_panel = msg_full
                short_message_for_statusbar = msg_short
                show_panel_on_issues = True
            finally:
                # Удаление временного файла
                if tmp_file_path and os.path.exists(tmp_file_path):
                    try:
                        os.remove(tmp_file_path)
                        logging.debug(f"Временный файл {tmp_file_path} удален.")
                    except Exception as e_remove:
                        logging.warning(f"Не удалось удалить временный файл {tmp_file_path}: {e_remove}")
            
            # Устанавливаем сообщения и решаем, показывать ли панель
            # Важно: этот вызов должен быть потокобезопасным, если _set_status_message
            # напрямую меняет UI. Но так как он кладет в очередь или меняет атрибуты,
            # а отрисовка в основном потоке, это должно быть нормально.
            # Для большей безопасности, если _set_status_message делает что-то сложное с curses,
            # лучше передавать результаты в основной поток через очередь.
            # Но текущая реализация _set_status_message (изменение атрибутов и put_nowait в очередь)
            # должна быть достаточно безопасной для вызова из потока.
            self._set_status_message(
                message_for_statusbar=short_message_for_statusbar, 
                is_lint_status=True, 
                full_lint_output=full_output_for_panel,
                activate_lint_panel_if_issues=show_panel_on_issues
            )
            # Если _set_status_message не вызывает show_lint_panel, а только меняет флаг,
            # то явный вызов show_lint_panel здесь не нужен, т.к. _set_status_message
            # установит self.lint_panel_active.
            # if show_panel_on_issues and full_output_for_panel and \
            #    full_output_for_panel != f"Flake8 ({effective_filename_for_flake8}): Нет проблем.":
            #     self.show_lint_panel() # Или пусть это делает _set_status_message

        # Запуск анализа в отдельном потоке
        lint_thread = threading.Thread(target=_run_flake8_thread, daemon=True)
        lint_thread.start()


    def show_lint_panel(self):
        logging.debug("show_lint_panel called")
        if not self.lint_panel_message:
            return  # нечего показывать
        self.lint_panel_active = True
        # Панель будет нарисована при следующей перерисовке экрана в Drawer.draw()


    def run_lint_async(self, code: Optional[str] = None) -> None:
        """
        Асинхронно запускает flake8-анализ для текущего содержимого буфера,
        если определен язык Python.
        Результат отображается во всплывающей панели линтера.

        :param code: Опционально, исходный код Python для проверки. 
                     Если None, используется текущее содержимое self.text.
        """
        # 1. Убедимся, что лексер определен. Если нет, попытаемся определить.
        if self._lexer is None:
            self.detect_language() 
            # После detect_language() self._lexer должен быть установлен (хотя бы в TextLexer)

        # 2. Проверяем, является ли текущий язык Python
        #    Можно проверять по имени лексера или по его типу.
        #    Имена могут варьироваться ('python', 'python3', 'py', 'ipython', 'cython', etc.)
        #    Проверка типа может быть более надежной, если вы используете стандартные лексеры Pygments.
        is_python_language = False
        if self._lexer: # Если лексер вообще определен
            lexer_name_lower = self._lexer.name.lower()
            # Распространенные имена для Python лексеров
            python_lexer_names = {'python', 'python3', 'py', 'cython', 'ipython', 'sage'} 
            if lexer_name_lower in python_lexer_names:
                is_python_language = True
            # Альтернативно или дополнительно, можно проверить тип:
            # from pygments.lexers.python import PythonLexer, Python3Lexer, CythonLexer # и т.д.
            # if isinstance(self._lexer, (PythonLexer, Python3Lexer, ...)):
            #     is_python_language = True
        
        if not is_python_language:
            message = "Flake8: Анализ доступен только для Python файлов."
            logging.info(f"run_lint_async: Пропуск линтинга, текущий лексер: {self._lexer.name if self._lexer else 'None'}")
            # Отображаем сообщение в панели линтера, чтобы пользователь знал, почему ничего не происходит
            self._set_status_message(message_for_statusbar=message, is_lint_status=True, full_lint_output=message)
            if self.lint_panel_message: # Показать панель с сообщением
                self.show_lint_panel()
            return

        # 3. Если код не передан, берем текущее содержимое буфера
        if code is None:
            code_to_lint = "\n".join(self.text)
        else:
            code_to_lint = code

        # 4. Запускаем flake8
        logging.debug(f"run_lint_async: Запуск flake8 для файла '{self.filename or "<буфер без имени>"}'")
        self._set_status_message("Flake8: Запуск анализа...", is_lint_status=True, full_lint_output="Flake8: Анализ выполняется...")
        if self.lint_panel_message: # Показать панель с сообщением о запуске
                self.show_lint_panel()

        self.run_flake8_on_code(code_to_lint, self.filename)


    def _check_pyclip_availability(self):
        """Проверяет доступность pyperclip и системных утилит для буфера обмена."""
        if not self.config.get("editor", {}).get("use_system_clipboard", True):
            logging.debug("System clipboard disabled by config.")
            return False
        try:
            # Попытка выполнить базовые операции
            pyperclip.copy("")
            # pyperclip.paste() # paste может требовать интерактивности в некоторых системах
            return True
        except pyperclip.PyperclipException as e:
            logging.warning(f"System clipboard unavailable: {str(e)}. Falling back to internal clipboard.")
            return False
        except Exception as e:
             logging.warning(f"Unexpected error checking system clipboard: {e}. Falling back to internal clipboard.")
             return False


    def get_selected_text(self):
        """Возвращает выделенный текст."""
        if not self.is_selecting or self.selection_start is None or self.selection_end is None:
            return ""
        start_row, start_col = self.selection_start
        end_row, end_col = self.selection_end

        # Нормализация: начало должно быть раньше конца
        if start_row > end_row or (start_row == end_row and start_col > end_col):
            start_row, start_col, end_row, end_col = end_row, end_col, start_row, start_col

        selected_lines = []
        if start_row == end_row:
            # Убедимся, что индексы в пределах строки
            line = self.text[start_row]
            start_col = max(0, min(start_col, len(line)))
            end_col = max(0, min(end_col, len(line)))
            selected_lines.append(line[start_col:end_col])
        else:
            # Первая строка (от start_col до конца)
            line = self.text[start_row]
            start_col = max(0, min(start_col, len(line)))
            selected_lines.append(line[start_col:])
            # Средние строки
            for row in range(start_row + 1, end_row):
                if 0 <= row < len(self.text): # Проверка на всякий случай
                    selected_lines.append(self.text[row])
            # Последняя строка (от начала до end_col)
            if 0 <= end_row < len(self.text): # Проверка на всякий случай
                line = self.text[end_row]
                end_col = max(0, min(end_col, len(line)))
                selected_lines.append(line[:end_col])
            # Если end_row > len(self.text)-1 (что не должно произойти при нормализации)
            elif end_row == len(self.text) and end_col == 0:
                 # Особый случай, когда выделение до конца последней строки и переходит на новую
                 pass # Ничего не добавляем, так как end_col=0 на несуществующей строке
            else:
                 logging.warning(f"get_selected_text: end_row {end_row} out of bounds {len(self.text)}")

        return "\n".join(selected_lines)


    def copy(self):
        """Копирует выделенный текст в буфер обмена."""
        selected_text = self.get_selected_text()
        if not selected_text:
            self._set_status_message("Nothing to copy")
            return

        # Копируем во внутренний буфер всегда
        self.internal_clipboard = selected_text
        message = "Copied to internal clipboard"

        # Попытка копирования в системный буфер, если разрешено и доступно
        if self.use_system_clipboard and self.pyclip_available:
            try:
                pyperclip.copy(selected_text)
                message = "Copied to system clipboard"
                logging.debug("Copied to system clipboard successfully")
            except pyperclip.PyperclipException as e:
                logging.error(f"Failed to copy to system clipboard: {str(e)}")
                message = "Copied to internal clipboard (system clipboard error)"
            except Exception as e:
                 logging.error(f"Unexpected error copying to system clipboard: {e}")
                 message = "Copied to internal clipboard (system clipboard error)"

        self._set_status_message(message)


    def cut(self):
        """Вырезает выделенный текст в буфер обмена."""
        with self._state_lock: # Добавляем блокировку
            logging.debug(
                f"CUT CALLED. is_selecting: {self.is_selecting}, "
                f"selection_start: {self.selection_start}, selection_end: {self.selection_end}"
            )

            if not self.is_selecting:
                self._set_status_message("Нечего вырезать (нет выделения)")
                return

            normalized_range = self._get_normalized_selection_range()
            if not normalized_range:
                self._set_status_message("Нечего вырезать (ошибка выделения)")
                return
                
            norm_start_coords, norm_end_coords = normalized_range

            # Получаем текст для вырезания ДО его фактического удаления
            # Это можно сделать с помощью отдельного метода get_text_in_range(norm_start, norm_end)
            # или адаптировать delete_selected_text_internal, чтобы он сначала вернул текст, потом удалил.
            # Для примера, используем существующий get_selected_text(), но он должен быть адаптирован
            # для работы с нормализованными координатами или мы должны передать ему self.selection_start/end.
            # Идеально, если бы delete_selected_text_internal возвращал удаленный текст.

            # Сначала получим текст, соответствующий нормализованному выделению.
            # Это потребует метода, который извлекает текст по нормализованным координатам.
            # Например, self.get_text_from_normalized_range(norm_start_coords, norm_end_coords)
            # Для простоты, предположим, что deleted_segments из delete_selected_text_internal и есть то, что нужно.

            # Удаляем выделенный текст, используя НОРМАЛИЗОВАННЫЕ координаты
            # delete_selected_text_internal должен возвращать удаленные сегменты
            deleted_text_segments = self.delete_selected_text_internal(
                norm_start_coords[0], norm_start_coords[1],
                norm_end_coords[0], norm_end_coords[1]
            )

            if not deleted_text_segments: # Если ничего не было удалено (например, пустой диапазон)
                self._set_status_message("Нечего вырезать")
                # Курсор и состояние выделения уже должны были быть обработаны delete_selected_text_internal
                self.is_selecting = False 
                self.selection_start = None
                self.selection_end = None
                return

            # Собираем удаленный текст в одну строку для буфера обмена
            text_for_clipboard = "\n".join(deleted_text_segments)

            # Копируем во внутренний буфер
            self.internal_clipboard = text_for_clipboard
            message = "Вырезано во внутренний буфер"

            # Попытка копирования в системный буфер
            if self.use_system_clipboard and self.pyclip_available:
                try:
                    pyperclip.copy(text_for_clipboard)
                    message = "Вырезано в системный буфер"
                    logging.debug("Вырезано в системный буфер успешно")
                except pyperclip.PyperclipException as e:
                    logging.error(f"Не удалось вырезать в системный буфер: {str(e)}")
                    message = "Вырезано во внутренний буфер (ошибка системного буфера)"
                except Exception as e:
                    logging.error(f"Непредвиденная ошибка при вырезании в системный буфер: {e}")
                    message = "Вырезано во внутренний буфер (ошибка системного буфера)"

            # Добавляем действие в историю, используя НОРМАЛИЗОВАННЫЕ координаты
            action = {
                "type": "delete_selection",
                "text": deleted_text_segments,      # list[str]
                "start": norm_start_coords,
                "end": norm_end_coords
            }
            self.action_history.append(action)
            self.undone_actions.clear()
            
            # self.modified = True # Должен быть установлен в delete_selected_text_internal
            # Курсор должен быть установлен в delete_selected_text_internal (в norm_start_coords)
            
            self.is_selecting = False # Сбрасываем состояние выделения
            self.selection_start = None
            self.selection_end = None
            
            self._set_status_message(message)
            logging.debug(f"cut: Завершено. Курсор в ({self.cursor_y}, {self.cursor_x})")

    def paste(self) -> None:
        with self._state_lock: # Добавляем блокировку
            logging.debug(f"paste() вызван – use_system_clipboard={self.use_system_clipboard}, pyclip_available={self.pyclip_available}")

            text_to_paste = self.internal_clipboard 
            source_of_paste = "внутреннего" 

            if self.use_system_clipboard and self.pyclip_available:
                try:
                    system_clipboard_text = pyperclip.paste()
                    if system_clipboard_text: 
                        text_to_paste = system_clipboard_text
                        source_of_paste = "системного"
                        logging.debug(f"Вставлено {len(text_to_paste)} символов из системного буфера обмена.")
                    else:
                        logging.debug("Системный буфер обмена пуст, используется внутренний.")
                except pyperclip.PyperclipException as e:
                    logging.error(f"Ошибка системного буфера обмена: {e} – используется внутренний.")
                except Exception as e: 
                    logging.exception(f"Непредвиденная ошибка буфера обмена: {e} – используется внутренний.")

            if not text_to_paste:
                self._set_status_message("Буфер обмена пуст")
                return

            text_to_paste = text_to_paste.replace("\r\n", "\n").replace("\r", "\n")
            
            # Позиция курсора, куда будет происходить вставка текста из буфера.
            # Изначально это текущая позиция, но она может измениться, если есть выделение.
            paste_target_y, paste_target_x = self.cursor_y, self.cursor_x

            if self.is_selecting:
                normalized_selection = self._get_normalized_selection_range()
                if normalized_selection:
                    norm_start_coords, norm_end_coords = normalized_selection
                    paste_target_y, paste_target_x = norm_start_coords # Вставка будет сюда

                    logging.debug(f"paste(): Удаление активного выделения с {norm_start_coords} по {norm_end_coords} перед вставкой.")
                    
                    deleted_segments = self.delete_selected_text_internal(
                        norm_start_coords[0], norm_start_coords[1],
                        norm_end_coords[0], norm_end_coords[1]
                    )
                    
                    self.action_history.append({
                        "type": "delete_selection",
                        "text": deleted_segments,
                        "start": norm_start_coords, 
                        "end": norm_end_coords,     
                    })
                    self.undone_actions.clear()
                    
                    # delete_selected_text_internal устанавливает курсор, обновляем paste_target_y/x
                    paste_target_y, paste_target_x = self.cursor_y, self.cursor_x

                    self.is_selecting = False
                    self.selection_start = None
                    self.selection_end = None
            
            # Обработка режима Замены (если он есть и активен)
            # Этот блок требует careful consideration для undo/redo.
            # Если insert_mode == False (т.е. режим замены)
            #   - Посчитать ширину первой строки text_to_paste
            #   - Удалить соответствующее количество символов/ячеек от (paste_target_y, paste_target_x)
            #   - Это удаление должно быть частью действия "вставить с заменой" или
            #     записано как отдельное действие "replace_N_chars".
            #   - Пока пропущено для простоты, т.к. это отдельная сложная задача для undo.

            # Вставляем текст, используя self.insert_text.
            # insert_text сам обработает запись "insert" действия в историю.
            # Он будет использовать текущее self.cursor_y, self.cursor_x, которые
            # являются paste_target_y, paste_target_x.
            self.insert_text(text_to_paste) 

            self._set_status_message(f"Вставлено из {source_of_paste} буфера обмена")
            logging.debug(f"paste(): Вставка завершена. Вставлено {len(text_to_paste)} символов. Курсор в ({self.cursor_y}, {self.cursor_x}).")


    def extend_selection_right(self):
        """Расширяет выделение вправо на один символ."""
        if not self.is_selecting:
            self.selection_start = (self.cursor_y, self.cursor_x)
            self.is_selecting = True
        # Перемещаем курсор
        if self.cursor_x < len(self.text[self.cursor_y]):
            self.cursor_x += 1
        # Обновляем конец выделения
        self.selection_end = (self.cursor_y, self.cursor_x)


    def extend_selection_left(self):
        """Расширяет выделение влево на один символ."""
        if not self.is_selecting:
            self.selection_start = (self.cursor_y, self.cursor_x)
            self.is_selecting = True
        # Перемещаем курсор
        if self.cursor_x > 0:
            self.cursor_x -= 1
        # Обновляем конец выделения
        self.selection_end = (self.cursor_y, self.cursor_x)


    def select_to_home(self):
        """Расширяет выделение до начала текущей строки."""
        if not self.is_selecting:
            self.selection_start = (self.cursor_y, self.cursor_x)
            self.is_selecting = True
        # Перемещаем курсор
        self.cursor_x = 0
        # Обновляем конец выделения
        self.selection_end = (self.cursor_y, self.cursor_x)

    def select_to_end(self):
        """Расширяет выделение до конца текущей строки."""
        if not self.is_selecting:
            self.selection_start = (self.cursor_y, self.cursor_x)
            self.is_selecting = True
        # Перемещаем курсор
        self.cursor_x = len(self.text[self.cursor_y])
        # Обновляем конец выделения
        self.selection_end = (self.cursor_y, self.cursor_x)

    def select_all(self):
        """Выделяет весь текст в документе."""
        self.selection_start = (0, 0)
        # Конец выделения ставим на конец последней строки.
        last_line_idx = max(0, len(self.text) - 1)
        self.selection_end = (last_line_idx, len(self.text[last_line_idx]))
        self.is_selecting = True
        # Перемещаем курсор в конец выделения
        self.cursor_y, self.cursor_x = self.selection_end
        self._set_status_message("All text selected")


    def extend_selection_up(self):
        """Расширяет выделение вверх на одну строку."""
        if not self.is_selecting:
            self.selection_start = (self.cursor_y, self.cursor_x)
            self.is_selecting = True
        if self.cursor_y > 0:
            self.cursor_y -= 1
            # Курсор перемещается на новую строку, сохраняя желаемую колонку
            # но не выходя за пределы новой строки
            self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))
        # Обновляем конец выделения
        self.selection_end = (self.cursor_y, self.cursor_x)

    def extend_selection_down(self):
        """Расширяет выделение вниз на одну строку."""
        if not self.is_selecting:
            self.selection_start = (self.cursor_y, self.cursor_x)
            self.is_selecting = True
        if self.cursor_y < len(self.text) - 1:
            self.cursor_y += 1
             # Курсор перемещается на новую строку, сохраняя желаемую колонку
            self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))
        # Обновляем конец выделения
        self.selection_end = (self.cursor_y, self.cursor_x)


    def undo(self):
        """
        Отменяет последнее действие из истории, восстанавливая текст и позицию курсора.
        Поддерживает типы действий: insert, delete_char, delete_newline, delete_selection,
        block_indent, block_unindent.
        """
        with self._state_lock:
            if not self.action_history:
                self._set_status_message("Нечего отменять")
                return
            last_action = self.action_history.pop()
            action_type = last_action.get("type")
            
            # Сохраняем текущее состояние для возможного отката при ошибке
            current_state_snapshot = {
                "text": [line for line in self.text],
                "cursor_y": self.cursor_y, "cursor_x": self.cursor_x,
                "is_selecting": self.is_selecting, 
                "selection_start": self.selection_start, "selection_end": self.selection_end
            }

            try:
                if action_type == "insert":
                    text_to_remove = last_action["text"]
                    row, col = last_action["position"] 
                    lines_to_remove_list = text_to_remove.split('\n')
                    num_lines_in_removed_text = len(lines_to_remove_list)
                    if not (0 <= row < len(self.text)): raise IndexError(f"Undo insert: Invalid row {row}")
                    
                    if num_lines_in_removed_text == 1:
                        current_line_content = self.text[row]
                        self.text[row] = current_line_content[:col] + current_line_content[col + len(text_to_remove):]
                    else:
                        prefix_on_first_line = self.text[row][:col]
                        end_row_affected_by_insert = row + num_lines_in_removed_text - 1
                        if end_row_affected_by_insert >= len(self.text): raise IndexError(f"Undo insert: Invalid end_row {end_row_affected_by_insert}")
                        len_last_inserted_line_segment = len(lines_to_remove_list[-1])
                        suffix_on_last_line = self.text[end_row_affected_by_insert][len_last_inserted_line_segment:]
                        self.text[row] = prefix_on_first_line + suffix_on_last_line
                        del self.text[row + 1 : end_row_affected_by_insert + 1]
                    self.cursor_y, self.cursor_x = row, col 

                elif action_type == "delete_char":
                    y, x = last_action["position"] 
                    deleted_char = last_action["text"] 
                    if not (0 <= y < len(self.text) and 0 <= x <= len(self.text[y])): raise IndexError(f"Undo delete_char: Invalid position ({y},{x})")
                    current_line = self.text[y]
                    self.text[y] = current_line[:x] + deleted_char + current_line[x:]
                    self.cursor_y, self.cursor_x = y, x 

                elif action_type == "delete_newline":
                    y, x = last_action["position"] 
                    moved_up_content = last_action["text"] 
                    if not (0 <= y < len(self.text) and 0 <= x <= len(self.text[y])): raise IndexError(f"Undo delete_newline: Invalid position ({y},{x})")
                    current_line_content = self.text[y]
                    self.text[y] = current_line_content[:x] 
                    self.text.insert(y + 1, moved_up_content) 
                    self.cursor_y, self.cursor_x = y, x 

                elif action_type == "delete_selection":
                    deleted_text_segments = last_action["text"] 
                    start_y, start_x = last_action["start"] 
                    text_to_restore_str = "\n".join(deleted_text_segments)
                    self.insert_text_at_position(text_to_restore_str, start_y, start_x) # insert_text_at_position ставит курсор
                    self.cursor_y, self.cursor_x = start_y, start_x # Переустанавливаем в начало для undo

                elif action_type == "block_indent":
                    original_selection = last_action.get("original_selection")
                    indent_str = last_action["indent_str"]
                    indent_len = len(indent_str)
                    for y_idx in range(last_action["start_line"], last_action["end_line"] + 1):
                        if y_idx < len(self.text) and self.text[y_idx].startswith(indent_str):
                            self.text[y_idx] = self.text[y_idx][indent_len:]
                    if original_selection:
                        self.is_selecting, self.selection_start, self.selection_end = True, original_selection[0], original_selection[1]
                        if self.selection_end: self.cursor_y, self.cursor_x = self.selection_end
                    
                elif action_type == "block_unindent":
                    original_selection = last_action.get("original_selection")
                    for change in last_action["changes"]:
                        if change["line_index"] < len(self.text):
                            self.text[change["line_index"]] = change["original_text"] # Восстанавливаем исходный текст строки
                    if original_selection:
                        self.is_selecting, self.selection_start, self.selection_end = True, original_selection[0], original_selection[1]
                        if self.selection_end: self.cursor_y, self.cursor_x = self.selection_end

                elif action_type == "comment_block" or action_type == "uncomment_block":
                    changes = last_action["changes"] 
                    selection_state = last_action.get("selection_before")
                    cursor_state_no_sel = last_action.get("cursor_before_no_selection")

                    for change_item in reversed(changes): 
                        idx = change_item["line_index"]
                        if idx < len(self.text):
                            self.text[idx] = change_item["original_text"]
                    
                    if selection_state:
                         self.is_selecting, self.selection_start, self.selection_end = selection_state
                         if self.is_selecting and self.selection_end: self.cursor_y, self.cursor_x = self.selection_end
                    elif cursor_state_no_sel:
                        self.is_selecting = False
                        self.selection_start, self.selection_end = None, None
                        self.cursor_y, self.cursor_x = cursor_state_no_sel
                    else: # На всякий случай сброс
                        self.is_selecting = False
                        self.selection_start, self.selection_end = None, None
                else:
                    logging.warning(f"Undo: Неизвестный тип действия: {action_type}")
                    self.action_history.append(last_action) 
                    return # Не смогли обработать, выходим

            except Exception as e:
                logging.exception(f"Undo: Ошибка во время undo для типа действия {action_type}: {e}")
                self._set_status_message(f"Undo не удалось для {action_type}: {str(e)[:80]}...")
                self.action_history.append(last_action) 
                # Откат к состоянию до попытки undo
                self.text = current_state_snapshot["text"]
                self.cursor_y, self.cursor_x = current_state_snapshot["cursor_y"], current_state_snapshot["cursor_x"]
                self.is_selecting = current_state_snapshot["is_selecting"]
                self.selection_start, self.selection_end = current_state_snapshot["selection_start"], current_state_snapshot["selection_end"]
                return

            self.undone_actions.append(last_action) 
            self.modified = True 
            
            # Общий сброс выделения, если оно не было явно восстановлено
            if action_type not in ["block_indent", "block_unindent", "comment_block", "uncomment_block"] or \
               (action_type in ["block_indent", "block_unindent"] and not last_action.get("original_selection")) or \
               (action_type in ["comment_block", "uncomment_block"] and not last_action.get("selection_before")):
                self.is_selecting = False 
                self.selection_start = None
                self.selection_end = None

            self._set_status_message("Действие отменено")


    def redo(self):
        """
        Повторяет действие, отмененное последней операцией undo.
        """
        with self._state_lock:
            if not self.undone_actions:
                self._set_status_message("Нечего повторять")
                return
            action_to_redo = self.undone_actions.pop()
            action_type = action_to_redo.get("type")

            current_state_snapshot = {
                "text": [line for line in self.text],
                "cursor_y": self.cursor_y, "cursor_x": self.cursor_x,
                "is_selecting": self.is_selecting, 
                "selection_start": self.selection_start, "selection_end": self.selection_end
            }

            try:
                if action_type == "insert":
                    text_to_insert = action_to_redo["text"]
                    row, col = action_to_redo["position"]
                    self.insert_text_at_position(text_to_insert, row, col) 

                elif action_type == "delete_char":
                    y, x = action_to_redo["position"]
                    if not (0 <= y < len(self.text) and 0 <= x < len(self.text[y])): raise IndexError(f"Redo delete_char: Invalid position ({y},{x})")
                    self.text[y] = self.text[y][:x] + self.text[y][x + 1:]
                    self.cursor_y, self.cursor_x = y, x 

                elif action_type == "delete_newline":
                    y, x = action_to_redo["position"] 
                    if not (0 <= y < len(self.text) - 1): raise IndexError(f"Redo delete_newline: Not enough lines")
                    line_to_merge = self.text.pop(y + 1)
                    self.text[y] += line_to_merge
                    self.cursor_y, self.cursor_x = y, x 

                elif action_type == "delete_selection":
                    start_y, start_x = action_to_redo["start"]
                    end_y, end_x = action_to_redo["end"] 
                    if not (0 <= start_y < len(self.text) and 0 <= end_y < len(self.text) and \
                            0 <= start_x <= len(self.text[start_y]) and 0 <= end_x <= len(self.text[end_y])):
                        raise IndexError(f"Redo delete_selection: Invalid range")
                    _deleted_segments = self.delete_selected_text_internal(start_y, start_x, end_y, end_x)
                    # delete_selected_text_internal ставит курсор

                elif action_type == "block_indent":
                    final_selection = action_to_redo.get("final_selection")
                    indent_str = action_to_redo["indent_str"]
                    for y_idx in range(action_to_redo["start_line"], action_to_redo["end_line"] + 1):
                        if y_idx < len(self.text):
                            self.text[y_idx] = indent_str + self.text[y_idx]
                    if final_selection:
                        self.is_selecting, self.selection_start, self.selection_end = True, final_selection[0], final_selection[1]
                        if self.selection_end: self.cursor_y, self.cursor_x = self.selection_end
                
                elif action_type == "block_unindent":
                    final_selection = action_to_redo.get("final_selection")
                    for change in action_to_redo["changes"]:
                         idx = change["line_index"]
                         # Применяем new_text, так как redo повторяет действие, которое привело к new_text
                         if idx < len(self.text): # Проверка на случай, если текст сильно изменился
                             self.text[idx] = change["new_text"] 
                    if final_selection:
                        self.is_selecting, self.selection_start, self.selection_end = True, final_selection[0], final_selection[1]
                        if self.selection_end: self.cursor_y, self.cursor_x = self.selection_end

                elif action_type == "comment_block" or action_type == "uncomment_block":
                    changes = action_to_redo["changes"]
                    selection_state_after = action_to_redo.get("selection_after")
                    cursor_state_no_sel_after = action_to_redo.get("cursor_after_no_selection")

                    for change_item in changes:
                        idx = change_item["line_index"]
                        if idx < len(self.text):
                            self.text[idx] = change_item["new_text"] # Применяем результат операции
                    
                    if selection_state_after:
                         self.is_selecting, self.selection_start, self.selection_end = selection_state_after
                         if self.is_selecting and self.selection_end: self.cursor_y, self.cursor_x = self.selection_end
                    elif cursor_state_no_sel_after:
                        self.is_selecting = False
                        self.selection_start, self.selection_end = None, None
                        self.cursor_y, self.cursor_x = cursor_state_no_sel_after
                    else: 
                        self.is_selecting = False
                        self.selection_start, self.selection_end = None, None
                else:
                    logging.warning(f"Redo: Неизвестный тип действия: {action_type}")
                    self.undone_actions.append(action_to_redo)
                    return

            except Exception as e:
                logging.exception(f"Redo: Ошибка во время redo для типа действия {action_type}: {e}")
                self._set_status_message(f"Redo не удалось для {action_type}: {str(e)[:80]}...")
                self.undone_actions.append(action_to_redo) 
                self.text = current_state_snapshot["text"]
                self.cursor_y, self.cursor_x = current_state_snapshot["cursor_y"], current_state_snapshot["cursor_x"]
                self.is_selecting = current_state_snapshot["is_selecting"]
                self.selection_start, self.selection_end = current_state_snapshot["selection_start"], current_state_snapshot["selection_end"]
                return

            self.action_history.append(action_to_redo) 
            self.modified = True
            
            if action_type not in ["block_indent", "block_unindent", "comment_block", "uncomment_block"] or \
               (action_type in ["block_indent", "block_unindent"] and not action_to_redo.get("final_selection")) or \
               (action_type in ["comment_block", "uncomment_block"] and not action_to_redo.get("selection_after")):
                self.is_selecting = False
                self.selection_start = None
                self.selection_end = None

            self._set_status_message("Действие повторено")


    def _get_normalized_selection_range(self) -> tuple[tuple[int, int], tuple[int, int]] | None:
        """
        Вспомогательный метод. Возвращает нормализованные координаты выделения (start_pos, end_pos),
        где start_pos всегда левее/выше end_pos.
        Возвращает None, если нет активного выделения.
        """
        if not self.is_selecting or not self.selection_start or not self.selection_end:
            return None

        sy1, sx1 = self.selection_start
        sy2, sx2 = self.selection_end

        if (sy1 > sy2) or (sy1 == sy2 and sx1 > sx2):
            norm_start_y, norm_start_x = sy2, sx2
            norm_end_y, norm_end_x = sy1, sx1
        else:
            norm_start_y, norm_start_x = sy1, sx1
            norm_end_y, norm_end_x = sy2, sx2
        
        return (norm_start_y, norm_start_x), (norm_end_y, norm_end_x)
    

    def insert_text_at_position(self, text: str, row: int, col: int) -> None:
        """
        Низкоуровневая вставка текста `text` в логическую позицию (row, col).
        НЕ добавляет действие в историю — вызывающий метод отвечает за это.

        Курсор устанавливается сразу после вставленного текста.
        Выбрасывает IndexError, если row или col некорректны для вставки.
        """
        if not text:
            logging.debug("insert_text_at_position: пустой текст -> нет действия")
            return

        # Проверка валидности строки (row)
        if not (0 <= row < len(self.text)):
            msg = f"insert_text_at_position: невалидный индекс строки {row} (размер буфера {len(self.text)})"
            logging.error(msg)
            raise IndexError(msg) # <--- ИЗМЕНЕНИЕ: выбрасываем исключение

        # Проверка валидности колонки (col) для вставки
        # Колонка должна быть в пределах [0, длина_строки]
        current_line_len = len(self.text[row])
        if not (0 <= col <= current_line_len):
            msg = f"insert_text_at_position: невалидный индекс колонки {col} для строки {row} (длина строки {current_line_len})"
            logging.error(msg)
            # Если мы хотим быть строгими и не разрешать col > current_line_len даже если потом клэмпим
            # raise IndexError(msg) # <--- Раскомментировать, если нужна строгая проверка до клэмпинга
            # Однако, клэмпинг ниже может быть достаточным, если col лишь немного выходит за пределы.
            # Но для row ошибка точно фатальна.

        # Ограничиваем col реальной длиной строки (позволяет вставку в конец строки)
        col = max(0, min(col, current_line_len))

        logging.debug(f"insert_text_at_position: text={text!r} row={row} col={col}")

        lines_to_insert = text.split('\n')

        original_line_prefix = self.text[row][:col]
        original_line_suffix = self.text[row][col:] # "хвост" оригинальной строки

        # 1. Заменяем текущую строку: prefix + первая вставляемая строка
        self.text[row] = original_line_prefix + lines_to_insert[0]

        # 2. Вставляем промежуточные строки (если есть)
        # lines_to_insert[1:-1] - все строки между первой и последней
        for offset, line_content in enumerate(lines_to_insert[1:-1], start=1):
            self.text.insert(row + offset, line_content)

        # 3. Добавляем последнюю вставляемую строку + остаток оригинальной строки
        if len(lines_to_insert) > 1:
            # Вставляем как новую строку: последняя вставляемая строка + хвост оригинальной строки
            self.text.insert(row + len(lines_to_insert) - 1, lines_to_insert[-1] + original_line_suffix)
        else:
            # Вставка одной строки – просто восстанавливаем "хвост"
            self.text[row] += original_line_suffix

        # 4. Пересчитываем позицию курсора
        if len(lines_to_insert) == 1:
            # Остались на той же логической строке
            self.cursor_y = row
            self.cursor_x = col + len(lines_to_insert[0]) # Длина вставленного текста (первой/единственной строки)
        else:
            # Переместились на последнюю вставленную логическую строку
            self.cursor_y = row + len(lines_to_insert) - 1
            self.cursor_x = len(lines_to_insert[-1]) # Длина последней вставленной строки

        self.modified = True
        logging.debug(f"insert_text_at_position: курсор теперь в (y={self.cursor_y}, x={self.cursor_x})")


    def insert_text(self, text: str) -> None:
        """
        Основной публичный метод вставки текста.
        Обрабатывает активное выделение, удаляя его сначала (записывает действие 'delete_selection').
        Затем вставляет новый текст (записывает действие 'insert').
        Управляет историей действий для undo/redo.
        Курсор устанавливается после вставленного текста.
        """
        if not text:
            logging.debug("insert_text: пустой текст, нечего вставлять")
            return

        with self._state_lock: # Добавим блокировку и здесь для консистентности
            effective_insert_y, effective_insert_x = self.cursor_y, self.cursor_x

            if self.is_selecting:
                normalized_selection = self._get_normalized_selection_range()
                if normalized_selection:
                    norm_start_coords, norm_end_coords = normalized_selection
                    effective_insert_y, effective_insert_x = norm_start_coords 

                    logging.debug(f"insert_text: Удаление активного выделения с {norm_start_coords} по {norm_end_coords} перед вставкой.")
                    
                    deleted_segments = self.delete_selected_text_internal(
                        norm_start_coords[0], norm_start_coords[1],
                        norm_end_coords[0], norm_end_coords[1]
                    )
                    
                    self.action_history.append({
                        "type": "delete_selection",
                        "text": deleted_segments,
                        "start": norm_start_coords,
                        "end": norm_end_coords,
                    })
                    self.undone_actions.clear()
                    
                    # delete_selected_text_internal должен был установить курсор
                    effective_insert_y, effective_insert_x = self.cursor_y, self.cursor_x

                    self.is_selecting = False
                    self.selection_start = None
                    self.selection_end = None
                    logging.debug(f"insert_text: Выделение удалено. Курсор в ({self.cursor_y}, {self.cursor_x}).")

            if 0 <= effective_insert_y < len(self.text):
                logging.debug(f"insert_text: ПЕРЕД низкоуровневой вставкой в ({effective_insert_y},{effective_insert_x}), строка[{effective_insert_y}] = {self.text[effective_insert_y]!r}")
            else:
                logging.debug(f"insert_text: ПЕРЕД низкоуровневой вставкой в ({effective_insert_y},{effective_insert_x}). Длина буфера: {len(self.text)}")

            try:
                self.insert_text_at_position(text, effective_insert_y, effective_insert_x)
            except IndexError as e:
                logging.error(f"insert_text: Ошибка при вызове insert_text_at_position: {e}")
                self._set_status_message(f"Ошибка вставки: {e}")
                return

            if 0 <= self.cursor_y < len(self.text):
                logging.debug(f"insert_text: ПОСЛЕ низкоуровневой вставки, строка[{self.cursor_y}] = {self.text[self.cursor_y]!r}. Курсор в ({self.cursor_y}, {self.cursor_x}).")
            else:
                logging.debug(f"insert_text: ПОСЛЕ низкоуровневой вставки. Курсор в ({self.cursor_y}, {self.cursor_x}). Длина буфера: {len(self.text)}")

            self.action_history.append({
                "type": "insert",
                "text": text,
                "position": (effective_insert_y, effective_insert_x) 
            })
            self.undone_actions.clear()
            
            self.modified = True
            self._set_status_message("Текст вставлен")
            logging.debug(f"insert_text: Завершено. Текст '{text!r}' вставлен. Финальный курсор в ({self.cursor_y}, {self.cursor_x}).")


    def delete_text_internal(self, start_row: int, start_col: int, end_row: int, end_col: int) -> None:
        """Удаляет текст в диапазоне [start_row, start_col) .. [end_row, end_col)."""
        # Нормализация
        if (start_row > end_row) or (start_row == end_row and start_col > end_col):
            start_row, start_col, end_row, end_col = end_row, end_col, start_row, start_col

        # Валидация строк
        if not (0 <= start_row < len(self.text)) or not (0 <= end_row < len(self.text)):
            logging.error("delete_text_internal: row index out of bounds (%s..%s)", start_row, end_row)
            return

        # Корректируем колонки — нельзя вылезать за пределы соответствующих строк
        start_col = max(0, min(start_col, len(self.text[start_row])))
        end_col = max(0, min(end_col, len(self.text[end_row])))

        if start_row == end_row:
            self.text[start_row] = self.text[start_row][:start_col] + self.text[start_row][end_col:]
        else:
            # Часть первой строки до start_col + часть последней строки после end_col
            new_first = self.text[start_row][:start_col] + self.text[end_row][end_col:]
            # Удаляем полностью средние строки и последнюю
            del self.text[start_row + 1:end_row + 1]
            # Обновляем первую строку
            self.text[start_row] = new_first

        self.modified = True # Обновляем статус модификации


    def handle_block_indent(self):
        """Увеличивает отступ для выделенных строк."""
        if not self.is_selecting or not self.selection_start or not self.selection_end:
            return

        with self._state_lock:
            norm_range = self._get_normalized_selection_range()
            if not norm_range: return
            
            start_coords, end_coords = norm_range
            start_y, start_x_orig = start_coords
            end_y, end_x_orig = end_coords

            tab_size = self.config.get("editor", {}).get("tab_size", 4)
            use_spaces = self.config.get("editor", {}).get("use_spaces", True)
            indent_str = " " * tab_size if use_spaces else "\t"
            indent_width = len(indent_str) # Для Tab это 1, для пробелов tab_size

            affected_lines = [] # Для истории undo
            original_selection_for_undo = (self.selection_start, self.selection_end) # Сохраняем оригинальные координаты

            for y in range(start_y, end_y + 1):
                # Проверка, что строка существует (на всякий случай)
                if y >= len(self.text): continue
                
                self.text[y] = indent_str + self.text[y]
                affected_lines.append(y)

            self.modified = True
            
            # Корректируем выделение и курсор
            # Начало выделения
            self.selection_start = (start_y, start_x_orig + indent_width)
            # Конец выделения (если это та же строка, что и начало, или нет - логика одна)
            self.selection_end = (end_y, end_x_orig + indent_width)
            
            # Корректируем курсор, если он был одной из границ выделения
            # Обычно курсор находится в self.selection_end
            self.cursor_y = self.selection_end[0]
            self.cursor_x = self.selection_end[1]

            self.action_history.append({
                "type": "block_indent",
                "start_line": start_y,
                "end_line": end_y,
                "indent_str": indent_str,
                "original_selection": original_selection_for_undo,
                "final_selection": (self.selection_start, self.selection_end) # Новое состояние для redo
            })
            self.undone_actions.clear()
            self._set_status_message(f"Indented {len(affected_lines)} line(s)")
            logging.debug(f"Block indent: lines {start_y}-{end_y} indented by '{indent_str}'. New selection: {self.selection_start} -> {self.selection_end}")


    def handle_block_unindent(self):
        """Уменьшает отступ для выделенных строк."""
        if not self.is_selecting or not self.selection_start or not self.selection_end:
            return

        with self._state_lock:
            norm_range = self._get_normalized_selection_range()
            if not norm_range: return

            start_coords, end_coords = norm_range
            start_y, start_x_orig = start_coords
            end_y, end_x_orig = end_coords
            
            tab_size = self.config.get("editor", {}).get("tab_size", 4)
            use_spaces = self.config.get("editor", {}).get("use_spaces", True)
            # Определяем, что удалять: tab_size пробелов или один символ табуляции
            unindent_width = tab_size if use_spaces else 1
            
            changes_for_undo = [] # list of (line_index, removed_prefix_str)
            affected_lines_count = 0
            original_selection_for_undo = (self.selection_start, self.selection_end)

            for y in range(start_y, end_y + 1):
                if y >= len(self.text): continue

                line = self.text[y]
                removed_prefix = ""
                
                current_indent = 0
                for i in range(len(line)):
                    if line[i] == ' ':
                        current_indent += 1
                    elif line[i] == '\t':
                        # Если используем пробелы, таб считаем как tab_size пробелов для удаления
                        # Если используем табы, таб считаем как 1
                        current_indent += tab_size if use_spaces else 1
                        if not use_spaces: # Если работаем с табами, удаляем один таб
                           if i < unindent_width: # unindent_width для табов = 1
                               removed_prefix = line[:i+1]
                               self.text[y] = line[i+1:]
                           break # Обработали таб
                    else: # Не пробел и не таб
                        break # Конец отступа
                
                if use_spaces: # Если работаем с пробелами
                    chars_to_remove = 0
                    # Сколько символов пробелов в начале строки, но не более unindent_width
                    for i in range(len(line)):
                        if line[i] == ' ' and chars_to_remove < unindent_width:
                            chars_to_remove += 1
                        else:
                            break
                    if chars_to_remove > 0:
                        removed_prefix = line[:chars_to_remove]
                        self.text[y] = line[chars_to_remove:]
                
                if removed_prefix:
                    changes_for_undo.append((y, removed_prefix))
                    affected_lines_count += 1
            
            if affected_lines_count > 0:
                self.modified = True
                
                # Корректируем выделение и курсор
                # Уменьшаем x-координаты на фактическую ширину удаленного префикса
                # Это сложнее, так как ширина разная для разных строк.
                # Простой вариант: сдвинуть на unindent_width, если было удаление.
                # Более точный: найти максимальную ширину удаленного префикса.
                # Для простоты, сдвинем на unindent_width, если хоть что-то удалилось.
                # Это может быть неидеально, если отступы были рваные.
                
                # Попытка более точной корректировки:
                # Найдем, насколько изменилась длина самой короткой измененной строки в начале выделения.
                # Это очень упрощенно. Идеально - пересчитать get_string_width.
                
                # Простая корректировка: уменьшаем на unindent_width
                # (может быть неточным, если удалили меньше, чем unindent_width)
                sel_start_x_new = max(0, start_x_orig - unindent_width)
                sel_end_x_new = max(0, end_x_orig - unindent_width)

                self.selection_start = (start_y, sel_start_x_new)
                self.selection_end = (end_y, sel_end_x_new)
                
                self.cursor_y = self.selection_end[0]
                self.cursor_x = self.selection_end[1]

                self.action_history.append({
                    "type": "block_unindent",
                    "changes": changes_for_undo,
                    "original_selection": original_selection_for_undo,
                    "final_selection": (self.selection_start, self.selection_end)
                })
                self.undone_actions.clear()
                self._set_status_message(f"Unindented {affected_lines_count} line(s)")
                logging.debug(f"Block unindent: {affected_lines_count} lines from {start_y}-{end_y} unindented. New selection: {self.selection_start} -> {self.selection_end}")
            else:
                self._set_status_message("Nothing to unindent in selection")


    def unindent_current_line(self):
        """Уменьшает отступ текущей строки, если нет выделения."""
        if self.is_selecting: # Это не должно вызываться с выделением
            return

        with self._state_lock:
            y = self.cursor_y
            if y >= len(self.text): return

            line = self.text[y]
            if not line or not (line[0] == ' ' or line[0] == '\t'):
                self._set_status_message("Nothing to unindent at line start")
                return

            tab_size = self.config.get("editor", {}).get("tab_size", 4)
            use_spaces = self.config.get("editor", {}).get("use_spaces", True)
            unindent_char_count = tab_size if use_spaces else 1
            
            removed_prefix = ""

            if use_spaces:
                chars_to_remove = 0
                for i in range(len(line)):
                    if line[i] == ' ' and chars_to_remove < unindent_char_count:
                        chars_to_remove += 1
                    else:
                        break
                if chars_to_remove > 0:
                    removed_prefix = line[:chars_to_remove]
                    self.text[y] = line[chars_to_remove:]
                    self.cursor_x = max(0, self.cursor_x - chars_to_remove)
            else: # use_tabs
                if line.startswith('\t'):
                    removed_prefix = '\t'
                    self.text[y] = line[1:]
                    self.cursor_x = max(0, self.cursor_x - 1) # Или на 1, или на tab_size? Для таба - на 1.

            if removed_prefix:
                self.modified = True
                # Для простоты, запишем как "delete_char" или "delete_selection" с одной строкой.
                # Или создадим новый тип "unindent_line".
                # Запишем как "block_unindent" с одной строкой для консистентности.
                original_cursor_for_undo = (y, self.cursor_x + len(removed_prefix)) # Позиция до удаления

                self.action_history.append({
                    "type": "block_unindent", # Используем тот же тип
                    "changes": [(y, removed_prefix)],
                    # selection здесь не так важно, но для консистентности
                    "original_selection": ((y, original_cursor_for_undo[1]), (y, original_cursor_for_undo[1])),
                    "final_selection": ((y, self.cursor_x), (y, self.cursor_x))
                })
                self.undone_actions.clear()
                self._set_status_message("Line unindented")
                logging.debug(f"Unindented line {y}. Removed '{removed_prefix}'. Cursor at {self.cursor_x}")
            else:
                self._set_status_message("Nothing to unindent at line start")


    def handle_smart_unindent(self):
        """
        Интеллектуальное уменьшение отступа (аналог Shift+Tab).
        Если есть выделение - уменьшает отступ у всех выделенных строк.
        Если нет выделения - уменьшает отступ у текущей строки.
        """
        if self.is_selecting:
            self.handle_block_unindent()
        else:
            self.unindent_current_line()


# PYGMENTS  ------------------------------------------
    def apply_syntax_highlighting_with_pygments(self, lines: list[str], line_indices: list[int]):
        """
        Применяет подсветку синтаксиса к списку видимых строк с использованием Pygments.
        Сохраняет кэширование результатов для каждой строки.
        """
        if self._lexer is None:
            self.detect_language()
            logging.debug(f"Pygments apply_syntax: Initialized lexer: {self._lexer.name if self._lexer else 'None'}")

        # (Ваша карта token_color_map остается здесь)
        token_color_map = {
            Token.Keyword: curses.color_pair(2), Token.Keyword.Constant: curses.color_pair(2),
            Token.Keyword.Declaration: curses.color_pair(2), Token.Keyword.Namespace: curses.color_pair(2),
            Token.Keyword.Pseudo: curses.color_pair(2), Token.Keyword.Reserved: curses.color_pair(2),
            Token.Keyword.Type: curses.color_pair(2), Token.Name.Builtin: curses.color_pair(7),
            Token.Name.Function: curses.color_pair(3), Token.Name.Class: curses.color_pair(4),
            Token.Name.Decorator: curses.color_pair(5), Token.Name.Exception: curses.color_pair(8) | curses.A_BOLD,
            Token.Name.Variable: curses.color_pair(6), Token.Name.Attribute: curses.color_pair(6),
            Token.Name.Tag: curses.color_pair(5), Token.Literal.String: curses.color_pair(3),
            Token.Literal.String.Doc: curses.color_pair(1), Token.Literal.String.Interpol: curses.color_pair(3),
            Token.Literal.String.Escape: curses.color_pair(5), Token.Literal.String.Backtick: curses.color_pair(3),
            Token.Literal.String.Delimiter: curses.color_pair(3), Token.Literal.Number: curses.color_pair(4),
            Token.Literal.Number.Float: curses.color_pair(4), Token.Literal.Number.Hex: curses.color_pair(4),
            Token.Literal.Number.Integer: curses.color_pair(4), Token.Literal.Number.Oct: curses.color_pair(4),
            Token.Comment: curses.color_pair(1), Token.Comment.Multiline: curses.color_pair(1),
            Token.Comment.Preproc: curses.color_pair(1), Token.Comment.Special: curses.color_pair(1) | curses.A_BOLD,
            Token.Operator: curses.color_pair(6), Token.Operator.Word: curses.color_pair(2),
            Token.Punctuation: curses.color_pair(6), Token.Text: curses.color_pair(0),
            Token.Text.Whitespace: curses.color_pair(0), Token.Error: curses.color_pair(8) | curses.A_BOLD,
            Token.Generic.Heading: curses.color_pair(5) | curses.A_BOLD, Token.Generic.Subheading: curses.color_pair(5),
            Token.Generic.Deleted: curses.color_pair(8), Token.Generic.Inserted: curses.color_pair(4),
            Token.Generic.Emph: curses.color_pair(3) | curses.A_BOLD, Token.Generic.Strong: curses.color_pair(2) | curses.A_BOLD,
            Token.Generic.Prompt: curses.color_pair(7), Token.Generic.Output: curses.color_pair(0),
        }
 
        default_color = curses.color_pair(0)
        highlighted_lines_result = []

        for line_content, line_idx_val in zip(lines, line_indices):
            line_hash = hash(line_content)
            is_text_lexer_special_case = isinstance(self._lexer, TextLexer)
            cache_key = (line_idx_val, line_hash, id(self._lexer), is_text_lexer_special_case)

            if cache_key in self._token_cache:
                cached_segments = self._token_cache[cache_key] # Это должно быть list[tuple[str, int]]
                highlighted_lines_result.append(cached_segments)
                # Исправляем логгирование для кэша, чтобы соответствовать формату ниже
                logging.debug(f"Pygments apply_syntax: Cache HIT for line {line_idx_val}. Segments: {[(s[0].replace(chr(9),'/t/'), s[1]) for s in cached_segments if isinstance(s, tuple) and len(s) == 2 and isinstance(s[0], str)]}")
                continue
            
            logging.debug(f"Pygments apply_syntax: Cache MISS for line {line_idx_val}. Line content: '{line_content}'")
            current_line_highlighted_segments = [] # Это будет list[tuple[str, int]]

            if isinstance(self._lexer, TextLexer):
                logging.debug(f"Pygments apply_syntax: Using TextLexer direct passthrough for line {line_idx_val}.")
                if not line_content:
                    current_line_highlighted_segments.append(("", default_color))
                else:
                    current_line_highlighted_segments.append((line_content, default_color))
            else: 
                try:
                    logging.debug(f"Pygments apply_syntax: Lexing line {line_idx_val} with lexer '{self._lexer.name}': '{line_content}'")
                    # Убираем stripnl и ensurenl, они не стандартные аргументы lex()
                    raw_tokens_from_pygments = list(lex(line_content, self._lexer))
                    logging.debug(f"Pygments apply_syntax: Raw tokens for line {line_idx_val}: {raw_tokens_from_pygments}")

                    if not raw_tokens_from_pygments and line_content:
                        logging.warning(f"Pygments apply_syntax: No tokens returned for non-empty line {line_idx_val}: '{line_content}'. Using default color.")
                        current_line_highlighted_segments.append((line_content, default_color))
                    elif not line_content:
                        current_line_highlighted_segments.append(("", default_color))
                    else:
                        for token_type, text_value in raw_tokens_from_pygments:
                            color_attr = default_color
                            current_token_type_for_map = token_type
                            while current_token_type_for_map:
                                if current_token_type_for_map in token_color_map:
                                    color_attr = token_color_map[current_token_type_for_map]
                                    break
                                current_token_type_for_map = current_token_type_for_map.parent
                            current_line_highlighted_segments.append((text_value, color_attr))
                except Exception as e: # Ловим все исключения от lex()
                    logging.error(f"Pygments apply_syntax: Error tokenizing line {line_idx_val}: '{line_content}'. Error: {e}", exc_info=True)
                    # При ошибке, вся строка получает дефолтный цвет
                    current_line_highlighted_segments = [(line_content, default_color)] # Гарантируем list[tuple[str,int]]
            
            # Логгируем обработанные сегменты
            logging.debug(f"Pygments apply_syntax: Processed segments for line {line_idx_val} (lexer '{self._lexer.name if self._lexer else 'None'}'): {[(s[0].replace(chr(9),'/t/'), s[1]) for s in current_line_highlighted_segments if isinstance(s, tuple) and len(s) == 2 and isinstance(s[0], str)]}")
            
            self._token_cache[cache_key] = current_line_highlighted_segments # Сохраняем list[tuple[str,int]]
            if len(self._token_cache) > 2000:
                try: del self._token_cache[next(iter(self._token_cache))]
                except StopIteration: pass
            highlighted_lines_result.append(current_line_highlighted_segments)
        
        return highlighted_lines_result


    def set_initial_cursor_position(self):
        """Sets the initial cursor position and scrolling offsets."""
        self.cursor_x = 0
        self.cursor_y = 0
        self.scroll_top = 0
        self.scroll_left = 0
        # При сбросе позиции курсора, сбрасываем и выделение
        self.is_selecting = False
        self.selection_start = None
        self.selection_end = None
        self.highlighted_matches = [] # Сбрасываем подсветку поиска
        self.search_matches = []
        self.search_term = ""
        self.current_match_idx = -1


    def init_colors(self):
        """Создаём цветовые пары curses и заполняем self.colors."""
        if not curses.has_colors():
            self.colors = {}
            return
        curses.start_color()
        curses.use_default_colors()
        bg = -1  # прозрачный фон

        # Базовые цветовые пары (подправлены по теме)
        curses.init_pair(1, curses.COLOR_WHITE,    bg)  # comment -> белый
        curses.init_pair(2, curses.COLOR_BLUE,     bg)  # keyword -> синий
        curses.init_pair(3, curses.COLOR_GREEN,    bg)  # string -> зелёный
        curses.init_pair(4, curses.COLOR_MAGENTA,  bg)  # literal, number -> магента
        curses.init_pair(5, curses.COLOR_CYAN,     bg)  # decorator, tag -> циан
        curses.init_pair(6, curses.COLOR_WHITE,    bg)  # operator, variable -> белый
        curses.init_pair(7, curses.COLOR_YELLOW,   bg)  # builtin, line_number -> жёлтый
        curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_RED)    # error -> чёрный на красном
        curses.init_pair(9, curses.COLOR_BLACK, curses.COLOR_YELLOW) # search_highlight -> чёрный на жёлтом
        # Дополнительные пары для статуса и Git
        curses.init_pair(10, curses.COLOR_WHITE,   bg)  # status (bright_white)
        curses.init_pair(11, curses.COLOR_RED,     bg)  # status_error (красный текст)
        curses.init_pair(12, curses.COLOR_GREEN,   bg)  # git_info (зелёный текст)
        curses.init_pair(13, curses.COLOR_GREEN,   bg)  # git_dirty (будет использован с A_BOLD)

        self.colors = {
            "comment":   curses.color_pair(1),
            "keyword":   curses.color_pair(2),
            "string":    curses.color_pair(3),
            "literal":   curses.color_pair(4),
            "number":    curses.color_pair(4),
            "type":      curses.color_pair(7),  # классы/типы – жёлтым
            "decorator": curses.color_pair(5),
            "tag":       curses.color_pair(5),
            "operator":  curses.color_pair(6),
            "variable":  curses.color_pair(6),
            "builtins":  curses.color_pair(7),
            "line_number": curses.color_pair(7),
            "error":     curses.color_pair(8) | curses.A_BOLD,
            "status":    curses.color_pair(10) | curses.A_BOLD,
            "status_error": curses.color_pair(11) | curses.A_BOLD,
            "search_highlight": curses.color_pair(9),
            "git_info":  curses.color_pair(12),
            "git_dirty": curses.color_pair(13) | curses.A_BOLD,
            # Дополнительные цвета/сокращения
            "green":     curses.color_pair(12),  # зелёный (используем пару12 как green)
        }


    def detect_language(self):
        """
        Определяет язык файла на основе расширения или содержимого и устанавливает лексер для подсветки.
        Сбрасывает кэш токенов при смене лексера.
        """
        new_lexer = None
        try:
            if self.filename and self.filename != "noname":
                extension = os.path.splitext(self.filename)[1].lower().lstrip(".")
                if extension:
                    try:
                        new_lexer = get_lexer_by_name(extension)
                        logging.debug(f"Pygments: Detected language by extension: {extension} -> {new_lexer.name}")
                    except Exception:
                        logging.debug(f"Pygments: No lexer found for extension {extension}. Trying content guess.")
                        # Если расширение не помогло, пробуем угадать по содержимому
                        content = "\n".join(self.text)[:10000] # Увеличим объем для угадывания
                        try:
                            new_lexer = guess_lexer(content, stripall=True) # stripall=True может помочь
                            logging.debug(f"Pygments: Guessed language by content: {new_lexer.name}")
                        except Exception:
                             logging.debug("Pygments: Guesser failed. Falling back to TextLexer.")
                             new_lexer = TextLexer() # Fallback
                else: # Если нет расширения
                     content = "\n".join(self.text)[:10000]
                     try:
                        new_lexer = guess_lexer(content, stripall=True)
                        logging.debug(f"Pygments: Guessed language by content (no extension): {new_lexer.name}")
                     except Exception:
                         logging.debug("Pygments: Guesser failed (no extension). Falling back to TextLexer.")
                         new_lexer = TextLexer() # Fallback
            else: # Если filename None или "noname"
                content = "\n".join(self.text)[:10000]
                try:
                    new_lexer = guess_lexer(content, stripall=True)
                    logging.debug(f"Pygments: Guessed language by content (no file): {new_lexer.name}")
                except Exception:
                     logging.debug("Pygments: Guesser failed (no file). Falling back to TextLexer.")
                     new_lexer = TextLexer() # Fallback

        except Exception as e:
            logging.error(f"Failed to detect language for {self.filename or 'no file'}: {e}", exc_info=True)
            new_lexer = TextLexer()  # Fallback на текстовый лексер

        # Сбрасываем кэш токенов только если лексер изменился
        if self._lexer is not new_lexer: # Сравниваем объекты лексеров
             logging.debug(f"Pygments: Lexer changed from {self._lexer.name if self._lexer else 'None'} to {new_lexer.name}. Clearing token cache.")
             self._token_cache = {}
             self._lexer = new_lexer
        # else:
             # logging.debug(f"Pygments: Lexer remained {self._lexer.name if self._lexer else 'None'}.")


    def delete_char_internal(self, row: int, col: int) -> str:
        """
        Удаляет **один** символ по (row, col) без записи в history.
        Возвращает удалённый символ (или '').
        """
        if not (0 <= row < len(self.text)):
            return ""
        line = self.text[row]
        if not (0 <= col < len(line)):
            return ""
        removed = line[col]
        self.text[row] = line[:col] + line[col + 1 :]
        self.modified = True
        logging.debug("delete_char_internal: removed %r at (%s,%s)", removed, row, col)
        return removed


    def handle_resize(self):
        """
        Обрабатывает событие изменения размера окна.
        Вызывается при изменении размера окна терминала.
        """
        logging.debug("handle_resize called")
        try:
            self.height, self.width = self.stdscr.getmaxyx()
            self._set_status_message(f"Resized to {self.width}x{self.height}")
            self._draw_buffer()
        except Exception as e:
            logging.error(f"Error in handle_resize: {e}", exc_info=True)
            self._set_status_message("Resize error (see log)")


    def _draw_buffer(self) -> None:
        """
        Paint visible part of self.text to `stdscr`.
        Handles both vertical (scroll_top) and horizontal (scroll_left) scrolling.
        """
        logging.debug(
            "Drawing buffer: cur=(%s,%s) scroll=(%s,%s)",
            self.cursor_x, self.cursor_y, self.scroll_left, self.scroll_top
        )
        try:
            # ── draw lines ──────────────────────────────────────────────
            for scr_y, line in enumerate(
                self.text[self.scroll_top : self.scroll_top + self.height]
            ):
                # Cut line by horizontal scroll, then clip to window width ‑ 1
                visible = line[self.logical_offset_by_width(line, self.scroll_left) :]
                # soft‑clip by width
                out, w_acc = "", 0
                for ch in visible:
                    w = wcwidth(ch)
                    if w < 0:       # skip control char
                        continue
                    if w_acc + w > self.width - 1:
                        break
                    out += ch
                    w_acc += w

                self.stdscr.move(scr_y, 0)
                self.stdscr.clrtoeol()
                self.stdscr.addstr(scr_y, 0, out)

            # ── place cursor ───────────────────────────────────────────
            scr_y = self.cursor_y - self.scroll_top
            # width up to logical cursor, then minus horizontal scroll
            scr_x = self.get_display_width(
                self.text[self.cursor_y][: self.cursor_x]
            ) - self.scroll_left

            # clamp to window
            scr_y = max(0, min(scr_y, self.height - 1))
            scr_x = max(0, min(scr_x, self.width - 1))
            self.stdscr.move(scr_y, scr_x)

        except Exception as exc:
            logging.error("Error in _draw_buffer: %s", exc, exc_info=True)
            self._set_status_message("Draw error (see log)")


    def logical_offset_by_width(self, line: str, cells: int) -> int:
        """
        Return index in *line* such that rendered width == *cells*.
        Используется, когда нужно получить логический индекс
        символа, с которого виден экран после горизонтального скролла.
        """
        acc = 0
        for i, ch in enumerate(line):
            w = wcwidth(ch)
            if w < 0:
                continue
            if acc + w > cells:
                return i
            acc += w
        return len(line)



    # ===================== Курсор: страничные и домашние клавиши ======================

    # ────────── Курсор: базовое перемещение ──────────
    # `array left (<-) `
    def handle_left(self) -> None:
        """
        Move cursor one position to the left.
        If at column 0 — jump to the end of the previous line (if any).
        """
        if self.cursor_x > 0:
            self.cursor_x -= 1
        elif self.cursor_y > 0:
            self.cursor_y -= 1
            self.cursor_x = len(self.text[self.cursor_y])

        self._clamp_scroll()                       # корректируем scroll_top / scroll_left
        logging.debug("cursor ← (%d,%d)", self.cursor_y, self.cursor_x)

    # `array right (->)`
    def handle_right(self) -> None:
        """
        Move cursor one position to the right.
        • Если курсор в середине строки — сдвигаемся на 1 «печатаемую» колонку,
        пропуская символы нулевой ширины (комбинирующие).
        • Если в конце строки и есть следующая строка — переходим в её начало.
        При любом перемещении корректируем scroll‑координаты.
        """
        try:
            line = self.text[self.cursor_y]

            #  внутри строки
            if self.cursor_x < len(line):
                self.cursor_x += 1
                # пропускаем символы нулевой ширины (diacritics/ZWJ/VS16…)
                while self.cursor_x < len(line) and wcwidth(line[self.cursor_x]) == 0:
                    self.cursor_x += 1

            #   конец строки → начало следующей
            elif self.cursor_y < len(self.text) - 1:
                self.cursor_y += 1
                self.cursor_x = 0

            # гарантируем, что курсор и прокрутка валидны
            self._clamp_scroll()
            logging.debug("cursor → (%d,%d)", self.cursor_y, self.cursor_x)

        except Exception:
            logging.exception("Error in handle_right")
            self._set_status_message("Cursor error (see log)")

    # `array up`
    def handle_up(self) -> None:
        """
        Move cursor one line up.

        • Если уже на первой строке – ничего не делаем.  
        • Колонка сохраняется, но не выходит за пределы новой строки.  
        • После перемещения вызываем _clamp_scroll() – вертикальная прокрутка всегда валидна.
        """
        if self.cursor_y > 0:
            self.cursor_y -= 1
            # не позволяем колонке «выдавать» за конец строки
            self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))

        self._clamp_scroll()
        logging.debug("cursor ↑ (%d,%d)", self.cursor_y, self.cursor_x)

    # `array down`
    def handle_down(self) -> None:
        """
        Move cursor one line down.

        • Если внизу файла – остаёмся на месте.  
        • Колонка сохраняется, но не выходит за пределы новой строки.  
        • После перемещения вызываем _clamp_scroll() для корректной прокрутки.
        """
        if self.cursor_y < len(self.text) - 1:
            self.cursor_y += 1
            self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))

        self._clamp_scroll()
        logging.debug("cursor ↓ (%d,%d)", self.cursor_y, self.cursor_x)


    def _clamp_scroll(self) -> None:
        """
        Гарантирует, что scroll_top и scroll_left
        всегда удерживают курсор в видимой области.
        """
        # размеры окна
        height, width = self.stdscr.getmaxyx()
        # область текста по вертикали (высота окна минус строки номера и статус-бара)
        text_height = max(1, height - 2)

        # Вертикальная прокрутка
        if self.cursor_y < self.scroll_top:
            self.scroll_top = self.cursor_y
        elif self.cursor_y >= self.scroll_top + text_height:
            self.scroll_top = self.cursor_y - text_height + 1

        # Горизонтальная прокрутка — считаем дисплейную ширину до курсора
        disp_x = self.get_display_width(self.text[self.cursor_y][: self.cursor_x])
        if disp_x < self.scroll_left:
            self.scroll_left = disp_x
        elif disp_x >= self.scroll_left + width:
            self.scroll_left = disp_x - width + 1

        # Гарантируем неотрицательные значения
        self.scroll_top = max(0, self.scroll_top)
        self.scroll_left = max(0, self.scroll_left)


    def get_display_width(self, text: str) -> int:
        """
        Return the printable width of *text* in terminal cells.

        * Uses wcwidth / wcswidth to honour full‑width CJK.
        * Treats non‑printable characters (wcwidth == ‑1) as width 0.
        """
        # Fast‑path for ASCII
        if text.isascii():
            return len(text)

        width = wcswidth(text)
        if width < 0:          # Means string contains non‑printables
            width = 0
            for ch in text:
                w = wcwidth(ch)
                width += max(w, 0)   # non‑printables add 0
        return width

    def _ensure_cursor_in_bounds(self) -> None:
        """
        Clamp `cursor_x` / `cursor_y` so they always reference a valid position
        inside `self.text`.

        • Если буфер пуст → создаётся пустая строка `[""]`, и курсор ставится в (0, 0).  
        • `cursor_y` ограничивается диапазоном [0 … len(text)-1].  
        • `cursor_x` ограничивается диапазоном [0 … len(current_line)].  
        • После коррекции выводится отладочный лог.
        """
        # Пустой буфер – гарантируем хотя бы одну строку
        if not self.text:
            self.text.append("")

        max_y = len(self.text) - 1
        self.cursor_y = max(0, min(self.cursor_y, max_y))

        max_x = len(self.text[self.cursor_y])
        self.cursor_x = max(0, min(self.cursor_x, max_x))

        logging.debug("Cursor clamped → (%d,%d) [line_len=%d]",
                    self.cursor_y, self.cursor_x, max_x)


    # # ────────── Курсор: Home/End , pUp/pDown ──────────
    # key home
    def handle_home(self):
        """Moves the cursor to the beginning of the current line (after leading whitespace)."""
        # "Умный" Home: если курсор не в начале отступа, перемещает в начало отступа;
        # если в начале отступа (или нет отступа), перемещает в абсолютное начало строки.
        current_line = self.text[self.cursor_y]
        leading_whitespace = re.match(r"^(\s*)", current_line)
        indent_end = leading_whitespace.end() if leading_whitespace else 0

        if self.cursor_x != indent_end:
            self.cursor_x = indent_end
        else:
            self.cursor_x = 0
        # Горизонтальная прокрутка будет скорректирована в _position_cursor
    
    # key end
    def handle_end(self):
        """Moves the cursor to the end of the current line."""
        self.cursor_x = len(self.text[self.cursor_y])
        # Горизонтальная прокрутка будет скорректирована в _position_cursor

    # key page-up
    def handle_page_up(self):
        """Moves the cursor up by one screen height."""
        # Получаем текущий размер окна, чтобы определить высоту области текста
        height, width = self.stdscr.getmaxyx()
        text_area_height = max(1, height - 2) # Высота текста = высота окна - 1 строка для номера + 1 для статуса

        # Определяем новую позицию scroll_top
        new_scroll_top = max(0, self.scroll_top - text_area_height)
        lines_moved = self.scroll_top - new_scroll_top # Сколько строк реально сдвинулся вид

        self.scroll_top = new_scroll_top
        self.cursor_y = max(0, self.cursor_y - lines_moved) # Сдвигаем курсор на столько же строк

        # Убедимся, что курсор не выходит за пределы новой строки
        self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))

        # Горизонтальная прокрутка будет скорректирована в _position_cursor

    # ###  key page-down
    def handle_page_down(self):
        """Moves the cursor down by one screen height."""
        # Получаем текущий размер окна
        height, width = self.stdscr.getmaxyx()
        text_area_height = max(1, height - 2) # Высота области текста

        # Определяем новую позицию scroll_top
        # scroll_top не может опускаться ниже, чем len(self.text) - text_area_height
        new_scroll_top = min(len(self.text) - text_area_height, self.scroll_top + text_area_height)
        new_scroll_top = max(0, new_scroll_top) # Не меньше 0

        lines_moved = new_scroll_top - self.scroll_top # Сколько строк реально сдвинулся вид

        self.scroll_top = new_scroll_top
        self.cursor_y = min(len(self.text) - 1, self.cursor_y + lines_moved) # Сдвигаем курсор на столько же строк

        # Убедимся, что курсор не выходит за пределы новой строки
        self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))

        # Горизонтальная прокрутка будет скорректирована в _position_cursor

    def handle_backspace(self) -> None:
        """
        Логика Backspace с полной поддержкой блочного выделения.

        - Если есть выделение – удалить *весь* диапазон (как Del).
        - Иначе:
            – курсор не в 0-й колонке → удалить символ слева;
            – курсор в 0-й колонке и строка не первая → склеить текущую с предыдущей.

        Все изменения заносятся в `action_history`, стек redo (`undone_actions`)
        очищается. После операции курсор и прокрутка гарантированно валидны.
        """
        with self._state_lock:
            try:
                # ── 1. Удаляем выделенный блок (если есть) ───────────────────
                if self.is_selecting: # Проверяем только is_selecting, т.к. start/end должны быть валидны
                    normalized_range = self._get_normalized_selection_range()
                    if not normalized_range:
                        logging.warning("handle_backspace: is_selecting=True, но не удалось получить нормализованный диапазон.")
                        # Можно сбросить is_selecting, если диапазон невалиден
                        self.is_selecting = False
                        self.selection_start = None
                        self.selection_end = None
                        return # Или перейти к логике удаления одного символа, если это желаемое поведение

                    norm_start_coords, norm_end_coords = normalized_range
                    
                    logging.debug(f"handle_backspace: Удаление выделения с {norm_start_coords} по {norm_end_coords}")

                    # Удаляем выделенный текст, используя НОРМАЛИЗОВАННЫЕ координаты
                    # delete_selected_text_internal должен устанавливать курсор в norm_start_coords
                    removed_segments = self.delete_selected_text_internal(
                        norm_start_coords[0], norm_start_coords[1],
                        norm_end_coords[0], norm_end_coords[1]
                    )
                    
                    # Записываем действие в историю, используя НОРМАЛИЗОВАННЫЕ координаты
                    self.action_history.append({
                        "type": "delete_selection",
                        "text": removed_segments,    # list[str]
                        "start": norm_start_coords,
                        "end": norm_end_coords,
                    })
                    
                    # Курсор должен был быть установлен в norm_start_coords методом delete_selected_text_internal
                    # self.cursor_y, self.cursor_x = norm_start_coords # Можно продублировать для уверенности или убрать, если метод надежен

                    self.is_selecting = False
                    self.selection_start = None
                    self.selection_end = None
                    self._set_status_message("Блок удален")
                    logging.debug(f"handle_backspace: Блок удален. Курсор в ({self.cursor_y}, {self.cursor_x})")

                # ── 2. Удаляем символ слева от курсора (если нет выделения и курсор не в начале строки) ──
                elif self.cursor_x > 0:
                    y, x = self.cursor_y, self.cursor_x
                    
                    # Проверка, что строка y существует (на всякий случай)
                    if y >= len(self.text):
                        logging.warning(f"handle_backspace: cursor_y {y} вне границ для длины текста {len(self.text)}")
                        return

                    line = self.text[y]
                    deleted_char = line[x - 1]
                    self.text[y] = line[:x - 1] + line[x:]
                    self.cursor_x -= 1 # Смещаем курсор влево

                    self.action_history.append({
                        "type": "delete_char", # Тип действия для удаления одного символа (backspace)
                        "text": deleted_char,
                        "position": (y, self.cursor_x), # Позиция КУДА встал курсор (т.е. x-1)
                                                        # или (y,x) - позиция удаленного символа *относительно начала строки до удаления*?
                                                        # Для Backspace, 'position' обычно это (y, x-1) - куда курсор встал.
                                                        # 'text' - это удаленный символ.
                                                        # undo(delete_char) вставит 'text' в 'position' и поставит курсор в 'position'.
                                                        # Если position (y,x-1), то вставится line[:x-1] + char + line[x-1:]. Верно.
                    })
                    self._set_status_message("Символ удален")
                    logging.debug(f"handle_backspace: Символ '{deleted_char}' в ({y},{x-1}) удален (оригинальный x был {x})")

                # ── 3. В начале строки (cursor_x == 0), не первая строка: склеиваем с предыдущей ──
                elif self.cursor_y > 0: # cursor_x здесь равен 0
                    current_row_idx = self.cursor_y
                    prev_row_idx = current_row_idx - 1

                    # Содержимое текущей строки, которое будет перемещено
                    text_moved_up = self.text[current_row_idx]
                    
                    # Позиция курсора после объединения будет в конце бывшей предыдущей строки
                    new_cursor_x = len(self.text[prev_row_idx])
                    
                    self.text[prev_row_idx] += text_moved_up
                    del self.text[current_row_idx]

                    self.cursor_y = prev_row_idx
                    self.cursor_x = new_cursor_x

                    self.action_history.append({
                        "type": "delete_newline", # Тип действия для объединения строк (backspace на newline)
                        "text": text_moved_up,    # Текст, который был поднят наверх
                        "position": (self.cursor_y, self.cursor_x), # Позиция курсора ПОСЛЕ объединения
                                                                    # (т.е. место, где был newline)
                    })
                    self._set_status_message("Строки объединены")
                    logging.debug(f"handle_backspace: Строка {current_row_idx} объединена с {prev_row_idx}. Курсор в ({self.cursor_y},{self.cursor_x})")

                else:
                    # Курсор в (0,0) - начало файла
                    logging.debug("handle_backspace: В начале файла – нет действия")
                    self._set_status_message("Начало файла")
                    # Ничего не делаем, modified не меняется, в историю не пишем

                # ── Финализация (только если было изменение) ──────────────────
                if self.modified: # Проверяем, было ли реальное изменение
                    self.undone_actions.clear()
                    self._ensure_cursor_in_bounds() # Убедиться, что курсор в допустимых границах
                    self._clamp_scroll()            # Скорректировать прокрутку, если нужно
                # Если изменений не было (например, backspace в (0,0)), то modified=False, и эти шаги не нужны.
                # `self.modified = True` должно устанавливаться внутри каждого блока, где происходит изменение.
                # В блоке `else` (курсор в (0,0)) `self.modified` не трогаем.

            except Exception: # Перехватываем любые неожиданные ошибки
                logging.exception("handle_backspace: Произошла ошибка")
                self._set_status_message("Ошибка Backspace (см. лог)")


    def handle_delete(self):
        """
        Удаляет символ под курсором или выделенный текст.
        Добавляет действие в историю для отмены.
        """
        with self._state_lock:  # Блокировка для безопасного изменения текста и истории
            if self.is_selecting: # self.selection_start и self.selection_end должны быть не None, если is_selecting=True
                normalized_range = self._get_normalized_selection_range()
                if not normalized_range:
                    # Этого не должно происходить, если is_selecting=True и selection_start/end установлены,
                    # но это безопасная проверка.
                    logging.warning("handle_delete: is_selecting=True, но не удалось получить нормализованный диапазон.")
                    return

                norm_start_coords, norm_end_coords = normalized_range
                
                logging.debug(f"handle_delete: Удаление выделения с {norm_start_coords} по {norm_end_coords}")
                
                # Удаляем выделенный текст, используя НОРМАЛИЗОВАННЫЕ координаты
                # delete_selected_text_internal должен быть готов к нормализованным координатам
                # и, в идеале, сам устанавливать курсор в norm_start_coords.
                deleted_text_lines = self.delete_selected_text_internal(
                    norm_start_coords[0], norm_start_coords[1],
                    norm_end_coords[0], norm_end_coords[1]
                )

                # Добавляем действие в историю, используя НОРМАЛИЗОВАННЫЕ координаты
                action = {
                    "type": "delete_selection",
                    "text": deleted_text_lines,         # Удаленный текст (list[str])
                    "start": norm_start_coords,         # Нормализованное начало
                    "end": norm_end_coords            # Нормализованный конец
                }
                self.action_history.append(action)
                self.modified = True
                
                # Устанавливаем курсор в начало удаленного (нормализованного) выделения.
                # Это также может делать delete_selected_text_internal. Если так, эта строка дублирует, но не вредит.
                # Если delete_selected_text_internal НЕ ставит курсор, эта строка КРИТИЧНА.
                self.cursor_y, self.cursor_x = norm_start_coords
                
                self.is_selecting = False
                self.selection_start = None
                self.selection_end = None
                self.undone_actions.clear()  # Очищаем историю redo
                self._set_status_message("Выделение удалено")
                logging.debug(f"handle_delete: Выделение удалено. Курсор установлен в {self.cursor_y}, {self.cursor_x}")

            else:
                # Нет выделения, обычный Delete (удаление символа или объединение строк)
                y, x = self.cursor_y, self.cursor_x
                
                if y >= len(self.text):
                    logging.warning(f"handle_delete: cursor_y {y} вне границ для длины текста {len(self.text)}")
                    return

                current_line_len = len(self.text[y])

                if x < current_line_len:
                    # Удаляем символ под курсором
                    deleted_char = self.text[y][x]
                    self.text[y] = self.text[y][:x] + self.text[y][x + 1:]
                    self.modified = True

                    self.action_history.append({
                        "type": "delete_char",
                        "text": deleted_char,
                        "position": (y, x) # Позиция курсора остается той же
                    })
                    self.undone_actions.clear()
                    logging.debug(f"handle_delete: Удален символ '{deleted_char}' в ({y}, {x})")

                elif y < len(self.text) - 1:
                    # Курсор в конце строки (x == current_line_len), удаляем перенос строки
                    next_line_idx = y + 1
                    next_line_content = self.text[next_line_idx]
                    
                    # Сохраняем позицию курсора *перед* объединением для истории
                    # Для delete_newline, "position" это место, где был newline,
                    # и куда встанет курсор после объединения.
                    original_cursor_pos_for_history = (y, x) # x здесь это len(self.text[y])

                    self.text[y] += self.text.pop(next_line_idx)
                    self.modified = True
                    
                    # Курсор остается в (y,x) на объединенной строке
                    self.cursor_y = y
                    self.cursor_x = x 

                    self.action_history.append({
                        "type": "delete_newline",
                        "text": next_line_content, # Содержимое присоединенной строки
                        "position": original_cursor_pos_for_history # Позиция, где был newline
                    })
                    self.undone_actions.clear()
                    logging.debug(f"handle_delete: Удален newline и объединена строка {next_line_idx} в {y}. Курсор в ({self.cursor_y}, {self.cursor_x})")
                else:
                    logging.debug("handle_delete: Курсор в конце файла, нет действия.")


    def delete_selected_text_internal(self, start_y: int, start_x: int, end_y: int, end_x: int) -> list[str]:
        """
        Низкоуровневый метод: удаляет текст между нормализованными (start_y, start_x) и (end_y, end_x).
        Возвращает удаленный текст как список строк (сегментов).
        Устанавливает курсор в (start_y, start_x).
        НЕ записывает действие в историю. Устанавливает self.modified = True.
        """
        logging.debug(f"delete_selected_text_internal: Удаление с ({start_y},{start_x}) по ({end_y},{end_x})")
        
        # Проверка валидности координат (базовая)
        if not (0 <= start_y < len(self.text) and 0 <= end_y < len(self.text) and \
                0 <= start_x <= len(self.text[start_y]) and 0 <= end_x <= len(self.text[end_y])):
            logging.error(f"delete_selected_text_internal: Невалидные координаты для удаления: ({start_y},{start_x}) по ({end_y},{end_x})")
            # В реальном коде здесь можно выбросить исключение или вернуть пустой список
            return []

        deleted_segments = []

        if start_y == end_y:
            # Удаление в пределах одной строки
            line_content = self.text[start_y]
            deleted_segments.append(line_content[start_x:end_x])
            self.text[start_y] = line_content[:start_x] + line_content[end_x:]
        else:
            # Многострочное удаление
            # Первая строка (частично)
            line_start_content = self.text[start_y]
            deleted_segments.append(line_start_content[start_x:])
            
            # Сохраняем часть строки start_y, которая останется
            remaining_prefix_on_start_line = line_start_content[:start_x]

            # Последняя строка (частично)
            line_end_content = self.text[end_y]
            deleted_segments.append(line_end_content[:end_x]) # Это неверно, если start_y+1 == end_y, нужно собирать полные строки между
            
            # Сохраняем часть строки end_y, которая останется (и будет присоединена к prefix)
            remaining_suffix_on_end_line = line_end_content[end_x:]

            # Удаляем полные строки между start_y и end_y (не включая их сами)
            # Строки для удаления: от start_y + 1 до end_y - 1
            # Сначала соберем их для deleted_segments
            if end_y > start_y + 1:
                 # Вставляем на вторую позицию, т.к. первый сегмент уже есть
                deleted_segments[1:1] = self.text[start_y + 1 : end_y]


            # Обновляем текст
            self.text[start_y] = remaining_prefix_on_start_line + remaining_suffix_on_end_line
            
            # Удаляем строки, которые были объединены или полностью удалены
            # Индексы для удаления: от start_y + 1 до end_y включительно
            del self.text[start_y + 1 : end_y + 1]
        
        self.cursor_y = start_y
        self.cursor_x = start_x
        self.modified = True
        logging.debug(f"delete_selected_text_internal: Удалено. Курсор в ({self.cursor_y},{self.cursor_x}). Удаленные сегменты: {deleted_segments!r}")
        return deleted_segments


    # для key TAB вспомогательный метод
    def handle_tab(self):
        """Inserts spaces or a tab character depending on configuration."""
        tab_size = self.config.get("editor", {}).get("tab_size", 4)
        use_spaces = self.config.get("editor", {}).get("use_spaces", True)
        # current_line = self.text[self.cursor_y] # Не используется, можно убрать

        # Текст для вставки
        # Переименована переменная, чтобы не совпадать с именем метода self.insert_text
        text_to_insert_val = " " * tab_size if use_spaces else "\t" 

        # Используем insert_text для корректного добавления в историю и сброса выделения
        self.insert_text(text_to_insert_val)
        logging.debug(f"handle_tab: Inserted {text_to_insert_val!r} into line {self.cursor_y}, text now: {self.text[self.cursor_y]!r}")
    
    # key TAB - основной метод
    # def handle_smart_tab(self):
    #     """
    #     Если курсор в начале строки (cursor_x == 0),
    #     копирует отступ (пробелы/таб) предыдущей строки.
    #     Если копирование невозможно или отступ пустой,
    #     вставляет стандартный отступ (tab_size или '\t') в начале строки.
    #     Иначе (курсор не в начале строки) – вставляет стандартный отступ в текущей позиции.
    #     """
    #     # Если есть выделение, Tab должен удалить выделение и вставить стандартный отступ
    #     # Или сдвинуть выделенные строки? По стандарту: удалить выделение и вставить Tab.
    #     if self.is_selecting:
    #          # Удаление выделения уже добавит действие в историю и сбросит undone_actions
    #          self.delete_selected_text_internal(*self.selection_start, *self.selection_end)
    #          self.selection_start = self.selection_end = None
    #          self.is_selecting = False
    #          # Вставляем один таб в позицию курсора
    #          self.handle_tab() # handle_tab вызовет insert_text, который добавит действие
    #          return # Выходим после обработки выделения

    #     # Если нет выделения
    #     if self.cursor_x > 0:
    #         # Если курсор не в начале строки, используем обычный таб в текущей позиции
    #         # handle_tab вызовет insert_text, который добавит действие
    #         self.handle_tab()
    #         return

    #     # Если курсор в начале строки (self.cursor_x == 0)
    #     indentation_to_copy = ""
    #     if self.cursor_y > 0:
    #         # Убедимся, что prev_line_idx корректен
    #         prev_line_idx = self.cursor_y - 1
    #         if 0 <= prev_line_idx < len(self.text):
    #             prev_line = self.text[prev_line_idx]
    #             m = re.match(r"^(\s*)", prev_line)
    #             if m:
    #                 indentation_to_copy = m.group(1)
    #         else:
    #             logging.warning(f"Smart tab: Invalid previous line index {prev_line_idx}")


    #     # Определяем текст для вставки: скопированный отступ или стандартный таб/пробелы
    #     if not indentation_to_copy:
    #         # Если нет отступа для копирования (первая строка или предыдущая строка без отступа),
    #         # вставляем стандартный таб/пробелы в начале строки
    #         tab_size = self.config.get("editor", {}).get("tab_size", 4)
    #         use_spaces = self.config.get("editor", {}).get("use_spaces", True)
    #         insert_text = " " * tab_size if use_spaces else "\t"
    #     else:
    #         # Используем скопированный отступ
    #         insert_text = indentation_to_copy

    #     # Вставляем текст отступа. Используем insert_text для корректной обработки undo
    #     # insert_text сам добавит действие в историю и сбросит undone_actions
    #     self.insert_text(insert_text)
    #     logging.debug(f"handle_smart_tab: Inserted {insert_text!r} into line {self.cursor_y}, text now: {self.text[self.cursor_y]!r}")
    #     # Курсор уже установлен в конец вставленного текста в insert_text

    #     logging.debug(f"Smart tab: Inserted indentation {insert_text!r} at line start")

    def handle_smart_tab(self):
        """
        Если есть выделение - увеличивает отступ у всех выделенных строк.
        Если курсор в начале строки (cursor_x == 0) без выделения,
        копирует отступ (пробелы/таб) предыдущей строки или вставляет стандартный.
        Иначе (курсор не в начале строки без выделения) – вставляет стандартный отступ.
        """
        if self.is_selecting:
            self.handle_block_indent()
            return

        # Если нет выделения
        if self.cursor_x > 0:
            # Если курсор не в начале строки, используем обычный таб в текущей позиции
            self.handle_tab() # handle_tab вызовет insert_text, который добавит действие
            return

        # Если курсор в начале строки (self.cursor_x == 0)
        indentation_to_copy = ""
        if self.cursor_y > 0:
            prev_line_idx = self.cursor_y - 1
            if 0 <= prev_line_idx < len(self.text):
                prev_line = self.text[prev_line_idx]
                m = re.match(r"^(\s*)", prev_line)
                if m:
                    indentation_to_copy = m.group(1)
            else:
                logging.warning(f"Smart tab: Invalid previous line index {prev_line_idx}")

        if not indentation_to_copy:
            tab_size = self.config.get("editor", {}).get("tab_size", 4)
            use_spaces = self.config.get("editor", {}).get("use_spaces", True)
            text_to_insert_val = " " * tab_size if use_spaces else "\t"
        else:
            text_to_insert_val = indentation_to_copy

        self.insert_text(text_to_insert_val) # insert_text управляет историей
        logging.debug(f"handle_smart_tab (no selection, line start): Inserted {text_to_insert_val!r}")


    def handle_char_input(self, key):
        """
        Handles all character‐input, separating control keys (Ctrl+…) from printable text.
        """
        try:
            #  Control keys (ASCII < 32)
            if isinstance(key, int) and key < 32:
                self.handle_control_key(key)
                return

            # Printable characters
            if isinstance(key, int):
                char = chr(key)
                self.insert_text(char)
            elif isinstance(key, str) and len(key) == 1:
                self.insert_text(key)
            else:
                logging.warning(f"handle_char_input received unexpected type: {type(key)}")

        except Exception as e:
            logging.exception(f"Error handling character input: {str(e)}")
            self._set_status_message(f"Input error: {str(e)[:80]}...")


    def handle_control_key(self, key_code):
        """
        Map control‐key codes to editor actions.
        """
        if key_code == 19:   # Ctrl+S
            self.save_file()
        elif key_code == 6:  # Ctrl+F
            self.find_prompt()
        elif key_code == 15: # Ctrl+O
            self.open_file()
        elif key_code == 3:  # Ctrl+Q
            self.exit_editor()
        else:
            logging.debug(f"Unhandled control key: {key_code}")


    def handle_enter(self):
        """Handles the Enter key, creating a new line at the cursor position."""
        # Если есть выделение, Enter должен удалить выделение и вставить новую строку
        with self._state_lock: # Блокировка для безопасного изменения текста и истории
            # Если есть выделение, Enter должен удалить выделение и вставить новую строку
            if self.is_selecting:
                # Удаление выделения добавит свое действие в историю и сбросит undone_actions
                self.delete_selected_text_internal(*self.selection_start, *self.selection_end)
                self.selection_start = self.selection_end = None
                self.is_selecting = False
                # Курсор установлен на начало удаленного диапазона в internal методе
            self.insert_text("\n")

        logging.debug("Handled Enter key")

    def parse_key(self, key_str: str) -> int:
        """
        Преобразует строку-описание горячей клавиши в curses-код.
        """
        if isinstance(key_str, int):
            return key_str

        key_str = key_str.strip().lower()
        if not key_str:
            raise ValueError("Пустая строка для горячей клавиши")

        # Сначала проверяем на точное совпадение в named, включая shift-комбинации
        # (они должны быть в нижнем регистре в named)
        named = {
            "del": getattr(curses, 'KEY_DC', 330),
            "delete": getattr(curses, 'KEY_DC', 330),
            "backspace": getattr(curses, 'KEY_BACKSPACE', 127), # Обычно 263 в keypad(True)
            "tab": ord("\t"), # curses.KEY_TAB (обычно 9)
            "enter": ord("\n"), # curses.KEY_ENTER (обычно 10 или 13)
            "space": ord(" "),
            "esc": 27, # curses.KEY_EXIT или 27
            "escape": 27,
            "up": getattr(curses, 'KEY_UP', 259),
            "down": getattr(curses, 'KEY_DOWN', 258),
            "left": getattr(curses, 'KEY_LEFT', 260),
            "right": getattr(curses, 'KEY_RIGHT', 261),
            "home": getattr(curses, 'KEY_HOME', 262),
            "end": getattr(curses, 'KEY_END', 360), # Может быть KEY_LL или специфичный для терминала
            "pageup": getattr(curses, 'KEY_PPAGE', 339),
            "pgup": getattr(curses, 'KEY_PPAGE', 339),
            "pagedown": getattr(curses, 'KEY_NPAGE', 338),
            "pgdn": getattr(curses, 'KEY_NPAGE', 338),
            "insert": getattr(curses, 'KEY_IC', 331),
            # Явные Shift-комбинации для спец. клавиш, которые curses может возвращать
            "sright": getattr(curses, 'KEY_SRIGHT', 402), # Shift+Right
            "sleft": getattr(curses, 'KEY_SLEFT', 393),   # Shift+Left
            "shome": getattr(curses, 'KEY_SHOME', 391),   # Shift+Home
            "send": getattr(curses, 'KEY_SEND', 386),    # Shift+End
            "spgup": getattr(curses, 'KEY_SPREVIOUS', 337), # Shift+PgUp (KEY_SR в некоторых системах)
            "spgdn": getattr(curses, 'KEY_SNEXT', 336),     # Shift+PgDn (KEY_SF в некоторых системах)
            # Добавим ваши:
            "shift+pgup": getattr(curses, 'KEY_SPREVIOUS', 337),
            "shift+pgdn": getattr(curses, 'KEY_SNEXT', 336),
        }
        named.update({f"f{i}": getattr(curses, f"KEY_F{i}", 256 + i) for i in range(1, 13)}) # KEY_F(i) обычно > 256

        if key_str in named:
            return named[key_str]

        parts = key_str.split('+')
        modifiers = []
        base_key_str = ""

        for part in parts:
            if part in ("ctrl", "alt", "shift"):
                modifiers.append(part)
            else:
                if base_key_str: # Уже нашли одну "базовую" клавишу
                    raise ValueError(f"Несколько базовых клавиш в хоткее: {key_str}")
                base_key_str = part
        
        if not base_key_str:
            raise ValueError(f"Не найдена базовая клавиша в хоткее: {key_str}")

        # Обработка базовой клавиши
        base_code: int
        if base_key_str in named: # Если "z" в "shift+z" это, например, "del"
            base_code = named[base_key_str]
        elif len(base_key_str) == 1:
            base_code = ord(base_key_str) # "z" -> ord('z')
        else: # Если базовая клавиша не одиночный символ и не в named (напр. "f1")
            raise ValueError(f"Неизвестная базовая клавиша: {base_key_str} в {key_str}")

        # Применение модификаторов
        # curses не имеет универсальных кодов для Alt+<буква> или Shift+<буква> (кроме заглавных).
        # Ctrl+<буква> обычно это ord(буква) & 0x1F или ord(буква) - ord('a') + 1.

        is_ctrl = "ctrl" in modifiers
        is_alt = "alt" in modifiers # Alt часто реализуется через Esc-префикс, get_wch() может вернуть строку
        is_shift = "shift" in modifiers

        if is_alt:
            # Alt часто не генерирует один int код, а меняет байтовую последовательность.
            # Если get_wch() возвращает строку для Alt-комбинаций, этот парсер не сможет
            # их перевести в int, если только нет спец. кодов от curses (редко).
            # Можно зарезервировать диапазон для Alt, как вы делали (base_key | 0x200)
            # но это будет работать, только если ваш input loop генерирует такие int'ы.
            # Для Alt+X (где X это буква), эмуляторы терминала часто шлют Esc + X.
            # parse_key здесь должен вернуть то, что ОЖИДАЕТ action_map.
            # Если action_map ожидает кастомные коды для Alt, то здесь их надо генерировать.
            # Например, если base_code это ord('x'), то alt+x -> ord('x') | 0x200.
            # Однако, если base_code это KEY_LEFT, то alt+left может быть другим.
            # Ваша логика `return base_key | 0x200` была для `alt+...`.
            # Но она стояла ПЕРЕД разделением на части.
            # Если key_str был "alt+a", то base_key парсился из "a".
            # Это можно оставить, но после обработки ctrl/shift.
            # Либо, ваш get_wch() должен возвращать такие коды.
            # Для простоты, если Alt, то это скорее всего не одиночный int от getch().
            # Если вы хотите мапить "alt+x" на что-то, это лучше делать через term_mappings или строки.
            logging.warning(f"Парсинг Alt-комбинаций ('{key_str}') может быть не универсальным и зависит от терминала/get_wch.")
            # Если вы определили кастомные коды для Alt, то применяйте их.
            # base_code |= 0x200 # Пример вашего предыдущего подхода

        if is_ctrl:
            if 'a' <= base_key_str <= 'z':
                char_code = ord(base_key_str)
                # Ctrl+буква (a-z) -> 1-26
                # Ctrl+Shift+буква (A-Z) -> кастомный диапазон, например 257-282
                # (ord(ch) - ord('a') + 1)
                ctrl_val = char_code - ord('a') + 1
                if is_shift:
                    # Пример: Ctrl+Shift+A = 257 (0x101)
                    # Предполагаем, что такие коды не конфликтуют с curses.KEY_*
                    # Это уже было в вашей логике:
                    base_code = ctrl_val | 0x100 # 256 + (1..26)
                else:
                    base_code = ctrl_val
            # elif base_key_str == '[': base_code = 27 # Ctrl+[ -> Esc
            # ... другие специальные Ctrl комбинации ...
            else:
                raise ValueError(f"Ctrl можно применять только к буквам a-z в {key_str} (или нужны явные маппинги)")
        
        elif is_shift: # Shift без Ctrl
            if 'a' <= base_key_str <= 'z':
                # shift+z -> ord('Z')
                base_code = ord(base_key_str.upper())
            # Для Shift + спец.клавиша (например, Shift+Tab) - они должны быть в `named` как "shift+tab"
            # или ваш get_wch() должен возвращать для них спец. код, который вы положите в `named`
            # или напрямую в `action_map`.
            # Эта ветка не должна ловить "shift+pgup", т.к. он уже в named.
            # Если мы здесь, значит это что-то типа "shift+1" или "shift+]"
            # Для них curses обычно не генерирует спец. коды, а возвращает сами символы ('!', '}')
            # Если base_key_str это '1', то ord(base_key_str.upper()) даст ошибку.
            # Значит, `shift+<не_буква>` должен быть явно определен в `named` или `term_mappings`.
            # Если мы дошли сюда с `shift+<не_буква>`, и его нет в `named`, это ошибка.
            elif not ('a' <= base_key_str <= 'z'):
                 # Если это "shift+f1", то "f1" уже должно было быть обработано `named`.
                 # Если это "shift+enter", то "enter" в `named`, но shift+enter - другой код.
                 # Это сложно сделать универсально. Лучше явные маппинги.
                raise ValueError(f"Shift с не-буквенной клавишей '{base_key_str}' должен быть явно определен в 'named_keys' (например, 'shift+tab')")

        # Обработка Alt в конце, если другие модификаторы уже применены
        if is_alt:
             base_code |= 0x200 # Ваш предыдущий подход для кастомных Alt кодов

        return base_code
    

    def get_char_width(self, char):
        """
        Calculates the display width of a character using wcwidth.
        Returns 1 for control characters or characters with ambiguous width (-1).
        Uses unicodedata to check if it's a control character.
        """
        if not isinstance(char, str) or len(char) != 1:
            return 1 # Неожиданный ввод, считаем ширину 1

        # Проверка на управляющие символы (кроме известных типа Tab, Enter)
        if unicodedata.category(char) in ('Cc', 'Cf'): # Cc: Control, Cf: Format
             # Здесь можно добавить исключения для символов, которые хотим отображать (например, '\t')
             if char == '\t':
                 # Ширина табуляции зависит от позиции курсора и tab_size,
                 # но wcwidth('\t') обычно 0 или 1.
                 # Для отрисовки Pygments токенов лучше вернуть wcwidth или 1.
                 # Реальная отрисовка табов происходит в DrawScreen.
                 width = wcwidth(char)
                 return width if width >= 0 else 1
             return 0 # Управляющие символы обычно не отображаются и имеют 0 ширину
        # Проверка на символы нулевой ширины (например, диакритика)
        if unicodedata.combining(char):
             return 0 # Объединяющиеся символы имеют нулевую ширину

        width = wcwidth(char)
        # wcwidth возвращает -1 для символов, для которых ширина неопределена,
        # или 0 для символов нулевой ширины (которые мы уже обработали).
        # Для -1 или 0 (если не объединяющийся), возвращаем 1, чтобы курсор двигался.
        # Если wcwidth вернул >=0, возвращаем его.
        return width if width >= 0 else 1


    def get_string_width(self, text):
        """
        Calculates the display width of a string using wcswidth.
        Handles potential errors by summing individual character widths.
        """
        if not isinstance(text, str):
             logging.warning(f"get_string_width received non-string input: {type(text)}")
             return 0

        try:
            width = wcswidth(text)
            if width >= 0:
                return width
        except Exception as e:
            logging.warning(f"wcswidth failed for '{text[:20]}...': {e}. Falling back to char sum.")
            pass # Fallback

        # Fallback: суммируем ширину каждого символа
        total_width = 0
        for char in text:
            total_width += self.get_char_width(char)
        return total_width
    

    def safe_open(self, filename: str, mode: str = "r", encoding: str | None = None, errors: str = "replace"):
        """
        Safely open a file in the given mode.
        • In binary mode, ignore encoding and errors.
        • In text mode, use the given encoding (or self.encoding) and the given errors policy.
        """
        try:
            if "b" in mode:
                # Binary mode: no encoding or errors
                return open(filename, mode)
            else:
                # Text mode: use specified or default encoding, with given errors policy
                return open(
                    filename,
                    mode,
                    encoding=encoding or self.encoding,
                    errors=errors,
                )
        except Exception as e:
            logging.error(f"Failed to safe_open file {filename!r} in mode {mode!r}: {e}")
            raise

#=============== Open file ============================
    def open_file(self, filename: str = None) -> None:
        """
        Opens a file, detects its encoding, and loads its contents.
        Shows an informative status message on error.
        Prompts to save unsaved changes before opening a new file.
        """
        logging.debug("open_file called")

        try:
            # 1. Prompt to save unsaved changes
            if self.modified:
                ans = self.prompt("Save changes? (y/n): ")
                if ans and ans.lower().startswith("y"):
                    self.save_file()

                if self.modified and not (ans and ans.lower().startswith("y")):
                    self._set_status_message("Open cancelled due to unsaved changes")
                    logging.debug("Open file cancelled due to unsaved changes")
                    return

            # 2. Get filename if not provided
            if not filename:
                filename = self.prompt("Enter file name to open: ")
            if not filename:
                self._set_status_message("Open cancelled")
                logging.debug("Open file cancelled: no filename provided")
                return

            # 3. Validate filename and permissions
            if not self.validate_filename(filename):
                self._set_status_message("Invalid filename or path")
                logging.warning(f"Open file failed: invalid filename {filename}")
                return
            if not os.path.exists(filename):
                self._set_status_message(f"File not found: {filename}")
                logging.warning(f"Open file failed: file not found {filename}")
                return
            if os.path.isdir(filename):
                self._set_status_message(f"Cannot open: {filename} is a directory")
                logging.warning(f"Open file failed: path is a directory {filename}")
                return
            if not os.access(filename, os.R_OK):
                self._set_status_message(f"No read permissions: {filename}")
                logging.warning(f"Open file failed: no read permissions {filename}")
                return

            # 4. Try to detect encoding and read file
            sample_size = 1024 * 10  # 10KB
            try:
                with self.safe_open(filename, mode="rb") as f:
                    raw_data = f.read(sample_size)
                detected = chardet.detect(raw_data)
                encoding = detected.get("encoding")
                confidence = detected.get("confidence", 0)
                logging.debug(f"Chardet detected encoding '{encoding}' with confidence {confidence} for {filename}")
            except Exception as e:
                self._set_status_message(f"Failed to read file sample: {e}")
                logging.exception(f"Failed to read file sample for encoding detection: {filename}")
                return

            # 5. Try to open file with detected or fallback encoding
            lines = None
            if not encoding or confidence < 0.5:
                logging.warning(
                    f"Low confidence ({confidence}) or unknown encoding detected for {filename}. Trying UTF-8 then Latin-1."
                )
                tried_encodings = ["utf-8", "latin-1"]
                for enc in tried_encodings:
                    try:
                        with self.safe_open(filename, mode="r", encoding=enc, errors="strict") as f:
                            lines = f.read().splitlines()
                            encoding = enc
                            logging.debug(f"Successfully read {filename} with encoding {enc}")
                            break
                    except (UnicodeDecodeError, OSError) as e:
                        logging.debug(f"Failed to read {filename} with encoding {enc}: {e}")
                if lines is None:
                    try:
                        with self.safe_open(filename, mode="r", encoding="utf-8", errors="replace") as f:
                            lines = f.read().splitlines()
                            encoding = "utf-8"
                            logging.warning(f"Read {filename} with utf-8 and errors='replace'")
                    except Exception as e_fallback:
                        self._set_status_message(f"Error reading {filename}: {e_fallback}")
                        logging.exception(f"Final fallback read failed for {filename}")
                        self.text = [""]
                        self.filename = None
                        self._lexer = TextLexer()
                        self.set_initial_cursor_position()
                        self.modified = False
                        return
            else:
                try:
                    with self.safe_open(filename, mode="r", encoding=encoding, errors="replace") as f:
                        lines = f.read().splitlines()
                    logging.debug(f"Successfully read {filename} with detected encoding {encoding}")
                except Exception as e_detected:
                    logging.warning(f"Failed to read {filename} with detected encoding {encoding}: {e_detected}. Trying utf-8 with errors='replace'.")
                    try:
                        with self.safe_open(filename, mode="r", encoding="utf-8", errors="replace") as f:
                            lines = f.read().splitlines()
                            encoding = "utf-8"
                            logging.debug(f"Successfully read {filename} with utf-8 errors='replace'")
                    except Exception as e_fallback2:
                        self._set_status_message(f"Error reading {filename}: {e_fallback2}")
                        logging.exception(f"Final fallback read failed for {filename}")
                        self.text = [""]
                        self.filename = None
                        self._lexer = TextLexer()
                        self.set_initial_cursor_position()
                        self.modified = False
                        return

            # 6. Update editor state after successful file opening
            self.text = lines if lines else [""]
            self.filename = filename
            self.modified = False
            self.encoding = encoding or "utf-8"
            self.set_initial_cursor_position()
            self.action_history.clear()
            self.undone_actions.clear()
            self._set_status_message(f"Opened {os.path.basename(filename)} (encoding: {self.encoding})")
            logging.debug(f"Opened file: {filename}, encoding: {self.encoding}, lines: {len(self.text)}")

            # Reset lexer, language, git info, and trigger async lint if needed
            self._lexer = None
            self.detect_language()
            self.update_git_info()
            if self._lexer and getattr(self._lexer, "name", "").lower() in ['python', 'python3']:
                self.run_lint_async(os.linesep.join(self.text))
            self.toggle_auto_save()

        except Exception as e:
            self._set_status_message(f"Error opening file: {e}")
            logging.exception(f"Unexpected error opening file: {filename}")
            self.text = [""]
            self.filename = None
            self._lexer = TextLexer()
            self.set_initial_cursor_position()
            self.modified = False


#================= SAVE_FILE ================================= 
    def save_file(self):
        """
        Сохраняет текущий документ.
        Если имя файла ещё не задано – предлагает «Save as:».
        """
        logging.debug("save_file called")

        if not self.filename or self.filename == "noname":
            logging.debug("Filename not set, invoking save_file_as")
            self.save_file_as()
            return

        if not self.validate_filename(self.filename):
            self._set_status_message("Invalid filename")
            logging.warning(f"Invalid filename: {self.filename}")
            return

        if os.path.isdir(self.filename):
            self._set_status_message(f"Cannot save: {self.filename} is a directory")
            logging.warning(f"Filename is a directory: {self.filename}")
            return

        if os.path.exists(self.filename) and not os.access(self.filename, os.W_OK):
            self._set_status_message(f"No write permissions: {self.filename}")
            logging.warning(f"No write permissions: {self.filename}")
            return

        try:
            self._write_file(self.filename)
            self.toggle_auto_save()
            self._set_status_message(f"Saved to {os.path.basename(self.filename)}")
        except Exception as e:
            self._set_status_message(f"Error saving file: {str(e)[:80]}...")


    def save_file_as(self):
        """
        Сохраняет текущий документ под новым именем.
        """
        logging.debug("save_file_as called")

        default_name = self.filename if self.filename and self.filename != "noname" \
            else self.config.get("editor", {}).get("default_new_filename", "new_file.txt")

        new_filename = self.prompt(f"Save file as ({default_name}): ")
        if not new_filename:
            self._set_status_message("Save as cancelled")
            logging.debug("User cancelled save as prompt or timeout")
            return

        new_filename = new_filename.strip() or default_name

        if not self.validate_filename(new_filename):
            self._set_status_message("Invalid filename or path")
            logging.warning(f"Invalid filename: {new_filename}")
            return

        if os.path.isdir(new_filename):
            self._set_status_message(f"Cannot save: {new_filename} is a directory")
            logging.warning(f"Filename is a directory: {new_filename}")
            return

        if os.path.exists(new_filename):
            choice = self.prompt("File already exists. Overwrite? (y/n): ")
            if not choice or choice.lower() != 'y':
                self._set_status_message("Save as cancelled (file exists)")
                logging.debug("User cancelled overwrite confirmation or timeout")
                return
            if not os.access(new_filename, os.W_OK):
                self._set_status_message(f"No write permissions: {new_filename}")
                logging.warning(f"No write permissions for existing file: {new_filename}")
                return

        target_dir = os.path.dirname(new_filename) or '.'
        if not os.path.exists(target_dir):
            try:
                os.makedirs(target_dir, exist_ok=True)
                logging.debug(f"Created missing directory: {target_dir}")
            except Exception as e:
                self._set_status_message(f"Cannot create directory: {target_dir}")
                logging.error(f"Cannot create directory {target_dir}: {e}")
                return

        if not os.access(target_dir, os.W_OK):
            self._set_status_message(f"No write permissions for directory: {target_dir}")
            logging.warning(f"No write permissions for directory: {target_dir}")
            return

        try:
            self._write_file(new_filename)
            self.toggle_auto_save()
            self._set_status_message(f"Saved as {os.path.basename(new_filename)}")
        except Exception as e:
            self._set_status_message(f"Error saving file: {str(e)[:80]}...")


    def _write_file(self, target: str):
        """
        Записывает текущий текст в указанный файл.
        """
        try:
            with self.safe_open(target, "w", encoding=self.encoding) as f:
                content = os.linesep.join(self.text)
                f.write(content)

            self.filename = target
            self.modified = False
            self.detect_language()

            logging.debug(f"File saved successfully: {target}")

            self.toggle_auto_save()

            if self._lexer and self._lexer.name in ["python", "python3"]:
                threading.Thread(target=self.run_lint_async, args=(content,), daemon=True).start()

            self.update_git_info()

        except Exception as e:
            logging.error(f"Failed to write file '{target}': {e}", exc_info=True)
            raise


    def revert_changes(self):
        """
        Reverts unsaved changes by reloading from the last saved version.
        """
        logging.debug("revert_changes called")

        if not self.filename or self.filename == "noname":
            self._set_status_message("Cannot revert: file has not been saved yet")
            logging.debug("Revert failed: file not saved")
            return

        if not os.path.exists(self.filename):
            self._set_status_message(f"Cannot revert: {os.path.basename(self.filename)} does not exist")
            logging.warning(f"File does not exist: {self.filename}")
            return

        confirmation = self.prompt("Revert unsaved changes? (y/n): ")
        if not confirmation or confirmation.lower() != "y":
            self._set_status_message("Revert cancelled")
            logging.debug("Revert cancelled by user or timeout")
            return

        original_modified = self.modified
        self.modified = False

        try:
            self.open_file(self.filename)
            if not self.modified:
                self._set_status_message(f"Reverted to {os.path.basename(self.filename)}")
                logging.debug(f"Reverted changes for {self.filename}")
        except Exception as e:
            self._set_status_message(f"Error reverting file: {str(e)[:80]}...")
            logging.exception(f"Unexpected error during revert: {self.filename}")
            self.modified = original_modified

# -------------- Auto-save ------------------------------
    def toggle_auto_save(self):
        """
        Включает или выключает автосохранение.
        Интервал (в минутах) задаётся через self._auto_save_interval (по умолчанию 5 минут).
        Сохраняет только если файл назван и есть изменения.
        """
        # Гарантируем поля
        if not hasattr(self, "_auto_save_enabled"):
            self._auto_save_enabled = False
        if not hasattr(self, "_auto_save_thread"):
            self._auto_save_thread = None

        # Получаем интервал из конфига или ставим дефолт
        try:
            interval = float(self.config.get("settings", {}).get("auto_save_interval", 5))
            if interval <= 0:
                interval = 5
        except Exception:
            interval = 5

        self._auto_save_interval = interval  # всегда актуальный интервал (минуты)
        self._auto_save_enabled = not self._auto_save_enabled

        if self._auto_save_enabled:
            # Запускаем поток автосэйва, если он не работает
            if self._auto_save_thread is None or not self._auto_save_thread.is_alive():
                def auto_save_task():
                    logging.info("Auto-save thread started")
                    last_saved_text = None
                    while self._auto_save_enabled:
                        try:
                            # Ждём интервал (минуты)
                            sleep_sec = max(1, int(self._auto_save_interval * 60))
                            for _ in range(sleep_sec):
                                if not self._auto_save_enabled:
                                    break
                                time.sleep(1)
                            if not self._auto_save_enabled:
                                break

                            # Не сохраняем, если файл не выбран
                            if not self.filename:
                                continue

                            current_text = os.linesep.join(self.text)
                            if self.modified and current_text != last_saved_text:
                                try:
                                    with open(self.filename, "w", encoding=self.encoding, errors="replace") as f:
                                        f.write(current_text)
                                    last_saved_text = current_text
                                    self.modified = False
                                    self._set_status_message(f"Auto-Saved to {self.filename}")
                                    logging.info(f"Auto-Saved to {self.filename}")
                                except Exception as e:
                                    self._set_status_message(f"Auto-save error: {e}")
                                    logging.exception("Auto-save failed")
                        except Exception as e:
                            logging.exception(f"Error in auto-save thread: {e}")
                            self._auto_save_enabled = False
                            self._set_status_message("Auto-save disabled due to error")
                    logging.info("Auto-save thread finished")

                self._auto_save_thread = threading.Thread(target=auto_save_task, daemon=True)
                self._auto_save_thread.start()
            self.status_message = f"Auto-save enabled (every {self._auto_save_interval} min)"
            self._set_status_message(self.status_message)
            logging.info(self.status_message)
        else:
            self.status_message = "Auto-save disabled"
            self._set_status_message(self.status_message)
            logging.info(self.status_message)


    def new_file(self) -> None:
        """
        Create a brand-new, empty buffer.

        ▸ If there are unsaved changes (self.modified=True), prompt:
        • 'y' → save current file, then create new
        • any other → cancel new_file
        ▸ Reset filename, lexer, git info, encoding.
        ▸ Clear undo/redo histories, disable autosave.
        ▸ Reset cursor/scrolling/search/selection.
        ▸ Clamp scroll and force a full redraw.
        """

        logging.debug("new_file called")
        try:
            # Если есть несохранённые изменения, спрашиваем и, при согласии, сохраняем
            if self.modified:
                ans = self.prompt("Save changes before creating new file? (y/n): ")
                if ans and ans.lower().startswith("y"):
                    self.save_file()
                else:
                    self._set_status_message("New file cancelled")
                    logging.debug("New file cancelled by user")
                    return  # отмена создания нового файла

            # Инициализируем «пустой» документ
            self.text = [""]
            self.filename = None
            self._lexer = None
            self._last_git_filename = None
            self.git_info = ("", "", "0")
            self.modified = False
            self.encoding = "UTF-8"

            # Сброс состояния курсора, истории действий, выделения и поиска
            self.set_initial_cursor_position()
            self.action_history.clear()
            self.undone_actions.clear()

            # Отключаем автосохранение для нового файла
            self._auto_save_enabled = False
            logging.debug("Auto-save disabled for new file")

            # Корректируем прокрутку и перерисовываем экран
            self._clamp_scroll()
            self._ensure_cursor_in_bounds()
            self.draw_screen()

            # Сообщаем об успехе
            self._set_status_message("New file created")
            logging.debug("New file created and screen redrawn")

        except Exception as e:
            logging.exception("Error in new_file", exc_info=True)
            self._set_status_message("Error creating new file")


    def cancel_operation(self):
        """
        Обработчик «Esc-отмены», вызывается из handle_input()
        и через action_map/горячую клавишу.

        • если есть выделение ‒ снимает его;
        • если есть активная подсветка поиска ‒ снимает ее;
        • иначе сбрасывает строку статуса на стандартную (пустую или информационную).
        """
        if self.lint_panel_active:
            self.lint_panel_active = False
            self.lint_panel_message = ""
            return
        
        if self.is_selecting:
            self.is_selecting = False
            self.selection_start = self.selection_end = None
            self._set_status_message("Selection cancelled")
            logging.debug("Cancelled: selection")
        elif self.highlighted_matches: # Если есть активная подсветка поиска
            self.highlighted_matches = [] # Очищаем ее
            # Не сбрасываем search_matches и search_term сразу,
            # чтобы F3 (find_next) могла работать после Ctrl+F без перерисовки.
            # Сбросим их при следующем поиске (find_prompt) или при открытии/создании файла.
            # self.search_matches = []
            # self.search_term = ""
            self.current_match_idx = -1 # Сбрасываем текущее совпадение
            self._set_status_message("Search highlighting cancelled")
            logging.debug("Cancelled: search highlighting")
        else:
            # Если не было ни выделения, ни подсветки, просто сообщение
            self._set_status_message("Operation cancelled")
            logging.debug("Cancelled: generic operation")
        # Перерисовка произойдет в главном цикле, отображая изменения


    def handle_escape(self):
            """
            Универсальная обработка Esc.

            • Если есть активное выделение ‒ убираем его.
            • Если есть активная подсветка поиска ‒ убираем ее.
            • Если открыт prompt (нажатие Esc в prompt уже вернуло пустую строку) -
              об этом позаботится prompt, и затем cancel_operation сбросит статус.
            • Если нет активных "состояний отмены" (выделение/поиск) и двойной Esc
              (быстрее 1.5 с) - предлагаем сохранить и выйти.
            • Иначе (одиночный Esc без активных состояний) ‒ просто ставим статус «Cancelled».
            """
            now = time.monotonic()
            last = getattr(self, "_last_esc_time", 0) # Получаем время предыдущего Esc, дефолт 0

            # 1) Есть активные состояния, которые можно отменить?
            if self.is_selecting or self.highlighted_matches:
                 # Делегируем обработку cancel_operation
                 self.cancel_operation()
                 # Обновляем время последнего Esc, чтобы двойной Esc работал,
                 # даже если между нажатиями была отмена выделения/поиска
                 self._last_esc_time = now
                 return # Обработка завершена

            # 2) Нет активных состояний отмены. Проверяем двойной Esc.
            if now - last < 1.5:
                logging.debug("Double Esc detected, attempting to exit")
                # Двойной Esc (быстрее 1.5 c) -> попытка выхода
                self.exit_editor() # exit_editor сам спросит о сохранении и завершит работу
                # Если exit_editor не завершит работу (например, пользователь отменит сохранение),
                # execution вернется сюда, и мы просто выйдем из handle_escape.

            # 3) Одиночный Esc без активных состояний -> просто "Cancelled"
            else:
                logging.debug("Single Esc detected, setting status to Cancelled")
                self._set_status_message("Cancelled")

            # Запоминаем время текущего Esc для следующей проверки
            self._last_esc_time = now


    def exit_editor(self):
        """
        Exits the editor with a prompt to save any unsaved changes.
        Handles closing curses gracefully.
        """
        logging.debug("Attempting to exit editor.")
        # ── 1. Проверяем несохраненные изменения ─────────────────────
        if self.modified:
            ans = self.prompt("Save changes before exiting? (y/n): ")
            if ans and ans.lower().startswith("y"):
                self.save_file()
                # Если save_file не удался (например, нет прав или отменен Save As),
                # modified останется True.
                # Если пользователь ввел 'y', но сохранение не прошло, мы все равно пытаемся выйти.
            elif ans and ans.lower().startswith("n"):
                 logging.debug("Exit: User chose NOT to save.")
                 pass # Продолжаем выход без сохранения
            else:
                 # Пользователь отменил запрос на сохранение (нажал Esc или Enter без ввода)
                 self._set_status_message("Exit cancelled")
                 logging.debug("Exit cancelled by user prompt.")
                 return # Отменяем выход

        # ── 2. Останавливаем фоновые потоки (автосохранение) ──────────
        self._auto_save_enabled = False  # Сигнализируем потоку остановиться
        logging.debug("Set auto_save_enabled to False")
        # Можно добавить другие флаги для остановки других потоков, если они есть.
        # Не будем явно дожидаться завершения потоков здесь, чтобы не блокировать UI надолго.
        # Daemon=True потоки завершатся при выходе программы.

        # ── 3. Корректное завершение curses ──────────────────────────
        # Убедимся, что мы в главном потоке перед вызовом curses.endwin()
        # Это предотвращает ошибки Curses при вызове из фонового потока.
        if threading.current_thread() is threading.main_thread():
            logging.debug("Running in main thread, calling curses.endwin()")
            try:
                curses.endwin()
                logging.debug("curses.endwin() called.")
            except curses.error as e:
                logging.error(f"Error calling curses.endwin(): {e}")
            except Exception as e:
                logging.error(f"Unexpected error during curses.endwin(): {e}")
        else:
            logging.warning("curses.endwin() called from non-main thread. Skipping.")
            # В идеале такого быть не должно, но на всякий случай.

        # ── 4. Завершение программы ──────────────────────────────
        sys.exit(0)


    def prompt(self, message: str, max_len: int = 1024, timeout: int = 60) -> Optional[str]:
        """
        Однострочный ввод в статус-баре с таймаутом ожидания.

        ▸ Enter    — подтвердить, вернуть введённую строку (strip)
        ▸ Esc      — отмена, вернуть None
        ▸ Tab      — вставить отступ (4 пробела)
        ▸ Backspace/Delete, ←/→, Home/End — стандартное поведение
        ▸ Resize   — перерисовка под новый размер экрана
        """
        logging.debug(f"Prompt вызван с сообщением: '{message}', максимальная длина: {max_len}, таймаут: {timeout} сек")
        locale.setlocale(locale.LC_CTYPE, '')  # корректная ширина Юникода

        orig_curs = curses.curs_set(1)
        curses.noecho()
        self.stdscr.nodelay(False)
        self.stdscr.timeout(timeout * 1000)  # Устанавливаем таймаут в миллисекундах

        buf: list[str] = []
        pos = 0
        tab_width = 4

        try:
            while True:
                h, w = self.stdscr.getmaxyx()
                row = h - 1
                max_msg = max(0, w - 10)
                disp_msg = message[:max_msg]
                msg_len = len(disp_msg)

                self.stdscr.move(row, 0)
                self.stdscr.clrtoeol()
                self.stdscr.addstr(row, 0, disp_msg, self.colors.get("status", 0))

                text = "".join(buf)
                avail = w - msg_len - 1

                if self.get_string_width(text) > avail:
                    curr = 0; tail = []
                    for ch in reversed(buf):
                        cw = self.get_char_width(ch)
                        if curr + cw > avail:
                            break
                        curr += cw; tail.insert(0, ch)
                    display = "".join(tail)
                    disp_pos = self.get_string_width("".join(buf)[len(buf)-len(tail):pos])
                else:
                    display = text
                    disp_pos = self.get_string_width(text[:pos])

                self.stdscr.addstr(row, msg_len, display)
                self.stdscr.move(row, msg_len + disp_pos)
                self.stdscr.refresh()

                try:
                    key = self.stdscr.get_wch()
                    logging.debug(f"Prompt: get_wch() returned: {repr(key)} (type: {type(key)})")
                except curses.error as e:
                    logging.error(f"Ошибка ввода (get_wch()) в prompt: {e}") # Было "Ошибка ввода (get_wch()): {e}"
                    if 'no input' in str(e).lower() or (isinstance(key, int) and key == curses.ERR): # Добавил isinstance для key
                        logging.warning(f"Prompt: get_wch() timed out or returned ERR. Message: '{message}'")
                        return None 
                    return None

                if key == 27 or key == '\x1b': # Проверяем и int 27, и строку '\x1b'
                    logging.debug(f"Prompt: Esc detected (key={repr(key)}). Cancelling.")
                    return None
                
                if key in ("\n", "\r", curses.KEY_ENTER, 10, 13): # Добавил 10 и 13 для надежности
                    logging.debug(f"Prompt: Enter detected (key={repr(key)}). Returning buffer.")
                    return "".join(buf).strip()
                
                if key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
                    if pos > 0:
                        pos -= 1; buf.pop(pos)
                elif key == curses.KEY_DC:
                    if pos < len(buf): buf.pop(pos)
                elif key == curses.KEY_LEFT:
                    pos = max(0, pos - 1)
                elif key == curses.KEY_RIGHT:
                    pos = min(len(buf), pos + 1)
                elif key == curses.KEY_HOME:
                    pos = 0
                elif key == curses.KEY_END:
                    pos = len(buf)
                elif key == curses.KEY_RESIZE:
                    continue
                elif key == "\t":
                    for _ in range(tab_width):
                        if len(buf) < max_len:
                            buf.insert(pos, " "); pos += 1
                elif isinstance(key, str) and key.isprintable():
                    if len(buf) < max_len:
                        buf.insert(pos, key); pos += 1
                else:
                    logging.debug(f"Ignored key: {key!r}")

        finally:
            self.stdscr.nodelay(True)
            self.stdscr.timeout(-1)  # Убираем таймаут
            curses.noecho()
            curses.curs_set(orig_curs)
            h, _ = self.stdscr.getmaxyx()
            self.stdscr.move(h - 1, 0)
            self.stdscr.clrtoeol()
            self.stdscr.refresh()
            curses.flushinp()


    # === ПОИСК ======== Search/Replace and Find ========================
    def search_and_replace(self):
        """
        Searches and replaces text throughout the document using regex.
        Prompts for a search pattern and replacement text, performs the replacement,
        and reports the number of occurrences replaced.
        Does NOT add to undo/redo history (history is cleared).
        """
        # Очищаем предыдущую подсветку поиска и результаты поиска
        self.highlighted_matches = []
        self.search_matches = []
        self.search_term = ""
        self.current_match_idx = -1

        # Сохраняем текущий статус, чтобы восстановить его, если промпт будет отменен
        original_status = self.status_message
        self._set_status_message("Search for (regex): ")

        # Запрашиваем шаблон поиска
        search_pattern = self.prompt("Search for (regex): ")

        # Если пользователь отменил ввод шаблона (нажал Esc или Enter без ввода)
        if not search_pattern:
            self._set_status_message(original_status if original_status else "Search/Replace cancelled")
            logging.debug("Search/Replace cancelled: no search pattern provided.")
            # Восстанавливаем статус и выходим
            return

        # Запрашиваем строку замены
        replace_with = self.prompt("Replace with: ")
        compiled_pattern = None
        try:
            compiled_pattern = re.compile(search_pattern, re.IGNORECASE)
            logging.debug(f"Compiled regex pattern: {search_pattern}")
        except re.error as e:
            # Ошибка в регулярном выражении
            error_msg = f"Regex error: {str(e)[:80]}..."
            self._set_status_message(error_msg)
            logging.warning(f"Search/Replace failed due to regex error: {e}")
            return # Выходим, если шаблон некорректен
        # --- Выполнение замены ---
        new_text = []
        replacements = 0
        error_during_replace = False

        try:
            with self._state_lock:
                 # Создаем копию списка строк. 
                 text_snapshot = list(self.text)
            logging.debug(f"Starting replacement process on {len(text_snapshot)} lines.")

            for idx, line in enumerate(text_snapshot):
                try:
                    # Выполняем замену в текущей строке
                    new_line, count = compiled_pattern.subn(replace_with, line)
                    new_text.append(new_line) # Добавляем измененную (или оригинальную) строку
                    replacements += count
                except Exception as e_line:
                    logging.error(f"Error replacing in line {idx+1}: {e_line}")
                    new_text.append(line) # Добавляем оригинальную строку в случае ошибки в этой строке
                    error_during_replace = True

            logging.debug(f"Finished replacement process. Found {replacements} replacements.")

            # --- Обновление состояния редактора после замены ---
            with self._state_lock:
                # Заменяем старый список строк новым
                self.text = new_text
                self.modified = True # Файл изменен после замены
                self.action_history.clear()
                self.undone_actions.clear()
                logging.debug("Cleared undo/redo history after search/replace.")

                # Сбрасываем подсветку поиска, т.к. текст изменился, старые совпадения неактуальны
                self.highlighted_matches = []
                self.search_matches = [] # Также сбрасываем найденные совпадения для F3
                self.search_term = "" # Сбрасываем термин поиска

            # Отображаем результат операции в статус-баре
            if error_during_replace:
                 # Если были ошибки в отдельных строках, сообщаем об этом
                 self._set_status_message(f"Replaced {replacements} occurrences, but errors occurred in some lines.")
                 logging.warning("Search/Replace completed with errors in some lines.")
            elif replacements > 0:
                # Успешная замена, найдены совпадения
                self._set_status_message(f"Replaced {replacements} occurrence(s)")
                logging.info(f"Search/Replace successful: {replacements} replacements.")
            else:
                # Совпадения не найдены
                self._set_status_message("No occurrences found")
                logging.info("Search/Replace: No occurrences found.")

        except Exception as e:
            # Любая другая непредвиденная ошибка во время всего процесса Search/Replace
            error_msg = f"Unexpected error during Search/Replace: {str(e)[:80]}..."
            self._set_status_message(error_msg)
            logging.exception(f"Unexpected error during search and replace for pattern '{search_pattern}'")


    def _collect_matches(self, term):
        """
        Находит все вхождения term (без учета регистра) в self.text.
        Возвращает список кортежей: [(row_idx, col_start_idx, col_end_idx), ...].
        Использует блокировку состояния для безопасного доступа к тексту.
        """
        matches = []
        if not term:
            return matches

        # Поиск без учета регистра
        low = term.lower()
        term_len = len(term)

        # Используем блокировку только для доступа к self.text
        with self._state_lock:
            # Делаем копию ссылок на строки, чтобы блокировка не держалась долго
            text_snapshot = list(self.text) # Копируем список строк
        # Выполняем поиск на снапшоте без блокировки
        for row_idx, line in enumerate(text_snapshot):
            start_col_idx = 0
            line_lower = line.lower() # Сравниваем с нижней версией строки
            while True:
                # Ищем в line_lower, но индексы берем из оригинальной line (т.е. снапшота)
                found_idx = line_lower.find(low, start_col_idx)
                if found_idx == -1:
                    break
                match_end_idx = found_idx + term_len
                matches.append((row_idx, found_idx, match_end_idx))
                # Следующий поиск начинаем после текущего совпадения
                # Иначе можем зациклиться на пустых совпадениях или совпадениях длиной 1
                start_col_idx = match_end_idx if term_len > 0 else found_idx + 1 # Избегаем бесконечного цикла для пустых term

        logging.debug(f"Found {len(matches)} matches for search term '{term}'")
        return matches


    def find_prompt(self):
        """
        Запрашивает строку поиска, находит все совпадения,
        сохраняет их для подсветки и переходит к первому.
        """
        # Очищаем предыдущие результаты подсветки при начале нового поиска
        self.highlighted_matches = []
        self.current_match_idx = -1
        self.search_matches = []
        self.search_term = ""

        term = self.prompt("Find: ")
        if term == "":
            self._set_status_message("Search cancelled")
            # Подсветка уже очищена выше
            return

        self.search_term = term
        # Находим все совпадения
        self.search_matches = self._collect_matches(term)
        # Сохраняем их же для подсветки (highlighted_matches используется draw)
        self.highlighted_matches = self.search_matches

        if not self.search_matches:
            self._set_status_message(f"'{term}' not found")
            self.current_match_idx = -1
        else:
            # Переходим к первому совпадению
            self.current_match_idx = 0
            self._goto_match(self.current_match_idx) # Перемещаем курсор и прокрутку
            self._set_status_message(f"Found {len(self.search_matches)} match(es). Press F3 for next.")


    def find_next(self):
        """
        Переходит к следующему совпадению (по циклу).
        Использует ранее найденные search_matches.
        Не меняет список highlighted_matches.
        """
        if not self.search_matches:
            # Если пользователь нажал F3 до первого поиска или поиск не дал результатов
            # Проверяем search_term, т.к. search_matches мог быть очищен отменой поиска (Esc)
            if not self.search_term:
                 self._set_status_message("No search term. Use Ctrl+F first.")
            else: # search_term есть, но совпадений нет
                 self._set_status_message(f"No matches found for '{self.search_term}'.")
            # Очищаем подсветку на всякий случай, если вдруг осталась
            self.highlighted_matches = []
            self.current_match_idx = -1
            return

        # Переходим к следующему индексу по кругу
        self.current_match_idx = (self.current_match_idx + 1) % len(self.search_matches)
        self._goto_match(self.current_match_idx) # Перемещаем курсор и прокрутку
        self._set_status_message(
            f"Match {self.current_match_idx + 1}/{len(self.search_matches)}"
        )


    def _goto_match(self, idx):
        """Переносит курсор/прокрутку к совпадению № idx и показывает его."""
        if not self.search_matches or not (0 <= idx < len(self.search_matches)):
             logging.warning(f"_goto_match called with invalid index {idx} for {len(self.search_matches)} matches")
             return # Некорректный индекс

        row, col_start, col_end = self.search_matches[idx]

        # Перемещаем курсор в начало совпадения
        self.cursor_y, self.cursor_x = row, col_start

        # Adjust vertical scroll (scroll_top)
        height, width = self.stdscr.getmaxyx()
        text_area_height = max(1, height - 2) # Высота области текста

        if self.cursor_y < self.scroll_top:
            # Курсор выше видимой области, сдвигаем вид вверх
            self.scroll_top = max(0, self.cursor_y - text_area_height // 2) # Центрируем или сдвигаем вверх
        elif self.cursor_y >= self.scroll_top + text_area_height:
            # Курсор ниже видимой области, сдвигаем вид вниз
             self.scroll_top = min(
                 max(0, len(self.text) - text_area_height), # Не опускаемся ниже последней видимой строки
                 self.cursor_y - text_area_height // 2 + 1 # Центрируем или сдвигаем вниз
            )
        # Ensure scroll_top is not negative
        self.scroll_top = max(0, self.scroll_top)


    def validate_filename(self, filename):
        """
        Validates the filename for length, correctness, and path.
        Проверяет, является ли имя файла безопасным и находится ли оно в пределах текущей директории или поддиректорий.
        """
        if not filename or len(filename) > 255: # Максимальная длина имени файла в большинстве ФС
            self._set_status_message("Filename too long or empty")
            logging.warning(f"Validation failed: filename empty or too long ({len(filename)})")
            return False

        # Удаляем начальные и конечные пробелы
        filename = filename.strip()
        if not filename:
             self._set_status_message("Filename cannot be just whitespace")
             logging.warning("Validation failed: filename is whitespace")
             return False

        # Проверка на недопустимые символы в имени файла (основные)
        # Это базовая проверка, полный набор зависит от ФС и ОС.
        invalid_chars = r'[<>:"/\\|?*\x00-\x1F]' # Символы, обычно недопустимые в именах файлов
        if re.search(invalid_chars, filename):
            self._set_status_message(f"Filename contains invalid characters")
            logging.warning(f"Validation failed: filename contains invalid characters: {filename}")
            return False

        # Проверка на специальные имена (например, COM1, LPT1 на Windows) - простая версия
        # Более полная проверка нужна для кросс-платформенности, но это базовый редактор
        if os.name == 'nt': # Для Windows
             windows_reserved_names = ["CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"]
             name_part = os.path.splitext(os.path.basename(filename))[0].upper()
             if name_part in windows_reserved_names:
                 self._set_status_message(f"Filename '{name_part}' is reserved system name")
                 logging.warning(f"Validation failed: filename is reserved system name: {filename}")
                 return False
        try:
            # Получаем абсолютный путь к файлу после разрешения символических ссылок и ..
            # Используем os.path.normpath для нормализации пути (удаление ., .., слэшей и т.п.)
            absolute_path = os.path.normpath(os.path.abspath(filename))
            # Получаем канонический путь к текущей рабочей директории
            current_dir = os.path.normpath(os.path.abspath(os.getcwd()))

            # Проверяем, начинается ли абсолютный путь файла с пути текущей директории
            # Это гарантирует, что файл находится внутри текущей директории или поддиректории
            # Также обрабатываем случай, когда файл находится прямо в текущей директории
            if absolute_path == current_dir: # Если это текущая директория, а не файл
                 return False # Нельзя сохранить в директорию с именем директории
            # Добавляем os.sep к current_dir, чтобы не спутать "myproject" и "myproject-new"
            if not absolute_path.startswith(current_dir + os.sep) and absolute_path != os.path.join(current_dir, os.path.basename(absolute_path)):
                 self._set_status_message(f"Path outside current directory: {filename}")
                 logging.warning(f"Validation failed: path outside current directory: {filename} (resolved to {absolute_path})")
                 return False

            # Убедимся, что путь не содержит ".." для выхода за пределы
            # os.path.normpath уже должен это обработать, но для дополнительной проверки
            if ".." in absolute_path.split(os.sep):
                 self._set_status_message(f"Path contains '..'")
                 logging.warning(f"Validation failed: path contains '..': {filename} (resolved to {absolute_path})")
                 return False

            logging.debug(f"Filename '{filename}' validated successfully (resolved to {absolute_path})")
            return True

        except Exception as e:
            # Ошибка при обработке пути (например, слишком длинный путь на некоторых ОС)
            self._set_status_message(f"Error validating path: {str(e)[:80]}...")
            logging.error(f"Error validating filename '{filename}': {e}", exc_info=True)
            return False # При любой ошибке валидация считается неуспешной


    # =============выполнения команд оболочки Shell commands =================================
    def _execute_shell_command_async(self, cmd_list):
        """
        Выполняет команду оболочки в отдельном потоке и отправляет результат
        в очередь self._shell_cmd_q (в потокобезопасной манере).
        """
        output = ""
        error = ""
        message = ""
        returncode = -1 # Default return code

        try:
            logging.debug(f"Executing shell command in thread: {' '.join(shlex.quote(c) for c in cmd_list)}")
            # Указываем current working directory
            cwd = os.path.dirname(self.filename) if self.filename and os.path.exists(self.filename) else os.getcwd()
            logging.debug(f"Shell command cwd: {cwd}")

            # Используем subprocess.Popen для контроля над потоками вывода
            process = subprocess.Popen(
                cmd_list,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True, # Декодируем вывод как текст
                encoding="utf-8", # Явно указываем кодировку
                errors="replace", # Заменяем некорректные символы
                cwd=cwd # Выполняем команду в директории файла или текущей
            )

            # Читаем вывод и ошибки в реальном времени или ждем завершения
            # Blocking communicate() ждет завершения
            output, error = process.communicate(timeout=30) # Таймаут 30 секунд
            returncode = process.returncode
            logging.debug(f"Shell command finished with code {returncode}. Output len: {len(output)}. Error len: {len(error)}")


        except FileNotFoundError:
            message = f"Executable not found: {cmd_list[0]}"
            logging.error(message)
        except subprocess.TimeoutExpired:
            message = "Command timed out (30s). Terminating."
            logging.warning(message)
            try:
                process.terminate() # Попытка мягкого завершения
                
            except:
                try:
                    process.kill() # Если не завершается, убиваем
                except Exception as kill_e:
                    logging.error(f"Failed to kill timed out process: {kill_e}")
            # Communicate again to get any remaining output/error after termination
            try:
                 output, error = process.communicate(timeout=5) # Ждем еще немного, чтобы собрать вывод
            except:
                 output = "(Output after termination)"
                 error = "(Error after termination)"

            returncode = process.returncode # Получаем код завершения после terminate/kill

        except Exception as e:
            logging.exception(f"Error executing shell command {' '.join(shlex.quote(c) for c in cmd_list)}")
            message = f"Exec error: {str(e)[:80]}..."
        finally:
            # Формируем сообщение только на основе output и error, если нет системной ошибки
            if not message: # Если не было FileNotFoundError, Timeout, General Exception
                if returncode != 0:
                     # Команда завершилась с ошибкой (ненулевой код)
                     message = f"Cmd failed ({returncode}): {error.strip()[:100]}" if error.strip() else f"Cmd failed ({returncode}): (no stderr)"
                elif output.strip():
                    # Команда успешна, есть вывод в stdout
                    message = f"Cmd successful: {output.strip().splitlines()[0][:100]}" # Показываем первую строку вывода
                    if len(output.strip().splitlines()) > 1:
                         message += "..."
                else:
                    # Команда успешна, нет вывода
                    message = "Command executed (no output)"

            # Отправляем результат в очередь — безопасно!
            # Блокировка нужна только для доступа к очереди, но не для всего формирования сообщения
            try:
                self._shell_cmd_q.put(message)
                logging.debug(f"Shell command result queued: {message}")
            except Exception as q_e:
                 logging.error(f"Failed to put shell command result into queue: {q_e}")


    def execute_shell_command(self):
        """
        Запрашивает у пользователя команду, запускает её выполнение в отдельном потоке
        и ожидает результат через очередь для обновления статуса.
        """
        # Сохраняем текущий статус, чтобы восстановить его после промпта
        original_status = self.status_message
        self._set_status_message("Enter command: ") # Статус во время ввода команды

        command = self.prompt("Enter command: ")

        # После промпта, status_message мог быть изменен в prompt.finally или процессе обработки очереди.
        # Мы хотим показать результат команды, когда он придет в очередь.
        # Временный статус "Running command..." будет установлен после shlex.split.

        if not command:
            self._set_status_message(original_status if original_status else "Command cancelled")
            logging.debug("Shell command cancelled by user.")
            return

        # Разбиваем строку на аргументы (учитывает кавычки, экранирование)
        try:
            cmd_list = shlex.split(command)
            if not cmd_list:  # Пустая команда после split
                self._set_status_message("Empty command")
                logging.warning("Shell command failed: empty command after split")
                return
        except ValueError as e:
            self._set_status_message(f"Parse error: {e}")
            logging.error(f"Shell command parse error: {e}")
            return

        # --- Потокобезопасное обновление статуса перед запуском ---
        # Отправляем сообщение в очередь, чтобы оно было обработано основным потоком
        # до того, как придет результат команды.
        display_cmd = ' '.join(shlex.quote(c) for c in cmd_list)
        if len(display_cmd) > 50: display_cmd = display_cmd[:47] + "..."
        self._set_status_message(f"Running command: {display_cmd}")

        # Запускаем выполнение команды в отдельном потоке
        # Убеждаемся, что имя потока уникально или осмысленно для логгирования
        thread_name = f"ShellExecThread-{int(time.time())}"
        threading.Thread(target=self._execute_shell_command_async,
                        args=(cmd_list,),
                        daemon=True, # Поток завершится при выходе программы
                        name=thread_name).start()

        logging.debug(f"Started shell command thread: {thread_name}")
        # Результат будет получен позже в главном цикле через _shell_cmd_q


# === GIT ==================================================================
    def _run_git_command_async(self, cmd_list, command_name):
        """
        Выполняет команду Git в отдельном потоке и отправляет результат в очередь.
        cmd_list - список аргументов для subprocess.run.
        command_name - имя команды для отображения в статусе.
        """
        message = ""
        try:
            # Определяем рабочую директорию для Git команды
            # Используем директорию файла или текущую, если файла нет
            repo_dir = os.path.dirname(self.filename) if self.filename and os.path.exists(self.filename) else os.getcwd()
            logging.debug(f"Running git command '{command_name}' in directory: {repo_dir}")

            # Используем safe_run для выполнения команды Git
            result = safe_run(cmd_list, cwd=repo_dir) # safe_run теперь принимает cwd

            if result.returncode == 0:
                message = f"Git {command_name} successful."
                # Для некоторых команд (commit, pull, push) нужно обновить Git информацию
                if command_name in ["pull", "commit", "push"]:
                     # Отправляем специальное сообщение для обновления Git инфо
                    self._git_cmd_q.put("update_git_info")
                    logging.debug("Queued 'update_git_info' after successful Git command.")

                # Добавляем stdout в сообщение, если он не пустой (для status, diff)
                if result.stdout.strip():
                    lines = result.stdout.strip().splitlines()
                    # Ограничиваем длину сообщения для статус-бара
                    if len(lines) > 0:
                         message += f" {lines[0][:100]}..." if len(lines[0]) > 100 or len(lines) > 1 else lines[0][:100]
                    # Полный вывод для status или diff можно было бы показать в отдельном окне/буфере,
                    # но для статус-бара достаточно краткой информации.

            else:
                # Команда завершилась с ошибкой
                stderr = result.stderr.strip()
                message = f"Git error ({result.returncode}): {stderr[:100]}..." if stderr else f"Git error ({result.returncode}): (no stderr)"
                logging.error(f"Git command '{command_name}' failed. Code: {result.returncode}. Stderr: {stderr}")

        except FileNotFoundError:
            message = "Git executable not found."
            logging.error(message)
        except Exception as e:
            logging.exception(f"Git command async error for '{command_name}'")
            message = f"Git error: {str(e)[:80]}..."

        # Отправляем финальное сообщение о результате команды через очередь
        try:
             self._git_cmd_q.put(message)
             logging.debug(f"Git command result queued: {message}")
        except Exception as q_e:
             logging.error(f"Failed to put Git command result into queue: {q_e}")


    def integrate_git(self):
        """Меню Git вызывается клавишей F9. Запрашивает команду и запускает ее асинхронно."""
        logging.debug("integrate_git called")
        try:
        
            # Проверяем, включена ли интеграция с Git в конфиге
            if not self.config.get("git", {}).get("enabled", True):
                self._set_status_message("Git integration is disabled in config.")
                logging.debug("Git menu called but integration is disabled.")
                return

            # Проверяем, находимся ли мы в Git репозитории
            repo_dir = os.path.dirname(self.filename) if self.filename and os.path.exists(self.filename) else os.getcwd()
            if not os.path.isdir(os.path.join(repo_dir, ".git")):
                self._set_status_message("Not a Git repository.")
                logging.debug(f"Git menu called but {repo_dir} is not a git repository.")
                return


            commands = {
                "1": ("status", ["git", "status", "--short"]), # Короткий статус для вывода в статус-бар
                "2": ("commit", None),          # Commit message запрашивается отдельно
                "3": ("push",   ["git", "push"]),
                "4": ("pull",   ["git", "pull"]),
                "5": ("diff",   ["git", "diff", "--no-color", "--unified=0"]), # Короткий diff без цвета
            }

            # Формируем строку опций для промпта
            opts_str = " ".join(f"{k}:{v[0]}" for k, v in commands.items())
            choice = self.prompt(f"Git menu [{opts_str}] → ")

            if not choice or choice not in commands:
                self._set_status_message("Git menu cancelled or invalid choice")
                logging.debug(f"Git menu cancelled or invalid choice: {choice}")
                return

            command_name, cmd_args_template = commands[choice]
            cmd_list = []

            if command_name == "commit":
                # Специальная обработка для commit, т.к. нужен message
                msg = self.prompt("Commit message: ")
                if not msg:
                    self._set_status_message("Commit cancelled (no message)")
                    logging.debug("Git commit cancelled: no message")
                    return
                # Собираем команду commit
                cmd_list = ["git", "commit", "-am", msg] # "-am" для "add changes and commit"
            elif cmd_args_template is not None:
                # Для остальных команд, используем предопределенный список аргументов
                cmd_list = list(cmd_args_template) # Копируем список

            # Запускаем команду, если cmd_list сформирован
            if cmd_list:
                # Запускаем команду в отдельном потоке
                thread_name = f"GitExecThread-{command_name}-{int(time.time())}"
                threading.Thread(target=self._run_git_command_async,
                                args=(cmd_list, command_name),
                                daemon=True,
                                name=thread_name).start()
                # Ставим временный статус, результат будет из очереди
                self._set_status_message(f"Running git {command_name}...")
                logging.debug(f"Started Git command thread: {thread_name} for {cmd_list}")
            else:
                # Этого блока не должно быть при текущей логике, но на всякий случай
                logging.warning(f"Git menu: No command list generated for choice {choice}")
                self._set_status_message("Git menu internal error: command not prepared.")


            self._set_status_message("Git menu not implemented")
        except Exception as e:
            logging.error(f"Error in integrate_git: {e}", exc_info=True)
            self._set_status_message("Git menu error (see log)")


    def _fetch_git_info_async(self, file_path: str):
        """
        Выполняет получение Git-информации в отдельном потоке.
        Аргументы:
            file_path: путь к текущему файлу (может быть None).
        Отправляет результат (branch, user, commits) в self._git_q.
        """
        branch = ""
        user_name = ""
        commits = "0"
        is_dirty = False

        try:
            # Определяем корень репозитория, начиная с директории файла или текущей
            # Если file_path None или не существует, используем текущую директорию
            start_dir = os.path.dirname(os.path.abspath(file_path)) if file_path and os.path.exists(file_path) else os.getcwd()

            # Поиск корня репозитория вверх по директориям
            repo_dir = start_dir
            while repo_dir != os.path.dirname(repo_dir) and not os.path.isdir(os.path.join(repo_dir, ".git")):
                repo_dir = os.path.dirname(repo_dir)

            if not os.path.isdir(os.path.join(repo_dir, ".git")):
                logging.debug(f"_fetch_git_info_async: No .git directory found upwards from {start_dir}")
                # Нет репозитория, отправляем дефолтную информацию
                with self._state_lock:
                    self._git_q.put(("", "", "0"))
                return # Выходим из потока


            logging.debug(f"_fetch_git_info_async: Found git repo at {repo_dir}")

            # 1. Определяем ветку
            try:
                # git branch --show-current предпочтительнее
                branch_result = subprocess.run(
                    ["git", "branch", "--show-current"],
                    capture_output=True, text=True, check=True, cwd=repo_dir, encoding='utf-8', errors='replace', timeout=5
                )
                branch = branch_result.stdout.strip()

            except subprocess.CalledProcessError:
                # Fallback: git symbolic-ref (для detached HEAD или старых версий)
                try:
                    branch_result = subprocess.run(
                        ["git", "symbolic-ref", "--short", "HEAD"],
                        capture_output=True, text=True, check=True, cwd=repo_dir, encoding='utf-8', errors='replace', timeout=5
                    )
                    branch = branch_result.stdout.strip()
                except subprocess.CalledProcessError:
                    # Если HEAD не символическая ссылка (detached HEAD)
                    try:
                         # Попытка получить сокращенный SHA текущего коммита
                         branch_result = subprocess.run(
                            ["git", "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True, check=True, cwd=repo_dir, encoding='utf-8', errors='replace', timeout=5
                         )
                         branch = branch_result.stdout.strip()[:7] # Первые 7 символов SHA
                    except subprocess.CalledProcessError:
                         branch = "detached" # Не удалось получить SHA
                    except subprocess.TimeoutExpired:
                         logging.warning("Git rev-parse timed out during branch check.")
                         branch = "timeout" # Таймаут
                    except Exception as e:
                        logging.error(f"Unexpected error getting detached HEAD info: {e}")
                        branch = "error" # Не удалось получить SHA
                except subprocess.TimeoutExpired:
                     logging.warning("Git symbolic-ref timed out during branch check.")
                     branch = "timeout"
                except Exception as e:
                    logging.error(f"Unexpected error getting branch info: {e}")
                    branch = "error"

            except FileNotFoundError:
                 logging.warning("Git executable not found during async branch check")
                 branch = "" # Нет git
            except subprocess.TimeoutExpired:
                 logging.warning("Git branch --show-current timed out.")
                 branch = "timeout"
            except Exception as e:
                logging.error(f"Unexpected error getting branch info: {e}")
                branch = "error"

            # 2. Грязный репозиторий ?
            try:
                dirty_result = subprocess.run(
                    ["git", "status", "--porcelain", "--ignore-submodules"], # --porcelain для машиночитаемого вывода
                    capture_output=True, text=True, check=True, cwd=repo_dir, encoding='utf-8', errors='replace', timeout=5
                )
                if dirty_result.returncode == 0 and dirty_result.stdout.strip():
                    # Проверяем, есть ли изменения, не добавленные в индекс (Untracked, Modified)
                    # или добавленные в индекс (Staged).
                    # --porcelain выводит строки типа " M filename", "?? filename"
                    # Если вывод не пустой, значит, есть изменения.
                    is_dirty = True
                    # Ветка уже содержит "*", добавляем флаг грязности
                    # Ветка уже содержит '*', добавляем флаг грязности
                    if '*' not in branch: # Избегаем двойных '*'
                         branch += "*"

            except FileNotFoundError:
                logging.warning("Git executable not found during async status check")
            except subprocess.CalledProcessError as e:
                logging.warning(f"Git status --porcelain failed: {e}")
            except subprocess.TimeoutExpired:
                logging.warning("Git status --porcelain timed out.")
            except Exception as e:
                logging.error(f"Unexpected error getting git status: {e}")

            # 3. Имя пользователя
            try:
                user_result = subprocess.run(
                    ["git", "config", "user.name"],
                    capture_output=True, text=True, check=True, cwd=repo_dir, encoding='utf-8', errors='replace', timeout=5
                )
                user_name = user_result.stdout.strip() if user_result.returncode == 0 else ""
            except FileNotFoundError:
                logging.warning("Git executable not found during async user check")
            except subprocess.CalledProcessError:
                 user_name = ""
            except subprocess.TimeoutExpired:
                 logging.warning("Git config user.name timed out.")
                 user_name = "timeout"
            except Exception as e:
                logging.error(f"Unexpected error getting git user name: {e}")
                user_name = "error"

            # 4. Количество коммитов
            try:
                commits_result = subprocess.run(
                    ["git", "rev-list", "--count", "HEAD"],
                    capture_output=True, text=True, check=True, cwd=repo_dir, encoding='utf-8', errors='replace', timeout=5
                )
                commits = commits_result.stdout.strip() if commits_result.returncode == 0 and commits_result.stdout.strip().isdigit() else "0"
            except FileNotFoundError:
                logging.warning("Git executable not found during async commits check")
                commits = "0"
            except subprocess.CalledProcessError:
                commits = "0"
            except subprocess.TimeoutExpired:
                logging.warning("Git rev-list --count timed out.")
                commits = "timeout"
            except Exception as e:
                logging.error(f"Unexpected error getting git commit count: {e}")
                commits = "error"

        except FileNotFoundError:
            logging.warning("Git executable not found in async thread (initial check)")
            # Если git не найден в начале, отправляем дефолт
            branch, user_name, commits = "", "", "0"
        except Exception as e:
            logging.exception(f"Error fetching Git info in async thread: {e}")
            branch, user_name, commits = "fetch error", "", "0"

        # Отправляем результат в очередь
        try:
            with self._state_lock:
                self._git_q.put((branch, user_name, commits))
            logging.debug(f"Fetched async Git info and queued: {(branch, user_name, commits)}")
        except Exception as q_e:
            logging.error(f"Failed to put fetched Git info into queue: {q_e}")


    def update_git_info(self):
        """
        Запускает асинхронное обновление Git-информации, если интеграция включена
        и текущий файл изменился (или редактор открыл файл).
        Потокобезопасно проверяет необходимость обновления.
        """
        # Проверяем, включена ли интеграция с Git в конфиге
        if not self.config.get("git", {}).get("enabled", True) or not self.config.get("settings", {}).get("show_git_info", True):
            # Если Git отключен или отображение Git инфо отключено, сбрасываем git_info
            with self._state_lock:
                if self.git_info != ("", "", "0"):
                     self.git_info = ("", "", "0")
                     logging.debug("Git integration or display disabled, git_info set to default")
            return # Не запускаем поток

        with self._state_lock:
            current_filename = self.filename
            # Проверяем, нужно ли обновлять инфо для текущего файла
            # Обновляем, если filename изменился ИЛИ если git_info еще дефолтное (первый старт)
            # ИЛИ если текущий файл сохранен и его git_info последний раз обновлялось давно?
            # Простая логика: обновляем, если filename изменился с момента последнего обновления Git инфо.
            # Или если filename НЕ None и last_git_filename БЫЛ None (при создании нового файла)
            needs_update = False
            if current_filename != self._last_git_filename:
                needs_update = True
                logging.debug(f"Git update needed: filename changed from {self._last_git_filename} to {current_filename}")
            elif current_filename is not None and self._last_git_filename is None:
                needs_update = True
                logging.debug(f"Git update needed: filename set to {current_filename}")
            # Можно добавить периодическое обновление, но это усложнит логику (таймер)

            if needs_update:
                 self._last_git_filename = current_filename # Обновляем последний файл, для которого запущен запрос
                 logging.debug(f"Starting Git info update for {current_filename}")
                 thread_name = f"GitInfoFetchThread-{int(time.time())}"
                 threading.Thread(
                    target=self._fetch_git_info_async,
                    args=(current_filename,),
                    daemon=True, # Поток завершится при выходе программы
                    name=thread_name
                 ).start()
            # else:
                 # logging.debug(f"Git update skipped: filename unchanged ({current_filename}) and git_info not explicitly requested.")


    def _handle_git_info(self, git_data):
        """
        Обрабатывает и форматирует информацию о git для статус-бара.
        git_data: tuple (branch, user, commits)
        """
        with self._state_lock:
            self.git_info = git_data

        branch, user, commits = git_data
        git_status_msg = ""
        if branch or user or commits != "0":
            # Определяем цвет для Git инфо в статус-баре
            git_color = self.colors.get("git_info", curses.color_pair(12))
            if '*' in branch:
                git_color = self.colors.get("git_dirty", curses.color_pair(13))
            # Форматируем строку статуса Git
            git_status_msg = f"Git: {branch}"
            if commits != "0":
                git_status_msg += f" ({commits} commits)"
            # Можно добавить user по желанию:
            # if user: git_status_msg += f" by {user}"
            # Удаляем неотображаемые символы
            git_status_msg = ''.join(c if c.isprintable() else '?' for c in git_status_msg)
            # Сохраняем статус, можно также отрисовать в статус-бар
            self.status_message = git_status_msg
            logging.debug(f"Git status updated: {git_status_msg}")
        else:
            self.status_message = "Git: (no repo)"
            logging.debug("Git status updated: no repo found.")

#-------------- end GIT ------------------------------------------------------

    def goto_line(self):
        """Переходит на указанную строку. Поддерживает +N / -N от текущей."""
        # Сохраняем текущий статус
        original_status = self.status_message
        self._set_status_message(f"Go to line (1-{len(self.text)}, ±N, %): ")

        raw = self.prompt(f"Go to line (1-{len(self.text)}, ±N, %): ")
        if not raw:
            self._set_status_message(original_status if original_status else "Goto cancelled")
            logging.debug("Goto cancelled by user.")
            return

        try:
            target_line = None
            total_lines = len(self.text)

            if raw.endswith('%'):
                # Процент от длины файла
                try:
                    pct = float(raw.rstrip('%')) # Может быть дробным
                    if not (0 <= pct <= 100):
                         self._set_status_message("Percentage out of range (0-100)")
                         return
                    # Рассчитываем целевую строку (1-based)
                    target_line = max(1, min(total_lines, round(total_lines * pct / 100.0)))
                    logging.debug(f"Goto: Percentage {pct}%, target line {target_line}")
                except ValueError:
                    self._set_status_message("Invalid percentage format")
                    logging.warning(f"Goto: Invalid percentage format '{raw}'")
                    return
            elif raw.startswith(('+', '-')):
                # Относительный сдвиг
                try:
                    delta = int(raw)
                    # Текущая строка (0-based) + 1 + delta -> целевая строка (1-based)
                    target_line = self.cursor_y + 1 + delta
                    logging.debug(f"Goto: Relative delta {delta}, current line {self.cursor_y + 1}, target line {target_line}")
                except ValueError:
                    self._set_status_message("Invalid relative number format")
                    logging.warning(f"Goto: Invalid relative number format '{raw}'")
                    return
            else:
                # Абсолютный номер строки
                try:
                    target_line = int(raw)
                    logging.debug(f"Goto: Absolute target line {target_line}")
                except ValueError:
                    self._set_status_message("Invalid line number format")
                    logging.warning(f"Goto: Invalid line number format '{raw}'")
                    return

            # Проверяем, что рассчитанный номер строки находится в допустимом диапазоне (1-based)
            if target_line is None or not (1 <= target_line <= total_lines):
                 self._set_status_message(f"Line out of range (1–{total_lines})")
                 logging.warning(f"Goto: Target line {target_line} out of range (1-{total_lines})")
                 return

            # Перемещаем курсор на целевую строку (0-based)
            self.cursor_y = target_line - 1
            self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y])) # Сохраняем колонку, если возможно

            # Adjust vertical scroll to bring the cursor into view, ideally centering it
            height, width = self.stdscr.getmaxyx()
            text_area_height = max(1, height - 2) # Высота области текста

            # Если целевая строка не в пределах текущего вида
            if self.cursor_y < self.scroll_top or self.cursor_y >= self.scroll_top + text_area_height:
                 # Сдвигаем scroll_top так, чтобы целевая строка была примерно по центру
                 self.scroll_top = max(0, self.cursor_y - text_area_height // 2)
                 # Убедимся, что scroll_top не выходит за пределы
                 self.scroll_top = min(self.scroll_top, max(0, len(self.text) - text_area_height))

            # Горизонтальная прокрутка будет скорректирована в _position_cursor

            self._set_status_message(f"Moved to line {target_line}")
            logging.debug(f"Goto: Cursor moved to line {target_line}")

        except Exception as e:
            # Любая другая непредвиденная ошибка
            self._set_status_message(f"Goto error: {str(e)[:80]}...")
            logging.exception(f"Unexpected error in goto_line for input '{raw}'")


    def toggle_insert_mode(self) -> None:
        """
        Switch between *Insert* (default) and *Replace* modes.

        *Insert*  – вставляет символ перед курсором, сдвигая хвост строки вправо.  
        *Replace* – заменяет символ под курсором (если есть), не меняя длину строки.
        """
        self.insert_mode = not self.insert_mode
        mode_txt = "Insert" if self.insert_mode else "Replace"
        logging.debug("Insert‑mode toggled → %s", mode_txt)
        self._set_status_message(f"Mode: {mode_txt}")


# ==================== bracket =======================
    def highlight_matching_brackets(self) -> None:
        """
        Подсветить парную скобку к той, на которой сейчас стоит курсор.
        Работает только в рамках одной строки (как и find_matching_bracket).
        """
        # ── 1. Курсор должен быть в пределах текста и экрана ──────────────────
        height, width = self.stdscr.getmaxyx()
        text_area_height = max(1, height - 2)

        if not (0 <= self.cursor_y < len(self.text)):
            return  # курсор вне текста

        if not (self.scroll_top <= self.cursor_y < self.scroll_top + text_area_height):
            return  # строка курсора не видна

        line = self.text[self.cursor_y]
        if not line:  # пустая строка
            return

        # ── 2. Определяем символ-скобку под курсором (или перед ним) ──────────
        col = self.cursor_x
        if col >= len(line):  # курсор «за» концом строки
            col = len(line) - 1

        if col < 0 or col >= len(line):  # двойная проверка на безопасность
            return

        if line[col] not in '(){}[]':
            return  # не на скобке

        bracket_char = line[col]

        # ── 3. Находим парную скобку ────────────────────────────────────────────
        match = self.find_matching_bracket(line, col, bracket_char)
        if not match:
            return  # пара не найдена

        match_y, match_x = match  # по контракту: match_y == self.cursor_y
        if not (0 <= match_x < len(self.text[match_y])):  # защита
            return

        # ── 4. Рассчитываем координаты для curses ──────────────────────────────
        line_num_width = len(str(max(1, len(self.text)))) + 1  # ширина префикса «NN »
        def to_screen_x(row: int, col_: int) -> int:
            return line_num_width + self.get_string_width(self.text[row][:col_]) - self.scroll_left

        scr_y  = self.cursor_y - self.scroll_top
        scr_x1 = to_screen_x(self.cursor_y, col)
        scr_x2 = to_screen_x(match_y,     match_x)

        if not (line_num_width <= scr_x1 < width):
            return
        if not (line_num_width <= scr_x2 < width):
            return

        # ── 5. Подсвечиваем обе скобки ─────────────────────────────────────────
        try:
            self.stdscr.chgat(scr_y, scr_x1, self.get_char_width(bracket_char), curses.A_REVERSE)
            self.stdscr.chgat(scr_y, scr_x2, self.get_char_width(self.text[match_y][match_x]), curses.A_REVERSE)
        except curses.error:
            pass  # не критично, если подсветка не сработает


    def find_matching_bracket(self, line, col, bracket):
        """
        Searches for the matching bracket for the one under the cursor *within the same line*.
        Returns (row, col) of the matching bracket or None if not found.
        Limitations: does not search across lines.
        """
        # Поддерживаемые пары скобок
        brackets = {"(": ")", "{": "}", "[": "]", ")": "(", "}": "{", "]": "["}
        open_brackets = "({["
        close_brackets = ")}]"

        if not isinstance(bracket, str) or len(bracket) != 1 or bracket not in brackets:
            return None # Не является одной из поддерживаемых скобок

        is_open = bracket in open_brackets
        # Целевая скобка для поиска
        target_bracket = brackets[bracket]

        stack = []
        # Направление поиска: вправо для открывающих, влево для закрывающих
        direction = 1 if is_open else -1
        # Начальная позиция поиска: следующий/предыдущий символ от позиции `col`
        # col - это индекс скобки, для которой ищем пару.
        start_pos = col + direction

        # Итерируем по строке в нужном направлении
        if direction == 1: # Поиск вправо от col
            for i in range(start_pos, len(line)):
                char = line[i]
                if char in open_brackets:
                    stack.append(char) # Добавляем в стек встреченные открывающие скобки
                elif char in close_brackets:
                    # Встретили закрывающую скобку
                    if not stack:
                        # Если стек пуст, эта закрывающая скобка может быть парой для искомой открывающей скобки
                        # (если искомая была открывающей).
                        if char == target_bracket:
                            return (self.cursor_y, i) # Найдена парная скобка
                    else:
                        # Если стек не пуст, эта закрывающая скобка соответствует внутренней открывающей.
                        # Проверяем, соответствует ли она последней скобке в стеке.
                        top_of_stack = stack[-1]
                        if brackets[top_of_stack] == char:
                             stack.pop() # Вынимаем соответствующую открывающую из стека
                        # Если не соответствует, это ошибка синтаксиса, игнорируем для поиска пары.

        else: # direction == -1, поиск влево от col
             for i in range(start_pos, -1, -1):
                char = line[i]
                if char in close_brackets:
                    stack.append(char) # Добавляем в стек встреченные закрывающие скобки
                elif char in open_brackets:
                    # Встретили открывающую скобку
                    if not stack:
                        # Если стек пуст, эта открывающая скобка может быть парой для искомой закрывающей скобки
                        # (если искомая была закрывающей).
                        if char == target_bracket:
                            return (self.cursor_y, i) # Найдена парная скобка
                    else:
                         # Если стек не пуст, эта открывающая скобка соответствует внутренней закрывающей.
                        top_of_stack = stack[-1]
                        if brackets[char] == top_of_stack: # Проверяем соответствие
                            stack.pop() # Вынимаем соответствующую закрывающую из стека
                        # Если не соответствует, это ошибка синтаксиса, игнорируем.

        return None # Пара не найдена в пределах строки или синтаксис некорректен


    def show_help(self):
        """
        Показывает всплывающее окно со справкой.
        """
        logging.debug("show_help called")
        try:
             
            # Текст справки
            help_lines = [
                "  ──  Sway-Pad Help  ──  ",
                "",
                "  F1        : Help",
                "  F2        : New file",
                "  F3        : Find next",
                "  F4        : Linter",
                "  F5        : Save as…",
                "  F6        : Search/Replace",
                "  F9        : Git-меню",

                "  Ctrl+S    : Save",
                "  Ctrl+O    : Open file",
                "  Ctrl+C    : Copy",
                "  Ctrl+X    : Cut",
                "  Ctrl+V    : Paste",

                "  Ctrl+Z    : Undo",
                "  Shift+Z   : Redo",

                "  Ctrl+F    : Find",
                "  Ctrl+G    : Go to line",
                "  Ctrl+Q    : Quit",
                "  Ctrl+A    : Select all",

                "  Shift+Arrows/Home/End: Extend selection",

                "----------------------------",
                "",
                "  © 2025 Siergej Sobolewski — Sway-Pad",
                "  Licensed under the GPLv3 License",
                "",
                "  Press any key to close", # Изменено с Esc
            ]

            # Расчет размеров окна справки
            h = len(help_lines) + 2 # Высота = количество строк + верхняя/нижняя рамка
            w = max(self.get_string_width(l) for l in help_lines) + 4  # Ширина = макс. ширина строки + левая/правая рамка и отступы
            max_y, max_x = self.stdscr.getmaxyx() # Размеры главного окна

            # Убедимся, что окно справки помещается в главный окно
            w = min(w, max_x - 2) # Не шире главного окна минус рамка
            h = min(h, max_y - 2) # Не выше главного окна минус рамка

            # Расчет позиции окна справки (центрирование)
            y0 = max(0, (max_y - h) // 2)
            x0 = max(0, (max_x - w) // 2)

            win = None # Объект окна для справки
            try:
                # Создаем новое окно для справки
                win = curses.newwin(h, w, y0, x0)
                win.bkgd(" ", curses.color_pair(0))
                win.border() # Рисуем рамку

                # Пишем текст справки в окно
                for i, text in enumerate(help_lines):
                    display_text = text[:w - 4] # Обрезаем, оставляя место для отступа и рамки
                    try:
                        # Рисуем строку, начиная с отступа 2 от левой рамки
                        win.addstr(i + 1, 2, display_text)
                    except curses.error as e:
                        logging.warning(f"Curses error drawing help text line {i}: {e}")
                        pass # Пропускаем, если не удалось нарисовать строку

                # Обновляем окно справки на экране
                win.noutrefresh() # Обновляем в памяти, но не на экране сразу

            except curses.error as e:
                logging.error(f"Curses error creating or drawing help window: {e}")
                self._set_status_message(f"Error displaying help: {str(e)[:80]}...")
                # Если создать окно не удалось, выходим из функции

            # Прячем курсор и запоминаем предыдущее состояние видимости курсора
            prev_vis = 1 # Значение по умолчанию (видимый)
            try:
                prev_vis = curses.curs_set(0)   # 0 = invisible, 1/2 = visible
                logging.debug(f"Hid cursor for help, previous state: {prev_vis}")
            except curses.error as e:
                logging.warning(f"Curses error hiding cursor for help: {e}. Terminal may not support cursor visibility changes.")
                # Если терминал не поддерживает, prev_vis останется 1

            # Обновляем главный экран (под окном справки) и затем окно справки
            # Это гарантирует, что окно справки отрисовано поверх всего
            self.stdscr.noutrefresh()
            if win: # Если окно было успешно создано
                win.noutrefresh()
            curses.doupdate() # Обновляем физический экран

            # Ждём нажатия любой клавиши для закрытия справки
            # Переключаем в блокирующий режим без задержки (уже сделано для prompt, но на всякий случай)
            try:
                self.stdscr.nodelay(False)
                # curses.flushinp() # Очищаем буфер ввода перед getch
                logging.debug("Waiting for key press to close help window.")
                ch = self.stdscr.getch() # Ждем любую клавишу
                KEY_LOGGER.debug(f"Help closed by key code: {ch}")

            except curses.error as e_getch:
                logging.error(f"Curses error getting char to close help: {e_getch}")
                # Если getch упал, возможно, терминал в плохом состоянии.
                # Попробуем восстановить курсор и выйти.
                pass # Продолжаем в блок finally

            # ★ Восстанавливаем видимость курсора и очищаем окно справки
            finally:
                logging.debug("Closing help window.")
                try:
                    # Удаляем окно справки из памяти curses
                    if win:
                        del win
                    # Восстанавливаем видимость курсора до предыдущего состояния
                    try:
                        curses.curs_set(prev_vis)
                        logging.debug(f"Restored cursor visibility to {prev_vis}")
                    except curses.error as e_curs_set:
                        logging.warning(f"Curses error restoring cursor after help: {e_curs_set}. Terminal may not support.")

                    # Очищаем буфер ввода на всякий случай
                    curses.flushinp()
                    # Перерисовываем весь экран, чтобы убрать окно справки
                    self.drawer.draw() # Вызываем метод отрисовки

                except curses.error as e_finally:
                    logging.critical(f"Critical Curses error during help window cleanup: {e_finally}", exc_info=True)
                    # Если очистка упала, может быть нужно экстренное завершение.
                    print(f"\nCritical Curses error during help window cleanup: {e_finally}", file=sys.stderr)
                    # Пробросим исключение для обработки в основном цикле
                    raise RuntimeError(f"Critical Curses error during help window cleanup: {e_finally}") from e_finally

            self._set_status_message("Help displayed")
        except Exception as e:
            logging.error(f"Error in show_help: {e}", exc_info=True)
            self._set_status_message("Help error (see log)")


    # =============  Главный цикл редактора  =========================================

    def run(self) -> None:
        """
        Главный цикл редактора с полной поддержкой ввода и раскладок.
        """
        logging.info("Editor main loop started")
        
        self.stdscr.nodelay(True)
        self.stdscr.keypad(True)

        needs_redraw   = True
        last_draw_time = 0.0
        FPS            = 30

        while True:
            try:
                if self._process_all_queues():
                    needs_redraw = True
            except Exception:
                logging.exception("queue-processing error")
                self._set_status_message("Queue error (see log)")
                curses.flushinp()
                needs_redraw = True

            try:
                key = self.stdscr.get_wch()  # str | int | -1
                logging.debug(f"Received key: {repr(key)} ({chr(key) if isinstance(key, int) and 0 < key < 256 else ''})")

                if key != -1:
                    if isinstance(key, str) and len(key) == 1:
                        key = ord(key)
                    self.handle_input(key)
                    needs_redraw = True

            except KeyboardInterrupt:
                self.exit_editor()
            except curses.error as e:
                if str(e) != "no input":
                    logging.error("curses input: %s", e)
                    self._set_status_message(f"Input error: {e}")
                    curses.flushinp()
                    needs_redraw = True
            except Exception:
                logging.exception("input error")
                self._set_status_message("Input error")
                curses.flushinp()
                needs_redraw = True

            now = time.time()
            if needs_redraw and now - last_draw_time >= 1 / FPS:
                try:
                    self.drawer.draw()
                except curses.error as e:
                    logging.error("draw: %s", e)
                    self._set_status_message("Draw error")
                except Exception:
                    logging.exception("draw error")
                    self._set_status_message("Draw error")
                last_draw_time = now
                needs_redraw = False

            time.sleep(0.005)


    def _process_all_queues(self) -> bool:
        """
        Обрабатывает все внутренние очереди сообщений.
        Возвращает True, если что-то обработано и требуется перерисовка.
        """
        processed_any = False

        # --- общая очередь сообщений (_msg_q) ---
        while True:
            try:
                msg = self._msg_q.get_nowait()
            except queue.Empty:
                break

            processed_any = True

            if isinstance(msg, tuple):
                msg_type, *msg_data = msg

                if msg_type == "auto_save":
                    text_lines, filename, encoding = msg_data
                    try:
                        with open(filename, "w", encoding=encoding, errors="replace") as f:
                            f.write(os.linesep.join(text_lines))
                        with self._state_lock:
                            self.modified = False
                        self.status_message = f"Auto-saved {os.path.basename(filename)}"
                        logging.info(f"Auto-saved {filename}")
                        if self._lexer and getattr(self._lexer, "name", None) in ("python", "python3"):
                            threading.Thread(
                                target=self.run_lint_async,
                                args=(os.linesep.join(text_lines),),
                                daemon=True
                            ).start()
                        self.update_git_info()
                    except Exception as e:
                        logging.exception("Auto-save error")
                        self.status_message = f"Auto-save error: {e}"

                elif msg_type == "status":
                    self.status_message = msg_data[0]
                    logging.debug(f"Set status_message to: {msg_data[0]}")

                else:
                    logging.warning(f"Unknown queue tuple ignored: {msg!r}")

            elif isinstance(msg, str):
                # Поддержка старого формата: просто строка — статус
                self.status_message = msg
                logging.debug(f"Set status_message to (legacy): {msg}")

            else:
                logging.warning(f"Unknown queue item ignored: {msg!r}")

        # --- очередь результатов shell-команд ---
        while True:
            try:
                result = self._shell_cmd_q.get_nowait()
            except queue.Empty:
                break
            processed_any = True
            self.status_message = str(result)
            logging.debug(f"Shell command result set to status: {result}")

        # --- очередь обновлённого git_info ---
        while True:
            try:
                git_data = self._git_q.get_nowait()
            except queue.Empty:
                break
            processed_any = True
            if isinstance(git_data, tuple) and len(git_data) >= 3:
                self._handle_git_info(git_data)
            else:
                logging.warning(f"Unknown git_data format in _git_q: {git_data!r}")

        # --- очередь результатов git-команд ---
        while True:
            try:
                git_msg = self._git_cmd_q.get_nowait()
            except queue.Empty:
                break
            processed_any = True
            text = str(git_msg)
            if text == "update_git_info":
                self.update_git_info()
                logging.debug("Git info update requested from git_cmd_q.")
            else:
                self.status_message = text
                logging.debug(f"Git command result set to status: {text}")

        return processed_any



# Class DrawScreen ------------------------------------------------------
class DrawScreen:
    """
    Класс для отрисовки экрана редактора.
    Содержит логику построения интерфейса с использованием curses.
    """

    def __init__(self, editor):
        self.editor = editor
        self.stdscr = editor.stdscr
        self.colors = editor.colors # Ссылка на словарь цветов редактора


    def draw(self):
        """Основной метод отрисовки экрана."""
        try:
            # 1. Обрабатываем фоновые очереди (auto-save, shell, git и т.п.)
            self.editor._process_all_queues()
            height, width = self.stdscr.getmaxyx()

            # 2. Проверяем минимальный размер окна
            if height < 5 or width < 20:
                self._show_small_window_error(height, width)
                self.editor.last_window_size = (height, width)
                self.stdscr.refresh()
                return

            # 3. Если размер окна изменился, пересчитываем видимые строки
            if (height, width) != self.editor.last_window_size:
                self.editor.visible_lines = max(1, height - 2)
                self.editor.last_window_size = (height, width)
                self.editor.scroll_left = 0
                self._adjust_vertical_scroll()
                logging.debug(f"Window resized to {width}x{height}. Visible lines: {self.editor.visible_lines}. Scroll left reset.")

            # 4. Очищаем экран
            self.stdscr.clear()

            # 5. Рисуем основной интерфейс
            self._draw_line_numbers()
            self._draw_text_with_syntax_highlighting()
            self._draw_search_highlights()
            self._draw_selection()
            self._draw_matching_brackets()
            self._draw_status_bar()

            # 6. Рисуем всплывающую панель линтера (если она активна)
            self._draw_lint_panel()   # <--- Вставить здесь обязательно!

            # 7. Позиционируем курсор (если панель не активна)
            if not getattr(self.editor, 'lint_panel_active', False):
                self._position_cursor()

            # 8. Обновляем экран
            self._update_display()

        except curses.error as e:
            logging.error(f"Curses error in DrawScreen.draw(): {e}", exc_info=True)
            self.editor._set_status_message(f"Draw error: {str(e)[:80]}...")

        except Exception as e:
            logging.exception("Unexpected error in DrawScreen.draw()")
            self.editor._set_status_message(f"Draw error: {str(e)[:80]}...")



    # def draw(self):
    #     """Основной метод отрисовки экрана."""
    #     try:
    #         # сначала обрабатываем фоновые очереди (auto-save, shell, git и т.п.)
    #         self.editor._process_all_queues()
    #         height, width = self.stdscr.getmaxyx()

    #         # Проверяем минимальный размер окна
    #         if height < 5 or width < 20:
    #              self._show_small_window_error(height, width)
    #              self.editor.last_window_size = (height, width) # Обновляем размер
    #              # В случае слишком маленького окна, не пытаемся рисовать остальное
    #              self.stdscr.refresh() # Обновляем экран, чтобы показать сообщение
    #              return

    #         # Если размер окна изменился, пересчитываем видимые строки
    #         if (height, width) != self.editor.last_window_size:
    #             self.editor.visible_lines = max(1, height - 2) # Высота текста = высота окна - 1 строка номера - 1 строка статуса
    #             self.editor.last_window_size = (height, width)
    #             # При изменении размера, сбрасываем горизонтальную прокрутку, т.к. ее позиция может стать некорректной
    #             self.editor.scroll_left = 0
    #             # Также пересчитываем scroll_top, чтобы курсор остался на экране
    #             self._adjust_vertical_scroll()
    #             logging.debug(f"Window resized to {width}x{height}. Visible lines: {self.editor.visible_lines}. Scroll left reset.")

    #         # Очищаем экран (или только область текста)
    #         self.stdscr.clear() # Полная очистка экрана
    #         # Рисуем номер строки
    #         self._draw_line_numbers()
    #         # Рисуем текст, подсветки, поиск, выделение
    #         self._draw_text_with_syntax_highlighting()     
    #         # Накладываем подсветку поиска поверх текста
    #         self._draw_search_highlights()
    #         # Накладываем подсветку выделения поверх всего
    #         self._draw_selection()         
    #         # Накладываем подсветку парных скобок поверх всего
    #         self._draw_matching_brackets() # NEW
    #         # Рисуем статус-бар 
    #         self._draw_status_bar()
    #         # Позиционируем курсор
    #         self._position_cursor()
    #         # Обновляем физический экран
    #         self._update_display()

    #     except curses.error as e:
    #          # Ловим ошибки Curses при отрисовке
    #          logging.error(f"Curses error in DrawScreen.draw(): {e}", exc_info=True)
    #          # Пытаемся установить статус (потокобезопасно)
    #          self.editor._set_status_message(f"Draw error: {str(e)[:80]}...")

    #     except Exception as e:
    #         # Ловим другие ошибки, не связанные с Curses, в процессе отрисовки
    #         logging.exception("Unexpected error in DrawScreen.draw()")
    #         self.editor._set_status_message(f"Draw error: {str(e)[:80]}...")


    def _show_small_window_error(self, height, width):
        """Отображает сообщение о слишком маленьком окне."""
        msg = f"Window too small ({width}x{height}). Minimum is 20x5."
        try:
            self.stdscr.clear() # Очищаем перед сообщением
            # Центрируем сообщение, если возможно
            msg_len = len(msg)
            start_col = max(0, (width - msg_len) // 2)
            self.stdscr.addstr(height // 2, start_col, msg)
        except curses.error:
            # Если даже это не сработало, терминал в плохом состоянии
            pass


    def _draw_line_numbers(self):
        """Рисует номера строк."""
        height, width = self.stdscr.getmaxyx()
        # Рассчитываем ширину, необходимую для номеров строк
        # Максимальный номер строки - это общее количество строк в файле
        max_line_num = len(self.editor.text)
        max_line_num_digits = len(str(max(1, max_line_num))) # Минимум 1 цифра для пустых файлов
        line_num_width = max_line_num_digits + 1 # +1 для пробела после номера

        # Проверяем, помещаются ли номера строк в ширину окна
        if line_num_width >= width:
             logging.warning(f"Window too narrow to draw line numbers ({width} vs {line_num_width})")
             # Если не помещаются, пропускаем отрисовку номеров
             self._text_start_x = 0 # Текст начинается с 0-й колонки
             return
        # Сохраняем начальную позицию для отрисовки текста
        self._text_start_x = line_num_width
        line_num_color = self.colors.get("line_number", curses.color_pair(7))
        # Итерируем по видимым строкам на экране
        for screen_row in range(self.editor.visible_lines):
            # Рассчитываем индекс строки в self.text
            line_idx = self.editor.scroll_top + screen_row
            # Проверяем, существует ли эта строка в self.text
            if line_idx < len(self.editor.text):
                # Форматируем номер строки (1-based)
                line_num_str = f"{line_idx + 1:>{max_line_num_digits}} " # Выравнивание по правому краю + пробел
                try:
                    # Рисуем номер строки
                    self.stdscr.addstr(screen_row, 0, line_num_str, line_num_color)
                except curses.error as e:
                    logging.error(f"Curses error drawing line number at ({screen_row}, 0): {e}")
                    # В случае ошибки, пропускаем отрисовку этой строки и продолжаем
            else:
                 # рисуем пустые строки с нужным фоном в области номеров
                 empty_num_str = " " * line_num_width
                 try:
                    self.stdscr.addstr(screen_row, 0, empty_num_str, line_num_color)
                 except curses.error as e:
                    logging.error(f"Curses error drawing empty line number background at ({screen_row}, 0): {e}")


    def _draw_lint_panel(self):
        """
        Рисует всплывающую панель с результатом линтера.
        """
        if not getattr(self.editor, 'lint_panel_active', False):
            return
        msg = self.editor.lint_panel_message
        if not msg:
            return
        h, w = self.stdscr.getmaxyx()
        panel_height = min(max(6, msg.count('\n') + 4), h - 2)
        panel_width = min(max(40, max(len(line) for line in msg.splitlines()) + 4), w - 4)
        start_y = max(1, (h - panel_height) // 2)
        start_x = max(2, (w - panel_width) // 2)

        # Рамка окна
        try:
            for i in range(panel_height):
                line = ""
                if i == 0:
                    line = "┌" + "─" * (panel_width - 2) + "┐"
                elif i == panel_height - 1:
                    line = "└" + "─" * (panel_width - 2) + "┘"
                else:
                    line = "│" + " " * (panel_width - 2) + "│"
                self.stdscr.addstr(start_y + i, start_x, line, curses.A_BOLD)

            # Сообщение, разбитое по строкам
            msg_lines = msg.splitlines()
            for idx, line in enumerate(msg_lines[:panel_height - 3]):
                self.stdscr.addnstr(
                    start_y + idx + 1, start_x + 2,
                    line.strip(), panel_width - 4, curses.A_NORMAL
                )
            # Footer
            footer = "Press Esc to close"
            self.stdscr.addnstr(
                start_y + panel_height - 2, start_x + 2,
                footer, panel_width - 4, curses.A_DIM
            )
        except curses.error as e:
            logging.error(f"Ошибка curses при отрисовке панели линтера: {e}")


    def _draw_text_with_syntax_highlighting(self):
        """Рисует текст с синтаксической подсветкой."""
        height, width = self.stdscr.getmaxyx()
        # text_area_width не используется напрямую в этой версии, но полезен для понимания
        # text_area_width = max(1, width - self._text_start_x) 

        start_line = self.editor.scroll_top
        end_line = min(start_line + self.editor.visible_lines, len(self.editor.text))

        if start_line >= end_line:
            logging.debug("DrawScreen draw_text: No visible lines to draw.")
            return

        visible_lines_content = self.editor.text[start_line:end_line]
        line_indices = list(range(start_line, end_line))

        logging.debug(
            f"DrawScreen draw_text: Drawing lines {start_line}-{end_line-1}. "
            f"scroll_left={self.editor.scroll_left}, text_start_x={self._text_start_x}, total_window_width={width}"
        )

        # highlighted_lines_tokens это list[list[tuple[str, int]]]
        # Внешний список - по строкам, внутренний - по токенам в строке, кортеж - (текст_токена, атрибут_цвета)
        highlighted_lines_tokens = self.editor.apply_syntax_highlighting_with_pygments(visible_lines_content, line_indices)

        for screen_row, (text_line_index, tokens_for_this_line) in enumerate(zip(line_indices, highlighted_lines_tokens)):
            
            original_line_text_for_log = self.editor.text[text_line_index]
            logging.debug(
                f"  DrawScreen draw_text: Line {text_line_index} (screen_row {screen_row}), "
                f"Original content: '{original_line_text_for_log[:70].replace(chr(9), '/t/')}{'...' if len(original_line_text_for_log)>70 else ''}'"
            )
            # Логгируем токены, которые пришли для этой строки
            logging.debug(
                f"    DrawScreen draw_text: Tokens for line {text_line_index}: "
                f"{[(token_text.replace(chr(9), '/t/'), token_attr) for token_text, token_attr in tokens_for_this_line if isinstance(token_text, str)]}"
            )
            # logical_char_col_abs - суммарная *логическая ширина* (от wcwidth) символов от начала строки
            logical_char_col_abs = 0 
            
            for token_index, (token_text_content, token_color_attribute) in enumerate(tokens_for_this_line):
                logging.debug(
                    f"      DrawScreen draw_text: Token {token_index}: text='{token_text_content.replace(chr(9),'/t/')}', attr={token_color_attribute}"
                )
                if not token_text_content: # Пропускаем пустые токены, если такие есть
                    logging.debug("        DrawScreen draw_text: Skipping empty token.")
                    continue

                for char_index_in_token, char_to_render in enumerate(token_text_content):
                    char_printed_width = self.editor.get_char_width(char_to_render)
                    
                    # Логгируем информацию о символе ДО обработки его ширины
                    logging.debug(
                        f"        DrawScreen draw_text: Char '{char_to_render.replace(chr(9),'/t/')}' (idx_in_token {char_index_in_token}), "
                        f"current_logical_col_abs_BEFORE_this_char={logical_char_col_abs}, char_width={char_printed_width}"
                    )

                    if char_printed_width == 0: 
                        logging.debug("          DrawScreen draw_text: Skipping zero-width char.")
                        continue # logical_char_col_abs не увеличивается

                    char_ideal_screen_start_x = self._text_start_x + (logical_char_col_abs - self.editor.scroll_left)
                    char_ideal_screen_end_x = char_ideal_screen_start_x + char_printed_width

                    is_char_visible_on_screen = (char_ideal_screen_end_x > self._text_start_x and
                                                 char_ideal_screen_start_x < width)

                    if is_char_visible_on_screen:
                        actual_draw_x = max(self._text_start_x, char_ideal_screen_start_x)

                        if actual_draw_x < width:
                            try:
                                logging.debug(
                                    f"          DrawScreen draw_text: DRAWING Char '{char_to_render.replace(chr(9),'/t/')}' "
                                    f"at screen ({screen_row}, {actual_draw_x}), "
                                    f"ideal_X={char_ideal_screen_start_x}, "
                                    f"final_attr={token_color_attribute}"
                                )
                                self.stdscr.addch(screen_row, actual_draw_x, char_to_render, token_color_attribute)
                            except curses.error as e:
                                logging.warning(
                                    f"          DrawScreen draw_text: CURSES ERROR drawing char '{char_to_render.replace(chr(9),'/t/')}' (ord: {ord(char_to_render)}) "
                                    f"at ({screen_row}, {actual_draw_x}) with attr {token_color_attribute}. Error: {e}"
                                )
                                break 
                        else:
                            logging.debug(
                                f"          DrawScreen draw_text: Char '{char_to_render.replace(chr(9),'/t/')}' not drawn, actual_draw_x={actual_draw_x} >= width={width}."
                            )
                    else:
                        logging.debug(
                            f"          DrawScreen draw_text: Char '{char_to_render.replace(chr(9),'/t/')}' not visible. "
                            f"Ideal screen X range: [{char_ideal_screen_start_x} - {char_ideal_screen_end_x}). "
                            f"Visible text area X range: [{self._text_start_x} - {width-1}]."
                        )
                    
                    logical_char_col_abs += char_printed_width
                    
                    # Проверка, не вышли ли мы за правую границу окна по логической ширине
                    next_char_ideal_screen_start_x = self._text_start_x + (logical_char_col_abs - self.editor.scroll_left)
                    if next_char_ideal_screen_start_x >= width:
                        logging.debug(
                            f"        DrawScreen draw_text: Next char would start at or beyond window width ({next_char_ideal_screen_start_x} >= {width}). "
                            f"Breaking inner char loop."
                        )
                        break 
                
                # Если внутренний цикл (по символам) был прерван (break), то прерываем и внешний (по токенам)
                else: # Этот 'else' относится к 'for char_index_in_token...'
                    continue 
                logging.debug(f"      DrawScreen draw_text: Broken from char loop, breaking token loop as well.")
                break 
            logging.debug(f"    DrawScreen draw_text: Finished processing tokens for line {text_line_index}. Final logical_char_col_abs = {logical_char_col_abs}")


    def _draw_search_highlights(self):
        """Накладывает подсветку найденных совпадений."""
        if not self.editor.highlighted_matches:
            return # Нет совпадений для подсветки

        # Цвет для подсветки поиска (например, A_REVERSE или специальная пара)
        search_color = self.colors.get("search_highlight", curses.A_REVERSE)
        height, width = self.stdscr.getmaxyx()
        line_num_width = len(str(max(1, len(self.editor.text)))) + 1 # Ширина номера строки + пробел
        text_area_width = max(1, width - line_num_width)

        # Итерируем по всем совпадениям, которые нужно подсветить
        for match_row, match_start_idx, match_end_idx in self.editor.highlighted_matches:
            # Проверяем, находится ли строка с совпадением в видимой области
            if match_row < self.editor.scroll_top or match_row >= self.editor.scroll_top + self.editor.visible_lines:
                continue # Строка не на экране, пропускаем

            screen_y = match_row - self.editor.scroll_top # Экранная строка для этого совпадения
            line = self.editor.text[match_row] # Оригинальная строка текста

            # Позиция X на экране, где начинается совпадение
            match_screen_start_x_before_scroll = self.editor.get_string_width(line[:match_start_idx])
            match_screen_start_x = line_num_width + match_screen_start_x_before_scroll - self.editor.scroll_left

            # Позиция X на экране, где заканчивается совпадение (или начинается следующий символ)
            match_screen_end_x_before_scroll = self.editor.get_string_width(line[:match_end_idx])
            match_screen_end_x = line_num_width + match_screen_end_x_before_scroll - self.editor.scroll_left

            # Определяем видимую часть совпадения на экране
            # Начальная X для отрисовки подсветки (не меньше, чем _text_start_x)
            draw_start_x = max(line_num_width, match_screen_start_x)

            # Конечная X для отрисовки подсветки (не больше, чем правый край окна)
            draw_end_x = min(width, match_screen_end_x)

            # Рассчитываем реальную ширину подсветки на экране
            highlight_width_on_screen = max(0, draw_end_x - draw_start_x)

            # Применяем атрибут подсветки, если видимая ширина больше 0
            if highlight_width_on_screen > 0:
                try:
                    # Итерируем по символам оригинальной строки
                    current_char_screen_x = line_num_width - self.editor.scroll_left # Начальная X для первого символа строки
                    for char_idx, char in enumerate(line):
                        char_width = self.editor.get_char_width(char)
                        char_screen_end_x = current_char_screen_x + char_width

                        # Если символ находится в диапазоне совпадения и виден на экране
                        if match_start_idx <= char_idx < match_end_idx and \
                           current_char_screen_x < width and char_screen_end_x > line_num_width: # Проверка видимости

                            # Координаты отрисовки символа на экране
                            draw_char_x = max(line_num_width, current_char_screen_x)
                            draw_char_width = min(char_width, width - draw_char_x)

                            if draw_char_width > 0:
                               try:
                                  # Подсвечиваем отдельный символ
                                  # chgat(y, x, num_chars, attr). num_chars=1 для одного символа
                                  self.stdscr.chgat(screen_y, draw_char_x, 1, search_color) # Подсвечиваем одну ячейку
                               except curses.error as e:
                                  # Ловим ошибку для отдельного символа
                                  logging.warning(f"Curses error highlighting single char at ({screen_y}, {draw_char_x}): {e}")
                        current_char_screen_x += char_width # Сдвигаем X для следующего символа
                except curses.error as e:
                    logging.error(f"Curses error applying search highlight: {e}")
 


    def _draw_selection(self):
        """Накладывает подсветку выделенного текста."""
        # Проверяем, активно ли выделение и заданы ли его границы
        if not self.editor.is_selecting or not self.editor.selection_start or not self.editor.selection_end:
            return # Нет активного выделения

        # Получаем координаты начала и конца выделения
        start_y, start_x = self.editor.selection_start
        end_y, end_x = self.editor.selection_end

        # Нормализуем координаты, чтобы начало всегда было "раньше" конца
        if start_y > end_y or (start_y == end_y and start_x > end_x):
            start_y, start_x, end_y, end_x = end_y, end_x, start_y, start_x

        # Получаем размеры окна и информацию о номерах строк
        height, width = self.stdscr.getmaxyx()
        line_num_width = len(str(max(1, len(self.editor.text)))) + 1
        text_area_width = max(1, width - line_num_width)

        # Цвет подсветки выделения (используем инверсию по умолчанию)
        selection_color = curses.A_REVERSE # Инвертирование цвета

        # Итерируем по строкам, которые попадают в диапазон выделения
        for y in range(start_y, end_y + 1):
            # Проверяем, находится ли текущая строка в видимой области
            if y < self.editor.scroll_top or y >= self.editor.scroll_top + self.editor.visible_lines:
                continue # Строка не на экране, пропускаем

            screen_y = y - self.editor.scroll_top # Экранная строка

            # Определяем начальный и конечный индекс символа для выделения в текущей строке.
            sel_start_char_idx = start_x if y == start_y else 0
            sel_end_char_idx = end_x if y == end_y else len(self.editor.text[y])

            # Проверка, что выделение в строке вообще существует (start_x < end_x или start_idx < end_idx)
            if sel_start_char_idx >= sel_end_char_idx:
                continue

            line = self.editor.text[y] # Оригинальная строка текста

            # Экранная позиция начала выделения в этой строке
            sel_screen_start_x_before_scroll = self.editor.get_string_width(line[:sel_start_char_idx])
            sel_screen_start_x = line_num_width + sel_screen_start_x_before_scroll - self.editor.scroll_left

            # Экранная позиция конца выделения в этой строке
            sel_screen_end_x_before_scroll = self.editor.get_string_width(line[:sel_end_char_idx])
            sel_screen_end_x = line_num_width + sel_screen_end_x_before_scroll - self.editor.scroll_left

            draw_start_x = max(line_num_width, sel_screen_start_x)
            draw_end_x = min(width, sel_screen_end_x)

            highlight_width_on_screen = max(0, draw_end_x - draw_start_x)

            if highlight_width_on_screen > 0:
                try:

                    self.stdscr.chgat(screen_y, draw_start_x, highlight_width_on_screen, selection_color)

                except curses.error as e:
                    logging.error(f"Curses error applying selection highlight at ({screen_y}, {draw_start_x}) with width {highlight_width_on_screen}: {e}")
 

    def _draw_matching_brackets(self):
        """Вызывает highlight_matching_brackets для отрисовки."""
        self.editor.highlight_matching_brackets()


    def truncate_string(self, s: str, max_width: int) -> str:
        """
        Обрезает строку s так, чтобы её
        визуальная ширина (wcwidth) не превышала max_width.
        """
        result = ""
        curr = 0
        for ch in s:
            w = wcwidth(ch)
            if w < 0:
                w = 1
            if curr + w > max_width:
                break
            result += ch
            curr += w
        return result


    def _draw_status_bar(self) -> None:
        """Рисует статус-бар, избегая ERR от addnstr()."""

        logging.debug("Drawing status bar")
        try:
            h, w = self.stdscr.getmaxyx()
            if h <= 0 or w <= 1:
                return

            y = h - 1
            max_col = w - 1

            # ── цвета ──────────────────────────────────────────────────────────
            c_norm = self.colors.get("status", curses.color_pair(10) | curses.A_BOLD)
            c_err = self.colors.get("status_error", curses.color_pair(11) | curses.A_BOLD)
            c_git = self.colors.get("git_info", curses.color_pair(12))
            c_dirty = self.colors.get("git_dirty", curses.color_pair(13) | curses.A_BOLD)

            self.stdscr.move(y, 0)
            self.stdscr.clrtoeol()
            self.stdscr.bkgdset(" ", c_norm)

            # ── формируем текст блоков ─────────────────────────────────────────
            icon = get_file_icon(self.editor.filename, self.editor.config)
            fname = os.path.basename(self.editor.filename) if self.editor.filename else "No Name"
            lexer_name = self.editor._lexer.name if self.editor._lexer else "plain text"

            left = f" {icon} {fname}{'*' if self.editor.modified else ''} | {lexer_name} | UTF-8 | Ln {self.editor.cursor_y+1}/{len(self.editor.text)}, Col {self.editor.cursor_x+1} | {'INS' if self.editor.insert_mode else 'REP'} "

            g_branch, _, g_commits = self.editor.git_info
            git_txt = f"Git: {g_branch} ({g_commits})" if g_branch else ""
            git_col = c_dirty if "*" in g_branch else c_git

            msg = self.editor.status_message or "Ready"
            msg_col = c_err if msg.startswith("Error") else c_norm

            # ── ширины блоков ───────────────────────────────────────────────────
            gw_left = self.editor.get_string_width(left)
            gw_msg = self.editor.get_string_width(msg)
            gw_git = self.editor.get_string_width(git_txt)

            # ── левый блок ──────────────────────────────────────────────────────
            x = 0
            self.stdscr.addnstr(y, x, left, min(gw_left, max_col - x), c_norm)
            x += gw_left

            # ── git блок справа ─────────────────────────────────────────────────
            if git_txt:
                x_git = max_col - gw_git
                self.stdscr.addnstr(y, x_git, git_txt, gw_git, git_col)
                right_limit = x_git
            else:
                right_limit = max_col

            # ── сообщение по центру (обрезка если нужно) ────────────────────────
            space_for_msg = right_limit - x
            if space_for_msg > 0:
                msg = self.truncate_string(msg, space_for_msg)
                gw_msg = self.editor.get_string_width(msg)
                x_msg = x + (space_for_msg - gw_msg) // 2
                self.stdscr.addnstr(y, x_msg, msg, gw_msg, msg_col)

        except Exception as e:
            logging.error(f"Error in _draw_status_bar: {e}", exc_info=True)
            self.editor._set_status_message("Status bar error (see log)")


    def _position_cursor(self) -> None:
        """Позиционирует курсор на экране, не позволяя ему «улетать» за Git-статус."""
        height, width = self.stdscr.getmaxyx()
        max_row       = height - 2                 # последняя строка текста (height-1 – статус-бар)
        line_num_width = len(str(max(1, len(self.editor.text)))) + 1  # «NN␠»
        text_area_width = max(1, width - line_num_width)

        # --- 1. Корректируем внутренние координаты ---------------------------------
        self.editor.cursor_y = max(0, min(self.editor.cursor_y, len(self.editor.text) - 1))
        current_line         = self.editor.text[self.editor.cursor_y]
        self.editor.cursor_x = max(0, min(self.editor.cursor_x, len(current_line)))

        cursor_line_idx = self.editor.cursor_y
        cursor_char_idx = self.editor.cursor_x

        # --- 2. Вертикальная прокрутка ----------------------------------------------
        screen_y = cursor_line_idx - self.editor.scroll_top
        if screen_y < 0:
            self.editor.scroll_top = cursor_line_idx
            screen_y = 0
        elif screen_y >= self.editor.visible_lines:
            self.editor.scroll_top = min(
                len(self.editor.text) - self.editor.visible_lines,
                cursor_line_idx - self.editor.visible_lines + 1
            )
            self.editor.scroll_top = max(0, self.editor.scroll_top)
            screen_y = self.editor.visible_lines - 1
        screen_y = max(0, min(screen_y, max_row))

        # --- 3. Горизонтальная прокрутка --------------------------------------------
        cursor_px_before_scroll = self.editor.get_string_width(current_line[:cursor_char_idx])
        current_cursor_screen_x = line_num_width + cursor_px_before_scroll - self.editor.scroll_left

        view_start_x = line_num_width
        view_end_x   = width - 1                   # последний допустимый столбец

        if current_cursor_screen_x < view_start_x:
            self.editor.scroll_left = cursor_px_before_scroll
        elif current_cursor_screen_x > view_end_x:
            self.editor.scroll_left = max(0, cursor_px_before_scroll - text_area_width + 1)

        # --- 4. Итоговые экранные координаты ----------------------------------------
        final_cursor_screen_x = (
            line_num_width
            + cursor_px_before_scroll
            - self.editor.scroll_left
        )
        draw_cursor_x = max(view_start_x, min(view_end_x, final_cursor_screen_x))

        # --- 5. Перемещаем курсор ----------------------------------------------------
        try:
            logging.debug(f"Positioning cursor: screen_y={screen_y}, draw_cursor_x={draw_cursor_x}. Logical: ({self.editor.cursor_y}, {self.editor.cursor_x}). Line: '{current_line}'")
            self.stdscr.move(screen_y, draw_cursor_x)
        except curses.error:
            # запасной вариант – ставим в начало строки, если что-то пошло не так
            try:
                self.stdscr.move(screen_y, view_start_x)
            except curses.error:
                pass

    def _adjust_vertical_scroll(self):
        """
        Adjusts vertical scroll (scroll_top) to ensure the cursor is visible.
        Called after window resize or other events that might push cursor off-screen.
        """
        height, width = self.stdscr.getmaxyx()
        text_area_height = max(1, height - 2)

        # Если общее количество строк меньше видимой области, scroll_top всегда 0
        if len(self.editor.text) <= text_area_height:
             self.editor.scroll_top = 0
             return

        # Текущая экранная Y позиция курсора (относительно начала видимой области)
        screen_y = self.editor.cursor_y - self.editor.scroll_top

        # Если курсор выше или ниже видимой области
        if screen_y < 0:
             # Сдвигаем scroll_top так, чтобы строка с курсором стала первой видимой
             self.editor.scroll_top = self.editor.cursor_y
             logging.debug(f"Adjusted vertical scroll: cursor above view. New scroll_top: {self.editor.scroll_top}")
        elif screen_y >= text_area_height:
             # Сдвигаем scroll_top так, чтобы строка с курсором стала последней видимой
             self.editor.scroll_top = self.editor.cursor_y - text_area_height + 1
             logging.debug(f"Adjusted vertical scroll: cursor below view. New scroll_top: {self.editor.scroll_top}")

        # Убеждаемся, что scroll_top не выходит за допустимые пределы (0 до len(text) - visible_lines)
        self.editor.scroll_top = max(0, min(self.editor.scroll_top, len(self.editor.text) - text_area_height))
        logging.debug(f"Final adjusted scroll_top: {self.editor.scroll_top}")


    def _update_display(self):
        """Обновляет физический экран."""
        try:
            # noutrefresh() - подготавливает обновление в памяти
            self.stdscr.noutrefresh()
            # doupdate() - выполняет все подготовленные обновления на физическом экране
            curses.doupdate()
        except curses.error as e:
            logging.error(f"Curses doupdate error: {e}")
            pass # Продолжаем, надеясь, что главный цикл обработает


def main(stdscr):
    """
    Initializes locale, stdout encoding, and handles command-line
    arguments before starting the main editor loop.
    """
    try:
        signal.signal(signal.SIGTSTP, signal.SIG_IGN)  # игнорировать Ctrl+Z
        signal.signal(signal.SIGINT, signal.SIG_IGN)   # Ctrl+C (если надо)
        logging.debug("SIGTSTP and SIGINT ignored")
    except Exception as e:
        logging.warning(f"Couldn't ignore SIGTSTP/SIGINT: {e}")
    
    # Установка локали важна для корректной работы с UTF-8 и wcwidth
    # os.environ["LANG"] = "en_US.UTF-8" # Может быть переопределено системными настройками     
    try:
        # Используем пустую строку для установки локали на основе переменных среды
        locale.setlocale(locale.LC_ALL, "")
        logging.info(f"Locale set to {locale.getlocale()}.")
    except locale.Error as e:
        logging.error(f"Failed to set locale: {e}. Character width calculation may be incorrect.")

    # Явно устанавливаем кодировку stdout для Python 3
    try:
        editor = SwayEditor(stdscr)

        # Обработка аргументов командной строки (открытие файла)
        if len(sys.argv) > 1:
            filename_arg = sys.argv[1]
            logging.info(f"Attempting to open file from command line: {filename_arg}")
            # open_file сам обрабатывает ошибки и устанавливает статус
            editor.open_file(filename_arg)
        else:
             logging.info("No file specified on command line. Starting with new buffer.")
             # Если файл не указан, начинаем с пустого буфера.
             # New file state is set in __init__.

        # Запуск главного цикла редактора
        logging.debug("Starting editor run() loop.")
        editor.run() # Главный цикл

    except Exception as e:
        logging.exception("Unhandled exception in main function after editor setup.")
        print("\nAn unexpected error occurred.", file=sys.stderr)
        print("See editor.log for details.", file=sys.stderr)
        # Попытка сохранить трассировку стека в отдельный файл, если логгирование упало или для удобства
        error_log_path = os.path.join(os.path.dirname(__file__), "critical_error.log")
        try:
            with open(error_log_path, "a", encoding="utf-8", errors="replace") as error_file:
                error_file.write(f"\n{'='*20} {time.asctime()} {'='*20}\n")
                traceback.print_exc(file=error_file)
                error_file.write(f"{'='*50}\n")
            print(f"Detailed error logged to {error_log_path}", file=sys.stderr)
        except Exception as log_e:
            print(f"Failed to write detailed error log: {log_e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr) # Печатаем в stderr как fallback

        sys.exit(1) # Выход с кодом ошибки

# Главная точка входа в скрипт
if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGTSTP, signal.SIG_IGN)

    # Загружаем конфиг перед запуском curses, т.к. логгирование может зависеть от конфига
    try:
        config = load_config()
        logging.info("Configuration loaded successfully.")
    except Exception as e:
        print(f"Failed to load config: {e}", file=sys.stderr)
        config = {
            "editor": {"use_system_clipboard": True, "tab_size": 4, "use_spaces": True},
            "keybindings": {},
            "git": {"enabled": True},
            "settings": {"auto_save_interval": 5, "show_git_info": True},
            "file_icons": {"text": "📝"},
            "supported_formats": {}
        }
        logging.error("Using fallback minimal config.")

    # Запускаем curses обёртку, которая вызовет main
    try:
        curses.wrapper(main)
    except Exception as e:
        logging.critical("Unhandled exception caught outside curses.wrapper.", exc_info=True)
        print("\nAn unhandled critical error occurred.", file=sys.stderr)
        print("See editor.log for details.", file=sys.stderr)
        error_log_path = os.path.join(os.path.dirname(__file__), "critical_error.log")
        try:
            with open(error_log_path, "a", encoding="utf-8", errors="replace") as error_file:
                error_file.write(f"\n{'='*20} {time.asctime()} {'='*20}\n")
                error_file.write("Error caught outside curses.wrapper:\n")
                traceback.print_exc(file=error_file)
                error_file.write(f"{'='*50}\n")
            print(f"Detailed error logged to {error_log_path}", file=sys.stderr)
        except Exception as log_e:
            print(f"Failed to write detailed error log: {log_e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

        sys.exit(1)
