#!/usr/bin/env python3
# Sway-Pad is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
import curses
import locale
import toml
import os
import io
import re
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
from flake8.api import legacy as flake8


# Установка кодировки по умолчанию
locale.setlocale(locale.LC_ALL, "")
# Установка кодировки по умолчанию
if sys.platform == "win32":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONLEGACYWINDOWSSTDIO"] = "1"
# Установка кодировки по умолчанию
if sys.platform == "linux":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONLEGACYWINDOWSSTDIO"] = "1"
# Установка кодировки по умолчанию
if sys.platform == "darwin":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONLEGACYWINDOWSSTDIO"] = "1"
# Установка кодировки по умолчанию
if sys.platform == "cygwin":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONLEGACYWINDOWSSTDIO"] = "1"
# Установка кодировки по умолчанию
if sys.platform == "aix":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONLEGACYWINDOWSSTDIO"] = "1"
# Установка кодировки по умолчанию   FreeBSD
if sys.platform == "freebsd":
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONLEGACYWINDOWSSTDIO"] = "1"



def run_flake8_on_code(code_string, filename="<buffer>"):
    """
    Запускает Flake8 на code_string через subprocess, возвращает список строк с сообщениями.
    """
    # Ограничение, если боитесь больших файлов
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
            capture_output=True,   # Захватываем stdout/err, чтобы не ломать curses
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



# Обработка ошибок:
# Если возникла ошибка, выводим сообщение в статус-бар
# и очищаем историю действий
# (или выполняем другие действия по вашему усмотрению)
# Например:
# Или выводим сообщение в статус-бар
# self.status_message = f"Error: {str(e)}" 
# self.action_history.clear()
# self.undone_actions.clear()  # Сброс отменённых действий

# Или просто логируем
# logging.error(f"Error handling character input: {str(e)}")
# logging.error(traceback.format_exc())  # Полный трейсбек ошибки
# Вывод информации в логи 
logging.basicConfig(
    filename="editor.log",
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)",
    force=True,  # ВАЖНО!
)

def deep_merge(base: dict, override: dict) -> dict:
    """
    Recursively merges 'override' dict into 'base' dict, returning a new dict.
    """
    result = dict(base)  # создаём копию, чтобы не портить оригинал
    for k, v in override.items():
        if (
            k in result
            and isinstance(result[k], dict)
            and isinstance(v, dict)
        ):
            # Рекурсивно мерджим вложенные словари
            result[k] = deep_merge(result[k], v)
        else:
            # Если ключ не словарь или отсутствует, заменяем/добавляем
            result[k] = v
    return result


def load_config() -> dict:
    """
    Loads configuration from 'config.toml', falling back to minimal defaults if not found or invalid.
    """
    minimal_default = {
        "colors": {"error": "red", "status": "bright_white"},
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
        }
    }

    try:
        # Читаем config.toml
        with open("config.toml", "r", encoding="utf-8") as f:
            file_content = f.read()
            # Парсим TOML
            user_config = toml.loads(file_content)
            # Сливаем с минимальным дефолтом (если нужно)
            final_config = deep_merge(minimal_default, user_config)
            return final_config
    except FileNotFoundError:
        logging.warning("Config file 'config.toml' not found. Using minimal defaults.")
        return minimal_default
    except toml.TomlDecodeError as e:
        logging.error(f"TOML parse error: {str(e)}")
        logging.error("Falling back to minimal defaults.")
        return minimal_default

   
def get_file_icon(filename: str, config: dict) -> str:
    """
    Returns the icon for a file based on its extension.
    """
    file_lower = filename.lower()

    # Здесь предполагается, что в TOML у вас есть секции "file_icons" и "supported_formats".
    # Если их нет – пропишите в config.toml.
    if "file_icons" not in config or "supported_formats" not in config:
        return "📝"  # fallback

    for key, icon in config["file_icons"].items():
        extensions = config["supported_formats"].get(key, [])
        # Проверяем, заканчивается ли файл одним из расширений
        if file_lower.endswith(tuple(ext.lower() for ext in extensions)):
            return icon

    return config["file_icons"].get("text", "📝")


class SwayEditor:
    """
    Main class for the Sway editor.
    """
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.stdscr.keypad(True)  # Включить режим клавиш
        
        curses.raw()  # Режим raw для лучшей обработки клавиш
        #curses.cbreak()  # Иногда curses.raw() подавляет некоторые сигналы и искажает ввод. Можно заменить:
        #curses.cbreak()  # Включить режим cbreak для обработки клавиш
        
        curses.nonl()  # Не переводить Enter
        curses.noecho()  # Не отображать ввод пользователя
        self.config = load_config()  # Загрузить конфигурацию из config.toml
        self.text = [""]
        self.cursor_x = 0
        self.cursor_y = 0
        self.scroll_top = 0
        self.scroll_left = 0
        self.filename = "new_file.py"
        self.modified = False
        self.encoding = "UTF-8"
        self.stdscr.nodelay(False)
        self.selection_start = None
        self.selection_end = None
        self.is_selecting = False
        locale.setlocale(locale.LC_ALL, "")
        curses.start_color()
        curses.use_default_colors()
        curses.curs_set(1)
        self.insert_mode = True
        self.syntax_highlighting = {}
        self.status_message = ""
        self.action_history = []
        self.undone_actions = []
        self.internal_clipboard = "" # Внутренний буфер обмена внутренний «клипборд» без pyperclip
        

        # TODO:Разобраться
        # Обновить привязки клавиш
        self.keybindings = {
            "delete": self.parse_key(self.config["keybindings"].get("delete", "del")),
            "paste": self.parse_key(self.config["keybindings"].get("paste", "ctrl+v")),
            "copy": self.parse_key(self.config["keybindings"].get("copy", "ctrl+c")),
            "cut": self.parse_key(self.config["keybindings"].get("cut", "ctrl+x")),
            "undo": self.parse_key(self.config["keybindings"].get("undo", "ctrl+z")),
            "open_file": self.parse_key(self.config["keybindings"].get("open_file", "ctrl+o")),
            "save_file": self.parse_key(self.config["keybindings"].get("save_file", "ctrl+s")),
            "select_all": self.parse_key(self.config["keybindings"].get("select_all", "ctrl+a")),
            "quit": self.parse_key(self.config["keybindings"].get("quit", "ctrl+q")),      
            "redo": self.parse_key(self.config["keybindings"].get("redo", "ctrl+shift+z")),          
            "extend_selection_right": curses.KEY_SRIGHT,
            "extend_selection_left": curses.KEY_SLEFT,
            "select_to_home": curses.KEY_SHOME,
            "select_to_end": curses.KEY_SEND, 
            "extend_selection_up": 337,    # Shift + Arrow Up
            "extend_selection_down": 336, # Shift + Arrow Down

        }

        # Настроить карту действий
        self.action_map = {
            "copy": self.copy,
            "paste": self.paste,
            "cut": self.cut,
            "undo": self.undo,
            "extend_selection_right": self.extend_selection_right,
            "extend_selection_left": self.extend_selection_left,
            "select_to_home": self.select_to_home,
            "select_to_end": self.select_to_end,
            "select_all": self.select_all,
            "open_file": lambda: print("Open file"),  # Заглушка
            "save_file": lambda: print("Save file"),  # Заглушка
            "quit": lambda: print("Quit"),          # Заглушка
            "redo": self.redo,
            "extend_selection_up": self.extend_selection_up,    # Добавляем новые методы
            "extend_selection_down": self.extend_selection_down,
            "select_all": self.select_all,
        }


        # Другие инициализации
        self.init_colors()
        self.load_syntax_highlighting()
        self.set_initial_cursor_position()


    def get_selected_text(self):
        if not self.is_selecting or self.selection_start is None or self.selection_end is None:
            return ""
        start_row, start_col = self.selection_start
        end_row, end_col = self.selection_end
        if start_row == end_row:
            return self.text[start_row][min(start_col, end_col):max(start_col, end_col)]
        else:
            selected_lines = []
            selected_lines.append(self.text[start_row][start_col:])
            for row in range(start_row + 1, end_row):
                selected_lines.append(self.text[row])
            selected_lines.append(self.text[end_row][:end_col])
            return "\n".join(selected_lines)


    def delete_selected_text(self):
        if not self.is_selecting or self.selection_start is None or self.selection_end is None:
            return
        start_row, start_col = self.selection_start
        end_row, end_col = self.selection_end
        
        # Обмен местами, если начало после конца
        if start_row > end_row or (start_row == end_row and start_col > end_col):
            start_row, end_row = end_row, start_row
            start_col, end_col = end_col, start_col
        
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
            text = self.text[start_row]
            self.text[start_row] = text[:start_col] + text[end_col:]
            self.cursor_x = start_col
        else:
            # Удаляем многострочный текст
            self.text[start_row] = self.text[start_row][:start_col] + self.text[end_row][end_col:]
            del self.text[start_row + 1:end_row + 1]
            self.cursor_y = start_row
            self.cursor_x = start_col
        
        self.selection_start = None
        self.selection_end = None
        self.is_selecting = False
        self.modified = True


    def copy(self):
        selected_text = self.get_selected_text()
        pyperclip.copy(selected_text)
        self.status_message = "Copied to clipboard"

    """ OLD CUT
    def cut(self):
        selected_text = self.get_selected_text()
        logging.debug(f"[CUT] to clipboard => '{selected_text}'")
        pyperclip.copy(selected_text)
        self.delete_selected_text()
        self.status_message = "Cut to clipboard"
    """
    def cut(self):
        self.internal_clipboard = self.get_selected_text()
        self.delete_selected_text()
        self.status_message = "Cut to internal clipboard"


    """ OLD PASTE
    def paste(self):
        text = pyperclip.paste()
        logging.debug(f"[PASTE] from clipboard => '{text}'")
        if self.is_selecting:
            self.delete_selected_text()
        self.insert_text(text)
        self.status_message = "Pasted from clipboard"
    """
    def paste(self):
        if self.is_selecting:
            self.delete_selected_text()
        self.insert_text(self.internal_clipboard or "")
        self.status_message = "Pasted from internal clipboard"

    
    def paste(self):
        text = self.internal_clipboard
        if self.is_selecting:
            self.delete_selected_text()
        self.insert_text(text)
        self.status_message = "Pasted from internal clipboard"

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
            self.cursor_y = start_row
            self.cursor_x = start_col
        
        self.undone_actions.append(last_action)
        self.modified = True
        self.is_selecting = False  # Сбрасываем выделение
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

        Args:
            line (str): The code line to highlight.

        Returns:
            list of tuples: Each tuple contains a substring and its associated curses color.
        """
        try:
            if self.filename and self.filename != "noname":
                lexer = get_lexer_by_name(self.detect_language())
            else:
                lexer = guess_lexer(line)
        except Exception:
            lexer = TextLexer()

        tokens = list(lex(line, lexer))

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
        return highlighted


    def run_lint_async(self, code):
        """
        Запускает линтинг в отдельном потоке (раньше pylint, теперь Flake8).
        """
        lint_results = run_flake8_on_code(code, self.filename)

        if not lint_results:
            # Если функция умолчит и вернет пустой список (вдруг такое случится)
            self.status_message = f"No issues found in {self.filename}"
            return

        # Предположим, если в результатах ровно одна строка, начинающаяся с "Flake8: No issues found."
        if len(lint_results) == 1 and lint_results[0].startswith("Flake8: No issues found."):
            self.status_message = f"No issues found in {self.filename}"
        else:
            # Иначе берём первые две строки
            short_info = " | ".join(lint_results[:2])
            self.status_message = short_info


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
        self.stdscr.clear()
        height, width = self.stdscr.getmaxyx()
        if height < 24 or width < 80:
            try:
                self.stdscr.addstr(
                    0, 0, "Window too small (min: 80x24)", self.colors["error"]
                )
                self.stdscr.refresh()
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

        visible_lines = height - 2
        if self.cursor_y < self.scroll_top:
            self.scroll_top = self.cursor_y
        elif self.cursor_y >= self.scroll_top + visible_lines:
            self.scroll_top = self.cursor_y - visible_lines + 1

        for screen_row in range(visible_lines):
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
            status_msg = (
                f"File: {self.filename} | "
                f"Type: {file_type} | "
                f"Encoding: {self.encoding} | "
                f"Line: {self.cursor_y + 1}/{len(self.text)} | "
                f"Column: {self.cursor_x + 1} | "
                f"Mode: {'Insert' if self.insert_mode else 'Replace'}"
            )
            self.stdscr.addstr(status_y, 0, " " * (width - 1), self.colors["status"])
            self.stdscr.addstr(status_y, 0, status_msg, self.colors["status"])
        except curses.error:
            pass

        cursor_screen_y = self.cursor_y - self.scroll_top
        cursor_screen_x = self.cursor_x - self.scroll_left + line_num_width
        if 0 <= cursor_screen_y < visible_lines and 0 <= cursor_screen_x < width:
            try:
                self.stdscr.move(cursor_screen_y, cursor_screen_x)
            except curses.error:
                pass

        # Отрисовка курсора
        # После отрисовки основного текста добавляем:
        if self.is_selecting and self.selection_start and self.selection_end:
            start_y, start_x = self.selection_start
            end_y, end_x = self.selection_end
            
            # Обмен местами если начало после конца
            if start_y > end_y or (start_y == end_y and start_x > end_x):
                start_y, end_y = end_y, start_y
                start_x, end_x = end_x, start_x
            
            for y in range(start_y, end_y+1):
                line = self.text[y]
                if y < self.scroll_top or y >= self.scroll_top + visible_lines:
                    continue
                screen_y = y - self.scroll_top
                if y == start_y and y == end_y:
                    # Одна строка
                    for x in range(start_x, end_x):
                        if x >= self.scroll_left and x < self.scroll_left + text_width:
                            self.stdscr.chgat(screen_y, x - self.scroll_left + line_num_width, 
                                            1, curses.A_REVERSE)
                #######################        
                else:
                    # Несколько строк
                    if y == start_y:
                        for x in range(start_x, len(line)):
                            if x >= self.scroll_left and x < self.scroll_left + text_width:
                                self.stdscr.chgat(
                                    screen_y,
                                    x - self.scroll_left + line_num_width,
                                    1,
                                    curses.A_REVERSE
                                )
                    elif y == end_y:
                        for x in range(0, end_x):
                            if x >= self.scroll_left and x < self.scroll_left + text_width:
                                self.stdscr.chgat(
                                    screen_y,
                                    x - self.scroll_left + line_num_width,
                                    1,
                                    curses.A_REVERSE
                                )
                    else:
                        # Полностью выделенная строка
                        # Проверим, пустая ли строка:
                        if len(line) == 0:
                            # Если строка вообще пустая, выделим хотя бы один символ
                            # Обычно можно «выделить» позицию 0 шириной 1 символ
                            # Либо можно пропустить, если хотите «не делать» на пустой строке
                            self.stdscr.chgat(screen_y, line_num_width, 1, curses.A_REVERSE)
                        else:
                            # Вычисляем видимую часть
                            line_len = len(line)
                            visible_line_length = max(
                                0, min(line_len - self.scroll_left, text_width)
                            )
                            self.stdscr.chgat(
                                screen_y,
                                line_num_width,       # Начинаем после нумерации
                                visible_line_length,  # Количество символов в видимом диапазоне
                                curses.A_REVERSE
                            )

                self.highlight_matching_brackets()
                self.stdscr.refresh()


    def detect_language(self):
        """Detects the file's language based on its extension."""
        ext = os.path.splitext(self.filename)[1].lower()
        logging.debug(f"Detecting language for extension: {ext}")
        for lang, exts in self.config.get("supported_formats", {}).items():
            if ext in exts:
                logging.debug(f"Detected language: {lang}")
                return lang
        logging.debug("No language detected, defaulting to 'text'")
        return "text"


    def handle_input(self, key):
        """
        Handles keyboard input, including special commands (open, save, etc.)
        and text entry.
        """
        logging.debug(f"Key pressed: {key}")
        logging.debug(f"Char input: key={key}, char={chr(key)}")
        logging.debug(f"Cursor position: ({self.cursor_y}, {self.cursor_x})")
        logging.debug(f"Text length: {len(self.text)}")
        logging.debug(f"Text content: {self.text}")
        logging.debug(f"Selection start: {self.selection_start}")
        logging.debug(f"Selection end: {self.selection_end}")
        logging.debug(f"Is selecting: {self.is_selecting}")
        logging.debug(f"Action history: {self.action_history}")
        logging.debug(f"Undone actions: {self.undone_actions}")
        logging.debug(f"Status message: {self.status_message}")
        logging.debug(f"Keybindings: {self.keybindings}")

        try:
            # Special keys
            if key == curses.KEY_ENTER or key == 10 or key == 13:  # Enter
                self.handle_enter()
            elif key == curses.KEY_UP or key == 259 or key == 450:  # Up
                self.handle_up()
            elif key == curses.KEY_DOWN or key == 258 or key == 456:  # Down
                self.handle_down()
            elif key == curses.KEY_LEFT or key == 260 or key == 452:  # Left
                self.handle_left()
            elif key == curses.KEY_RIGHT or key == 261 or key == 454:  # Right
                self.handle_right()
            elif key == curses.KEY_BACKSPACE or key == 127 or key == 8:  # Backspace
                self.handle_backspace()
            elif key == curses.KEY_DC or key == 330 or key == 462:  # Delete
                self.handle_delete()
            elif key == curses.KEY_HOME or key == 262 or key == 449:  # Home
                self.handle_home()
            elif key == curses.KEY_END or key == 360 or key == 455:  # End
                self.handle_end()
            elif key == curses.KEY_PPAGE or key == 339 or key == 451:  # Page Up
                self.handle_page_up()
            elif key == curses.KEY_NPAGE or key == 338 or key == 457:  # Page Down
                self.handle_page_down()
            elif key == 9:  # Tab
                self.handle_tab()
            elif key == 27:  # Escape
                self.handle_escape()
            # Function keys and other special keys
            elif key == self.keybindings["quit"]:           # Quit (Ctrl+Q)
                self.exit_editor()
            elif key == self.keybindings["save_file"]:      # Save (Ctrl+S)
                self.save_file()
            elif key == self.keybindings["open_file"]:      # Open (Ctrl+O)
                self.open_file()
            elif key == self.keybindings["copy"]:           # Copy (Ctrl+C)
                self.copy()
            elif key == self.keybindings["cut"]:            # Cut (Ctrl+X)
                self.cut()
            elif key == self.keybindings["paste"]:          # Paste (Ctrl+V)
                self.paste()
            elif key == self.keybindings["undo"]:           # Undo (Ctrl+Z)
                self.undo()
            elif key == self.keybindings["redo"]:           # Redo (Ctrl+Shift+Z)
                self.redo()

            # Regular character input
            elif key >= 32 and key <= 255:                  # Printable ASCII
                self.handle_char_input(key)                 
            elif key == self.keybindings["select_all"]:     # Select All (Ctrl+A)
                self.select_all()
            elif key == curses.KEY_SRIGHT:                  # Shift + Right
                self.extend_selection_right()
            elif key == curses.KEY_SLEFT:                   # Shift + Left
                self.extend_selection_left()
            elif key == curses.KEY_SHOME:                   # Shift + Home
                self.select_to_home()
            elif key == curses.KEY_SEND:                    # Shift + End
                self.select_to_end()
            elif key == 337:                                # Shift + Arrow Up
                self.extend_selection_up()
            elif key ==336:                                 # Shift + Arrow Down
                self.extend_selection_down()

        except Exception as e:
            self.status_message = f"Input error: {str(e)}"
            logging.exception("Error handling input")


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

#=======================  start new =====================

    def handle_delete(self):
        """Удаляет выделенный текст, если он есть, иначе удаляет символ под курсором или объединяет со следующей строкой."""
        if hasattr(self, 'is_selecting') and self.is_selecting and hasattr(self, 'selection_start') and hasattr(self, 'selection_end'):
            start_row, start_col = self.selection_start
            end_row, end_col = self.selection_end
            
            # Нормализация: убедимся, что начало перед концом
            if start_row > end_row or (start_row == end_row and start_col > end_col):
                start_row, start_col, end_row, end_col = end_row, end_col, start_row, start_col
            
            if start_row == end_row:  # Одна строка
                line = self.text[start_row]
                self.text[start_row] = line[:start_col] + line[end_col:]
                self.cursor_x = start_col  # Перемещаем курсор в начало удаленного диапазона
            else:  # Многострочный случай
                # Первая строка: оставляем до start_col
                self.text[start_row] = self.text[start_row][:start_col]
                # Последняя строка: добавляем от end_col
                self.text[start_row] += self.text[end_row][end_col:]
                # Удаляем средние строки
                for i in range(start_row + 1, end_row + 1):
                    self.text.pop(start_row + 1)
                self.cursor_y = start_row
                self.cursor_x = start_col
            
            # Сбрасываем состояние выделения
            self.is_selecting = False
            self.selection_start = self.selection_end = None
            self.modified = True
        else:
            # Исходное поведение
            if self.cursor_x < len(self.text[self.cursor_y]):
                line = self.text[self.cursor_y]
                self.text[self.cursor_y] = line[:self.cursor_x] + line[self.cursor_x + 1:]
                self.modified = True
            elif self.cursor_y < len(self.text) - 1:
                self.text[self.cursor_y] += self.text.pop(self.cursor_y + 1)
                self.modified = True



    def handle_backspace(self):
        """Удаляет выделенный текст, если он есть, иначе удаляет символ слева от курсора или объединяет со предыдущей строкой."""
        if hasattr(self, 'is_selecting') and self.is_selecting and hasattr(self, 'selection_start') and hasattr(self, 'selection_end'):
            start_row, start_col = self.selection_start
            end_row, end_col = self.selection_end
            
            # Нормализация: убедимся, что начало перед концом
            if start_row > end_row or (start_row == end_row and start_col > end_col):
                start_row, start_col, end_row, end_col = end_row, end_col, start_row, start_col
            
            if start_row == end_row:  # Одна строка
                line = self.text[start_row]
                self.text[start_row] = line[:start_col] + line[end_col:]
                self.cursor_x = start_col  # Перемещаем курсор в начало
            else:  # Многострочный случай
                # Первая строка: оставляем до start_col
                self.text[start_row] = self.text[start_row][:start_col]
                # Последняя строка: добавляем от end_col
                self.text[start_row] += self.text[end_row][end_col:]
                # Удаляем средние строки
                for i in range(start_row + 1, end_row + 1):
                    self.text.pop(start_row + 1)
                self.cursor_y = start_row
                self.cursor_x = start_col
            
            # Сбрасываем состояние выделения
            self.is_selecting = False
            self.selection_start = self.selection_end = None
            self.modified = True
        else:
            # Исходное поведение
            if self.cursor_x > 0:
                line = self.text[self.cursor_y]
                self.text[self.cursor_y] = line[:self.cursor_x - 1] + line[self.cursor_x:]
                self.cursor_x -= 1
                self.modified = True
            elif self.cursor_y > 0:
                self.cursor_y -= 1
                self.cursor_x = len(self.text[self.cursor_y])
                self.text[self.cursor_y] += self.text.pop(self.cursor_y + 1)
                self.modified = True



# end new ==================================================





#-------------------------------------------start -- old
    """
    #def handle_delete(self):
    #Deletes the character under the cursor or joins with the next line if at end.
       if self.cursor_x < len(self.text[self.cursor_y]):
            line = self.text[self.cursor_y]
            self.text[self.cursor_y] = line[: self.cursor_x] + line[self.cursor_x + 1 :]
            self.modified = True
        elif self.cursor_y < len(self.text) - 1:
            self.text[self.cursor_y] += self.text.pop(self.cursor_y + 1)
            self.modified = True
        

    #def handle_backspace(self):
        Deletes the character to the left of the cursor or joins with the previous line.     
        if self.cursor_x > 0:
            line = self.text[self.cursor_y]
            self.text[self.cursor_y] = line[: self.cursor_x - 1] + line[self.cursor_x :]
            self.cursor_x -= 1
            self.modified = True
        elif self.cursor_y > 0:
            self.cursor_y -= 1
            self.cursor_x = len(self.text[self.cursor_y])
            self.text[self.cursor_y] += self.text.pop(self.cursor_y + 1)
            self.modified = True 
    """
#-----------------------------------------------end----old----------------------        

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
        Inserts indentation matching the previous line if the cursor is at the start.
        Otherwise falls back to normal tab insertion.
        """
        if self.cursor_y > 0:
            prev_line = self.text[self.cursor_y - 1]
            leading_space_match = re.match(r"^(\s*)", prev_line)
            if leading_space_match:
                leading_space = leading_space_match.group(1)
                if self.cursor_x == 0:
                    self.text[self.cursor_y] = leading_space + self.text[self.cursor_y]
                    self.cursor_x = len(leading_space)
                    self.modified = True
                    return

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




#---------------------------------------------------------------------------------





    def handle_enter(self):
        """Handles the Enter key, creating a new line at the cursor position."""
        self.text.insert(self.cursor_y + 1, "")
        content = self.text[self.cursor_y][self.cursor_x :]
        self.text[self.cursor_y] = self.text[self.cursor_y][: self.cursor_x]
        self.text[self.cursor_y + 1] = content
        self.cursor_y += 1
        self.cursor_x = 0
        self.modified = True


#-----  Parse_key--------------------------------------start -- old
    """old
    #def parse_key(self, key_str):
        
        #Converts a hotkey description string into the corresponding key code.
        
        logging.debug(f"[parse_key] Received: '{key_str}'")

        if not key_str:
            logging.debug("[parse_key] Empty key_str")
            return -1

        key_str = key_str.strip().lower()
        parts = key_str.split("+")
        logging.debug(f"[parse_key] Parts: {parts}")

                 
        if len(parts) == 2 and parts[0] == "ctrl":
            if parts[1].isalpha() and len(parts[1]) == 1:
                return ord(parts[1]) - ord('a') + 1  # Ctrl+A = 1, ..., Ctrl+Z = 26
            elif parts[1] == "space":
                return ord(' ') & 0x1f  # Ctrl+Space, often 0
            elif parts[1] == "tab":
                return ord('\t') & 0x1f  # Ctrl+Tab, often 9
            return -1
        elif len(parts) == 3 and parts[0] == "ctrl" and parts[1] == "shift":
            if parts[2].isalpha() and len(parts[2]) == 1:
                base = ord(parts[2]) - ord('a') + 1
                if hasattr(curses, 'SHIFT_MASK'):
                    return base | curses.SHIFT_MASK
                return base
            return -1
        elif key_str in ("del", "delete"):  # Handle both "del" and "delete"
            return curses.KEY_DC
        elif key_str == "space":
            return ord(' ')
        elif key_str == "tab":
            return ord('\t')
        elif key_str == "enter":
            return ord('\n')
        try:
            return ord(key_str)
        except TypeError:
            return -1
    """

    def parse_key(self, key_str):
        
        # Converts a hotkey description string into the corresponding key code.
    
        logging.debug(f"[parse_key] Received: '{key_str}'")

        if not key_str:
            logging.debug("[parse_key] Empty key_str")
            return -1

        key_str = key_str.strip().lower()
        parts = key_str.split("+")
        logging.debug(f"[parse_key] Parts: {parts}")

        try:
            if len(parts) == 2 and parts[0] == "ctrl":
                if parts[1].isalpha() and len(parts[1]) == 1:
                    result = ord(parts[1]) - ord('a') + 1
                    logging.debug(f"[parse_key] Ctrl+{parts[1].upper()} → {result}")
                    return result
                elif parts[1] == "space":
                    logging.debug("[parse_key] Ctrl+Space → 0")
                    return ord(' ') & 0x1f
                elif parts[1] == "tab":
                    logging.debug("[parse_key] Ctrl+Tab → 9")
                    return ord('\t') & 0x1f

            elif len(parts) == 3 and parts[0] == "ctrl" and parts[1] == "shift":
                if parts[2].isalpha() and len(parts[2]) == 1:
                    base = ord(parts[2]) - ord('a') + 1
                    logging.debug(f"[parse_key] Ctrl+Shift+{parts[2].upper()} → {base}")
                    return base

            elif key_str in ("del", "delete"):
                logging.debug("[parse_key] Delete key detected → KEY_DC")
                return curses.KEY_DC

            elif key_str == "space":
                logging.debug("[parse_key] Space key → 32")
                return ord(' ')

            elif key_str == "tab":
                logging.debug("[parse_key] Tab key → 9")
                return ord('\t')

            elif key_str == "enter":
                logging.debug("[parse_key] Enter key → 10")
                return ord('\n')

            result = ord(key_str)
            logging.debug(f"[parse_key] Fallback: ord('{key_str}') → {result}")
            return result
        except Exception as e:
            logging.exception(f"[parse_key] Error parsing key: {key_str}")
            logging.debug(f"[parse_key] Exception: {e}")
            return -1



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
        Saves the file. If no filename is set, prompts for one.
        After saving, runs pylint in a separate thread.
        """
        if self.filename == "noname":
            self.filename = self.prompt("Save as: ")
            if not self.filename:
                self.status_message = "Save cancelled"
                return

        if os.path.isdir(self.filename):
            self.status_message = f"Cannot save: {self.filename} is a directory"
            return

        if os.path.exists(self.filename):
            if not os.access(self.filename, os.W_OK):
                self.status_message = f"No write permissions: {self.filename}"
                return
        try:
            with open(self.filename, "w", encoding=self.encoding, errors="replace") as f:
                f.write(os.linesep.join(self.text))
            self.modified = False
            self.status_message = f"Saved to {self.filename}"
            code = os.linesep.join(self.text)
            threading.Thread(
                target=self.run_lint_async, args=(code,), daemon=True
            ).start()
        except OSError as e:
            self.status_message = f"Error saving file: {e}"
            logging.exception(f"Error saving file: {self.filename}")
        except Exception as e:
            self.status_message = f"Error saving file: {e}"
            logging.exception(f"Error saving file: {self.filename}")


    def save_file_as(self):
        """
        Saves the file under a new name, updates self.filename,
        resets modification flag, and runs pylint asynchronously.
        """
        new_filename = self.prompt("Save file as: ")
        if not new_filename:
            self.status_message = "Save cancelled"
            return

        if os.path.isdir(new_filename):
            self.status_message = f"Cannot save: {new_filename} is a directory"
            return

        if os.path.exists(new_filename):
            if not os.access(new_filename, os.W_OK):
                self.status_message = f"No write permissions: {new_filename}"
                return

        try:
            with open(new_filename, "w", encoding=self.encoding, errors="replace") as f:
                f.write(os.linesep.join(self.text))
            self.filename = new_filename
            self.modified = False
            self.status_message = f"Saved as {new_filename}"
            code = os.linesep.join(self.text)
            threading.Thread(
                target=self.run_lint_async, args=(code,), daemon=True
            ).start()
        except (OSError, Exception) as e:
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
            self.filename = "noname"
            self.modified = False
            self.set_initial_cursor_position()
            self.status_message = "New file created"
        except Exception as e:
            self.status_message = f"Error creating new file: {e}"
            logging.exception("Error creating new file")


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


    def handle_escape(self):
        """Handles the Escape key."""
        if self.modified:
            choice = self.prompt("Save changes before exit? (y/n): ")
            if choice and choice.lower().startswith("y"):
                self.save_file()
        self.exit_editor()


    def prompt(self, message):
        """
        Displays a message to the user, captures text input from the keyboard,
        and returns the string entered.
        """
        self.stdscr.nodelay(False)
        curses.echo()
        try:
            self.stdscr.addstr(curses.LINES - 1, 0, message)
            self.stdscr.clrtoeol()
            self.stdscr.refresh()
            response = (
                self.stdscr.getstr(curses.LINES - 1, len(message), 1024)
                .decode("utf-8", errors="replace")
                .strip()
            )
        except Exception:
            response = ""
            logging.exception("Prompt error")
        finally:
            curses.noecho()
            self.stdscr.nodelay(False)
        return response


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
        Executes a shell command entered by the user, displaying
        partial output or error in the status bar.
        """
        command = self.prompt("Enter command: ")
        if not command:
            self.status_message = "Command cancelled"
            return

        try:
            curses.def_prog_mode()
            curses.endwin()
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            output, error = process.communicate(timeout=30)
            curses.reset_prog_mode()
            self.stdscr.refresh()

            if error:
                self.status_message = f"Error: {error[:50]}..."
            else:
                self.status_message = f"Command executed: {output[:50]}..."
        except subprocess.TimeoutExpired:
            self.status_message = "Command timed out"
        except Exception as e:
            self.status_message = f"Error executing command: {str(e)}"


    def integrate_git(self):
        """
        Provides simple integration with Git, allowing the user
        to select various commands.
        """
        commands = {
            "1": ("status", "git status"),
            "2": ("commit", "git commit -a"),
            "3": ("push", "git push"),
            "4": ("pull", "git pull"),
            "5": ("diff", "git diff"),
        }

        menu = "\n".join([f"{k}: {v[0]}" for k, v in commands.items()])
        choice = self.prompt(f"Select Git command:\n{menu}\nChoice: ")

        if choice in commands:
            try:
                curses.def_prog_mode()
                curses.endwin()
                process = subprocess.run(
                    commands[choice][1], shell=True, text=True, capture_output=True
                )
                curses.reset_prog_mode()
                self.stdscr.refresh()

                if process.returncode == 0:
                    self.status_message = f"Git {commands[choice][0]} successful"
                else:
                    self.status_message = f"Git error: {process.stderr[:50]}..."
            except Exception as e:
                self.status_message = f"Git error: {str(e)}"
        else:
            self.status_message = "Invalid choice"


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


# TODO: ---------------------------------------------------

    def session_save(self):
        """ TODO: Saves the current editor session. (Not implemented)"""
        pass


    def session_restore(self):
        """ TODO: Restores the editor session. (Not implemented)"""
        pass


    def encrypt_file(self):
        """ TODO: Encrypts the current file. (Not implemented)"""
        pass


    def decrypt_file(self):
        """ TODO: Decrypts the current file. (Not implemented)"""
        pass


    def validate_configuration(self):
        """ TODO: Validates YAML/TOML/JSON config files before saving. (Not implemented)"""
        pass

# ------------------------------------


    def run(self):
        """
        Main editor loop: draws the screen, handles input,
        and refreshes the display continuously.
        """
        while True:
            try:
                self.draw_screen()
                self.stdscr.keypad(True)
                key = self.stdscr.getch()
                self.handle_input(key)
            except KeyboardInterrupt:
                self.exit_editor()
            except Exception as e:
                logging.exception("Unhandled exception in main loop")
                self.status_message = f"Error: {str(e)}"


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
