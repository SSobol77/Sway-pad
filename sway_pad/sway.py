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

from pygments import lex
from pygments.lexers import get_lexer_by_name, guess_lexer, TextLexer
from pygments.token import Token
from wcwidth import wcwidth, wcswidth 


# Установка кодировки по умолчанию
def _set_default_encoding():
    """Устанавливает кодировку по умолчанию для Python 3.8 и выше."""
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONLEGACYWINDOWSSTDIO", "1")

_set_default_encoding()

# Функция для проверки кода с помощью Flake8
def run_flake8_on_code(code_string, filename="<buffer>"):
    """
    Запускает Flake8 на code_string через subprocess, возвращает список строк с сообщениями.
    """
    # Ограничение для больших файлов
    if len(code_string) > 100_000:
        return ["File is too large for flake8 analysis"]

    # Создаём временный файл с .py
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as tmp:
        tmp_name = tmp.name
        tmp.write(code_string)

    try:
        # Запускаем flake8 как отдельный процесс.
        # --max-line-length=88 здесь как пример;
        # добавьте нужные вам опции или используйте конфиг .flake8
        cmd = ["flake8", "--max-line-length=88", tmp_name]

        process = subprocess.run(
            cmd,
            capture_output=True,  # Захватываем stdout/err, чтобы не ломать curses
            text=True
        )
        
        # Парсим stdout
        # Если файл без ошибок, flake8 возвращает пустую строку
        # (или даже код возврата 0 или 1, в зависимости от warning)
        output = process.stdout.strip()
        if not output:
            return ["Flake8: No issues found."]
        
        # Разбиваем на строки
        return output.split("\n")
    finally:
        # Удаляем временный файл
        os.remove(tmp_name)

# Функция для рекурсивного объединения словарей
def deep_merge(base: dict, override: dict) -> dict:
    """
    Recursively merges 'override' dict into 'base' dict, returning a new dict.
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
def safe_run(cmd: list[str]) -> subprocess.CompletedProcess:
    """
    Обёртка над subprocess.run без shell=True, с захватом вывода.
    Не возбуждает исключения при ненулевом коде возврата (check=False).
    """
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


# Функция для получения иконки файла
def get_file_icon(filename: str, config: dict) -> str:
    """
    Returns the icon for a file based on its extension.
    """
    file_lower = filename.lower()
    if "file_icons" not in config or "supported_formats" not in config:
        return "📝"  # Fallback для отсутствующей конфигурации

    for key, icon in config["file_icons"].items():
        extensions = config["supported_formats"].get(key, [])
        if file_lower.endswith(tuple(ext.lower() for ext in extensions)):
            return icon

    return config["file_icons"].get("text", "📝")


# Функция для получения информации о Git (ветка, имя пользователя, количество коммитов)
def get_git_info(file_path: str) -> tuple[str, str, str]:
    repo_dir = os.path.dirname(os.path.abspath(file_path)) if file_path else os.getcwd()
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        logging.debug(f"No .git directory found in {repo_dir}")
        return "", "", "0"

    # 1. Определяем ветку
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, check=True, cwd=repo_dir
        ).stdout.strip()
    except FileNotFoundError:
        logging.warning("Git executable not found")
        return "", "", "0"
    except subprocess.CalledProcessError:
        # fallback: git symbolic-ref
        try:
            branch = subprocess.run(
                ["git", "symbolic-ref", "--short", "HEAD"],
                capture_output=True, text=True, check=True, cwd=repo_dir
            ).stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            branch = "main"

    # 2. Грязный репозиторий ?
    try:
        dirty = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, check=True, cwd=repo_dir
        ).stdout.strip()
        if dirty:
            branch += "*"
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logging.warning(f"Git status failed: {e}")

    # 3. Имя пользователя
    try:
        user_name = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True, text=True, check=True, cwd=repo_dir
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        user_name = ""

    # 4. Кол-во коммитов
    try:
        commits = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            capture_output=True, text=True, check=True, cwd=repo_dir
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        commits = "0"

    return branch, user_name, commits


# Загрузка конфигурации
def load_config() -> dict:
    """
    Loads configuration from 'config.toml', falling back to minimal defaults if not found or invalid.
    """
    minimal_default = {
        "colors": {
            "error": "red",
            "status": "bright_white",
            "green": "green"  # Для Git-информации
        },
        "fonts": {"font_family": "monospace", "font_size": 12},
        "keybindings": {
            "delete": "del",
            "paste": "ctrl+v",
            "copy": "ctrl+c",
            "cut": "ctrl+x",
            "undo": "ctrl+z",
            "open_file": "ctrl+o",
            "save_file": "ctrl+s",
            "select_all": "ctrl+a",
            "quit": "ctrl+q",
            "redo": "ctrl+shift+z",
        },
        "editor": {
            "use_system_clipboard": True,
            "default_new_filename": "new_file.py"          
        },
        "file_icons": {
            "python": "🐍",
            "javascript": "📜",
            "text": "📝",
            "html": "🌐",
            "css": "🎨"
        },
        "supported_formats": {
            "python": ["py", "pyw"],
            "javascript": ["js", "mjs", "cjs", "jsx"],
            "text": ["txt"],
            "html": ["html", "htm"],
            "css": ["css"]
        }
    }

    try:
        with open("config.toml", "r", encoding="utf-8") as f:
            file_content = f.read()
            user_config = toml.loads(file_content)
            final_config = deep_merge(minimal_default, user_config)
            return final_config
    except FileNotFoundError:
        logging.warning("Config file 'config.toml' not found. Using minimal defaults.")
        return minimal_default
    except toml.TomlDecodeError as e:
        logging.error(f"TOML parse error: {str(e)}")
        logging.error("Falling back to minimal defaults.")
        return minimal_default

# Обработка ошибок:
logging.basicConfig(
    filename="editor.log",
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)",
    force=True,
)

logger = logging.getLogger(__name__)

# Логгер для событий клавиатуры
KEY_LOGGER = logging.getLogger("sway2.keyevents")
KEY_LOGGER.propagate = False
KEY_LOGGER.setLevel(logging.DEBUG)
KEY_LOGGER.addHandler(logging.NullHandler())

if os.environ.get("SWAY2_KEYTRACE", "").lower() in {"1", "true", "yes"}:
    from logging.handlers import RotatingFileHandler
    handler = RotatingFileHandler("keytrace.log", maxBytes=1_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    KEY_LOGGER.addHandler(handler)
    KEY_LOGGER.propagate = True
else:
    KEY_LOGGER.disabled = True


class SwayEditor:
    """Main class for the Sway editor."""
    def __init__(self, stdscr):
        # ── базовая инициализация curses ───────────────────────────
        self.stdscr = stdscr
        self.stdscr.keypad(True)
        curses.raw(); curses.nonl(); curses.noecho()


        # ── внутренние поля, которые НУЖНЫ ДАЛЬШЕ ──────────────────
        self.insert_mode = True
        self.status_message = ""
        self._msg_q = queue.Queue() 
        self.action_history, self.undone_actions = [], []

        # Очередь для результатов команд оболочки
        self._shell_cmd_q = queue.Queue()
        # Очередь для Git-информации
        self._git_q = queue.Queue() 

        # ── поля для поиска ───────────────────────────────
        self.search_term = ""           # Текущий запрос поиска
        self.search_matches = []        # Список всех совпадений: [(row, col_start_idx, col_end_idx), ...]
        self.current_match_idx = -1     # Индекс текущего совпадения для F3/перехода
        self.highlighted_matches = []   # Список совпадений для ПОДСВЕТКИ на экране

        # ── прочие поля/заглушки ───────────────────────────────────
        self.config   = load_config()
        self.filename = ""  
        self.text     = [""]
        self.cursor_x = self.cursor_y = 0
        self.scroll_top = self.scroll_left = 0
        self.modified = False
        self.encoding = "UTF-8"
        self.selection_start = self.selection_end = None
        self.is_selecting = False
        self.git_info = None
        self._lexer, self._token_cache = None, {}
        self.visible_lines, self.last_window_size = 0, (0, 0)
        self.colors = {} # Инициализируем словарь цветов
        
        # ── системные вызовы ───────────────────────────────────────
        self.stdscr.nodelay(False)
        locale.setlocale(locale.LC_ALL, "")
        curses.start_color(); curses.use_default_colors(); curses.curs_set(1)

        # ── clipboard ──────────────────────────────────────────────
        self.use_system_clipboard = self.config.get("editor", {}).get("use_system_clipboard", True)
        self.pyclip_available = self._check_pyclip_availability()

        # ── keybindings (в одном месте!) ───────────────────────────
        self.keybindings = {
            # стандартные
            "delete":      self.parse_key(self.config["keybindings"].get("delete", "del")),
            "paste":       self.parse_key(self.config["keybindings"].get("paste",  "ctrl+v")),
            "copy":        self.parse_key(self.config["keybindings"].get("copy",   "ctrl+c")),
            "cut":         self.parse_key(self.config["keybindings"].get("cut",    "ctrl+x")),
            "undo":        self.parse_key(self.config["keybindings"].get("undo",   "ctrl+z")),
            "new_file":    self.parse_key(self.config["keybindings"].get("new_file", "f4")),
            "open_file":   self.parse_key(self.config["keybindings"].get("open_file", "ctrl+o")),
            "save_file":   self.parse_key(self.config["keybindings"].get("save_file", "ctrl+s")),
            "save_as": self.parse_key(self.config["keybindings"].get("save_as", "f5")),
            "select_all":  self.parse_key(self.config["keybindings"].get("select_all","ctrl+a")),
            "quit":        self.parse_key(self.config["keybindings"].get("quit",   "ctrl+q")),
            "redo":        self.parse_key(self.config["keybindings"].get("redo",   "ctrl+shift+z")),
            "goto_line": self.parse_key(self.config["keybindings"].get("goto_line", "ctrl+g")),
            # курсор / выделение
            "extend_selection_right": curses.KEY_SRIGHT,
            "extend_selection_left":  curses.KEY_SLEFT,
            "select_to_home":         curses.KEY_SHOME,
            "select_to_end":          curses.KEY_SEND,
            "extend_selection_up":    curses.KEY_SR,
            "extend_selection_down":  curses.KEY_SF,
            # Git-меню
            "git_menu": self.parse_key(self.config["keybindings"].get("git_menu", "f2")),
            # ★ Esc-отмена
            "cancel_operation": self.parse_key(self.config["keybindings"].get("cancel_operation", "esc")),
            # Поиск
            "find":       self.parse_key(self.config["keybindings"].get("find", "ctrl+f")),
            "find_next":  self.parse_key(self.config["keybindings"].get("find_next", "f3")),
            "help": self.parse_key(self.config["keybindings"].get("help", "f1")),
        }

        # ── action_map ─────────────────────────────────────────────
        self.action_map = {
            "copy": self.copy, "paste": self.paste, "cut": self.cut,
            "undo": self.undo, "redo": self.redo, "select_all": self.select_all,
            "extend_selection_right": self.extend_selection_right,
            "extend_selection_left":  self.extend_selection_left,
            "select_to_home": self.select_to_home, "select_to_end": self.select_to_end,
            "extend_selection_up": self.extend_selection_up,
            "extend_selection_down": self.extend_selection_down,
            "new_file": self.new_file,
            "open_file": lambda: print("Open file"),
            "save_file": lambda: print("Save file"),
            "save_as": self.save_file_as,
            "delete": lambda: print("Delete"),
            "quit":   lambda: print("Quit"),
            "git_menu": self.integrate_git,
            # ★ Esc-отмена
            "cancel_operation": self.cancel_operation,
            "help": self.show_help,
            "goto_line": self.goto_line,
        }

        # ── финальные инициализации ────────────────────────────────
        self.init_colors()
        #self.load_syntax_highlighting()
        self.set_initial_cursor_position()
        
        self.update_git_info()
        

    def _check_pyclip_availability(self):
        """Проверяет доступность pyperclip и системных утилит для буфера обмена."""
        try:
            pyperclip.copy("")
            pyperclip.paste()
            return True
        except pyperclip.PyperclipException as e:
            logging.warning(f"System clipboard unavailable: {str(e)}")
            return False


    def get_selected_text(self):
        if not self.is_selecting or self.selection_start is None or self.selection_end is None:
            return ""
        start_row, start_col = self.selection_start
        end_row, end_col = self.selection_end
        
        # Нормализация: начало должно быть раньше конца
        if start_row > end_row or (start_row == end_row and start_col > end_col):
            start_row, start_col, end_row, end_col = end_row, end_col, start_row, start_col
        
        selected_lines = []
        if start_row == end_row:
            line = self.text[start_row]
            selected_lines.append(line[start_col:end_col])
        else:
            # Первая строка
            selected_lines.append(self.text[start_row][start_col:])
            # Средние строки
            for row in range(start_row + 1, end_row):
                selected_lines.append(self.text[row])
            # Последняя строка
            selected_lines.append(self.text[end_row][:end_col])
        
        return "\n".join(selected_lines)


    def delete_selected_text(self):
        if not self.is_selecting or self.selection_start is None or self.selection_end is None:
            return
        start_row, start_col = self.selection_start
        end_row, end_col = self.selection_end
        
        # Нормализация
        if start_row > end_row or (start_row == end_row and start_col > end_col):
            start_row, start_col, end_row, end_col = end_row, end_col, start_row, start_col
        
        # Сохраняем удалённый текст для undo
        deleted_text = self.get_selected_text()
        self.action_history.append({
            "type": "delete",
            "text": deleted_text,
            "start": start_row,
            "start_col": start_col,
            "end": end_row,
            "end_col": end_col
        })
        
        if start_row == end_row:
            self.text[start_row] = self.text[start_row][:start_col] + self.text[start_row][end_col:]
            self.cursor_x = start_col
        else:
            # Объединяем первую и последнюю строки
            self.text[start_row] = self.text[start_row][:start_col] + self.text[end_row][end_col:]
            # Удаляем промежуточные строки
            del self.text[start_row + 1:end_row + 1]
            self.cursor_y = start_row
            self.cursor_x = start_col
        
        self.selection_start = None
        self.selection_end = None
        self.is_selecting = False
        self.modified = True
        self.undone_actions.clear()


    def copy(self):
        """Копирует выделенный текст в буфер обмена."""
        selected_text = self.get_selected_text()
        if not selected_text:
            self.status_message = "Nothing to copy"
            return
        self.internal_clipboard = selected_text
        if self.use_system_clipboard and self.pyclip_available:
            try:
                pyperclip.copy(selected_text)
                self.status_message = "Copied to system clipboard"
            except pyperclip.PyperclipException as e:
                logging.error(f"Failed to copy to system clipboard: {str(e)}")
                self.status_message = "Copied to internal clipboard (system clipboard error)"
        else:
            self.status_message = "Copied to internal clipboard"


    def cut(self):
        """Вырезает выделенный текст в буфер обмена."""
        selected_text = self.get_selected_text()
        if not selected_text:
            self.status_message = "Nothing to cut"
            return
        self.internal_clipboard = selected_text
        if self.use_system_clipboard and self.pyclip_available:
            try:
                pyperclip.copy(selected_text)
                self.status_message = "Cut to system clipboard"
            except pyperclip.PyperclipException as e:
                logging.error(f"Failed to cut to system clipboard: {str(e)}")
                self.status_message = "Cut to internal clipboard (system clipboard error)"
        self.delete_selected_text()


    def paste(self):
        """Вставляет текст из буфера обмена."""
        if self.use_system_clipboard and self.pyclip_available:
            try:
                text = pyperclip.paste()
                if not text:
                    text = self.internal_clipboard
                    self.status_message = "Pasted from internal clipboard (system clipboard empty)"
                else:
                    self.status_message = "Pasted from system clipboard"
            except pyperclip.PyperclipException as e:
                logging.error(f"Failed to paste from system clipboard: {str(e)}")
                text = self.internal_clipboard
                self.status_message = "Pasted from internal clipboard (system clipboard error)"
        else:
            text = self.internal_clipboard
            self.status_message = "Pasted from internal clipboard"

        if not text:
            self.status_message = "Clipboard is empty"
            return

        if self.is_selecting:
            self.delete_selected_text()
        self.insert_text(text)


    def extend_selection_right(self):
        if not self.is_selecting:
            self.selection_start = (self.cursor_y, self.cursor_x)
            self.is_selecting = True
        if self.cursor_x < len(self.text[self.cursor_y]):
            self.cursor_x += 1
        self.selection_end = (self.cursor_y, self.cursor_x)

    def extend_selection_left(self):
        if not self.is_selecting:
            self.selection_start = (self.cursor_y, self.cursor_x)
            self.is_selecting = True
        if self.cursor_x > 0:
            self.cursor_x -= 1
        self.selection_end = (self.cursor_y, self.cursor_x)


    def select_to_home(self):
        if not self.is_selecting:
            self.selection_start = (self.cursor_y, self.cursor_x)
            self.is_selecting = True
        self.cursor_x = 0
        self.selection_end = (self.cursor_y, self.cursor_x)

    def select_to_end(self):
        if not self.is_selecting:
            self.selection_start = (self.cursor_y, self.cursor_x)
            self.is_selecting = True
        self.cursor_x = len(self.text[self.cursor_y])
        self.selection_end = (self.cursor_y, self.cursor_x)

    def select_all(self):
        self.selection_start = (0, 0)
        self.selection_end = (len(self.text) - 1, len(self.text[-1]))
        self.is_selecting = True


    def undo(self):
        if not self.action_history:
            self.status_message = "Nothing to undo"
            return
        last_action = self.action_history.pop()
        
        if last_action["type"] == "insert":
            text = last_action["text"]
            row, col = last_action["position"]
            lines = text.split('\n')
            end_row = row + len(lines) - 1
            end_col = col + len(lines[-1]) if len(lines) == 1 else len(lines[-1])
            self.delete_text(row, col, end_row, end_col)
            self.cursor_y = row
            self.cursor_x = col
        elif last_action["type"] == "delete":
            text = last_action["text"]
            start_row = last_action["start"]
            start_col = last_action["start_col"]
            self.insert_text_at_position(text, start_row, start_col)
            # Восстанавливаем курсор в конец вставленного текста
            lines = text.split('\n')
            self.cursor_y = start_row + len(lines) - 1
            self.cursor_x = start_col + len(lines[-1]) if len(lines) == 1 else len(lines[-1])
        
        self.undone_actions.append(last_action)
        self.modified = True
        self.is_selecting = False
        self.selection_start = None
        self.selection_end = None
        self.status_message = "Undo performed"


    def extend_selection_up(self):
        """Расширяет выделение вверх на одну строку."""
        if not self.is_selecting:
            self.selection_start = (self.cursor_y, self.cursor_x)
            self.is_selecting = True
        if self.cursor_y > 0:
            self.cursor_y -= 1
            self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))
        self.selection_end = (self.cursor_y, self.cursor_x)

    def extend_selection_down(self):
        """Расширяет выделение вниз на одну строку."""
        if not self.is_selecting:
            self.selection_start = (self.cursor_y, self.cursor_x)
            self.is_selecting = True
        if self.cursor_y < len(self.text) - 1:
            self.cursor_y += 1
            self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))
        self.selection_end = (self.cursor_y, self.cursor_x)

    
    def redo(self):
        if not self.undone_actions:
            self.status_message = "Nothing to redo"
            return
        last_undone = self.undone_actions.pop()
        
        if last_undone["type"] == "insert":
            text = last_undone["text"]
            row, col = last_undone["position"]
            self.insert_text_at_position(text, row, col)
        elif last_undone["type"] == "delete":
            start_row = last_undone["start"]
            start_col = last_undone["start_col"]
            end_row = last_undone["end"]
            end_col = last_undone["end_col"]
            self.delete_text(start_row, start_col, end_row, end_col)
        
        self.action_history.append(last_undone)
        self.modified = True
        self.status_message = "Redo performed"
    

    def insert_text_at_position(self, text, row, col):
        lines = text.split('\n')
        if row < len(self.text):
            self.text[row] = self.text[row][:col] + lines[0] + self.text[row][col:]
        else:
            while len(self.text) <= row:
                self.text.append("")
            self.text[row] = lines[0]
        for i in range(1, len(lines)):
            new_row = row + i
            if new_row < len(self.text):
                self.text[new_row] = lines[i]
            else:
                self.text.append(lines[i])
        if lines:
            last_row = row + len(lines) - 1
            if len(lines) == 1:
                self.cursor_y = row
                self.cursor_x = col + len(lines[0])
            else:
                self.cursor_y = last_row
                self.cursor_x = len(lines[-1])
        else:
            self.cursor_y = row
            self.cursor_x = col


    def delete_text(self, start_row, start_col, end_row, end_col):
        if start_row == end_row:
            line = self.text[start_row]
            self.text[start_row] = line[:start_col] + line[end_col:]
        else:
            self.text[start_row] = self.text[start_row][:start_col] + self.text[end_row][end_col:]
            del self.text[start_row + 1:end_row + 1]
        self.modified = True


    def insert_text(self, text):
        if self.is_selecting:
            self.delete_selected_text()
        lines = text.split('\n')
        current_line = self.text[self.cursor_y]
        start_x = self.cursor_x  # Сохраняем начальную позицию
        start_y = self.cursor_y
        self.text[self.cursor_y] = current_line[:self.cursor_x] + lines[0] + current_line[self.cursor_x:]
        self.cursor_x += len(lines[0])
        for i in range(1, len(lines)):
            self.cursor_y += 1
            self.text.insert(self.cursor_y, lines[i])
            self.cursor_x = len(lines[i])
        self.modified = True
        self.action_history.append({
            "type": "insert",
            "text": text,
            "position": (start_y, start_x)  # Используем начальную позицию
        })
        self.status_message = "Text inserted"    


    def apply_syntax_highlighting_with_pygments(self, line):
        """
        Uses Pygments for automatic language detection and tokenization.
        The resulting tokens are mapped to curses color pairs for syntax highlighting.
        """
        if self._lexer is None:
            try:
                if self.filename and self.filename != "noname":
                    self._lexer = get_lexer_by_name(self.detect_language())
                else:
                    self._lexer = guess_lexer(line)
            except Exception:
                self._lexer = TextLexer()

        # Кэширование результата
        line_hash = hash(line)
        cache_key = (line_hash, id(self._lexer))
        if cache_key in self._token_cache:
            return self._token_cache[cache_key]

        tokens = list(lex(line, self._lexer))

        token_color_map = {
            Token.Keyword: curses.color_pair(2),
            Token.Keyword.Constant: curses.color_pair(2),
            Token.Keyword.Declaration: curses.color_pair(2),
            Token.Keyword.Namespace: curses.color_pair(2),
            Token.Keyword.Pseudo: curses.color_pair(2),
            Token.Keyword.Reserved: curses.color_pair(2),
            Token.Keyword.Type: curses.color_pair(2),
            Token.Name.Builtin: curses.color_pair(2),
            Token.Name.Function: curses.color_pair(3),
            Token.Name.Class: curses.color_pair(3),
            Token.Name.Decorator: curses.color_pair(5),
            Token.Name.Exception: curses.color_pair(4),
            Token.Name.Variable: curses.color_pair(7),
            Token.Name.Namespace: curses.color_pair(2),
            Token.Name.Attribute: curses.color_pair(7),
            Token.Name.Tag: curses.color_pair(5),
            Token.Literal.String: curses.color_pair(3),
            Token.Literal.String.Doc: curses.color_pair(3),
            Token.Literal.String.Interpol: curses.color_pair(3),
            Token.Literal.String.Escape: curses.color_pair(3),
            Token.Literal.String.Backtick: curses.color_pair(3),
            Token.Literal.String.Delimiter: curses.color_pair(3),
            Token.Literal.Number: curses.color_pair(4),
            Token.Literal.Number.Float: curses.color_pair(4),
            Token.Literal.Number.Hex: curses.color_pair(4),
            Token.Literal.Number.Integer: curses.color_pair(4),
            Token.Literal.Number.Oct: curses.color_pair(4),
            Token.Comment: curses.color_pair(1),
            Token.Comment.Multiline: curses.color_pair(1),
            Token.Comment.Preproc: curses.color_pair(1),
            Token.Comment.Special: curses.color_pair(1),
            Token.Operator: curses.color_pair(6),
            Token.Operator.Word: curses.color_pair(6),
            Token.Punctuation: curses.color_pair(6),
            Token.Text: curses.color_pair(0),
            Token.Text.Whitespace: curses.color_pair(0),
            Token.Error: curses.color_pair(8),
            Token.Generic.Heading: curses.color_pair(5) | curses.A_BOLD,
            Token.Generic.Subheading: curses.color_pair(5),
            Token.Generic.Deleted: curses.color_pair(8),
            Token.Generic.Inserted: curses.color_pair(4),
            Token.Generic.Emph: curses.color_pair(3) | curses.A_BOLD,
            Token.Generic.Strong: curses.color_pair(2) | curses.A_BOLD,
            Token.Generic.Prompt: curses.color_pair(7),
        }
        default_color = curses.color_pair(0)
        highlighted = []

        for token, text_val in tokens:
            color = default_color
            for token_type, curses_color in token_color_map.items():
                if token in token_type:
                    color = curses_color
                    break
            highlighted.append((text_val, color))

        self._token_cache[cache_key] = highlighted
        # Ограничение размера кэша (например, 1000 строк)
        if len(self._token_cache) > 1000:
            self._token_cache.pop(next(iter(self._token_cache)))
        
        return highlighted


    def run_lint_async(self, code):
        """
        Запускает Flake8 в отдельном потоке и отправляет краткий результат
        в очередь self._msg_q, чтобы не обращаться к curses из другого потока.
        """
        lint_results = run_flake8_on_code(code, self.filename)

        # Формируем сообщение для статус-бара
        if (not lint_results or
            (len(lint_results) == 1 and
            lint_results[0].startswith("Flake8: No issues"))):
            message = f"No issues found in {self.filename}"
        else:
            # берём первые две строки отчёта
            message = " | ".join(lint_results[:2])

        # Кладём его в потокобезопасную очередь;
        # главный цикл прочитает и покажет.
        self._msg_q.put(message)




    def set_initial_cursor_position(self):
        """Sets the initial cursor position and scrolling offsets."""
        self.cursor_x = 0
        self.cursor_y = 0
        self.scroll_top = 0
        self.scroll_left = 0


    def init_colors(self):
        """Initializes curses color pairs for syntax highlighting and search."""
        bg_color = -1 # Используем фон терминала по умолчанию
        curses.start_color()
        curses.use_default_colors()

        # Основные цвета для синтаксиса (примеры)
        curses.init_pair(1, curses.COLOR_BLUE, bg_color)    # Comment
        curses.init_pair(2, curses.COLOR_GREEN, bg_color)   # Keyword
        curses.init_pair(3, curses.COLOR_MAGENTA, bg_color) # String
        curses.init_pair(4, curses.COLOR_YELLOW, bg_color)  # Literal / Type
        curses.init_pair(5, curses.COLOR_CYAN, bg_color)    # Decorator / Tag
        curses.init_pair(6, curses.COLOR_WHITE, bg_color)   # Operator / Punctuation / Variable
        curses.init_pair(7, curses.COLOR_YELLOW, bg_color)  # Line number / Builtins
        curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_RED) # Error background

        # *** цвет для подсветки поиска ***
        # Черный текст на светло-желтом фоне
        curses.init_pair(9, curses.COLOR_BLACK, curses.COLOR_YELLOW) # Search Highlight

        # Обновляем словарь self.colors
        self.colors = {
            "error": curses.color_pair(8) | curses.A_BOLD, # делаем ошибку жирной
            "line_number": curses.color_pair(7),
            "status": curses.color_pair(6) | curses.A_BOLD, # Статус жирным
            "comment": curses.color_pair(1),
            "keyword": curses.color_pair(2),
            "string": curses.color_pair(3),
            "variable": curses.color_pair(6),
            "punctuation": curses.color_pair(6),
            "literal": curses.color_pair(4),
            "decorator": curses.color_pair(5),
            "type": curses.color_pair(4),
            "tag": curses.color_pair(5),
            "attribute": curses.color_pair(3),
            "builtins": curses.color_pair(7),
            "escape": curses.color_pair(5),
            "magic": curses.color_pair(3),
            "exception": curses.color_pair(8),
            "function": curses.color_pair(2),
            "class": curses.color_pair(4),
            "number": curses.color_pair(3),
            "operator": curses.color_pair(6),
            "green": curses.color_pair(2), # Для Git
            # *** Добавляем цвет поиска в словарь ***
            "search_highlight": curses.color_pair(9),
        }

    def apply_syntax_highlighting(self, line, lang):
        """
        Applies syntax highlighting using Pygments. Language is auto-detected by filename or content.
        """
        return self.apply_syntax_highlighting_with_pygments(line)

    def load_syntax_highlighting(self):
        """Loads and compiles syntax highlighting rules from the configuration."""
        self.syntax_highlighting = {}
        try:
            syntax_cfg = self.config.get("syntax_highlighting", {})
            for lang, rules in syntax_cfg.items():
                patterns = rules.get("patterns", [])
                for rule in patterns:
                    try:
                        compiled = re.compile(rule["pattern"])
                        color_pair = self.colors.get(
                            rule["color"], curses.color_pair(0)
                        )
                        self.syntax_highlighting.setdefault(lang, []).append(
                            (compiled, color_pair)
                        )
                    except Exception as e:
                        logging.exception(
                            f"Error in syntax highlighting rule for {lang}: {rule}"
                        )
        except Exception as e:
            logging.exception("Error loading syntax highlighting")


    def draw_screen(self):
        """
        Renders the editor screen, including text lines, line numbers, status bar, cursor,
        selection, and search result highlighting.
        Uses wcwidth for correct character width calculation.
        """
        # --- Начало отрисовки (как в предыдущей версии) ---
        self.stdscr.erase()
        height, width = self.stdscr.getmaxyx()

        if self.last_window_size != (height, width):
            self.visible_lines = height - 2
            self.last_window_size = (height, width)

        if height < 5 or width < 20:
            # Если окно слишком маленькое, показываем сообщение об ошибке
            try:
                error_color = self.colors.get("error", curses.A_NORMAL)
                self.stdscr.addstr(0, 0, "Window too small", error_color)
                self.stdscr.noutrefresh()
                curses.doupdate()
                return
            except curses.error as e:
                logging.error(f"Curses error during small window display: {e}")
                return

        max_line_num_digits = len(str(len(self.text)))
        line_num_width = max_line_num_digits + 1
        text_width = width - line_num_width

        # --- Вертикальная прокрутка (как раньше) ---
        if self.cursor_y < self.scroll_top:
            self.scroll_top = self.cursor_y
        elif self.cursor_y >= self.scroll_top + self.visible_lines:
            self.scroll_top = self.cursor_y - self.visible_lines + 1

        # --- Отрисовка строк текста с синтаксисом (как раньше) ---
        for screen_row in range(self.visible_lines):
            text_line_index = self.scroll_top + screen_row
            if text_line_index >= len(self.text): break

            # Номер строки
            line_num_str = f"{text_line_index + 1:<{max_line_num_digits}} "
            try: self.stdscr.addstr(screen_row, 0, line_num_str, self.colors["line_number"])
            except curses.error as e:
                logging.error(f"Curses error drawing line number at ({screen_row}, 0): {e}")
                pass # Возможно, просто пропустить отрисовку номера строки при ошибке

            # Текст строки с подсветкой синтаксиса
            line = self.text[text_line_index]
            # Убедитесь, что apply_syntax_highlighting существует и возвращает [(text, color), ...]
            syntax_line = self.apply_syntax_highlighting(line, self.detect_language())

            current_screen_x = line_num_width
            char_col_index = 0

            for text_part, color in syntax_line:
                if current_screen_x >= width: break
                part_buffer = ""
                accumulated_width = 0
                for char in text_part:
                    char_width = self.get_char_width(char)
                    if char_col_index < self.scroll_left:
                        char_col_index += 1
                        continue
                    if current_screen_x + char_width > width:
                        if part_buffer:
                            try: self.stdscr.addstr(screen_row, current_screen_x - accumulated_width, part_buffer, color)
                            except curses.error as e: 
                                logging.error(f"Curses error drawing status bar: {e}") 
                                pass
                        part_buffer = ""
                        current_screen_x = width
                        break
                    part_buffer += char
                    accumulated_width += char_width
                    char_col_index += 1
                if part_buffer:
                    try:
                        draw_pos_x = current_screen_x
                        self.stdscr.addstr(screen_row, draw_pos_x, part_buffer, color)
                        current_screen_x += accumulated_width
                    except curses.error as e:
                        logging.error(f"Curses error drawing text part at ({screen_row}, {draw_pos_x}): {e}")
                        current_screen_x = width # Пропускаем эту часть строки при ошибке
                                    
                if current_screen_x >= width: break

        # --- Отрисовка подсветки поиска ---
        if self.highlighted_matches:
            search_highlight_color = self.colors.get("search_highlight", curses.A_REVERSE) # Получаем цвет

            for match_row, match_start_idx, match_end_idx in self.highlighted_matches:
                # Проверяем, видима ли строка совпадения на экране
                if match_row < self.scroll_top or match_row >= self.scroll_top + self.visible_lines:
                    continue

                screen_y = match_row - self.scroll_top # Экранная строка Y
                line = self.text[match_row]

                # Рассчитываем начальную позицию X и ширину подсветки на ЭКРАНЕ
                highlight_screen_start_x = -1
                highlight_screen_width = 0
                current_screen_x = line_num_width # Начинаем с позиции после номера строки

                for char_idx, char in enumerate(line):
                    char_width = self.get_char_width(char)

                    # Пропускаем символы до scroll_left
                    if char_idx < self.scroll_left:
                        current_screen_x += char_width # Нужно для правильного расчета стартовой позиции видимой части
                        continue

                    # Мы в видимой части. Проверяем, входит ли символ в совпадение
                    is_highlighted = match_start_idx <= char_idx < match_end_idx

                    # Проверяем, помещается ли символ на экране
                    effective_screen_x = current_screen_x
                    # Корректируем позицию на экране для символов после scroll_left
                    if char_idx >= self.scroll_left:
                         scroll_w = self.get_string_width(line[self.scroll_left:char_idx])
                         effective_screen_x = line_num_width + scroll_w


                    if effective_screen_x >= width: # Используем effective_screen_x для проверки границы
                        break # Достигли правого края

                    if is_highlighted:
                        if highlight_screen_start_x == -1:
                            # Начало видимой части подсветки
                            highlight_screen_start_x = effective_screen_x
                        highlight_screen_width += char_width

                    # Обновляем позицию на экране (не нужно, т.к. считаем effective_screen_x заново)
                    # current_screen_x += char_width # Убрано, считаем effective_screen_x

                # Применяем подсветку через chgat, если она видима
                if highlight_screen_start_x != -1 and highlight_screen_width > 0:
                    # Убедимся, что ширина не выходит за пределы экрана
                    actual_width = min(highlight_screen_width, width - highlight_screen_start_x)
                    if actual_width > 0:
                        try:
                            self.stdscr.chgat(
                                screen_y,
                                highlight_screen_start_x,
                                actual_width, # Используем скорректированную ширину
                                search_highlight_color
                            )
                        except curses.error as e:
                            logging.error(f"Curses error applying search highlight at ({screen_y}, {highlight_screen_start_x}) width {actual_width}: {e}")
                            pass # Игнорируем ошибки chgat, но логируем их

        # --- Отрисовка выделения (как раньше, поверх подсветки поиска) ---
        if self.is_selecting and self.selection_start and self.selection_end:
            start_y, start_x_idx = self.selection_start
            end_y, end_x_idx = self.selection_end
            if (start_y > end_y) or (start_y == end_y and start_x_idx > end_x_idx):
                start_y, start_x_idx, end_y, end_x_idx = end_y, end_x_idx, start_y, start_x_idx

            for y in range(start_y, end_y + 1):
                if y < self.scroll_top or y >= self.scroll_top + self.visible_lines: continue
                screen_y = y - self.scroll_top
                line = self.text[y]
                line_len_idx = len(line)
                current_start_idx = start_x_idx if y == start_y else 0
                current_end_idx = end_x_idx if y == end_y else line_len_idx
                if current_start_idx >= current_end_idx and not (line_len_idx == 0 and y == start_y == end_y): continue

                sel_screen_start_x, sel_screen_width = -1, 0
                current_screen_x = line_num_width
                for char_idx, char in enumerate(line):
                    char_width = self.get_char_width(char)
                    if char_idx < self.scroll_left:
                        current_screen_x += char_width
                        continue
                    is_selected = current_start_idx <= char_idx < current_end_idx
                    effective_screen_x = line_num_width + self.get_string_width(line[self.scroll_left:char_idx])
                    if effective_screen_x >= width: break
                    if is_selected:
                        if sel_screen_start_x == -1: sel_screen_start_x = effective_screen_x
                        sel_screen_width += char_width
                if sel_screen_start_x != -1 and sel_screen_width > 0:
                    actual_width = min(sel_screen_width, width - sel_screen_start_x)
                    if actual_width > 0:
                        try: self.stdscr.chgat(screen_y, sel_screen_start_x, actual_width, curses.A_REVERSE)
                        except curses.error as e:
                            logging.error(f"Curses error applying selection highlight at ({screen_y}, {sel_screen_start_x}) width {actual_width}: {e}")
                            pass # Игнорируем ошибки chgat, но логируем их

                elif line_len_idx == 0 and current_start_idx == 0 and current_end_idx == 0 and y >= start_y and y <= end_y:
                    if line_num_width < width:
                        try: self.stdscr.chgat(screen_y, line_num_width, 1, curses.A_REVERSE)
                        except curses.error as e:
                            logging.error(f"Curses error applying selection highlight for empty line at ({screen_y}, {line_num_width}): {e}")
                            pass

        # --- Отрисовка статус-бара (как раньше) ---
        try:
            status_y = height - 1
            # ... (код отрисовки статус-бара без изменений) ...
            file_type = self.detect_language()
            file_icon = get_file_icon(self.filename or "untitled", self.config)
            git_branch, git_user, git_commits = self.git_info or ("", "", "0")
            left_status = (f"{file_icon} {os.path.basename(self.filename) if self.filename else '[No Name]'}{'*' if self.modified else ''} | "
                           f"{file_type} | {self.encoding} | "
                           f"Ln {self.cursor_y + 1}/{len(self.text)}, Col {self.cursor_x + 1} | "
                           f"{'INS' if self.insert_mode else 'OVR'}")
            if git_branch or git_user: right_status = f"Git: {git_branch} ({git_commits})"
            else: right_status = ""
            status_color = self.colors.get("status", curses.A_NORMAL)
            self.stdscr.addstr(status_y, 0, " " * (width -1), status_color)
            left_width = self.get_string_width(left_status)
            max_left_width = width - self.get_string_width(right_status) - 2
            if left_width > max_left_width: left_status = left_status[:max_left_width - 3] + "..."
            self.stdscr.addstr(status_y, 0, left_status, status_color)
            if right_status:
                git_color = self.colors.get("green", status_color)
                right_start_col = width - self.get_string_width(right_status) -1
                if right_start_col < self.get_string_width(left_status) + 1: right_start_col = self.get_string_width(left_status) + 1
                if right_start_col < width: self.stdscr.addstr(status_y, right_start_col, right_status, git_color)
            if self.status_message:
                msg_width = self.get_string_width(self.status_message)
                msg_start_col = (width - msg_width) // 2
                if msg_start_col > self.get_string_width(left_status) and msg_start_col + msg_width < (width - self.get_string_width(right_status) -1) :
                    self.stdscr.addstr(status_y, msg_start_col, self.status_message, status_color | curses.A_BOLD)
                self.status_message = ""
        except curses.error: pass


        # --- Позиционирование курсора (как раньше) ---
        cursor_screen_y = self.cursor_y - self.scroll_top
        cursor_line_text = self.text[self.cursor_y]
        processed_width_before_cursor = self.get_string_width(cursor_line_text[:self.cursor_x])
        scroll_left_width = self.get_string_width(cursor_line_text[:self.scroll_left])
        final_cursor_screen_x = line_num_width + processed_width_before_cursor - scroll_left_width

        if 0 <= cursor_screen_y < self.visible_lines and line_num_width <= final_cursor_screen_x < width:
            try:
                self.stdscr.move(cursor_screen_y, final_cursor_screen_x)
            except curses.error as e:
                logging.error(f"Curses error moving cursor to ({cursor_screen_y}, {final_cursor_screen_x}): {e}")
                    # Попытка переместить курсор хотя бы в начало видимой строки
                try:
                    self.stdscr.move(cursor_screen_y, line_num_width)
                except curses.error as e_fallback:
                    logging.error(f"Curses fallback error moving cursor to ({cursor_screen_y}, {line_num_width}): {e_fallback}")
                    pass
                
        elif 0 <= cursor_screen_y < self.visible_lines:
            try: self.stdscr.move(cursor_screen_y, line_num_width)
            except curses.error: pass

        # --- Обновление экрана ---
        self.stdscr.noutrefresh()
        curses.doupdate()


    def detect_language(self):
        """Detects the file's language based on its extension."""
        ext = os.path.splitext(self.filename)[1].lstrip('.').lower()
        logging.debug(f"Detecting language for extension: {ext}")
        for lang, exts in self.config.get("supported_formats", {}).items():
            if ext in exts:
                logging.debug(f"Detected language: {lang}")
                return lang
        logging.debug("No language detected, defaulting to 'text'")
        return "text"


    def handle_input(self, key):
        try:
            # Логируем только критические события
            logging.debug(f"Key pressed: {key}")

            # === Основная логика обработки клавиш ===
            if key in (curses.KEY_ENTER, 10, 13):     # Enter
                self.handle_enter()
            elif key in (curses.KEY_UP, 259, 450):     # Up Arrow  
                self.handle_up()
            elif key in (curses.KEY_DOWN, 258, 456):   # Down Arrow
                self.handle_down()
            elif key in (curses.KEY_LEFT, 260, 452):   # Left Arrow
                self.handle_left()
            elif key in (curses.KEY_RIGHT, 261, 454):   # Right Arrow
                self.handle_right()
            elif key in (curses.KEY_BACKSPACE, 127, 8):   # Backspace
                self.handle_backspace()
            elif key in (curses.KEY_DC, 330, 462):    # Delete
                self.handle_delete()
            elif key in (curses.KEY_HOME, 262, 449):   # Home
                self.handle_home()
            elif key in (curses.KEY_END, 360, 455):    # End
                self.handle_end()
            elif key in (curses.KEY_PPAGE, 339, 451):  # Page Up
                self.handle_page_up()
            elif key in (curses.KEY_NPAGE, 338, 457):  # Page Down
                self.handle_page_down()
            elif key == ord('\t'):                                        # Tab
                self.handle_smart_tab()                                
            elif key == self.keybindings["help"]:                       # F1
                self.show_help()
            elif key == self.keybindings["new_file"]:                   # F4
                self.new_file()
            elif key == self.keybindings["save_file"]:     # Ctrl+S
                self.save_file()
            elif key == self.keybindings["save_as"]:       # F5
                self.save_file_as()
            elif key == self.keybindings["open_file"]:     # Ctrl+O
                self.open_file()
            elif key == self.keybindings["copy"]:          # Ctrl+C
                self.copy()
            elif key == self.keybindings["cut"]:           # Ctrl+X
                self.cut()
            elif key == self.keybindings["paste"]:         # Ctrl+V
                self.paste()
            elif key == self.keybindings["redo"]:          # Ctrl+Y
                self.redo()
            elif key == self.keybindings["undo"]:          # Ctrl+Z
                self.undo()
            elif key == self.keybindings["find"]:          # Ctrl+F
                self.find_prompt()
            elif key == self.keybindings["find_next"]:     # F3
                self.find_next()
            elif key == self.keybindings["goto_line"]:  # Ctrl+G   
                self.goto_line()
                
            elif key >= 32 and key <= 255:                 # Printable characters
                self.handle_char_input(key)
            elif key == self.keybindings["select_all"]:    # Ctrl+A
                self.select_all()
            elif key == curses.KEY_SRIGHT:                 # Shift+Right Arrow
                self.extend_selection_right()
            elif key == curses.KEY_SLEFT:                  # Shift+Left Arrow
                self.extend_selection_left()
            elif key == curses.KEY_SHOME:                  # Shift+Home
                self.select_to_home()
            elif key == curses.KEY_SEND:                   # Shift+End
                self.select_to_end()
            elif key in (self.keybindings["extend_selection_up"], curses.KEY_SR, 337):
                self.extend_selection_up()
            elif key in (self.keybindings["extend_selection_down"], curses.KEY_SF, 336):
                self.extend_selection_down()         
            elif key == self.keybindings["quit"]:          # Ctrl+Q
                self.exit_editor()
            elif key == self.keybindings["cancel_operation"]:  # Esc
                self.cancel_operation()
            elif key == self.keybindings["git_menu"]:      #  F2 Git menu
                self.integrate_git()
            
        
        except Exception as e:
                self.status_message = f"Error: {str(e)}"
                logging.error(f"Input handling error: {str(e)}")
                logging.error(traceback.format_exc())


    def handle_up(self):
        """Moves the cursor up by one line."""
        if self.cursor_y > 0:
            self.cursor_y -= 1
            self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))


    def handle_down(self):
        """Moves the cursor down by one line."""
        if self.cursor_y < len(self.text) - 1:
            self.cursor_y += 1
            self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))


    def handle_left(self):
        """Moves the cursor left by one character or to the previous line end."""
        if self.cursor_x > 0:
            self.cursor_x -= 1
        elif self.cursor_y > 0:
            self.cursor_y -= 1
            self.cursor_x = len(self.text[self.cursor_y])


    def handle_right(self):
        """Moves the cursor right by one character or to the beginning of the next line."""
        if self.cursor_x < len(self.text[self.cursor_y]):
            self.cursor_x += 1
        elif self.cursor_y < len(self.text) - 1:
            self.cursor_y += 1
            self.cursor_x = 0


    def handle_home(self):
        """Moves the cursor to the beginning of the current line."""
        self.cursor_x = 0


    def handle_end(self):
        """Moves the cursor to the end of the current line."""
        self.cursor_x = len(self.text[self.cursor_y])


    def handle_page_up(self):
        """Moves the cursor up by one screen height."""
        height = self.stdscr.getmaxyx()[0]
        self.cursor_y = max(0, self.cursor_y - height)
        self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))
        self.scroll_top = max(0, self.scroll_top - height)


    def handle_page_down(self):
        """Moves the cursor down by one screen height."""
        height = self.stdscr.getmaxyx()[0]
        self.cursor_y = min(len(self.text) - 1, self.cursor_y + height)
        self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))
        if self.cursor_y >= self.scroll_top + height:
            self.scroll_top = max(0, min(len(self.text) - height, self.scroll_top + height))


    def handle_backspace(self):
        if self.is_selecting and self.selection_start and self.selection_end:
            self.delete_selected_text()
        else:
            if self.cursor_x > 0:
                line = self.text[self.cursor_y]
                deleted_char = line[self.cursor_x - 1]
                self.text[self.cursor_y] = line[:self.cursor_x - 1] + line[self.cursor_x:]
                self.cursor_x -= 1
                self.modified = True
                self.action_history.append({
                    "type": "delete",
                    "text": deleted_char,
                    "start": self.cursor_y,
                    "start_col": self.cursor_x,
                    "end": self.cursor_y,
                    "end_col": self.cursor_x + 1
                })
            elif self.cursor_y > 0:
                deleted_line = self.text.pop(self.cursor_y)
                self.cursor_y -= 1
                self.cursor_x = len(self.text[self.cursor_y])
                self.text[self.cursor_y] += deleted_line
                self.modified = True
                self.action_history.append({
                    "type": "delete",
                    "text": "\n" + deleted_line,
                    "start": self.cursor_y,
                    "start_col": self.cursor_x,
                    "end": self.cursor_y + 1,
                    "end_col": 0
                })
        self.undone_actions.clear()


    def handle_delete(self):
        if self.is_selecting and self.selection_start and self.selection_end:
            self.delete_selected_text()
        else:
            if self.cursor_x < len(self.text[self.cursor_y]):
                line = self.text[self.cursor_y]
                deleted_char = line[self.cursor_x]
                self.text[self.cursor_y] = line[:self.cursor_x] + line[self.cursor_x + 1:]
                self.modified = True
                self.action_history.append({
                    "type": "delete",
                    "text": deleted_char,
                    "start": self.cursor_y,
                    "start_col": self.cursor_x,
                    "end": self.cursor_y,
                    "end_col": self.cursor_x + 1
                })
            elif self.cursor_y < len(self.text) - 1:
                deleted_line = self.text.pop(self.cursor_y + 1)
                self.text[self.cursor_y] += deleted_line
                self.modified = True
                self.action_history.append({
                    "type": "delete",
                    "text": "\n" + deleted_line,
                    "start": self.cursor_y,
                    "start_col": len(self.text[self.cursor_y]) - len(deleted_line),
                    "end": self.cursor_y + 1,
                    "end_col": 0
                })
        self.undone_actions.clear()


    def handle_tab(self):
        """Inserts spaces or a tab character depending on configuration."""
        tab_size = self.config.get("editor", {}).get("tab_size", 4)
        use_spaces = self.config.get("editor", {}).get("use_spaces", True)
        current_line = self.text[self.cursor_y]

        if use_spaces:
            spaces = " " * tab_size
            self.text[self.cursor_y] = (
                current_line[: self.cursor_x] + spaces + current_line[self.cursor_x :]
            )
            self.cursor_x += tab_size
        else:
            self.text[self.cursor_y] = (
                current_line[: self.cursor_x] + "\t" + current_line[self.cursor_x :]
            )
            self.cursor_x += 1

        self.modified = True


    def handle_smart_tab(self):
        """
        Если курсор в начале строки (cursor_x == 0),
        копирует отступ (пробелы/таб) предыдущей строки.
        Иначе – падает обратно на handle_tab().
        """
        if self.cursor_y > 0:
            prev_line = self.text[self.cursor_y - 1]
            m = re.match(r"^(\s*)", prev_line)
            if m and self.cursor_x == 0:
                # копируем leading_spaces из prev_line
                self.text[self.cursor_y] = m.group(1) + self.text[self.cursor_y]
                self.cursor_x = len(m.group(1))
                self.modified = True
                return
        # иначе – обычный таб
        self.handle_tab()



    def handle_char_input(self, key):
        """Handles regular character input and supports undo."""

        try:
            char = chr(key)
            current_line = self.text[self.cursor_y]

            # Сохраняем позицию для undo
            start_y, start_x = self.cursor_y, self.cursor_x

            if self.insert_mode:
                self.text[self.cursor_y] = (
                    current_line[:self.cursor_x] + char + current_line[self.cursor_x:]
                )
            else:
                self.text[self.cursor_y] = (
                    current_line[:self.cursor_x]
                    + char
                    + (
                        current_line[self.cursor_x + 1:]
                        if self.cursor_x < len(current_line)
                        else ""
                    )
                )

            self.cursor_x += 1
            self.modified = True

            # Добавляем в историю действий
            self.action_history.append({
                "type": "insert",
                "text": char,
                "position": (start_y, start_x)
            })
            self.undone_actions.clear()  # Сброс отменённых действий

            logging.debug(f"Inserted char '{char}' at ({start_y}, {start_x})")

        except (ValueError, UnicodeEncodeError):
            logging.error(f"Cannot encode character: {key}")
        except Exception as e:
            logging.exception(f"Error handling character input: {str(e)}")
            self.status_message = f"Input error: {str(e)}"



    def handle_enter(self):
        """Handles the Enter key, creating a new line at the cursor position."""
        self.text.insert(self.cursor_y + 1, "")
        content = self.text[self.cursor_y][self.cursor_x :]
        self.text[self.cursor_y] = self.text[self.cursor_y][: self.cursor_x]
        self.text[self.cursor_y + 1] = content
        self.cursor_y += 1
        self.cursor_x = 0
        self.modified = True


    def parse_key(self, key_str: str) -> int:
        """
        Преобразует строку-описание горячей клавиши в curses-код.

        Поддерживается:
        • F1–F12, стрелки, Home/End, PgUp/PgDn, Insert/Delete, Backspace
        • Ctrl+<буква>, Ctrl+Shift+<буква>
        • Alt+<…> (буква, цифра, символ, F-клавиша, именованная), помечается битом 0x200
        Если строка некорректна — возбуждает ValueError.
        """
        if not key_str:
            raise ValueError("empty hotkey")

        key_str = key_str.strip().lower()

        # ---------- фиксированные имена ----------
        named = {
            "del":        curses.KEY_DC,
            "delete":     curses.KEY_DC,
            "backspace":  curses.KEY_BACKSPACE,
            "tab":        ord("\t"),
            "enter":      ord("\n"),
            "return":     ord("\n"),
            "space":      ord(" "),
            "esc":        27,
            "escape":     27,
            "up":         curses.KEY_UP,
            "down":       curses.KEY_DOWN,
            "left":       curses.KEY_LEFT,
            "right":      curses.KEY_RIGHT,
            "home":       curses.KEY_HOME,
            "end":        curses.KEY_END,
            "pageup":     curses.KEY_PPAGE,
            "pgup":       curses.KEY_PPAGE,
            "pagedown":   curses.KEY_NPAGE,
            "pgdn":       curses.KEY_NPAGE,
            "insert":     curses.KEY_IC,
        }
        # Добавляем F1–F12
        named.update({f"f{i}": getattr(curses, f"KEY_F{i}") for i in range(1, 13)})

        # ---------- Alt-модификатор ----------
        if key_str.startswith("alt+"):
            base = self.parse_key(key_str[4:])      # рекурсивно разбираем «хвост»
            return base | 0x200                     # задаём собственный бит Alt

        parts = key_str.split("+")

        # ---------- Ctrl и Ctrl+Shift ----------
        if len(parts) == 2 and parts[0] == "ctrl":
            ch = parts[1]
            if len(ch) == 1 and ch.isalpha():
                return ord(ch) - ord("a") + 1       # стандарт ASCII Ctrl
            raise ValueError(f"unsupported Ctrl combination: {key_str}")

        if len(parts) == 3 and parts[:2] == ["ctrl", "shift"]:
            ch = parts[2]
            if len(ch) == 1 and ch.isalpha():
                return (ord(ch) - ord("a") + 1) | 0x100   # свой диапазон
            raise ValueError(f"unsupported Ctrl+Shift combination: {key_str}")

        # ---------- просто имя ----------
        if key_str in named:
            return named[key_str]

        # ---------- один символ ----------
        if len(key_str) == 1:
            return ord(key_str)

        raise ValueError(f"cannot parse hotkey: {key_str}")


    def get_char_width(self, char):
        """
        Calculates the display width of a character using wcwidth.
        Returns 1 for control characters or characters with ambiguous width (-1).
        """
        width = wcwidth(char)
        # Возвращаем 1 для непечатаемых или нулевой ширины, иначе ширину
        return width if width > 0 else 1


    def get_string_width(self, text):
        """
        Calculates the display width of a string using wcswidth.
        Handles potential errors by summing individual character widths.
        """
        try:
            width = wcswidth(text)
            if width >= 0:
                return width
        except Exception:
            pass # Fallback

        total_width = 0
        for char in text:
            total_width += self.get_char_width(char)
        return total_width


    def open_file(self):
        """
        Открывает файл с авто-определением кодировки (chardet),
        сбрасывает лексер и Git-инфо.
        """
        # спросить о сохранении, если были изменения
        if self.modified:
            if (ans := self.prompt("Save changes? (y/n): ")).lower().startswith("y"):
                self.save_file()

        filename = self.prompt("Open file: ")
        if not filename:                       # Esc или пустая строка
            self.status_message = "Open cancelled"
            return
        if not self.validate_filename(filename):
            self.status_message = "Invalid filename"
            return

        try:
            # ── определяем кодировку ───────────────────────────
            with open(filename, "rb") as f:
                enc_guess = chardet.detect(f.read())["encoding"]
            self.encoding = enc_guess or "UTF-8"

            # ── читаем файл ────────────────────────────────────
            with open(filename, "r", encoding=self.encoding, errors="replace") as f:
                self.text = f.read().splitlines() or [""]

            # ── обновляем состояние ────────────────────────────
            self.filename  = filename
            self._lexer    = None        # ✨ заставляем Pygments выбрать новый лексер
            self.modified  = False
            self.set_initial_cursor_position()
            self.status_message = f"Opened {filename}  (enc: {self.encoding})"
            self.update_git_info()
            curses.flushinp()

        except FileNotFoundError:
            self.status_message = f"File not found: {filename}"
        except Exception as e:
            self.status_message = f"Error opening file: {e}"
            logging.exception(f"Error opening file: {filename}")


    def save_file(self):
        """
        Сохраняет текущий документ.
        Если имя файла ещё не задано – предлагает «Save as:».
        """
        # ── 1. имя не задано → спрашиваем ─────────────────────
        if not self.filename:
            new_name = self.prompt("Save as: ")
            if not new_name:
                self.status_message = "Save cancelled"
                return
            if not self.validate_filename(new_name):
                self.status_message = "Invalid filename"
                return
            self.filename = new_name
            self._lexer   = None          # ✨ подсветка по новому расширению

        # ── 2. санк-проверки ──────────────────────────────────
        if os.path.isdir(self.filename):
            self.status_message = f"Cannot save: {self.filename} is a directory"
            return
        if os.path.exists(self.filename) and not os.access(self.filename, os.W_OK):
            self.status_message = f"No write permissions: {self.filename}"
            return

        # ── 3. записываем файл ───────────────────────────────
        try:
            with open(self.filename, "w", encoding=self.encoding, errors="replace") as f:
                f.write(os.linesep.join(self.text))

            self.modified = False
            self.status_message = f"Saved to {self.filename}"

            code = os.linesep.join(self.text)
            threading.Thread(target=self.run_lint_async,
                            args=(code,), daemon=True).start()
            self.update_git_info()

        except Exception as e:
            self.status_message = f"Error saving file: {e}"
            logging.exception(f"Error saving file: {self.filename}")


    def save_file_as(self):
        """
        Сохраняет документ под новым именем
        и сбрасывает лексер для корректной подсветки.
        """
        new_filename = self.prompt("Save file as: ")
        if not new_filename:
            self.status_message = "Save cancelled"
            return
        if not self.validate_filename(new_filename):
            self.status_message = "Invalid filename"
            return
        if os.path.isdir(new_filename):
            self.status_message = f"Cannot save: {new_filename} is a directory"
            return
        if os.path.exists(new_filename) and not os.access(new_filename, os.W_OK):
            self.status_message = f"No write permissions: {new_filename}"
            return

        try:
            with open(new_filename, "w", encoding=self.encoding, errors="replace") as f:
                f.write(os.linesep.join(self.text))

            self.filename = new_filename
            self._lexer   = None          # ✨ выбрать лексер по новому расширению
            self.modified = False
            self.status_message = f"Saved as {new_filename}"

            code = os.linesep.join(self.text)
            threading.Thread(target=self.run_lint_async,
                            args=(code,), daemon=True).start()

        except Exception as e:
            self.status_message = f"Error saving file: {e}"
            logging.exception(f"Error saving file: {new_filename}")


    def revert_changes(self):
        """
        Reverts unsaved changes by reloading from the last saved version.
        """
        if self.filename == "noname":
            self.status_message = "Cannot revert: file has not been saved yet"
            return

        if not os.path.exists(self.filename):
            self.status_message = f"Cannot revert: file {self.filename} does not exist"
            return

        confirmation = self.prompt("Revert to last saved version? (y/n): ")
        if not confirmation or confirmation.lower() != "y":
            self.status_message = "Revert cancelled"
            return

        try:
            with open(self.filename, "r", encoding=self.encoding, errors="replace") as f:
                self.text = f.read().splitlines()
                if not self.text:
                    self.text = [""]

            self.modified = False
            self.set_initial_cursor_position()
            self.status_message = f"Reverted to last saved version of {self.filename}"
        except OSError as e:
            self.status_message = f"Error reverting file: {e}"
            logging.exception(f"Error reverting file: {self.filename}")
        except Exception as e:
            self.status_message = f"Unexpected error: {e}"
            logging.exception(f"Unexpected error reverting file: {self.filename}")


    def new_file(self):
        """
        Создаёт пустой документ.
        При наличии несохранённых правок предлагает сохранить.
        Сбрасывает имя файла, лексер и позицию курсора.
        """
        # ── 1. при необходимости спрашиваем о сохранении ─────────────
        if self.modified:
            if (ans := self.prompt("Save changes? (y/n): ")).lower().startswith("y"):
                self.save_file()

        # ── 2. инициализируем «чистый» буфер ─────────────────────────
        try:
            self.text       = [""]
            self.filename   = ""       # имя ещё не задано
            self._lexer     = None     # ✨ Pygments выберет лексер позже
            self.modified   = False
            self.encoding   = "UTF-8"  # разумное значение по умолчанию

            self.set_initial_cursor_position()
            self.status_message = "New file created"

        except Exception as e:
            self.status_message = f"Error creating new file: {e}"
            logging.exception("Error creating new file")


    def cancel_operation(self):
        """
        Обработчик «Esc-отмены», вызывается из handle_input()
        и через action_map/горячую клавишу.

        • если есть выделение ‒ снимает его;  
        • если открыт prompt (нажатие Esc уже вернуло пустую строку) –
        просто пишет статус «Cancelled»;  
        • иначе сбрасывает строку статуса.
        """
        if self.is_selecting:
            self.is_selecting = False
            self.selection_start = self.selection_end = None
            self.status_message = "Selection cancelled"
        elif self.highlighted_matches: # Если есть активная подсветка поиска
            self.highlighted_matches = [] # Очищаем ее
            self.search_matches = []      # Также сбрасываем результаты для F3
            self.search_term = ""
            self.current_match_idx = -1
            self.status_message = "Search highlighting cancelled"
        else:
            # Если не было ни выделения, ни подсветки, просто сообщение
            self.status_message = "Operation cancelled"
        # Перерисовка произойдет в главном цикле


    def handle_escape(self):
            """
            Универсальная обработка Esc.

            • Если есть активное выделение ‒ просто убираем его.  
            • Если предыдущая Esc была менее чем 1.5 с назад,
            считаем это намерением выйти и завершаемся.  
            • Иначе  ‒ лишь ставим статус «Cancelled».
            """
            now = time.monotonic()
            last = getattr(self, "_last_esc_time", 0)

            # 1) идёт выделение → сбрасываем
            if self.is_selecting:
                self.is_selecting = False
                self.selection_start = self.selection_end = None
                self.status_message = "Selection cancelled"

            # 2) двойной Esc (быстрее 1.5 c) → выход
            elif now - last < 1.5:
                if self.modified:
                    choice = self.prompt("Save changes before exit? (y/n): ")
                    if choice and choice.lower().startswith("y"):
                        self.save_file()
                self.exit_editor()

            # 3) всё остальное → просто «Cancelled»
            else:
                self.status_message = "Cancelled"

            # запоминаем время Esc
            self._last_esc_time = now


    def exit_editor(self):
        """
        Exits the editor with a prompt to save any unsaved changes.
        """
        if self.modified:
            choice = self.prompt("Save changes? (y/n): ")
            if choice and choice.lower().startswith("y"):
                self.save_file()
        
        self._auto_save_enabled = False # Останавливаем поток автосохранения
        if hasattr(self, "_auto_save_thread") and self._auto_save_thread and self._auto_save_thread.is_alive():
            self._auto_save_thread.join(timeout=5) # Ждем немного, чтобы поток завершился
        curses.endwin()
        sys.exit(0)


    def prompt(self, message: str, max_len: int = 1024) -> str:
        """
        Однострочный ввод в статус-строке.

        ▸ Esc      — отмена, возвращает ""  
        ▸ Enter    — подтверждение  
        ▸ Backspace, ←/→, Home/End работают «как привычно»  
        """
        # переключаем curses в «обычный» режим
        self.stdscr.nodelay(False)
        curses.echo(False)

        # координаты строки ввода
        row = curses.LINES - 1
        col = 0
        try:
            # рисуем приглашение
            self.stdscr.move(row, col)
            self.stdscr.clrtoeol()
            self.stdscr.addstr(row, col, message)
            self.stdscr.refresh()

            # буфер и позиция курсора внутри него
            buf: list[str] = []
            pos = 0

            while True:
                ch = self.stdscr.get_wch()   # поддерживает UTF-8

                # ─── клавиши управления ──────────────────────────────
                if ch in ("\n", "\r"):                    # Enter
                    break
                elif ch == "\x1b":                       # Esc (0x1B)
                    buf = []          # пустой ответ = отмена
                    break
                elif ch in ("\x08", "\x7f", curses.KEY_BACKSPACE):
                    if pos > 0:
                        pos -= 1
                        buf.pop(pos)
                elif ch in (curses.KEY_LEFT,):
                    pos = max(0, pos - 1)
                elif ch in (curses.KEY_RIGHT,):
                    pos = min(len(buf), pos + 1)
                elif ch in (curses.KEY_HOME,):
                    pos = 0
                elif ch in (curses.KEY_END,):
                    pos = len(buf)

                # ─── печатный символ ─────────────────────────────────
                elif isinstance(ch, str) and ch.isprintable():
                    if len(buf) < max_len:
                        buf.insert(pos, ch)
                        pos += 1

                # ─── отрисовка строки ввода ──────────────────────────
                # очищаем хвост, чтобы стирались удалённые символы
                self.stdscr.move(row, len(message))
                self.stdscr.clrtoeol()
                self.stdscr.addstr(row, len(message), "".join(buf))
                # позиционируем курсор
                self.stdscr.move(row, len(message) + pos)
                self.stdscr.refresh()
        # ────────────────────────────────────────────────
        except Exception as e:
            logging.exception(f"Prompt error: {e}")
            buf = []          # считаем ввод отменённым

        finally:
            try:
                curses.flushinp()          # чистим буфер ввода
                curses.noecho()            # отключаем эхо
                self.stdscr.nodelay(False) # возвращаем в «нормальный» режим
                self.stdscr.move(row, 0)   # возвращаем курсор в начало строки
                self.stdscr.clrtoeol()     # очищаем строку ввода
                self.stdscr.refresh()      # очищаем экран
            except curses.error as e_finally:
                logging.error(f"Curses error in prompt cleanup: {e_finally}")
                # В этом случае, возможно, потребуется аварийное завершение или сброс
                print(f"Critical Curses error during prompt cleanup: {e_finally}")
                sys.exit(1) # Или другой механизм выхода

        return "".join(buf).strip()


    # === ПОИСК ====================================================================

    def _collect_matches(self, term):
        """
        Находит все вхождения term (без учета регистра) в self.text.
        Возвращает список кортежей: [(row_idx, col_start_idx, col_end_idx), ...].
        """
        matches = []
        if not term:
            return matches
        low = term.lower() # Поиск без учета регистра
        for row_idx, line in enumerate(self.text):
            start_col_idx = 0
            line_lower = line.lower() # Сравниваем с нижней версией строки
            while True:
                # Ищем в line_lower, но индексы берем из оригинальной line
                found_idx = line_lower.find(low, start_col_idx)
                if found_idx == -1:
                    break
                match_end_idx = found_idx + len(term)
                matches.append((row_idx, found_idx, match_end_idx))
                # Следующий поиск начинаем после текущего совпадения
                start_col_idx = match_end_idx
        return matches


    def find_prompt(self):
        """
        Запрашивает строку поиска, находит все совпадения,
        сохраняет их для подсветки и переходит к первому.
        """
        # Очищаем предыдущие результаты подсветки
        self.highlighted_matches = []
        self.current_match_idx = -1

        term = self.prompt("Find: ")
        if term == "":
            self.status_message = "Search cancelled"
            # Нужно перерисовать экран, чтобы убрать старую подсветку, если была
            # self.draw_screen() # Неявно вызовется в главном цикле
            return

        self.search_term = term
        # Находим все совпадения
        self.search_matches = self._collect_matches(term)
        # Сохраняем их же для подсветки
        self.highlighted_matches = self.search_matches

        if not self.search_matches:
            self.status_message = f"'{term}' not found"
            self.current_match_idx = -1
        else:
            # Переходим к первому совпадению
            self.current_match_idx = 0
            self._goto_match(self.current_match_idx) # Перемещаем курсор
            self.status_message = f"Found {len(self.search_matches)} match(es). Press F3 for next."

        # Перерисовка произойдет в главном цикле, отображая подсветку


    def find_next(self):
        """
        Переходит к следующему совпадению (по циклу).
        Не меняет список highlighted_matches.
        """
        if not self.search_matches:
            # Если пользователь нажал F3 до первого поиска или поиск не дал результатов
            self.status_message = "No search results to cycle through. Use Ctrl+F first."
            # Очищаем подсветку на всякий случай
            self.highlighted_matches = []
            return

        # Переходим к следующему индексу по кругу
        self.current_match_idx = (self.current_match_idx + 1) % len(self.search_matches)
        self._goto_match(self.current_match_idx) # Перемещаем курсор
        self.status_message = (
            f"Match {self.current_match_idx + 1}/{len(self.search_matches)}"
        )
        # Перерисовка с подсветкой произойдет в главном цикле


    def _goto_match(self, idx):
        """Переносит курсор/прокрутку к совпадению № idx и показывает его."""
        row, col_start, col_end = self.search_matches[idx]
        self.cursor_y, self.cursor_x = row, col_start
        height = self.stdscr.getmaxyx()[0]
        # вертикальная прокрутка
        if self.cursor_y < self.scroll_top:
            self.scroll_top = max(0, self.cursor_y - height // 2)
        elif self.cursor_y >= self.scroll_top + self.visible_lines:
            self.scroll_top = min(
                len(self.text) - self.visible_lines,
                self.cursor_y - height // 2,
            )
        # горизонтальная прокрутка
        width = self.stdscr.getmaxyx()[1]
        ln_width = len(str(len(self.text))) + 1   # ширина номера + пробел
        text_width = width - ln_width
        if self.cursor_x < self.scroll_left:
            self.scroll_left = self.cursor_x
        elif self.cursor_x >= self.scroll_left + text_width:
            self.scroll_left = max(0, self.cursor_x - text_width + 1)


    def search_text(self, search_term):
        """
        Searches for all occurrences of the specified term across all lines
        of the document and returns a list of matches.
        """
        matches = []
        for line_num, line in enumerate(self.text):
            for match in re.finditer(re.escape(search_term), line):
                matches.append((line_num, match.start(), match.end()))
        return matches


    def validate_filename(self, filename):
        """
        Validates the filename for length, correctness, and path.
        """

        if not filename or len(filename) > 255:
            return False

        try:
            # Получаем канонический путь к файлу после разрешения символических ссылок и ..
            absolute_path = os.path.abspath(filename)
            # Получаем канонический путь к текущей рабочей директории
            current_dir = os.path.abspath(os.getcwd())

            # Проверяем, начинается ли абсолютный путь файла с пути текущей директории
            # Это гарантирует, что файл находится внутри текущей директории или поддиректории
            return absolute_path.startswith(current_dir + os.sep) or absolute_path == current_dir
        except Exception as e:
            logging.error(f"Error validating filename '{filename}': {e}")
            return False # При любой ошибке валидация считается неуспешной
        



# =============выполнения команд оболочки Shell commands =================================

    def _execute_shell_command_async(self, cmd_list):
        """
        Выполняет команду оболочки в отдельном потоке и отправляет результат
        в очередь self._shell_cmd_q.
        """
        output = ""
        error = ""
        message = ""

        try:
            # Блокируем curses-экран на время выполнения внешней команды
            # (Это должно происходить в потоке, который *не* вызывает curses)
            # Лучше передать результат блокировки в основной поток, если это возможно.
            # Простой вариант: блокируем здесь, НО stdscr.refresh() НЕ вызываем.
            # Curses state switching should ideally be handled by the main thread
            # based on a signal from the worker thread.
            # For simplicity in this example, we do state switching here,
            # but be aware of potential issues with state changes from non-main threads.

            # ВНИМАНИЕ: Вызовы curses.def_prog_mode(), curses.endwin(),
            # curses.reset_prog_mode() из вторичного потока потенциально опасны!
            # Лучше отправить сигнал в основной поток для выполнения этих действий.
            # Однако, как временное решение для примера:
            # curses.def_prog_mode() # <-- Может вызвать проблемы
            # curses.endwin() # <-- Может вызвать проблемы

            # Запускаем без shell=True
            process = subprocess.Popen(
                cmd_list,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            # communicate() блокирует поток, но не curses
            output, error = process.communicate(timeout=30)

        except FileNotFoundError:
            message = f"Executable not found: {cmd_list[0]}"
        except subprocess.TimeoutExpired:
            message = "Command timed out"
            # Можно убить процесс здесь, если необходимо:
            # process.kill()
            # output, error = process.communicate() # Получить оставшийся вывод
        except Exception as e:
            logging.exception(f"Error executing shell command: {e}")
            message = f"Exec error: {e}"
        finally:
            # Возвращаемся в curses-режим (опять же, лучше в основном потоке)
            # curses.reset_prog_mode() # <-- Может вызвать проблемы
            # self.stdscr.refresh() # <-- КРИТИЧЕСКИ НЕЛЬЗЯ вызывать из вторичного потока!

            # Отправляем результат обратно в основной поток через очередь
            if not message: # Если нет ошибки выполнения процесса
                if error and error.strip():
                    message = f"Error: {error.strip()[:80]}..." # Ограничиваем длину
                else:
                    message = f"Command executed: {output.strip()[:80]}..." # Ограничиваем длину

            self._shell_cmd_q.put(message)
            

    def execute_shell_command(self):
        """
        Запрашивает у пользователя команду, запускает её выполнение в отдельном потоке
        и ожидает результат через очередь для обновления статуса.
        """
        command = self.prompt("Enter command: ")
        if not command:
            self.status_message = "Command cancelled"
            return

        # разбиваем строку на аргументы (учитывает кавычки, экранирование)
        try:
            cmd_list = shlex.split(command)
            if not cmd_list: # Пустая команда после split
                self.status_message = "Empty command"
                return
        except ValueError as e:
            self.status_message = f"Parse error: {e}"
            return

        # Запускаем выполнение команды в отдельном потоке
        self.status_message = f"Executing command: {' '.join(cmd_list[:3])}..." # Показать начало команды
        threading.Thread(target=self._execute_shell_command_async,
                        args=(cmd_list,), daemon=True).start()

        # Результат будет получен позже в главном цикле через _shell_cmd_q



    # GIT ==================================================================

    def _run_git_command_async(self, cmd_list, command_name):
        """Выполняет команду Git в отдельном потоке."""
        try:
            # Блокируем curses-экран, чтобы вывести результат после выполнения
            curses.def_prog_mode()
            curses.endwin()

            proc = safe_run(cmd_list)

            # возвращаемся в curses-режим
            curses.reset_prog_mode()
            self.stdscr.refresh() # Здесь может быть ошибка, если вызывается не из основного потока!
                                # Лучше передать результат в очередь.

            if proc.returncode == 0:
                message = f"Git {command_name} successful"
                # Возможно, нужно передать output/stderr тоже
            else:
                message = f"Git error: {proc.stderr.strip()[:120]}"

            self._git_cmd_q.put(message) # Отправка сообщения в очередь
            if command_name in ["pull", "commit"]: # Обновляем Git-инфо после этих команд
                self.update_git_info()


        except FileNotFoundError:
            self._git_cmd_q.put("Git не установлен или не найден в PATH")
        except Exception as e:
            logging.exception(f"Git command async error: {e}")
            self._git_cmd_q.put(f"Git error: {e}")


    def integrate_git(self):
        """Меню Git вызывается клавишей F2."""
        commands = {
            "1": ("status", "git status"),
            "2": ("commit", None),          # формируем динамически
            "3": ("push",   "git push"),
            "4": ("pull",   "git pull"),
            "5": ("diff",   "git diff"),
        }

        opts = " ".join(f"{k}:{v[0]}" for k, v in commands.items())
        choice = self.prompt(f"Git menu [{opts}] → ")

        if not choice or choice not in commands:
            self.status_message = "Invalid choice or cancelled"
            return

        command_name = commands[choice][0]
        cmd = []

        if choice == "2":                               # commit
            msg = self.prompt("Commit message: ")
            if not msg:
                self.status_message = "Commit cancelled"
                return
            cmd = ["git", "commit", "-am", msg]         # список аргументов
        else:
            cmd_str = commands[choice][1]
            if cmd_str:
                try:
                    cmd = shlex.split(cmd_str) # Разбиваем строку команды
                except ValueError as e:
                    self.status_message = f"Git command parse error: {e}"
                    return

        if cmd:
            # Запускаем команду в отдельном потоке
            threading.Thread(target=self._run_git_command_async,
                            args=(cmd, command_name), daemon=True).start()
            self.status_message = f"Running git {command_name}..."


    def _fetch_git_info_async(self, file_path: str):
        """Выполняет получение Git-инфо в отдельном потоке."""
        try:
            repo_dir = os.path.dirname(os.path.abspath(file_path)) if file_path else os.getcwd()
            if not os.path.isdir(os.path.join(repo_dir, ".git")):
                self._git_q.put(("", "", "0")) # Пустое инфо
                return

            # 1. Определяем ветку
            try:
                branch = subprocess.run(
                    ["git", "branch", "--show-current"],
                    capture_output=True, text=True, check=True, cwd=repo_dir
                ).stdout.strip()
            except FileNotFoundError:
                logging.warning("Git executable not found")
                return "", "", "0"
            except subprocess.CalledProcessError:
                # fallback: git symbolic-ref
                try:
                    branch = subprocess.run(
                        ["git", "symbolic-ref", "--short", "HEAD"],
                        capture_output=True, text=True, check=True, cwd=repo_dir
                    ).stdout.strip()
                except (subprocess.CalledProcessError, FileNotFoundError):
                    branch = "main"

            # 2. Грязный репозиторий ?
            try:
                dirty = subprocess.run(
                    ["git", "status", "--short"],
                    capture_output=True, text=True, check=True, cwd=repo_dir
                ).stdout.strip()
                if dirty:
                    branch += "*"
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                logging.warning(f"Git status failed: {e}")

            # 3. Имя пользователя
            try:
                user_name = subprocess.run(
                    ["git", "config", "user.name"],
                    capture_output=True, text=True, check=True, cwd=repo_dir
                ).stdout.strip()
            except (subprocess.CalledProcessError, FileNotFoundError):
                user_name = ""

            # 4. Кол-во коммитов
            try:
                commits = subprocess.run(
                    ["git", "rev-list", "--count", "HEAD"],
                    capture_output=True, text=True, check=True, cwd=repo_dir
                ).stdout.strip()
            except (subprocess.CalledProcessError, FileNotFoundError):
                commits = "0"

            # 5. Отправляем результат в очередь
            self._git_q.put((branch, user_name, commits))

        except FileNotFoundError:
            logging.warning("Git executable not found in async thread")
            self._git_q.put(("", "", "0")) # Отправка ошибки
        except Exception as e:
            logging.exception(f"Error fetching Git info in async thread: {e}")
            self._git_q.put(("", "", "0")) # Отправка ошибки


    def update_git_info(self):
        """Запускает асинхронное обновление Git-информации."""
        # Запускаем поток, только если не было активного файла или filename изменился
        # Или если прошло достаточно времени с последнего обновления?
        # Простая версия: запускаем всегда при необходимости
        threading.Thread(target=self._fetch_git_info_async,
                        args=(self.filename,), daemon=True).start()


    def goto_line(self):
        """Переходит на указанную строку. Поддерживает +N / -N от текущей."""
        raw = self.prompt("Go to line (±N or %): ")
        if not raw:
            self.status_message = "Goto cancelled"
            return

        try:
            if raw.endswith('%'):
                # Процент от длины файла
                pct = int(raw.rstrip('%'))
                target = max(1, min(len(self.text), round(len(self.text) * pct / 100)))
            elif raw.startswith(('+', '-')):
                # Относительный сдвиг
                delta = int(raw)
                target = self.cursor_y + 1 + delta
            else:
                target = int(raw)
        except ValueError:
            self.status_message = "Invalid number"
            return

        if not (1 <= target <= len(self.text)):
            self.status_message = f"Line out of range (1–{len(self.text)})"
            return

        self.cursor_y = target - 1
        self.cursor_x = 0
        # Центрируем вид
        height = self.stdscr.getmaxyx()[0]
        self.scroll_top = max(0, self.cursor_y - height // 2)
        self.status_message = f"Moved to line {target}"  # ✓


    def find_and_replace(self):
        """
        Performs find and replace with optional regex support.
        """
        search_term = self.prompt("Search for: ")
        if not search_term:
            return

        replace_term = self.prompt("Replace with: ")
        if replace_term is None:
            return

        try:
            count = 0
            for i in range(len(self.text)):
                new_line = re.sub(search_term, replace_term, self.text[i])
                if new_line != self.text[i]:
                    count += len(re.findall(search_term, self.text[i]))
                    self.text[i] = new_line
                    self.modified = True
            self.status_message = f"Replaced {count} occurrences"
        except re.error as e:
            self.status_message = f"Invalid regex pattern: {str(e)}"
        except Exception as e:
            self.status_message = f"Error during replace: {str(e)}"


    def toggle_insert_mode(self):
        """Toggles between Insert and Replace modes."""
        self.insert_mode = not self.insert_mode
        self.status_message = f"Mode: {'Insert' if self.insert_mode else 'Replace'}"


    def find_matching_bracket(self, line, col, bracket):
        """
        Searches for the matching bracket for the one under the cursor.
        Returns (row, col) of the matching bracket or None if not found.
        """
        brackets = {"(": ")", "{": "}", "[": "]", ")": "(", "}": "{", "]": "["}
        stack = []
        direction = 1 if bracket in "({[" else -1
        start = col + direction

        if direction == 1:
            for i in range(start, len(line)):
                char = line[i]
                if char in "({[":
                    stack.append(char)
                elif char in ")}]":
                    if not stack:
                        return None
                    top = stack.pop()
                    if brackets[top] != char:
                        return None
                    if not stack:
                        return (self.cursor_y, i)
        else:
            for i in range(start, -1, -1):
                char = line[i]
                if char in ")}]":
                    stack.append(char)
                elif char in "({[":
                    if not stack:
                        return None
                    top = stack.pop()
                    if brackets[char] != top:
                        return None
                    if not stack:
                        return (self.cursor_y, i)
        return None


    def highlight_matching_brackets(self):
        """
        Highlights matching brackets if the cursor is currently on a bracket.
        """
        if not (
            0 <= self.cursor_y < len(self.text)
            and 0 <= self.cursor_x < len(self.text[self.cursor_y])
        ):
            return

        line = self.text[self.cursor_y]
        char = line[self.cursor_x]

        if char in "(){}[]":
            match_pos = self.find_matching_bracket(line, self.cursor_x, char)
            if match_pos:
                height, width = self.stdscr.getmaxyx()
                if (
                    0 <= self.cursor_y - self.scroll_top < height
                    and 0 <= self.cursor_x - self.scroll_left < width
                ):
                    self.stdscr.addch(
                        self.cursor_y - self.scroll_top,
                        self.cursor_x - self.scroll_left,
                        char,
                        curses.A_REVERSE,
                    )
                match_y, match_x = match_pos
                if (
                    0 <= match_y - self.scroll_top < height
                    and 0 <= match_x - self.scroll_left < width
                ):
                    self.stdscr.addch(
                        match_y - self.scroll_top,
                        match_x - self.scroll_left,
                        line[match_x],
                        curses.A_REVERSE,
                    )


    def search_and_replace(self):
        """
        Searches and replaces text throughout the document using regex.
        Prompts for a search pattern and replacement text, performs the replacement,
        and reports the number of occurrences replaced.
        """
        search_pattern = self.prompt("Enter search pattern (regex): ")
        if not search_pattern:
            self.status_message = "Search cancelled"
            return

        replace_with = self.prompt("Enter replacement string: ")
        if replace_with is None:
            self.status_message = "Replacement cancelled"
            return

        try:
            compiled_pattern = re.compile(search_pattern)
            new_text = []
            replacements = 0

            for line in self.text:
                new_line, count = compiled_pattern.subn(replace_with, line)
                new_text.append(new_line)
                replacements += count

            self.text = new_text
            self.modified = True
            self.status_message = f"Replaced {replacements} occurrence(s)"
        except re.error as e:
            self.status_message = f"Invalid regex pattern: {e}"
        except Exception as e:
            self.status_message = f"Error during search and replace: {e}"


# -------------- Auto-save ------------------------------
    def toggle_auto_save(self):
        """Enables or disables auto-save functionality."""
        # Инициализируем флаг и поток, если их нет
        if not hasattr(self, "_auto_save_enabled"):
            self._auto_save_enabled = False
            self._auto_save_thread = None

        self._auto_save_enabled = not self._auto_save_enabled

        if self._auto_save_enabled:
            if self._auto_save_thread is None or not self._auto_save_thread.is_alive():
                def auto_save_task():
                    logging.info("Auto-save thread started")
                    while self._auto_save_enabled: # Цикл зависит от флага
                        try:
                            time.sleep(60) # Период сохранения
                            if self.modified:
                                # Сохранение должно происходить в отдельном потоке,
                                # но взаимодействие со SwayEditor (например, обновление статуса)
                                # должно быть через очередь.
                                # Простейший вариант: вызываем save_file, но он блокирует.
                                # Более правильный: отправить сигнал в основной поток на сохранение.
                                # Пример с блокирующим save_file (не рекомендуется в реальном приложении,
                                # если save_file сам не потокобезопасен или не отправляет статус через очередь):
                                # self.save_file() # <- Это вызовет проблемы, если save_file блокирует UI!
                                # Лучше:
                                self._msg_q.put("Attempting auto-save...")
                                try:
                                    # Это все равно должно быть выполнено синхронно в основном потоке
                                    # или Save должен быть реализован так, чтобы не блокировать
                                    # Но для примера:
                                    with open(self.filename, "w", encoding=self.encoding, errors="replace") as f:
                                        f.write(os.linesep.join(self.text))
                                    self.modified = False # Доступ к modified из другого потока - не потокобезопасно!
                                                        # Должно быть через очередь или блокировку.
                                    self._msg_q.put(f"Auto-saved to {self.filename}")
                                except Exception as e:
                                    self._msg_q.put(f"Auto-save error: {e}")
                                    logging.exception("Auto-save failed")

                        except Exception as e:
                            logging.exception(f"Error in auto-save thread: {e}")
                            # Можно выключить автосохранение при критической ошибке
                            self._auto_save_enabled = False
                            self._msg_q.put("Auto-save disabled due to error")

                    logging.info("Auto-save thread finished")


                self._auto_save_thread = threading.Thread(target=auto_save_task, daemon=True) # daemon=True для автоматического завершения
                self._auto_save_thread.start()
            self.status_message = "Auto-save enabled"
        else:
            self.status_message = "Auto-save disabled"
            # Нет необходимости явно останавливать daemon=True поток,
            # он завершится сам, когда self._auto_save_enabled станет False
            # Но если бы не был daemon, пришлось бы ждать завершения

    # Добавить в exit_editor() перед curses.endwin():
    # self._auto_save_enabled = False # Останавливаем поток автосохранения
    # if hasattr(self, "_auto_save_thread") and self._auto_save_thread and self._auto_save_thread.is_alive():
    #     self._auto_save_thread.join(timeout=5) # Ждем немного, чтобы поток завершился


    def show_help(self):
        """
        Показывает всплывающее окно со справкой.
        Закрывается по Esc.  На время показа курсор скрывается.
        """
        help_lines = [
            "  ──  Sway-Pad Help  ──  ",
            "",
            "  F1        : Help (это окно)",
            "  F2        : Git-меню",
            "  F3        : Find next",
            "  F4        : New file",
            "  F5        : Save as…",
            "  Ctrl+S    : Save",
            "  Ctrl+O    : Open file",
            "  Ctrl+Q    : Quit",
            "  Ctrl+Z/Y  : Undo / Redo",
            "  Ctrl+F    : Find",
            "",
            "  © 2025 Siergej Sobolewski — Sway-Pad",
            "  Licensed under the GPL-3.0 License",
            "",
            "  Esc — закрыть окно",
        ]

        # размеры и позиция
        h = len(help_lines) + 2
        w = max(len(l) for l in help_lines) + 4
        max_y, max_x = self.stdscr.getmaxyx()
        y0 = (max_y - h) // 2
        x0 = (max_x - w) // 2

        win = curses.newwin(h, w, y0, x0)
        win.bkgd(" ", curses.color_pair(6))
        win.border()

        for i, text in enumerate(help_lines, start=1):
            win.addstr(i, 2, text)

        win.refresh()

        # ★ прячем курсор и запоминаем предыдущее состояние
        prev_vis = 1 # Значение по умолчанию
        try:
            prev_vis = curses.curs_set(0)   # 0 = invisible, 1/2 = visible
        except curses.error as e:
            logging.warning(f"Curses error hiding cursor in help: {e}")
            prev_vis = 1 # если терминал не поддерживает                   # если терминал не поддерживает

        # ждём Esc
        while True:
            try:
                ch = win.getch()
                if ch == 27:                     # Esc
                    break
            except curses.error as e_getch:
                logging.error(f"Curses error getting char in help: {e_getch}")
                # Возможно, здесь нужно прервать цикл, если getch не работает
                break

        # ★ возвращаем курсор
        try:
            curses.curs_set(prev_vis)
        except curses.error as e_curs_set:
            logging.warning(f"Curses error restoring cursor after help: {e_curs_set}")
        del win
        self.draw_screen()


# =============  Главный цикл редактора  =========================================

    def run(self):
        """
        Главный цикл редактора:
        • принимает сообщения из фоновых потоков через self._msg_q
        • перерисовывает экран
        • обрабатывает клавиши
        • ловит исключения и завершает работу корректно
        """
        while True:
            # ── 1. Получаем сообщения от фоновых задач ──────────────────
            try:
                while not self._msg_q.empty():
                    self.status_message = self._msg_q.get_nowait()
            except queue.Empty:
                pass

            # Получаем сообщения о результатах команд оболочки
            try:
                while not self._shell_cmd_q.empty():
                    self.status_message = self._shell_cmd_q.get_nowait()
            except queue.Empty:
                pass

            # Получаем Git-информацию из очереди
            try:
                while not self._git_q.empty():
                    # Получаем кортеж (branch, user_name, commits) из потока
                    git_data = self._git_q.get_nowait()
                    # Обновляем self.git_info
                    self.git_info = git_data
                    # Можно также обновить статус-бар, если нужно уведомить пользователя об обновлении Git-инфо
                    self.status_message = "Git info updated" # Или более детально
                    logging.debug(f"Updated Git info: {self.git_info}") # Добавьте логирование для отладки
            except queue.Empty:
                 pass
            except Exception as e:
                 logging.exception(f"Error processing Git info queue: {e}")
                 self.status_message = f"Git info error: {e}"

            # ── 2. Отрисовываем интерфейс ────────────────────────────────
            try:
                self.draw_screen()
            except Exception as e:
                logging.exception("Draw error")
                self.status_message = f"Draw error: {e}"

            # ── 3. Обрабатываем ввод пользователя ───────────────────────
            try:
                self.stdscr.keypad(True)
                key = self.stdscr.getch()          # блокирующий вызов
                self.handle_input(key)
            except KeyboardInterrupt:
                self.exit_editor()
            except Exception as e:
                logging.exception("Unhandled exception in main loop")
                self.status_message = f"Error: {e}"



def main(stdscr):
    """
    Initializes locale, stdout encoding, and handles command-line
    arguments before starting the main editor loop.
    """
    os.environ["LANG"] = "en_US.UTF-8"
    locale.setlocale(locale.LC_ALL, "")
    sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")
    editor = SwayEditor(stdscr)

    try:
        if len(sys.argv) > 1:
            editor.filename = sys.argv[1]
            editor.open_file()
    except Exception as e:
        logging.exception(f"Error opening file from command line: {e}")

    editor.run()


if __name__ == "__main__":
    config = load_config()
    print("Configuration loaded:")
    print(config)
    try:
        curses.wrapper(main)
    except Exception as e:
        logging.exception("Unhandled exception in main")
        print("An error occurred. See editor.log for details.")
        error_log_path = os.path.join(os.path.dirname(__file__), "error.log")
        with open(error_log_path, "a") as error_file:
            error_file.write(traceback.format_exc())

        print(f"Editor launch error: {e}")
        print(f"See {error_log_path} for details.")
        sys.exit(1)
