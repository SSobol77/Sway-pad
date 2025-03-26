# SwayEditor Class Documentation

## Class Overview
The `SwayEditor` class is the core component of the Sway-Pad text editor. It provides a curses-based interface with syntax highlighting, file operations, and advanced editing features. The editor supports multithreaded linting via Pylint and uses Pygments for syntax highlighting.

**Dependencies**:
- `curses` for terminal UI
- `pygments` for syntax highlighting
- `toml` for configuration loading
- `threading` for background tasks

---

## Attributes
### Public Attributes
| Attribute          | Type              | Description |
|--------------------|-------------------|-------------|
| `stdscr`           | `curses.window`   | Main curses screen object |
| `config`           | `dict`            | Configuration settings from `config.toml` |
| `text`             | `list[str]`       | Document lines stored as a list |
| `cursor_x`         | `int`             | Current cursor horizontal position |
| `cursor_y`         | `int`             | Current cursor vertical position |
| `filename`         | `str`             | Current file path (or "new_file.py" if new) |
| `modified`         | `bool`            | Indicates unsaved changes |
| `syntax_highlighting` | `dict` | Syntax rules for supported languages |
| `keybindings`      | `dict`            | Key mappings for editor commands |

### Private Attributes
| Attribute          | Type              | Description |
|--------------------|-------------------|-------------|
| `_scroll_top`       | `int`             | Vertical scroll position |
| `_scroll_left`      | `int`             | Horizontal scroll position |
| `_status_message`   | `str`             | Temporary status message |

---

## Methods

### Initialization
#### `__init__(self, stdscr)`
- **Parameters**: `stdscr` (curses window object)
- **Description**: Initializes the editor with default settings, loads configuration, and sets up curses environment.

---

### Public Methods

#### `run(self)`
- **Description**: Main loop for the editor. Continuously redraws the screen and handles user input.

#### `open_file(self)`
- **Description**: Opens a file with encoding detection. Prompts for save if changes exist.

#### `save_file(self)`
- **Description**: Saves the current file. Prompts for filename if new. Triggers async Pylint analysis.

#### `exit_editor(self)`
- **Description**: Exits the editor after checking for unsaved changes.

#### `detect_language(self)`
- **Description**: Detects programming language based on file extension.
- **Returns**: `str` (e.g., "python", "javascript")

#### `apply_syntax_highlighting(self, line, lang)`
- **Description**: Applies syntax highlighting using Pygments lexer for given language.
- **Parameters**:
  - `line` (`str`): Text line to highlight
  - `lang` (`str`): Programming language identifier
- **Returns**: List of tuples `(text_part, color_code)`

#### `handle_input(self, key)`
- **Description**: Processes keyboard input and executes corresponding actions (navigation, editing, commands).

#### `draw_screen(self)`
- **Description**: Renders the editor interface including text, line numbers, and status bar.

---

### Private/Helper Methods

#### `_init_colors(self)`
- **Description**: Initializes curses color pairs based on configuration.

#### `_parse_key(self, key_str)`
- **Description**: Converts keybinding strings (like "ctrl+s") into curses key codes.

#### `_find_matching_bracket(self, line, col, bracket)`
- **Description**: Finds matching bracket for cursor position.
- **Parameters**:
  - `line` (`str`): Current line text
  - `col` (`int`): Current column position
  - `bracket` (`str`): Current bracket character
- **Returns**: `(row, col)` of matching bracket or `None`

#### `_run_pylint_async(self, code)`
- **Description**: Runs Pylint analysis in background thread.
- **Parameters**: `code` (`str`): Current document content

#### `_highlight_matching_brackets(self)`
- **Description**: Highlights matching brackets when cursor is on a bracket.

#### `_search_text(self, search_term)`
- **Description**: Searches for text occurrences across document lines.
- **Parameters**: `search_term` (`str`): Search pattern
- **Returns**: List of `(line_num, start, end)` matches

#### `_prompt(self, message)`
- **Description**: Displays modal prompt for user input.
- **Parameters**: `message` (`str`): Prompt text
- **Returns**: `str` user input or empty string

---

### File Operations

#### `new_file(self)`
- **Description**: Creates new empty document (prompts to save if needed).

#### `save_file_as(self)`
- **Description**: Saves current document under new filename.

#### `revert_changes(self)`
- **Description**: Reverts to last saved version of the file.

#### `encrypt_file(self)`
- **Description**: Encrypts current file (placeholder - not implemented).

---

### Navigation

#### `goto_line(self)`
- **Description**: Moves cursor to specified line number via prompt.

#### `handle_up(self)`
#### `handle_down(self)`
#### `handle_left(self)`
#### `handle_right(self)`
- **Description**: Cursor movement handlers for arrow keys.

#### `handle_page_up(self)`
#### `handle_page_down(self)`
- **Description**: Scroll page up/down.

---

### Editing Features

#### `handle_enter(self)`
- **Description**: Inserts new line at cursor position.

#### `handle_tab(self)`
- **Description**: Inserts indentation (spaces/tabs per config).

#### `toggle_insert_mode(self)`
- **Description**: Switches between INSERT and REPLACE modes.

#### `find_and_replace(self)`
- **Description**: Searches/replace text with regex support.

#### `undo(self)`
#### `redo(self)`
- **Description**: Undo/redo functionality (TODO: implementation pending).

---

## Example Usage
```python
import curses
from sway_pad.sway import SwayEditor

def main(stdscr):
    editor = SwayEditor(stdscr)
    editor.run()

if __name__ == "__main__":
    curses.wrapper(main)
```

---

## Configuration
The editor's behavior is controlled via `config.toml`, including:
- Keybindings (`keybindings` section)
- Syntax highlighting rules (`syntax_highlighting` section)
- UI colors (`colors` section)
- File type associations (`supported_formats`)

---

## Key Features
| Feature               | Implementation Notes |
|-----------------------|----------------------|
| Syntax Highlighting   | Pygments-based with regex overrides |
| Multithreaded Linting | Pylint runs in background thread |
| File Encoding Support | chardet for automatic encoding detection |
| Unicode Support       | Handles full-width characters |
| Search/Replace        | Regex-enabled search with replace |
| Git Integration       | Basic status/push/pull commands |

---

## Error Handling
- **File I/O**: Graceful handling for permission errors/invalid paths
- **Syntax Errors**: Shows Pylint warnings in status bar
- **UI Errors**: Catches curses exceptions during screen updates

---

## Dependencies
```python
import curses
import pygments
import toml
import threading
import subprocess
```

---

## TODO List
- [ ] Implement selection mode
- [ ] Add undo/redo history
- [ ] Session save/restore
- [ ] Auto-save functionality
- [ ] Full encryption support
- [ ] Improved error reporting
