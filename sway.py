#!/usr/bin/env python3
import curses
import locale
import toml
import time
import traceback
import os
import re
import sys
import logging


CONFIG_FILE = "config.toml"  # Имя файла конфигурации редактора в формате TOML

# Настройка системы логирования для записи событий и ошибок в файл editor.log
logging.basicConfig(filename='editor.log', level=logging.DEBUG,
                    format='%(asctime)s - %(levelname)s - %(message)s (%(filename)s:%(lineno)d)')


#=================================================================
# 1. Загрузка конфигурации из файла конфигурации (`config.toml`)
#    с применением значений по умолчанию, если файл отсутствует 
#    или содержит ошибки.
#-----------------------------------------------------------------
def load_config():
    """Add default configuration values"""
    default_config = {
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
        "syntax_highlighting": {},
        "supported_formats": {}
    }
    
    try:
        with open(CONFIG_FILE, "r") as f:
            config_content = f.read()
            try:
                user_config = toml.loads(config_content)
                return {**default_config, **user_config}
            except toml.TomlDecodeError as e:
                logging.error(f"TOML parse error: {str(e)}")
                logging.error(f"Config content:\n{config_content}")
                return default_config
    except FileNotFoundError:
        logging.warning(f"Config file '{CONFIG_FILE}' not found. Using defaults.")
        return default_config


class SwayEditor:
    """
    Основной класс редактора Sway.
    
    Реализует функциональность консольного редактора для Linux:
    - Редактирование текстовых и конфигурационных файлов
    - Подсветка синтаксиса различных форматов (YAML, TOML, JSON и др.)
    - Горячие клавиши и удобство навигации, ориентированное на DevOps-задачи
    """
   
    #-----------------------------------------------------------------
    # 2. Инициализация редактора: загрузка конфигурации,
    #    установка начальных параметров редактора, включение цветов и 
    #    настройка привязок клавиш.
    #-----------------------------------------------------------------
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.config = load_config()
        self.text = [""]
        self.cursor_x = 0
        self.cursor_y = 0
        self.scroll_top = 0
        self.scroll_left = 0
        self.filename = "noname"
        self.modified = False
        self.encoding = "UTF-8"  # Добавляем информацию о кодировке
        self.stdscr.keypad(True)  # Enable keypad
        self.stdscr.nodelay(False)  # Синхронный режим ввода
        # Установка локали для Unicode
        locale.setlocale(locale.LC_ALL, '')
        # Включаем поддержку цветов
        curses.start_color()
        curses.use_default_colors()
        # Устанавливаем режим курсора
        curses.curs_set(1)  # Видимый курсор
        self.insert_mode = True
        self.syntax_highlighting = {}
        self.status_message = ""

        curses.start_color()
        curses.use_default_colors()
        self.init_colors()
        curses.curs_set(1)

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


    #---------------------------------------------------------------
    # 3. Установка начальной позиции курсора и параметров прокрутки текста.
    #---------------------------------------------------------------
    def set_initial_cursor_position(self):
        self.cursor_x = 0
        self.cursor_y = 0
        self.scroll_top = 0
        self.scroll_left = 0


    #---------------------------------------------------------------
    # 4. Инициализация цветовых пар для выделения синтаксиса и элементов интерфейса.
    #---------------------------------------------------------------
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
            'error': curses.color_pair(8),
            'line_number': curses.color_pair(7),
            'status': curses.color_pair(6),
            'comment': curses.color_pair(1),
            'keyword': curses.color_pair(2),
            'string': curses.color_pair(3),
            'variable': curses.color_pair(6),
            'punctuation': curses.color_pair(6),
            'literal': curses.color_pair(4),
            'decorator': curses.color_pair(5),
            'type': curses.color_pair(4),
            'selector': curses.color_pair(2),
            'property': curses.color_pair(5),
            'tag': curses.color_pair(2),
            'attribute': curses.color_pair(3),
        }


    #---------------------------------------------------------------
    # 5. Применение синтаксической подсветки текста с использованием 
    #    заранее скомпилированных регулярных выражений.
    #---------------------------------------------------------------
    def apply_syntax_highlighting(self, line, lang):
        """Cache compiled regex patterns"""
        if not hasattr(self, '_compiled_patterns'):
            self._compiled_patterns = {}
        
        if lang not in self._compiled_patterns:
            self._compiled_patterns[lang] = [
                (re.compile(pattern), color)
                for pattern, color in self.syntax_highlighting.get(lang, [])
            ]

        if not line.strip():
            return [(line, curses.color_pair(0))]

        if lang not in self.syntax_highlighting:
            return [(line, curses.color_pair(0))]

        all_matches = []
        for pattern, color_pair in self._compiled_patterns[lang]:
            for match in pattern.finditer(line):
                start, end = match.span()
                all_matches.append((start, end, color_pair))

        if not all_matches:
            return [(line, curses.color_pair(0))]

        all_matches.sort()
        result = []
        last_end = 0

        for start, end, color_pair in all_matches:
            if start > last_end:
                result.append((line[last_end:start], curses.color_pair(0)))
            result.append((line[start:end], color_pair))
            last_end = end

        if last_end < len(line):
            result.append((line[last_end:], curses.color_pair(0)))

        return result


    #--------------------------------------------------------------
    # 6. Загрузка и компиляция правил синтаксической подсветки из 
    #    конфигурационного файла.
    #--------------------------------------------------------------
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


    #--------------------------------------------------------------
    # 7. Отрисовка экрана редактора, включая строки текста, 
    #    номера строк, статусную строку и позиционирование курсора.
    #--------------------------------------------------------------
    def draw_screen(self):
        self.stdscr.clear()
        height, width = self.stdscr.getmaxyx()

        if height < 24 or width < 80:
            try:
                self.stdscr.addstr(0, 0, "Window too small (min: 80x24)", self.colors['error'])
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
                self.stdscr.addstr(screen_row, 0, line_num_format.format(line_num), self.colors['line_number'])
            except curses.error:
                pass

            line = self.text[line_num - 1] if line_num <= len(self.text) else ""
            syntax_line = self.apply_syntax_highlighting(line, self.detect_language())
            x_pos = 0

            for text_part, color in syntax_line:
                if x_pos + len(text_part.encode('utf-8')) <= self.scroll_left:
                    x_pos += len(text_part.encode('utf-8'))
                    continue

                visible_start = max(0, self.scroll_left - x_pos)
                visible_part = text_part[visible_start:]
                # Calculate visible width considering UTF-8 characters
                visible_width = len(visible_part.encode('utf-8'))
                visible_part = visible_part[:text_width - (x_pos - self.scroll_left)]
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
                f"Type: {file_type} | "  # Добавляем информацию о типе файла
                f"Encoding: {self.encoding} | "  # Добавляем информацию о кодировке
                f"Line: {self.cursor_y + 1}/{len(self.text)} | "
                f"Column: {self.cursor_x + 1} | "
                f"Mode: {'Insert' if self.insert_mode else 'Replace'}"
            )
            self.stdscr.addstr(status_y, 0, " " * (width - 1), self.colors['status'])
            self.stdscr.addstr(status_y, 0, status_msg, self.colors['status'])
        except curses.error:
            pass

        cursor_screen_y = self.cursor_y - self.scroll_top
        cursor_screen_x = self.cursor_x - self.scroll_left + line_num_width

        if (0 <= cursor_screen_y < visible_lines and
            0 <= cursor_screen_x < width):
            try:
                self.stdscr.move(cursor_screen_y, cursor_screen_x)
            except curses.error:
                pass

        self.stdscr.refresh()


    #----------------------------------------------------------------
    # 8. Определение языка файла на основе его расширения для последующего 
    #    применения синтаксической подсветки.
    #----------------------------------------------------------------
    def detect_language(self):
        ext = os.path.splitext(self.filename)[1].lower()
        for lang, exts in self.config.get("supported_formats", {}).items():
            if ext in exts:
                return lang
        return "text"


    #################################################################
    # 9. Обработка нажатых клавиш: 
    # 
    #   обработка специальных команд (открыть, сохранить и т.д.) 
    #   и ввод текста.
    #----------------------------------------------------------------
    def handle_input(self, key):
        if key == -1:
            return

        try:
            # Проверка горячих клавиш
            if key == self.keybindings.get("open_file"):
                self.open_file()
                return
            if key == self.keybindings.get("save_file"):
                self.save_file()
                return
            if key == self.keybindings.get("quit"):
                self.exit_editor()
                return
            
            # TODO: Добавляем остальные горячие клавиши
            if key == self.keybindings.get("delete"):
                self.handle_delete()
                return
            if key == self.keybindings.get("paste"):
                # Заглушка для функционала paste
                self.status_message = "Paste not implemented yet"
                return
            if key == self.keybindings.get("copy"):
                # Заглушка для функционала copy
                self.status_message = "Copy not implemented yet"
                return
            if key == self.keybindings.get("cut"):
                # Заглушка для функционала cut
                self.status_message = "Cut not implemented yet"
                return
            if key == self.keybindings.get("undo"):
                # Заглушка для функционала undo
                self.status_message = "Undo not implemented yet"
                return
            if key == self.keybindings.get("select_all"):
                # Заглушка для функционала select_all
                self.status_message = "Select all not implemented yet"
                return

            # Handle Enter key
            if key == ord('\n'):
                current_line = self.text[self.cursor_y]
                remaining = current_line[self.cursor_x:]
                self.text[self.cursor_y] = current_line[:self.cursor_x]
                self.text.insert(self.cursor_y + 1, remaining)
                self.cursor_y += 1
                self.cursor_x = 0
                self.modified = True
                return

            # Function keys and arrow keys
            special_keys = {
                curses.KEY_UP: self.handle_up,
                curses.KEY_DOWN: self.handle_down,
                curses.KEY_LEFT: self.handle_left,
                curses.KEY_RIGHT: self.handle_right,
                curses.KEY_HOME: self.handle_home,
                curses.KEY_END: self.handle_end,
                curses.KEY_PPAGE: self.handle_page_up,
                curses.KEY_NPAGE: self.handle_page_down,
                curses.KEY_DC: self.handle_delete,
                curses.KEY_BACKSPACE: self.handle_backspace,
                127: self.handle_backspace,  # Additional backspace code
                ord('\b'): self.handle_backspace  # Another backspace code
            }

            if key in special_keys:
                special_keys[key]()
                return

             # Regular character input
            if 32 <= key <= 126 or key > 127:
                self.handle_char_input(key)

        except Exception as e:
            logging.exception("Error handling input")
            self.status_message = f"Input error: {str(e)}"


    #---------------------------------------------------------------
    # 10. Перемещение курсора вверх по строкам.
    #     Клаваиша `Arr Up`.
    #---------------------------------------------------------------
    def handle_up(self):
        if self.cursor_y > 0:
            self.cursor_y -= 1
            self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))



    #--------------------------------------------------------------
    # 11. Перемещение курсора вниз по строкам.
    #     Клаваиша `Arr Down`.
    #--------------------------------------------------------------    
    def handle_down(self):
        if self.cursor_y < len(self.text) - 1:
            self.cursor_y += 1
            self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))


    #---------------------------------------------------------------
    # 12. Перемещение курсора влево на один символ или на предыдущую
    #     строку. Клаваиша `<-`.
    #---------------------------------------------------------------
    def handle_left(self):
        if self.cursor_x > 0:
            self.cursor_x -= 1
        elif self.cursor_y > 0:
            self.cursor_y -= 1
            self.cursor_x = len(self.text[self.cursor_y])


    #---------------------------------------------------------------
    # 13. Перемещение курсора вправо на один символ или на следующую
    #     строку. Клаваиша `->`.
    #---------------------------------------------------------------
    def handle_right(self):
        if self.cursor_x < len(self.text[self.cursor_y]):
            self.cursor_x += 1
        elif self.cursor_y < len(self.text) - 1:
            self.cursor_y += 1
            self.cursor_x = 0


    #---------------------------------------------------------------
    # 14. Перемещение курсора в начало текущей строки. 
    #     Клаваиша `Home`.
    #---------------------------------------------------------------
    def handle_home(self):
        self.cursor_x = 0


    #---------------------------------------------------------------
    # 15. Перемещение курсора в конец текущей строки. 
    #     Клаваиша `End`.
    #---------------------------------------------------------------
    def handle_end(self):
        self.cursor_x = len(self.text[self.cursor_y])


    #---------------------------------------------------------------
    # 16. Перемещение курсора вверх на страницу (на 10 строк). 
    #     Клаваиша `PageUp`.
    #---------------------------------------------------------------
    def handle_page_up(self):
        self.cursor_y = max(0, self.cursor_y - 10)
        self.scroll_top = max(0, self.scroll_top - 10)
        self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))


    #---------------------------------------------------------------
    # 17. Перемещение курсора вниз на страницу (на 10 строк).
    #     Клаваиша `PageDown`.
    #---------------------------------------------------------------
    def handle_page_down(self):
        self.cursor_y = min(len(self.text) - 1, self.cursor_y + 10)
        self.scroll_top = min(len(self.text) - 1, self.scroll_top + 10)
        self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))


    #---------------------------------------------------------------
    # 18. Удаление символа под курсором или объединение текущей 
    #     строки со следующей. Клаваиша `Delete`.
    #---------------------------------------------------------------
    def handle_delete(self):
        if self.cursor_x < len(self.text[self.cursor_y]):
            self.text[self.cursor_y] = (
                self.text[self.cursor_y][:self.cursor_x] +
                self.text[self.cursor_y][self.cursor_x + 1:]
            )
            self.modified = True
        elif self.cursor_y < len(self.text) - 1:
            # Если курсор в конце строки и есть следующая строка,
            # объединяем текущую строку со следующей
            self.text[self.cursor_y] += self.text[self.cursor_y + 1]
            del self.text[self.cursor_y + 1]
            self.modified = True


    #---------------------------------------------------------------
    # 19. Удаление символа слева от курсора или объединение текущей 
    #     строки с предыдущей. Клаваиша `Backspace`.
    #---------------------------------------------------------------
    def handle_backspace(self):
        if self.cursor_x > 0:
            line = self.text[self.cursor_y]
            self.text[self.cursor_y] = line[:self.cursor_x - 1] + line[self.cursor_x:]
            self.cursor_x -= 1
            self.modified = True
        elif self.cursor_y > 0:
            prev_line = self.text[self.cursor_y - 1]
            self.cursor_x = len(prev_line)
            self.text[self.cursor_y - 1] += self.text[self.cursor_y]
            del self.text[self.cursor_y]
            self.cursor_y -= 1
            self.modified = True



    #---------------------------------------------------------------
    # 19a. Начало и конец выделения текста (для копирования/вырезания).
    #---------------------------------------------------------------
    def start_selection(self):
        """TODO: Начало выделения текста."""
        pass

    def end_selection(self):
        """TODO: Конец выделения текста."""
        pass


    #---------------------------------------------------------------
    # 19b. Копирование выделенного текста в буфер обмена.
    #---------------------------------------------------------------
    def copy_selection(self):
        """TODO: Копирование выделенного фрагмента текста."""
        pass


    #---------------------------------------------------------------
    # 19c. Вырезание выделенного текста в буфер обмена.
    #---------------------------------------------------------------
    def cut_selection(self):
        """TODO: Вырезание выделенного текста."""
        pass


    #---------------------------------------------------------------
    # 19d. Вставка текста из буфера обмена.
    #---------------------------------------------------------------
    def paste_from_clipboard(self):
        """TODO: Вставка текста из буфера обмена."""
        pass


    #---------------------------------------------------------------
    # 19e. Отмена и повтор последних действий.
    #---------------------------------------------------------------
    def undo(self):
        """TODO: Отмена последнего действия."""
        pass

    def redo(self):
        """TODO: Повтор последнего отменённого действия."""
        pass


    #---------------------------------------------------------------
    # 20. Ввод обычного печатного символа в текущую позицию курсора.
    #---------------------------------------------------------------
    def handle_char_input(self, key):
        try:
            char = chr(key)
            current_line = self.text[self.cursor_y]
            if self.insert_mode:
                self.text[self.cursor_y] = (
                    current_line[:self.cursor_x] +
                    char +
                    current_line[self.cursor_x:]
                )
            else:
                self.text[self.cursor_y] = (
                    current_line[:self.cursor_x] +
                    char +
                    (current_line[self.cursor_x + 1:] if self.cursor_x < len(current_line) else '')
                )
            self.cursor_x += 1
            self.modified = True
        except (ValueError, UnicodeEncodeError):
            logging.error(f"Cannot encode character: {key}")


    #===============================================================
    # 21. Преобразование строки с описанием горячих клавиш из 
    #     конфигурации в соответствующий код клавиши.
    #---------------------------------------------------------------
    def parse_key(self, key_str):
        if not key_str:
            return -1

        parts = key_str.split("+")
        if len(parts) == 2 and parts[0].lower() == "ctrl":
            return ord(parts[1].lower()) - ord('a') + 1
        elif key_str.lower() == "del":
            return curses.KEY_DC
        elif key_str.lower() == "insert":
            return curses.KEY_IC
        try:
            return ord(key_str)
        except TypeError:
            return -1


    #---------------------------------------------------------------
    # 22. Расчёт ширины символа с учётом особенностей UTF-8
    #     и отображения полушироких и полношироких символов.
    #---------------------------------------------------------------
    def get_char_width(self, char):
        """Calculate the display width of a character"""
        try:
            if ord(char) < 128:
                return 1
            # Используем east_asian_width для определения ширины символа
            import unicodedata
            width = unicodedata.east_asian_width(char)
            if width in ('F', 'W'):  # Full-width characters
                return 2
            elif width == 'A':  # Ambiguous width
                return 2
            else:
                return 1
        except (UnicodeEncodeError, TypeError):
            return 1


    #=================================================================
    # 23. Открытие указанного пользователем файла с автоматическим
    #     определением кодировки и загрузкой содержимого в редактор.
    #     Модуль `сhardet`.
    #-----------------------------------------------------------------
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
            import chardet
            with open(filename, 'rb') as f:
                result = chardet.detect(f.read())
                self.encoding = result['encoding'] or 'UTF-8'
            
            with open(filename, "r", encoding=self.encoding, errors='replace') as f:
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
                with open(filename, "r", encoding="utf-8", errors='replace') as f:
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


    #---------------------------------------------------------------
    # 24. Сохранение текущего содержимого редактора в файл с 
    #     проверкой разрешений на запись.
    #---------------------------------------------------------------
    def save_file(self):
        if self.filename == "noname":
            self.filename = self.prompt("Save as: ")
            if not self.filename:
                self.status_message = "Save cancelled"
                return

        # Check if the file exists and is writable *before* attempting to open
        if os.path.exists(self.filename):
            if not os.access(self.filename, os.W_OK):
                self.status_message = f"No write permission: {self.filename}"
                return
        try:
            with open(self.filename, "w", encoding=self.encoding, errors='replace') as f:
                f.write(os.linesep.join(self.text))
            self.modified = False
            self.status_message = f"Saved to {self.filename}"
        except OSError as e:
            self.status_message = f"Error saving file: {e}"
            logging.exception(f"Error saving file: {self.filename}")
        except Exception as e:
            self.status_message = f"Error saving file: {e}"
            logging.exception(f"Error saving file: {self.filename}")



    #---------------------------------------------------------------
    # 24a. Сохранение текущего файла под новым именем. 
    # TODO: реализовать
    #---------------------------------------------------------------
    def save_file_as(self):
        """Метод сохранения файла под другим именем."""
        pass


    #---------------------------------------------------------------
    # 24b. Откат изменений к последнему сохранённому состоянию файла. 
    # TODO: реализовать
    #---------------------------------------------------------------
    def revert_changes(self):
        """Откат изменений в текущем файле до последнего сохранения."""
        pass


    #---------------------------------------------------------------
    # 24c. Создание нового пустого файла. 
    # TODO: реализовать
    #---------------------------------------------------------------
    def new_file(self):
        """Создание нового пустого документа с предварительным запросом на сохранение текущих изменений."""
        pass


    #---------------------------------------------------------------
    # 25. Выход из редактора с предварительным запросом на 
    #     сохранение несохранённых изменений.
    #---------------------------------------------------------------
    def exit_editor(self):
        if self.modified:
            choice = self.prompt("Save changes? (y/n): ")
            if choice and choice.lower().startswith("y"):
                self.save_file()
        curses.endwin()  # Restore terminal state
        sys.exit(0)


    #---------------------------------------------------------------
    # 26. Вывод сообщения пользователю и получение ввода текста
    #     с клавиатуры.
    #---------------------------------------------------------------
    def prompt(self, message):
        self.stdscr.nodelay(False)  # Переключаемся в блокирующий режим
        curses.echo()
        try:
            self.stdscr.addstr(curses.LINES - 1, 0, message)
            self.stdscr.clrtoeol()
            self.stdscr.refresh()
            # Use a larger buffer for UTF-8 input
            response = self.stdscr.getstr(curses.LINES - 1, len(message), 1024).decode('utf-8', errors='replace').strip()
        except Exception as e:
            response = ""
            logging.exception("Prompt error")
        finally:
            curses.noecho()
            self.stdscr.nodelay(False)  # Оставляем в блокирующем режиме для основного цикла
        return response

    #---------------------------------------------------------------
    # 27. Поиск заданного текста по всему документу и возврат 
    #     позиций найденных совпадений.
    #---------------------------------------------------------------
    def search_text(self, search_term):
        """Add search functionality"""
        matches = []
        for line_num, line in enumerate(self.text):
            for match in re.finditer(re.escape(search_term), line):
                matches.append((line_num, match.start(), match.end()))
        return matches


    #---------------------------------------------------------------
    # 28. Проверка имени файла на корректность, длину и допустимый путь.
    #---------------------------------------------------------------
    def validate_filename(self, filename):
        """Add filename validation"""
        if not filename or len(filename) > 255:
            return False
        if os.path.isabs(filename):
            base_dir = os.path.dirname(os.path.abspath(filename))
            return os.path.commonpath([base_dir, os.getcwd()]) == os.getcwd()
        return True



    #---------------------------------------------------------------
    # 28a. Выполнение произвольной shell-команды.
    # TODO: реализовать
    #---------------------------------------------------------------
    def execute_shell_command(self):
        """Выполнение shell-команды из редактора."""
        pass


    #---------------------------------------------------------------
    # 28b. Простая интеграция с Git (commit, push, pull, diff).  
    # TODO: реализовать
    #---------------------------------------------------------------
    def integrate_git(self):
        """Интеграция основных команд Git."""
        pass


    #---------------------------------------------------------------
    # 28с. Переход к конкретной строке документа. 
    # TODO: реализовать
    #---------------------------------------------------------------
    def goto_line(self):
        """Переход к указанной строке."""
        pass


    #---------------------------------------------------------------
    # 28d. Поиск и замена текста с поддержкой регулярных выражений.  
    # TODO: реализовать
    #---------------------------------------------------------------
    def find_and_replace(self):
        """Поиск и замена текста."""
        pass


    #---------------------------------------------------------------
    # 28e. Переключение режима вставки/замены.  
    # TODO: реализовать
    #---------------------------------------------------------------
    def toggle_insert_mode(self):
        """Переключение между Insert и Replace режимами."""
        pass


    #---------------------------------------------------------------
    # 28f. Подсветка парных скобок в редакторе.
    # TODO: реализовать
    #---------------------------------------------------------------
    def highlight_matching_brackets(self):
        """Подсветка парных скобок."""
        pass


    #---------------------------------------------------------------
    # 28i. Поиск и замена текста с поддержкой регулярных выражений.
    # TODO: реализовать
    #---------------------------------------------------------------
    def search_and_replace(self):
        """Поиск и замена текста с поддержкой regex."""
        pass


    #---------------------------------------------------------------
    # 28j. Сохранение и восстановление сессии.
    # TODO: реализовать
    #---------------------------------------------------------------
    def session_save(self):
        """Сохранение текущей сессии редактора."""
        pass

    def session_restore(self):
        """Восстановление сессии редактора."""
        pass


    #---------------------------------------------------------------
    # 28k. Включение и отключение автосохранения.
    # TODO: реализовать
    #---------------------------------------------------------------
    def toggle_auto_save(self):
        """Включение/отключение функции автосохранения."""
        pass


    #---------------------------------------------------------------
    # 28l. Шифрование и дешифрование текущего файла. 
    # TODO: реализовать
    #---------------------------------------------------------------
    def encrypt_file(self):
        """Шифрование текущего файла."""
        pass

    def decrypt_file(self):
        """Дешифрование текущего файла."""
        pass


    #---------------------------------------------------------------
    # 28m. Валидация конфигурационных файлов перед сохранением. 
    # TODO: реализовать
    #---------------------------------------------------------------
    def validate_configuration(self):
        """Проверка YAML/TOML/JSON файлов перед сохранением."""
        pass



    #===============================================================
    # 29. Главный цикл работы редактора: отрисовка интерфейса и 
    #     ожидание нажатия клавиш от пользователя.
    #---------------------------------------------------------------
    def run(self):
        # Удаляем sleep для более отзывчивого интерфейса
        while True:
            try:
                self.draw_screen()
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
#-------------------------------------------------------------------
def main(stdscr):
    # Setup locale for Unicode
    os.environ['LANG'] = 'en_US.UTF-8'
    locale.setlocale(locale.LC_ALL, '')
    
    # Setup stdout encoding
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, errors='replace')
    
    # Create editor instance
    editor = SwayEditor(stdscr)
    try:
        if len(sys.argv) > 1:
            # Если указано имя файла в аргументах командной строки
            editor.filename = sys.argv[1]
            editor.open_file()
    except Exception as e:
        logging.exception(f"Error opening file from command line: {e}")
    
    editor.run()



#---------------------------------------------------------------
# 31. 
#---------------------------------------------------------------
if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except Exception as e:
        logging.exception("Unhandled exception in main")
        print(f"An error occurred. See editor.log for details.")
        sys.exit(1)
