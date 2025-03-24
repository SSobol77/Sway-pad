#!/usr/bin/env python3
import curses
import locale
import toml
import os
import re
import sys
import time
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


def run_pylint_on_code(code, filename="tmp.py"):
    """
    Executes pylint on the provided code string and returns the linter output.
    """
    # Skip analysis if code exceeds 100000 characters
    if len(code) > 100000:
        return "File is too large for pylint analysis"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tmp:
        tmp.write(code)
        tmp_name = tmp.name
    try:
        result = subprocess.run(
            ["pylint", tmp_name, "--output-format=text"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
        )
        output = result.stdout.strip()
        return output
    except subprocess.TimeoutExpired:
        return "Pylint: time limit exceeded."
    except Exception as e:
        return f"Pylint error: {str(e)}"
    finally:
        try:
            os.remove(tmp_name)
        except Exception:
            pass


logging.basicConfig(
    filename="editor.log",
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)",
)


def deep_merge(base, override):
    """
    Recursively merges two dictionaries.
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
    Loads configuration from config.toml, falling back to defaults if not found or invalid.
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
            "error": "red",
            "status": "bright_white",
            "variable": "white",
            "tag": "blue",
            "attribute": "cyan",
            "magic": "magenta",
            "builtin": "yellow",
            "exception": "red",
            "function": "light_blue",
            "class": "yellow",
            "number": "magenta",
            "operator": "white",
            "escape": "cyan",
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
        },
        "editor": {
            "show_line_numbers": True,
            "tab_size": 4,
            "use_spaces": True,
            "word_wrap": False,
            "auto_indent": True,
            "auto_brackets": True,
        },
        "supported_formats": {
            "python": [".py"],
            "toml": [".toml"],
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
                "cursor": "#AEAFAD",
            },
        },
        "file_icons": {
            "text": "📄",
            "code": "📝",
            "css": "🎨",
            "html": "🌐",
            "json": "📊",
            "yaml": "⚙️",
            "folder": "📁",
            "folder_open": "📂",
        },
        "syntax_highlighting": {
            "python": {
                "patterns": [
                    {
                        "pattern": r"\b(and|as|assert|async|await|break|class|continue|def|del|elif|else|except|exec|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|print|raise|return|try|while|with|yield)\b",
                        "color": "keyword",
                    },
                    {"pattern": r"@\w+(?:\([^)]*?\))?", "color": "decorator"},
                    {
                        "pattern": r"(?s)(f|r|rf|fr)?('''(.|\n)*?'''|\"\"\"(.|\n)*?\"\"\")",
                        "color": "string",
                    },
                    {
                        "pattern": r"(f|r|rf|fr|b|br|rb)?(['\"])(?:\\.|(?!\2).)*\2",
                        "color": "string",
                    },
                    {
                        "pattern": r"\b(?:\d+\.\d+|\.\d+|\d+)(?:e[+-]?\d+)?j?\b",
                        "color": "literal",
                    },
                    {"pattern": r"\b0[bB][01_]+\b", "color": "literal"},
                    {"pattern": r"\b0[oO][0-7_]+\b", "color": "literal"},
                    {"pattern": r"\b0[xX][0-9a-fA-F_]+\b", "color": "literal"},
                    {"pattern": r"#.*$", "color": "comment"},
                    {"pattern": r'"""(.|\n)*?"""', "color": "comment"},
                    {"pattern": r"'''(.|\n)*?'''", "color": "comment"},
                    {
                        "pattern": r"\b(ArithmeticError|AssertionError|AttributeError|BaseException|BlockingIOError|BrokenPipeError|BufferError|BytesWarning|ChildProcessError|ConnectionAbortedError|ConnectionError|ConnectionRefusedError|ConnectionResetError|DeprecationWarning|EOFError|Ellipsis|EncodingWarning|EnvironmentError|Exception|FileExistsError|FileNotFoundError|FloatingPointError|FutureWarning|GeneratorExit|IOError|ImportError|ImportWarning|IndentationError|IndexError|InterruptedError|IsADirectoryError|KeyError|KeyboardInterrupt|LookupError|MemoryError|ModuleNotFoundError|NameError|NotADirectoryError|NotImplemented|NotImplementedError|OSError|OverflowError|PendingDeprecationWarning|PermissionError|ProcessLookupError|RecursionError|ReferenceError|ResourceWarning|RuntimeError|RuntimeWarning|StopAsyncIteration|StopIteration|SyntaxError|SyntaxWarning|SystemError|SystemExit|TabError|TimeoutError|TypeError|UnboundLocalError|UnicodeDecodeError|UnicodeEncodeError|UnicodeError|UnicodeTranslateError|UnicodeWarning|UserWarning|ValueError|Warning|ZeroDivisionError|__import__|abs|all|any|ascii|bin|bool|breakpoint|bytearray|bytes|callable|chr|classmethod|compile|complex|copyright|credits|delattr|dict|dir|divmod|enumerate|eval|exec|exit|filter|float|format|frozenset|getattr|globals|hasattr|hash|help|hex|id|input|int|isinstance|issubclass|iter|len|license|list|locals|map|max|memoryview|min|next|object|oct|open|ord|pow|print|property|range|repr|reversed|round|set|setattr|slice|sorted|staticmethod|str|sum|super|tuple|type|vars|zip)\b",
                        "color": "builtin",
                    },
                    {
                        "pattern": r"\b(List|Dict|Tuple|Set|Optional|Union|Any|Callable|TypeVar|Generic|Iterable|Iterator|Sequence|Mapping|MutableMapping|Awaitable|Coroutine|AsyncIterable|NamedTuple|TypedDict|Final|Literal|Annotated|TypeGuard|Self|Protocol|dataclass|field|classmethod|staticmethod)\b",
                        "color": "type",
                    },
                    {"pattern": r"r[\"'].*?[\"']", "color": "regexp"},
                    {
                        "pattern": r"\b(True|False|None|Ellipsis|NotImplemented)\b",
                        "color": "literal",
                    },
                    {
                        "pattern": r"__(?:init|new|str|repr|enter|exit|getattr|setattr|delattr|getitem|setitem|delitem|iter|next|call|len|contains|add|sub|mul|truediv|floordiv|mod|pow|lshift|rshift|and|or|xor|invert|eq|ne|lt|le|gt|ge|bool|bytes|format|hash|dir|sizeof|getstate|setstate|reduce|reduce_ex|subclasshook|del|doc|name|qualname|module|defaults|kwdefaults|annotations|dict|weakref|slots|class|self|cls)__(?=\()",
                        "color": "magic",
                    },
                    {"pattern": r"\bimport\s+\w+(?:\.\w+)*\b", "color": "import"},
                    {
                        "pattern": r"\bfrom\s+\w+(?:\.\w+)*\s+import\b",
                        "color": "import",
                    },
                ]
            },
            "javascript": {
                "patterns": [
                    {"pattern": r"//.*$", "color": "comment"},
                    {"pattern": r"/\*[\s\S]*?\*/", "color": "comment"},
                    {
                        "pattern": r"\b(let|const|var|function|return|if|else|for|while|do|switch|case|break|continue|try|catch|finally|new|delete|typeof|instanceof|this|class|extends|super|import|export|from|as|async|await|yield)\b",
                        "color": "keyword",
                    },
                    {"pattern": r"`[^`]*`", "color": "string"},
                    {"pattern": r"\"[^\"]*\"", "color": "string"},
                    {
                        "pattern": r"\b(\d+(\.\d+)?|true|false|null|undefined|NaN|Infinity)\b",
                        "color": "literal",
                    },
                    {"pattern": r"console\.log", "color": "keyword"},
                    {"pattern": r"\$\{[^}]*\}", "color": "literal"},
                ]
            },
        },
    }
    try:
        with open("config.toml", "r", encoding="utf-8") as f:
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
        logging.warning("Config file 'config.toml' not found. Using defaults.")
        return default_config


"""    
def get_file_icon(filename, config):
   #"
   # Returns the icon for a file based on its extension.
   #
    for key, value in config["file_icons"].items():
        if filename.endswith(tuple(config["supported_formats"].get(key, []))):
            return value
    return config["file_icons"]["text"]
"""


class SwayEditor:
    """
    Main class for the Sway editor.
    """

    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.stdscr.keypad(True)  # Enable keypad mode
        curses.raw()  # Raw mode for better key handling
        curses.nonl()  # Do not translate the Enter key
        curses.noecho()  # Do not echo user input
        self.config = load_config()  # Load configuration from config.toml
        self.text = [""]
        self.cursor_x = 0
        self.cursor_y = 0
        self.scroll_top = 0
        self.scroll_left = 0
        self.filename = "new_file.py"
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
            "open_file": self.parse_key(
                self.config["keybindings"].get("open_file", "ctrl+o")
            ),
            "save_file": self.parse_key(
                self.config["keybindings"].get("save_file", "ctrl+s")
            ),
            "select_all": self.parse_key(
                self.config["keybindings"].get("select_all", "ctrl+a")
            ),
            "quit": self.parse_key(self.config["keybindings"].get("quit", "ctrl+q")),
        }
        self.load_syntax_highlighting()
        self.set_initial_cursor_position()

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

    def run_pylint_async(self, code):
        """
        Runs pylint asynchronously. The first 200 characters of lint output are shown in the status bar.
        """
        lint_output = run_pylint_on_code(code)
        if lint_output:
            self.status_message = f"Pylint: {lint_output[:200]}..."
        else:
            self.status_message = f"Saved to {self.filename} with no pylint warnings."

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
            elif key == self.keybindings["quit"] or key == 17 or key == 3:  # Quit
                self.exit_editor()
            elif key == self.keybindings["save_file"] or key == 19:  # Save
                self.save_file()
            elif key == self.keybindings["open_file"] or key == 15:  # Open
                self.open_file()
            # Regular character input
            elif 32 <= key <= 126:  # Printable ASCII
                self.handle_char_input(key)

        except Exception as e:
            self.status_message = f"Input error: {str(e)}"
            logging.exception("Error handling input")


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


    def handle_delete(self):
        """Deletes the character under the cursor or joins with the next line if at end."""
        if self.cursor_x < len(self.text[self.cursor_y]):
            line = self.text[self.cursor_y]
            self.text[self.cursor_y] = line[: self.cursor_x] + line[self.cursor_x + 1 :]
            self.modified = True
        elif self.cursor_y < len(self.text) - 1:
            self.text[self.cursor_y] += self.text.pop(self.cursor_y + 1)
            self.modified = True


    def handle_backspace(self):
        """Deletes the character to the left of the cursor or joins with the previous line."""
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


    def start_selection(self):
        """TODO: Start text selection."""
        pass


    def end_selection(self):
        """TODO: End text selection."""
        pass


    def copy_selection(self):
        """TODO: Copy selected text to clipboard."""
        pass


    def cut_selection(self):
        """TODO: Cut selected text."""
        pass


    def paste_from_clipboard(self):
        """TODO: Paste text from clipboard."""
        pass


    def undo(self):
        """TODO: Undo the last action."""
        pass


    def redo(self):
        """TODO: Redo the last undone action."""
        pass


    def handle_char_input(self, key):
        """Handles regular character input."""
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
        """Handles the Enter key, creating a new line at the cursor position."""
        self.text.insert(self.cursor_y + 1, "")
        content = self.text[self.cursor_y][self.cursor_x :]
        self.text[self.cursor_y] = self.text[self.cursor_y][: self.cursor_x]
        self.text[self.cursor_y + 1] = content
        self.cursor_y += 1
        self.cursor_x = 0
        self.modified = True


    def parse_key(self, key_str):
        """
        Converts a hotkey description string into the corresponding key code.
        """
        if not key_str:
            return -1

        parts = key_str.split("+")
        if len(parts) == 2 and parts[0].lower() == "ctrl":
            return ord(parts[1].lower()) - ord("a") + 1
        elif key_str.lower() == "del":
            return curses.KEY_DC
        elif key_str.lower() == "insert":
            return curses.KEY_IC
        try:
            return ord(key_str)
        except TypeError:
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
                target=self.run_pylint_async, args=(code,), daemon=True
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
                target=self.run_pylint_async, args=(code,), daemon=True
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


    def session_save(self):
        """Saves the current editor session. (Not implemented)"""
        pass


    def session_restore(self):
        """Restores the editor session. (Not implemented)"""
        pass


    def toggle_auto_save(self):
        """Enables or disables auto-save functionality."""
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


    def encrypt_file(self):
        """Encrypts the current file. (Not implemented)"""
        pass


    def decrypt_file(self):
        """Decrypts the current file. (Not implemented)"""
        pass


    def validate_configuration(self):
        """Validates YAML/TOML/JSON config files before saving. (Not implemented)"""
        pass


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
