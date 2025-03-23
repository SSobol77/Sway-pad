import curses

def main(stdscr):
    stdscr.keypad(True)
    curses.raw()
    curses.noecho()
    while True:
        key = stdscr.getch()
        stdscr.addstr(0, 0, f"Key: {key}      ")
        stdscr.refresh()

curses.wrapper(main)