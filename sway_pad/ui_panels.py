# ui_panels.py
import curses
import textwrap
import logging

class CursesPanel:
    """
    Базовый класс для отображения всплывающей панели на экране curses.
    Управляет отрисовкой, прокруткой и обработкой ввода.
    """
    def __init__(self, stdscr, title: str, content: str, colors: dict):
        self.stdscr = stdscr
        self.title = title
        self.content_lines = content.split('\n')
        self.colors = colors
        self.scroll_top = 0
        self.is_active = False

    def show(self):
        """Отображает панель и входит в цикл обработки ввода."""
        self.is_active = True
        original_cursor_visibility = curses.curs_set(0) # Скрыть курсор

        try:
            while self.is_active:
                self.draw()
                key = self.stdscr.getch() # Используем stdscr для ввода
                self.handle_input(key)
        finally:
            curses.curs_set(original_cursor_visibility) # Восстановить курсор
            # Не очищаем экран здесь, основной цикл редактора это сделает

    def draw(self):
        """Отрисовывает панель и ее содержимое."""
        term_h, term_w = self.stdscr.getmaxyx()

        panel_h = min(term_h - 4, max(10, len(self.content_lines) + 4))
        panel_w = min(term_w - 4, 80)
        
        y = (term_h - panel_h) // 2
        x = (term_w - panel_w) // 2

        # Создаем окно, если его еще нет, или меняем размер
        # Для простоты, мы будем рисовать прямо на stdscr
        
        # 1. Отрисовка фона/тени (опционально, но красиво)
        bg_attr = self.colors.get("status", curses.A_NORMAL)
        for i in range(panel_h):
            self.stdscr.addstr(y + i, x, ' ' * panel_w, bg_attr)

        # 2. Отрисовка рамки
        border_attr = self.colors.get("keyword", curses.A_BOLD)
        # Просто рисуем псевдо-рамку
        for i in range(x, x + panel_w):
            self.stdscr.addch(y, i, ' ', border_attr)
            self.stdscr.addch(y + panel_h - 1, i, ' ', border_attr)
        # Заголовок
        title_str = f" {self.title} "
        self.stdscr.addstr(y, x + (panel_w - len(title_str)) // 2, title_str, border_attr)


        # 3. Отрисовка содержимого с переносом строк
        content_h = panel_h - 2
        content_w = panel_w - 2
        
        wrapped_lines = []
        for line in self.content_lines:
            wrapped_lines.extend(textwrap.wrap(line, width=content_w, replace_whitespace=False) or [''])
        
        total_lines = len(wrapped_lines)
        max_scroll = max(0, total_lines - content_h)
        self.scroll_top = min(self.scroll_top, max_scroll)

        for i in range(content_h):
            line_idx = self.scroll_top + i
            if line_idx < total_lines:
                self.stdscr.addstr(y + 1 + i, x + 1, wrapped_lines[line_idx].ljust(content_w), bg_attr)
        
        # 4. Индикаторы прокрутки
        if self.scroll_top > 0:
            self.stdscr.addstr(y + 1, x + panel_w - 2, "↑", border_attr)
        if self.scroll_top < max_scroll:
            self.stdscr.addstr(y + panel_h - 2, x + panel_w - 2, "↓", border_attr)

        self.stdscr.refresh()

    def handle_input(self, key):
        """Обрабатывает ввод пользователя для панели."""
        if key in (ord('q'), ord('Q'), 27): # Esc
            self.is_active = False
        elif key in (curses.KEY_UP, ord('k')):
            self.scroll_top = max(0, self.scroll_top - 1)
        elif key in (curses.KEY_DOWN, ord('j')):
            content_h = self.stdscr.getmaxyx()[0] - 4 - 2
            total_lines = len(textwrap.wrap('\n'.join(self.content_lines), width=self.stdscr.getmaxyx()[1] - 4 - 2))
            max_scroll = max(0, total_lines - content_h)
            self.scroll_top = min(max_scroll, self.scroll_top + 1)
        elif key == curses.KEY_RESIZE:
            # Просто выходим из цикла, основной цикл редактора перерисует все
            pass