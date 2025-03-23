#!/usr/bin/env python3
import curses
import locale
import toml
import os
import re
import sys
import time  # Add this import
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


# Линтер
def run_pylint_on_code(code, filename="tmp.py"):
    """
    Запускает pylint на переданном коде и возвращает вывод линтера.
    Код сохраняется во временный файл, после чего pylint анализирует этот файл.
    Добавлен таймаут в 5 секунд, чтобы избежать зависания.
    
    Параметры:
        code (str): Строка с кодом для анализа.
        filename (str): Имя временного файла (по умолчанию "tmp.py").
    
    Возвращает:
        str: Вывод pylint или сообщение об истечении времени.
    """
    # Если код превышает 100000 символов, пропускаем анализ
    if len(code) > 100000:
        return "Файл слишком большой для анализа pylint"
        
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp:
        tmp.write(code)
        tmp_name = tmp.name
    try:
        result = subprocess.run(
            ["pylint", tmp_name, "--output-format=text"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3  # уменьшенный таймаут
        )
        output = result.stdout.strip()
        return output
    except subprocess.TimeoutExpired:
        return "Pylint: время ожидания истекло."
    except Exception as e:
        return f"Pylint error: {str(e)}"
    finally:
        try:
            os.remove(tmp_name)
        except Exception:
            pass


# =================================================================
# 1. Загрузка конфигурации из файла конфигурации (`config.toml`)
#    с применением значений по умолчанию, если файл отсутствует
#    или содержит ошибки.
# -----------------------------------------------------------------

# Путь к файлу конфигурации
CONFIG_FILE = "config.toml"

# Настройка логирования
logging.basicConfig(
    filename='editor.log',
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)'
)

def deep_merge(base, override):
    """
    Рекурсивное объединение словарей.
    Если ключ существует в обоих словарях и оба значения являются словарями,
    выполняется рекурсивное объединение, иначе значение из override заменяет base.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config():
    """
    Загрузка конфигурации из файла config.toml с применением значений по умолчанию.
    Если файл отсутствует или содержит ошибки, возвращается словарь с настройками по умолчанию.
    """
    default_config = {
        "colors": {
            "line_number": "yellow",
            "cursor": "yellow",
            "keyword": "blue",
            "string": "green",
            "comment": "white",
            "literal": "magenta",
            "decorator": "cyan",
            "type": "yellow",
            "selector": "magenta",
            "property": "cyan",
            "punctuation": "white",
            "background": "#1E1E1E",
            "foreground": "#D4D4D4",
            "error": "red",           # Для ошибок
            "status": "bright_white", # Статусная строка
            "variable": "white",      # Переменные
            "tag": "blue",            # HTML/XML теги
            "attribute": "cyan",      # Атрибуты
            "magic": "magenta",       # Магические методы
            "builtin": "yellow",      # Встроенные функции
            "exception": "red",       # Исключения
            "function": "light_blue", # Функции
            "class": "yellow",        # Классы
            "number": "magenta",      # Числа
            "operator": "white",      # Операторы
            "escape": "cyan"          # Экранированные символы
        },
        "fonts": {
            "font_family": "monospace",
            "font_size": 12
        },
        "keybindings": {
            "delete": "del",
            "paste": "ctrl+v",
            "copy": "ctrl+c",
            "cut": "ctrl+x",
            "undo": "ctrl+z",
            "open_file": "ctrl+o",
            "save_file": "ctrl+s",
            "select_all": "ctrl+a",
            "quit": "ctrl+q"
        },
        "editor": {
            "show_line_numbers": True,
            "tab_size": 4,
            "use_spaces": True,
            "word_wrap": False,
            "auto_indent": True,
            "auto_brackets": True
        },
        "supported_formats": {
            "python" : [".py"],
            "toml" : [".toml"],
            "javascript": [".js", ".mjs", ".cjs", ".jsx"],
            "css": [".css"],
            "html": [".html", ".htm"],
            "json": [".json"],
            "yaml": [".yaml", ".yml"],
            "xml": [".xml"],
            "markdown": [".md"],
            "plaintext": [".txt"],
            "shell": [".sh", ".bash", ".zsh"],
            "dart": [".dart"],
            "go": [".go"],
            "c_cpp": [".c", ".cpp", ".h", ".hpp"],
            "java": [".java"],
            "julia": [".jl"],
            "rust": [".rs"],
            "csharp": [".cs"],
            "dockerfile": ["Dockerfile"],
            "terraform": [".tf"],
            "jenkins": ["Jenkinsfile"],
            "puppet": [".pp"],
            "saltstack": [".sls"],
            "git": [".gitignore", ".gitconfig", "config"],
            "notebook": [".ipynb"],
        },
        "theme": {
            "name": "dark",
            "ui": {
                "background": "#252526",
                "foreground": "#CCCCCC",
                "accent": "#007ACC",
                "selection": "#264F78",
                "inactive_selection": "#3A3D41",
                "cursor": "#AEAFAD"
            }
        },
        "file_icons": {
            "text": "📄",
            "code": "📝",
            "css": "🎨",
            "html": "🌐",
            "json": "📊",
            "yaml": "⚙️",
            "folder": "📁",
            "folder_open": "📂"
        },
        "syntax_highlighting": {
            "python": {
                "patterns": [
                    # Ключевые слова Python
                    {"pattern": r"\b(and|as|assert|async|await|break|class|continue|def|del|elif|else|except|exec|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|print|raise|return|try|while|with|yield)\b", "color": "keyword"},
                    # Декораторы
                    {"pattern": r"@\w+(?:\([^)]*?\))?", "color": "decorator"},
                    # Строковые литералы (тройные и обычные)
                    {"pattern": r"(?s)(f|r|rf|fr)?('''(.|\n)*?'''|\"\"\"(.|\n)*?\"\"\")", "color": "string"},
                    {"pattern": r"(f|r|rf|fr|b|br|rb)?(['\"])(?:\\.|(?!\2).)*\2", "color": "string"},
                    # Числовые литералы
                    {"pattern": r"\b(?:\d+\.\d+|\.\d+|\d+)(?:e[+-]?\d+)?j?\b", "color": "literal"},
                    {"pattern": r"\b0[bB][01_]+\b", "color": "literal"},
                    {"pattern": r"\b0[oO][0-7_]+\b", "color": "literal"},
                    {"pattern": r"\b0[xX][0-9a-fA-F_]+\b", "color": "literal"},
                    # Комментарии и docstrings
                    {"pattern": r"#.*$", "color": "comment"},
                    {"pattern": r'"""(.|\n)*?"""', "color": "comment"},
                    {"pattern": r"'''(.|\n)*?'''", "color": "comment"},
                    # Встроенные функции и исключения
                    {"pattern": r"\b(ArithmeticError|AssertionError|AttributeError|BaseException|BlockingIOError|BrokenPipeError|BufferError|BytesWarning|ChildProcessError|ConnectionAbortedError|ConnectionError|ConnectionRefusedError|ConnectionResetError|DeprecationWarning|EOFError|Ellipsis|EncodingWarning|EnvironmentError|Exception|FileExistsError|FileNotFoundError|FloatingPointError|FutureWarning|GeneratorExit|IOError|ImportError|ImportWarning|IndentationError|IndexError|InterruptedError|IsADirectoryError|KeyError|KeyboardInterrupt|LookupError|MemoryError|ModuleNotFoundError|NameError|NotADirectoryError|NotImplemented|NotImplementedError|OSError|OverflowError|PendingDeprecationWarning|PermissionError|ProcessLookupError|RecursionError|ReferenceError|ResourceWarning|RuntimeError|RuntimeWarning|StopAsyncIteration|StopIteration|SyntaxError|SyntaxWarning|SystemError|SystemExit|TabError|TimeoutError|TypeError|UnboundLocalError|UnicodeDecodeError|UnicodeEncodeError|UnicodeError|UnicodeTranslateError|UnicodeWarning|UserWarning|ValueError|Warning|ZeroDivisionError|__import__|abs|all|any|ascii|bin|bool|breakpoint|bytearray|bytes|callable|chr|classmethod|compile|complex|copyright|credits|delattr|dict|dir|divmod|enumerate|eval|exec|exit|filter|float|format|frozenset|getattr|globals|hasattr|hash|help|hex|id|input|int|isinstance|issubclass|iter|len|license|list|locals|map|max|memoryview|min|next|object|oct|open|ord|pow|print|property|range|repr|reversed|round|set|setattr|slice|sorted|staticmethod|str|sum|super|tuple|type|vars|zip)\b", "color": "builtin"},
                    # Аннотации типов
                    {"pattern": r"\b(List|Dict|Tuple|Set|Optional|Union|Any|Callable|TypeVar|Generic|Iterable|Iterator|Sequence|Mapping|MutableMapping|Awaitable|Coroutine|AsyncIterable|NamedTuple|TypedDict|Final|Literal|Annotated|TypeGuard|Self|Protocol|dataclass|field|classmethod|staticmethod)\b", "color": "type"},
                    # Регулярные выражения
                    {"pattern": r"r[\"'].*?[\"']", "color": "regexp"},
                    # Константы
                    {"pattern": r"\b(True|False|None|Ellipsis|NotImplemented)\b", "color": "literal"},
                    # Специальные методы
                    {"pattern": r"__(?:init|new|str|repr|enter|exit|getattr|setattr|delattr|getitem|setitem|delitem|iter|next|call|len|contains|add|sub|mul|truediv|floordiv|mod|pow|lshift|rshift|and|or|xor|invert|eq|ne|lt|le|gt|ge|bool|bytes|format|hash|dir|sizeof|getstate|setstate|reduce|reduce_ex|subclasshook|del|doc|name|qualname|module|defaults|kwdefaults|annotations|dict|weakref|slots|class|self|cls)__(?=\()", "color": "magic"},
                    # Импорты
                    {"pattern": r"\bimport\s+\w+(?:\.\w+)*\b", "color": "import"},
                    {"pattern": r"\bfrom\s+\w+(?:\.\w+)*\s+import\b", "color": "import"}
                ]
            },
            "javascript": {
                "patterns": [
                    {"pattern": r"//.*$", "color": "comment"},
                    {"pattern": r"/\*[\s\S]*?\*/", "color": "comment"},
                    {"pattern": r"\b(let|const|var|function|return|if|else|for|while|do|switch|case|break|continue|try|catch|finally|new|delete|typeof|instanceof|this|class|extends|super|import|export|from|as|async|await|yield)\b", "color": "keyword"},
                    {"pattern": r"`[^`]*`", "color": "string"},
                    {"pattern": r"\"[^\"]*\"", "color": "string"},
                    {"pattern": r"\b(\d+(\.\d+)?|true|false|null|undefined|NaN|Infinity)\b", "color": "literal"},
                    {"pattern": r"console\.log", "color": "keyword"},
                    {"pattern": r"\$\{[^}]*\}", "color": "literal"}
                ]
            },
            # Другие языки можно добавить при необходимости
        }
    }
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config_content = f.read()
            try:
                user_config = toml.loads(config_content)
                merged_config = deep_merge(default_config, user_config)
                return merged_config
            except toml.TomlDecodeError as e:
                logging.error(f"TOML parse error: {str(e)}")
                logging.error(f"Config content:\n{config_content}")
                return default_config
    except FileNotFoundError:
        logging.warning(f"Config file '{CONFIG_FILE}' not found. Using defaults.")
        return default_config


#################################################################
class SwayEditor:
    """
    Основной класс редактора Sway.
    """
    def __init__(self, stdscr):
        self.stdscr = stdscr
        # Enable special keys handling
        self.stdscr.keypad(True)  # Enable keypad mode
        curses.raw()              # Raw mode for better key handling
        curses.nonl()            # Don't translate enter key
        curses.noecho()          # Don't echo keys
        self.config = load_config()  # Загружаем конфигурацию из файла config.toml
        self.text = [""]
        self.cursor_x = 0
        self.cursor_y = 0
        self.scroll_top = 0
        self.scroll_left = 0
        self.filename = "new_file.py" 
        #self.filename = "noname"
        self.modified = False
        self.encoding = "UTF-8"
        self.stdscr.nodelay(False)
        locale.setlocale(locale.LC_ALL, "")
        curses.start_color()
        curses.use_default_colors()
        curses.curs_set(1)
        self.insert_mode = True
        self.syntax_highlighting = {}
        self.status_message = ""
        
        self.init_colors()
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
        }
        self.load_syntax_highlighting()
        self.set_initial_cursor_position()



    def apply_syntax_highlighting_with_pygments(self, line):
        """
        Использует Pygments для автоматического определения языка и токенизации строки.
        Полученные токены сопоставляются с цветовыми парами curses для подсветки синтаксиса.
        
        Параметры:
            line (str): строка кода для подсветки.
        
        Возвращает:
            list кортежей: каждый кортеж содержит подстроку и соответствующий цвет curses.
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
            # Ключевые слова
            Token.Keyword:                    curses.color_pair(2),
            Token.Keyword.Constant:           curses.color_pair(2),
            Token.Keyword.Declaration:        curses.color_pair(2),
            Token.Keyword.Namespace:          curses.color_pair(2),
            Token.Keyword.Pseudo:             curses.color_pair(2),
            Token.Keyword.Reserved:           curses.color_pair(2),
            Token.Keyword.Type:               curses.color_pair(2),
            # Имена встроенных функций и другие имена
            Token.Name.Builtin:               curses.color_pair(2),
            Token.Name.Function:              curses.color_pair(3),
            Token.Name.Class:                 curses.color_pair(3),
            Token.Name.Decorator:             curses.color_pair(5),
            Token.Name.Exception:             curses.color_pair(4),
            Token.Name.Variable:              curses.color_pair(7),
            Token.Name.Namespace:             curses.color_pair(2),
            Token.Name.Attribute:             curses.color_pair(7),
            Token.Name.Tag:                   curses.color_pair(5),
            # Строковые литералы
            Token.Literal.String:             curses.color_pair(3),
            Token.Literal.String.Doc:         curses.color_pair(3),
            Token.Literal.String.Interpol:    curses.color_pair(3),
            Token.Literal.String.Escape:      curses.color_pair(3),
            Token.Literal.String.Backtick:    curses.color_pair(3),
            Token.Literal.String.Delimiter:   curses.color_pair(3),
            # Числовые литералы
            Token.Literal.Number:             curses.color_pair(4),
            Token.Literal.Number.Float:       curses.color_pair(4),
            Token.Literal.Number.Hex:         curses.color_pair(4),
            Token.Literal.Number.Integer:     curses.color_pair(4),
            Token.Literal.Number.Oct:         curses.color_pair(4),
            # Комментарии
            Token.Comment:                    curses.color_pair(1),
            Token.Comment.Multiline:          curses.color_pair(1),
            Token.Comment.Preproc:            curses.color_pair(1),
            Token.Comment.Special:            curses.color_pair(1),
            # Операторы и знаки препинания
            Token.Operator:                   curses.color_pair(6),
            Token.Operator.Word:              curses.color_pair(6),
            Token.Punctuation:                curses.color_pair(6),
            # Пробельные символы и текст
            Token.Text:                       curses.color_pair(0),
            Token.Text.Whitespace:            curses.color_pair(0),
            # Ошибки
            Token.Error:                      curses.color_pair(8),
            # Дополнительные Generic
            Token.Generic.Heading:            curses.color_pair(5) | curses.A_BOLD,
            Token.Generic.Subheading:         curses.color_pair(5),
            Token.Generic.Deleted:            curses.color_pair(8),
            Token.Generic.Inserted:           curses.color_pair(4),
            Token.Generic.Emph:               curses.color_pair(3) | curses.A_BOLD,
            Token.Generic.Strong:             curses.color_pair(2) | curses.A_BOLD,
            Token.Generic.Prompt:             curses.color_pair(7),
        }
        default_color = curses.color_pair(0)
        highlighted = []
        for token, text in tokens:
            color = default_color
            for token_type, curses_color in token_color_map.items():
                if token in token_type:
                    color = curses_color
                    break
            highlighted.append((text, color))
        return highlighted


    def run_pylint_async(self, code):
        """
        Асинхронный запуск pylint. Результат выводится в статусную строку.
        Здесь мы выводим только первые 200 символов вывода, чтобы не перегружать статус.
        """
        lint_output = run_pylint_on_code(code)
        if lint_output:
            # Обновляем статус, показывая начало вывода линтера
            self.status_message = f"Pylint: {lint_output[:200]}..."  # обрезаем длинный вывод
        else:
            self.status_message = f"Сохранено в {self.filename} без предупреждений от pylint."




    # ---------------------------------------------------------------
    # 3. Установка начальной позиции курсора и параметров прокрутки текста.
    # ---------------------------------------------------------------
    def set_initial_cursor_position(self):
        self.cursor_x = 0
        self.cursor_y = 0
        self.scroll_top = 0
        self.scroll_left = 0

    # ---------------------------------------------------------------
    # 4. Инициализация цветовых пар для выделения синтаксиса и элементов интерфейса.
    # ---------------------------------------------------------------
    def init_colors(self):
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


    # ---------------------------------------------------------------
    # 5. Применение синтаксической подсветки текста с использованием
    #    заранее скомпилированных регулярных выражений.
    # ---------------------------------------------------------------

    def apply_syntax_highlighting(self, line, lang):
        """
        Переработанный метод подсветки синтаксиса с использованием Pygments.
        Метод автоматически определяет язык по имени файла или содержимому строки.
        
        Параметры:
            line (str): Строка кода, которую необходимо подсветить.
            lang (str): Параметр для совместимости, но не используется.
        
        Возвращает:
            list кортежей: Каждый кортеж содержит подстроку и соответствующую цветовую пару curses.
        """
        return self.apply_syntax_highlighting_with_pygments(line)

    

    # --------------------------------------------------------------
    # 6. Загрузка и компиляция правил синтаксической подсветки из
    #    конфигурационного файла.
    # --------------------------------------------------------------
    def load_syntax_highlighting(self):
        self.syntax_highlighting = {}
        try:
            syntax_cfg = self.config.get("syntax_highlighting", {})
            for lang, rules in syntax_cfg.items():
                patterns = rules.get("patterns", [])
                for rule in patterns:
                    try:
                        compiled = re.compile(rule["pattern"])
                        color_pair = self.colors.get(rule["color"], curses.color_pair(0))
                        self.syntax_highlighting.setdefault(lang, []).append((compiled, color_pair))
                    except Exception as e:
                        logging.exception(f"Error in syntax highlighting rule for {lang}: {rule}")
        except Exception as e:
            logging.exception("Error loading syntax highlighting")


    # --------------------------------------------------------------
    # 7. Отрисовка экрана редактора, включая строки текста,
    #    номера строк, статусную строку и позиционирование курсора.
    # --------------------------------------------------------------
    def draw_screen(self):
        self.stdscr.clear()
        height, width = self.stdscr.getmaxyx()
        if height < 24 or width < 80:
            try:
                self.stdscr.addstr(0, 0, "Window too small (min: 80x24)", self.colors["error"])
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
                self.stdscr.addstr(screen_row, 0, line_num_format.format(line_num), self.colors["line_number"])
            except curses.error:
                pass

            line = self.text[line_num - 1] if line_num <= len(self.text) else ""
            syntax_line = self.apply_syntax_highlighting(line, self.detect_language())
            x_pos = 0
            for text_part, color in syntax_line:
                if x_pos + len(text_part.encode("utf-8")) <= self.scroll_left:
                    x_pos += len(text_part.encode("utf-8"))
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
        
        # Подсветка парных скобок
        self.highlight_matching_brackets()

        # Обновление экрана
        self.stdscr.refresh()

    # ----------------------------------------------------------------
    # 8. Определение языка файла на основе его расширения для последующего
    #    применения синтаксической подсветки.
    # ----------------------------------------------------------------
    def detect_language(self):
        ext = os.path.splitext(self.filename)[1].lower()
        logging.debug(f"Detecting language for extension: {ext}")
        for lang, exts in self.config.get("supported_formats", {}).items():
            logging.debug(f"Checking if {ext} is in {exts} for language {lang}")
            if ext in exts:
                logging.debug(f"Detected language: {lang}")
                return lang
        logging.debug("No language detected, using 'text'")
        return "text"

    #################################################################
    # 9. Обработка нажатых клавиш:
    #
    #   обработка специальных команд (открыть, сохранить и т.д.)
    #   и ввод текста.
    # ----------------------------------------------------------------
    def handle_input(self, key):
        """Handle keyboard input."""
        logging.debug(f"Key pressed: {key}")
        try:
            # Special keys
            if key == curses.KEY_ENTER or key == 10 or key == 13:  # Enter
                self.handle_enter()
            elif key == curses.KEY_UP or key == 259 or key == 450:  # Up arrow 259, 450
                self.handle_up()
            elif key == curses.KEY_DOWN or key == 258 or key == 456:  # Down arrow 258, 456
                self.handle_down()
            elif key == curses.KEY_LEFT or key == 260 or key == 452:  # Left arrow 260, 452
                self.handle_left()
            elif key == curses.KEY_RIGHT or key == 261 or key == 454:  # Right arrow 261, 454
                self.handle_right()
            elif key == curses.KEY_BACKSPACE or key == 127 or key == 8:  # Добавьте 8 (PowerShell)
                self.handle_backspace()
            elif key == curses.KEY_DC or key == 330 or key == 462:  # Delete   330, 462
                self.handle_delete()
            elif key == curses.KEY_HOME or key == 262 or key == 449:  # Home  262, 449
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
            elif key == self.keybindings["quit"] or key == 17 or key == 3:  # Quit
                self.exit_editor()
            elif key == self.keybindings["save_file"] or key == 19:   # Save
                self.save_file()
            elif key == self.keybindings["open_file"] or key == 15:  # Open
                self.open_file()
            # Regular character input
            elif 32 <= key <= 126:  # Printable ASCII characters
                self.handle_char_input(key)
                
        except Exception as e:
            self.status_message = f"Input error: {str(e)}"
            logging.exception("Error handling input")

    # ---------------------------------------------------------------
    # 10. Перемещение курсора вверх по строкам.
    #     Клаваиша `Arr Up`.
    # ---------------------------------------------------------------
    def handle_up(self):
        """Handle up arrow key."""
        if self.cursor_y > 0:
            self.cursor_y -= 1
            self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))

    # --------------------------------------------------------------
    # 11. Перемещение курсора вниз по строкам.
    #     Клаваиша `Arr Down`.
    # --------------------------------------------------------------
    def handle_down(self):
        """Handle down arrow key."""
        if self.cursor_y < len(self.text) - 1:
            self.cursor_y += 1
            self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))

    # ---------------------------------------------------------------
    # 12. Перемещение курсора влево на один символ или на предыдущую
    #     строку. Клаваиша `<-`.
    # ---------------------------------------------------------------
    def handle_left(self):
        """Handle left arrow key."""
        if self.cursor_x > 0:
            self.cursor_x -= 1
        elif self.cursor_y > 0:
            self.cursor_y -= 1
            self.cursor_x = len(self.text[self.cursor_y])

    # ---------------------------------------------------------------
    # 13. Перемещение курсора вправо на один символ или на следующую
    #     строку. Клаваиша `->`.
    # ---------------------------------------------------------------
    def handle_right(self):
        """Handle right arrow key."""
        if self.cursor_x < len(self.text[self.cursor_y]):
            self.cursor_x += 1
        elif self.cursor_y < len(self.text) - 1:
            self.cursor_y += 1
            self.cursor_x = 0

    # ---------------------------------------------------------------
    # 14. Перемещение курсора в начало текущей строки.
    #     Клаваиша `Home`.
    # ---------------------------------------------------------------
    def handle_home(self):
        """Handle Home key."""
        self.cursor_x = 0

    # ---------------------------------------------------------------
    # 15. Перемещение курсора в конец текущей строки.
    #     Клаваиша `End`.
    # ---------------------------------------------------------------
    def handle_end(self):
        """Handle End key."""
        self.cursor_x = len(self.text[self.cursor_y])

    # ---------------------------------------------------------------
    # 16. Перемещение курсора вверх на страницу (на 10 строк).
    #     Клаваиша `PageUp`.
    # ---------------------------------------------------------------
    def handle_page_up(self):
        """Handle Page Up key."""
        height = self.stdscr.getmaxyx()[0]
        self.cursor_y = max(0, self.cursor_y - height)
        self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))
        self.scroll_top = max(0, self.scroll_top - height)

    # ---------------------------------------------------------------
    # 17. Перемещение курсора вниз на страницу (на 10 строк).
    #     Клаваиша `PageDown`.
    # ---------------------------------------------------------------
    def handle_page_down(self):
        """Handle Page Down key."""
        height = self.stdscr.getmaxyx()[0]
        self.cursor_y = min(len(self.text) - 1, self.cursor_y + height)
        self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))
        if self.cursor_y >= self.scroll_top + height:
            self.scroll_top = max(0, min(len(self.text) - height, self.scroll_top + height))

    # ---------------------------------------------------------------
    # 18. Удаление символа под курсором или объединение текущей
    #     строки со следующей. Клаваиша `Delete`.
    # ---------------------------------------------------------------
    def handle_delete(self):
        """Handle delete key."""
        if self.cursor_x < len(self.text[self.cursor_y]):
            # Delete character at cursor
            line = self.text[self.cursor_y]
            self.text[self.cursor_y] = line[:self.cursor_x] + line[self.cursor_x+1:]
            self.modified = True
        elif self.cursor_y < len(self.text) - 1:
            # Join with next line
            self.text[self.cursor_y] += self.text.pop(self.cursor_y + 1)
            self.modified = True

    # ---------------------------------------------------------------
    # 19. Удаление символа слева от курсора или объединение текущей
    #     строки с предыдущей. Клаваиша `Backspace`.
    # ---------------------------------------------------------------
    def handle_backspace(self):
        """Handle backspace key."""
        if self.cursor_x > 0:
            # Delete character before cursor
            line = self.text[self.cursor_y]
            self.text[self.cursor_y] = line[:self.cursor_x-1] + line[self.cursor_x:]
            self.cursor_x -= 1
            self.modified = True
        elif self.cursor_y > 0:
            # Join with previous line
            self.cursor_y -= 1
            self.cursor_x = len(self.text[self.cursor_y])
            self.text[self.cursor_y] += self.text.pop(self.cursor_y + 1)
            self.modified = True

    # Tab
    def handle_tab(self):
        """Handle Tab key - insert spaces or tab character based on configuration"""

        # Default to 4 spaces, but this could be configurable
        tab_size = self.config.get("editor", {}).get("tab_size", 4)
        use_spaces = self.config.get("editor", {}).get("use_spaces", True)

        current_line = self.text[self.cursor_y]

        if use_spaces:
            # Insert spaces for tab
            spaces = " " * tab_size
            self.text[self.cursor_y] = (
                current_line[: self.cursor_x] + spaces + current_line[self.cursor_x :]
            )
            self.cursor_x += tab_size
        else:
            # Insert actual tab character
            self.text[self.cursor_y] = (
                current_line[: self.cursor_x] + "\t" + current_line[self.cursor_x :]
            )
            self.cursor_x += 1

        self.modified = True

    # Smart Tab implement smart indentation that aligns with the indentation of the previous line
    def handle_smart_tab(self):
        """Smart tab that respects the indentation of the previous line"""
        if self.cursor_y > 0:
            prev_line = self.text[self.cursor_y - 1]
            # Calculate leading whitespace
            leading_space_match = re.match(r"^(\s*)", prev_line)
            if leading_space_match:
                leading_space = leading_space_match.group(1)
                # Only apply if we're at the beginning of the line
                if self.cursor_x == 0:
                    self.text[self.cursor_y] = leading_space + self.text[self.cursor_y]
                    self.cursor_x = len(leading_space)
                    self.modified = True
                    return

        # Fall back to regular tab if not at beginning or no previous line
        self.handle_tab()

    # ---------------------------------------------------------------
    # 19a. Начало и конец выделения текста (для копирования/вырезания).
    # ---------------------------------------------------------------
    def start_selection(self):
        """TODO: Начало выделения текста."""
        pass

    def end_selection(self):
        """TODO: Конец выделения текста."""
        pass

    # ---------------------------------------------------------------
    # 19b. Копирование выделенного текста в буфер обмена.
    # TODO:
    # ---------------------------------------------------------------
    def copy_selection(self):
        """TODO: Копирование выделенного фрагмента текста."""
        pass

    # ---------------------------------------------------------------
    # 19c. Вырезание выделенного текста в буфер обмена.
    # TODO:
    # ---------------------------------------------------------------
    def cut_selection(self):
        """TODO: Вырезание выделенного текста."""
        pass

    # ---------------------------------------------------------------
    # 19d. Вставка текста из буфера обмена.
    # TODO:
    # ---------------------------------------------------------------
    def paste_from_clipboard(self):
        """TODO: Вставка текста из буфера обмена."""
        pass

    # ---------------------------------------------------------------
    # 19e. Отмена и повтор последних действий.
    # TODO:
    # ---------------------------------------------------------------
    def undo(self):
        """TODO: Отмена последнего действия."""
        pass
    
    # TODO:
    def redo(self):
        """TODO: Повтор последнего отменённого действия."""
        pass

    # ---------------------------------------------------------------
    # 20. Ввод обычного печатного символа в текущую позицию курсора.
    # ---------------------------------------------------------------
    def handle_char_input(self, key):
        try:
            char = chr(key)
            current_line = self.text[self.cursor_y]
            if self.insert_mode:
                self.text[self.cursor_y] = (
                    current_line[: self.cursor_x] + char + current_line[self.cursor_x :]
                )
            else:
                self.text[self.cursor_y] = (
                    current_line[: self.cursor_x]
                    + char
                    + (
                        current_line[self.cursor_x + 1 :]
                        if self.cursor_x < len(current_line)
                        else ""
                    )
                )
            self.cursor_x += 1
            self.modified = True
        except (ValueError, UnicodeEncodeError):
            logging.error(f"Cannot encode character: {key}")


    def handle_enter(self):
        """Handle enter key press."""
        self.text.insert(self.cursor_y + 1, "")  # Insert new empty line
        content = self.text[self.cursor_y][self.cursor_x:]  # Get content after cursor
        self.text[self.cursor_y] = self.text[self.cursor_y][:self.cursor_x]  # Keep content before cursor
        self.text[self.cursor_y + 1] = content  # Move content after cursor to new line
        self.cursor_y += 1  # Move cursor to next line
        self.cursor_x = 0  # Move cursor to start of line
        self.modified = True


    # ===============================================================
    # 21. Преобразование строки с описанием горячих клавиш из
    #     конфигурации в соответствующий код клавиши.
    # ---------------------------------------------------------------
    def parse_key(self, key_str):
        """
        Преобразование строки с описанием горячих клавиш в код клавиши.
        """
        if not key_str:
            return -1

        parts = key_str.split("+")
        if len(parts) == 2 and parts[0].lower() == "ctrl":  # Changed & to and
            return ord(parts[1].lower()) - ord('a') + 1
        elif key_str.lower() == "del":
            return curses.KEY_DC
        elif key_str.lower() == "insert":
            return curses.KEY_IC
        try:
            return ord(key_str)
        except TypeError:
            return -1

    # ---------------------------------------------------------------
    # 22. Расчёт ширины символа с учётом особенностей UTF-8
    #     и отображения полушироких и полношироких символов.
    # ---------------------------------------------------------------
    def get_char_width(self, char):
        """Calculate the display width of a character"""
        try:
            if ord(char) < 128:
                return 1
            # Используем east_asian_width для определения ширины символа
            width = unicodedata.east_asian_width(char)
            if width in ("F", "W"):  # Full-width characters
                return 2
            elif width == "A":  # Ambiguous width
                return 2
            else:
                return 1
        except (UnicodeEncodeError, TypeError):
            return 1

    # =================================================================
    # 23. Открытие указанного пользователем файла с автоматическим
    #     определением кодировки и загрузкой содержимого в редактор.
    #     Модуль `сhardet`.
    # -----------------------------------------------------------------
    def open_file(self):
        if self.modified:
            choice = self.prompt("Save changes? (y/n): ")
            if choice and choice.lower().startswith("y"):
                self.save_file()

        filename = self.prompt("Open file: ")
        if not filename:
            self.status_message = "Open cancelled"
            return

        try:
            # Попытка определить кодировку файла
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
            curses.flushinp()  # Очистка буфера ввода
        except ImportError:
            # Если модуль chardet не установлен, просто используем UTF-8
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
                curses.flushinp()  # Очистка буфера ввода
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

    # ---------------------------------------------------------------
    # 24. Сохранение текущего содержимого редактора в файл с
    #     проверкой разрешений на запись.
    # ---------------------------------------------------------------
    def save_file(self):
        """
        Сохранение файла. Если имя файла не задано, запрашивается у пользователя.
        После успешного сохранения запускается pylint в отдельном потоке,
        чтобы не блокировать основной интерфейс редактора.
        """
        if self.filename == "noname":
            self.filename = self.prompt("Save as: ")
            if not self.filename:
                self.status_message = "Сохранение отменено"
                return

        # Проверка, является ли self.filename директорией
        if os.path.isdir(self.filename):
            self.status_message = f"Cannot save: {self.filename} is a directory"
            return

        if os.path.exists(self.filename):
            if not os.access(self.filename, os.W_OK):
                self.status_message = f"Нет прав на запись: {self.filename}"
                return
        try:
            with open(self.filename, "w", encoding=self.encoding, errors="replace") as f:
                f.write(os.linesep.join(self.text))
            self.modified = False
            self.status_message = f"Сохранено в {self.filename}"
            
            # Запускаем pylint в отдельном потоке
            code = os.linesep.join(self.text)
            threading.Thread(target=self.run_pylint_async, args=(code,), daemon=True).start()
        except OSError as e:
            self.status_message = f"Ошибка при сохранении файла: {e}"
            logging.exception(f"Ошибка при сохранении файла: {self.filename}")
        except Exception as e:
            self.status_message = f"Ошибка при сохранении файла: {e}"
            logging.exception(f"Ошибка при сохранении файла: {self.filename}")


    # ---------------------------------------------------------------
    # 24a. Сохранение текущего файла под новым именем.
    # 
    # ---------------------------------------------------------------
    def save_file_as(self):
        """
        Сохранение текущего файла под новым именем.
        Запрашивает новое имя, сохраняет содержимое, обновляет self.filename,
        сбрасывает флаг модификации и запускает pylint в отдельном потоке.
        """
        new_filename = self.prompt("Save file as: ")
        if not new_filename:
            self.status_message = "Сохранение отменено: имя файла не указано"
            return

        if os.path.isdir(new_filename):
            self.status_message = f"Cannot save: {new_filename} is a directory"
            return

        if os.path.exists(new_filename):
            if not os.access(new_filename, os.W_OK):
                self.status_message = f"Нет прав на запись: {new_filename}"
                return

        try:
            with open(new_filename, "w", encoding=self.encoding, errors="replace") as f:
                f.write(os.linesep.join(self.text))
            self.filename = new_filename
            self.modified = False
            self.status_message = f"Сохранено как {new_filename}"
            
            code = os.linesep.join(self.text)
            threading.Thread(target=self.run_pylint_async, args=(code,), daemon=True).start()
        except (OSError, Exception) as e:
            self.status_message = f"Ошибка при сохранении файла: {e}"
            logging.exception(f"Ошибка при сохранении файла: {new_filename}")
                

    # ---------------------------------------------------------------
    # 24b. Откат изменений к последнему сохранённому состоянию файла.
    # ---------------------------------------------------------------
    def revert_changes(self):
        """
        Откат изменений в текущем файле до последнего сохранения.
        Перезагружает содержимое файла из сохранённой версии, если файл существует.
        """
        # Проверка: был ли файл сохранён ранее
        if self.filename == "noname":
            self.status_message = "Cannot revert: file has not been saved yet"
            return

        # Проверка: существует ли файл
        if not os.path.exists(self.filename):
            self.status_message = f"Cannot revert: file {self.filename} does not exist"
            return

        # Запрос подтверждения у пользователя
        confirmation = self.prompt("Revert to last saved version? All unsaved changes will be lost. (y/n): ")
        if not confirmation or confirmation.lower() != 'y':
            self.status_message = "Revert cancelled"
            return

        try:
            # Перезагрузка содержимого файла с текущей кодировкой
            with open(self.filename, "r", encoding=self.encoding, errors="replace") as f:
                self.text = f.read().splitlines()
                if not self.text:
                    self.text = [""]  # Если файл пустой, добавляем пустую строку

            # Сброс состояния редактора
            self.modified = False
            self.set_initial_cursor_position()
            self.status_message = f"Reverted to last saved version of {self.filename}"

        except OSError as e:
            self.status_message = f"Error reverting file: {e}"
            logging.exception(f"Error reverting file: {self.filename}")
        except Exception as e:
            self.status_message = f"Unexpected error: {e}"
            logging.exception(f"Unexpected error reverting file: {self.filename}")


    # ---------------------------------------------------------------
    # 24c. Создание нового пустого файла.
    # ---------------------------------------------------------------
    def new_file(self):
        """
        Создание нового пустого документа с предварительным запросом на сохранение текущих изменений.
        """
        # Проверяем, есть ли несохраненные изменения
        if self.modified:
            # Запрашиваем у пользователя, хочет ли он сохранить изменения
            choice = self.prompt("Save changes? (y/n): ")
            if choice and choice.lower().startswith("y"):
                # Если пользователь выбрал "y", сохраняем текущий файл
                self.save_file()
            # Если выбрано "n" или что-то другое, продолжаем без сохранения

        try:
            # Создаем новый пустой документ
            self.text = [""]  # Одна пустая строка
            self.filename = "noname"  # Имя по умолчанию для нового файла
            self.modified = False  # Новый файл не имеет изменений
            self.set_initial_cursor_position()  # Устанавливаем курсор в начало
            self.status_message = "New file created"  # Сообщаем пользователю о создании файла
        except Exception as e:
            # Обрабатываем возможные ошибки
            self.status_message = f"Error creating new file: {e}"
            logging.exception("Error creating new file")


    # ---------------------------------------------------------------
    # 25. Выход из редактора с предварительным запросом на
    #     сохранение несохранённых изменений.
    # ---------------------------------------------------------------
    def exit_editor(self):
        if self.modified:
            choice = self.prompt("Save changes? (y/n): ")
            if choice and choice.lower().startswith("y"):
                self.save_file()
        curses.endwin()  # Restore terminal state
        sys.exit(0)

    def handle_escape(self):
        """Handle escape key press"""
        if self.modified:
            choice = self.prompt("Save changes before exit? (y/n): ")
            if choice and choice.lower().startswith('y'):
                self.save_file()
        self.exit_editor()

    # ---------------------------------------------------------------
    # 26. Вывод сообщения пользователю и получение ввода текста
    #     с клавиатуры.
    # ---------------------------------------------------------------
    def prompt(self, message):
        self.stdscr.nodelay(False)  # Переключаемся в блокирующий режим
        curses.echo()
        try:
            self.stdscr.addstr(curses.LINES - 1, 0, message)
            self.stdscr.clrtoeol()
            self.stdscr.refresh()
            # Use a larger buffer for UTF-8 input
            response = (
                self.stdscr.getstr(curses.LINES - 1, len(message), 1024)
                .decode("utf-8", errors="replace")
                .strip()
            )
        except Exception as e:
            response = ""
            logging.exception("Prompt error")
        finally:
            curses.noecho()
            self.stdscr.nodelay(
                False
            )  # Оставляем в блокирующем режиме для основного цикла
        return response

    # ---------------------------------------------------------------
    # 27. Поиск заданного текста по всему документу и возврат
    #     позиций найденных совпадений.
    # ---------------------------------------------------------------
    def search_text(self, search_term):
        """Add search functionality"""
        matches = []
        for line_num, line in enumerate(self.text):
            for match in re.finditer(re.escape(search_term), line):
                matches.append((line_num, match.start(), match.end()))
        return matches

    # ---------------------------------------------------------------
    # 28. Проверка имени файла на корректность, длину и допустимый путь.
    # ---------------------------------------------------------------
    def validate_filename(self, filename):
        """Add filename validation"""
        if not filename or len(filename) > 255:
            return False
        if os.path.isabs(filename):
            base_dir = os.path.dirname(os.path.abspath(filename))
            return os.path.commonpath([base_dir, os.getcwd()]) == os.getcwd()
        return True

    # ===================================================================
    # TODO: Реализовать группу методов интеграции и улучшений редактора:

    # ---------------------------------------------------------------
    # 28a. Выполнение произвольной shell-команды.
    # ---------------------------------------------------------------
    def execute_shell_command(self):
        """Выполнение shell-команды из редактора."""
        command = self.prompt("Enter command: ")
        if not command:
            self.status_message = "Command cancelled"
            return
        
        try:
            # Save screen state
            curses.def_prog_mode()
            curses.endwin()
            
            # Execute command
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            output, error = process.communicate(timeout=30)
            
            # Restore screen
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

    # ---------------------------------------------------------------
    # 28b. Простая интеграция с Git (commit, push, pull, diff).
    # ---------------------------------------------------------------
    def integrate_git(self):
        """Интеграция основных команд Git."""
        commands = {
            '1': ('status', 'git status'),
            '2': ('commit', 'git commit -a'),
            '3': ('push', 'git push'),
            '4': ('pull', 'git pull'),
            '5': ('diff', 'git diff')
        }
        
        menu = "\n".join([f"{k}: {v[0]}" for k, v in commands.items()])
        choice = self.prompt(f"Select Git command:\n{menu}\nChoice: ")
        
        if choice in commands:
            try:
                curses.def_prog_mode()
                curses.endwin()
                
                process = subprocess.run(
                    commands[choice][1],
                    shell=True,
                    text=True,
                    capture_output=True
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

    # ---------------------------------------------------------------
    # 28с. Переход к конкретной строке документа.
    # ---------------------------------------------------------------
    def goto_line(self):
        """Переход к указанной строке."""
        line_num = self.prompt("Go to line: ")
        try:
            line_num = int(line_num)
            if 1 <= line_num <= len(self.text):
                self.cursor_y = line_num - 1
                self.cursor_x = 0
                # Ensure the line is visible
                height = self.stdscr.getmaxyx()[0]
                if self.cursor_y < self.scroll_top:
                    self.scroll_top = max(0, self.cursor_y - height//2)
                elif self.cursor_y >= self.scroll_top + height - 2:
                    self.scroll_top = min(len(self.text) - height + 2, self.cursor_y - height//2)
            else:
                self.status_message = f"Line number out of range (1-{len(self.text)})"
        except ValueError:
            self.status_message = "Invalid line number"

    # ---------------------------------------------------------------
    # 28d. Поиск и замена текста с поддержкой регулярных выражений.
    # ---------------------------------------------------------------
    def find_and_replace(self):
        """Поиск и замена текста с поддержкой regex."""
        search_term = self.prompt("Search for: ")
        if not search_term:
            return
            
        replace_term = self.prompt("Replace with: ")
        if replace_term is None:  # User cancelled
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

    # ---------------------------------------------------------------
    # 28e. Переключение режима вставки/замены.
    # ---------------------------------------------------------------
    def toggle_insert_mode(self):
        """Переключение между Insert и Replace режимами."""
        self.insert_mode = not self.insert_mode
        self.status_message = f"Mode: {'Insert' if self.insert_mode else 'Replace'}"


    # ---------------------------------------------------------------
    # 28 f. Вспомогательный метод для поиска парной скобки
    # Сначала добавим метод, который будет искать парную скобку для той, на которой находится курсор.    
    # ---------------------------------------------------------------
    def find_matching_bracket(self, line, col, bracket):
        """
        Ищет парную скобку для скобки под курсором.
        Аргументы:
            line (str): текущая строка текста
            col (int): позиция курсора в строке
            bracket (str): символ скобки под курсором
        Возвращает:
            tuple (int, int): (строка, столбец) парной скобки или None, если не найдено
        """
        brackets = {'(': ')', '{': '}', '[': ']', ')': '(', '}': '{', ']': '['}
        stack = []
        direction = 1 if bracket in '({[' else -1  # Вперед для открывающих, назад для закрывающих
        start = col + direction

        if direction == 1:  # Ищем закрывающую скобку
            for i in range(start, len(line)):
                char = line[i]
                if char in '({[':
                    stack.append(char)
                elif char in ')}]':
                    if not stack:
                        return None
                    top = stack.pop()
                    if brackets[top] != char:
                        return None
                    if not stack:  # Стек пуст — найдена пара
                        return (self.cursor_y, i)
        else:  # Ищем открывающую скобку
            for i in range(start, -1, -1):
                char = line[i]
                if char in ')}]':
                    stack.append(char)
                elif char in '({[':
                    if not stack:
                        return None
                    top = stack.pop()
                    if brackets[char] != top:
                        return None
                    if not stack:  # Стек пуст — найдена пара
                        return (self.cursor_y, i)
        return None


    # ---------------------------------------------------------------
    # 28ff. Подсветка парных скобок в редакторе.
    # Основной метод - Он будет проверять символ под курсором и подсвечивать 
    # как текущую скобку, так и её пару, если она найдена.
    # ---------------------------------------------------------------
    def highlight_matching_brackets(self):
        """Подсветка парных скобок."""
        # Проверяем, что курсор находится в пределах текста
        if not (0 <= self.cursor_y < len(self.text) and 0 <= self.cursor_x < len(self.text[self.cursor_y])):
            return

        line = self.text[self.cursor_y]
        char = line[self.cursor_x]

        # Если символ под курсором — скобка
        if char in '(){}[]':
            match_pos = self.find_matching_bracket(line, self.cursor_x, char)
            if match_pos:
                height, width = self.stdscr.getmaxyx()
                # Подсвечиваем текущую скобку
                if (0 <= self.cursor_y - self.scroll_top < height and 
                    0 <= self.cursor_x - self.scroll_left < width):
                    self.stdscr.addch(self.cursor_y - self.scroll_top, 
                                    self.cursor_x - self.scroll_left, 
                                    char, 
                                    curses.A_REVERSE)

                # Подсвечиваем парную скобку, если она видима
                match_y, match_x = match_pos
                if (0 <= match_y - self.scroll_top < height and 
                    0 <= match_x - self.scroll_left < width):
                    self.stdscr.addch(match_y - self.scroll_top, 
                                    match_x - self.scroll_left, 
                                    line[match_x], 
                                    curses.A_REVERSE)




    # ---------------------------------------------------------------
    # 28i. Поиск и замена текста с поддержкой регулярных выражений.
    # ---------------------------------------------------------------
    def search_and_replace(self):
        """
        Поиск и замена текста с поддержкой regex.
        Запрашивает у пользователя шаблон для поиска и строку для замены,
        выполняет замену во всем документе и сообщает о количестве замен.
        """
        # Запрашиваем у пользователя шаблон для поиска (regex)
        search_pattern = self.prompt("Enter search pattern (regex): ")
        if not search_pattern:
            self.status_message = "Search cancelled"
            return

        # Запрашиваем у пользователя строку для замены
        replace_with = self.prompt("Enter replacement string: ")
        if replace_with is None:  # Предполагаем, что prompt возвращает None при отмене
            self.status_message = "Replacement cancelled"
            return

        try:
            # Компилируем регулярное выражение
            compiled_pattern = re.compile(search_pattern)
            new_text = []
            replacements = 0

            # Проходим по каждой строке в документе
            for line in self.text:
                # Выполняем замену и получаем новую строку и количество замен
                new_line, count = compiled_pattern.subn(replace_with, line)
                new_text.append(new_line)
                replacements += count

            # Обновляем текст документа
            self.text = new_text
            self.modified = True  # Устанавливаем флаг изменения
            self.status_message = f"Replaced {replacements} occurrence(s)"

        except re.error as e:
            # Обрабатываем ошибку недопустимого регулярного выражения
            self.status_message = f"Invalid regex pattern: {e}"
        except Exception as e:
            # Обрабатываем другие возможные ошибки
            self.status_message = f"Error during search and replace: {e}"

    # ---------------------------------------------------------------
    # 28j. Сохранение и восстановление сессии.
    # TODO: реализовать
    # ---------------------------------------------------------------
    def session_save(self):
        """Сохранение текущей сессии редактора."""
        pass

    def session_restore(self):
        """Восстановление сессии редактора."""
        pass

    # ---------------------------------------------------------------
    # 28k. Включение и отключение автосохранения.
    # ---------------------------------------------------------------
    def toggle_auto_save(self):
        """Включение/отключение функции автосохранения."""
        self.auto_save = getattr(self, 'auto_save', False)
        self.auto_save = not self.auto_save
        
        if self.auto_save:
            def auto_save_thread():
                while self.auto_save:
                    time.sleep(60)  # Auto-save every minute
                    if self.modified:
                        self.save_file()
            
            threading.Thread(target=auto_save_thread, daemon=True).start()
            self.status_message = "Auto-save enabled"
        else:
            self.status_message = "Auto-save disabled"

    # ---------------------------------------------------------------
    # 28l. Шифрование и дешифрование текущего файла.
    # TODO: реализовать
    # ---------------------------------------------------------------
    def encrypt_file(self):
        """Шифрование текущего файла."""
        pass

    def decrypt_file(self):
        """Дешифрование текущего файла."""
        pass

    # ---------------------------------------------------------------
    # 28m. Валидация конфигурационных файлов перед сохранением.
    # TODO: реализовать
    # ---------------------------------------------------------------
    def validate_configuration(self):
        """Проверка YAML/TOML/JSON файлов перед сохранением."""
        pass

    # ===============================================================
    # 29. Главный цикл работы редактора: отрисовка интерфейса и
    #     ожидание нажатия клавиш от пользователя.
    # ---------------------------------------------------------------
    def run(self):
        # Удаляем sleep для более отзывчивого интерфейса
        while True:
            try:
                self.draw_screen()
                self.stdscr.keypad(True)
                key = self.stdscr.getch()
                self.handle_input(key)
            except KeyboardInterrupt:
                # Обработка Ctrl+C
                self.exit_editor()
            except Exception as e:
                logging.exception("Unhandled exception in main loop")
                self.status_message = f"Error: {str(e)}"


####################################################################
# 30. Инициализация редактора с учётом локали, кодировки вывода
#     и обработкой аргументов командной строки.
# -------------------------------------------------------------------
def main(stdscr):
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


# ==================== Main Entry Point ====================
# 31. :) Точка входа в приложение
# -----------------------------------------------------------
if __name__ == "__main__":
    config = load_config()
    print("Конфигурация загружена:")
    print(config)
    try:
        curses.wrapper(main)
    except Exception as e:
        logging.exception("Unhandled exception in main")
        print("An error occurred. See editor.log for details.")
        error_log_path = os.path.join(os.path.dirname(__file__), "error.log")
        with open(error_log_path, "a") as error_file:
            error_file.write(traceback.format_exc())

        print(f"Ошибка запуска редактора: {e}")
        print(f"Ошибка запуска редактора. Подробности в {error_log_path}.")
        sys.exit(1)