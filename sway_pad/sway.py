#!/usr/bin/env python3
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

        # ── поля для поиска ───────────────────────────────
        self.search_term        = ""   # текущий запрос
        self.search_matches     = []   # [(row,col_start,col_end), …]
        self.current_match_idx  = -1   # индекс в search_matches

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
        }

        # ── финальные инициализации ────────────────────────────────
        self.init_colors()
        self.load_syntax_highlighting()
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

    def update_git_info(self):
        """Обновляет кэшированную Git-информацию для текущего файла."""
        self.git_info = get_git_info(self.filename)

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
        """Initializes curses color pairs for syntax highlighting."""
        bg_color = -1
        curses.init_pair(1, curses.COLOR_BLUE, bg_color)
        curses.init_pair(2, curses.COLOR_GREEN, bg_color)
        curses.init_pair(3, curses.COLOR_MAGENTA, bg_color)
        curses.init_pair(4, curses.COLOR_YELLOW, bg_color)
        curses.init_pair(5, curses.COLOR_CYAN, bg_color)
        curses.init_pair(6, curses.COLOR_WHITE, bg_color)
        curses.init_pair(7, curses.COLOR_YELLOW, bg_color)
        curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_YELLOW)
        self.colors = {
            "error": curses.color_pair(8),
            "line_number": curses.color_pair(7),
            "status": curses.color_pair(6),
            "comment": curses.color_pair(1),
            "keyword": curses.color_pair(2),
            "string": curses.color_pair(3),
            "variable": curses.color_pair(6),
            "punctuation": curses.color_pair(6),
            "literal": curses.color_pair(4),
            "decorator": curses.color_pair(5),
            "type": curses.color_pair(4),
            "selector": curses.color_pair(2),
            "property": curses.color_pair(5),
            "tag": curses.color_pair(2),
            "attribute": curses.color_pair(3),
            "builtins": curses.color_pair(4), 
            "escape": curses.color_pair(5),     
            "magic": curses.color_pair(3),      
            "exception": curses.color_pair(8),   
            "function": curses.color_pair(2),    
            "class": curses.color_pair(4),       
            "number": curses.color_pair(3),      
            "operator": curses.color_pair(6),    
            "green": curses.color_pair(2), 
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
        """Renders the editor screen, including text lines, line numbers, status bar, and cursor."""
        self.stdscr.erase()  # вместо clear()
        height, width = self.stdscr.getmaxyx()

        # Обновляем visible_lines при изменении размера окна
        if self.last_window_size != (height, width):
            self.visible_lines = height - 2
            self.last_window_size = (height, width)

        if height < 24 or width < 80:
            try:
                self.stdscr.addstr(
                    0, 0, "Window too small (min: 80x24)", self.colors["error"]
                )
                self.stdscr.noutrefresh()  # вместо refresh()
                curses.doupdate()
                return
            except curses.error:
                pass
                return

        max_line_num = len(str(len(self.text)))
        line_num_format = f"{{:>{max_line_num}}} "
        line_num_width = len(line_num_format.format(0))
        text_width = width - line_num_width

        if self.cursor_x < self.scroll_left:
            self.scroll_left = max(0, self.cursor_x)
        elif self.cursor_x >= self.scroll_left + text_width:
            self.scroll_left = max(0, self.cursor_x - text_width + 1)

        if self.cursor_y < self.scroll_top:
            self.scroll_top = self.cursor_y
        elif self.cursor_y >= self.scroll_top + self.visible_lines:
            self.scroll_top = self.cursor_y - self.visible_lines + 1

        for screen_row in range(self.visible_lines):
            line_num = self.scroll_top + screen_row + 1
            if line_num > len(self.text):
                break
            try:
                self.stdscr.addstr(
                    screen_row,
                    0,
                    line_num_format.format(line_num),
                    self.colors["line_number"],
                )
            except curses.error:
                pass

            line = self.text[line_num - 1] if line_num <= len(self.text) else ""
            syntax_line = self.apply_syntax_highlighting(line, self.detect_language())
            x_pos = 0
            for text_part, color in syntax_line:
                text_len = len(text_part.encode("utf-8"))
                if x_pos + text_len <= self.scroll_left:
                    x_pos += text_len
                    continue
                visible_start = max(0, self.scroll_left - x_pos)
                visible_part = text_part[visible_start:]
                visible_width = len(visible_part.encode("utf-8"))
                visible_part = visible_part[: text_width - (x_pos - self.scroll_left)]
                screen_x = line_num_width + (x_pos - self.scroll_left)
                try:
                    self.stdscr.addstr(screen_row, screen_x, visible_part, color)
                except curses.error:
                    pass
                x_pos += visible_width

        try:
            status_y = height - 1
            file_type = self.detect_language()
            file_icon = get_file_icon(self.filename, self.config)
            # Используем кэшированную Git-информацию
            git_branch, git_user, git_commits = self.git_info

            # Формируем левую часть статусной строки
            left_status = (
                f"File: {self.filename} | "
                f"Type: {file_icon} {file_type} | "
                f"Encoding: {self.encoding} | "
                f"Line: {self.cursor_y + 1}/{len(self.text)} | "
                f"Column: {self.cursor_x + 1} | "
                f"Mode: {'Insert' if self.insert_mode else 'Replace'}"
            )

            # Формируем правую часть статусной строки
            if git_branch or git_user:
                right_status = f"Git :: branch: {git_branch} | {git_user} | commits: {git_commits}"
            else:
                right_status = "Git :: none"

            # Очищаем строку статуса
            self.stdscr.addstr(status_y, 0, " " * (width - 1), self.colors["status"])

            # Отображаем левую часть
            max_left_length = width - len(right_status) - 2
            self.stdscr.addstr(status_y, 0, left_status[:max_left_length], self.colors["status"])

            # Отображаем правую часть (Git-информация) в зелёном цвете
            if right_status:
                self.stdscr.addstr(
                    status_y,
                    width - len(right_status) - 1,
                    right_status,
                    self.colors.get("green", self.colors["status"])
                )
        except curses.error:
            pass

        cursor_screen_y = self.cursor_y - self.scroll_top
        cursor_screen_x = self.cursor_x - self.scroll_left + line_num_width
        if 0 <= cursor_screen_y < self.visible_lines and 0 <= cursor_screen_x < width:
            try:
                self.stdscr.move(cursor_screen_y, cursor_screen_x)
            except curses.error:
                pass

        # блок выделения
        if self.is_selecting and self.selection_start and self.selection_end:
            start_y, start_x = self.selection_start
            end_y, end_x = self.selection_end
            
            if start_y > end_y or (start_y == end_y and start_x > end_x):
                start_y, start_x, end_y, end_x = end_y, end_x, start_y, start_x
            
            for y in range(start_y, end_y + 1):
                if y < self.scroll_top or y >= self.scroll_top + self.visible_lines:
                    continue
                screen_y = y - self.scroll_top
                line = self.text[y]
                line_len = len(line)
                
                # Определяем начало и конец выделения для текущей строки
                if y == start_y and y == end_y:
                    sel_start = max(start_x, self.scroll_left)
                    sel_end = min(end_x, self.scroll_left + text_width)
                elif y == start_y:
                    sel_start = max(start_x, self.scroll_left)
                    sel_end = min(line_len, self.scroll_left + text_width)
                elif y == end_y:
                    sel_start = self.scroll_left
                    sel_end = min(end_x, self.scroll_left + text_width)
                else:
                    sel_start = self.scroll_left
                    sel_end = min(line_len, self.scroll_left + text_width)
                
                # Применяем выделение одним вызовом chgat
                if sel_start < sel_end:
                    try:
                        self.stdscr.chgat(
                            screen_y,
                            line_num_width + (sel_start - self.scroll_left),
                            sel_end - sel_start,
                            curses.A_REVERSE
                        )
                    except curses.error:
                        pass
                elif line_len == 0 and y >= start_y and y <= end_y:
                    # Для пустых строк выделяем один символ
                    try:
                        self.stdscr.chgat(screen_y, line_num_width, 1, curses.A_REVERSE)
                    except curses.error:
                        pass

        self.highlight_matching_brackets()
        self.stdscr.noutrefresh()  # вместо refresh()
        curses.doupdate()  # единый вызов в конце

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
            if key == curses.KEY_ENTER or key == 10 or key == 13:      # Enter
                self.handle_enter()
            elif key == curses.KEY_UP or key == 259 or key == 450:     # Up Arrow
                self.handle_up()
            elif key == curses.KEY_DOWN or key == 258 or key == 456:   # Down Arrow
                self.handle_down()
            elif key == curses.KEY_LEFT or key == 260 or key == 452:   # Left Arrow
                self.handle_left()
            elif key == curses.KEY_RIGHT or key == 261 or key == 454:   # Right Arrow
                self.handle_right()
            elif key == curses.KEY_BACKSPACE or key == 127 or key == 8:   # Backspace
                self.handle_backspace()
            elif key == curses.KEY_DC or key == 330 or key == 462:    # Delete
                self.handle_delete()
            elif key == curses.KEY_HOME or key == 262 or key == 449:   # Home
                self.handle_home()
            elif key == curses.KEY_END or key == 360 or key == 455:    # End
                self.handle_end()
            elif key == curses.KEY_PPAGE or key == 339 or key == 451:  # Page Up
                self.handle_page_up()
            elif key == curses.KEY_NPAGE or key == 338 or key == 457:  # Page Down
                self.handle_page_down()
            elif key == 9:                                              # Tab
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
            elif key == 337:                               # Shift+Page Up
                self.extend_selection_up()
            elif key == 336:                               # Shift+Page Down
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
        self.is_selecting = False  #сброс выделения при обычном движении курсора
        if self.cursor_y > 0:
            self.cursor_y -= 1
            self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))


    def handle_down(self):
        """Moves the cursor down by one line."""
        self.is_selecting = False  #сброс выделения при обычном движении курсора
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
        Calculates the display width of a character, accounting for full-width and half-width characters.
        """
        try:
            if ord(char) < 128:
                return 1
            width = unicodedata.east_asian_width(char)
            if width in ("F", "W"):
                return 2
            elif width == "A":
                return 2
            else:
                return 1
        except (UnicodeEncodeError, TypeError):
            return 1


    def open_file(self):
        """
        Opens a file with automatic encoding detection using chardet,
        or UTF-8 fallback if chardet is not available.
        """
        if self.modified:
            choice = self.prompt("Save changes? (y/n): ")
            if choice and choice.lower().startswith("y"):
                self.save_file()

        filename = self.prompt("Open file: ")
        if not self.validate_filename(filename):
            self.status_message = "Invalid filename"
            return
        if not filename:
            self.status_message = "Open cancelled"
            return

        try:
            with open(filename, "rb") as f:
                result = chardet.detect(f.read())
                self.encoding = result["encoding"] or "UTF-8"

            with open(filename, "r", encoding=self.encoding, errors="replace") as f:
                self.text = f.read().splitlines()
                if not self.text:
                    self.text = [""]
            self.filename = filename
            self.modified = False
            self.set_initial_cursor_position()
            self.status_message = f"Opened {filename} with encoding {self.encoding}"
            self.update_git_info()  # Обновляем Git-информацию при открытии файла
            curses.flushinp()
        except ImportError:
            try:
                with open(filename, "r", encoding="utf-8", errors="replace") as f:
                    self.text = f.read().splitlines()
                    if not self.text:
                        self.text = [""]
                self.filename = filename
                self.encoding = "UTF-8"
                self.modified = False
                self.set_initial_cursor_position()
                self.status_message = f"Opened {filename}"
                self.update_git_info()  # Обновляем Git-информацию при открытии файла
                curses.flushinp()
            except FileNotFoundError:
                self.status_message = f"File not found: {filename}"
                logging.error(f"File not found: {filename}")
            except OSError as e:
                self.status_message = f"Error opening file: {e}"
                logging.exception(f"Error opening file: {filename}")
            except Exception as e:
                self.status_message = f"Error opening file: {e}"
                logging.exception(f"Error opening file: {filename}")
        except FileNotFoundError:
            self.status_message = f"File not found: {filename}"
            logging.error(f"File not found: {filename}")
        except OSError as e:
            self.status_message = f"Error opening file: {e}"
            logging.exception(f"Error opening file: {filename}")
        except Exception as e:
            self.status_message = f"Error opening file: {e}"
            logging.exception(f"Error opening file: {filename}")
 

    def save_file(self):
        """
        Сохраняет файл.  Если имя ещё не задано – спрашивает «Save as:».
        После первого сохранения просто перезаписывает тот же файл.
        """
        # 1. имя ещё не задано? -> спрашиваем
        if not self.filename:                       # пустая строка
            new_name = self.prompt("Save as: ")
            if not new_name:
                self.status_message = "Save cancelled"
                return
            if not self.validate_filename(new_name):
                self.status_message = "Invalid filename"
                return
            self.filename = new_name                # имя подтвердили

        # 2. проверки существующего имени
        if os.path.isdir(self.filename):
            self.status_message = f"Cannot save: {self.filename} is a directory"
            return
        if os.path.exists(self.filename) and not os.access(self.filename, os.W_OK):
            self.status_message = f"No write permissions: {self.filename}"
            return

        # 3. запись файла
        try:
            with open(self.filename, "w", encoding=self.encoding, errors="replace") as f:
                f.write(os.linesep.join(self.text))
            self.modified = False
            self.status_message = f"Saved to {self.filename}"

            code = os.linesep.join(self.text)
            threading.Thread(target=self.run_lint_async, args=(code,), daemon=True).start()
            self.update_git_info()
        except Exception as e:
            self.status_message = f"Error saving file: {e}"
            logging.exception(f"Error saving file: {self.filename}")


    def save_file_as(self):
        """
        Сохраняет документ под новым именем.
        • Проверяет корректность имени через validate_filename().
        • Обновляет self.filename и флаг modified.
        • После записи запускает линтер в отдельном потоке.
        """
        new_filename = self.prompt("Save file as: ")
        if not new_filename:
            self.status_message = "Save cancelled"
            return

        # ── 1. Проверяем валидность имени ──────────────────────────────
        if not self.validate_filename(new_filename):
            self.status_message = "Invalid filename"
            return

        if os.path.isdir(new_filename):
            self.status_message = f"Cannot save: {new_filename} is a directory"
            return

        if os.path.exists(new_filename) and not os.access(new_filename, os.W_OK):
            self.status_message = f"No write permissions: {new_filename}"
            return

        # ── 2. Пытаемся записать файл ─────────────────────────────────
        try:
            with open(new_filename, "w", encoding=self.encoding, errors="replace") as f:
                f.write(os.linesep.join(self.text))

            self.filename = new_filename
            self.modified = False
            self.status_message = f"Saved as {new_filename}"

            # Линтинг в фоне
            code = os.linesep.join(self.text)
            threading.Thread(target=self.run_lint_async, args=(code,), daemon=True).start()

        except OSError as e:
            self.status_message = f"Error saving file: {e}"
            logging.exception(f"Error saving file: {new_filename}")
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
        Creates a new empty document, prompting the user to save changes if any.
        """
        if self.modified:
            choice = self.prompt("Save changes? (y/n): ")
            if choice and choice.lower().startswith("y"):
                self.save_file()

        try:
            self.text = [""]
            self.filename = ""           # тоже пусто
            self.modified = False
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
        else:
            self.status_message = "Cancelled"


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

        except Exception:
            logging.exception("Prompt error")
            buf = []          # считаем ввод отменённым

        finally:
            curses.flushinp()            # очистить буфер ввода
            curses.noecho()
            self.stdscr.nodelay(False)

        return "".join(buf).strip()


    # === ПОИСК ====================================================================

    def _collect_matches(self, term):
        """Возвращает список всех (row, col_start, col_end) для term (без учёта регистра)."""
        matches = []
        if not term:
            return matches
        low = term.lower()
        for row, line in enumerate(self.text):
            start = 0
            while True:
                idx = line.lower().find(low, start)
                if idx == -1:
                    break
                matches.append((row, idx, idx + len(term)))
                start = idx + len(term)
        return matches


    def find_prompt(self):
        """
        Запрашивает у пользователя строку поиска, выделяет все совпадения
        и переходит к первому.
        """
        term = self.prompt("Find: ")
        if term == "":
            self.status_message = "Search cancelled"
            return

        self.search_term    = term
        self.search_matches = self._collect_matches(term)
        if not self.search_matches:
            self.status_message = f"'{term}' not found"
            self.current_match_idx = -1
            return

        self.current_match_idx = 0
        self._goto_match(self.current_match_idx)
        self.status_message = f"Found {len(self.search_matches)} match(es)"


    def find_next(self):
        """
        Переходит к следующему совпадению (по циклу).
        """
        if not self.search_matches:
            # если пользователь нажал F3 до первого поиска
            self.find_prompt()
            return

        self.current_match_idx = (self.current_match_idx + 1) % len(self.search_matches)
        self._goto_match(self.current_match_idx)
        self.status_message = (
            f"Match {self.current_match_idx + 1}/{len(self.search_matches)}"
        )


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
        if os.path.isabs(filename):
            base_dir = os.path.dirname(os.path.abspath(filename))
            return os.path.commonpath([base_dir, os.getcwd()]) == os.getcwd()
        return True


    def execute_shell_command(self):
        """
        Запрашивает у пользователя команду, выполняет её без shell=True
        и выводит первые символы stdout/stderr в строку статуса.
        """
        command = self.prompt("Enter command: ")
        if not command:
            self.status_message = "Command cancelled"
            return

        # разбиваем строку на аргументы (учитывает кавычки, экранирование)
        try:
            cmd_list = shlex.split(command)
        except ValueError as e:
            self.status_message = f"Parse error: {e}"
            return

        # блокируем curses-экран, чтобы вывести результат после выполнения
        try:
            curses.def_prog_mode()
            curses.endwin()

            # запускаем без shell=True
            process = subprocess.Popen(
                cmd_list,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            output, error = process.communicate(timeout=30)

        except subprocess.TimeoutExpired:
            self.status_message = "Command timed out"
            return
        except FileNotFoundError:
            self.status_message = f"Executable not found: {cmd_list[0]}"
            return
        except Exception as e:
            self.status_message = f"Exec error: {e}"
            return
        finally:
            # возвращаемся в curses-режим
            curses.reset_prog_mode()
            self.stdscr.refresh()

        # выводим результат
        if error and error.strip():
            self.status_message = f"Error: {error[:50]}..."
        else:
            self.status_message = f"Command executed: {output[:50]}..."

    
    def integrate_git(self):
        """
        Меню Git вызывается клавишей F2.
        """
        commands = {
            "1": ("status", "git status"),
            "2": ("commit", None),          # формируем динамически
            "3": ("push",   "git push"),
            "4": ("pull",   "git pull"),
            "5": ("diff",   "git diff"),
        }

        # однострочное приглашение — никаких \n
        opts = " ".join(f"{k}:{v[0]}" for k, v in commands.items())
        choice = self.prompt(f"Git menu [{opts}] → ")

        if choice not in commands:
            self.status_message = "Invalid choice"
            return

        if choice == "2":                               # commit
            msg = self.prompt("Commit message: ")
            if not msg:
                self.status_message = "Commit cancelled"
                return
            cmd = ["git", "commit", "-am", msg]         # список аргументов
        else:
            cmd = commands[choice][1].split()           # "git status" → ["git","status"]

        try:
            curses.def_prog_mode()
            curses.endwin()
            proc = safe_run(cmd)                     
            curses.reset_prog_mode()
            self.stdscr.refresh()

            if proc.returncode == 0:
                self.status_message = f"Git {commands[choice][0]} successful"
                self.update_git_info()        # перечитать ветку / счётчик
            else:
                self.status_message = f"Git error: {proc.stderr.strip()[:120]}"

        except FileNotFoundError:
            self.status_message = "Git не установлен или не найден в PATH"
        except Exception as e:
            self.status_message = f"Git error: {e}"


    def goto_line(self):
        """
        Moves the cursor to the specified line number within the document.
        """
        line_num = self.prompt("Go to line: ")
        try:
            line_num = int(line_num)
            if 1 <= line_num <= len(self.text):
                self.cursor_y = line_num - 1
                self.cursor_x = 0
                height = self.stdscr.getmaxyx()[0]
                if self.cursor_y < self.scroll_top:
                    self.scroll_top = max(0, self.cursor_y - height // 2)
                elif self.cursor_y >= self.scroll_top + height - 2:
                    self.scroll_top = min(
                        len(self.text) - height + 2, self.cursor_y - height // 2
                    )
            else:
                self.status_message = f"Line number out of range (1-{len(self.text)})"
        except ValueError:
            self.status_message = "Invalid line number"


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

# !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    def toggle_auto_save(self):
        """ TODO: Enables or disables auto-save functionality."""
        self.auto_save = getattr(self, "auto_save", False)
        self.auto_save = not self.auto_save

        if self.auto_save:

            def auto_save_thread():
                while self.auto_save:
                    time.sleep(60)
                    if self.modified:
                        self.save_file()

            threading.Thread(target=auto_save_thread, daemon=True).start()
            self.status_message = "Auto-save enabled"
        else:
            self.status_message = "Auto-save disabled"


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
            "  Licensed under the Apache License 2.0",
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
        try:
            prev_vis = curses.curs_set(0)   # 0 = invisible, 1/2 = visible
        except curses.error:
            prev_vis = 1                     # если терминал не поддерживает

        # ждём Esc
        while True:
            ch = win.getch()
            if ch == 27:                     # Esc
                break

        # ★ возвращаем курсор
        try:
            curses.curs_set(prev_vis)
        except curses.error:
            pass

        del win
        self.draw_screen()

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
