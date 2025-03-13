#!/usr/bin/env python3
import curses
import toml
import re
import os

CONFIG_FILE = "config.toml"

def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return toml.load(f)
    except FileNotFoundError:
        print(f"Config file '{CONFIG_FILE}' not found.")
        exit(1)
    except toml.TomlDecodeError as e:
        print(f"Error parsing config file: {e}")
        exit(1)


class SwayEditor:
    # Инициализация редактора
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.config = load_config()
        self.text = [""]  # Начальный текст (пустая строка)
        self.cursor_x = 0  # Начальная позиция X (после номера строки)
        self.cursor_y = 0  # Начальная позиция Y
        self.scroll_top = 0
        self.scroll_left = 0
        self.filename = "noname"
        self.modified = False
        self.syntax_rules = {}
        self.status_message = ""

        # Инициализация цветов
        curses.start_color()
        curses.use_default_colors()
        self.init_colors()
        # Настройка видимости курсора
        curses.curs_set(2)  # Включаем видимый курсор
        self.cursor_color = curses.color_pair(5)  # Новый цвет для курсора

        # Настройка горячих клавиш
        self.keybindings = {
            "delete": self.parse_key(self.config["keybindings"]["delete"]),
            "paste": self.parse_key(self.config["keybindings"]["paste"]),
            "copy": self.parse_key(self.config["keybindings"]["copy"]),
            "cut": self.parse_key(self.config["keybindings"]["cut"]),
            "undo": self.parse_key(self.config["keybindings"]["undo"]),
            "open_file": self.parse_key(self.config["keybindings"]["open_file"]),
            "save_file": self.parse_key(self.config["keybindings"]["save_file"]),
            "select_all": self.parse_key(self.config["keybindings"]["select_all"]),
            "quit": self.parse_key(self.config["keybindings"].get("quit", "ctrl+q")),
        }

        # Подсветка синтаксиса
        self.load_syntax_highlighting()

        # Установка начальной позиции курсора
        self.set_initial_cursor_position()

    # Устанавливает начальную позицию курсора на 1:2
    def set_initial_cursor_position(self):
        self.cursor_y = 0  # Первая строка
        max_line_num = len(str(len(self.text)))  # Максимальная длина номера строки
        line_num_width = max_line_num + 1  # Ширина номера строки + пробел
        self.cursor_x = line_num_width + 1  # Позиция 1:2 (после номера строки)

        
    # Отрисовка экрана
    def draw_screen(self):
        self.stdscr.clear()
        height, width = self.stdscr.getmaxyx()

        # Проверка минимальных размеров окна
        if height < 5 or width < 50:
            self.stdscr.addstr(0, 0, f"Window too small (min: 5x50)", curses.color_pair(4))
            self.stdscr.refresh()
            return

        # Определение максимальной длины номера строки
        max_line_num = len(str(len(self.text)))
        line_num_format = f"{{:>{max_line_num}}} "
        line_num_width = len(line_num_format.format(0))

        # Отображение текста с номерами строк
        visible_lines = height - 2  # -2 для статусной строки
        start_line = self.scroll_top
        end_line = min(start_line + visible_lines, len(self.text))

        # Обновление вертикальной прокрутки
        if self.cursor_y < self.scroll_top:
            self.scroll_top = self.cursor_y
        elif self.cursor_y >= self.scroll_top + visible_lines:
            self.scroll_top = self.cursor_y - visible_lines + 1

        # Отрисовка видимых строк
        for screen_row in range(visible_lines):
            line_num = start_line + screen_row + 1
            if line_num > len(self.text):
                break

            # Отрисовка номера строки
            try:
                self.stdscr.addstr(screen_row, 0, line_num_format.format(line_num), curses.color_pair(4))
            except curses.error:
                pass

            # Отрисовка содержимого строки
            line = self.text[line_num - 1]
            syntax_line = self.apply_syntax_highlighting(line, "python")
            x_pos = line_num_width - self.scroll_left  # Начальная позиция с учетом прокрутки

            for text_part, color in syntax_line:
                # Пропускаем невидимые части
                if x_pos + len(text_part) < 0:
                    x_pos += len(text_part)
                    continue

                visible_part = text_part[max(0, -x_pos):]
                visible_part = visible_part[:width - line_num_width]

                try:
                    self.stdscr.addstr(screen_row, max(0, x_pos + self.scroll_left), 
                                    visible_part, color)
                except curses.error:
                    pass

                x_pos += len(text_part)


        # Обновление горизонтальной прокрутки
        max_visible_x = width - line_num_width
        cursor_abs_x = self.cursor_x + line_num_width
        if cursor_abs_x < self.scroll_left:
            self.scroll_left = cursor_abs_x
        elif cursor_abs_x >= self.scroll_left + max_visible_x:
            self.scroll_left = cursor_abs_x - max_visible_x + 1


        # Позиционирование курсора
        screen_cursor_y = self.cursor_y - self.scroll_top
        screen_cursor_x = (self.cursor_x + line_num_width) - self.scroll_left


        # Рисуем курсор с желтым цветом
        try:
            self.stdscr.addch(screen_cursor_y, screen_cursor_x, '_', self.cursor_color)
        except curses.error:
            pass

        # Отрисовка статусной строки
        status = f"File: {self.filename} | Pos: {self.cursor_y+1}:{self.cursor_x+1}"
        if self.modified:
            status += " (modified)"
        try:
            self.stdscr.addstr(height-1, 0, status.ljust(width), curses.color_pair(4))
        except curses.error:
            pass

        # Временное сообщение
        if self.status_message:
            try:
                self.stdscr.addstr(height-1, 0, self.status_message.ljust(width), curses.color_pair(4))
                self.status_message = ""
            except curses.error:
                pass

        self.stdscr.refresh()
  
    # Настройка цветов
    def init_colors(self):
        colors = self.config["colors"]
        curses.init_pair(1, self.get_color(colors["keyword_color"]), -1)
        curses.init_pair(2, self.get_color(colors["string_color"]), -1)
        curses.init_pair(3, self.get_color(colors["comment_color"]), -1)
        curses.init_pair(4, curses.COLOR_WHITE, curses.COLOR_BLACK)  # Статусная строка
        curses.init_pair(5, curses.COLOR_YELLOW, -1)  # Цвет курсора
    
    # Получение кода цвета
    def get_color(self, color_name):
        color_map = {
            "black": curses.COLOR_BLACK,
            "white": curses.COLOR_WHITE,
            "red": curses.COLOR_RED,
            "green": curses.COLOR_GREEN,
            "yellow": curses.COLOR_YELLOW,
            "blue": curses.COLOR_BLUE,
            "magenta": curses.COLOR_MAGENTA,
            "cyan": curses.COLOR_CYAN,
        }
        return color_map.get(color_name.lower(), curses.COLOR_WHITE)


    # Парсинг горячих клавиш
    def parse_key(self, key_str):
        parts = key_str.split("+")
        if len(parts) == 2 and parts[0].lower() == "ctrl":
            return ord(parts[1].lower()) - ord('a') + 1
        elif key_str.lower() == "del":
            return curses.KEY_DC
        elif key_str.lower() == "insert":
            return curses.KEY_IC
        else:
            return ord(key_str[-1])
        
        
    # Загрузка правил подсветки синтаксиса
    def load_syntax_highlighting(self):
        for lang, rules in self.config.get("syntax_highlighting", {}).items():
            compiled_rules = []
            for rule in rules:
                pattern = re.compile(rule["pattern"])
                color = rule["color"]
                compiled_rules.append((pattern, color))
            self.syntax_rules[lang] = compiled_rules
    
    
    # Применение подсветки синтаксиса
    def apply_syntax_highlighting(self, line, lang):
        if not line.strip():  # Если строка пустая или содержит только пробелы
            return [(line, curses.color_pair(0))]
        if lang not in self.syntax_rules:
            return [(line, curses.color_pair(0))]
        
        result = []
        current_pos = 0
        for pattern, color_name in self.syntax_rules[lang]:
            for match in pattern.finditer(line):
                start, end = match.span()
                if start > current_pos:
                    result.append((line[current_pos:start], curses.color_pair(0)))
                color_pair = self.get_color_code(color_name)
                result.append((line[start:end], curses.color_pair(color_pair)))
                current_pos = end
        if current_pos < len(line):
            result.append((line[current_pos:], curses.color_pair(0)))
        return result


    # Преобразование цвета
    def get_color_code(self, color_name):
        color_map = {
            "keyword_color": 1,
            "string_color": 2,
            "comment_color": 3
        }
        return color_map.get(color_name, 0)
            

    #--------------------------------------
    # Обработка ввода
    #--------------------------------------
    def handle_input(self, key):
        if key == self.keybindings.get("quit", 17):  # 17 = Ctrl+Q
            self.exit_editor()
        elif key == self.keybindings.get("save_file", 19):  # 19 = Ctrl+S
            self.save_file()
        elif key == self.keybindings.get("open_file", 6):  # Добавить эту строку (6 = Ctrl+F по вашей конфигурации)
            self.open_file()
        elif key == curses.KEY_UP:
            if self.cursor_y > 0:
                self.cursor_y -= 1
        elif key == curses.KEY_DOWN:
            if self.cursor_y < len(self.text) - 1:
                self.cursor_y += 1
        elif key == curses.KEY_LEFT:
            if self.cursor_x > 0:
                self.cursor_x -= 1
            else:
                # Переход на предыдущую строку
                if self.cursor_y > 0:
                    self.cursor_y -= 1
                    self.cursor_x = len(self.text[self.cursor_y])
        elif key == curses.KEY_RIGHT:
            if self.cursor_x < len(self.text[self.cursor_y]):
                self.cursor_x += 1
            else:
                # Переход на следующую строку
                if self.cursor_y < len(self.text) - 1:
                    self.cursor_y += 1
                    self.cursor_x = 0
        elif key == ord('\n'):
            self.text.insert(self.cursor_y + 1, "")
            self.cursor_y += 1
            self.cursor_x = 0
            self.modified = True
        elif key == curses.KEY_BACKSPACE or key == 127:
            if self.cursor_x > 0:
                self.text[self.cursor_y] = (
                    self.text[self.cursor_y][:self.cursor_x - 1] +
                    self.text[self.cursor_y][self.cursor_x:]
                )
                self.cursor_x -= 1
                self.modified = True
            elif self.cursor_y > 0:
                # Объединение со строкой выше
                prev_line = self.text[self.cursor_y - 1]
                self.text[self.cursor_y - 1] += self.text[self.cursor_y]
                del self.text[self.cursor_y]
                self.cursor_y -= 1
                self.cursor_x = len(prev_line)
                self.modified = True
        else:
            self.text[self.cursor_y] = (
                self.text[self.cursor_y][:self.cursor_x] +
                chr(key) +
                self.text[self.cursor_y][self.cursor_x:]
            )
            self.cursor_x += 1
            self.modified = True



    #--------------------------------------
    # Открытие файла
    #--------------------------------------
    def open_file(self):
        if self.modified:
            choice = self.prompt("Save changes? (y/n): ")
            if choice.lower().startswith("y"):
                self.save_file()

        filename = self.prompt("Open file: ")
        if not filename:
            self.status_message = "Open cancelled"
            return
        
        try:
            with open(filename, "r") as f:
                self.text = f.read().splitlines()
                # Добавляем пустую строку, если список пуст
                if not self.text:
                    self.text = [""]
            self.filename = filename
            self.modified = False
            self.cursor_x = 0
            self.cursor_y = 0
            self.scroll_top = 0
            self.scroll_left = 0
            self.status_message = f"Opened {filename}"
        except Exception as e:
            self.status_message = f"Error opening file: {str(e)}"

    #--------------------------------------
    # Сохранение файла
    #--------------------------------------
    def save_file(self):
        if self.filename == "noname":
            self.filename = self.prompt("Save as: ")
            if not self.filename:
                self.status_message = "Save cancelled"
                return
        try:
            with open(self.filename, "w") as f:
                f.write(os.linesep.join(self.text))
            self.modified = False
            self.status_message = f"Saved to {self.filename}"
        except Exception as e:
            self.status_message = f"Error saving: {str(e)}"

    def exit_editor(self):
        if self.modified:
            choice = self.prompt("Save changes? (y/n): ")
            if choice.lower().startswith("y"):
                self.save_file()
        curses.endwin()
        exit()

    def prompt(self, message):
        self.stdscr.nodelay(False)
        curses.echo()
        self.stdscr.addstr(curses.LINES-1, 0, message)
        self.stdscr.clrtoeol()
        self.stdscr.refresh()
        response = self.stdscr.getstr(curses.LINES-1, len(message), 256).decode('utf-8')
        curses.noecho()
        self.stdscr.nodelay(True)
        return response

    def run(self):
        while True:
            self.draw_screen()
            key = self.stdscr.getch()
            self.handle_input(key)


#--------------------------------------
# Основная функция 
# 
# Эти изменения позволят вам открывать файлы с помощью сочетания клавиш Ctrl+F, 
# а также защитят от потери несохраненных изменений при открытии нового файла. 
# Дополнительное изменение позволит открывать файлы непосредственно из командной 
# строки при запуске редактора.
#  
#--------------------------------------
def main(stdscr):
    import sys
    editor = SwayEditor(stdscr)  # Создаем экземпляр редактора
    
    # Открытие файла, если он указан в аргументах командной строки
    if len(sys.argv) > 1:
        editor.filename = sys.argv[1]
        try:
            with open(editor.filename, "r") as f:
                editor.text = f.read().splitlines()
                if not editor.text:
                    editor.text = [""]
        except Exception as e:
            editor.status_message = f"Error opening {editor.filename}: {str(e)}"
            editor.filename = "noname"
    
    editor.run()  # Запускаем цикл редактора

# Запуск редактора
if __name__ == "__main__":
    curses.wrapper(main)
    