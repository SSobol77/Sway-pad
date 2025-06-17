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
if sys.platform != "win32":
    import termios
import functools
import time
import pyperclip
import logging
import logging.handlers
import chardet
import unicodedata
import traceback
import subprocess
import tempfile
import threading
import curses.ascii
import signal
import json
import importlib.util
import asyncio
#import uuid 


from pygments.lexers import get_lexer_for_filename, guess_lexer, TextLexer
from pygments import lex
#from pygments.lexers.special import TextLexer
from pygments.token import Token
from wcwidth import wcwidth, wcswidth
from typing import Callable, Tuple, Optional, List, Dict, Any, Union

HAS_DEVOPS_LINTERS = importlib.util.find_spec("lint_devops") is not None

if HAS_DEVOPS_LINTERS:
    import lint_devops


# --- Default Encoding Setup ---
def _set_default_encoding() -> None:
    """
    Sets default I/O encoding environment variables for Python,
    aiming for consistent UTF-8 behavior, especially with subprocesses.
    This is particularly helpful for cross-platform compatibility.
    """
    # PYTHONIOENCODING: Forces Python's stdin, stdout, and stderr to use UTF-8.
    # This helps ensure correct character handling when interacting with subprocesses
    # or when output is redirected, especially in environments where the default
    # locale might not be UTF-8.
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    # PYTHONLEGACYWINDOWSSTDIO: On Python 3.6+, setting this to any non-empty string
    # (or '1' by convention) makes stdin, stdout, stderr use 'utf-8' encoding
    # when they are connected to console I/O (e.g. a cmd.exe window) on Windows,
    # instead of the console's current code page (e.g. cp437, cp1252).
    # This helps with Unicode characters in console output on Windows.
    # For Python < 3.6, PYTHONIOENCODING might be sufficient, but this adds robustness.
    # For modern Windows (Windows 10 with UTF-8 console support enabled), this might
    # be less critical but doesn't harm.
    if os.name == 'nt':  # Apply only on Windows
        os.environ.setdefault("PYTHONLEGACYWINDOWSSTDIO", "1")

    logging.debug(
        f"Default I/O encoding set: PYTHONIOENCODING='{os.environ.get('PYTHONIOENCODING')}', "
        f"PYTHONLEGACYWINDOWSSTDIO='{os.environ.get('PYTHONLEGACYWINDOWSSTDIO', 'Not Set (or Not Windows)')}'"
    )


_set_default_encoding()  # Call once at module load time


# --- Dictionary Deep Merge Utility ---
def deep_merge(base: Dict[Any, Any], override: Dict[Any, Any]) -> Dict[Any, Any]:
    """
    Recursively merges the `override` dictionary into the `base` dictionary.

    If a key exists in both dictionaries and both values are dictionaries,
    the merge is performed recursively. Otherwise, the value from `override`
    replaces the value from `base`. The original `base` dictionary is not modified;
    a new merged dictionary is returned.

    Args:
        base (Dict[Any, Any]): The base dictionary.
        override (Dict[Any, Any]): The dictionary whose values will override those in `base`.

    Returns:
        Dict[Any, Any]: A new dictionary containing the merged result.

    Example:
        >>> base = {'a': 1, 'b': {'x': 10, 'y': 20}}
        >>> override = {'b': {'y': 99, 'z': 100}, 'c': 3}
        >>> deep_merge(base, override)
        {'a': 1, 'b': {'x': 10, 'y': 99, 'z': 100}, 'c': 3}
    """
    result = dict(base)  # Start with a shallow copy of the base
    for key, override_value in override.items():
        base_value = result.get(key)
        if (isinstance(base_value, dict) and
                isinstance(override_value, dict)):
            # If both values are dictionaries, recurse
            result[key] = deep_merge(base_value, override_value)
        else:
            # Otherwise, the override value takes precedence
            result[key] = override_value
    return result


def hex_to_xterm(hex_color: str) -> int:
    """
    Converts a hex color string to the nearest xterm-256 color index.
    """
    hex_color = hex_color.lstrip('#')
    if len(hex_color) != 6:
        return 255  # Default to white on error

    try:
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    except ValueError:
        return 255

    # Simple grayscale check
    if r == g == b:
        if r < 8: 
            return 16
        if r > 248: 
            return 231
        return round(((r - 8) / 247) * 24) + 232

    # Color cube
    color_index = 16
    color_index += 36 * round(r / 255 * 5)
    color_index += 6 * round(g / 255 * 5)
    color_index += round(b / 255 * 5)
    return int(color_index)


# --- Safe Subprocess Execution Utility ---
def safe_run(
        cmd: list[str],
        cwd: Optional[str] = None,
        timeout: Optional[float] = None,
        **kwargs: Any
) -> subprocess.CompletedProcess:
    """
    Safely executes an external command and captures its output.

    This function wraps `subprocess.run()` with safe defaults:
    - Ensures text output with UTF-8 decoding and error replacement.
    - Captures both stdout and stderr.
    - Handles common error scenarios gracefully.
    - Never raises exceptions — instead returns a CompletedProcess with error output and code.

    Args:
        cmd (list[str]): Command to execute, passed as a list of arguments.
        cwd (Optional[str], optional): Working directory for the subprocess. Defaults to None.
        timeout (Optional[float], optional): Timeout in seconds. Defaults to None.
        **kwargs (Any): Additional keyword arguments forwarded to `subprocess.run`.

    Returns:
        subprocess.CompletedProcess: Result of the command execution, with `returncode`, `stdout`, and `stderr`.

    Warnings:
        If the caller provides `check=True` via `kwargs`, it will override the default behavior
        and may cause exceptions to propagate.

    Example:
        >>> result = safe_run(["echo", "hello"])
        >>> print(result.stdout)
        hello
        >>> print(result.returncode)
        0
    """
    if "check" in kwargs:
        logging.warning(
            "safe_run: 'check=True' passed in kwargs — caller is responsible for handling exceptions."
        )

    effective_kwargs = {
        "capture_output": True,
        "text": True,
        "check": False,
        "encoding": "utf-8",
        "errors": "replace",
        **kwargs,
    }

    if cwd is not None:
        effective_kwargs["cwd"] = cwd
    if timeout is not None:
        effective_kwargs["timeout"] = timeout

    try:
        return subprocess.run(cmd, **effective_kwargs)
    except FileNotFoundError as e:
        logging.error(f"safe_run: Command not found: {cmd[0]!r}", exc_info=True)
        return subprocess.CompletedProcess(cmd, returncode=127, stdout="", stderr=str(e))
    except subprocess.TimeoutExpired as e:
        logging.warning(f"safe_run: Command timed out after {timeout}s: {' '.join(cmd)}")
        return subprocess.CompletedProcess(
            cmd,
            returncode=-9,
            stdout=(e.stdout.decode("utf-8", "replace") if e.stdout else ""),
            stderr=(e.stderr.decode("utf-8", "replace") if e.stderr else "Process timed out."),
        )
    except OSError as e:
        logging.error(f"safe_run: OS error while running {cmd}: {e}", exc_info=True)
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr=str(e))
    except Exception as _:
        logging.exception(f"safe_run: Unexpected error while executing: {' '.join(cmd)}")
        return subprocess.CompletedProcess(cmd, returncode=-1, stdout="", stderr="Unexpected error occurred.")


# --- Enhanced Logging Setup Function ---
def setup_logging(config: Optional[Dict[str, Any]] = None) -> None:
    """
    Configures application-wide logging handlers and log levels.

    This function sets up a flexible logging system with support for file logging,
    console logging, error-only file logging, and optional key event tracing.
    It clears existing root logger handlers to avoid duplicate logs and applies
    settings from the provided configuration dictionary.

    The routine sets up a flexible logging stack with up to four
    independent handlers:

    1. **File handler** – rotating *editor.log* capturing everything from
       the configured `file_level` (default **DEBUG**) upward.
    2. **Console handler** – optional `stderr` output whose threshold is
       `console_level` (default **WARNING**).
    3. **Error-file handler** – optional rotating *error.log* that stores
       only **ERROR** and **CRITICAL** events.
    4. **Key-event handler** – optional rotating *keytrace.log* enabled
       when the environment variable ``SWAY2_KEYTRACE`` is set to
       ``1/true/yes``; attached to the ``sway.keyevents`` logger.

    Existing handlers on the *root logger* are cleared to avoid duplicate
    records when the function is invoked multiple times (e.g. in unit
    tests).

    Args:
        config (dict | None): Optional *application* configuration blob.
            Only the ``["logging"]`` sub-section is consulted; recognised
            keys are:

            - ``file_level`` (str): Log-level for *editor.log*
              (DEBUG, INFO, …).  *Default*: ``"DEBUG"``.
            - ``console_level`` (str): Log-level for console output.
              *Default*: ``"WARNING"``.
            - ``log_to_console`` (bool): Disable/enable console handler.
              *Default*: ``True``.
            - ``separate_error_log`` (bool): Whether to create *error.log*.
              *Default*: ``False``.

    Side Effects:
        - Creates directories for log files if they don’t exist; falls
          back to the system temp directory on failure.
        - Replaces all handlers on the *root logger*.
        - Configures the namespace logger ``sway.keyevents`` to **not**
          propagate and attaches/clears its handlers independently.

    Notes:
        The function never raises; all I/O or permission errors are
        reported to *stderr* and the logging subsystem continues with a
        best-effort configuration.

    Example:
        >>> config = {
        ...     "logging": {
        ...         "file_level": "INFO",
        ...         "console_level": "ERROR",
        ...         "log_to_console": True,
        ...         "separate_error_log": True
        ...     }
        ... }
        >>> setup_logging(config)
    """
    if config is None:
        config = {}
    # --- Main Application Logger (e.g., for editor.log) ---
    log_filename = "editor.log"
    # Use .get safely for nested dictionaries
    logging_config = config.get("logging", {})
    log_file_level_str = logging_config.get("file_level", "DEBUG").upper()
    log_file_level = getattr(logging, log_file_level_str, logging.DEBUG)

    log_dir = os.path.dirname(log_filename)
    if log_dir and not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir)
        except OSError as e_mkdir:
            print(f"Error creating log directory '{log_dir}': {e_mkdir}", file=sys.stderr)
            log_filename = os.path.join(tempfile.gettempdir(), "sway_editor.log")
            print(f"Logging to temporary file: '{log_filename}'", file=sys.stderr)

    file_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)-8s - %(name)-15s - %(message)s (%(filename)s:%(lineno)d)"
    )
    file_handler = None
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_filename, maxBytes=2 * 1024 * 1024, backupCount=5, encoding='utf-8'
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(log_file_level)
    except Exception as e_fh:
        print(f"Error setting up file logger for '{log_filename}': {e_fh}. File logging may be impaired.",
              file=sys.stderr)

    # --- Console Handler ---
    log_to_console_enabled = logging_config.get("log_to_console", True)
    console_handler = None
    if log_to_console_enabled:
        console_level_str = logging_config.get("console_level", "WARNING").upper()
        console_log_level = getattr(logging, console_level_str, logging.WARNING)

        console_formatter = logging.Formatter(
            "%(levelname)-8s - %(name)-12s - %(message)s")  # Slightly shorter name field
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(console_log_level)

    # --- Optional Separate Error Log File ---
    separate_error_log_enabled = logging_config.get("separate_error_log", False)
    error_file_handler = None
    if separate_error_log_enabled:
        error_log_filename = "error.log"
        try:
            error_log_dir = os.path.dirname(error_log_filename)
            if error_log_dir and not os.path.exists(error_log_dir):
                os.makedirs(error_log_dir)

            error_file_handler = logging.handlers.RotatingFileHandler(
                error_log_filename, maxBytes=1 * 1024 * 1024, backupCount=3, encoding='utf-8'
            )
            error_file_handler.setFormatter(file_formatter)
            error_file_handler.setLevel(logging.ERROR)
        except Exception as e_efh:
            print(f"Error setting up separate error log '{error_log_filename}': {e_efh}.", file=sys.stderr)

    # Configure the root logger
    root_logger = logging.getLogger()
    root_logger.handlers = []  # Clear existing root handlers to avoid duplicates

    if file_handler:
        root_logger.addHandler(file_handler)
    if console_handler:
        root_logger.addHandler(console_handler)
    if error_file_handler:
        root_logger.addHandler(error_file_handler)

    root_logger.setLevel(log_file_level)  # Set root to the most verbose level needed by file handlers

    # --- Key Event Logger ---
    key_event_logger = logging.getLogger("sway.keyevents")
    key_event_logger.propagate = False
    key_event_logger.setLevel(logging.DEBUG)
    # Clear any handlers that might have been added if setup_logging is called multiple times
    key_event_logger.handlers = []

    if os.environ.get("SWAY2_KEYTRACE", "").lower() in {"1", "true", "yes"}:
        try:
            key_trace_filename = "keytrace.log"
            key_trace_log_dir = os.path.dirname(key_trace_filename)
            if key_trace_log_dir and not os.path.exists(key_trace_log_dir):
                os.makedirs(key_trace_log_dir)

            key_trace_handler = logging.handlers.RotatingFileHandler(
                key_trace_filename, maxBytes=1 * 1024 * 1024, backupCount=3, encoding='utf-8'
            )
            key_trace_formatter = logging.Formatter("%(asctime)s - %(message)s")
            key_trace_handler.setFormatter(key_trace_formatter)
            key_event_logger.addHandler(key_trace_handler)
            # Do not enable propagate for key_event_logger unless you want key traces in the main log too.
            logging.info("Key event tracing enabled, logging to '%s'.",
                         key_trace_filename)  # Use root logger for this info
        except Exception as e_keytrace:
            logging.error(f"Failed to set up key trace logging: {e_keytrace}", exc_info=True)  # Use root logger
            key_event_logger.disabled = True
    else:
        key_event_logger.addHandler(logging.NullHandler())
        key_event_logger.disabled = True
        logging.debug("Key event tracing is disabled.")  # Use root logger

    logging.info("Logging setup complete. Root logger level: %s.", logging.getLevelName(root_logger.level))
    if file_handler:
        logging.info(f"File logging to '{log_filename}' at level: {logging.getLevelName(file_handler.level)}.")
    if console_handler:
        logging.info(f"Console logging to stderr at level: {logging.getLevelName(console_handler.level)}.")
    if error_file_handler:
        logging.info("Error logging to 'error.log' at level: ERROR.")


# ──────────────────────────── Global loggers ────────────────────────────
# These logger objects are **created at import-time** but remain
# *unconfigured* until ``setup_logging()`` attaches appropriate handlers.
logger = logging.getLogger("sway")  # main application logger
KEY_LOGGER = logging.getLogger("sway.keyevents")  # raw key-press trace


# ─────────────────── File Icon Retrieval Function ───────────────────
def get_file_icon(filename: Optional[str], config: Dict[str, Any]) -> str:
    """
    Returns an icon string for a file based on its name and extension, using the provided configuration.

    The function prioritizes exact filename matches (e.g., "Makefile", "Dockerfile") and then checks
    file extensions to determine the appropriate icon. If no match is found, a default or text icon is returned.

    Args:
        filename (Optional[str]): The name or path of the file. Can be None for new or untitled files.
        config (Dict[str, Any]): Configuration dictionary expected to contain "file_icons" and "supported_formats".

    Returns:
        str: The icon string representing the file type.

    Example:
        >>> config = {
        ...     "file_icons": {"default": "❓", "text": "📝", "python": "🐍"},
        ...     "supported_formats": {"python": [".py"], "text": [".txt", ".md", "README"]}
        ... }
        >>> get_file_icon("main.py", config)
        '🐍'
        >>> get_file_icon("README", config)
        '📝'
        >>> get_file_icon("unknown.xyz", config)
        '❓'
        >>> get_file_icon(None, config)
        '❓'
    """
    file_icons = config.get("file_icons", {})
    default_icon = file_icons.get("default", "❓")  # Default if no specific icon is found
    text_icon = file_icons.get("text", "📝 ")  # Specific default for text-like or new files

    if not filename:  # Handles new, unsaved files or None input
        # return text_icon # Use text icon for new/untitled files
        return default_icon

        # Normalize filename for matching (lowercase)
    filename_lower = filename.lower()
    base_name_lower = os.path.basename(filename_lower)  # e.g., "myfile.txt" from "/path/to/myfile.txt"

    supported_formats: Dict[str, List[str]] = config.get("supported_formats", {})

    if not file_icons or not supported_formats:
        logging.warning("get_file_icon: 'file_icons' or 'supported_formats' missing in config. Using default icon.")
        return default_icon

    # 1. Check for direct filename matches (e.g., "Makefile", "Dockerfile", ".gitignore")
    # These often don't have typical extensions but are specific file types.
    # The extensions list for these in `supported_formats` might contain the full name.
    for icon_key, extensions_or_names in supported_formats.items():
        if isinstance(extensions_or_names, list):
            for ext_or_name in extensions_or_names:
                # If the "extension" is actually a full filename (e.g., "makefile", "dockerfile")
                # or a name starting with a dot (e.g., ".gitignore")
                if not ext_or_name.startswith(".") and base_name_lower == ext_or_name.lower():
                    return file_icons.get(icon_key, default_icon)
                elif ext_or_name.startswith(
                        ".") and base_name_lower == ext_or_name.lower():  # Handles .gitignore, .gitattributes
                    return file_icons.get(icon_key, default_icon)

    # 2. Check for extension matches
    # Handle complex extensions like ".tar.gz" by checking parts of the extension.
    # We can get all "extensions" by splitting by dot.
    # Example: "myfile.tar.gz" -> parts ["myfile", "tar", "gz"]
    # We want to check for ".gz", ".tar.gz"
    name_parts = base_name_lower.split('.')
    if len(name_parts) > 1:  # If there is at least one dot
        # Iterate from the longest possible extension to the shortest
        # e.g., for "file.tar.gz", check ".tar.gz", then ".gz"
        for i in range(1, len(name_parts)):
            # Construct extension like ".gz", ".tar.gz"
            current_extension_to_check = "." + ".".join(name_parts[i:])

            for icon_key, defined_extensions in supported_formats.items():
                if isinstance(defined_extensions, list):
                    # Convert defined extensions to lowercase for comparison
                    lower_defined_extensions = [ext.lower() for ext in defined_extensions]
                    ext_to_match = current_extension_to_check[1:]  # Remove leading dot

                    if ext_to_match in lower_defined_extensions:
                        return file_icons.get(icon_key, default_icon)

    # 3. If no specific match by full name or extension, return the generic text icon
    #    or a more generic default if text icon is also not found (though unlikely).
    #    The problem description implied returning text_icon as a final fallback.
    #    Using `default_icon` might be more appropriate if truly nothing matched.
    #    Let's stick to text_icon as the ultimate fallback if other logic fails.
    logging.debug(f"get_file_icon: No specific icon found for '{filename}'. Falling back to text icon.")
    return text_icon

# --- Configuration Loading (if defined in this file) ---
def load_config() -> dict:
    """
    Loads and merges the application configuration from a *config.toml* file, applying safe defaults.

    This function implements a three-tier configuration strategy:
    1. Loads hard-coded minimal defaults to ensure the application can start in any environment.
    2. Optionally loads and merges user settings from *config.toml* in the current working directory, overriding only specified keys.
    3. Performs a post-merge sanity check to ensure all required sections and keys are present, filling in any missing values from the defaults.

    All errors (missing file, TOML syntax errors, I/O issues) are logged and resolved by falling back to defaults, so the function never raises.

    Returns:
        dict: The fully merged and validated configuration dictionary.

    Side Effects:
        - Logs debug, warning, or error messages for any issues encountered.
        - Reads *config.toml* from disk if present.

    Example:
        >>> config = load_config()
        >>> print(config["editor"]["tab_size"])
        4
    """
    # 1. Minimal hard-coded defaults
    minimal_default = {
        "colors": {
            "error": "red",
            "status": "bright_white",  # curses.COLOR_WHITE + curses.A_BOLD
            "green": "green"  # Git-info
        },
        # Font Family and Size in curses meta info for GUI
        "fonts": {"font_family": "monospace", "font_size": 12},
        "keybindings": {
            "delete": "del",
            "paste": "ctrl+v",
            "copy": "ctrl+c",
            "cut": "ctrl+x",
            "undo": "ctrl+z",
            "redo": "ctrl+y",
            "lint": "f4",
            "new_file": "f2",
            "open_file": "ctrl+o",
            "save_file": "ctrl+s",
            "save_as": "f5",
            "select_all": "ctrl+a",
            "quit": "ctrl+q",
            "goto_line": "ctrl+g",
            "git_menu": "f9",
            "cancel_operation": "esc",
            "find": "ctrl+f",
            "find_next": "f3",
            "search_and_replace": "f6",
            "help": "f1",
            "extend_selection_left": ["shift+left", "alt-h"],
            "extend_selection_right": ["shift+right", "alt-l"],
            "extend_selection_up": ["shift+up", "alt-k"],
            "extend_selection_down": ["shift+down", "alt-j"],
        },
        "editor": {
            "use_system_clipboard": True,
            "default_new_filename": "new_file.py",
            "tab_size": 4,
            "use_spaces": True
        },
        "file_icons": {
            "python": "🐍",
            "toml": "❄️",
            "javascript": "📜",
            "typescript": "📑",
            "php": "🐘",
            "ruby": "♦️",
            "css": "🎨",
            "html": "🌐",
            "json": "📊",
            "yaml": "⚙️",
            "xml": "📰",
            "markdown": "📋",
            "text": "📝", 
            "shell": "💫", 
            "dart": "🎯",
            "go": "🐹",
            "c": "🇨",
            "cpp": "🇨➕",
            "java": "☕",
            "julia": "🧮",
            "rust": "🦀",
            "csharp": "♯",
            "scala": "💎",
            "r": "📉",
            "swift": "🐦",
            "dockerfile": "🐳",
            "terraform": "🛠️",
            "jenkins": "🧑‍✈️",
            "puppet": "🎎",
            "saltstack": "🧂",
            "git": "🔖",
            "notebook": "📒",
            "diff": "↔️",
            "makefile": "🛠️",
            "ini": "🔩",
            "csv": "🗂️",
            "sql": "💾",
            "graphql": "📈",
            "kotlin": "📱",
            "lua": "🌙",
            "perl": "🐪",
            "powershell": "💻",
            "nix": "❄️",
            "image": "🖼️",  # jpg, jpeg, png, gif, bmp, svg, webp
            "audio": "🎵",  # mp3, wav, ogg, flac
            "video": "🎞️",  # mp4, mkv, avi, mov, webm
            "archive": "📦",  # zip, tar, gz, rar, 7z
            "font": "🖋️",  # ttf, otf, woff, woff2
            "binary": "⚙️",  # .exe, .dll, .so, .o, .bin, .app
            "document": "📄",  # .doc, .docx, .odt, .pdf, .ppt, .pptx, .odp
            "folder": "📁",  # Icon for directories (not used by get_file_icon, but useful for file managers)
            "folder_open": "📂",  # likewise
            "default": "❓"  # Default icon if nothing fits
        },
        "supported_formats": {
            "python": ["py", "pyw", "pyc", "pyd"],  # pyc/pyd - binary but related to python
            "toml": ["toml"],
            "javascript": ["js", "mjs", "cjs", "jsx"],
            "typescript": ["ts", "tsx", "mts", "cts"],
            "php": ["php", "php3", "php4", "php5", "phtml"],
            "ruby": ["rb", "rbw", "gemspec"],
            "css": ["css"],
            "html": ["html", "htm", "xhtml"],
            "json": ["json", "jsonc", "geojson", "webmanifest"],
            "yaml": ["yaml", "yml"],
            "xml": ["xml", "xsd", "xsl", "xslt", "plist", "rss", "atom", "csproj", "svg"],
            "markdown": ["md", "markdown", "mdown", "mkd"],
            "text": ["txt", "log", "rst", "srt", "sub", "me", "readme"],
            "shell": ["sh", "bash", "zsh", "ksh", "fish", "command", "tool"],
            "dart": ["dart"],
            "go": ["go"],
            "c": ["c", "h"],
            "cpp": ["cpp", "cxx", "cc", "hpp", "hxx", "hh", "inl", "tpp"],
            "java": ["java", "jar", "class"],
            "julia": ["jl"],
            "rust": ["rs", "rlib"],
            "csharp": ["cs"],
            "scala": ["scala", "sc"],
            "r": ["r", "rds", "rda"],
            "swift": ["swift"],
            "dockerfile": ["dockerfile"],
            "terraform": ["tf", "tfvars"],
            "jenkins": ["jenkinsfile", "groovy"],
            "puppet": ["pp"],
            "saltstack": ["sls"],
            "git": ["gitignore", "gitattributes", "gitmodules", "gitkeep"],
            "notebook": ["ipynb"],
            "diff": ["diff", "patch"],
            "makefile": ["makefile", "mk", "mak"],
            "ini": ["ini", "cfg", "conf", "properties", "editorconfig"],
            "csv": ["csv", "tsv"],
            "sql": ["sql"],
            "graphql": ["graphql", "gql"],
            "kotlin": ["kt", "kts"],
            "lua": ["lua"],
            "perl": ["pl", "pm", "t", "pod"],
            "powershell": ["ps1", "psm1", "psd1"],
            "nix": ["nix"],
            "image": ["jpg", "jpeg", "png", "gif", "bmp", "ico", "webp", "tiff", "tif", "heic", "heif"],
            "audio": ["mp3", "wav", "ogg", "flac", "aac", "m4a", "wma"],
            "video": ["mp4", "mkv", "avi", "mov", "webm", "flv", "wmv"],
            "archive": ["zip", "tar", "gz", "tgz", "bz2", "rar", "7z", "xz", "iso", "deb", "rpm", "pkg"],
            "font": ["ttf", "otf", "woff", "woff2", "eot"],
            "binary": ["exe", "dll", "so", "o", "bin", "app", "com", "msi", "dmg"],
            "document": ["doc", "docx", "odt", "rtf", "pdf", "ppt", "pptx", "odp", "xls", "xlsx", "ods", "epub", "mobi"]
            # 'folder' and 'folder_open' are temporarily not used in get_file_icon by extension,
            # they are more for display in the file manager.
        },
         "comments": {
            # Scripting Languages
            "python": {"line_prefix": "# ", "docstring_delim": '"""'},
            "ruby": {"line_prefix": "# ", "block_delims": ["=begin", "=end"]},
            "perl": {"line_prefix": "# ", "block_delims": ["=pod", "=cut"]},
            "lua": {"line_prefix": "-- ", "block_delims": ["--[[", "]]"]},
            
            # Web Development
            "javascript": {"line_prefix": "// ", "block_delims": ["/*", "*/"]},
            "typescript": {"line_prefix": "// ", "block_delims": ["/*", "*/"]},
            "php": {"line_prefix": "// ", "block_delims": ["/*", "*/"]},
            "html": {"block_delims": ["<!--", "-->"]},
            "xml": {"block_delims": ["<!--", "-->"]},
            "css": {"block_delims": ["/*", "*/"]},
            "scss": {"line_prefix": "// ", "block_delims": ["/*", "*/"]},
            "graphql": {"line_prefix": "# "},

            # C-Family and Compiled Languages
            "c": {"line_prefix": "// ", "block_delims": ["/*", "*/"]},
            "cpp": {"line_prefix": "// ", "block_delims": ["/*", "*/"]},
            "csharp": {"line_prefix": "// ", "block_delims": ["/*", "*/"]},
            "java": {"line_prefix": "// ", "block_delims": ["/*", "*/"]},
            "go": {"line_prefix": "// ", "block_delims": ["/*", "*/"]},
            "rust": {"line_prefix": "// ", "block_delims": ["/*", "*/"]},
            "swift": {"line_prefix": "// ", "block_delims": ["/*", "*/"]},
            "kotlin": {"line_prefix": "// ", "block_delims": ["/*", "*/"]},
            "scala": {"line_prefix": "// ", "block_delims": ["/*", "*/"]},
            "dart": {"line_prefix": "// ", "block_delims": ["/*", "*/"]},

            # Functional Languages
            "haskell": {"line_prefix": "-- ", "block_delims": ["{-", "-}"]},
            "elixir": {"line_prefix": "# ", "docstring_delim": '"""'},
            "erlang": {"line_prefix": "% "},
            "clojure": {"line_prefix": ";; "},
            "fsharp": {"line_prefix": "// ", "block_delims": ["(*", "*)"]},
            "ocaml": {"block_delims": ["(*", "*)"]},
            
            # DevOps & System
            "shell": {"line_prefix": "# "}, # Covers sh, bash, zsh
            "powershell": {"line_prefix": "# ", "block_delims": ["<#", "#>"]},
            "dockerfile": {"line_prefix": "# "},
            "makefile": {"line_prefix": "# "},
            "terraform": {"line_prefix": "# ", "block_delims": ["/*", "*/"]},
            "jenkins": {"line_prefix": "// ", "block_delims": ["/*", "*/"]},  # Jenkinsfile is Groovy
            "puppet": {"line_prefix": "# "}, 
            "saltstack": {"line_prefix": "# "}, # Salt (SLS) files are YAML-based
            "nix": {"line_prefix": "# ", "block_delims": ["/*", "*/"]},
            "vim": {'line_prefix': '" '},
            "assembly": {"line_prefix": "; "},

            # Data, Config, and Markup
            "sql": {"line_prefix": "-- ", "block_delims": ["/*", "*/"]},
            "yaml": {"line_prefix": "# "},
            "toml": {"line_prefix": "# "},
            "ini": {"line_prefix": "; "},
            "markdown": {"block_delims": ["<!--", "-->"]}, # Uses HTML comments <-- ДОБАВЛЕНО
            "latex": {"line_prefix": "% "},

            # Scientific and Other
            "r": {"line_prefix": "# "},
            "julia": {"line_prefix": "# ", "block_delims": ["#=", "=#"]}, # <-- ДОБАВЛЕНО
            "matlab": {"line_prefix": "% ", "block_delims": ["%{", "%}"]},
            "nim": {"line_prefix": "# ", "block_delims": ["#[", "]#"]},
            "crystal": {"line_prefix": "# "},
            "zig": {"line_prefix": "// "},
            "bat": {"line_prefix": "REM "},
        },
        "git": {
            "enabled": True
        },
        "settings": {
            "auto_save_interval": 5,
            "show_git_info": True
        }
    }
    config_path = "config.toml"
    user_config: dict = {}

    # 2. Attempt to read user-provided TOML file
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as fh:
                user_config = toml.loads(fh.read())
            logging.debug("Loaded user config from %s", config_path)
        except FileNotFoundError:
            logging.warning("Config file %s vanished – using defaults.", config_path)
        except toml.TomlDecodeError as exc:
            logging.error("TOML parse error in %s: %s – using defaults.", config_path, exc)
        except Exception as exc:
            logging.error("Unexpected error reading %s: %s – using defaults.", config_path, exc)
    else:
        logging.warning("Config file %s not found – using defaults.", config_path)

    # 3. Merge user config onto minimal defaults
    final_config: dict = deep_merge(minimal_default, user_config)
    
    # Ensure every default section/key exists even if deep_merge missed it
    for section, default_val in minimal_default.items():
        if section not in final_config:
            final_config[section] = default_val
            continue
        if isinstance(default_val, dict):
            for sub_key, sub_val in default_val.items():
                final_config[section].setdefault(sub_key, sub_val)
                
    logging.debug("Final configuration loaded successfully.")
    return final_config

## ==================== KeyBinder Class ====================
class KeyBinder:  
    """
    Manages keybindings, input handling, and action mapping for the editor.
    This class is responsible for translating user key presses into editor actions.

    Methods:
    1. handle_input
    2. _load_keybindings
    3. _decode_keystring
    4. get_key_input_tty_safe
    5. _setup_action_map
    6. parse_alt_key(
    7. get_key_input
    
    """
    def __init__(self, editor: "SwayEditor"):
        """
        Initializes the KeyBinder.

        Args:
            editor: A reference to the main SwayEditor instance to access its
                    state (config, stdscr) and action methods.
        """
        self.editor = editor
        self.config = editor.config
        self.stdscr = editor.stdscr
        
        # State that now belongs to KeyBinder
        self.keybindings = self._load_keybindings()
        self.action_map = self._setup_action_map()
     
    # ───────────────────── Handle Input ─────────────────────
    def handle_input(self, key: Union[str, int]) -> bool:
        """
        Processes a single key event from the user and updates the editor state accordingly.

        This method is called from the main event loop whenever a key is pressed. It first checks
        if the key matches any action in the `action_map` (including integer key codes and logical
        string keys like 'alt-h'). If no mapped action is found, it attempts to handle the key as
        a printable character input.

        Args:
            key (Union[str, int]): The key event received. Can be an integer (for special keys or
                control characters) or a string (for most characters and logical key names).

        Returns:
            bool: True if the editor state was changed and a screen redraw is needed, False otherwise.

        Example:
            >>> editor.handle_input(10)  # Enter key
            True
            >>> editor.handle_input('a')  # Printable character
            True
            >>> editor.handle_input('alt-h')  # Logical key name
            True

        Side Effects:
            - May modify the text buffer, cursor position, selection, or status message.
            - May trigger undo/redo, clipboard, or other editor actions.
            - May update the modified flag or require a UI redraw.
        """

        logging.debug("handle_input: Received raw key event → %r (type: %s)", key, type(key).__name__)

        action_caused_visual_change = False
        original_status = self.editor.status_message  # Store to detect if status message changes

        with self.editor._state_lock:  # Ensure thread safety for state modifications
            try:
                # --- Attempt 1: Direct key lookup (handles integers and string keys like 'alt-h') ---
                if key in self.action_map:
                    logging.debug(
                        f"handle_input: Key '{key}' (type: {type(key).__name__}) found directly in action_map. "
                        f"Calling method: {self.action_map[key].__name__}"
                    )
                    # Methods in action_map are expected to return True if they changed state.
                    if self.action_map[key]():
                        action_caused_visual_change = True
                    # Even if the action method returned False, if it changed the status message,
                    # that constitutes a visual change.
                    if self.editor.status_message != original_status:
                        action_caused_visual_change = True
                    return action_caused_visual_change

                # --- Attempt 2: If key is a single char string, try its ordinal value ---
                # This is for control characters (like Ctrl+A -> '\x01') or other single chars
                # that might be mapped via their integer code.
                key_code_for_action_map: Optional[int] = None
                is_potentially_printable_char_string = False  # Will be True if it's a single char str not mapped by ord()

                if isinstance(key, str) and len(key) == 1:
                    key_code_for_action_map = ord(key)
                    if key_code_for_action_map in self.action_map:
                        logging.debug(
                            f"handle_input: Ordinal value {key_code_for_action_map} of string key '{key}' found in action_map. "
                            f"Calling method: {self.action_map[key_code_for_action_map].__name__}"
                        )
                        if self.action_map[key_code_for_action_map]():
                            action_caused_visual_change = True
                        if self.editor.status_message != original_status:
                            action_caused_visual_change = True
                        return action_caused_visual_change
                    else:
                        # It's a single char string, its ord() is not in action_map.
                        # It might be a printable character for text insertion.
                        # Exclude common control ranges that should ideally be in action_map if handled.
                        if not (0 <= key_code_for_action_map <= 31 or key_code_for_action_map == 127):
                            is_potentially_printable_char_string = True
                            logging.debug(
                                f"handle_input: String key '{key}' (ord: {key_code_for_action_map}) not in action_map, marked as potentially printable.")
                        # else: it's a control character string not in action_map, will be unhandled later.

                elif isinstance(key, int):
                    # If 'key' was an int and not found in the first check, it means this specific int code
                    # is not directly mapped. It might be a printable char code if it's in the Unicode range.
                    # The first `if key in self.action_map:` would have caught mapped integer keys like curses.KEY_UP.
                    # So, key_code_for_action_map is simply `key` here for potential printable check.
                    key_code_for_action_map = key  # Retain for logging if it becomes unhandled
                    pass  # Will be checked in "Handle as a printable character" block if it's in range.

                # If we reach here, the key was not found directly, and if it was a single char string,
                # its ordinal was not found.
                logging.debug(
                    f"handle_input: Key {repr(key)} (derived code for map: {key_code_for_action_map}) not handled by action_map yet.")

                # --- Attempt 3: Handle as a printable character if not mapped and plausible ---
                if is_potentially_printable_char_string:  # key was str, len 1, not a control char, ord(key) not in map
                    if wcswidth(key) > 0:  # Check if it's displayable with a positive width
                        logging.debug(
                            f"handle_input: Treating string '{repr(key)}' as printable character for insertion.")
                        if self.editor.insert_text(key):  # insert_text returns True if it modified content
                            action_caused_visual_change = True
                    else:
                        self.editor._set_status_message(f"Ignored unhandled zero-width/non-displayable string: {repr(key)}")

                elif isinstance(key, int) and key not in self.action_map:  # Integer key not in action_map
                    if 32 <= key < 1114112:  # Plausible Unicode codepoint for a printable character
                        try:
                            char_from_code = chr(key)
                            logging.debug(
                                f"handle_input: Integer key {key} (not in action_map) is in printable range. Char: '{repr(char_from_code)}'")
                            if wcswidth(char_from_code) > 0:
                                if self.editor.insert_text(char_from_code):
                                    action_caused_visual_change = True
                            else:
                                self.editor._set_status_message(
                                    f"Ignored non-displayable/zero-width int key: {key} ('{repr(char_from_code)}')")
                        except ValueError:
                            logging.warning(
                                f"handle_input: Invalid ordinal for chr(): {key}. Cannot convert to character.")
                            self.editor._set_status_message(f"Invalid key code: {key}")
                    else:  # Integer key not printable and not in action_map (e.g., unmapped function key code)
                        KEY_LOGGER.debug("Unhandled integer key code (not printable range, not in action_map): %r", key)
                        self.editor._set_status_message(f"Unhandled key code: {key}")

                # --- Attempt 4: Fallback for any other unhandled input ---
                # This is reached if 'key' was not ERR, not handled by action_map (directly or via ord()),
                # and not processed as a printable char.
                # Example: A multi-character string from get_wch() that isn't 'alt-...' or similar.
                elif key != curses.ERR and not is_potentially_printable_char_string and not (
                        isinstance(key, int) and 32 <= key < 1114112):
                    # The conditions mean:
                    # - It's not curses.ERR.
                    # - It wasn't a single char string marked as printable.
                    # - It wasn't an integer in the printable range.
                    # - And it wasn't caught by action_map earlier.
                    # So, it's likely a multi-char string that `parse_alt_key` didn't resolve,
                    # or an unmapped control character string/int.
                    KEY_LOGGER.debug("Completely unhandled input by primary logic: %r (type: %s)", key,
                                     type(key).__name__)
                    self.editor._set_status_message(f"Unhandled input sequence: {repr(key)}")

                # If status message was changed by any of the preceding branches, it implies a redraw is needed.
                if self.editor.status_message != original_status:
                    action_caused_visual_change = True

                return action_caused_visual_change

            except Exception as e_handler:  # Catch-all for unexpected errors within the input handler itself
                logging.exception("Input handler critical error. This should be investigated.")
                self.editor._set_status_message(f"Input handler error (see log): {str(e_handler)[:50]}")
                return True  # Assume redraw is needed to display the error status

    def _load_keybindings(self) -> dict[str, list[Union[int, str]]]:
        """
        Loads and returns the keybindings configuration for the editor.

        This method determines the correct key codes for various actions,
        taking into account terminal-specific differences (such as Backspace, Ctrl+Z, etc.).
        It returns a dictionary mapping action names to lists of key codes or key strings.

        Returns:
            dict[str, list[Union[int, str]]]: A dictionary where each key is an action name
            (np. "delete", "undo"), a wartość to lista kodów klawiszy lub nazw klawiszy
            wywołujących daną akcję.

        Example:
            >>> editor = SwayEditor(stdscr)
            >>> keybindings = editor._load_keybindings()
            >>> print(keybindings["delete"])
            [curses.KEY_DC, 330, 'del']
        """
        # Getting the correct key codes for TTY
        def get_backspace_code():
                """
                Determines the correct key codes for the Backspace key depending on the terminal.

                This helper function returns a list of integer codes that may represent the Backspace key
                in different terminal environments. It is useful for handling Backspace consistently
                across various platforms and terminal emulators.

                Returns:
                    list[int]: A list of possible key codes for Backspace (e.g., [curses.KEY_BACKSPACE, 8, 127]).

                Example:
                    >>> codes = SwayEditor.get_backspace_code()
                    >>> print(8 in codes)
                    True
                """
                return [curses.KEY_BACKSPACE, 8, 127]

        def get_ctrl_z_codes():
            """
            Returns all possible key codes for the Ctrl+Z key combination.

            This helper function provides a list of integer codes that may represent
            the Ctrl+Z (undo) key in different terminal environments. It includes
            the standard ASCII code, any available curses constant, and common
            alternative codes.

            Returns:
                list[int]: A list of possible key codes for Ctrl+Z (e.g., [26, curses.KEY_SUSPEND, 407]).

            Example:
                >>> codes = SwayEditor.get_ctrl_z_codes()
                >>> print(26 in codes)
                True
            """
            codes = [26]  # ASCII SUB (стандартный Ctrl+Z)

            if hasattr(curses, 'KEY_SUSPEND'):
                codes.append(curses.KEY_SUSPEND)

            codes.append(407)  # Alternative code for undo
            return codes

        default_keybindings: dict[str, Union[str, int, list[Union[str, int]]]] = {
            "delete": ["del", curses.KEY_DC],
            "paste": ["ctrl+v", 22],
            "copy": ["ctrl+c", 3],
            "cut": ["ctrl+x", 24],
            "undo": ["ctrl+z"] + get_ctrl_z_codes(),
            "redo": ["ctrl+y", 558, 25],
            "new_file": ["f2", 266],
            "open_file": ["ctrl+o", 15],
            "save_file": ["ctrl+s", 19],
            "save_as": ["f5", 269],
            "select_all": ["ctrl+a", 1],
            "quit": ["ctrl+q", 17],
            "goto_line": ["ctrl+g", 7],
            "request_ai_explanation": ["f7", 271], 
            "git_menu": ["f9", 273],
            "help": ["f1", 265],
            "find": ["ctrl+f", 6],
            "find_next": ["f3", 267],
            "search_and_replace": ["f6", 270],
            "cancel_operation": ["esc", 27],
            "tab": ["tab", 9],
            "shift_tab": ["shift+tab", 353],
            "lint": ["f4", 268],
            "toggle_comment_block": ["ctrl+\\", 28],
            "handle_home": ["home", curses.KEY_HOME, 262],
            "handle_end": ["end", getattr(curses, 'KEY_END', curses.KEY_LL), 360],
            "handle_page_up": ["pageup", curses.KEY_PPAGE, 339],
            "handle_page_down": ["pagedown", curses.KEY_NPAGE, 338],
            "toggle_insert_mode": ["insert", curses.KEY_IC, 331],
            "select_to_home": [curses.KEY_SHOME],
            "select_to_end": [curses.KEY_SEND],
            "extend_selection_up": ["shift+up", getattr(curses, 'KEY_SR', getattr(curses, 'KEY_SPREVIOUS', 337)),
                                    "alt-k"],
            "extend_selection_down": ["shift+down", getattr(curses, 'KEY_SF', getattr(curses, 'KEY_SNEXT', 336)),
                                      "alt-j"],
            "extend_selection_left": ["shift+left", curses.KEY_SLEFT, "alt-h"],
            "extend_selection_right": ["shift+right", curses.KEY_SRIGHT, "alt-l"],
            "handle_backspace": ["backspace"] + get_backspace_code(),
        }

        user_keybindings_config = self.config.get("keybindings", {})
        parsed_keybindings: dict[str, list[Union[int, str]]] = {}

        for action, default_value_spec in default_keybindings.items():
            key_value_spec_from_config = user_keybindings_config.get(action, default_value_spec)

            if not key_value_spec_from_config:
                logging.debug(f"Keybinding for action '{action}' is disabled or empty.")
                continue

            key_codes_for_action: list[Union[int, str]] = []

            specs_to_process: list[Union[str, int]]
            if isinstance(key_value_spec_from_config, list):
                specs_to_process = key_value_spec_from_config
            elif isinstance(key_value_spec_from_config, str) and "|" in key_value_spec_from_config:
                specs_to_process = [s.strip() for s in key_value_spec_from_config.split('|')]
            else:
                specs_to_process = [key_value_spec_from_config]

            for key_spec_item in specs_to_process:
                if not key_spec_item and key_spec_item != 0:
                    continue
                try:
                    key_code = self._decode_keystring(key_spec_item)
                    if key_code not in key_codes_for_action:
                        key_codes_for_action.append(key_code)
                except ValueError as e:
                    logging.error(
                        f"Error parsing keybinding item '{key_spec_item!r}' for action '{action}': {e}. "
                        f"This specific binding for the action will be ignored."
                    )
                except Exception as e_unhandled:
                    logging.error(
                        f"Unexpected error parsing keybinding item '{key_spec_item!r}' for action '{action}': {e_unhandled}",
                        exc_info=True
                    )

            if key_codes_for_action:
                parsed_keybindings[action] = key_codes_for_action
            else:
                logging.warning(f"No valid key codes found for action '{action}' after parsing. It will not be bound.")

        logging.debug(f"Loaded and parsed keybindings (action -> list of key_codes): {parsed_keybindings}")
        return parsed_keybindings

    def _decode_keystring(self, key_input: Union[str, int]) -> Union[int, str]:
        """
        Decodes a key specification string or integer into a key code or logical key identifier.

        This method supports terminal-specific key codes, named keys, and modifier combinations
        (Ctrl, Alt, Shift). It normalizes and parses key strings, returning either an integer
        key code or a logical string for Alt-based bindings.

        Args:
            key_input (Union[str, int]): The key specification as a string (e.g., "ctrl+z", "alt-x")
                or an integer key code.

        Returns:
            Union[int, str]: The resolved key code (int) or logical key identifier (str).

        Raises:
            ValueError: If the key string is invalid or contains unknown modifiers.

        Example:
            >>> self._decode_keystring("ctrl+z")
            26
            >>> self._decode_keystring("alt-x")
            'alt-x'
        """
        if isinstance(key_input, int):
            return key_input

        if not isinstance(key_input, str):
            raise ValueError(f"Invalid key_input type: {type(key_input)}. Expected str or int.")

        original_key_string = key_input
        s = key_input.strip().lower()

        if not s:
            raise ValueError("Key string cannot be empty.")

        logging.debug(f"_decode_keystring: Parsing key_input: {original_key_string!r} (initial s: {s!r})")

        # Normalize alt+key to alt-key
        if "alt" in s.split('+'):
            parts = s.split('+')
            if "alt" in parts:
                base_key_for_alt = parts[-1]
                other_mods = [m for m in parts[:-1] if m != "alt"]
                other_mods.sort()

                normalized_s_parts = ["alt-"]
                if other_mods:
                    normalized_s_parts.append("+".join(other_mods))
                    normalized_s_parts.append("+")
                normalized_s_parts.append(base_key_for_alt)

                s = "".join(normalized_s_parts)
                logging.debug(f"_decode_keystring: Normalized '{original_key_string}' to '{s}' for Alt processing.")

        if s.startswith("alt-"):
            logging.debug(f"_decode_keystring: Interpreted as logical Alt-binding: {s!r}")
            return s

        # Named keys map for terminal environments
        named_keys_map: Dict[str, int] = {
            'f1': curses.KEY_F1, 'f2': curses.KEY_F2, 'f3': curses.KEY_F3, 'f4': curses.KEY_F4,
            'f5': curses.KEY_F5, 'f6': curses.KEY_F6, 'f7': curses.KEY_F7, 'f8': curses.KEY_F8,
            'f9': curses.KEY_F9, 'f10': curses.KEY_F10, 'f11': curses.KEY_F11, 'f12': curses.KEY_F12,
            'left': curses.KEY_LEFT, 'right': curses.KEY_RIGHT,
            'up': curses.KEY_UP, 'down': curses.KEY_DOWN,
            'home': curses.KEY_HOME, 'end': getattr(curses, 'KEY_END', curses.KEY_LL),
            'pageup': curses.KEY_PPAGE, 'pgup': curses.KEY_PPAGE,
            'pagedown': curses.KEY_NPAGE, 'pgdn': curses.KEY_NPAGE,
            'delete': curses.KEY_DC, 'del': curses.KEY_DC,
            'backspace': curses.KEY_BACKSPACE,
            'insert': curses.KEY_IC,
            'tab': 9,
            'enter': curses.KEY_ENTER, 'return': curses.KEY_ENTER,
            'space': ord(' '),
            'esc': 27, 'escape': 27,
            'shift+left': curses.KEY_SLEFT, 'sleft': curses.KEY_SLEFT,
            'shift+right': curses.KEY_SRIGHT, 'sright': curses.KEY_SRIGHT,
            'shift+up': getattr(curses, 'KEY_SR', getattr(curses, 'KEY_SPREVIOUS', 337)),
            'sup': getattr(curses, 'KEY_SR', getattr(curses, 'KEY_SPREVIOUS', 337)),
            'shift+down': getattr(curses, 'KEY_SF', getattr(curses, 'KEY_SNEXT', 336)),
            'sdown': getattr(curses, 'KEY_SF', getattr(curses, 'KEY_SNEXT', 336)),
            'shift+home': curses.KEY_SHOME,
            'shift+end': curses.KEY_SEND,
            'shift+pageup': getattr(curses, 'KEY_SPPAGE', getattr(curses, 'KEY_SPREVIOUS', 337)),
            'shift+pagedown': getattr(curses, 'KEY_SNPAGE', getattr(curses, 'KEY_SNEXT', 336)),
            'shift+tab': getattr(curses, 'KEY_BTAB', 353),
            '/': ord('/'), '?': ord('?'), '\\': ord('\\'),
        }

        # Add function keys F1-F12
        named_keys_map.update({f"f{i}": getattr(curses, f"KEY_F{i}", 256 + i) for i in range(1, 13)})

        if s in named_keys_map:
            code = named_keys_map[s]
            logging.debug(f"_decode_keystring: Named key {s!r} resolved to code {code}")
            return code

        # Parse modifiers
        parts = s.split('+')
        base_key_str = parts[-1].strip()
        modifiers = set(p.strip() for p in parts[:-1])

        if "alt" in modifiers:
            logging.error(f"_decode_keystring: 'alt' unexpectedly found in modifiers for '{s}' at a late stage.")
            modifiers.remove("alt")
            remaining_modifiers_part = ""
            if modifiers:
                sorted_remaining_modifiers = sorted(list(modifiers))
                remaining_modifiers_part = "+".join(sorted_remaining_modifiers) + "+"
            return f"alt-{remaining_modifiers_part}{base_key_str}"

        # Determine base key code
        base_code: int
        if base_key_str in named_keys_map:
            base_code = named_keys_map[base_key_str]
        elif len(base_key_str) == 1:
            base_code = ord(base_key_str)
        else:
            raise ValueError(f"Unknown base key '{base_key_str}' in '{original_key_string}'")

        # Handle Ctrl modifier
        if "ctrl" in modifiers:
            modifiers.remove("ctrl")
            if 'a' <= base_key_str <= 'z' and len(base_key_str) == 1:
                base_code = ord(base_key_str) - ord('a') + 1
            elif base_key_str == '#':
                base_code = 51  # Ctrl+#
                logging.debug("_decode_keystring: Ctrl+# mapped to code 51")
            elif base_key_str == '/':
                base_code = 31  # Ctrl+/ = ASCII 31
                logging.debug("_decode_keystring: Ctrl+/ mapped to code 31")
            elif base_key_str == '\\':
                base_code = 28  # Ctrl+\\ = ASCII 28
            elif base_key_str == '[':
                base_code = 27  # Ctrl+[ = ESC
            elif base_key_str == ']':
                base_code = 29  # Ctrl+]
            elif base_key_str == 'z':
                base_code = 26  # Ctrl+Z

        # Handle Shift modifier
        if "shift" in modifiers:
            modifiers.remove("shift")
            if 'a' <= base_key_str <= 'z' and len(base_key_str) == 1 and base_code == ord(base_key_str):
                base_code = ord(base_key_str.upper())

        if modifiers:
            raise ValueError(f"Unknown or unhandled modifiers {list(modifiers)} in '{original_key_string}'")

        logging.debug(f"_decode_keystring: Final resolved integer key code for '{original_key_string}': {base_code}")
        return base_code


    def _setup_action_map(self) -> Dict[Union[int, str], Callable[..., Any]]:
        """
        Constructs the mapping from key codes (int or str) to editor action methods.

        This method combines built-in key handlers for TTY/curses compatibility and
        user-defined keybindings from the configuration. It ensures that all supported
        key codes are mapped to the correct editor methods, handling possible
        overwrites and providing fallbacks for essential actions.

        Returns:
            A dictionary mapping key codes or logical key names to their corresponding
            callable editor action methods.
        """
        # This dictionary maps action names (from config) to the actual methods
        # on the SwayEditor instance.
        action_to_method_map: Dict[str, Callable] = {
            "open_file": self.editor.open_file,
            "save_file": self.editor.save_file,
            "save_as": self.editor.save_file_as,
            "new_file": self.editor.new_file,
            "git_menu": self.editor.git.integrate_git,
            "copy": self.editor.copy,
            "cut": self.editor.cut,
            "paste": self.editor.paste,
            "undo": self.editor.undo,
            "redo": self.editor.redo,
            "handle_home": self.editor.handle_home,
            "handle_end": self.editor.handle_end,
            "handle_page_up": self.editor.handle_page_up,
            "handle_page_down": self.editor.handle_page_down,
            "extend_selection_up": self.editor.extend_selection_up,
            "extend_selection_down": self.editor.extend_selection_down,
            "extend_selection_left": self.editor.extend_selection_left,
            "extend_selection_right": self.editor.extend_selection_right,
            "select_to_home": self.editor.select_to_home,
            "select_to_end": self.editor.select_to_end,
            "find": self.editor.find_prompt,
            "find_next": self.editor.find_next,
            "search_and_replace": self.editor.search_and_replace,
            "goto_line": self.editor.goto_line,
            "help": self.editor.show_help,
            "cancel_operation": self.editor.handle_escape,
            "select_all": self.editor.select_all,
            "delete": self.editor.handle_delete,
            "quit": self.editor.exit_editor,
            "tab": self.editor.handle_smart_tab,
            "shift_tab": self.editor.handle_smart_unindent,
            "lint": self.editor.run_lint_async,
            "show_lint_panel": self.editor.show_lint_panel,
            "toggle_comment_block": self.editor.toggle_comment_block,
            "toggle_insert_mode": self.editor.toggle_insert_mode,
            "handle_up": self.editor.handle_up,
            "handle_down": self.editor.handle_down,
            "handle_left": self.editor.handle_left,
            "handle_right": self.editor.handle_right,
            "handle_backspace": self.editor.handle_backspace,
            "handle_enter": self.editor.handle_enter,
            "request_ai_explanation": self.editor.request_ai_explanation,
            
            "debug_show_lexer": lambda: self.editor._set_status_message(
                f"Current Lexer: {self.editor._lexer.name if self.editor._lexer else 'None'}"
            )
        }

        final_key_action_map: Dict[Union[int, str], Callable] = {}

        # --- Built-in key handlers for TTY/curses compatibility ---
        # These are hardcoded to ensure basic navigation always works.
        builtin_curses_key_handlers: Dict[int, Callable] = {
            curses.KEY_UP: action_to_method_map["handle_up"],
            curses.KEY_DOWN: action_to_method_map["handle_down"],
            curses.KEY_LEFT: action_to_method_map["handle_left"],
            curses.KEY_RIGHT: action_to_method_map["handle_right"],
            curses.KEY_RESIZE: self.editor.handle_resize,
            curses.KEY_ENTER: action_to_method_map["handle_enter"],
            10: action_to_method_map["handle_enter"],  # LF
            13: action_to_method_map["handle_enter"],  # CR
            # You can add more essential built-ins here if needed.
        }

        for key_code, method_callable in builtin_curses_key_handlers.items():
            final_key_action_map[key_code] = method_callable

        # --- Map keybindings from config and defaults ---
        # This loop processes the keybindings loaded by `_load_keybindings`.
        for action_name, key_code_list in self.keybindings.items():
            method_callable = action_to_method_map.get(action_name)
            if not method_callable:
                logging.warning(
                    f"Action '{action_name}' in keybindings but no corresponding method. Ignored."
                )
                continue

            for key_code in key_code_list:
                if not isinstance(key_code, (int, str)):
                    logging.error(
                        f"Invalid key code '{key_code}' for '{action_name}'. Skipped."
                    )
                    continue

                # Warn if a user binding overwrites an existing mapping with a different method.
                if (
                    key_code in final_key_action_map and
                    final_key_action_map[key_code].__name__ != method_callable.__name__
                ):
                    logging.warning(
                        f"Keybinding for action '{action_name}' (key: {key_code}) is overwriting "
                        f"an existing mapping for method '{final_key_action_map[key_code].__name__}'."
                    )
                
                final_key_action_map[key_code] = method_callable

        # --- Add temporary debug keybinding ---
        # This is a good place to add temporary or hardcoded bindings.
        f12_key_code = getattr(curses, 'KEY_F12', 276) # Use getattr for safety
        debug_method = action_to_method_map.get("debug_show_lexer")
        if debug_method:
            final_key_action_map[f12_key_code] = debug_method
            logging.info(f"Diagnostic key 'F12' (code: {f12_key_code}) bound to 'debug_show_lexer'.")

        # Log the final map for debugging.
        final_map_log_str = {k: v.__name__ for k, v in final_key_action_map.items()}
        logging.debug(
            f"Final constructed action map (Key Code -> Method Name): {final_map_log_str}"
        )

        return final_key_action_map

    def parse_alt_key(self, seq: str) -> str:
        """
        Parses escape sequences for Alt+Arrow keys or Alt+HJKL combinations.

        This function interprets raw terminal escape sequences and maps them to logical
        key names such as 'alt-h', 'alt-j', 'alt-k', 'alt-l', or returns an empty string
        if the sequence is not recognized.

        Args:
            seq (str): Raw key sequence received from the terminal.

        Returns:
            str: Logical key name (e.g., 'alt-k', 'alt-h'), or an empty string if the
                sequence does not correspond to a supported Alt-key combination.

        Example:
            >>> editor = SwayEditor(stdscr)
            >>> editor.parse_alt_key('\x1b[1;3A')
            'alt-k'
            >>> editor.parse_alt_key('\x1bh')
            'alt-h'
            >>> editor.parse_alt_key('random')
            ''
        """
        # For Alt+Arrow keys (e.g., \x1b[1;3A for Alt+Up in some modern emulators)
        # These sequences can vary significantly. This covers one common variant.
        # \x1bOA, \x1bOB, \x1bOC, \x1bOD is another frequent variant (xterm, vt100 application mode)
        if seq.startswith('\x1b[1;3'):  # Typical for some modern emulators
            direction_char = seq[-1]
            # Map arrow characters (A,B,C,D) to HJKL equivalents
            # A (Up) -> k, B (Down) -> j, C (Right) -> l, D (Left) -> h
            # IMPORTANT: using a hyphen for consistency with 'alt-h' format
            mapping = {'A': 'alt-k', 'B': 'alt-j', 'C': 'alt-l', 'D': 'alt-h'}
            return mapping.get(direction_char, '')
        elif seq.startswith('\x1bO'):  # Another common variant for arrow keys
            direction_char = seq[-1]
            mapping = {'A': 'alt-k', 'B': 'alt-j', 'C': 'alt-l', 'D': 'alt-h'}
            return mapping.get(direction_char, '')

        # For Alt+Letter combinations (e.g., \x1bh for Alt+h)
        # This is a standard way many terminals send Alt+<char>
        if seq.startswith('\x1b') and len(seq) == 2:
            letter = seq[1].lower()  # Get the second character after ESC
            # Check if it's one of the expected HJKL letters
            if letter in 'hjkl':
                return f'alt-{letter}'  # Use a hyphen
        return ''


    def get_key_input_tty_safe(self) -> str | int:
        """
        Reads a key input from the terminal with enhanced TTY compatibility.

        This method handles problematic key codes in TTY environments, such as
        different representations of Backspace, Ctrl+Z, and Ctrl+/.
        It also processes escape sequences for Alt-key combinations.

        Args:
            stdscr: The curses window object.

        Returns:
            str | int: The key code or character read, or -1 on error.
        """
        try:
            key = self.stdscr.get_wch()

            # Handle problematic TTY key codes
            if isinstance(key, int):
                logging.debug(f"get_key_input_tty_safe: Received integer key: {key}")

                # Map common TTY codes to logical actions
                if key == 8:  # Ctrl+H (Backspace)
                    logging.debug(
                        "get_key_input_tty_safe: Detected Ctrl+H/Backspace (code 8)"
                    )
                    return curses.KEY_BACKSPACE
                elif key == 127:  # DEL (Backspace)
                    logging.debug(
                        "get_key_input_tty_safe: Detected DEL as Backspace (code 127)"
                    )
                    return curses.KEY_BACKSPACE
                elif key == 26:  # Ctrl+Z
                    logging.debug("get_key_input_tty_safe: Detected Ctrl+Z (code 26)")
                    return 26
                elif key == 31:  # Ctrl+/
                    logging.debug("get_key_input_tty_safe: Detected Ctrl+/ (code 31)")
                    return 31
                elif key == 263:  # Ctrl+/
                    logging.debug("get_key_input_tty_safe: Detected Ctrl+/ (code 263)")
                    return 263

            # Handle Alt-key escape sequences
            if key == "\x1b":  # ESC
                self.stdscr.nodelay(True)
                seq = key
                try:
                    # В этом внутреннем цикле нам нужен таймаут, чтобы не зависнуть
                    # в ожидании продолжения escape-последовательности, которой может не быть.
                    # Устанавливаем очень маленький таймаут.
                    self.stdscr.timeout(50) # 50 миллисекунд
                    for _ in range(6):
                        next_char = self.stdscr.get_wch()
                        if next_char == curses.ERR: # Таймаут истек
                            break
                        if isinstance(next_char, int):
                            # Если пришло число, это не часть посл-ти
                            # нужно "вернуть" его в буфер, чтобы обработать на след. итерации
                            curses.ungetch(next_char)
                            break
                        seq += next_char
                except curses.error:
                    pass # Игнорируем ошибку таймаута здесь
                finally:
                    self.stdscr.nodelay(True) # Возвращаем основной режим
                    self.stdscr.timeout(-1)   # Сбрасываем таймаут
                
                # После попытки собрать последовательность, парсим ее
                parsed = self.parse_alt_key(seq)
                if parsed:
                    logging.debug(f"get_key_input_tty_safe: Parsed alt sequence: {parsed}")
                    return parsed
            
            logging.debug(f"get_key_input_tty_safe: returning: {repr(key)}")
            return key

        except curses.error:
            # Это ОЖИДАЕМОЕ исключение, когда нет ввода. Просто возвращаем curses.ERR
            return curses.ERR
        except Exception as e:
            # Это уже НЕОЖИДАННАЯ ошибка
            logging.error(f"get_key_input_tty_safe: Unexpected error: {e}", exc_info=True)
            return -1 # Возвращаем -1, чтобы показать, что произошла реальная ошибка

    def get_key_input(self) -> str | int:
        """
        Reads a key input from the user, detecting Alt+arrow keys or Alt+h/j/k/l combinations.

        This method reads a key press from the given curses window. If the input corresponds
        to an Alt+arrow key or Alt+h/j/k/l sequence, it returns a logical key name
        (e.g., 'alt-h', 'alt-k'). Otherwise, it returns the original key code or character.

        Args:
            stdscr: The curses window object from which to read input.

        Returns:
            str | int: The logical key name (such as 'alt-h') or the original key code/character.

        Example:
            >>> editor = SwayEditor(stdscr)
            >>> key = editor.get_key_input(stdscr)
            >>> if key == 'alt-h':
            ...     print("Alt+H pressed")
            >>> elif key == curses.KEY_UP:
            ...     print("Up arrow pressed")
        """
        try:
            key = self.stdscr.get_wch()
            
            if key == '\x1b':
                self.stdscr.nodelay(True)
                self.stdscr.timeout(50)
                seq = key
                try:
                    for _ in range(6):
                        next_char = self.stdscr.get_wch()
                        if next_char == curses.ERR:
                            break
                        if isinstance(next_char, int):
                            curses.ungetch(next_char)
                            break
                        seq += next_char
                except curses.error:
                    pass
                finally:
                    self.stdscr.nodelay(True)
                    self.stdscr.timeout(-1)

                parsed = self.parse_alt_key(seq)
                if parsed:
                    return parsed

            return key
        except curses.error:
            return curses.ERR
        except Exception as e:
            logging.error(f"get_key_input: Unexpected error: {e}", exc_info=True)
            return -1


# ==================== GitBridge Class ====================
class GitBridge:
    """
    Manages all Git integration for the editor.

    This class serves as a dedicated component for all interactions with the
    Git version control system. It centralizes the logic for both user-initiated
    commands (e.g., commit, push) and asynchronous background tasks that update
    the repository status in the UI.

    The component's design follows the DRY (Don't Repeat Yourself) principle.
    A single private method, `_get_repo_info_sync`, contains all the core logic
    for fetching repository status. Public methods like `get_info` (synchronous)
    and `_fetch_git_info_async` (asynchronous worker) act as thin wrappers
    around this central method.

    Results and status updates are communicated back to the main `SwayEditor`
    instance through thread-safe queues.

    Attributes:
        editor (SwayEditor): A reference to the main editor instance.
        config (dict): A shortcut to the editor's configuration dictionary.
        info (Tuple[str, str, str]): A tuple storing the current cached Git
            status: (branch_name, user_name, commit_count).
        last_filename_context (Optional[str]): The file path used for the last
            Git context check, to prevent redundant updates.
        info_q (queue.Queue): A queue for passing fetched repository info from
            the background thread to the main thread.
        cmd_q (queue.Queue): A queue for passing status messages from executed
            Git commands to the main thread.

    Key Methods:
        get_info(): Synchronously fetches and returns essential Git information.
            Ideal for immediate needs like initial editor startup.
        update_git_info(): Triggers a non-blocking, asynchronous background
            fetch of the repository status. This is the primary method for
            keeping the UI updated without freezing the editor.
        integrate_git(): Displays an interactive Git command menu to the user
            and dispatches the chosen command for asynchronous execution.
        process_queues(): Drains the Git-related queues to process results
            from background threads and update the UI.
        reset_state(): Clears the cached Git status, typically used when the
            editor context changes to a non-Git environment.
    """
    def __init__(self, editor: "SwayEditor"):
        """Initializes the GitBridge component.

        This constructor links the GitBridge to the main editor instance and
        initializes the state required for Git integration. It sets up shortcuts
        to the editor's configuration and queues, and prepares internal state
        variables for tracking repository information.

        Args:
            editor: A reference to the main `SwayEditor` instance to access its
                state (e.g., config, filename) and shared resources (e.g., queues).
        """
        self.editor = editor
        self.config = editor.config  # Shortcut to the editor's configuration.

        # --- State specific to the GitBridge component ---
        
        # Stores the current Git info tuple: (branch, user, commit_count).
        self.info: Tuple[str, str, str] = ("", "", "0")
        
        # Tracks the last file path used to fetch Git info to prevent redundant updates.
        self.last_filename_context: Optional[str] = None
        
        # --- Shared resources from the main editor ---

        # Queue for receiving fetched repository status from the async worker.
        self.info_q: "queue.Queue[tuple[str,str,str]]" = editor._git_q
        
        # Queue for receiving result messages from executed Git commands.
        self.cmd_q: "queue.Queue[str]" = editor._git_cmd_q


    def _get_repo_info_sync(self, file_path_context: Optional[str]) -> Tuple[str, str, str]:
        """
        Synchronously fetches core Git repository info (branch, user, commits, dirty state).
        This is the single source of truth for Git status retrieval.

        Args:
            file_path_context: The path to a file or directory to establish context.

        Returns:
            A tuple (branch_name, user_name, commit_count).
        """
        # 1. Determine the repository directory.
        repo_dir_candidate: str
        if file_path_context and os.path.isfile(file_path_context):
            repo_dir_candidate = os.path.dirname(os.path.abspath(file_path_context))
        else:
            repo_dir_candidate = os.getcwd()

        # 2. Check for .git directory.
        if not os.path.isdir(os.path.join(repo_dir_candidate, ".git")):
            logging.debug(f"GitBridge._get_repo_info_sync: No .git found in '{repo_dir_candidate}'.")
            return "", "", "0"

        logging.debug(f"GitBridge._get_repo_info_sync: Found .git in '{repo_dir_candidate}'.")
        
        # Create a specialized function using functools.partial for cleaner calls.
        run_git = functools.partial(safe_run, cwd=repo_dir_candidate, timeout=3)

        branch_name, user_name, commit_count = "", "", "0"

        # 3. Get branch name (with fallbacks).
        result = run_git(["git", "branch", "--show-current"])
        if result.returncode == 0 and result.stdout.strip():
            branch_name = result.stdout.strip()
        else:
            result = run_git(["git", "symbolic-ref", "--short", "HEAD"])
            if result.returncode == 0 and result.stdout.strip():
                branch_name = result.stdout.strip()
            else:
                result = run_git(["git", "rev-parse", "--short", "HEAD"])
                # More robust handling of the commit hash
                if result.returncode == 0 and result.stdout.strip():
                    branch_name = result.stdout.strip()[:7]
                else:
                    branch_name = "detached"

        if not branch_name:
            branch_name = "unborn"

        # 4. Check for dirty status.
        result_dirty = run_git(["git", "status", "--porcelain", "--ignore-submodules"])
        if result_dirty.returncode == 0 and result_dirty.stdout.strip():
            if '*' not in branch_name:
                branch_name += "*"

        # 5. Get user name.
        result_user = run_git(["git", "config", "user.name"])
        if result_user.returncode == 0:
            user_name = result_user.stdout.strip()

        # 6. Get commit count.
        result_commits = run_git(["git", "rev-list", "--count", "HEAD"])
        if result_commits.returncode == 0 and result_commits.stdout.strip().isdigit():
            commit_count = result_commits.stdout.strip()

        logging.debug(f"Git info fetched: Branch='{branch_name}', User='{user_name}', Commits='{commit_count}'")
        return branch_name, user_name, commit_count
    

    # Methods that are primarily UI or async triggers - they set status, so return True
    def integrate_git(self) -> bool:
        """
        Shows the Git menu, prompts for a Git command, and executes it asynchronously.

        This method is the main user-facing entry point for Git functionality. It first
        checks if Git is enabled and if the current context is a valid repository.
        Then, it presents a menu of commands and dispatches the user's choice to an
        asynchronous worker thread.

        Returns:
            bool: True if a status message was set or an interaction occurred that
                requires a screen redraw. Returns False if the operation was
                cancelled without any state change.
        """
        logging.debug("GitBridge.integrate_git called")
        original_status = self.editor.status_message
        redraw_needed = False

        try:
            # Check if Git integration is enabled in the config
            if not self.config.get("git", {}).get("enabled", True):
                self.editor._set_status_message("Git integration is disabled in config.")
                logging.debug("Git menu called but integration is disabled.")
                return self.editor.status_message != original_status

            # Determine the repository directory using the editor's current file context
            repo_dir: str
            if self.editor.filename and os.path.isfile(self.editor.filename):
                repo_dir = os.path.dirname(os.path.abspath(self.editor.filename))
            else:
                repo_dir = os.getcwd()
            # Check if the determined path is actually a Git repository
            if not os.path.isdir(os.path.join(repo_dir, ".git")):
                self.editor._set_status_message("Not a Git repository.")
                logging.debug(f"Git menu called but '{repo_dir}' is not a git repository.")
                return self.editor.status_message != original_status

            # Define the available Git commands for the menu
            commands = {
                "1": ("status", ["git", "status", "--short"]),
                "2": ("commit", None),  # Commit message is prompted for separately
                "3": ("push", ["git", "push"]),
                "4": ("pull", ["git", "pull"]),
                "5": ("diff", ["git", "diff", "--no-color", "--unified=0"]),
            }

            # Construct the options string for the prompt
            opts_str_parts = [f"{k}:{v[0]}" for k, v in commands.items()]
            opts_str = " ".join(opts_str_parts)
            prompt_message = f"Git menu [{opts_str}] → "

            # Prompt the user for a command choice
            status_before_prompt = self.editor.status_message
            choice = self.editor.prompt(prompt_message)
            if self.editor.status_message != status_before_prompt:
                redraw_needed = True

            if not choice or choice not in commands:
                if not redraw_needed and self.editor.status_message == original_status:
                    self.editor._set_status_message("Git menu cancelled.")
                logging.debug(f"Git menu cancelled or invalid choice: '{choice}'")
                return True # A status change or prompt interaction always warrants a redraw

            command_name, cmd_args_template = commands[choice]
            cmd_list_to_run = []

            # Special handling for 'commit' which requires a message
            if command_name == "commit":
                commit_msg = self.editor.prompt("Commit message: ")
                if not commit_msg:
                    self.editor._set_status_message("Commit cancelled (no message).")
                    logging.debug("Git commit cancelled: no message provided.")
                    return True
                # Use -a to stage all modified/deleted files, and -m for the message
                cmd_list_to_run = ["git", "commit", "-a", "-m", commit_msg]
            elif cmd_args_template is not None:
                cmd_list_to_run = list(cmd_args_template)

            # Execute the prepared command asynchronously
            if cmd_list_to_run:
                self.editor._set_status_message(f"Running git {command_name}...")
                
                thread_name = f"GitExecThread-{command_name}-{int(time.time())}"
                threading.Thread(
                    target=self._run_git_command_async,
                    args=(cmd_list_to_run, command_name),
                    daemon=True,
                    name=thread_name
                ).start()
                
                logging.debug(f"Started Git command thread: {thread_name} for {cmd_list_to_run}")
                return True  # Status message changed, redraw needed

        except Exception as e:
            logging.error(f"Error in GitBridge.integrate_git: {e}", exc_info=True)
            self.editor._set_status_message("Git menu error (see log)")
            return True

        return redraw_needed

    def get_info(self, file_path_context: Optional[str]) -> Tuple[str, str, str]:
        """
        Public synchronous method to retrieve Git information.
        This is a thin wrapper around the core `_get_repo_info_sync` method.
        """
        return self._get_repo_info_sync(file_path_context)
    

    def update_git_info(self) -> None:
        """
        Initiates an asynchronous update of Git information if enabled and context changed.
        """
        git_enabled = self.config.get("git", {}).get("enabled", True)
        show_info = self.config.get("settings", {}).get("show_git_info", True)

        if not git_enabled or not show_info:
            if self.info != ("", "", "0"):
                self.reset_state()
            return

        current_file_context = self.editor.filename
        with self.editor._state_lock:
            if current_file_context == self.last_filename_context:
                return  # No change in context, no need to re-fetch.
            self.last_filename_context = current_file_context

        logging.info(f"Starting async Git info fetch for context: '{current_file_context or 'None'}'.")
        
        # Create a unique and descriptive name for the stream, useful for debugging.
        # os.path.basename extracts the file name from the full path.
        # `or 'NoFile'` handles the case where current_file_context is None.
        thread_name = f"GitInfoFetchThread-{os.path.basename(current_file_context or 'NoFile')}"
        
        thread = threading.Thread(
            target=self._fetch_git_info_async,
            args=(current_file_context,),
            daemon=True,
            name=thread_name  # Use the created name
        )
        thread.start()
        logging.debug(f"Git info fetch thread '{thread_name}' started.")


    def _run_git_command_async(self, cmd_list: List[str], command_name: str) -> None:
        """
        Executes a Git command in a separate thread and sends the result message
        to the command queue.

        This worker method uses the global `safe_run` utility for robust subprocess
        execution. It determines the correct repository path from the editor's
        current file context. The final result, whether success or failure,
        is a single string message that is put into `self.cmd_q` for the main
        thread to process and display.

        Args:
            cmd_list: The Git command and its arguments as a list.
            command_name: A display name for the command (e.g., "status", "commit")
                        used in logging and status messages.
        """
        result_message: str = ""

        try:
            # Determine the working directory for the Git command from the editor's state.
            repo_dir_path: str
            if self.editor.filename and os.path.isfile(self.editor.filename):
                repo_dir_path = os.path.dirname(os.path.abspath(self.editor.filename))
            else:
                repo_dir_path = os.getcwd()

            logging.debug(f"Async Git command: Running 'git {command_name}' in directory: '{repo_dir_path}'")
            
            # Use the global safe_run utility function to execute the command.
            git_process_result = safe_run(cmd_list, cwd=repo_dir_path)

            if git_process_result.returncode == 0:
                # Command executed successfully
                result_message = f"Git {command_name}: Successful."
                
                # For state-changing commands, queue a request to update the displayed Git info.
                if command_name.lower() in ["pull", "commit", "push", "fetch", "merge", "rebase", "checkout", "reset"]:
                    try:
                        self.cmd_q.put("request_git_info_update")
                        logging.debug(f"Async Git command: Queued 'request_git_info_update' after 'git {command_name}'.")
                    except queue.Full:
                        logging.error("Git command queue full, could not queue git info update request.")

                # Append a summary of stdout if it's not empty.
                stdout_content = git_process_result.stdout.strip()
                if stdout_content:
                    first_line_of_stdout = stdout_content.splitlines()[0]
                    summary_preview = (first_line_of_stdout[:90] + "..." if len(first_line_of_stdout) > 90 or '\n' in stdout_content else first_line_of_stdout[:90])
                    result_message += f" Output: {summary_preview}"
                    logging.debug(f"Async Git command 'git {command_name}' stdout:\n{stdout_content}")
            else:
                # Command failed
                stderr_content = git_process_result.stderr.strip()
                error_summary = stderr_content.splitlines()[0] if stderr_content else "(no stderr output)"
                result_message = (
                    f"Git {command_name} error (code {git_process_result.returncode}): "
                    f"{error_summary[:100]}"
                )
                if len(error_summary) > 100 or '\n' in stderr_content:
                    result_message += "..."
                logging.error(f"Async Git command 'git {command_name}' failed. Stderr:\n{stderr_content}")

        except Exception as e_git_exec:
            command_str_for_log = ' '.join(shlex.quote(c) for c in cmd_list)
            logging.exception(f"Unexpected error during async Git command '{command_name}' ({command_str_for_log})")
            result_message = f"Git {command_name} error: {str(e_git_exec)[:80]}..."

        # Send the final result message to the Git command queue.
        try:
            self.cmd_q.put(result_message)
            logging.debug(f"Async Git command: Result message queued: '{result_message}'")
        except queue.Full:
            logging.error(f"Git command queue is full. Dropping result: '{result_message}'")


    def _fetch_git_info_async(self, file_path_context: Optional[str]) -> None:
        """
        Asynchronous worker that calls the synchronous fetch method and puts the result in a queue.
        """
        try:
            # The core logic is now just one call.
            git_data = self._get_repo_info_sync(file_path_context)
            self.info_q.put(git_data)
            logging.debug(f"Async Git info fetched and queued: {git_data}")
        except Exception as e:
            logging.error(f"Error in async git info fetch thread: {e}", exc_info=True)
            # Put an error state into the queue so the UI can be updated.
            self.info_q.put(("fetch_error", "", "0"))


    def _handle_git_info(self, git_data: tuple[str, str, str]) -> None:
        """
        Processes fetched Git data, updates the internal state, and sets a status message.

        This method is called by the queue processor when new Git information arrives
        from the asynchronous fetch worker. It caches the new data in `self.info` and,
        if the data has changed, composes and dispatches a user-facing status message
        to the main editor.

        Args:
            git_data: A tuple of (branch, user_name, commit_count) as strings.
                    An empty branch name signals that no Git repository was found.
        """
        # Use the editor's main lock to ensure thread-safe state modification.
        with self.editor._state_lock:
            old_info = self.info
            self.info = git_data  # Update the component's own state

        # If the information hasn't changed, there's nothing more to do.
        if git_data == old_info:
            return

        branch, user, commits = git_data
        git_is_enabled = self.config.get("git", {}).get("enabled", True)

        # Handle cases where no repository is found or the feature is disabled.
        if not branch:
            self.editor._set_status_message("Not a Git repository.")
            logging.info("Git info updated: Repository not detected.")
            return

        if not git_is_enabled:
            self.editor._set_status_message("Git integration is disabled.")
            logging.info("Git info updated: Integration is disabled in config.")
            return

        # Compose a user-friendly status message.
        dirty_mark = " *" if branch.endswith("*") else ""
        # Remove the dirty marker from the branch name for display.
        clean_branch_name = branch.rstrip('*')
        commits_part = f" ({commits} commits)" if commits != "0" else ""
        user_part = f" by {user}" if user else ""

        pretty_status_message = f"Git: On branch '{clean_branch_name}'{dirty_mark}{commits_part}{user_part}"
        
        # Send the composed message to the editor's status bar.
        self.editor._set_status_message(pretty_status_message)
        logging.info("Git info changed -> %s", pretty_status_message)

    def reset_state(self):
        """Resets the Git state for a new file or when Git is disabled."""
        self.info = ("", "", "0")
        self.last_filename_context = None
        logging.debug("GitBridge state has been reset.")

    def process_queues(self) -> bool:
        """Processes messages from Git-related queues."""
        changed = False
        # Обработка очереди с информацией о репозитории
        try:
            while True:
                git_info_data = self.info_q.get_nowait()
                self._handle_git_info(git_info_data) # Вызов локального _handle_git_info
                changed = True
        except queue.Empty:
            pass

        # Обработка очереди с результатами команд
        try:
            while True:
                result_msg = self.cmd_q.get_nowait()
                if result_msg == "request_git_info_update":
                    self.update_git_info()
                else:
                    self.editor._set_status_message(result_msg)
                changed = True
        except queue.Empty:
            pass
            
        return changed


## ================= CodeCommenter Class ====================
class CodeCommenter:
    """Manages all logic for toggling comments in various programming languages.

    This class encapsulates the functionality for adding and removing
    comments from the text buffer. It is designed as a helper component for the
    main `SwayEditor` class, which delegates all comment-related actions to an
    instance of this class.

    It intelligently handles single-line comments, block comments, and
    language-specific docstrings by analyzing the code's context and the
    active language's syntax.

    Attributes:
        editor: A reference to the main `SwayEditor` instance, providing
            access to the text buffer, cursor state, and configuration.

    Methods:
        perform_toggle: The main public entry point. It analyzes the context
            and dispatches to the appropriate toggling helper method.
        _get_language_comment_info: Retrieves comment syntax (line, block,
            docstring) for the current language from an internal map.
        _analyze_comment_context: Determines if a given location is suitable
            for a docstring (e.g., after a function definition).
        _find_preceding_definition: A helper that searches upwards for the
            nearest `def` or `class` statement to establish context.
        _is_valid_docstring_position: A helper that validates if the space
            between a definition and a comment contains no executable code.
        _toggle_docstring_pep8: The dispatcher for adding or removing
            PEP 8-compliant docstrings.
        _check_existing_docstring_pep8: Checks if a block of text is
            already a well-formed single-line or multi-line docstring.
        _add_docstring_pep8: Wraps a block of text with docstring delimiters.
        _remove_docstring_pep8: Unwraps a block of text by removing its
            docstring delimiters.
        _toggle_line_comments: The dispatcher for adding or removing
            line-by-line comments.
        _add_line_comments: Prepends a comment prefix to each line in a range.
        _remove_line_comments: Strips a comment prefix from each line in a range.
        _toggle_block_comment: Wraps or unwraps a range of lines with
            block comment delimiters (e.g., `/* ... */`).
    """ 
    def __init__(self, editor: 'SwayEditor'):
        """Initializes the CodeCommenter.

        This constructor stores a reference to the main editor instance, allowing
        this component to access and modify the editor's state, such as the
        text buffer, cursor position, and configuration settings.

        Args:
            editor: An instance of the main `SwayEditor` class.
        """
        self.editor = editor
        
    def perform_toggle(self, start_y: int, end_y: int) -> None:
        """Dispatches the appropriate comment-toggling action for a line range.

        This method acts as the main entry point for all comment operations. It
        analyzes the language and context of the specified line range and then
        delegates the task to the most suitable helper method. The order of
        priority is:
        1. Docstrings (if the context is appropriate for one).
        2. Line-by-line comments.
        3. Block comments.

        If the language is not supported or no suitable commenting method is
        found, it updates the editor's status bar with an appropriate message.

        Args:
            start_y: The 0-based starting line index of the range to toggle.
            end_y: The 0-based ending line index (inclusive) of the range.

        Example:
            If a user toggles comments on a line following a Python function
            definition, this method will detect the docstring context and call
            `_toggle_docstring_pep8`.

            If the user toggles comments on a standard block of C code, this
            method will likely call `_toggle_line_comments`.
        """
        # First, retrieve the comment syntax for the current language.
        language_info = self._get_language_comment_info()
        if not language_info:
            self.editor._set_status_message("Comments not supported for this language.")
            return

        # Analyze the code context to check if this is a docstring location.
        comment_context = self._analyze_comment_context(start_y, end_y)

        # --- Dispatch to the appropriate handler based on priority ---

        # 1. Prioritize docstrings if the context is valid and the language supports it.
        if comment_context['is_docstring_context'] and language_info.get('docstring_delim'):
            self._toggle_docstring_pep8(start_y, end_y, language_info['docstring_delim'], comment_context)
        
        # 2. Fall back to line-by-line comments if available.
        elif language_info.get('line_prefix'):
            self._toggle_line_comments(start_y, end_y, language_info['line_prefix'])
        
        # 3. As a last resort, use block comments if the language supports them.
        elif language_info.get('block_delims'):
            self._toggle_block_comment(start_y, end_y, language_info['block_delims'])
        
        # If no commenting method is available for the language.
        else:
            self.editor._set_status_message("No suitable comment method available.")


    def _get_language_comment_info(self) -> Optional[dict]:
        """
        Retrieves comment syntax metadata for the editor's current language from the configuration.

        This method consults the [comments] section of the application's configuration
        to find comment syntax for the language currently active in the editor.
        It uses the primary name and aliases of the active Pygments lexer for the lookup.

        If the language is not yet detected, it will first trigger detection.

        Returns:
            A dictionary containing comment syntax information, or None if the
            language is not supported or defined in the configuration. The dictionary
            may contain the following keys:
            - 'line_prefix' (Optional[str]): The prefix for single-line comments.
            - 'block_delims' (Optional[List[str]]): Delimiters for block comments.
            - 'docstring_delim' (Optional[str]): The delimiter for docstrings.
        """
        # 1. Ensure the language is detected.
        if not self.editor.current_language:
            self.editor.detect_language()
        if not self.editor.current_language or not self.editor._lexer:
            logging.warning("_get_language_comment_info: Language not detected.")
            return None

        # 2. Get the comment configuration section.
        comments_config = self.editor.config.get("comments", {})
        if not comments_config:
            logging.warning("No [comments] section found in the configuration.")
            return None

        # 3. Create a list of names to check, starting with the primary name.
        # Pygments lexer names are typically lowercase.
        lang_keys_to_check = [self.editor.current_language.lower()]
        lang_keys_to_check.extend([alias.lower() for alias in self.editor._lexer.aliases])

        # 4. Search for a matching configuration using the language name and its aliases.
        for lang_key in lang_keys_to_check:
            if lang_key in comments_config:
                logging.debug(f"Found comment syntax for language '{lang_key}'.")
                # Return the configuration for the found language.
                # The unpacking in _toggle_block_comment handles a list fine.
                return comments_config[lang_key]

        # 5. If no match was found after checking all aliases.
        logging.warning(
            f"No comment syntax configuration found for language '{self.editor.current_language}' or its aliases."
        )
        return None

    def _analyze_comment_context(self, start_y: int, end_y: int) -> dict:
        """Analyzes the context around a line range to see if it's a docstring location.

        This method inspects the source code to determine if the given line range
        (`start_y` to `end_y`) qualifies as a docstring for a module, class, or
        function. It applies two main heuristics in order:
        1.  Checks if the location is at the top of the file, suitable for a
            module docstring.
        2.  Checks if the location immediately follows a `def` or `class`
            statement, making it a function or class docstring.

        Args:
            start_y: The 0-based starting line index of the block to analyze.
            end_y: The 0-based ending line index of the block. Currently unused
                but reserved for future, more complex context checks.

        Returns:
            A dictionary summarizing the context analysis. It contains:
            - 'is_docstring_context' (bool): True if the location is suitable.
            - 'definition_line' (Optional[int]): The line of the parent
            definition, or None for module docstrings.
            - 'definition_type' (Optional[str]): The type of context, e.g.,
            'module', 'function', 'class'.
            - 'indentation' (int): The required indentation in spaces for the
            docstring to conform to PEP 257.

        Example:
            If `start_y` is 0 in a new file, the method returns:
            `{'is_docstring_context': True, 'definition_type': 'module', ...}`

            If `start_y` points to a line immediately after `def my_func():`,
            the method returns:
            `{'is_docstring_context': True, 'definition_type': 'function', ...}`
        """
        # Initialize the default context, assuming it's not a docstring location.
        context = {
            "is_docstring_context": False,
            "definition_line": None,
            "definition_type": None,
            "indentation": 0,
        }

        # --- Heuristic 1: Check for a module-level docstring at the top of the file ---
        # A module docstring must be the first statement in the file.
        if start_y <= 1:  # Check the first or second line (0-indexed).
            significant_code_before = False
            # Scan all lines *before* the potential docstring start.
            for y in range(start_y):
                if y >= len(self.editor.text):
                    continue
                line = self.editor.text[y].strip()

                # Ignore shebangs, encoding declarations, modelines, and comments.
                # Any other non-blank line is considered significant code.
                if (
                    line
                    and not line.startswith("#")
                    and not line.startswith("#!/")
                    and "coding:" not in line
                    and "encoding:" not in line
                    and "vim:" not in line
                    and "emacs:" not in line
                ):
                    significant_code_before = True
                    break

            if not significant_code_before:
                context.update(
                    {
                        "is_docstring_context": True,
                        "definition_type": "module",
                        "indentation": 0,  # Module docstrings are not indented.
                    }
                )
                return context

        # --- Heuristic 2: Check for a docstring immediately following a definition ---
        definition_info = self._find_preceding_definition(start_y)
        if definition_info:
            context.update(
                {
                    "is_docstring_context": True,
                    "definition_line": definition_info["line"],
                    "definition_type": definition_info["type"],
                    # Per PEP 257, the docstring should be indented one level
                    # deeper than the definition line.
                    "indentation": definition_info["indentation"] + 4,
                }
            )
            return context

        # If neither heuristic matched, return the default context.
        return context

    # Class attribute for storing definition patterns.
    # This makes them reusable and keeps the method logic cleaner.
    DEFINITION_PATTERNS = [
        (r"def\s+\w+.*?:\s*$", "function"),
        (r"class\s+\w+.*?:\s*$", "class"),
        (r"async\s+def\s+\w+.*?:\s*$", "async_function"),
    ]
    # A list of tuples containing regular expression patterns and their corresponding
    # definition types. These are used to identify function, class, and async
    # function definitions in the text buffer. Note that the patterns do not
    # include `^` or `\s*` at the beginning, as they are matched against
    # stripped lines.
    

    def _find_preceding_definition(self, start_y: int) -> Optional[dict]:
        """Finds the nearest preceding `def` or `class` statement.

        This method searches upwards from the line above `start_y` to find the
        closest function, class, or async function definition. The search is
        limited to a 20-line window for performance. For a definition to be
        considered a valid context for a docstring, the space between it and
        `start_y` must not contain any executable code, as verified by
        `_is_valid_docstring_position`.

        Args:
            start_y: The 0-based line index from which to start searching upwards.

        Returns:
            A dictionary containing information about the found definition, or
            None if no suitable definition is found within the search window.
            The dictionary has the following keys:
                'line' (int): The line number of the definition.
                'type' (str): The type of definition ('function', 'class', etc.).
                'indentation' (int): The indentation level of the definition line.

        Example:
            If the text buffer contains:
            '''
            class MyClass:
                
                def my_method(self):
                    # This is the line where start_y is (e.g., index 3)
            '''
            Calling `_find_preceding_definition(3)` would return:
            `{'line': 2, 'type': 'function', 'indentation': 4}`
        """
        # Search upward from the line just before start_y, for a max of 20 lines.
        for y in range(start_y - 1, max(-1, start_y - 20), -1):
            if y >= len(self.editor.text):
                continue

            full_line_text = self.editor.text[y]
            stripped_line = full_line_text.strip()

            # Skip blank lines and standard comments immediately.
            if not stripped_line or stripped_line.startswith("#"):
                continue

            # Check if the stripped line matches any definition pattern.
            for pattern, def_type in self.DEFINITION_PATTERNS:
                # We match against the stripped line, so patterns don't need `^\s*`.
                if re.match(pattern, stripped_line):
                    # Found a definition. Now, verify if it's a valid context
                    # for a docstring at the `start_y` position.
                    if self._is_valid_docstring_position(y, start_y):
                        return {
                            "line": y,
                            "type": def_type,
                            "indentation": len(full_line_text) - len(stripped_line),
                        }
                    # If it's not a valid position, it might be a definition, but
                    # we can't use it for this docstring. Continue searching upwards.
                    break  # Move to the next line up

            # If we encounter any other significant code, stop the search,
            # as it breaks the immediate link to a preceding definition.
            # This `if` condition is now implicitly handled by the loop structure,
            # but adding it explicitly makes the logic clearer.
            if stripped_line:
                # We found a line with code that wasn't a definition or comment.
                # This means any definition above it is not "immediately" preceding.
                break

        return None

    def _is_valid_docstring_position(self, def_line: int, comment_start: int) -> bool:
        """Validates if a docstring can be legally placed at a given line.

        In Python, a docstring must be the first statement following a `def`
        or `class` line. This function checks the lines between the definition
        and the potential docstring to ensure no other code exists, which would
        violate PEP 257. Blank lines and standard '#' comments are permitted.

        Args:
            def_line: The 0-based index of the line containing the definition.
            comment_start: The 0-based line index where the docstring would start.

        Returns:
            True if the position is a valid location for a docstring, False otherwise.
        """
        # Iterate through the lines between the definition and the comment's start.
        for y in range(def_line + 1, comment_start):
            # Defensively guard against out-of-range indices.
            if y >= len(self.editor.text):
                continue
            
            # Get the line and strip leading/trailing whitespace.
            line = self.editor.text[y].strip()

            # If a line is not empty and is not a standard comment, it's executable
            # code, which invalidates the docstring position. We make an exception
            # for lines that are themselves existing docstring delimiters,
            # as they do not count as executable code in this context.
            if line and not line.startswith("#"):
                if not (line.startswith('"""') or line.startswith("'''")):
                    return False

        # If the loop completes, no invalidating code was found.
        return True


    def _is_valid_docstring_position(self, def_line: int, comment_start: int) -> bool:
        """Checks if a location is a valid position for a docstring.

        According to PEP 257, a docstring must be the first statement in a
        module, function, or class definition. This method verifies that the
        lines between a definition (`def_line`) and the start of a potential
        docstring (`comment_start`) contain no executable code. Only blank
        lines, standard comments (`#`), or existing docstrings are permitted
        in this space.

        Args:
            def_line: The 0-based line index of the `def` or `class` statement.
            comment_start: The 0-based line index where the new docstring
                would begin.

        Returns:
            True if the location is valid for a docstring, False otherwise.
        """
        # Iterate through the lines strictly between the definition and the comment.
        for y in range(def_line + 1, comment_start):
            # Defensively guard against out-of-range indices.
            if y >= len(self.editor.text):
                continue

            line = self.editor.text[y].strip()

            # If we encounter a non-blank line that is not a standard '#' comment,
            # it might be executable code, which invalidates the docstring position.
            if line and not line.startswith("#"):
                # The only exception is if the line is an existing docstring,
                # which we can ignore.
                if not (line.startswith('"""') or line.startswith("'''")):
                    return False

        # If the loop completes without finding any invalidating code, the position is valid.
        return True

    def _add_docstring_pep8(self, start_y: int, end_y: int, delim: str, indent_str: str) -> None:
        """
        Safely inserts PEP 8-compliant docstring delimiters around the selected block.
        """
        # 1. Remember the original cursor line.
        original_cursor_y = self.editor.cursor_y
        final_cursor_y = original_cursor_y

        if start_y == end_y:
            # Handle single-line docstring creation
            line_content = self.editor.text[start_y].strip()
            
            # --- SAFETY CHECK ---
            # Prevent creating invalid syntax like """ ""-text-"" """
            if delim in line_content:
                self.editor._set_status_message(f"Error: Text contains docstring delimiter '{delim}'.")
                return

            self.editor.text[start_y] = f"{indent_str}{delim}{line_content}{delim}"
        else:
            # Handle multi-line docstring creation
            self.editor.text.insert(end_y + 1, f"{indent_str}{delim}")
            self.editor.text.insert(start_y, f"{indent_str}{delim}")
            
            if original_cursor_y >= start_y:
                final_cursor_y += 1

        self.editor.modified = True
        self.editor._set_status_message(f"Added docstring with {delim}")

        # Reset selection and reposition the cursor.
        self.editor.is_selecting = False
        self.editor.selection_start = None
        self.editor.selection_end = None
        
        # Set the cursor to the beginning of its (now shifted) original line.
        self.editor.cursor_y = min(final_cursor_y, len(self.editor.text) - 1)
        self.editor.cursor_x = 0

    def _check_existing_docstring_pep8(
        self, start_y: int, end_y: int, delim: str, indent_str: str
    ) -> tuple[bool, bool]:
        """
        Checks if a line range already forms a well-formed PEP 8 docstring.

        This method inspects the text buffer between `start_y` and `end_y` 
        (inclusive) to determine if it is wrapped in docstring delimiters. It
        classifies the block based on two criteria: whether it is a docstring
        and whether it is single-line or multi-line.

        Args:
            start_y: The 0-based index of the first line in the block to check.
            end_y: The 0-based index of the last line in the block.
            delim: The delimiter string to check for.
            indent_str: The expected indentation of the docstring. This argument
                is currently unused but is kept for API consistency with
                related methods.

        Returns:
            A tuple of two booleans (is_docstring, is_single_line)
            - is_docstring - True if the block is a valid docstring, False otherwise.
            - is_single_line - True if the docstring is on a single line. This is
            always False if is_docstring is False.
        """
        # An out-of-bounds selection cannot be a docstring.
        if start_y >= len(self.editor.text):
            return False, False

        first_line_stripped = self.editor.text[start_y].strip()

        # --- Case 1: Check for a single-line docstring ---
        # e.g., """content""" on one line.
        if (
            start_y == end_y
            and first_line_stripped.startswith(delim)
            and first_line_stripped.endswith(delim)
        ):
            return True, True

        # --- Case 2: Check for a multi-line docstring ---
        # This requires the first line to be just the opening delimiter
        # and the last line to be just the closing delimiter.
        if (
            end_y < len(self.editor.text)
            and self.editor.text[start_y].strip() == delim
            and self.editor.text[end_y].strip() == delim
        ):
            return True, False
            
        # If neither of the above conditions are met, it's not a recognized docstring format.
        return False, False

    def _add_docstring_pep8(self, start_y: int, end_y: int, delim: str, indent_str: str) -> None:
        """
        Inserts PEP 8-compliant docstring delimiters around the selected block.

        This method wraps a range of lines with docstring delimiters, handling
        both single-line and multi-line selections. After the operation, any
        active selection is cleared, and the cursor is repositioned to the
        beginning of the line where it was before the operation started.

        Args:
            start_y: The 0-based starting line index of the range to wrap.
            end_y: The 0-based ending line index (inclusive) of the range.
            delim: The delimiter string to use.
            indent_str: The indentation string to prepend to the delimiters.
        """
        # 1. Remember the original cursor line to restore its position later.
        original_cursor_y = self.editor.cursor_y
        final_cursor_y = original_cursor_y

        if start_y == end_y:
            # --- Handle single-line docstring creation ---
            line_content = self.editor.text[start_y].strip()
            self.editor.text[start_y] = f"{indent_str}{delim}{line_content}{delim}"
            # The cursor Y position doesn't change in this case.
            # The X position will be set to 0 later.
        else:
            # --- Handle multi-line docstring creation ---
            # Insert the closing delimiter first to avoid shifting start_y.
            self.editor.text.insert(end_y + 1, f"{indent_str}{delim}")
            
            # Insert the opening delimiter. This will shift all subsequent lines down.
            self.editor.text.insert(start_y, f"{indent_str}{delim}")
            
            # If the original cursor was at or below the insertion point of the
            # *first* delimiter, it has been shifted down by one line.
            if original_cursor_y >= start_y:
                final_cursor_y += 1
            
            # Note: we don't need to account for the second insertion at `end_y + 1`
            # because our goal is to place the cursor on the line that was originally
            # at `original_cursor_y`. The second insertion happens *after* this line.

        self.editor.modified = True
        self.editor._set_status_message(f"Added docstring with {delim}")

        # 3. Reset selection and reposition the cursor.
        self.editor.is_selecting = False
        self.editor.selection_start = None
        self.editor.selection_end = None
        
        # 4. Set the cursor to the beginning of its (now shifted) original line.
        self.editor.cursor_y = min(final_cursor_y, len(self.editor.text) - 1)
        self.editor.cursor_x = 0


    def _remove_docstring_pep8(self, start_y: int, end_y: int, delim: str, indent_str: str, is_single_line: bool) -> None:
        """
        Removes PEP8 -style docstring delimiters and resets the editor state.

        This method unwraps a text block from its docstring delimiters. After the
        operation, any active selection is cleared, and the cursor is repositioned
        to the beginning of the line where it was before the operation.

        Args:
            start_y: The 0-based starting line index of the docstring block.
            end_y: The 0-based ending line index (inclusive).
            delim: The delimiter string to remove.
            indent_str: The indentation to preserve for single-line unwrapping.
            is_single_line: True if the docstring occupies a single line.
        """
        # 1. Remember the original cursor line.
        original_cursor_y = self.editor.cursor_y

        if is_single_line:
            # --- Handle single-line docstring removal ---
            line = self.editor.text[start_y]
            content = line.strip()
            if content.startswith(delim) and content.endswith(delim):
                uncommented_content = content[len(delim): -len(delim)]
                self.editor.text[start_y] = f"{indent_str}{uncommented_content}"
            
            # In this case, no lines are deleted, so the original_cursor_y is still valid.
            final_cursor_y = original_cursor_y
        else:
            # --- Handle multi-line docstring removal ---
            lines_deleted_before_cursor = 0
            
            # Remove from the bottom up to keep indices stable during deletion.
            if end_y < len(self.editor.text) and self.editor.text[end_y].strip() == delim:
                del self.editor.text[end_y]
                if end_y < original_cursor_y:
                    lines_deleted_before_cursor += 1
            
            if start_y < len(self.editor.text) and self.editor.text[start_y].strip() == delim:
                del self.editor.text[start_y]
                if start_y < original_cursor_y:
                    lines_deleted_before_cursor += 1
            
            # Calculate the new correct position for the cursor.
            final_cursor_y = original_cursor_y - lines_deleted_before_cursor

        self.editor.modified = True
        self.editor._set_status_message("Removed docstring")

        # --- Reset selection and set cursor to the corrected position ---
        self.editor.is_selecting = False
        self.editor.selection_start = None
        self.editor.selection_end = None
        
        # Set the cursor to the beginning of its (now shifted) original line.
        self.editor.cursor_y = min(final_cursor_y, len(self.editor.text) - 1)
        self.editor.cursor_x = 0


    def _toggle_line_comments(self, start_y: int, end_y: int, comment_prefix: str) -> None:
        """
        Smartly toggles line comments for the given line range.

        Its heuristic is:
        1. If **all** non-blank lines in the selection are already commented,
        the entire block is **uncommented**.
        2. Otherwise (if at least one non-blank line is not commented), the
        entire block is **commented**. Blank lines within the selection
        are ignored during this check but are not modified.
        """
        prefix_to_check = comment_prefix.strip()
        
        with self.editor._state_lock:
            lines_in_range = []
            for y in range(start_y, end_y + 1):
                if y < len(self.editor.text):
                    lines_in_range.append(self.editor.text[y])

            # Filter out blank lines for the decision-making process.
            non_blank_lines = [line for line in lines_in_range if line.strip()]

            # Decide whether to comment or uncomment.
            # If there are no non-blank lines, we default to commenting.
            should_uncomment = False
            if non_blank_lines: # Only check if there are non-blank lines.
                should_uncomment = all(
                    line.lstrip().startswith(prefix_to_check) for line in non_blank_lines
                )

            if should_uncomment:
                self._remove_line_comments(start_y, end_y, comment_prefix)
            else:
                self._add_line_comments(start_y, end_y, comment_prefix)


    def _add_line_comments(self, start_y: int, end_y: int, comment_prefix: str) -> None:
        """
        Adds line comments to a range of lines at a consistent indentation level.
        Skips lines that are already commented to prevent double-commenting.
        """
        prefix_to_add = comment_prefix.strip()
        
        with self.editor._state_lock:
            # Find the minimum indentation of non-blank lines in the target range.
            min_indent = float('inf')
            lines_to_process = []
            for y in range(start_y, end_y + 1):
                if y < len(self.editor.text):
                    line = self.editor.text[y]
                    if line.strip():
                        lines_to_process.append((y, line))
                        indent = len(line) - len(line.lstrip())
                        min_indent = min(min_indent, indent)

            if not lines_to_process: # No non-blank lines to comment.
                return

            indent_str = " " * int(min_indent)

            for y, line in lines_to_process:
                # Check if the line is already commented at the correct indent level.
                if not line.lstrip().startswith(prefix_to_add):
                    self.editor.text[y] = indent_str + prefix_to_add + " " + line.lstrip()

            self.editor.modified = True
            self.editor._set_status_message(f"Added '{prefix_to_add}' line comments")


    def _remove_line_comments(self, start_y: int, end_y: int, comment_prefix: str) -> None:
        """Removes a single level of line comments from a range of lines."""
        prefix_stripped = comment_prefix.strip()
        
        with self.editor._state_lock:
            for y in range(start_y, end_y + 1):
                if y < len(self.editor.text):
                    line = self.editor.text[y]
                    stripped_line = line.lstrip()

                    if stripped_line.startswith(prefix_stripped):
                        # Find the content after the prefix, stripping one optional space.
                        content_after_prefix = stripped_line[len(prefix_stripped):]
                        if content_after_prefix.startswith(' '):
                            content_after_prefix = content_after_prefix[1:]
                        
                        # Reconstruct the line.
                        indent_len = len(line) - len(stripped_line)
                        self.editor.text[y] = line[:indent_len] + content_after_prefix

            self.editor.modified = True
            self.editor._set_status_message(f"Removed '{prefix_stripped}' line comments")
                

    def _toggle_block_comment(self, start_y: int, end_y: int, block_delims: tuple) -> None:
        """Toggles a C-style block comment around the selected line range.

        This method wraps or unwraps a range of lines (`start_y` to `end_y`) with
        block comment delimiters (e.g., `/*` and `*/`). The logic for toggling is
        based on a simple check: if the first non-whitespace part of the first
        selected line is the opening delimiter AND the last non-whitespace part
        of the last line is the closing delimiter, the block is considered
        commented and will be uncommented. Otherwise, the block will be wrapped
        in comment delimiters.

        All text modifications are performed under the editor's state lock to
        ensure thread safety. The editor's `modified` status and status message
        are updated accordingly. Note that this operation does not currently
        adjust the selection or cursor position after modification.

        Args:
            start_y: The 0-based starting line index of the range.
            end_y: The 0-based ending line index (inclusive).
            block_delims: A tuple containing the opening and closing
                comment tags, e.g., `('/*', '*/')`.
        """
        open_tag, close_tag = block_delims

        with self.editor._state_lock:
            # Check if the selection is already wrapped in a block comment.
            first_line = self.editor.text[start_y].lstrip()
            last_line = self.editor.text[end_y].rstrip()

            is_block_commented = first_line.startswith(open_tag) and last_line.endswith(
                close_tag
            )

            if is_block_commented:
                # --- UNCOMMENT: remove the opening and closing delimiters ---
                
                # Remove the first occurrence of the opening tag on the first line.
                # Using replace with count=1 ensures we only remove one instance.
                self.editor.text[start_y] = self.editor.text[start_y].replace(open_tag, "", 1)

                # Remove the last occurrence of the closing tag on the last line.
                # rsplit is a robust way to handle this from the right side.
                if close_tag in self.editor.text[end_y]:
                    self.editor.text[end_y] = self.editor.text[end_y].rsplit(close_tag, 1)[0]

                self.editor.modified = True
                self.editor._set_status_message(f"Removed {open_tag}...{close_tag} block comment")
            else:
                # --- COMMENT: wrap the range in the block delimiters ---
                
                # Preserve the leading whitespace of the first line.
                indent = len(self.editor.text[start_y]) - len(first_line)

                # Insert the opening tag after the existing indentation on the first line.
                self.editor.text[start_y] = (
                    self.editor.text[start_y][:indent] + open_tag + " " + first_line
                )
                # Append the closing tag to the end of the last line.
                self.editor.text[end_y] += " " + close_tag

                self.editor.modified = True
                self.editor._set_status_message(f"Wrapped selection in {open_tag}...{close_tag}")


## ==================== History Class (Undo/Redo) ====================
class History:
    """
    Manages the undo/redo action history for the editor.
    """
    def __init__(self, editor: "SwayEditor"):
        self.editor = editor
        self._action_history: list[dict[str, Any]] = []
        self._undone_actions: list[dict[str, Any]] = []
        self._is_in_compound_action = False

    def begin_compound_action(self):
        """Starts a sequence of actions that should be undone/redone together."""
        self._is_in_compound_action = True
        logging.debug("History: Beginning compound action.")

    def end_compound_action(self):
        """Ends a sequence of actions."""
        self._is_in_compound_action = False
        # Очищаем стек redo только в конце всей операции
        self._undone_actions.clear() 
        logging.debug("History: Ended compound action, cleared redo stack.")

    def add_action(self, action: dict[str, Any]):
        """Adds a new action to the history."""
        if not isinstance(action, dict) or "type" not in action:
            logging.warning(f"History: Attempted to add invalid action: {action}")
            return
            
        self._action_history.append(action)
        
        # Очищаем redo стек, только если мы НЕ внутри составного действия
        if not self._is_in_compound_action:
            self._undone_actions.clear()

        logging.debug(f"History: Action '{action['type']}' added. History size: {len(self._action_history)}")

    def clear(self):
        """Clears both undo and redo stacks."""
        self._action_history.clear()
        self._undone_actions.clear()
        logging.debug("History: Undo/Redo stacks cleared.")


    def undo(self) -> bool:
        """
        Undoes the last action from the _action_history stack.
        Restores the text, cursor position, selection state, and modified status
        to what it was before the last action was performed.

        Returns:
            bool: True if the editor's state (text, cursor, scroll, selection, modified flag,
                  or status message) changed as a result of the undo operation, False otherwise.
        """
        logging.debug(f"UNDO CALLED. Action history length: {len(self._action_history)}")
        if self._action_history:
            logging.debug(f"Next action to undo: {self._action_history[-1]['type']}")
        with self.editor._state_lock:
            original_status = self.editor.status_message  # For checking if status message changes at the end

            if not self._action_history:
                self.editor._set_status_message("Nothing to undo")
                return self.editor.status_message != original_status  # Redraw if status changed

            # Store current state to compare against after undoing the action
            pre_undo_text_tuple = tuple(self.editor.text)
            pre_undo_cursor_pos = (self.editor.cursor_y, self.editor.cursor_x)
            pre_undo_scroll_pos = (self.editor.scroll_top, self.editor.scroll_left)
            pre_undo_selection_state = (self.editor.is_selecting, self.editor.selection_start, self.editor.selection_end)
            pre_undo_modified_flag = self.editor.modified

            last_action = self._action_history.pop()
            action_type = last_action.get("type")
            # This flag tracks if the core data (text, selection, cursor) was changed by this undo
            content_or_selection_changed_by_this_undo = False

            logging.debug(f"Undo: Attempting to undo action of type '{action_type}' with data: {last_action}")

            try:
                if action_type == "insert":
                    text_that_was_inserted = last_action["text"]
                    row, col = last_action["position"]
                    lines_inserted = text_that_was_inserted.split('\n')
                    num_lines_in_inserted_text = len(lines_inserted)

                    if not (0 <= row < len(self.editor.text)):
                        raise IndexError(
                            f"Undo insert: Start row {row} out of bounds (text len {len(self.editor.text)}). Action: {last_action}")

                    if num_lines_in_inserted_text == 1:
                        len_inserted = len(text_that_was_inserted)
                        # Check if the text to be removed actually matches what's there
                        if not (col <= len(self.editor.text[row]) and self.editor.text[row][
                                                               col:col + len_inserted] == text_that_was_inserted):
                            logging.warning(
                                f"Undo insert: Text mismatch for deletion at [{row},{col}] len {len_inserted}. Expected '{text_that_was_inserted}', found '{self.text[row][col:col + len_inserted]}'.")
                            # Potentially raise error or try to proceed if desired, for now, log and proceed carefully.
                            # This indicates a potential inconsistency in undo stack or text state.
                        self.editor.text[row] = self.editor.text[row][:col] + self.editor.text[row][col + len_inserted:]
                    else:  # Multi-line insert undo
                        end_row_affected_by_original_insert = row + num_lines_in_inserted_text - 1
                        if end_row_affected_by_original_insert >= len(self.editor.text):
                            raise IndexError(
                                f"Undo insert: End row {end_row_affected_by_original_insert} out of bounds (text len {len(self.editor.text)}). Action: {last_action}")

                        # The suffix that was originally on line 'row' and got pushed down
                        # is now at the end of line 'end_row_affected_by_original_insert'
                        # after the last segment of the inserted text.
                        original_suffix_from_line_row = self.editor.text[end_row_affected_by_original_insert][
                                                        len(lines_inserted[-1]):]

                        self.editor.text[row] = self.editor.text[row][:col] + original_suffix_from_line_row
                        # Delete the lines that were created by the multi-line insert
                        del self.editor.text[row + 1: end_row_affected_by_original_insert + 1]

                    self.editor.cursor_y, self.editor.cursor_x = row, col
                    content_or_selection_changed_by_this_undo = True

                elif action_type == "delete_char":
                    y, x = last_action["position"]  # Position where char was deleted, and cursor stayed
                    char_that_was_deleted = last_action["text"]
                    if not (0 <= y < len(self.editor.text) and 0 <= x <= len(self.editor.text[y])):
                        raise IndexError(
                            f"Undo delete_char: Invalid position ({y},{x}) for re-insertion. Action: {last_action}")
                    self.editor.text[y] = self.editor.text[y][:x] + char_that_was_deleted + self.editor.text[y][x:]
                    self.editor.cursor_y, self.editor.cursor_x = y, x  # Cursor stays at the position of the re-inserted char
                    content_or_selection_changed_by_this_undo = True

                elif action_type == "delete_newline":
                    y, x_at_split_point = last_action["position"]  # Cursor pos after original merge
                    content_of_merged_line = last_action["text"]  # This was the line that got appended
                    if not (0 <= y < len(self.editor.text) and 0 <= x_at_split_point <= len(self.editor.text[y])):
                        raise IndexError(
                            f"Undo delete_newline: Invalid position ({y},{x_at_split_point}) for split. Action: {last_action}")

                    line_to_be_split = self.editor.text[y]
                    self.editor.text[y] = line_to_be_split[:x_at_split_point]
                    self.editor.text.insert(y + 1, content_of_merged_line)
                    self.editor.cursor_y, self.editor.cursor_x = y, x_at_split_point  # Cursor to the split point
                    content_or_selection_changed_by_this_undo = True

                elif action_type == "delete_selection":
                    deleted_segments = last_action["text"]  # This is a list[str]
                    start_y, start_x = last_action["start"]  # Coords where deletion started

                    text_to_restore = "\n".join(deleted_segments)
                    if self.editor.insert_text_at_position(text_to_restore, start_y, start_x):  # This returns bool
                        content_or_selection_changed_by_this_undo = True
                    # For undo of delete_selection, cursor should go to the start of the re-inserted text.
                    self.editor.cursor_y, self.editor.cursor_x = start_y, start_x
                    # Restore selection state if it was stored with the action (optional enhancement)
                    # For now, just clear selection after undoing a deletion.
                    self.editor.is_selecting = False
                    self.editor.selection_start = None
                    self.editor.selection_end = None

                elif action_type in ("block_indent", "block_unindent", "comment_block", "uncomment_block"):
                    changes = last_action.get("changes", [])  # List of dicts
                    if not changes:
                        logging.warning(f"Undo ({action_type}): No 'changes' data in action. Action: {last_action}")

                    for change_item in reversed(changes):  # Restore original_text in reverse order of application
                        idx = change_item["line_index"]
                        original_line_text = change_item.get("original_text")
                        if original_line_text is None:
                            logging.warning(f"Undo ({action_type}): Missing 'original_text' for line {idx}. Skipping.")
                            continue
                        if idx < len(self.editor.text):
                            if self.editor.text[idx] != original_line_text:
                                self.editor.text[idx] = original_line_text
                                content_or_selection_changed_by_this_undo = True
                        else:
                            logging.warning(
                                f"Undo ({action_type}): Line index {idx} out of bounds for text len {len(self.editor.text)}. Skipping.")

                    # Restore selection and cursor state as it was *before* the original operation
                    selection_state_before_op = last_action.get("selection_before")
                    cursor_state_no_sel_before_op = last_action.get("cursor_before_no_selection")

                    # Store current selection/cursor to compare *after* attempting to restore
                    current_sel_is, current_sel_start, current_sel_end = self.editor.is_selecting, self.editor.selection_start, self.editor.selection_end
                    current_curs_y, current_curs_x = self.editor.cursor_y, self.editor.cursor_x

                    if selection_state_before_op and isinstance(selection_state_before_op, tuple) and len(
                            selection_state_before_op) == 2:
                        # Assumes selection_before is (sel_start_coords, sel_end_coords)
                        # The full state was (is_selecting, sel_start_coords, sel_end_coords)
                        # Let's assume "selection_before" from actions like block_indent stores the tuple (start_coords, end_coords)
                        # and implies is_selecting = True.
                        # If it stores (is_selecting, start_coords, end_coords), then adjust accordingly.
                        # Based on block_indent, it stores (start_coords, end_coords).
                        self.editor.is_selecting = True
                        self.editor.selection_start, self.editor.selection_end = selection_state_before_op[0], \
                        selection_state_before_op[1]
                        if self.editor.is_selecting and self.editor.selection_end:  # Position cursor at end of restored selection
                            self.editor.cursor_y, self.editor.cursor_x = self.editor.selection_end
                    elif cursor_state_no_sel_before_op and isinstance(cursor_state_no_sel_before_op, tuple):
                        self.editor.is_selecting = False
                        self.seditor.election_start, self.editor.selection_end = None, None
                        self.editor.cursor_y, self.editor.cursor_x = cursor_state_no_sel_before_op
                    else:  # Fallback if no specific state stored, clear selection
                        self.editor.is_selecting = False
                        self.editor.selection_start, self.editor.selection_end = None, None
                        # Cursor might have been affected by text changes if any.

                    # Check if selection or cursor state actually changed due to restoration
                    if (self.editor.is_selecting != current_sel_is or
                            self.editor.selection_start != current_sel_start or
                            self.editor.selection_end != current_sel_end or
                            (self.editor.cursor_y, self.editor.cursor_x) != (current_curs_y, current_curs_x)):
                        content_or_selection_changed_by_this_undo = True

                else:
                    logging.warning(f"Undo: Unknown action type '{action_type}'. Cannot undo. Action: {last_action}")
                    self._action_history.append(last_action)  # Put it back on history if not handled
                    self.editor._set_status_message(f"Undo failed: Unknown action type '{action_type}'")
                    return True  # Status changed

            except IndexError as e_idx:  # Catch errors from list/string indexing during undo logic
                logging.error(f"Undo: IndexError during undo of '{action_type}': {e_idx}", exc_info=True)
                self.editor._set_status_message(f"Undo error for '{action_type}': Index out of bounds.")
                self._action_history.append(last_action)  # Attempt to put action back
                return True  # Status changed, state might be inconsistent
            except Exception as e_undo_general:  # Catch any other unexpected errors
                logging.exception(f"Undo: Unexpected error during undo of '{action_type}': {e_undo_general}")
                self.editor._set_status_message(f"Undo error for '{action_type}': {str(e_undo_general)[:60]}...")
                self._action_history.append(last_action)  # Attempt to put action back
                return True  # Status changed

            # If undo logic completed (even if it raised an error that was caught and handled above by returning True)
            self._undone_actions.append(last_action)  # Move the undone action to the redo stack

            # Determine `self.editor.modified` state after undo
            if not self._action_history:  # If history is now empty
                self.editor.modified = False  # All changes undone, back to last saved or new state
                logging.debug("Undo: Action history empty, file considered not modified.")
            else:
                # Check if the current text matches the state of the last item in history
                # This is complex. A simpler heuristic: if there's history, it's modified.
                # A more robust system would store a "saved_checkpoint" in history.
                self.editor.modified = True
                logging.debug(
                    f"Undo: Action history not empty ({len(self._action_history)} items), file considered modified.")

            # Ensure cursor and scroll are valid after any operation
            self.editor._ensure_cursor_in_bounds()
            scroll_changed_by_clamp = self.editor._clamp_scroll_and_check_change(pre_undo_scroll_pos)

            # Determine if a redraw is needed based on actual state changes
            final_redraw_needed = False
            if (content_or_selection_changed_by_this_undo or
                    tuple(self.editor.text) != pre_undo_text_tuple or
                    (self.editor.cursor_y, self.editor.cursor_x) != pre_undo_cursor_pos or
                    scroll_changed_by_clamp or
                    (self.editor.is_selecting, self.editor.selection_start, self.editor.selection_end) != pre_undo_selection_state or
                    self.editor.modified != pre_undo_modified_flag):
                final_redraw_needed = True

            if final_redraw_needed:
                self.editor._set_status_message("Action undone")
                logging.debug(f"Undo successful, state changed for action type '{action_type}'. Redraw needed.")
            else:
                # This implies the undo operation resulted in the exact same state as before it ran
                if self.editor.status_message == original_status:
                    self.editor._set_status_message("Undo: No effective change from current state")
                logging.debug(
                    f"Undo for action type '{action_type}' resulted in no effective change from current state.")

            # Return True if a redraw is needed due to state changes OR if status message changed
            return final_redraw_needed or (self.editor.status_message != original_status)

    def redo(self) -> bool:
        """
        Redoes the last undone action from the _undone_actions stack.
        Restores the text, cursor position, selection state, and modified status
        to what it was after the original action was performed (and before it was undone).

        Returns:
            bool: True if the editor's state (text, cursor, scroll, selection, modified flag,
                or status message) changed as a result of the redo operation, False otherwise.
        """
        with self.editor._state_lock:
            original_status = self.editor.status_message  # For checking if status message changes at the end

            if not self._undone_actions:
                self.editor._set_status_message("Nothing to redo")
                return self.editor.status_message != original_status  # Redraw if status message changed

            # Store current state to compare against after redoing the action
            pre_redo_text_tuple = tuple(self.editor.text)
            pre_redo_cursor_pos = (self.editor.cursor_y, self.editor.cursor_x)
            pre_redo_scroll_pos = (self.editor.scroll_top, self.editor.scroll_left)
            pre_redo_selection_state = (self.editor.is_selecting, self.editor.selection_start, self.editor.selection_end)
            pre_redo_modified_flag = self.editor.modified

            action_to_redo = self._undone_actions.pop()
            action_type = action_to_redo.get("type")
            # This flag tracks if the core data (text, selection, cursor) was changed by this redo
            content_or_selection_changed_by_this_redo = False

            logging.debug(f"Redo: Attempting to redo action of type '{action_type}' with data: {action_to_redo}")

            try:
                if action_type == "insert":
                    # To redo an insert, we re-insert the text.
                    # 'text' is the text that was originally inserted.
                    # 'position' is (row, col) where insertion originally started.
                    text_to_re_insert = action_to_redo["text"]
                    row, col = action_to_redo["position"]
                    # insert_text_at_position updates cursor and self.editor.modified
                    if self.editor.insert_text_at_position(text_to_re_insert, row, col):
                        content_or_selection_changed_by_this_redo = True
                    # Cursor is set by insert_text_at_position to be after the inserted text.

                elif action_type == "delete_char":
                    # To redo a delete_char, we re-delete the character.
                    # 'text' is the character that was originally deleted.
                    # 'position' is (y,x) where the character was (and where cursor stayed).
                    y, x = action_to_redo["position"]
                    # Ensure the character that was re-inserted by 'undo' is still there to be 're-deleted'.
                    # This also implies the line length and content should match expectations.
                    char_that_was_reinserted_by_undo = action_to_redo["text"]
                    if not (0 <= y < len(self.editor.text) and
                            0 <= x < len(self.editor.text[y]) and
                            self.editor.text[y][x] == char_that_was_reinserted_by_undo):
                        raise IndexError(
                            f"Redo delete_char: Text mismatch or invalid position ({y},{x}) for re-deletion. "
                            f"Expected '{char_that_was_reinserted_by_undo}' at position. Action: {action_to_redo}"
                        )
                    self.editor.text[y] = self.editor.text[y][:x] + self.editor.text[y][x + 1:]
                    self.editor.cursor_y, self.editor.cursor_x = y, x  # Cursor stays at the position of deletion
                    content_or_selection_changed_by_this_redo = True

                elif action_type == "delete_newline":
                    # To redo a delete_newline (merge), we re-merge the lines.
                    # 'text' is the content of the line that was merged up.
                    # 'position' is (y,x) where cursor ended after original merge.
                    y_target_line, x_cursor_after_merge = action_to_redo["position"]
                    # To redo, we expect line y_target_line to exist, and line y_target_line + 1
                    # (which was re-created by undo) to also exist and match 'text'.
                    if not (0 <= y_target_line < len(self.editor.text) - 1 and
                            self.editor.text[y_target_line + 1] == action_to_redo["text"]):
                        raise IndexError(
                            f"Redo delete_newline: State mismatch for re-merging at line {y_target_line}. Action: {action_to_redo}")

                    self.editor.text[y_target_line] += self.editor.text.pop(y_target_line + 1)
                    self.editor.cursor_y, self.editor.cursor_x = y_target_line, x_cursor_after_merge
                    content_or_selection_changed_by_this_redo = True

                elif action_type == "delete_selection":
                    # To redo a delete_selection, we re-delete the selection.
                    # 'start' and 'end' are the normalized coordinates of the original selection.
                    start_y, start_x = action_to_redo["start"]
                    end_y, end_x = action_to_redo["end"]
                    # delete_selected_text_internal updates cursor and self.editor.modified
                    # It expects normalized coordinates.
                    deleted_segments_again = self.editor.delete_selected_text_internal(start_y, start_x, end_y, end_x)
                    # Check if something was actually deleted this time.
                    if deleted_segments_again or (start_y, start_x) != (end_y, end_x):
                        content_or_selection_changed_by_this_redo = True
                    # Cursor is set by delete_selected_text_internal to (start_y, start_x)

                elif action_type in ("block_indent", "block_unindent", "comment_block", "uncomment_block"):
                    # These actions store 'changes': list of {"line_index", "original_text", "new_text"}
                    # and 'selection_after', 'cursor_after_no_selection' which represent the state
                    # *after* the original operation was performed.
                    # To redo, we re-apply the "new_text" for each change and restore "after" states.
                    changes = action_to_redo.get("changes", [])
                    if not changes:
                        logging.warning(f"Redo ({action_type}): No 'changes' data in action. Action: {action_to_redo}")

                    for change_item in changes:  # Apply in the original order
                        idx = change_item["line_index"]
                        new_line_text = change_item.get("new_text")
                        if new_line_text is None:
                            logging.warning(f"Redo ({action_type}): Missing 'new_text' for line {idx}. Skipping.")
                            continue
                        if idx < len(self.editor.text):
                            if self.editor.text[idx] != new_line_text:
                                self.editor.text[idx] = new_line_text
                                content_or_selection_changed_by_this_redo = True
                        else:
                            logging.warning(f"Redo ({action_type}): Line index {idx} out of bounds. Skipping.")

                    selection_state_after_op = action_to_redo.get("selection_after")
                    cursor_state_no_sel_after_op = action_to_redo.get("cursor_after_no_selection")

                    current_sel_is, current_sel_start = self.editor.is_selecting, self.editor.selection_start
                    current_sel_end = self.editor.selection_end
                    current_curs_y, current_curs_x = self.editor.cursor_y, self.editor.cursor_x

                    if selection_state_after_op and isinstance(selection_state_after_op, tuple) and len(
                            selection_state_after_op) == 3:
                        self.editor.is_selecting, self.editor.selection_start, self.editor.selection_end = selection_state_after_op
                        if self.is_selecting and self.selection_end:
                            self.editor.cursor_y, self.editor.cursor_x = self.editor.selection_end
                    elif cursor_state_no_sel_after_op and isinstance(cursor_state_no_sel_after_op, tuple):
                        self.editor.is_selecting = False
                        self.editor.selection_start, self.editor.selection_end = None, None
                        self.editor.cursor_y, self.editor.cursor_x = cursor_state_no_sel_after_op
                    else:  # Fallback
                        self.editor.is_selecting = False
                        self.editor.selection_start, self.editor.selection_end = None, None

                    if (self.editor.is_selecting != current_sel_is or
                            self.editor.selection_start != current_sel_start or
                            self.editor.selection_end != current_sel_end or
                            (self.editor.cursor_y, self.editor.cursor_x) != (current_curs_y, current_curs_x)):
                        content_or_selection_changed_by_this_redo = True

                    if not changes and not content_or_selection_changed_by_this_redo:
                        pass  # No change by this redo
                    elif not content_or_selection_changed_by_this_redo and changes:  # Text didn't change but selection/cursor might have
                        content_or_selection_changed_by_this_redo = True

                else:
                    logging.warning(f"Redo: Unknown action type '{action_type}'. Cannot redo. Action: {action_to_redo}")
                    self._undone_actions.append(action_to_redo)  # Put it back on undone stack
                    self.editor._set_status_message(f"Redo failed: Unknown action type '{action_type}'")
                    return True  # Status changed

            except IndexError as e_idx:
                logging.error(f"Redo: IndexError during redo of '{action_type}': {e_idx}", exc_info=True)
                self.editor._set_status_message(f"Redo error for '{action_type}': Index out of bounds or text mismatch.")
                self._undone_actions.append(action_to_redo)
                return True
            except Exception as e_redo_general:
                logging.exception(f"Redo: Unexpected error during redo of '{action_type}': {e_redo_general}")
                self.editor._set_status_message(f"Redo error for '{action_type}': {str(e_redo_general)[:60]}...")
                self._undone_actions.append(action_to_redo)
                return True

                # If redo logic completed for a known action type
            self._action_history.append(action_to_redo)  # Move action back to main history

            # A redo operation always implies the document is modified from its last saved state,
            # because it's re-applying a change that was previously undone.
            if content_or_selection_changed_by_this_redo:  # If redo actually did something
                self.editor.modified = True

            self.editor._ensure_cursor_in_bounds()
            scroll_changed_by_clamp = self.editor._clamp_scroll_and_check_change(pre_redo_scroll_pos)

            final_redraw_needed = False
            if (content_or_selection_changed_by_this_redo or
                    tuple(self.editor.text) != pre_redo_text_tuple or
                    (self.editor.cursor_y, self.editor.cursor_x) != pre_redo_cursor_pos or
                    scroll_changed_by_clamp or
                    (self.editor.is_selecting, self.editor.selection_start, self.editor.selection_end) != pre_redo_selection_state or
                    self.editor.modified != pre_redo_modified_flag):
                final_redraw_needed = True

            if final_redraw_needed:
                self.editor._set_status_message("Action redone")
                logging.debug(f"Redo successful and state changed for action type '{action_type}'. Redraw needed.")
            else:
                if self.editor.status_message == original_status:
                    self.editor._set_status_message("Redo: No effective change from current state")
                logging.debug(
                    f"Redo for action type '{action_type}' resulted in no effective change from current state.")

            return final_redraw_needed or (self.editor.status_message != original_status)


## ================== LinterBridge Class ====================
class LinterBridge:
    """
    Управляет всеми операциями линтинга, выбирая между LSP (для Python)
    и внешними CLI-линтерами (для DevOps-форматов).
    """

    def __init__(self, editor: 'SwayEditor'):
        self.editor = editor
        # --- State for LSP ---
        self.lsp_proc: Optional[subprocess.Popen] = None
        self.lsp_reader: Optional[threading.Thread] = None
        self.lsp_message_q: "queue.Queue[dict]" = queue.Queue(maxsize=256)
        self.is_lsp_initialized = False
        self.lsp_seq_id = 0
        self.lsp_doc_versions: dict[str, int] = {}


    def run_linter(self, code: Optional[str] = None) -> bool:
        """
        Acts as the primary dispatcher for all linting operations.

        This method determines which linting tool to use based on the current
        file's language. It handles:
        1.  Running external DevOps linters via `lint_devops.py`.
        2.  Running the Ruff LSP for Python files.
        3.  Reporting when no suitable linter is available.

        It orchestrates the entire process, from getting the code to be linted
        to updating the editor's status and initiating the asynchronous analysis.

        Args:
            code (Optional[str]): The source code to lint. If None, the current
                                editor buffer content is used. This is useful for
                                linting the file content as it was just saved.

        Returns:
            bool: True if the editor's status message was changed, indicating a
                redraw is needed. False otherwise.
        """
        original_status = self.editor.status_message

        # 1. Detect language if not already set.
        if self.editor.current_language is None:
            self.editor.detect_language()

        current_lang = self.editor.current_language

        # 1a. Check for and run external DevOps linters first.
        if HAS_DEVOPS_LINTERS and current_lang in lint_devops.DEVOPS_LINTERS:
            if code is None:
                with self.editor._state_lock:
                    code_to_lint = os.linesep.join(self.editor.text)
            else:
                code_to_lint = code

            # Run the DevOps linter in a background thread to keep the UI responsive.
            self.editor._set_status_message(f"Running linter for {current_lang}...", is_lint_status=True)
            thread = threading.Thread(
                target=self._run_devops_linter_thread,
                args=(current_lang, code_to_lint),
                daemon=True
            )
            thread.start()
            return self.editor.status_message != original_status

        # 1b. Check if the language is Python to use Ruff LSP.
        if current_lang != "python":
            msg = "Ruff: Linting is only available for Python files."
            self.editor._set_status_message(
                message_for_statusbar=msg,
                is_lint_status=True,
                full_lint_output=msg,
                activate_lint_panel_if_issues=False, # Don't activate panel for this info message
            )
            return self.editor.status_message != original_status

        # 2. Prepare Python code for linting via LSP.
        if code is None:
            with self.editor._state_lock:
                code_to_lint = os.linesep.join(self.editor.text)
        else:
            code_to_lint = code

        # 3. Start or reuse the LSP server.
        self._start_lsp_server_if_needed()
        if not self.is_lsp_initialized:
            msg = "Ruff LSP is still initializing..."
            self.editor._set_status_message(
                message_for_statusbar=msg,
                is_lint_status=True,
                full_lint_output=msg,
                activate_lint_panel_if_issues=False,
            )
            return self.editor.status_message != original_status

        # 4. Send didOpen or didChange notification to the LSP server.
        uri = self._get_lsp_uri()
        if uri not in self.lsp_doc_versions:
            self._send_lsp_did_open(code_to_lint)
            op = "didOpen"
        else:
            self._send_lsp_did_change(code_to_lint)
            op = "didChange"

        # 5. Update the status bar to indicate that linting has started.
        self.editor._set_status_message(
            message_for_statusbar="Ruff: Analysis started...",
            is_lint_status=True,
            full_lint_output="Ruff: Analysis in progress...",
            activate_lint_panel_if_issues=True,
        )
        logging.debug(
            "run_linter: Sent LSP '%s' for Python (%d bytes).", op, len(code_to_lint)
        )
        return self.editor.status_message != original_status

    def _run_devops_linter_thread(self, language: str, code: str):
        """
        Worker-функция для потока, запускающая внешний линтер.
        Результат помещается в основную очередь сообщений редактора.
        """
        try:
            # Используем синхронный вызов из lint_devops, т.к. мы уже в отдельном потоке
            result = lint_devops.run_devops_linter(language, code)
            # Помещаем результат в главную очередь сообщений, чтобы он отобразился в статус-баре
            # и в панели линтера
            self.editor._set_status_message(
                f"{language}: analysis complete.",
                is_lint_status=True,
                full_lint_output=result,
                activate_lint_panel_if_issues=True,
            )
        except Exception as e:
            logging.error(f"Error running DevOps linter for {language}", exc_info=True)
            self.editor._set_status_message(
                f"Error in {language} linter: {e}",
                is_lint_status=True,
                full_lint_output=traceback.format_exc(),
                activate_lint_panel_if_issues=True
            )

    def _run_python_lsp(self, code: str) -> bool:
        """Запускает и взаимодействует с Ruff LSP для Python."""
        original_status = self.editor.status_message
        
        self._start_lsp_server_if_needed()
        if not self.is_lsp_initialized:
            msg = "Ruff LSP is still initializing..."
            self.editor._set_status_message(msg, is_lint_status=True, full_lint_output=msg)
            return self.editor.status_message != original_status

        uri = self._get_lsp_uri()
        if uri not in self.lsp_doc_versions:
            self._send_lsp_did_open(code)
            op = "didOpen"
        else:
            self._send_lsp_did_change(code)
            op = "didChange"

        self.editor._set_status_message(
            "Ruff: analysis started...", is_lint_status=True, full_lint_output="Ruff: analysis in progress..."
        )
        logging.debug("Sent %s (%d bytes) to Ruff-LSP.", op, len(code))
        return self.editor.status_message != original_status


    def reload_devops_module(self) -> bool:
        """
        Attempts to hot-reload the `lint_devops` module at runtime.

        This allows for updating linter configurations and logic without
        restarting the entire editor, which is useful for development.

        The method modifies the module-level `lint_devops` variable.

        Returns:
            bool: True if the module was successfully reloaded, False otherwise.
        """
        global lint_devops  # Refers to the module-level import in sway.py

        if not HAS_DEVOPS_LINTERS:
            logging.warning("Attempted to reload 'lint_devops', but it was not found initially.")
            self.editor._set_status_message("'lint_devops' module not available to reload.")
            return False

        try:
            # The core of the hot-reloading logic
            lint_devops = importlib.reload(lint_devops)
            logging.info("'lint_devops' module has been successfully reloaded.")
            self.editor._set_status_message("DevOps linters module reloaded.")
            return True
        except Exception as e:
            # Catch any errors during the reload process
            logging.error("Failed to reload 'lint_devops' module: %s", e, exc_info=True)
            self.editor._set_status_message(f"Error reloading linters module: {e}")
            return False
    
    def _start_lsp_server_if_needed(self):
        """
        Starts or reuses the Ruff LSP (Language Server Protocol) process for Python files.

        This method ensures that the Ruff LSP server is running for the current buffer if the language is Python.
        If the language is not yet detected, it calls `detect_language()`. For non-Python files, the method exits immediately.
        If the Ruff LSP process is already running, it does nothing. Otherwise, it starts the Ruff LSP process,
        launches the stdout reader thread, sends the `initialize` message, and marks the server as initialized.

        The method is idempotent: calling it multiple times is safe and will not start duplicate processes.
        If the Ruff executable is not found, a status message is set and the server is not started.

        Returns:
            None

        Example:
            >>> editor.current_language = None
            >>> editor._start_lsp_server_if_needed()
            # Detects language, starts Ruff LSP if Python

            >>> editor.current_language = "python"
            >>> editor._start_lsp_server_if_needed()
            # Starts Ruff LSP if not already running

            >>> editor.current_language = "rust"
            >>> editor._start_lsp_server_if_needed()
            # Does nothing for non-Python languages

        Side Effects:
            - May start a subprocess for Ruff LSP.
            - May launch a background thread for LSP communication.
            - May update internal LSP state and status messages.
        """
        if self.lsp_proc and self.lsp_proc.poll() is None:
            return
        
        cmd = ["ruff", "server", "--preview"]
        try:
            self.lsp_proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
            logging.info("Ruff LSP started with PID %s", self.lsp_proc.pid)
        except Exception as exc:
            self.editor._set_status_message(f"❌ Ruff LSP error: {exc}")
            self.lsp_proc = None
            return

        self.lsp_reader = threading.Thread(target=self._lsp_reader_loop, name="LSP-stdout", daemon=True)
        self.lsp_reader.start()

        root_uri = f"file://{os.getcwd()}"
        params = {
            "processId": os.getpid(), "rootUri": root_uri, "capabilities": {},
            "clientInfo": {"name": "SwayEditor"}, "workspaceFolders": [{"uri": root_uri, "name": "workspace"}]
        }
        self._send_lsp("initialize", params, is_request=True)
        self._send_lsp("initialized", {})
        self.is_lsp_initialized = True


    def _send_lsp(self, method, params=None, *, is_request=False):
        """
        Sends a Language Server Protocol (LSP) message to the LSP server with a properly formatted Content-Length header.

        This helper method serializes the provided method name and parameters to a JSON-RPC 2.0-compliant message,
        prepends the correct Content-Length header, and writes the message to the LSP server's stdin. If `is_request`
        is True, a unique incrementing id is added to the payload.

        Args:
            method (str): The LSP method name (e.g., "initialize", "textDocument/didOpen").
            params (Optional[dict]): The parameters for the LSP method, or None for methods with no parameters.
            is_request (bool, optional): Whether this message should include an "id" field (for request/response pattern).
                If False (default), sends a notification.

        Returns:
            None

        Side Effects:
            - Writes the message directly to the LSP server's stdin.
            - Logs all outgoing messages, headers, and JSON payloads at DEBUG level.
            - On failure to send (e.g., broken pipe), logs the error, updates the status bar,
            and attempts to terminate the LSP process and reset initialization state.
        """
        if not self.lsp_proc or self.lsp_proc.stdin is None or self.lsp_proc.poll() is not None:
            return
        payload = {"jsonrpc": "2.0", "method": method}
        if params: 
            payload["params"] = params
        if is_request:
            self.lsp_seq_id += 1
            payload["id"] = self.lsp_seq_id
        
        payload_json = json.dumps(payload)
        payload_bytes = payload_json.encode('utf-8')
        header = f"Content-Length: {len(payload_bytes)}\r\n\r\n"
        
        try:
            self.lsp_proc.stdin.write(header.encode('utf-8') + payload_bytes)
            self.lsp_proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self.shutdown()


    def _lsp_reader_loop(self) -> None:
        """
        Continuously reads and processes responses from the LSP server's stdout.

        This method runs in a background thread and correctly implements the protocol for
        reading Language Server Protocol (LSP) messages. It parses the `Content-Length`
        header, reads the exact number of bytes for the message body, decodes it
        from UTF-8, and pushes the resulting JSON object into the internal message queue
        for processing by the main thread.

        The loop is designed to be robust:
        - It reads byte-by-byte to find the header, preventing blocking on partial reads.
        - It reads the message body in chunks to ensure the full message is received.
        - It exits gracefully if the LSP process terminates or its stdout stream closes.
        - It logs all significant events, errors, and protocol violations.

        Returns:
            None

        Side Effects:
            - Consumes the stdout of the LSP process.
            - Parses and enqueues LSP JSON messages.
            - Logs errors and warnings related to the stream, protocol, and I/O.
        """
        while True:
            # Create a local reference for thread safety. This prevents self.lsp_proc
            # from becoming None midway through the loop if shutdown() is called.
            proc = self.lsp_proc
            if not proc or proc.poll() is not None:
                logging.info("LSP Reader: Process has terminated. Exiting loop.")
                break

            stream = proc.stdout
            if not stream:
                logging.error("LSP Reader: stdout stream is not available. Exiting loop.")
                break

            # --- Step 1: Read the LSP header block byte-by-byte ---
            # The header ends with b"\r\n\r\n". Using readline() is incorrect here
            # as LSP is not a line-based protocol.
            header_buffer = b""
            try:
                while not header_buffer.endswith(b"\r\n\r\n"):
                    byte = stream.read(1)
                    if not byte:  # EOF reached; the process has closed its stdout.
                        logging.info("LSP Reader: EOF reached while reading header. Exiting.")
                        return  # Exit the thread cleanly.
                    header_buffer += byte

                    # Safety break to prevent infinite loop on malformed headers.
                    if len(header_buffer) > 4096:
                        logging.error("LSP Reader: Header exceeded 4096 bytes, possible stream corruption. Exiting.")
                        return
            except Exception as e:
                logging.error(f"LSP Reader: Exception while reading header: {e}. Exiting.", exc_info=True)
                return

            # --- Step 2: Parse the Content-Length from the header ---
            header_str = header_buffer.decode('ascii', 'ignore')
            match = re.search(r"Content-Length:\s*(\d+)", header_str, re.IGNORECASE)
            if not match:
                logging.error(f"LSP Reader: Failed to parse Content-Length from header: {header_str!r}")
                continue  # Attempt to recover by starting the next message read.

            content_length = int(match.group(1))

            # --- Step 3: Read the exact number of bytes for the message body ---
            # A single stream.read(n) is not guaranteed to return all n bytes.
            # It's crucial to read in a loop until the full content is received.
            body_bytes = b""
            bytes_remaining = content_length
            try:
                while bytes_remaining > 0:
                    chunk = stream.read(bytes_remaining)
                    if not chunk:
                        logging.error(f"LSP Reader: EOF reached unexpectedly while reading message body. "
                                    f"Expected {bytes_remaining} more bytes.")
                        return
                    body_bytes += chunk
                    bytes_remaining -= len(chunk)
            except Exception as e:
                logging.error(f"LSP Reader: Exception while reading body: {e}. Exiting.", exc_info=True)
                return

            # --- Step 4: Decode, parse, and queue the message ---
            try:
                body_str = body_bytes.decode('utf-8')
                message = json.loads(body_str)
                
                logging.debug(
                    f"LSP RECV <- ID: {message.get('id', 'N/A')}, Method: {message.get('method', 'N/A')}, "
                    f"Result/Error: {str(message.get('result', message.get('error', 'N/A')))[:200]}"
                )
                # Use the LinterBridge's message queue
                self.lsp_message_q.put_nowait(message)
            except json.JSONDecodeError:
                logging.error(f"LSP Reader: Bad JSON received. Body: {body_bytes.decode('utf-8', 'replace')[:500]}")
            except queue.Full:
                logging.error("LSP Reader: Message queue is full. Message dropped.")
            except Exception as e:
                logging.exception(f"LSP Reader: Error processing received message: {e}")


    def process_lsp_queue(self) -> bool:
        """
        Processes all pending messages from the internal LSP server queue.

        This method is intended to be called on each iteration of the main editor
        loop to handle incoming messages from the Language Server Protocol (LSP)
        server. It retrieves all messages from the queue without blocking and
        dispatches recognized LSP methods to their appropriate handlers.

        Currently, it processes `textDocument/publishDiagnostics` messages to
        handle real-time linting and error reporting.

        Returns:
            bool: True if a message was processed that resulted in a state change
                requiring a UI redraw (e.g., new diagnostics were handled).
                Returns False if the queue was empty or no significant messages
                were processed.
        """
        # Flag to track if any action taken requires a screen redraw.
        changed = False

        # Loop through the queue until it's empty.
        while not self.lsp_message_q.empty():
            try:
                # Retrieve one message from the queue without blocking.
                message = self.lsp_message_q.get_nowait()

                # Check if the message is a diagnostics notification.
                if message.get("method") == "textDocument/publishDiagnostics":
                    # Get the parameters for the diagnostics message.
                    params = message.get("params", {})
                    # Delegate to the diagnostics handler method.
                    self._handle_diagnostics(params)
                    # Handling diagnostics always implies a potential UI change.
                    changed = True
                else:
                    # Log any other messages for debugging purposes.
                    logging.debug(f"LSP: Received unhandled message: {message.get('method', 'N/A')}")

            except queue.Empty:
                # This is the expected exit condition for the loop.
                break
            except Exception as e:
                # Catch any unexpected errors during message processing.
                logging.error("LSP: Error processing message from queue: %s", e, exc_info=True)

        return changed

    
    def _handle_diagnostics(self, params: dict) -> None:
        """
        Processes diagnostics data received from the LSP server.

        This method takes the `params` from a `textDocument/publishDiagnostics`
        notification, formats the diagnostic information, and then updates the
        editor's UI by calling `_set_status_message`.

        - If no diagnostics are present, it reports a "no issues" status.
        - If diagnostics exist, it formats them for both a brief status bar message
        and a detailed linter panel view.

        Args:
            params: The dictionary of parameters from the LSP notification,
                    expected to contain a "diagnostics" key with a list of
                    diagnostic objects.

        Returns:
            None
        """
        diagnostics: list[dict] = params.get("diagnostics", [])

        # Case 1: No issues found
        if not diagnostics:
            self.editor._set_status_message(
                message_for_statusbar="✓ No issues found (Ruff)",
                is_lint_status=True,
                full_lint_output="✓ No issues found (Ruff)",
                activate_lint_panel_if_issues=False,  # Don't show panel if no issues
            )
            return

        # Case 2: Issues were found
        
        # Format the first diagnostic for a concise status bar message.
        # We defensively check for keys to avoid crashes on malformed messages.
        try:
            first_diagnostic = diagnostics[0]
            line_number = first_diagnostic.get("range", {}).get("start", {}).get("line", -1) + 1
            message = first_diagnostic.get("message", "Unknown issue")
            
            # Ensure line_number is valid before using it
            if line_number > 0:
                status_bar_message = f"Ruff: {message} (Line {line_number})"
            else:
                status_bar_message = f"Ruff: {message}"

        except (IndexError, TypeError, AttributeError) as e:
            logging.warning(f"Could not parse first diagnostic: {e}", exc_info=True)
            status_bar_message = "Ruff: Issues found (check panel)"

        # Format all diagnostics for the detailed linter panel.
        panel_lines = []
        for diag in diagnostics:
            try:
                line = diag.get("range", {}).get("start", {}).get("line", -1) + 1
                char = diag.get("range", {}).get("start", {}).get("character", -1) + 1
                msg = diag.get("message", "No message provided.")
                
                # Create a clean line for the panel
                if line > 0 and char > 0:
                    panel_lines.append(f"{line}:{char}  {msg}")
                else:
                    panel_lines.append(msg)
            except (TypeError, AttributeError):
                panel_lines.append("Malformed diagnostic item.")
        
        panel_text = "\n".join(panel_lines)

        # Update the editor's UI with the formatted messages.
        # The `activate_lint_panel_if_issues=True` will cause the panel
        # to appear if it's not already active.
        self.editor._set_status_message(
            message_for_statusbar=status_bar_message,
            is_lint_status=True,
            full_lint_output=panel_text,
            activate_lint_panel_if_issues=True,
        )


    def _get_lsp_uri(self) -> str:
        """
        Returns the file:// URI that identifies the current buffer in the editor.

        The URI is a standard way to identify documents in the Language Server
        Protocol. This method constructs an absolute URI from the editor's
        current filename. For new, unsaved buffers, a placeholder name is used.

        Returns:
            str: An absolute file URI string (e.g., "file:///path/to/your/file.py").
        """
        # Access the editor's filename via the stored editor instance.
        # If the filename is not set (e.g., a new buffer), use '<buffer>' as a placeholder.
        # os.path.abspath resolves the path to an absolute path, which is required by the LSP spec.
        filename_or_placeholder = self.editor.filename or '<buffer>'
        absolute_path = os.path.abspath(filename_or_placeholder)
        
        # Construct the final file URI.
        return f"file://{absolute_path}"


    def _send_lsp_did_open(self, text: str) -> None:
        """
        Sends a `textDocument/didOpen` notification to the LSP server.

        This notification is sent when a document is first opened in the editor.
        It informs the server about the document's URI, language, initial version,
        and its full text content, allowing the server to start analyzing it.

        Args:
            text (str): The full text content of the document being opened.
        """
        # Generate the unique URI for the document.
        uri = self._get_lsp_uri()

        # Initialize the document version for this URI. LSP versions start at 1.
        self.lsp_doc_versions[uri] = 1

        # Construct the parameters for the didOpen notification.
        params = {
            "textDocument": {
                "uri": uri,
                "languageId": "python",  # Hardcoded to Python as this client is for Ruff
                "version": 1,
                "text": text,
            }
        }

        # Send the notification to the server using the internal _send_lsp helper.
        self._send_lsp("textDocument/didOpen", params)
        

    def _send_lsp_did_change(self, text: str) -> None:
        """
        Sends a `textDocument/didChange` notification to the LSP server.

        This notification is sent whenever the document's content is modified.
        It includes the document's URI, a new version number, and the full updated
        text. This client uses a "full-text" synchronization strategy, which is
        simpler to implement and well-supported.

        Args:
            text (str): The full, updated text content of the document.
        """
        # Generate the unique URI for the document.
        uri = self._get_lsp_uri()

        # Increment the document version number. The server uses this to resolve
        # potential race conditions between edits and diagnostics.
        current_version = self.lsp_doc_versions.get(uri, 1) + 1
        self.lsp_doc_versions[uri] = current_version

        # Construct the parameters for the didChange notification.
        # The 'contentChanges' array contains the new text.
        params = {
            "textDocument": {
                "uri": uri,
                "version": current_version
            },
            "contentChanges": [
                {
                    "text": text
                }
            ],
        }
        
        # Send the notification to the server using the internal _send_lsp helper.
        self._send_lsp("textDocument/didChange", params)


    def shutdown(self):
        if self.lsp_proc and self.lsp_proc.poll() is None:
            self._send_lsp("shutdown", {})
            self._send_lsp("exit", {})
            try:
                self.lsp_proc.stdin.close() # type: ignore
                self.lsp_proc.terminate()
                self.lsp_proc.wait(timeout=1.0)
            except (subprocess.TimeoutExpired, OSError, BrokenPipeError):
                if self.lsp_proc.poll() is None: 
                    self.lsp_proc.kill()
        if self.lsp_reader and self.lsp_reader.is_alive():
            self.lsp_reader.join(timeout=0.5)
        self.lsp_proc = None
        self.is_lsp_initialized = False



## ================= сlass DrawScreen ==============================
class DrawScreen:
    """
    Класс для отрисовки экрана редактора.
    Содержит логику построения интерфейса с использованием curses.
    """

    MIN_WINDOW_WIDTH = 20
    MIN_WINDOW_HEIGHT = 5
    DEFAULT_TAB_WIDTH = 4  # Не используется напрямую в этих методах, но оставлена

    def __init__(self, editor: Any):  # Используем Any для editor для примера
        self.editor = editor
        self.stdscr = editor.stdscr
        self.colors = editor.colors
        # _text_start_x должен быть инициализирован где-то, например, в _draw_line_numbers
        # Для этого примера установим значение по умолчанию.
        self._text_start_x = 0
        # Убедимся, что editor.visible_lines существует
        if not hasattr(self.editor, 'visible_lines'):
            self.editor.visible_lines = self.stdscr.getmaxyx()[0] - 2  # Примерное значение

    def _needs_full_redraw(self) -> bool:
        """Return True when DrawScreen.draw() must call stdscr.erase().

        A full redraw is required (a) after a window-resize or
        (b) when the editor core explicitly sets the private flag
        `_force_full_redraw` to True.
        """
        resized = self.editor.last_window_size != self.stdscr.getmaxyx()
        force = getattr(self.editor, "_force_full_redraw", False)
        return resized or force

    # ─────────────────────  Безопасное «срезание» слева  ─────────────────────
    def _safe_cut_left(self, s: str, cells_to_skip: int) -> str:
        """
        Отбрасывает слева ровно cells_to_skip экранных ячеек (а не символов!),
        гарантируя, что мы НЕ разрезаем двуширинный символ пополам.

        Возвращает оставшийся хвост строки.
        """
        skipped = 0
        res = []
        for ch in s:
            w = self.editor.get_char_width(ch)  # 1 или 2 (wcwidth)
            if skipped + w <= cells_to_skip:  # всё ещё в зоне «скролла»
                skipped += w
                continue
            if skipped < cells_to_skip < skipped + w:  # граница попала внутрь wide-char
                skipped += w  # пропускаем его целиком
                continue
            res.append(ch)
        return ''.join(res)

    def _should_draw_text(self) -> bool:
        """
        Проверяет, следует ли отрисовывать текстовую область.
        Учитывает видимость строк и минимальные размеры окна.
        """
        height, width = self.stdscr.getmaxyx()
        if self.editor.visible_lines <= 0:
            logging.debug("DrawScreen _should_draw_text: No visible lines area (visible_lines <= 0).")
            return False
        if height < self.MIN_WINDOW_HEIGHT or width < self.MIN_WINDOW_WIDTH:
            logging.debug(
                f"DrawScreen _should_draw_text: Window too small ({width}x{height}). "
                f"Min required: {self.MIN_WINDOW_WIDTH}x{self.MIN_WINDOW_HEIGHT}."
            )
            return False

        # Дополнительная проверка: есть ли вообще текст для отрисовки
        if not self.editor.text or (len(self.editor.text) == 1 and not self.editor.text[0]):
            # Если текст пуст, возможно, все равно нужно очистить область, но сам текст рисовать не нужно.
            # Для простоты, если нет текста, считаем, что рисовать нечего.
            # В реальном приложении это может быть сложнее (например, отрисовка пустого буфера).
            # logging.debug("DrawScreen _should_draw_text: Text buffer is empty.")
            # return False # Раскомментировать, если пустой текст не должен вызывать отрисовку
            pass

        logging.debug("DrawScreen _should_draw_text: Conditions met for drawing text.")
        return True

    def _get_visible_content_and_highlight(self) -> List[Tuple[int, List[Tuple[str, int]]]]:
        """
        Получает видимые строки и их токены с подсветкой синтаксиса.
        Возвращает список кортежей: (line_index, tokens_for_this_line).
        """
        start_line = self.editor.scroll_top
        # self.editor.visible_lines должно быть корректно установлено (например, height - 2)
        num_displayable_lines = self.editor.visible_lines

        end_line = min(start_line + num_displayable_lines, len(self.editor.text))

        if start_line >= end_line:
            logging.debug("DrawScreen _get_visible_content: No visible lines to process.")
            return []

        visible_lines_content = self.editor.text[start_line:end_line]
        line_indices = list(range(start_line, end_line))

        # highlighted_lines_tokens это list[list[tuple[str, int]]]
        highlighted_lines_tokens = self.editor.apply_syntax_highlighting_with_pygments(
            visible_lines_content, line_indices
        )

        # Собираем результат в формате list[tuple[int, list[tuple[str, int]]]]
        visible_content_data = []
        for i, line_idx in enumerate(line_indices):
            if i < len(highlighted_lines_tokens):
                tokens_for_line = highlighted_lines_tokens[i]
                visible_content_data.append((line_idx, tokens_for_line))
            else:
                # Этого не должно произойти, если apply_syntax_highlighting_with_pygments работает корректно
                logging.warning(f"Mismatch between line_indices and highlighted_tokens for line_idx {line_idx}")
                # Добавляем пустые токены для этой строки, чтобы избежать ошибки
                visible_content_data.append((line_idx, []))

        logging.debug(f"DrawScreen _get_visible_content: Prepared {len(visible_content_data)} lines for drawing.")
        return visible_content_data

    def _draw_text_with_syntax_highlighting(self):
        """
        Упрощенный метод отрисовки текста.
        Использует вспомогательные методы для проверок, получения контента и отрисовки строк.
        """
        if not self._should_draw_text():
            logging.debug("DrawScreen _draw_text_with_syntax_highlighting: Drawing skipped by _should_draw_text.")
            # Очищаем текстовую область, если не рисуем, чтобы убрать старый текст
            # Это важно, если _should_draw_text возвращает False из-за маленького окна.
            try:
                for r in range(self.editor.visible_lines):
                    self.stdscr.move(r, self._text_start_x)  # self._text_start_x - начало текстовой области
                    self.stdscr.clrtoeol()
            except curses.error as e:
                logging.warning(f"Curses error clearing text area in _draw_text_with_syntax_highlighting: {e}")
            return

        visible_content_data = self._get_visible_content_and_highlight()
        if not visible_content_data:
            logging.debug(
                "DrawScreen _draw_text_with_syntax_highlighting: No visible content from _get_visible_content_and_highlight.")
            # Аналогично, очищаем, если нет контента (например, пустой файл за пределами видимости)
            try:
                for r in range(self.editor.visible_lines):
                    self.stdscr.move(r, self._text_start_x)
                    self.stdscr.clrtoeol()
            except curses.error as e:
                logging.warning(f"Curses error clearing text area (no content): {e}")
            return

        # Получаем ширину окна один раз
        _h, window_width = self.stdscr.getmaxyx()

        logging.debug(
            f"DrawScreen _draw_text_with_syntax_highlighting: Drawing {len(visible_content_data)} lines. "
            f"scroll_left={self.editor.scroll_left}, text_start_x={self._text_start_x}, window_width={window_width}"
        )

        for screen_row, line_data_tuple in enumerate(visible_content_data):
            # screen_row - это экранная строка (0, 1, ...)
            # line_data_tuple - это (line_index_in_editor_text, tokens_for_this_line)
            self._draw_single_line(screen_row, line_data_tuple, window_width)

    def _draw_single_line(
            self,
            screen_row: int,
            line_data: Tuple[int, List[Tuple[str, int]]],
            window_width: int
    ) -> None:
        """
        Draw a single logical line of source text on the given screen row,
        applying horizontal scroll and syntax-highlight attributes.  Wide
        Unicode characters (wcwidth == 2) are never split in half.

        Args:
            screen_row: Absolute Y position in the curses window.
            line_data:  (buffer_index, [(lexeme, attr), ...]).
            window_width: Current terminal width (in cells).
        """
        line_index, tokens_for_this_line = line_data

        # Clear the target area first.
        try:
            self.stdscr.move(screen_row, self._text_start_x)
            self.stdscr.clrtoeol()
        except curses.error as e:
            logging.error(
                "Curses error while clearing line %d: %s", screen_row, e
            )
            return

        logical_col_abs = 0  # running display width from line start

        for token_text, token_attr in tokens_for_this_line:
            if not token_text:
                continue

            token_disp_width = self.editor.get_string_width(token_text)
            token_start_abs = logical_col_abs
            ideal_x = self._text_start_x + (token_start_abs - self.editor.scroll_left)

            # How many display cells of this token are scrolled off to the left?
            cells_cut_left = 0
            if ideal_x < self._text_start_x:
                cells_cut_left = self._text_start_x - ideal_x

            draw_x = max(self._text_start_x, ideal_x)
            avail_screen_w = window_width - draw_x
            if avail_screen_w <= 0:
                # Nothing further on this line is visible.
                break

            visible_w = max(0, token_disp_width - cells_cut_left)
            visible_w = min(visible_w, avail_screen_w)
            if visible_w <= 0:
                logical_col_abs += token_disp_width
                continue

            # 1. Cut left part safely (do not split a wide char).
            visible_part = self._safe_cut_left(token_text, cells_cut_left)
            if not visible_part:
                logical_col_abs += token_disp_width
                continue

            # 2. Cut right part to fit remaining screen width.
            text_to_draw = ""
            drawn_w = 0
            for ch in visible_part:
                char_w = self.editor.get_char_width(ch)
                if drawn_w + char_w > visible_w:
                    break
                text_to_draw += ch
                drawn_w += char_w

            if text_to_draw:
                try:
                    self.stdscr.addstr(
                        screen_row, draw_x, text_to_draw, token_attr
                    )
                except curses.error as e:
                    # Fallback: draw char-by-char if addstr fails (rare, but safe).
                    logging.debug(
                        "addstr failed at (%d,%d): %s – falling back to addch",
                        screen_row, draw_x, e
                    )
                    cx = draw_x
                    for ch in text_to_draw:
                        if cx >= window_width:
                            break
                        try:
                            self.stdscr.addch(screen_row, cx, ch, token_attr)
                        except curses.error:
                            break
                        cx += self.editor.get_char_width(ch)

            logical_col_abs += token_disp_width

            # Early exit if we've reached the right edge.
            if draw_x + visible_w >= window_width:
                break

    def draw(self):
        """The main screen drawing method."""
        try:
            # 1. Get current window dimensions.
            height, width = self.stdscr.getmaxyx()

            # Check for minimum window size.
            if height < self.MIN_WINDOW_HEIGHT or width < self.MIN_WINDOW_WIDTH:
                self._show_small_window_error(height, width)
                self.editor.last_window_size = (height, width)
                self.stdscr.refresh()
                return

            # 2. Handle window resize.
            if (height, width) != self.editor.last_window_size:
                self.editor.visible_lines = max(1, height - 2)
                self.editor.last_window_size = (height, width)
                self.editor.scroll_left = 0
                self._adjust_vertical_scroll()
                logging.debug(
                    f"Window resized to {width}x{height}. "
                    f"Visible lines: {self.editor.visible_lines}. Scroll left reset."
                )

            # 3. Clear the screen (fully or partially).
            if self._needs_full_redraw():
                self.stdscr.erase()
                self.editor._force_full_redraw = False
            else:
                self._clear_invalidated_lines()

            # 4. Draw all UI components.
            self._draw_line_numbers()
            self._draw_text_with_syntax_highlighting()
            self._draw_search_highlights()
            self._draw_selection()
            self.editor.highlight_matching_brackets()
            self._draw_status_bar()

            # 5. Draw the linter panel if active.
            self._draw_lint_panel()

            # 6. Position the cursor if the panel is not active.
            if not getattr(self.editor, 'lint_panel_active', False):
                self._position_cursor()

            # 7. Update the physical display.
            self._update_display()
            self._maybe_hide_lint_panel()

        except curses.error as e:
            logging.error(f"Curses error in DrawScreen.draw(): {e}", exc_info=True)
            self.editor._set_status_message(f"Draw error: {str(e)[:80]}...")
        except Exception as e:
            logging.exception("Unexpected error in DrawScreen.draw()")
            self.editor._set_status_message(f"Draw error: {str(e)[:80]}...")

    def _clear_invalidated_lines(self):
        """
        Очищает строки, которые в этом кадре будут перерисованы.
        Избегаем глобального clear().
        """
        for row in range(self.editor.visible_lines):
            try:
                self.stdscr.move(row, self._text_start_x)
                self.stdscr.clrtoeol()
            except curses.error:
                pass
        # статус-бар
        try:
            h, _ = self.stdscr.getmaxyx()
            self.stdscr.move(h - 1, 0)
            self.stdscr.clrtoeol()
        except curses.error:
            pass

    def _keep_lint_panel_alive(self, hold_ms: int = 400) -> None:
        """Pin the lint-panel open for a minimum time window.

        This helper is meant to be called immediately after the Flake8 worker
        (running in a background thread) delivers its final output and
        `self.lint_panel_message` has been populated.

        The method sets a **future timestamp** in the private attribute
        ``_next_lint_panel_hide_ts``.  While the current wall-clock time is less
        than that timestamp, :pymeth:`_maybe_hide_lint_panel` will keep
        ``self.editor.lint_panel_active`` set to ``True`` so that the panel is
        drawn on every frame and does **not** “flash” for only a single frame.

        Args:
            hold_ms: Minimum time in **milliseconds** for which the lint panel
                must remain visible.  Default is 400 ms.

        Side Effects:
            * Forces ``self.editor.lint_panel_active = True``.
            * Updates the private timer ``self._next_lint_panel_hide_ts``.

        Notes:
            The draw-loop should call :pymeth:`_maybe_hide_lint_panel` once per
            frame to honour the timer created here.
        """
        self.editor.lint_panel_active = True
        self._next_lint_panel_hide_ts = time.time() + hold_ms / 1000.0

    def _maybe_hide_lint_panel(self) -> None:
        """Deactivate the lint panel once the hold timer expires.

        Should be invoked once per draw frame (e.g. near the end of
        :pymeth:`DrawScreen.draw`).  If the current time is **past** the moment
        stored in ``self._next_lint_panel_hide_ts``, the helper clears
        ``self.editor.lint_panel_active`` so the panel will no longer be painted.

        This keeps the panel visible for at least the duration requested via
        :pymeth:`_keep_lint_panel_alive` and automatically hides it afterwards.

        Side Effects:
            May set ``self.editor.lint_panel_active = False`` when the timer
            elapses.  Does nothing if the panel was already inactive or if the
            timer has not yet expired.
        """
        if getattr(self, "_next_lint_panel_hide_ts", 0) < time.time():
            self.editor.lint_panel_active = False

    def _show_small_window_error(self, height, width):
        """Отображает сообщение о слишком маленьком окне."""
        msg = f"Window too small ({width}x{height}). Minimum is 20x5."
        try:
            self.stdscr.clear()  # Очищаем перед сообщением
            # Центрируем сообщение, если возможно
            msg_len = len(msg)
            start_col = max(0, (width - msg_len) // 2)
            self.stdscr.addstr(height // 2, start_col, msg)
        except curses.error:
            # Если даже это не сработало, терминал в плохом состоянии
            pass

    def _draw_line_numbers(self):
        """Рисует номера строк."""
        height, width = self.stdscr.getmaxyx()
        # Рассчитываем ширину, необходимую для номеров строк
        # Максимальный номер строки - это общее количество строк в файле
        max_line_num = len(self.editor.text)
        max_line_num_digits = len(str(max(1, max_line_num)))  # Минимум 1 цифра для пустых файлов
        line_num_width = max_line_num_digits + 1  # +1 для пробела после номера

        # Проверяем, помещаются ли номера строк в ширину окна
        if line_num_width >= width:
            logging.warning(f"Window too narrow to draw line numbers ({width} vs {line_num_width})")
            # Если не помещаются, пропускаем отрисовку номеров
            self._text_start_x = 0  # Текст начинается с 0-й колонки
            return
        # Сохраняем начальную позицию для отрисовки текста
        self._text_start_x = line_num_width
        line_num_color = self.colors.get("line_number", curses.color_pair(7))
        # Итерируем по видимым строкам на экране
        for screen_row in range(self.editor.visible_lines):
            # Рассчитываем индекс строки в self.text
            line_idx = self.editor.scroll_top + screen_row
            # Проверяем, существует ли эта строка в self.text
            if line_idx < len(self.editor.text):
                # Форматируем номер строки (1-based)
                line_num_str = f"{line_idx + 1:>{max_line_num_digits}} "  # Выравнивание по правому краю + пробел
                try:
                    # Рисуем номер строки
                    self.stdscr.addstr(screen_row, 0, line_num_str, line_num_color)
                except curses.error as e:
                    logging.error(f"Curses error drawing line number at ({screen_row}, 0): {e}")
                    # В случае ошибки, пропускаем отрисовку этой строки и продолжаем
            else:
                # рисуем пустые строки с нужным фоном в области номеров
                empty_num_str = " " * line_num_width
                try:
                    self.stdscr.addstr(screen_row, 0, empty_num_str, line_num_color)
                except curses.error as e:
                    logging.error(f"Curses error drawing empty line number background at ({screen_row}, 0): {e}")

    def _draw_lint_panel(self):
        """
        Рисует всплывающую панель с результатом линтера.
        """
        if not getattr(self.editor, 'lint_panel_active', False):
            return
        msg = self.editor.lint_panel_message
        if not msg:
            return
        h, w = self.stdscr.getmaxyx()
        panel_height = min(max(6, msg.count('\n') + 4), h - 2)
        panel_width = min(max(40, max(len(line) for line in msg.splitlines()) + 4), w - 4)
        start_y = max(1, (h - panel_height) // 2)
        start_x = max(2, (w - panel_width) // 2)

        # Рамка окна
        try:
            for i in range(panel_height):
                line = ""
                if i == 0:
                    line = "┌" + "─" * (panel_width - 2) + "┐"
                elif i == panel_height - 1:
                    line = "└" + "─" * (panel_width - 2) + "┘"
                else:
                    line = "│" + " " * (panel_width - 2) + "│"
                self.stdscr.addstr(start_y + i, start_x, line, curses.A_BOLD)

            # Сообщение, разбитое по строкам
            msg_lines = msg.splitlines()
            for idx, line in enumerate(msg_lines[:panel_height - 3]):
                self.stdscr.addnstr(
                    start_y + idx + 1, start_x + 2,
                    line.strip(), panel_width - 4, curses.A_NORMAL
                )
            # Footer
            footer = "Press Esc to close"
            self.stdscr.addnstr(
                start_y + panel_height - 2, start_x + 2,
                footer, panel_width - 4, curses.A_DIM
            )
        except curses.error as e:
            logging.error(f"Ошибка curses при отрисовке панели линтера: {e}")

    def _draw_search_highlights(self):
        """
        Applies visual highlighting to all search matches found in the visible text area.

        This method iterates through all currently highlighted search matches and uses
        curses attributes to visually distinguish them on the screen (for example,
        by applying a reverse color scheme or a special color pair). Only matches
        that are currently visible on the screen are processed.

        Cautiously handles Unicode and wide characters, screen boundaries, and
        possible curses errors for robust rendering.

        Raises:
            None. All curses errors are logged; the editor remains responsive.
        """
        if not self.editor.highlighted_matches:
            return  # No matches to highlight

        # Get the search highlight color attribute (defaults to A_REVERSE if not set)
        search_color = self.colors.get("search_highlight", curses.A_REVERSE)
        height, width = self.stdscr.getmaxyx()
        line_num_width = len(str(max(1, len(self.editor.text)))) + 1  # Width for line numbers plus space

        # Iterate through all matches to be highlighted
        for match_row, match_start_idx, match_end_idx in self.editor.highlighted_matches:
            # Check if the match is within the currently visible lines
            if match_row < self.editor.scroll_top or match_row >= self.editor.scroll_top + self.editor.visible_lines:
                continue  # Match is off-screen; skip

            screen_y = match_row - self.editor.scroll_top  # Screen row for this match
            line = self.editor.text[match_row]  # The text of the line containing the match

            # Compute X screen positions (before and after scrolling) for match start and end
            match_screen_start_x_before_scroll = self.editor.get_string_width(line[:match_start_idx])
            match_screen_start_x = line_num_width + match_screen_start_x_before_scroll - self.editor.scroll_left

            match_screen_end_x_before_scroll = self.editor.get_string_width(line[:match_end_idx])
            match_screen_end_x = line_num_width + match_screen_end_x_before_scroll - self.editor.scroll_left

            # Clamp drawing area to the visible screen boundaries
            draw_start_x = max(line_num_width, match_screen_start_x)
            draw_end_x = min(width, match_screen_end_x)

            # Calculate the actual width of the highlight to draw
            highlight_width_on_screen = max(0, draw_end_x - draw_start_x)

            # Apply the highlight attribute if there is something to show
            if highlight_width_on_screen > 0:
                try:
                    # Iterate over characters in the line to accurately highlight wide characters
                    current_char_screen_x = line_num_width - self.editor.scroll_left  # Initial X for first char
                    for char_idx, char in enumerate(line):
                        char_width = self.editor.get_char_width(char)
                        char_screen_end_x = current_char_screen_x + char_width

                        # If this character falls within the match range and is visible
                        if (match_start_idx <= char_idx < match_end_idx and
                                current_char_screen_x < width and
                                char_screen_end_x > line_num_width):

                            draw_char_x = max(line_num_width, current_char_screen_x)
                            draw_char_width = min(char_width, width - draw_char_x)

                            if draw_char_width > 0:
                                try:
                                    # Highlight a single character cell with the search color
                                    # chgat(y, x, num_chars, attr): num_chars=1 for one character
                                    self.stdscr.chgat(screen_y, draw_char_x, 1, search_color)
                                except curses.error as e:
                                    logging.warning(
                                        f"Curses error highlighting single char at ({screen_y}, {draw_char_x}): {e}"
                                    )
                        current_char_screen_x += char_width  # Move X for the next character
                except curses.error as e:
                    logging.error(f"Curses error applying search highlight: {e}")

    def _draw_selection(self) -> None:
        """Paint the visual highlight for the current text selection.

        The routine is called from :pymeth:`DrawScreen.draw` *after* the
        coloured text has already been rendered.  It simply toggles the
        attribute :pydata:`curses.A_REVERSE` on the screen cells that belong
        to the active selection.

        Algorithm
        ---------
        1. Exit immediately when no selection is active (nothing to draw).
        2. Normalise *start* / *end* coordinates so that the *start* point is
        “above or equal to” the *end* point in document order.
        3. Pre-compute geometry:
        * ``line_num_width`` – gutter width with line numbers,
        * ``text_area_width`` – printable width for code,
        * ``selection_color`` – the attribute to apply.
        4. Iterate over **document** rows in the selection range and skip
        rows that are scrolled out of view.
        5. For each visible row, compute the **screen** X-offsets of the left
        and right selection borders with the help of
        :pymeth:`SwayEditor.get_string_width`, then clip them by the
        left gutter and right window edge.
        6. Call :pymeth:`curses.window.chgat` to flip the attribute; errors
        (e.g. when the window is extremely narrow) are logged and ignored.

        The method never touches editor state, cursor position or scrolling.
        """
        # 1. Abort early when there is nothing to highlight.
        if (
                not self.editor.is_selecting
                or not self.editor.selection_start
                or not self.editor.selection_end
        ):
            return

        # 2. Unpack and normalise coordinates so that (start_y, start_x) ≤ (end_y, end_x).
        start_y, start_x = self.editor.selection_start
        end_y, end_x = self.editor.selection_end
        if (start_y > end_y) or (start_y == end_y and start_x > end_x):
            start_y, start_x, end_y, end_x = end_y, end_x, start_y, start_x

        # 3. Geometry & reusable values.
        height, width = self.stdscr.getmaxyx()
        line_num_width = len(str(max(1, len(self.editor.text)))) + 1  # “99 |”
        selection_attr = curses.A_REVERSE

        # 4. Iterate through document rows overlapped by the selection.
        for doc_y in range(start_y, end_y + 1):
            # Skip rows that are outside of the viewport.
            if (
                    doc_y < self.editor.scroll_top
                    or doc_y >= self.editor.scroll_top + self.editor.visible_lines
            ):
                continue

            screen_y = doc_y - self.editor.scroll_top

            # Determine logical character indices of the highlight in this row.
            sel_start_idx = start_x if doc_y == start_y else 0
            sel_end_idx = end_x if doc_y == end_y else len(self.editor.text[doc_y])
            if sel_start_idx >= sel_end_idx:  # empty slice → nothing to draw
                continue

            line_text = self.editor.text[doc_y]

            # Convert logical indices → *screen* columns (wcwidth-aware), then
            # adjust for horizontal scrolling and line-number gutter.
            x_left = (
                    line_num_width
                    + self.editor.get_string_width(line_text[:sel_start_idx])
                    - self.editor.scroll_left
            )
            x_right = (
                    line_num_width
                    + self.editor.get_string_width(line_text[:sel_end_idx])
                    - self.editor.scroll_left
            )

            # Clip by the printable area.
            draw_start_x = max(line_num_width, x_left)
            draw_end_x = min(width, x_right)
            highlight_w = max(0, draw_end_x - draw_start_x)

            # 5. Apply the attribute if at least one cell is visible.
            if highlight_w > 0:
                try:
                    self.stdscr.chgat(screen_y, draw_start_x, highlight_w, selection_attr)
                except curses.error as err:
                    logging.error(
                        "Curses error while applying selection highlight "
                        "at (%d, %d) width=%d: %s",
                        screen_y, draw_start_x, highlight_w, err,
                    )

    def truncate_string(self, s: str, max_width: int) -> str:
        """Return *s* clipped to **visual** width *max_width*.

        Wide-Unicode characters (e.g. CJK), zero-width joiners and other
        multi-cell glyphs are accounted for with :pyfunc:`wcwidth.wcwidth`.

        Parameters
        ----------
        s :
            The original text.
        max_width :
            Maximum number of terminal cells the string may occupy.

        Returns
        -------
        str
            Either the original text (if it already fits) or a prefix whose
            display width does not exceed *max_width*.
        """
        result: list[str] = []
        consumed = 0

        for ch in s:
            w = wcwidth(ch)
            if w < 0:  # Non-printable → treat as single-cell
                w = 1
            if consumed + w > max_width:  # Would overflow → stop
                break
            result.append(ch)
            consumed += w

        return "".join(result)

    def _draw_status_bar(self) -> None:
        """
        Draws the single-line status bar at the bottom of the screen.

        Layout:
        | Left Chunk (File Info) | Centered Message | Right Chunk (Git Info) |

        This method clears the last row, applies a background color, prints the three
        main chunks with wcwidth-aware clipping, and then resets the background.
        All exceptions are caught and logged to prevent crashing the render loop.
        """
        logging.debug("Drawing status bar")
        try:
            h, w = self.stdscr.getmaxyx()
            if h <= 0 or w <= 1:
                return  # Window is too small to draw anything.

            y = h - 1  # The bottom row of the screen
            max_col = w - 1

            # --- Color attributes ---
            c_norm = self.colors.get("status", curses.color_pair(10) | curses.A_BOLD)
            c_err = self.colors.get("status_error", curses.color_pair(11) | curses.A_BOLD)
            c_git = self.colors.get("git_info", curses.color_pair(12))
            c_dirty = self.colors.get("git_dirty", curses.color_pair(13) | curses.A_BOLD)

            # Clear the line and set a temporary background
            self.stdscr.move(y, 0)
            self.stdscr.clrtoeol()
            self.stdscr.bkgdset(" ", c_norm)

            # --- Left Chunk: File and cursor info ---
            icon = get_file_icon(self.editor.filename, self.editor.config)
            fname = os.path.basename(self.editor.filename) if self.editor.filename else "No Name"
            lexer = self.editor._lexer.name if self.editor._lexer else "plain text"
            
            left = (f" {icon} {fname}{'*' if self.editor.modified else ''}"
                    f" | {lexer} | {self.editor.encoding.upper()}"  # Using editor's encoding
                    f" | Ln {self.editor.cursor_y + 1}/{len(self.editor.text)}, "
                    f"Col {self.editor.cursor_x + 1}"
                    f" | {'INS' if self.editor.insert_mode else 'REP'} ")

            # Access the Git info tuple from the GitBridge component
            g_branch, _g_user, g_commits = self.editor.git.info
            
            git_enabled = self.editor.config.get("git", {}).get("enabled", True)

            if not g_branch:
                git_txt = "Git: None"
                git_attr = c_norm
            elif not git_enabled:
                git_txt = f"Git: {g_branch.rstrip('*')}"
                git_attr = c_norm
            else:
                git_txt = f"Git: {g_branch.rstrip('*')}"
                if g_commits != "0":
                    git_txt += f" ({g_commits})"
                git_attr = c_dirty if "*" in g_branch else c_git

            # --- Middle Chunk: Status message ---
            msg = self.editor.status_message or "Ready"
            msg_attr = c_err if msg.lower().startswith("error") else c_norm

            # --- Width calculations and painting ---
            gw_left = self.editor.get_string_width(left)
            gw_git = self.editor.get_string_width(git_txt)
            
            # Paint left chunk
            x = 0
            self.stdscr.addnstr(y, x, left, min(gw_left, max_col - x), c_norm)
            x += gw_left

            # Paint right chunk
            x_git = max_col - gw_git
            if x_git > x: # Ensure there is space
                self.stdscr.addnstr(y, x_git, git_txt, gw_git, git_attr)
                right_limit = x_git
            else:
                right_limit = max_col
            
            # Paint centered message
            space_for_msg = right_limit - x
            if space_for_msg > 1:
                msg = self.truncate_string(msg, space_for_msg - 1) # Leave some padding
                gw_msg = self.editor.get_string_width(msg)
                x_msg = x + (space_for_msg - gw_msg) // 2
                self.stdscr.addnstr(y, x_msg, msg, gw_msg, msg_attr)

        except Exception as e:
            # Log error but don't crash the editor
            logging.error("Error in _draw_status_bar: %s", e, exc_info=True)
            try:
                self.editor._set_status_message("Status bar error (see log)")
            except Exception:
                pass
        finally:
            # Always reset background to default to avoid affecting other parts of the UI
            self.stdscr.bkgdset(" ", curses.A_NORMAL)
            

    def _position_cursor(self) -> None:
        """
        Positions the cursor on the screen, ensuring it does not move beyond the status bar
        and always remains within the visible text area.

        This method adjusts both the logical (editor) and physical (screen) cursor positions.
        It also takes into account line numbers, horizontal and vertical scrolling, and ensures
        that the cursor never overlaps with the Git status or status bar at the bottom.

        Side Effects:
            May update self.editor.scroll_top and self.editor.scroll_left.
            Moves the curses cursor to the calculated position.
        """
        height, width = self.stdscr.getmaxyx()
        max_row = height - 2  # The last line available for text (height-1 is the status bar)
        line_num_width = len(str(max(1, len(self.editor.text)))) + 1  # Width for line numbers (e.g. 'NN ')
        text_area_width = max(1, width - line_num_width)

        # --- 1. Adjust internal coordinates ------------------------------------------
        # Ensure the cursor_y is within valid range of lines.
        self.editor.cursor_y = max(0, min(self.editor.cursor_y, len(self.editor.text) - 1))
        current_line = self.editor.text[self.editor.cursor_y]
        # Ensure the cursor_x is within the current line's length.
        self.editor.cursor_x = max(0, min(self.editor.cursor_x, len(current_line)))

        cursor_line_idx = self.editor.cursor_y
        cursor_char_idx = self.editor.cursor_x

        # --- 2. Vertical scrolling ---------------------------------------------------
        # Calculate the screen Y coordinate of the cursor.
        screen_y = cursor_line_idx - self.editor.scroll_top
        if screen_y < 0:
            # Scroll up to bring the cursor line to the top of the visible area.
            self.editor.scroll_top = cursor_line_idx
            screen_y = 0
        elif screen_y >= self.editor.visible_lines:
            # Scroll down to bring the cursor line to the bottom of the visible area.
            self.editor.scroll_top = min(
                len(self.editor.text) - self.editor.visible_lines,
                cursor_line_idx - self.editor.visible_lines + 1
            )
            self.editor.scroll_top = max(0, self.editor.scroll_top)
            screen_y = self.editor.visible_lines - 1
        screen_y = max(0, min(screen_y, max_row))

        # --- 3. Horizontal scrolling -------------------------------------------------
        # Compute the pixel width before the cursor (taking multi-width chars into account).
        cursor_px_before_scroll = self.editor.get_string_width(current_line[:cursor_char_idx])
        current_cursor_screen_x = line_num_width + cursor_px_before_scroll - self.editor.scroll_left

        view_start_x = line_num_width
        view_end_x = width - 1  # The last allowed column

        if current_cursor_screen_x < view_start_x:
            # Scroll left to make the cursor visible at the start of the text area.
            self.editor.scroll_left = cursor_px_before_scroll
        elif current_cursor_screen_x > view_end_x:
            # Scroll right to make the cursor visible at the end of the text area.
            self.editor.scroll_left = max(0, cursor_px_before_scroll - text_area_width + 1)

        # --- 4. Final screen coordinates ---------------------------------------------
        final_cursor_screen_x = (
                line_num_width
                + cursor_px_before_scroll
                - self.editor.scroll_left
        )
        draw_cursor_x = max(view_start_x, min(view_end_x, final_cursor_screen_x))

        # --- 5. Move the cursor -----------------------------------------------------
        try:
            logging.debug(
                f"Positioning cursor: screen_y={screen_y}, draw_cursor_x={draw_cursor_x}. "
                f"Logical: ({self.editor.cursor_y}, {self.editor.cursor_x}). Line: '{current_line}'"
            )
            self.stdscr.move(screen_y, draw_cursor_x)
        except curses.error:
            # Fallback: move to the start of the line if something goes wrong.
            try:
                self.stdscr.move(screen_y, view_start_x)
            except curses.error:
                pass

    def _adjust_vertical_scroll(self):
        """
        Adjusts the vertical scroll (scroll_top) to ensure the cursor remains visible on the screen.

        This method is typically called after window resize events or other situations
        where the cursor could move off-screen. It ensures that scroll_top is always
        within valid bounds and that the cursor is always within the visible text area.

        Side Effects:
            Modifies self.editor.scroll_top as necessary.

        Raises:
            None. All adjustments are logged.
        """
        height, width = self.stdscr.getmaxyx()
        text_area_height = max(1, height - 2)

        # If the total number of lines fits on the screen, always show from the top.
        if len(self.editor.text) <= text_area_height:
            self.editor.scroll_top = 0
            return

        # Calculate the cursor's position relative to the visible area.
        screen_y = self.editor.cursor_y - self.editor.scroll_top

        # If the cursor is above the visible area, scroll up.
        if screen_y < 0:
            self.editor.scroll_top = self.editor.cursor_y
            logging.debug(f"Adjusted vertical scroll: cursor above view. New scroll_top: {self.editor.scroll_top}")
        # If the cursor is below the visible area, scroll down.
        elif screen_y >= text_area_height:
            self.editor.scroll_top = self.editor.cursor_y - text_area_height + 1
            logging.debug(f"Adjusted vertical scroll: cursor below view. New scroll_top: {self.editor.scroll_top}")

        # Ensure scroll_top stays within valid bounds.
        self.editor.scroll_top = max(0, min(self.editor.scroll_top, len(self.editor.text) - text_area_height))
        logging.debug(f"Final adjusted scroll_top: {self.editor.scroll_top}")

    def _update_display(self):
        """
        Physically updates the screen contents using the curses library.

        This method prepares the virtual screen refresh with `noutrefresh()`, which collects all
        pending drawing operations in memory, and then applies all those changes at once to the
        physical terminal using `curses.doupdate()`. This double-buffering approach helps prevent
        flickering and ensures smoother UI updates.

        If a curses error occurs during the refresh, the error is logged and the method
        returns gracefully, assuming the main application loop will handle the situation.

        Raises:
            None. All errors are logged; the editor remains operational.
        """
        try:
            # noutrefresh() prepares window updates in memory, without immediately
            # applying them to the physical terminal screen.
            self.stdscr.noutrefresh()

            # doupdate() applies all pending updates from all windows to the terminal at once.
            curses.doupdate()
        except curses.error as e:
            logging.error(f"Curses doupdate error: {e}")
            # Continue running; the main application loop will handle screen errors gracefully.
            pass


# sway.py

# ... (все ваши импорты, включая 'import threading', 'import asyncio', 'import queue')

# ==================== AsyncEngine Class ====================

class AsyncEngine:
    """
    Runs an asyncio event loop in a separate thread to manage long-running
    and I/O-bound tasks like LSP clients and AI chat clients, without
    blocking the main curses UI thread.
    """
    def __init__(self, to_ui_queue: queue.Queue):
        """
        Args:
            to_ui_queue: A thread-safe queue to send results back to the main UI thread.
        """
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.thread: Optional[threading.Thread] = None
        self.from_ui_queue: queue.Queue = queue.Queue()
        self.to_ui_queue: queue.Queue = to_ui_queue
        self._tasks = set()

    def _start_loop_in_thread(self):
        """Internal method to set up and run the event loop."""
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.main_loop())
        finally:
            if self.loop and self.loop.is_running():
                self.loop.stop()
            self.loop.close()
            logging.info("AsyncEngine event loop has shut down.")

    def start(self):
        """Starts the asyncio event loop in a background thread."""
        if self.thread is not None:
            logging.warning("AsyncEngine already started.")
            return
        logging.info("Starting AsyncEngine background thread...")
        self.thread = threading.Thread(target=self._start_loop_in_thread, daemon=True, name="AsyncEngineThread")
        self.thread.start()

    async def main_loop(self):
        """The main async loop that listens for tasks from the UI."""
        logging.info("AsyncEngine main_loop running.")
        # Здесь в будущем можно будет инициализировать долгоживущие клиенты
        # self.ai_client = AiChatClient(...)
        # self.lsp_client = LspClient(...)
        # await self.lsp_client.start()

        while True:
            try:
                task_data = await self.loop.run_in_executor(None, self.from_ui_queue.get)
                if task_data is None:
                    logging.info("AsyncEngine received stop signal (None).")
                    break
                task = asyncio.create_task(self.dispatch_task(task_data))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            except Exception as e:
                logging.error(f"Error in AsyncEngine main_loop: {e}", exc_info=True)
                await asyncio.sleep(1)
        await self._cancel_all_tasks()

    async def dispatch_task(self, task_data: Dict[str, Any]):
        """Dispatches a task to the correct async handler based on its type."""
        task_type = task_data.get("type")
        logging.debug(f"AsyncEngine dispatching task of type: {task_type}")
        try:
            if task_type == "ai_chat":
                # Временно импортируем и создаем клиента здесь.
                # В будущем он должен быть атрибутом класса.
                # from ai_chat_client import AiChatClient
                # ai_client = self.ai_client
                # ЗАГЛУШКА: имитируем долгий AI-запрос
                await asyncio.sleep(3) # Имитация работы
                reply_text = f"AI reply for: '{task_data.get('prompt', '')[:30]}...'"
                self.to_ui_queue.put({"type": "ai_reply", "text": reply_text})
            else:
                logging.warning(f"AsyncEngine received unknown task type: {task_type}")
        except Exception as e:
            logging.error(f"Error executing async task '{task_type}': {e}", exc_info=True)
            self.to_ui_queue.put({"type": "task_error", "task_type": task_type, "error": str(e)})

    def submit_task(self, task_data: Dict[str, Any]):
        """Thread-safe method for the UI thread to submit a task."""
        self.from_ui_queue.put(task_data)

    def stop(self):
        """Gracefully stops the AsyncEngine."""
        if not self.thread or not self.loop or not self.thread.is_alive():
            return
        logging.info("Stopping AsyncEngine...")
        try:
            self.from_ui_queue.put(None)
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                logging.error("AsyncEngine thread did not stop gracefully.")
        except Exception as e:
            logging.error(f"Exception while stopping AsyncEngine: {e}", exc_info=True)

    async def _cancel_all_tasks(self):
        """Cancels all running asyncio tasks."""
        if not self._tasks:
            return
        logging.info(f"Cancelling {len(self._tasks)} outstanding async tasks...")
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logging.info("All async tasks cancelled.")



## ==================== SwayEditor Class ====================
class SwayEditor:
    """
    The main class for the Sway-Pad terminal-based text editor.

    This class encapsulates the core logic, state, and user interface of the Sway-Pad editor.
    It manages file operations, text editing, syntax highlighting, keybindings, clipboard actions,
    selection handling, Git integration, LSP (Language Server Protocol) support, and interaction
    with the curses-based terminal UI.

    Attributes:
        stdscr (curses.window): The main curses window object for rendering the UI.
        config (dict): The loaded configuration dictionary.
        cursor_x (int): Current cursor column position.
        cursor_y (int): Current cursor row position.
        filename (Optional[str]): The name of the currently open file.
        selection_start (Optional[Tuple[int, int]]): Start position of the current selection.
        selection_end (Optional[Tuple[int, int]]): End position of the current selection.
        is_selecting (bool): Whether a text selection is active.
        lint_panel_message (Optional[str]): Message displayed in the linter panel.
        insert_mode (bool): Whether the editor is in insert mode.
        auto_save_enabled (bool): Whether auto-save is enabled.
        git_info (Tuple[str, str, str]): Current Git branch, user, and commit count.
        ... (many more, see class body for details)

    Methods:
        open_file(filename_to_open: Optional[str] = None) -> bool
            Opens a file and loads its contents into the editor.
        save_file() -> bool
            Saves the current buffer to the associated file.
        save_file_as() -> bool
            Prompts for a new filename and saves the buffer.
        new_file() -> bool
            Main input handler for key events.
        run() -> None
            Main event loop of the editor.
        handle_up() / handle_down() / handle_left() / handle_right() -> bool
            Cursor navigation methods.
        handle_backspace() / handle_delete() -> bool
            Text deletion methods.
        insert_text(text: str) -> bool
            Inserts text at the current cursor position.
        cut() / copy() / paste() -> bool

        show_help() -> bool
            Displays the help panel.

    Side Effects:
        - Interacts with the terminal via curses for UI rendering.
        - Reads and writes files on disk.
        - Executes subprocesses for Git and LSP integration.
        - Uses system clipboard if enabled.
        - Logs events and errors using the logging subsystem.

    Example:
        >>> import curses
        >>> def main(stdscr):
        ...     editor = SwayEditor(stdscr)
        ...     editor.run()
        >>> curses.wrapper(main)
    """
    def _set_status_message(
            self,
            message_for_statusbar: str,
            is_lint_status: bool = False,
            full_lint_output: Optional[str] = None,
            activate_lint_panel_if_issues: bool = False
    ) -> None:
        """
        Sets the status message to be displayed in the status bar and, for linter messages,
        updates the linter panel content and visibility.

        Args:
            message_for_statusbar (str): The (usually short) message for the status bar.
            is_lint_status (bool): True if this is a status message originating from a linter.
            full_lint_output (Optional[str]): The full output from the linter, intended for the
                                            linter panel. Used only if is_lint_status is True.
                                            If None and is_lint_status is True,
                                            self.lint_panel_message is not changed.
            activate_lint_panel_if_issues (bool): If True and is_lint_status is True,
                                                the linter panel will be activated if full_lint_output
                                                indicates issues (i.e., not a "no issues" message).
        """
        if not hasattr(self, "_last_status_msg_sent"):
            self._last_status_msg_sent = None
        if not hasattr(self, "_force_full_redraw"):  # Make sure the flag exists
            self._force_full_redraw = False

        if is_lint_status:
            # Track if anything changed in the panel
            panel_state_or_content_changed = False

            # Update the linter panel message if provided
            if full_lint_output is not None:
                new_panel_message_str = str(full_lint_output)
                if self.lint_panel_message != new_panel_message_str:
                    self.lint_panel_message = new_panel_message_str
                    logging.debug(f"Linter panel message updated: '{self.lint_panel_message[:100]}...'")
                    panel_state_or_content_changed = True  # The panel's content has changed

            logging.debug(f"Linter status bar message: '{message_for_statusbar}'")

            # Queue the message for the status bar
            if message_for_statusbar != self._last_status_msg_sent:
                try:
                    self._msg_q.put_nowait(str(message_for_statusbar))
                    self._last_status_msg_sent = message_for_statusbar
                except queue.Full:
                    logging.error("Status message queue is full (linter message). Dropping message.")
                except Exception as e:
                    logging.error(f"Failed to add linter status message to queue: {e}", exc_info=True)

            # Decide whether to activate the linter panel
            if activate_lint_panel_if_issues and self.lint_panel_message:
                no_issues_substrings = ["no issues found", "нет проблем"]
                panel_message_lower = self.lint_panel_message.strip().lower()
                has_actual_issues = not any(sub in panel_message_lower for sub in no_issues_substrings)

                if has_actual_issues:
                    if not self.lint_panel_active:  # If the panel was not active
                        self.lint_panel_active = True
                        logging.debug("Linter panel activated due to detected issues.")
                        panel_state_or_content_changed = True  # The panel's active state has changed
                else:
                    # If there are no real issues and activate_lint_panel_if_issues=True,
                    # it means we should NOT activate the panel automatically.
                    # If it was already active, we do NOT deactivate it here;
                    # that is handled by cancel_operation or _maybe_hide_lint_panel.
                    logging.debug("No linting issues found; panel not automatically activated (or remains as is).")

            # Force a redraw if the panel is active and its state or content has changed
            if panel_state_or_content_changed and self.lint_panel_active:
                # If the panel was ALREADY active and its content changed, OR
                # if the panel JUST became active (due to has_actual_issues),
                # a redraw is needed to show the changes.
                self._force_full_redraw = True
                logging.debug(
                    "Forcing full redraw because linter panel state or content changed while panel is (or became) active.")

        else:  # Normal status bar messages (not from the linter)
            if message_for_statusbar == self._last_status_msg_sent:
                logging.debug(f"Skipping duplicate status message: '{message_for_statusbar}'")
                return
            try:
                self._msg_q.put_nowait(str(message_for_statusbar))
                self._last_status_msg_sent = message_for_statusbar
                logging.debug(f"Queued status message for status bar: '{message_for_statusbar}'")
            except queue.Full:
                logging.error("Status message queue is full. Dropping message.")
            except Exception as e:
                logging.error(f"Failed to add status message to queue: '{message_for_statusbar}': {e}", exc_info=True)

    def __init__(self, stdscr: "curses.window") -> None:
        """
        Creates and fully initializes a `SwayEditor` instance.

        This constructor performs all one-time setup steps by initializing core editor state
        and delegating specialized setup to dedicated component classes like KeyBinder,
        GitBridge, and DrawScreen.

        Workflow:
        1.  Configures the low-level terminal and `curses` runtime.
        2.  Loads user configuration from `config.toml` or uses safe defaults.
        3.  Initializes color pairs, clipboard, and auto-save parameters.
        4.  Sets up core editor state (text buffer, cursor, etc.) and threading primitives.
        5.  Initializes component classes:
            -   `KeyBinder` for all key-to-action mapping.
            -   `GitBridge` for all version control integration.
            -   `DrawScreen` for all UI rendering.
        6.  Performs an initial synchronous fetch of Git information if enabled.
        7.  Sets the system locale for correct character handling.

        Args:
            stdscr: The root `curses` window object as received from `curses.wrapper`.

        Side Effects:
            - Configures terminal for raw key input.
            - Loads configuration from disk.
            - Initializes logging, clipboard, and background thread resources.
            - May read files from disk and query Git synchronously upon startup.

        Attributes (excerpt):
            stdscr (curses.window): The root curses window.
            config (dict): The merged application configuration.
            text (List[str]): The in-memory document, one string per line.
            keybinder (KeyBinder): Manages all key input and action mapping.
            git (GitBridge): Manages all Git integration.
            drawer (DrawScreen): Renders the entire UI.
            visible_lines (int): Number of text rows that currently fit in the viewport.
            _force_full_redraw (bool): Flag to force a full screen erase on the next frame.

        Raises:
            RuntimeError: Re-raises critical exceptions that make the editor
                unusable (e.g., failure to set up keybindings).
        """
        # ───────────────────── Terminal low-level tweaks ─────────────────────
        if sys.platform != "win32":
            try:
                fd = sys.stdin.fileno()
                attrs = termios.tcgetattr(fd)
                attrs[0] &= ~(termios.IXON | termios.IXOFF)
                attrs[3] &= ~termios.ICANON
                termios.tcsetattr(fd, termios.TCSANOW, attrs)
                logging.debug("Terminal IXON/IXOFF and ICANON successfully disabled (Unix).")
            except Exception as exc:
                logging.warning("Could not set Unix terminal attributes: %s", exc)
        else:
            logging.debug("Windows detected: skipping termios setup.")

        # ───────────────────── Curses runtime initialization ─────────────────
        self.stdscr = stdscr
        self.stdscr.keypad(True)
        curses.raw()
        curses.noecho()
        curses.curs_set(1)

        # ───────────────────── Configuration & Colors ───────────────────────
        try:
            self.config = load_config()
        except Exception as exc:
            logging.error("Failed to load config: %s – using defaults", exc)
            # Fallback config structure
            self.config = {
                "editor": {"use_system_clipboard": True, "tab_size": 4, "use_spaces": True, "default_new_filename": "untitled.txt"},
                "keybindings": {}, "colors": {}, "git": {"enabled": True},
                "settings": {"auto_save_interval": 1, "show_git_info": True},
                "file_icons": {"text": "📝", "default": "❓"}, "supported_formats": {},
            }

        self.user_colors = self.config.get("colors", {})
        self.colors: dict[str, int] = {}
        self.init_colors()

        # ───────────────────── Clipboard support ─────────────────────────────
        self.use_system_clipboard = self.config["editor"].get("use_system_clipboard", True)
        self.pyclip_available = self._check_pyclip_availability()
        if not self.pyclip_available and self.use_system_clipboard:
            logging.warning("System clipboard unavailable – falling back to internal buffer.")
            self.use_system_clipboard = False
        self.internal_clipboard: str = ""

        # ───────────────────── Auto-save parameters ──────────────────────────
        self._auto_save_thread: Optional[threading.Thread] = None
        self._auto_save_enabled = False
        self._auto_save_stop_event = threading.Event()
        try:
            self._auto_save_interval = float(self.config["settings"].get("auto_save_interval", 1.0))
            if self._auto_save_interval <= 0: 
                raise ValueError
        except (ValueError, TypeError):
            logging.warning("Invalid auto_save_interval – defaulting to 1.0 min.")
            self._auto_save_interval = 1.0

        # ───────────────────── Core editor state ─────────────────────────────
        self.insert_mode = True
        self.status_message = "Ready"
        self._last_status_msg_sent: Optional[str] = None
        self.lint_panel_message: Optional[str] = None
        self.lint_panel_active = False

        # Note: action_history and undone_actions moved to History class
        self.history = History(self)

        # ───────────────────── Thread-safe queues / locks ────────────────────
        self._state_lock = threading.RLock()
        self._shell_cmd_q: "queue.Queue[str]" = queue.Queue()        
        self._msg_q: "queue.Queue[str]" = queue.Queue() # очередь _msg_q является общей для всего редактора
        self._git_q: "queue.Queue[tuple[str,str,str]]" = queue.Queue()
        self._git_cmd_q: "queue.Queue[str]" = queue.Queue()

        # ─────────────────────  ASYNC ENGINE  ────────────────────
        self._async_results_q: "queue.Queue[dict]" = queue.Queue()
        
        # Async Engine Component Initialization 
        self.async_engine = AsyncEngine(to_ui_queue=self._async_results_q)
        self.async_engine.start()

        # ───────────────────── Language / Highlighting state ──────────────────
        self.current_language: Optional[str] = None
        self._lexer: Optional[TextLexer] = None

        # ───────────────────── Buffer & Caret position ───────────────────────
        self.text = [""]
        self.cursor_x = 0
        self.cursor_y = 0
        self.scroll_top = 0
        self.scroll_left = 0
        self.modified = False
        self.encoding = "UTF-8"
        self.filename: Optional[str] = None

        # Selection & Search state
        self.selection_start: Optional[tuple[int, int]] = None
        self.selection_end: Optional[tuple[int, int]] = None
        self.is_selecting = False
        self.search_term = ""
        self.search_matches: list[tuple[int, int, int]] = []
        self.current_match_idx = -1
        self.highlighted_matches: list[tuple[int, int, int]] = []
        self.custom_syntax_patterns = []

        # ───────────────── Drawing & Component Initialization ──────────────────
        self.visible_lines = 0
        self.last_window_size: tuple[int, int] = (0, 0)
        self._force_full_redraw = False
        self.drawer = DrawScreen(self)

        # ───────────────────── Initial Caret & Scroll ────────────────────────
        self.set_initial_cursor_position()

        # ───────────────────── History Component ──────────────────────────
        self.history = History(self)

        # ───────────────────── CodeCommenter – Initial  ─────────────────
        # CodeCommenter Component
        self.commenter = CodeCommenter(self)

        # ───────────────────── Git – Initial Synchronous Fetch ─────────────────
        # GitBridge Component
        self.git = GitBridge(self)

        # LSP
        self.linter_bridge = LinterBridge(self)
        
        # KeyBinder Component
        try:
            self.keybinder = KeyBinder(self)
        except Exception as exc:
            logging.critical("Failed to initialize KeyBinder: %s", exc, exc_info=True)
            raise RuntimeError("Key binding setup failed, cannot continue.") from exc
        
        # ───────────────────── Locale ────────────────────────────────────────
        try:
            locale.setlocale(locale.LC_ALL, "")
        except locale.Error as exc:
            logging.error("Could not set system locale: %s", exc, exc_info=True)

        logging.info("SwayEditor initialized successfully.")


    def close(self) -> None:
        """
        Gracefully shuts down the editor and releases all associated resources.

        This method stops background services (like auto-save) and delegates the
        shutdown of any running linter processes (such as the LSP server) to the
        `LinterBridge` component.

        This ensures a clean exit by properly terminating child processes and
        joining their associated threads.

        Notes:
            This method is idempotent: repeated calls are safe and will not raise
            exceptions if background services are already stopped.
        """
        logging.info("SwayEditor.close() called, preparing to shut down all components.")

        # --- 1. Stop background tasks (like auto-save) if they exist ---
        # This logic remains the same, assuming you have an auto-save mechanism.
        try:
            # Check if the auto-save thread exists and signal it to stop.
            if hasattr(self, "_auto_save_stop_event"):
                self._auto_save_stop_event.set()
                logging.debug("Signaled auto-save thread to stop.")
            # Your original code called a method; this is a more direct way if
            # you have a stop event. Adapt as needed.
            # self._stop_auto_save_thread()
        except AttributeError:
            # It's okay if auto-save attributes don't exist (e.g., disabled).
            pass
        except Exception as e:
            # Log any other errors during task shutdown.
            logging.warning(f"Exception while stopping background tasks: {e}")

        # --- 2. Delegate LSP/linter shutdown to the LinterBridge component ---
        # Instead of handling the process and messages directly here, we just call
        # the shutdown method on our dedicated component. LinterBridge now
        # encapsulates all the details of stopping the LSP process and its threads.
        logging.debug("Delegating linter/LSP shutdown to LinterBridge.")
        self.linter_bridge.shutdown()

        logging.info("SwayEditor components have been shut down.")
        

    # ───────────────────── Comment/Uncomment Block - SwayEditor ─────────────────────
    def _determine_lines_to_toggle_comment(self) -> Optional[tuple[int, int]]:
        """
        Determines the line range affected by comment or uncomment actions.

        With an active selection, returns the range from the first to the last selected line,
        excluding the last line if the selection ends at column 0 (to match common IDE behavior).
        Without a selection, returns the current cursor row as both start and end.

        Returns:
            Optional[tuple[int, int]]: (start_row, end_row) indices, or None if selection is invalid.

        Example:
            >>> editor.selection_start = (2, 0)
            >>> editor.selection_end = (5, 0)
            >>> editor.is_selecting = True
            >>> editor._determine_lines_to_toggle_comment()
            (2, 4)
            >>> editor.is_selecting = False
            >>> editor.cursor_y = 3
            >>> editor._determine_lines_to_toggle_comment()
            (3, 3)
        """
        if self.is_selecting and self.selection_start and self.selection_end:
            norm_range = self._get_normalized_selection_range()
            if not norm_range:
                return None
            start_coords, end_coords = norm_range

            start_y = start_coords[0]
            end_y = end_coords[0]

            # Exclude the last line if selection ends at column 0 and spans multiple lines
            if end_coords[1] == 0 and end_y > start_y:
                end_y -= 1
            return start_y, end_y
        else:
            return self.cursor_y, self.cursor_y

 
    def toggle_comment_block(self) -> bool:
        """
        Определяет диапазон строк и делегирует операцию комментирования
        вспомогательному классу CodeCommenter.
        """
        line_range = self._determine_lines_to_toggle_comment()
        if line_range is None:
            self._set_status_message("No lines selected to comment/uncomment.")
            return True # Сообщение статуса изменилось

        start_y, end_y = line_range
        
        # Делегируем вызов нашему новому объекту
        self.commenter.perform_toggle(start_y, end_y)

        # Операция всегда что-то меняет или выводит сообщение,
        # поэтому всегда считаем, что нужна перерисовка.
        return True
 
    # --- lsp -------------------------------------------------
    def reload_devops_module(self) -> bool:
        """
        Delegates the request to hot-reload the DevOps linter module.

        The actual implementation is handled by the LinterBridge component.
        This method provides a clean access point from the editor's keybindings.

        Returns:
            bool: The result from the LinterBridge's reload attempt.
        """
        # Delegate the call to the component responsible for linters.
        return self.linter_bridge.reload_devops_module()

    def run_lint_async(self, code: Optional[str] = None) -> bool:
        """
        Initiates a linting operation for the current buffer.

        This method delegates the entire linting process—including tool selection
        (LSP vs. external), execution, and result handling—to the LinterBridge
        component.

        Args:
            code (Optional[str]): The source code to lint. If None, the current
                                editor buffer content is used.

        Returns:
            bool: True if the operation changed the editor's status message,
                indicating a redraw is needed.
        """
        return self.linter_bridge.run_linter(code)

    def show_lint_panel(self) -> bool:
        """
        Toggles the visibility of the linter panel.

        This method acts as a user-driven toggle for the panel that displays
        detailed linter output. Its behavior is as follows:
        - If the panel is currently active, it will be hidden.
        - If the panel is inactive but there is a lint message to show, it
        will be activated and displayed.
        - If the panel is inactive and there is no lint message, it informs
        the user and remains hidden.

        This provides an intuitive toggle action for the user, typically bound
        to a key like F4 (which might be shared with `run_lint_async`).

        Returns:
            bool: True if the panel's visibility state (`self.lint_panel_active`)
                or the status message changed, indicating a redraw is needed.
                False otherwise.
        """
        logging.debug(
            f"show_lint_panel called. Current state: panel_active={self.lint_panel_active}, "
            f"message_exists={bool(self.lint_panel_message)}"
        )

        original_panel_state = self.lint_panel_active
        original_status = self.status_message
        
        # Logic for toggling the panel
        if self.lint_panel_active:
            # If the panel is currently visible, hide it.
            self.lint_panel_active = False
            self._set_status_message("Lint panel hidden")
            logging.debug("show_lint_panel: Panel was active, now hidden.")
        elif self.lint_panel_message:
            # If the panel is hidden but there is a message to show, activate it.
            self.lint_panel_active = True
            # No need to set a status message here, as the panel appearing is the feedback.
            logging.debug("show_lint_panel: Panel was inactive, now activated to show message.")
        else:
            # If the panel is hidden and there is no message, inform the user.
            self._set_status_message("No linting information to display.")
            logging.debug("show_lint_panel: No lint message available to show.")

        # A change occurred if the panel's state flipped or the status message was updated.
        state_changed = (self.lint_panel_active != original_panel_state or
                        self.status_message != original_status)
        
        if state_changed:
            logging.debug(f"show_lint_panel: State changed. Panel active: {self.lint_panel_active}. "
                        f"Status: '{self.status_message}'")

        return state_changed

    # ----- Clipboard Handling --------------------------------------
    def _check_pyclip_availability(self) -> bool:  # Added return type hint
        """
        Checks the availability of the pyperclip library and underlying system clipboard utilities.
        This is typically called once during editor initialization.
        """
        # First, check if system clipboard usage is enabled in the configuration.
        if not self.config.get("editor", {}).get("use_system_clipboard", True):
            logging.debug("System clipboard usage is disabled by editor configuration.")
            return False  # Not available because it's turned off by config

        # Try to import and perform a basic operation with pyperclip.
        try:
            # Attempt a benign copy operation to check if pyperclip and its dependencies are functional.
            # An empty string copy should not affect the actual clipboard content significantly
            # but will trigger exceptions if pyperclip cannot access the clipboard.
            pyperclip.copy("")

            # pyperclip.paste() can sometimes be problematic or require user interaction
            # (e.g., on Wayland or due to terminal security policies), so a successful copy("")
            # is often a sufficient check for basic availability.
            # If paste() is also critical for your definition of "available", you might add it,
            # but be prepared for more potential PyperclipException scenarios.
            logging.debug("pyperclip and system clipboard utilities appear to be available.")
            return True
        except pyperclip.PyperclipException as e:
            # This exception is raised by pyperclip if it encounters issues specific
            # to clipboard access (e.g., required utilities like xclip/xsel on Linux not found).
            logging.warning(
                f"System clipboard unavailable via pyperclip: {str(e)}. "
                f"Falling back to internal clipboard. Ensure clipboard utilities "
                f"(e.g., xclip, xsel, wl-copy, pbcopy) are installed."
            )
            return False
        except ImportError:  # Should not happen if pyperclip is a listed dependency
            logging.warning(
                "pyperclip library not found. System clipboard integration disabled. "
                "Please install it (e.g., 'pip install pyperclip')."
            )
            return False
        except Exception as e:
            # Catch any other unexpected errors during the check.
            logging.warning(
                f"An unexpected error occurred while checking system clipboard availability: {e}. "
                f"Falling back to internal clipboard."
                , exc_info=True)  # Include stack trace for unexpected errors
            return False

    def get_selected_text(self):
        """
        Returns the currently selected text from the editor buffer.

        This method retrieves text between `selection_start` and `selection_end`, inclusive.
        It handles single-line and multi-line selections and normalizes the order if the
        start of the selection is logically after the end.

        Returns:
            str: The selected text as a string. Returns an empty string if no selection is active.
        """

        if not self.is_selecting or self.selection_start is None or self.selection_end is None:
            return ""

        start_row, start_col = self.selection_start
        end_row, end_col = self.selection_end

        # Normalize the selection so that the start always comes before the end
        if start_row > end_row or (start_row == end_row and start_col > end_col):
            start_row, start_col, end_row, end_col = end_row, end_col, start_row, start_col

        selected_lines = []

        if start_row == end_row:
            # Ensure indices are within the bounds of the line
            line = self.text[start_row]
            start_col = max(0, min(start_col, len(line)))
            end_col = max(0, min(end_col, len(line)))
            selected_lines.append(line[start_col:end_col])
        else:
            # First line (from start_col to the end)
            line = self.text[start_row]
            start_col = max(0, min(start_col, len(line)))
            selected_lines.append(line[start_col:])

            # Middle lines (entire lines between the first and last)
            for row in range(start_row + 1, end_row):
                if 0 <= row < len(self.text):  # Safety check
                    selected_lines.append(self.text[row])

            # Last line (from the beginning to end_col)
            if 0 <= end_row < len(self.text):  # Safety check
                line = self.text[end_row]
                end_col = max(0, min(end_col, len(line)))
                selected_lines.append(line[:end_col])
            elif end_row == len(self.text) and end_col == 0:
                # Special case: selection ends at column 0 on a new non-existent line
                pass  # Do not add anything
            else:
                logging.warning(f"get_selected_text: end_row {end_row} out of bounds {len(self.text)}")

        return "\n".join(selected_lines)

    def copy(self) -> bool:
        """
        Copies the selected text to the internal clipboard and, if enabled/available,
        to the system clipboard.
        This action does not modify the document's text content, cursor, or scroll position.
        It only potentially changes the status message.

        Returns:
            bool: True if the status message changed as a result of this operation,
                  False otherwise.
        """

        original_status = self.status_message  # Store to check for actual change

        selected_text = self.get_selected_text()  # Retrieves text based on self.selection_start/end

        if not selected_text:
            # No text was selected (or selection was empty).
            self._set_status_message("Nothing to copy")
            return self.status_message != original_status  # Redraw if status changed

        # Text was selected, proceed with copying.
        self.internal_clipboard = selected_text
        current_status_update = "Copied to internal clipboard"  # Default message

        if self.use_system_clipboard and self.pyclip_available:
            try:
                pyperclip.copy(selected_text)
                current_status_update = "Copied to system clipboard"
                logging.debug("Selected text copied to system clipboard successfully.")
            except pyperclip.PyperclipException as e:
                logging.error(f"Failed to copy to system clipboard: {str(e)}")
                current_status_update = "Copied to internal clipboard (system clipboard error)"
            except Exception as e:  # Catch any other unexpected errors from pyperclip or underlying tools
                logging.error(f"Unexpected error copying to system clipboard: {e}", exc_info=True)
                current_status_update = "Copied to internal clipboard (unexpected system clipboard error)"

        self._set_status_message(current_status_update)

        # Return True if the status message actually changed from its original state.
        # This handles the case where the new status message might be the same as the old one
        # (e.g., if "Nothing to copy" was already the status).
        return self.status_message != original_status

    # auxiliary method
    def _clamp_scroll_and_check_change(self, original_scroll_tuple: Tuple[int, int]) -> bool:
        """
        Calls _clamp_scroll and returns True if scroll_top or scroll_left changed
        from the provided original_scroll_tuple.
        """
        old_st, old_sl = original_scroll_tuple
        self._clamp_scroll()  # This method updates self.scroll_top and self.scroll_left
        return self.scroll_top != old_st or self.scroll_left != old_sl

    # This is a helper method - a function that only reads the selection state
    # (self.is_selecting, self.selection_start, self.selection_end) and does not change any editor state.
    # Its job is to return normalized coordinates or None.
    def _get_normalized_selection_range(self) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
        """
        Helper method. Returns normalized selection coordinates (start_pos, end_pos),
        where start_pos is always logically before or at the same position as end_pos
        (i.e., start_row < end_row, or start_row == end_row and start_col <= end_col).

        Returns:
            Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
                A tuple containing two tuples: ((start_row, start_col), (end_row, end_col))
                representing the normalized selection range.
                Returns None if there is no active selection or if selection boundaries are not set.
        """
        if not self.is_selecting or self.selection_start is None or self.selection_end is None:
            logging.debug("_get_normalized_selection_range: No active or valid selection.")
            return None

        # Unpack current selection start and end points
        # These are (row, col) tuples
        sy1, sx1 = self.selection_start
        sy2, sx2 = self.selection_end

        # Normalize the coordinates: (norm_start_y, norm_start_x) should be <= (norm_end_y, norm_end_x)
        if (sy1 > sy2) or (sy1 == sy2 and sx1 > sx2):
            # If the original start is after the original end, swap them
            norm_start_y, norm_start_x = sy2, sx2
            norm_end_y, norm_end_x = sy1, sx1
            logging.debug(
                f"_get_normalized_selection_range: Swapped selection points. Original: (({sy1},{sx1}), ({sy2},{sx2})), Normalized: (({norm_start_y},{norm_start_x}), ({norm_end_y},{norm_end_x}))")
        else:
            # Original order is already normalized
            norm_start_y, norm_start_x = sy1, sx1
            norm_end_y, norm_end_x = sy2, sx2
            logging.debug(
                f"_get_normalized_selection_range: Selection points already normalized: (({norm_start_y},{norm_start_x}), ({norm_end_y},{norm_end_x}))")

        return ((norm_start_y, norm_start_x), (norm_end_y, norm_end_x))

    def delete_text_internal(self, start_row: int, start_col: int, end_row: int, end_col: int) -> None:
        """Удаляет текст в диапазоне [start_row, start_col) .. [end_row, end_col)."""
        # Нормализация
        if (start_row > end_row) or (start_row == end_row and start_col > end_col):
            start_row, start_col, end_row, end_col = end_row, end_col, start_row, start_col

        # Валидация строк
        if not (0 <= start_row < len(self.text)) or not (0 <= end_row < len(self.text)):
            logging.error("delete_text_internal: row index out of bounds (%s..%s)", start_row, end_row)
            return

        # Корректируем колонки — нельзя вылезать за пределы соответствующих строк
        start_col = max(0, min(start_col, len(self.text[start_row])))
        end_col = max(0, min(end_col, len(self.text[end_row])))

        if start_row == end_row:
            self.text[start_row] = self.text[start_row][:start_col] + self.text[start_row][end_col:]
        else:
            # Часть первой строки до start_col + часть последней строки после end_col
            new_first = self.text[start_row][:start_col] + self.text[end_row][end_col:]
            # Удаляем полностью средние строки и последнюю
            del self.text[start_row + 1:end_row + 1]
            # Обновляем первую строку
            self.text[start_row] = new_first

        self.modified = True  # Обновляем статус модификации

    def handle_smart_unindent(self) -> bool:
        """
        Handles smart unindentation (typically Shift+Tab).
        - If text is selected, unindents all lines in the selected block.
        - If no text is selected, unindents the current line.
        Returns True if any change occurred that requires a redraw, False otherwise.
        """
        if self.is_selecting:
            return self.handle_block_unindent()  # This method now returns bool
        else:
            return self.unindent_current_line()  # This method now returns bool


    @functools.lru_cache(maxsize=20000)
    def _get_tokenized_line(self, line_content: str, lexer_id: int, custom_rules_exist: bool) -> List[Tuple[str, int]]:
        """
        Tokenizes a single line of text for syntax highlighting.

        This method acts as a dispatcher:
        1. If `custom_rules_exist` is True, it uses the editor's custom regex-based
        highlighter (`apply_custom_highlighting`).
        2. Otherwise, it falls back to using the currently set Pygments lexer.

        The results are memoized using `@lru_cache` to significantly improve
        performance by avoiding re-tokenization of identical lines. The `lexer_id`
        and `custom_rules_exist` parameters are part of the cache key to ensure
        that changes to the lexer or rules invalidate the cache correctly.

        Args:
            line_content (str): The text content of the line to tokenize.
            lexer_id (int): The ID of the current Pygments lexer object.
            custom_rules_exist (bool): A flag indicating if custom syntax rules
                                    should be used instead of Pygments.

        Returns:
            A list of (substring, curses_attribute) tuples representing the
            colorized segments of the line.
        """
        # --- Dispatch to the correct highlighting method ---
        if custom_rules_exist:
            # Use custom regex-based highlighting if rules are defined for the language.
            return self.apply_custom_highlighting(line_content)

        # --- Fallback to Pygments-based highlighting ---
        if self._lexer is None:
            return [(line_content, self.colors.get("default", curses.A_NORMAL))]

        # This is a simplified color map. You can expand it or make it more dynamic.
        # It maps Pygments token types to the semantic color names from your config.
        token_color_map = {
            Token.Keyword: self.colors.get("keyword"),
            Token.Name.Function: self.colors.get("function"),
            Token.Name.Class: self.colors.get("class", self.colors.get("type")),
            Token.Name.Decorator: self.colors.get("decorator"),
            Token.Literal.String: self.colors.get("string"),
            Token.Literal.String.Doc: self.colors.get("comment"),
            Token.Literal.Number: self.colors.get("number"),
            Token.Comment: self.colors.get("comment"),
            Token.Operator: self.colors.get("operator"),
            Token.Punctuation: self.colors.get("default"),
            Token.Name.Builtin: self.colors.get("builtin"),
            Token.Name.Tag: self.colors.get("tag"),
            Token.Name.Attribute: self.colors.get("attribute"),
            Token.Error: self.colors.get("error"),
            
            Token.Keyword: curses.color_pair(2), Token.Keyword.Constant: curses.color_pair(2),
            Token.Keyword.Declaration: curses.color_pair(2), Token.Keyword.Namespace: curses.color_pair(2),
            Token.Keyword.Pseudo: curses.color_pair(2), Token.Keyword.Reserved: curses.color_pair(2),
            Token.Keyword.Type: curses.color_pair(2), Token.Name.Builtin: curses.color_pair(7),
            Token.Name.Function: curses.color_pair(3), Token.Name.Class: curses.color_pair(4),
            Token.Name.Decorator: curses.color_pair(5), Token.Name.Exception: curses.color_pair(8) | curses.A_BOLD,
            Token.Name.Variable: curses.color_pair(6), Token.Name.Attribute: curses.color_pair(6),
            Token.Name.Tag: curses.color_pair(5), Token.Literal.String: curses.color_pair(3),
            Token.Literal.String.Doc: curses.color_pair(1), Token.Literal.String.Interpol: curses.color_pair(3),
            Token.Literal.String.Escape: curses.color_pair(5), Token.Literal.String.Backtick: curses.color_pair(3),
            Token.Literal.String.Delimiter: curses.color_pair(3), Token.Literal.Number: curses.color_pair(4),
            Token.Literal.Number.Float: curses.color_pair(4), Token.Literal.Number.Hex: curses.color_pair(4),
            Token.Literal.Number.Integer: curses.color_pair(4), Token.Literal.Number.Oct: curses.color_pair(4),
            Token.Comment: curses.color_pair(1), Token.Comment.Multiline: curses.color_pair(1),
            Token.Comment.Preproc: curses.color_pair(1), Token.Comment.Special: curses.color_pair(1) | curses.A_BOLD,
            Token.Operator: curses.color_pair(6), Token.Operator.Word: curses.color_pair(2),
            Token.Punctuation: curses.color_pair(6), Token.Text: curses.color_pair(0),
            Token.Text.Whitespace: curses.color_pair(0), Token.Error: curses.color_pair(8) | curses.A_BOLD,
            Token.Generic.Heading: curses.color_pair(5) | curses.A_BOLD, Token.Generic.Subheading: curses.color_pair(5),
            Token.Generic.Deleted: curses.color_pair(8), Token.Generic.Inserted: curses.color_pair(4),
            Token.Generic.Emph: curses.color_pair(3) | curses.A_BOLD,
            Token.Generic.Strong: curses.color_pair(2) | curses.A_BOLD,
            Token.Generic.Prompt: curses.color_pair(7), Token.Generic.Output: curses.color_pair(0),
        }
        default_color = self.colors.get("default", curses.A_NORMAL)
        
        tokenized_segments = []
        try:
            raw_tokens = list(lex(line_content, self._lexer))
            if not raw_tokens and line_content:
                return [(line_content, default_color)]
            
            for token_type, text_value in raw_tokens:
                color_attr = default_color
                # Traverse up the token tree to find a matching color.
                # E.g., Token.Keyword.Constant will match Token.Keyword if not defined itself.
                current_type = token_type
                while current_type:
                    if current_type in token_color_map:
                        color_attr = token_color_map[current_type]
                        break
                    current_type = current_type.parent
                tokenized_segments.append((text_value, color_attr or default_color))

        except Exception as e:
            logging.error(f"Pygments tokenization error for line '{line_content[:70]}...': {e}")
            return [(line_content, default_color)]

        return tokenized_segments


        # # A token-to-color mapping for various token types
        # token_color_map = {
        #     Token.Keyword: curses.color_pair(2), Token.Keyword.Constant: curses.color_pair(2),
        #     Token.Keyword.Declaration: curses.color_pair(2), Token.Keyword.Namespace: curses.color_pair(2),
        #     Token.Keyword.Pseudo: curses.color_pair(2), Token.Keyword.Reserved: curses.color_pair(2),
        #     Token.Keyword.Type: curses.color_pair(2), Token.Name.Builtin: curses.color_pair(7),
        #     Token.Name.Function: curses.color_pair(3), Token.Name.Class: curses.color_pair(4),
        #     Token.Name.Decorator: curses.color_pair(5), Token.Name.Exception: curses.color_pair(8) | curses.A_BOLD,
        #     Token.Name.Variable: curses.color_pair(6), Token.Name.Attribute: curses.color_pair(6),
        #     Token.Name.Tag: curses.color_pair(5), Token.Literal.String: curses.color_pair(3),
        #     Token.Literal.String.Doc: curses.color_pair(1), Token.Literal.String.Interpol: curses.color_pair(3),
        #     Token.Literal.String.Escape: curses.color_pair(5), Token.Literal.String.Backtick: curses.color_pair(3),
        #     Token.Literal.String.Delimiter: curses.color_pair(3), Token.Literal.Number: curses.color_pair(4),
        #     Token.Literal.Number.Float: curses.color_pair(4), Token.Literal.Number.Hex: curses.color_pair(4),
        #     Token.Literal.Number.Integer: curses.color_pair(4), Token.Literal.Number.Oct: curses.color_pair(4),
        #     Token.Comment: curses.color_pair(1), Token.Comment.Multiline: curses.color_pair(1),
        #     Token.Comment.Preproc: curses.color_pair(1), Token.Comment.Special: curses.color_pair(1) | curses.A_BOLD,
        #     Token.Operator: curses.color_pair(6), Token.Operator.Word: curses.color_pair(2),
        #     Token.Punctuation: curses.color_pair(6), Token.Text: curses.color_pair(0),
        #     Token.Text.Whitespace: curses.color_pair(0), Token.Error: curses.color_pair(8) | curses.A_BOLD,
        #     Token.Generic.Heading: curses.color_pair(5) | curses.A_BOLD, Token.Generic.Subheading: curses.color_pair(5),
        #     Token.Generic.Deleted: curses.color_pair(8), Token.Generic.Inserted: curses.color_pair(4),
        #     Token.Generic.Emph: curses.color_pair(3) | curses.A_BOLD,
        #     Token.Generic.Strong: curses.color_pair(2) | curses.A_BOLD,
        #     Token.Generic.Prompt: curses.color_pair(7), Token.Generic.Output: curses.color_pair(0),
        # }


    # --- Syntax-highlighting helper ------------------------------
    def apply_syntax_highlighting_with_pygments(
            self,
            lines: list[str],
            line_indices: list[int],
    ) -> list[list[tuple[str, int]]]:
        """
        Returns a colorized representation of the requested lines.

        This method dispatches to the appropriate tokenizer for each line,
        either the custom regex-based highlighter or the Pygments lexer,
        based on whether custom rules are defined for the current language.

        Args:
            lines: A list of raw string content for each line to be highlighted.
            line_indices: The original buffer indices of the lines (unused here,
                        but maintained for API consistency).

        Returns:
            A list of lists, where each inner list contains (substring,
            curses_attribute) tuples for a single line.
        """
        # Ensure a lexer is set, even if it's just TextLexer.
        if self._lexer is None:
            self.detect_language()

        highlighted: list[list[tuple[str, int]]] = []
        lexer_id = id(self._lexer) if self._lexer else 0
        
        # Determine once if custom rules should be used for this language.
        has_custom_rules = bool(getattr(self, 'custom_syntax_patterns', []))

        if has_custom_rules:
            logging.debug("Applying custom syntax highlighting rules.")
        else:
            logging.debug(f"Applying Pygments highlighting with lexer: '{self._lexer.name if self._lexer else 'None'}'")

        for raw_line in lines:
            # Pass all three required arguments to the cached function.
            segments = self._get_tokenized_line(raw_line, lexer_id, has_custom_rules)
            highlighted.append(segments)

        return highlighted

    # --- Colour-initialisation helper -----------------------------
    def _detect_color_capabilities(self) -> tuple[bool, bool, int]:
        """
        Detects the terminal's color support capabilities.

        Returns:
            tuple[bool, bool, int]: A tuple of the form:
                - have_color (bool): Whether the terminal supports at least 8 colors.
                - use_extended (bool): Whether extended color modes (256+) are supported.
                - max_colors (int): The number of colors supported by the terminal.

        Notes:
            This function queries the terminal's color capabilities using `curses.tigetnum("colors")`
            and applies thresholds for interpreting the result:
                - Fewer than 8 colors: considered no usable color support.
                - 8–15 colors: basic ANSI palette.
                - 16–255 colors: normal extended mode.
                - 256 or more: full extended color mode is assumed.
        """
        max_colors = curses.tigetnum("colors")

        if max_colors < 8:
            return False, False, max_colors  # Not enough colors for meaningful display
        if max_colors < 16:
            return True, False, max_colors  # Basic 8-color palette
        if max_colors < 256:
            return True, False, max_colors  # 16-color mode (with bright variants)
        return True, True, max_colors  # 256-color mode or better


# New:
    def init_colors(self) -> None:
        """
        Initializes curses color pairs based on the user's config.toml.
        It reads hex color codes and finds the closest match in the xterm-256 palette.
        """
        if not curses.has_colors() or curses.COLORS < 256:
            logging.warning("Terminal does not support 256 colors. Using default attributes.")
            # Basic monochrome/8-color fallback
            self.colors = {
                "comment": curses.A_DIM, "keyword": curses.A_BOLD,
                "string": curses.A_NORMAL, "number": curses.A_NORMAL,
                "function": curses.A_BOLD, "status": curses.A_REVERSE,
                # ... add other basic fallbacks as needed ...
                "default": curses.A_NORMAL
            }
            return

        curses.start_color()
        curses.use_default_colors()
        bg = -1  # Use the terminal's default background

        # Default color map (semantic name -> default hex code)
        # This is used if a color is not defined in the user's config.
        default_color_map = {
            "comment": "#8B949E", "keyword": "#FF7B72", "string": "#A5D6FF",
            "number": "#79C0FF", "function": "#D2A8FF", "constant": "#79C0FF",
            "type": "#F2CC60", "operator": "#FF7B72", "decorator": "#D2A8FF",
            "variable": "#C9D1D9", "property": "#C9D1D9", "tag": "#7EE787",
            "attribute": "#79C0FF", "error": "#F85149", "status": "#C9D1D9",
            "selection_bg": "#264F78", "line_number": "#817248",
            "search_highlight": "#FFAB70", "default": "#C9D1D9" # Default text color
        }
        
        # User-defined colors from config.toml [colors] section
        user_colors = self.config.get("colors", {})
        
        # We will map semantic names to curses color pair IDs
        # Start pair IDs from 1 (0 is reserved for default fg/bg)
        pair_id_counter = 1
        
        for name, default_hex in default_color_map.items():
            # Prefer user's color, fall back to our default map
            hex_code = user_colors.get(name, default_hex)
            
            # We need a foreground and background for a color pair.
            # For most syntax highlighting, we only change the foreground.
            if name.endswith("_bg"): # Special case for background colors
                continue # We will handle these separately if needed

            try:
                # Convert hex to the nearest xterm-256 color index
                fg_xterm_idx = hex_to_xterm(hex_code)
                
                # Initialize the curses color pair
                curses.init_pair(pair_id_counter, fg_xterm_idx, bg)
                
                # Store the color pair attribute in our colors dictionary
                self.colors[name] = curses.color_pair(pair_id_counter)
                
                logging.debug(f"Color '{name}': Hex {hex_code} -> xterm {fg_xterm_idx} -> Pair {pair_id_counter}")
                
                pair_id_counter += 1
                if pair_id_counter >= curses.COLOR_PAIRS:
                    logging.warning("Ran out of available color pairs.")
                    break
            except Exception as e:
                logging.error(f"Failed to initialize color for '{name}' with hex '{hex_code}': {e}")
                self.colors[name] = curses.A_NORMAL # Fallback

        # Special handling for search highlight, which needs a background color
        search_bg_hex = user_colors.get("search_highlight_bg", "#553f07") # Example default
        search_fg_hex = user_colors.get("search_highlight_fg", "#FFFFFF")
        try:
            bg_idx = hex_to_xterm(search_bg_hex)
            fg_idx = hex_to_xterm(search_fg_hex)
            if pair_id_counter < curses.COLOR_PAIRS:
                curses.init_pair(pair_id_counter, fg_idx, bg_idx)
                self.colors["search_highlight"] = curses.color_pair(pair_id_counter)
                pair_id_counter += 1
        except Exception as e:
            logging.error(f"Failed to initialize search_highlight color: {e}")
            self.colors["search_highlight"] = curses.A_REVERSE # Fallback


    def detect_language(self):
        """
        Detects the file's language, sets the Pygments lexer, and loads custom
        regex highlighting patterns from the configuration if they exist.

        This method follows a clear priority:
        1.  It attempts to get a lexer based on the filename, which is the most
            reliable method.
        2.  If that fails (or no filename is present), it tries to guess the
            language from the file's content.
        3.  If both methods fail, it defaults to a plain `TextLexer`.
        
        After determining the language, it checks the configuration for a
        `[syntax_highlighting.<language>]` section and loads any custom
        regex patterns defined there.

        If the lexer or custom patterns change from the previous state, the
        tokenization cache is cleared to ensure the UI updates correctly.
        """
        old_lexer_id = id(self._lexer) if self._lexer else None
        old_custom_patterns_tuple = tuple(getattr(self, 'custom_syntax_patterns', []))
        
        new_lexer = None
        try:
            # Priority 1: Detect by filename. This is the most reliable method.
            if self.filename and self.filename != "noname":
                try:
                    # Use get_lexer_for_filename, which correctly handles extensions.
                    new_lexer = get_lexer_for_filename(self.filename, stripall=True)
                    logging.debug(f"Pygments: Detected '{new_lexer.name}' using filename '{self.filename}'.")
                except Exception:  # Pygments raises ClassNotFound, a subclass of Exception.
                    logging.debug(f"Pygments: No lexer for filename '{self.filename}'. Falling back to content guess.")
                    # Fallthrough to content guessing.

            # Priority 2: Guess from content if filename detection failed or is not applicable.
            if not new_lexer:
                content_sample = "\n".join(self.text[:200])[:10000]
                if content_sample.strip():
                    try:
                        new_lexer = guess_lexer(content_sample, stripall=True)
                        logging.debug(f"Pygments: Guessed language by content: '{new_lexer.name}'.")
                    except Exception:
                        logging.debug("Pygments: Content guess failed. Using TextLexer as fallback.")
                        new_lexer = TextLexer()
                else:
                    logging.debug("Pygments: No content to guess from. Using TextLexer.")
                    new_lexer = TextLexer()

        except Exception as e:
            logging.error(f"An unexpected error occurred during language detection: {e}", exc_info=True)
            new_lexer = TextLexer()

        # Final fallback if something went wrong and new_lexer is still None.
        if new_lexer is None:
            new_lexer = TextLexer()

        # --- Update editor state with the new lexer ---
        self._lexer = new_lexer
        self.current_language = self._lexer.name.lower()

        # --- Load custom syntax patterns from config.toml ---
        # Always clear old patterns before loading new ones.
        self.custom_syntax_patterns = [] 
        syntax_config = self.config.get("syntax_highlighting", {})
        # Use both the main name and aliases for finding rules.
        lang_keys_to_check = [self.current_language] + self._lexer.aliases
        
        for lang_key in lang_keys_to_check:
            if lang_key in syntax_config:
                patterns_from_config = syntax_config[lang_key].get("patterns", [])
                if patterns_from_config:
                    logging.info(f"Loading {len(patterns_from_config)} custom syntax rules for '{lang_key}'.")
                    for rule in patterns_from_config:
                        try:
                            # Compile regex for performance and validate the rule structure.
                            compiled_pattern = re.compile(rule["pattern"])
                            color_name = rule["color"]
                            self.custom_syntax_patterns.append((compiled_pattern, color_name))
                        except (re.error, KeyError) as e:
                            logging.warning(f"Skipping invalid syntax rule for '{lang_key}': {rule}. Error: {e}")
                    # Stop after finding the first matching language key.
                    break 

        # --- Clear Cache if Anything Changed ---
        # Compare the new state (lexer ID and patterns) with the old state.
        new_lexer_id = id(self._lexer)
        new_custom_patterns_tuple = tuple(self.custom_syntax_patterns)

        if (new_lexer_id != old_lexer_id) or (new_custom_patterns_tuple != old_custom_patterns_tuple):
            logging.info(
                f"Pygments lexer or custom syntax rules changed to '{self._lexer.name}'. Clearing tokenization cache."
            )
            if hasattr(self, '_get_tokenized_line') and hasattr(self._get_tokenized_line, 'cache_clear'):
                self._get_tokenized_line.cache_clear()
            
# new method:
    def apply_custom_highlighting(self, line: str) -> List[Tuple[str, int]]:
        """
        Applies syntax highlighting to a line using custom regex patterns from config.
        """
        # Карта для преобразования имен цветов из конфига в атрибуты curses
        color_map = self.colors

        # Создаем "карту" цветов для каждого символа в строке
        # Изначально все символы имеют цвет по умолчанию
        line_len = len(line)
        char_colors = [color_map.get("default", curses.A_NORMAL)] * line_len

        # Применяем каждое правило
        for pattern, color_name in self.custom_syntax_patterns:
            color_attr = color_map.get(color_name, color_map["default"])
            for match in pattern.finditer(line):
                start, end = match.span()
                # "Раскрашиваем" символы, попавшие в совпадение
                for i in range(start, end):
                    if i < line_len:
                        char_colors[i] = color_attr

        # Собираем строку обратно в сегменты с одинаковым цветом
        if not line:
            return [("", color_map["default"])]
        
        segments = []
        current_segment_text = line[0]
        current_segment_color = char_colors[0]
        
        for i in range(1, line_len):
            if char_colors[i] == current_segment_color:
                current_segment_text += line[i]
            else:
                segments.append((current_segment_text, current_segment_color))
                current_segment_text = line[i]
                current_segment_color = char_colors[i]
        
        segments.append((current_segment_text, current_segment_color))
        
        return segments


    def delete_char_internal(self, row: int, col: int) -> str:
        """
        Удаляет **один** символ по (row, col) без записи в history.
        Возвращает удалённый символ (или '').
        """
        if not (0 <= row < len(self.text)):
            return ""
        line = self.text[row]
        if not (0 <= col < len(line)):
            return ""
        removed = line[col]
        self.text[row] = line[:col] + line[col + 1:]
        self.modified = True
        logging.debug("delete_char_internal: removed %r at (%s,%s)", removed, row, col)
        return removed

    def handle_resize(self) -> bool:
        """
        Handles window resize events.
        Updates editor's understanding of window dimensions and visible lines,
        adjusts scroll and cursor if necessary, and signals that a redraw is needed.
        The actual redrawing is handled by the main loop via self.drawer.draw().
        """
        logging.debug("handle_resize called")
        # original_status = self.status_message # Not strictly needed if we always set a "Resized" status

        try:
            # Get current terminal dimensions directly from curses
            new_height, new_width = self.stdscr.getmaxyx()

            # Update the editor's internal state related to window size.
            # self.visible_lines is used by PageUp/PageDown and potentially other logic.
            # It should be updated here.
            self.visible_lines = max(1, new_height - 2)  # Assuming 2 lines for status/info bars

            # Update last_window_size. This is primarily used by DrawScreen.draw()
            # to detect if a full layout recalculation is needed.
            self.last_window_size = (new_height, new_width)

            # When window width changes, it's often safest to reset horizontal scroll.
            # Vertical scroll adjustment is handled by _clamp_scroll.
            self.scroll_left = 0
            logging.debug(
                f"Window resized to {new_width}x{new_height}. "
                f"Visible text lines: {self.visible_lines}. Horizontal scroll reset."
            )

            # Ensure cursor position is still valid within the text buffer
            # (though resize itself doesn't change text content).
            self._ensure_cursor_in_bounds()

            # Adjust scroll (both vertical and horizontal) to ensure
            # the cursor remains visible after resize. _clamp_scroll handles this.
            self._clamp_scroll()

            # Set a status message indicating the resize.
            self._set_status_message(f"Resized to {new_width}x{new_height}")

            # A resize always requires a full redraw.
            return True

        except Exception as e:
            # Log any error during resize handling
            logging.error(f"Error in handle_resize: {e}", exc_info=True)
            # Set an error status message
            self._set_status_message("Resize error (see log)")
            # Still signal a redraw is needed to display the error status.
            return True

    def get_display_width(self, text: str) -> int:
        """
        Return the printable width of *text* in terminal cells.

        * Uses wcwidth / wcswidth to honour full‑width CJK.
        * Treats non‑printable characters (wcwidth == ‑1) as width 0.
        """
        # Fast‑path for ASCII
        if text.isascii():
            return len(text)

        width = wcswidth(text)
        if width < 0:  # Means string contains non‑printables
            width = 0
            for ch in text:
                w = wcwidth(ch)
                width += max(w, 0)  # non‑printables add 0
        return width

    # ===================== Курсор и его методы ======================
    # I. Прямое управление позицией курсора (навигация):
    # handle_up(self)
    # handle_down(self)
    # handle_left(self)
    # handle_right(self)
    # handle_home(self)
    # handle_end(self)
    # handle_page_up(self)
    # handle_page_down(self)
    # goto_line(self)
    # _goto_match(self, match_index: int) (вспомогательный для поиска)
    # set_initial_cursor_position(self) (сброс позиции)
    #
    # ** Вспомогательные методы для курсора и прокрутки:
    # _ensure_cursor_in_bounds(self)
    # _clamp_scroll(self)

    # 1.`array up`
    def handle_up(self) -> bool:
        """
        Move cursor one line up.
        Returns True if cursor or scroll position changed, False otherwise.
        """
        old_y, old_x = self.cursor_y, self.cursor_x
        old_scroll_top = self.scroll_top
        changed = False

        if self.cursor_y > 0:
            self.cursor_y -= 1
            self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))
            # _clamp_scroll будет вызван, и он может изменить scroll_top

        self._clamp_scroll()  # Always call to ensure scroll is correct

        if old_y != self.cursor_y or old_x != self.cursor_x or old_scroll_top != self.scroll_top:
            changed = True
            logging.debug("cursor ↑ (%d,%d), scroll_top: %d", self.cursor_y, self.cursor_x, self.scroll_top)
            if self.status_message not in ["Ready", ""] and not self.status_message.lower().startswith("error"):
                msg_lower = self.status_message.lower()
                if ("inserted" in msg_lower or "deleted" in msg_lower or
                        "copied" in msg_lower or "pasted" in msg_lower or
                        "cut" in msg_lower or "undone" in msg_lower or
                        "redone" in msg_lower or "cancelled" in msg_lower or
                        "commented" in msg_lower or "uncommented" in msg_lower):
                    self._set_status_message("Ready")
        else:
            logging.debug("cursor ↑ already at top or no change (%d,%d)", self.cursor_y, self.cursor_x)
            # Clear status even if no move, if it was an action message
            if self.status_message not in ["Ready", ""] and not self.status_message.lower().startswith("error"):
                msg_lower = self.status_message.lower()
                if ("inserted" in msg_lower or "deleted" in msg_lower or
                        "copied" in msg_lower or "pasted" in msg_lower or
                        "cut" in msg_lower or "undone" in msg_lower or
                        "redone" in msg_lower or "cancelled" in msg_lower or
                        "commented" in msg_lower or "uncommented" in msg_lower):
                    self._set_status_message("Ready")
                    changed = True  # Status changed, so redraw needed

        return changed

    # 2. `array down`
    def handle_down(self) -> bool:
        """
        Move cursor one line down.
        Returns True if cursor or scroll position changed, False otherwise.
        """
        old_y, old_x = self.cursor_y, self.cursor_x
        old_scroll_top = self.scroll_top
        changed = False

        if self.cursor_y < len(self.text) - 1:
            self.cursor_y += 1
            self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))

        self._clamp_scroll()

        if old_y != self.cursor_y or old_x != self.cursor_x or old_scroll_top != self.scroll_top:
            changed = True
            logging.debug("cursor ↓ (%d,%d), scroll_top: %d", self.cursor_y, self.cursor_x, self.scroll_top)
            if self.status_message not in ["Ready", ""] and not self.status_message.lower().startswith("error"):
                msg_lower = self.status_message.lower()
                if ("inserted" in msg_lower or "deleted" in msg_lower or
                        "copied" in msg_lower or "pasted" in msg_lower or
                        "cut" in msg_lower or "undone" in msg_lower or
                        "redone" in msg_lower or "cancelled" in msg_lower or
                        "commented" in msg_lower or "uncommented" in msg_lower):
                    self._set_status_message("Ready")
        else:
            logging.debug("cursor ↓ already at bottom or no change (%d,%d)", self.cursor_y, self.cursor_x)
            if self.status_message not in ["Ready", ""] and not self.status_message.lower().startswith("error"):
                msg_lower = self.status_message.lower()
                if ("inserted" in msg_lower or "deleted" in msg_lower or
                        "copied" in msg_lower or "pasted" in msg_lower or
                        "cut" in msg_lower or "undone" in msg_lower or
                        "redone" in msg_lower or "cancelled" in msg_lower or
                        "commented" in msg_lower or "uncommented" in msg_lower):
                    self._set_status_message("Ready")
                    changed = True
        return changed

    # 3. `array left (<-) `
    def handle_left(self) -> bool:
        """
        Move cursor one position to the left.
        Returns True if cursor or scroll position changed, False otherwise.
        """
        old_y, old_x = self.cursor_y, self.cursor_x
        old_scroll_left = self.scroll_left
        old_scroll_top = self.scroll_top  # For line jumps
        changed = False

        if self.cursor_x > 0:
            self.cursor_x -= 1
        elif self.cursor_y > 0:
            self.cursor_y -= 1
            self.cursor_x = len(self.text[self.cursor_y])

        self._clamp_scroll()

        if (old_y != self.cursor_y or old_x != self.cursor_x or
                old_scroll_left != self.scroll_left or old_scroll_top != self.scroll_top):
            changed = True
            logging.debug("cursor ← (%d,%d), scroll: (%d,%d)", self.cursor_y, self.cursor_x, self.scroll_top,
                          self.scroll_left)
            if self.status_message not in ["Ready", ""] and not self.status_message.lower().startswith("error"):
                msg_lower = self.status_message.lower()
                if ("inserted" in msg_lower or "deleted" in msg_lower or
                        "copied" in msg_lower or "pasted" in msg_lower or
                        "cut" in msg_lower or "undone" in msg_lower or
                        "redone" in msg_lower or "cancelled" in msg_lower or
                        "commented" in msg_lower or "uncommented" in msg_lower):
                    self._set_status_message("Ready")
        else:
            logging.debug("cursor ← no change or at boundary (%d,%d)", self.cursor_y, self.cursor_x)
            if self.status_message not in ["Ready", ""] and not self.status_message.lower().startswith("error"):
                msg_lower = self.status_message.lower()
                if ("inserted" in msg_lower or "deleted" in msg_lower or
                        "copied" in msg_lower or "pasted" in msg_lower or
                        "cut" in msg_lower or "undone" in msg_lower or
                        "redone" in msg_lower or "cancelled" in msg_lower or
                        "commented" in msg_lower or "uncommented" in msg_lower):
                    self._set_status_message("Ready")
                    changed = True
        return changed

    # 4. `array right (->)`
    def handle_right(self) -> bool:
        """
        Move cursor one position to the right.
        Returns True if cursor or scroll position changed, False otherwise.
        """
        old_y, old_x = self.cursor_y, self.cursor_x
        old_scroll_left = self.scroll_left
        old_scroll_top = self.scroll_top  # For line jumps
        changed = False

        try:
            line_len = len(self.text[self.cursor_y])
            if self.cursor_x < line_len:
                self.cursor_x += 1
                while self.cursor_x < line_len and wcswidth(self.text[self.cursor_y][self.cursor_x]) == 0:
                    self.cursor_x += 1
            elif self.cursor_y < len(self.text) - 1:
                self.cursor_y += 1
                self.cursor_x = 0

            self._clamp_scroll()

            if (old_y != self.cursor_y or old_x != self.cursor_x or
                    old_scroll_left != self.scroll_left or old_scroll_top != self.scroll_top):
                changed = True
                logging.debug("cursor → (%d,%d), scroll: (%d,%d)", self.cursor_y, self.cursor_x, self.scroll_top,
                              self.scroll_left)
                if self.status_message not in ["Ready", ""] and not self.status_message.lower().startswith("error"):
                    msg_lower = self.status_message.lower()
                    if ("inserted" in msg_lower or "deleted" in msg_lower or
                            "copied" in msg_lower or "pasted" in msg_lower or
                            "cut" in msg_lower or "undone" in msg_lower or
                            "redone" in msg_lower or "cancelled" in msg_lower or
                            "commented" in msg_lower or "uncommented" in msg_lower):
                        self._set_status_message("Ready")
            else:
                logging.debug("cursor → no change or at boundary (%d,%d)", self.cursor_y, self.cursor_x)
                if self.status_message not in ["Ready", ""] and not self.status_message.lower().startswith("error"):
                    msg_lower = self.status_message.lower()
                    if ("inserted" in msg_lower or "deleted" in msg_lower or
                            "copied" in msg_lower or "pasted" in msg_lower or
                            "cut" in msg_lower or "undone" in msg_lower or
                            "redone" in msg_lower or "cancelled" in msg_lower or
                            "commented" in msg_lower or "uncommented" in msg_lower):
                        self._set_status_message("Ready")
                        changed = True
            return changed
        except IndexError:  # Should not happen if cursor_y is always valid
            logging.exception("Error in handle_right (IndexError)")
            self._set_status_message("Cursor error (see log)")
            return True  # Assume redraw needed
        except Exception:
            logging.exception("Error in handle_right")
            self._set_status_message("Cursor error (see log)")
            return True  # Assume redraw needed

    # 5. key HOME
    def handle_home(self) -> bool:
        """
        Moves the cursor to the beginning of the current line.
        Implements "smart home" behavior:
        - First press: moves to the first non-whitespace character (or column 0 if no indent).
        - Second press (if already at indent): moves to absolute column 0.
        Returns True if the cursor or scroll position changed, False otherwise.
        """
        original_cursor_x = self.cursor_x
        original_scroll_left = self.scroll_left  # To check if _clamp_scroll changes it
        changed_state = False

        with self._state_lock:
            # Ensure cursor_y is valid, though it shouldn't change here
            if self.cursor_y >= len(self.text):
                logging.warning(f"handle_home: cursor_y {self.cursor_y} out of bounds.")
                return False  # No change possible

            current_line_content = self.text[self.cursor_y]

            # Find the end of the leading whitespace (indentation)
            match = re.match(r"^(\s*)", current_line_content)
            indentation_end_column = match.end() if match else 0

            if self.cursor_x != indentation_end_column:
                # Cursor is not at the indentation point yet, move to it.
                self.cursor_x = indentation_end_column
            else:
                # Cursor is already at the indentation point (or col 0 if no indent),
                # so move to the absolute beginning of the line (column 0).
                self.cursor_x = 0

            # After cursor_x is set, adjust horizontal scroll if needed.
            self._clamp_scroll()

        # Determine if any relevant state actually changed.
        if (self.cursor_x != original_cursor_x or
                self.scroll_left != original_scroll_left):
            changed_state = True
            logging.debug(
                f"handle_home: New cursor_x: {self.cursor_x}, scroll_left: {self.scroll_left}. Changed: {changed_state}"
            )
        else:
            logging.debug("handle_home: No change in cursor_x or scroll_left.")

        # Clear transient status messages if a move occurred or was attempted
        if self.status_message not in ["Ready", ""] and not self.status_message.lower().startswith("error"):
            msg_lower = self.status_message.lower()
            if ("inserted" in msg_lower or "deleted" in msg_lower or
                    "copied" in msg_lower or "pasted" in msg_lower or
                    "cut" in msg_lower or "undone" in msg_lower or
                    "redone" in msg_lower or "cancelled" in msg_lower or
                    "commented" in msg_lower or "uncommented" in msg_lower):
                self._set_status_message("Ready")
                changed_state = True  # Status message change also implies redraw

        return changed_state

    # 6. key END
    def handle_end(self) -> bool:
        """
        Moves the cursor to the end of the current line.
        Returns True if the cursor or scroll position changed, False otherwise.
        """
        original_cursor_x = self.cursor_x
        original_scroll_left = self.scroll_left  # To check if _clamp_scroll changes it
        changed_state = False

        with self._state_lock:
            if self.cursor_y >= len(self.text):
                logging.warning(f"handle_end: cursor_y {self.cursor_y} out of bounds.")
                return False  # No change possible

            self.cursor_x = len(self.text[self.cursor_y])
            # After cursor_x is set, adjust horizontal scroll if needed.
            self._clamp_scroll()

        # Determine if any relevant state actually changed.
        if (self.cursor_x != original_cursor_x or
                self.scroll_left != original_scroll_left):
            changed_state = True
            logging.debug(
                f"handle_end: New cursor_x: {self.cursor_x}, scroll_left: {self.scroll_left}. Changed: {changed_state}"
            )
        else:
            logging.debug("handle_end: No change in cursor_x or scroll_left.")

        # Clear transient status messages if a move occurred or was attempted
        if self.status_message not in ["Ready", ""] and not self.status_message.lower().startswith("error"):
            msg_lower = self.status_message.lower()
            if ("inserted" in msg_lower or "deleted" in msg_lower or
                    "copied" in msg_lower or "pasted" in msg_lower or
                    "cut" in msg_lower or "undone" in msg_lower or
                    "redone" in msg_lower or "cancelled" in msg_lower or
                    "commented" in msg_lower or "uncommented" in msg_lower):
                self._set_status_message("Ready")
                # If only status changed, and cursor/scroll didn't, ensure changed_state reflects this.
                if not changed_state:
                    changed_state = True

        return changed_state

    # 7. key Page-Up
    def handle_page_up(self) -> bool:
        """Moves the cursor and view up by approximately one screen height.

        This method scrolls the view upwards by the number of currently visible
        text lines (`self.visible_lines`). The cursor's vertical position is
        adjusted accordingly, and its horizontal position (column) is preserved
        if possible, clamped by the length of the new line. If the cursor moves
        or the scroll position changes, this method returns True.

        It also clears transient status messages (e.g., "Text inserted") if
        a movement occurs, resetting the status to "Ready" unless it's an
        error message.

        Args:
            None

        Returns:
            bool: True if the cursor position, scroll position, or status message
                  changed, indicating a redraw might be needed. False otherwise.
        """
        # Store initial state for comparison
        original_cursor_pos = (self.cursor_y, self.cursor_x)
        original_scroll_pos = (self.scroll_top, self.scroll_left)
        original_status = self.status_message
        changed_state = False

        with self._state_lock:
            if self.visible_lines <= 0:
                logging.warning("handle_page_up: visible_lines is not positive, cannot page.")
                return False

            page_height = self.visible_lines  # Number of text lines visible on screen

            # Move cursor by one page height upwards.
            new_cursor_y_candidate = max(0, self.cursor_y - page_height)
            self.cursor_y = new_cursor_y_candidate

            # Ensure cursor_x is valid for the new line, maintaining the desired column.
            if self.cursor_y < len(self.text):
                self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))
            else:  # Should not happen if self.text always has at least [""]
                self.cursor_x = 0

            # _clamp_scroll will adjust scroll_top and scroll_left to ensure
            # the new cursor_y and cursor_x are visible.
            self._clamp_scroll()

        # Determine if any relevant state actually changed.
        if ((self.cursor_y, self.cursor_x) != original_cursor_pos or
                (self.scroll_top, self.scroll_left) != original_scroll_pos):
            changed_state = True
            logging.debug(
                f"handle_page_up: New cursor ({self.cursor_y},{self.cursor_x}), "
                f"scroll ({self.scroll_top},{self.scroll_left}). Changed: {changed_state}"
            )
        else:
            logging.debug("handle_page_up: No change in cursor or scroll state.")

        # Clear transient status messages if a move occurred.
        # (Remaining logic for status message clearing stays the same)
        if changed_state and self.status_message != original_status:
            if self.status_message not in ["Ready", ""] and not self.status_message.lower().startswith("error"):
                msg_lower = self.status_message.lower()
                # Check if the current status message is one of the transient action messages
                transient_action_keywords = [
                    "inserted", "deleted", "copied", "pasted",
                    "cut", "undone", "redone", "cancelled",
                    "commented", "uncommented"
                ]
                if any(keyword in msg_lower for keyword in transient_action_keywords):
                    self._set_status_message("Ready")
                    # If status changed back to Ready, it's still a change from original_status
                    # if original_status wasn't Ready.
                    if self.status_message != original_status and not changed_state:
                        # This part of 'if' ensures changed_state is True if only status changed.
                        # However, changed_state is already True if we are in this block.
                        # The important part is that self._set_status_message("Ready") might have occurred.
                        pass  # Redraw will be triggered by changed_state = True or status changing

        # The method returns True if 'changed_state' is True (cursor/scroll moved)
        # OR if the status message itself is different from what it was at the start.
        return changed_state or (self.status_message != original_status)

    # 8. key Page-Down
    def handle_page_down(self) -> bool:
        """
        Moves the cursor and view down by approximately one screen height of text.
        The cursor attempts to maintain its horizontal column, clamped by line length.
        Returns True if the cursor or scroll position changed, False otherwise.
        """
        # Store initial state for comparison
        original_cursor_pos = (self.cursor_y, self.cursor_x)
        original_scroll_pos = (self.scroll_top, self.scroll_left)
        original_status = self.status_message
        changed_state = False

        with self._state_lock:
            if self.visible_lines <= 0:
                logging.warning("handle_page_down: visible_lines is not positive, cannot page.")
                return False

            page_height = self.visible_lines
            max_y_idx = len(self.text) - 1
            if max_y_idx < 0:
                max_y_idx = 0  # Handle empty text [""] case

            # Calculate new cursor_y candidate
            new_cursor_y_candidate = min(max_y_idx, self.cursor_y + page_height)

            if new_cursor_y_candidate != self.cursor_y:
                self.cursor_y = new_cursor_y_candidate

            # Ensure cursor_x is valid for the new line
            if self.cursor_y < len(self.text):
                self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))
            else:
                self.cursor_x = 0

            # _clamp_scroll will ensure cursor_y is visible and adjust scroll_top and scroll_left
            self._clamp_scroll()

        # Determine if any relevant state actually changed.
        if ((self.cursor_y, self.cursor_x) != original_cursor_pos or
                (self.scroll_top, self.scroll_left) != original_scroll_pos):
            changed_state = True
            logging.debug(
                f"handle_page_down: New cursor ({self.cursor_y},{self.cursor_x}), "
                f"scroll ({self.scroll_top},{self.scroll_left}). Changed: {changed_state}"
            )
        else:
            logging.debug("handle_page_down: No change in cursor or scroll state.")

        # Clear transient status messages if a move occurred
        if changed_state and self.status_message != original_status:
            if self.status_message not in ["Ready", ""] and not self.status_message.lower().startswith("error"):
                msg_lower = self.status_message.lower()
                if ("inserted" in msg_lower or "deleted" in msg_lower or
                        "copied" in msg_lower or "pasted" in msg_lower or
                        "cut" in msg_lower or "undone" in msg_lower or
                        "redone" in msg_lower or "cancelled" in msg_lower or
                        "commented" in msg_lower or "uncommented" in msg_lower):
                    self._set_status_message("Ready")
                    if self.status_message != original_status and not changed_state:
                        changed_state = True

        return changed_state

    # 9. -- GOTO LINE ------------------------------
    def goto_line(self) -> bool:
        """
        Moves the cursor to a specified line number. Supports absolute numbers,
        relative numbers (+N, -N), and percentages (N%).
        Returns True if the cursor, scroll position, or status message changed,
        False otherwise (e.g., invalid input but status didn't change from original).
        """
        original_status = self.status_message
        original_cursor_pos = (self.cursor_y, self.cursor_x)
        original_scroll_top = self.scroll_top

        # The prompt itself will temporarily change the status bar.
        # We need to capture the status *after* the prompt to see if the prompt interaction
        # itself should be considered the "final" status change for this operation if parsing fails.
        prompt_text = f"Go to line (1-{len(self.text)}, ±N, %): "
        raw_input_str = self.prompt(prompt_text)

        status_after_prompt = self.status_message  # Status might have been restored by prompt's finally block

        if not raw_input_str:  # User cancelled the prompt (e.g., pressed Esc or Enter on empty input)
            # If the prompt itself set a new status (e.g. "Prompt timeout"), that's a change.
            # If prompt restored original_status, but user cancelled, set "Goto cancelled".
            if status_after_prompt == original_status:
                self._set_status_message("Goto cancelled")
            # Return True if status message is different from what it was *before* the prompt.
            return self.status_message != original_status

        target_line_num_one_based: Optional[int] = None
        total_lines = len(self.text)
        if total_lines == 0:  # Should not happen if self.text always has at least [""]
            self._set_status_message("Cannot go to line: buffer is empty")
            return self.status_message != original_status

        try:
            if raw_input_str.endswith('%'):
                percentage_str = raw_input_str.rstrip('%')
                if not percentage_str:  # Just '%' was entered
                    raise ValueError("Percentage value missing.")
                percentage = float(percentage_str)
                if not (0 <= percentage <= 100):
                    self._set_status_message("Percentage out of range (0-100)")
                    return True  # Status changed
                # Calculate target line (1-based), ensuring it's within [1, total_lines]
                # round() handles .5 by rounding to the nearest even number in Python 3.
                # int(val + 0.5) is a common way to round half up for positive numbers.
                # For percentages, simple rounding is usually fine.
                target_line_num_one_based = max(1, min(total_lines, round(total_lines * percentage / 100.0)))
                if target_line_num_one_based == 0 and total_lines > 0:
                    target_line_num_one_based = 1  # Ensure at least line 1
                logging.debug(f"Goto: Percentage {percentage}%, target line {target_line_num_one_based}")
            elif raw_input_str.startswith(('+', '-')):
                if len(raw_input_str) == 1:  # Just '+' or '-' was entered
                    raise ValueError("Relative offset value missing.")
                relative_offset = int(raw_input_str)
                # Current line is 0-based (self.cursor_y), target is 1-based
                target_line_num_one_based = (self.cursor_y + 1) + relative_offset
                logging.debug(
                    f"Goto: Relative offset {relative_offset}, from line {self.cursor_y + 1}, target line {target_line_num_one_based}")
            else:
                target_line_num_one_based = int(raw_input_str)
                logging.debug(f"Goto: Absolute target line {target_line_num_one_based}")

            # Validate the calculated target_line_num_one_based
            if target_line_num_one_based is None:  # Should not happen if parsing logic is complete
                raise ValueError("Line number could not be determined.")
            if not (1 <= target_line_num_one_based <= total_lines):
                self._set_status_message(f"Line number out of range (1–{total_lines})")
                return True  # Status changed

            # Convert 1-based target to 0-based for internal use
            target_y_zero_based = target_line_num_one_based - 1

            # Only proceed if the target is different from the current line
            if target_y_zero_based == self.cursor_y and self.cursor_x == min(self.cursor_x,
                                                                             len(self.text[target_y_zero_based])):
                # If already on the target line and x is valid for it (or will be clamped to valid)
                # No actual cursor line change, but check if x needs clamping or status needs update.
                self.cursor_x = min(self.cursor_x, len(self.text[target_y_zero_based]))  # Ensure x is valid
                self._clamp_scroll()  # Ensure scroll is correct for current position
                if (self.cursor_y, self.cursor_x) != original_cursor_pos or self.scroll_top != original_scroll_top:
                    self._set_status_message(f"Moved to line {target_line_num_one_based}, column adjusted")
                    return True
                else:
                    self._set_status_message(f"Already at line {target_line_num_one_based}")
                    return self.status_message != original_status

            # Move cursor
            self.cursor_y = target_y_zero_based
            # Try to maintain horizontal cursor position, clamped to new line length
            self.cursor_x = min(original_cursor_pos[1], len(self.text[self.cursor_y]))

            self._clamp_scroll()  # Adjust scroll to make the new cursor position visible

            # Check if cursor or scroll actually changed
            if (self.cursor_y, self.cursor_x) != original_cursor_pos or \
                    self.scroll_top != original_scroll_top:
                self._set_status_message(f"Moved to line {target_line_num_one_based}")
                return True
            else:
                # This case should be rare if logic above for "already at line" is correct.
                # It means target was same as current, and clamp_scroll did nothing.
                # However, the prompt was shown.
                if status_after_prompt != original_status:  # If prompt itself set a lasting status
                    return True
                    # If prompt restored status, but we set a new one (e.g. "already at line")
                self._set_status_message(f"At line {target_line_num_one_based} (no change)")
                return self.status_message != original_status

        except ValueError as ve:  # Handles errors from int(), float(), or custom raises
            logging.warning(f"Goto: Invalid input format '{raw_input_str}': {ve}")
            self._set_status_message(f"Invalid format: {raw_input_str[:30]}")
            return True  # Status changed due to error message
        except Exception as e:  # Catch any other unexpected errors
            logging.error(f"Unexpected error in goto_line for input '{raw_input_str}': {e}", exc_info=True)
            self._set_status_message(f"Goto error: {str(e)[:60]}...")
            return True  # Status changed due to error message

    # 10. вспомогательный для поиска
    def _goto_match(self, match_index: int) -> None:  # Added type hint and English docstring
        """
        Moves the cursor and adjusts the scroll view to the search match
        specified by `match_index`.

        This method assumes `self.search_matches` is populated and `match_index` is valid.
        It updates `self.cursor_y`, `self.cursor_x`, `self.scroll_top`, and `self.scroll_left`
        as necessary to make the match visible, ideally near the center of the screen.

        Args:
            match_index (int): The index of the desired match in `self.search_matches`.
        """
        # 1. Validate the match_index and ensure search_matches is populated.
        if not self.search_matches or not (0 <= match_index < len(self.search_matches)):
            logging.warning(
                f"_goto_match called with invalid index {match_index} for "
                f"{len(self.search_matches)} available matches. No action taken."
            )
            return  # Invalid index or no matches to go to

        # 2. Get the coordinates of the target match.
        # search_matches stores tuples: (row_index, column_start_index, column_end_index)
        target_row, target_col_start, _ = self.search_matches[match_index]  # We only need start for cursor

        logging.debug(
            f"_goto_match: Navigating to match {match_index + 1}/{len(self.search_matches)} "
            f"at (row:{target_row}, col:{target_col_start})."
        )

        # 3. Move the logical cursor to the start of the match.
        # Ensure target_row and target_col_start are valid within the current text buffer.
        # This is a safeguard; _collect_matches should provide valid indices.
        if target_row >= len(self.text):
            logging.error(f"_goto_match: Match row {target_row} is out of bounds for text length {len(self.text)}.")
            return
        if target_col_start > len(self.text[target_row]):  # Allow being at the end of the line
            logging.error(
                f"_goto_match: Match col_start {target_col_start} is out of bounds for line {target_row} (len {len(self.text[target_row])}).")
            # Optionally clamp target_col_start or return
            # target_col_start = len(self.text[target_row]) # Clamp to end of line
            return

        self.cursor_y = target_row
        self.cursor_x = target_col_start

        # 4. Adjust scroll to ensure the new cursor position is visible.
        # self._clamp_scroll() handles both vertical and horizontal scrolling
        # to bring self.cursor_y and self.cursor_x into view.
        # It also attempts to center the cursor line if it's far off-screen.

        # The previous logic for adjusting scroll_top was a simplified centering.
        # _clamp_scroll provides a more comprehensive adjustment.
        # Let's review _clamp_scroll's behavior for centering.
        # Current _clamp_scroll:
        #   if self.cursor_y < self.scroll_top: self.scroll_top = self.cursor_y
        #   elif self.cursor_y >= self.scroll_top + text_height: self.scroll_top = self.cursor_y - text_height + 1
        # This ensures visibility but doesn't explicitly center.
        # To achieve better centering for _goto_match, we can pre-adjust scroll_top here.

        if self.visible_lines > 0:  # visible_lines should be height - 2 (status/number bars)
            text_area_height = self.visible_lines

            # Desired scroll_top to center the cursor_y, or bring it into view if near edges.
            # Try to place the target line roughly in the middle third of the screen.
            desired_scroll_top = self.cursor_y - (text_area_height // 3)

            # Clamp desired_scroll_top to valid range: [0, max_scroll_possible]
            max_scroll_possible = max(0, len(self.text) - text_area_height)
            self.scroll_top = max(0, min(desired_scroll_top, max_scroll_possible))

            logging.debug(f"_goto_match: Tentative scroll_top set to {self.scroll_top} to center line {self.cursor_y}.")

        # Now, call _clamp_scroll() to finalize both vertical and horizontal scroll based on
        # the new cursor_y, cursor_x, and the tentative scroll_top.
        # _clamp_scroll will also handle horizontal scrolling for cursor_x.
        self._clamp_scroll()

        logging.debug(
            f"_goto_match: Final state after _clamp_scroll: "
            f"Cursor=({self.cursor_y},{self.cursor_x}), Scroll=({self.scroll_top},{self.scroll_left})"
        )
        # This method modifies editor state (cursor, scroll) but does not directly
        # return a bool for redraw; the caller (find_prompt or find_next) handles that.

    # 11. сброс позиции
    def set_initial_cursor_position(self):
        """Sets the initial cursor position and scrolling offsets."""
        self.cursor_x = 0
        self.cursor_y = 0
        self.scroll_top = 0
        self.scroll_left = 0
        # При сбросе позиции курсора, сбрасываем и выделение
        self.is_selecting = False
        self.selection_start = None
        self.selection_end = None
        self.highlighted_matches = []  # Сбрасываем подсветку поиска
        self.search_matches = []
        self.search_term = ""
        self.current_match_idx = -1

    # Вспомогательные методы для курсора и прокрутки:
    # 12. ── Курсор: прокрутка и ограничение ────────────────────────────────
    def _clamp_scroll(self) -> None:
        """
        Гарантирует, что scroll_top и scroll_left
        всегда удерживают курсор в видимой области.
        """
        # размеры окна
        height, width = self.stdscr.getmaxyx()
        # область текста по вертикали (высота окна минус строки номера и статус-бара)
        text_height = max(1, height - 2)

        # Вертикальная прокрутка
        if self.cursor_y < self.scroll_top:
            self.scroll_top = self.cursor_y
        elif self.cursor_y >= self.scroll_top + text_height:
            self.scroll_top = self.cursor_y - text_height + 1

        # Горизонтальная прокрутка — считаем дисплейную ширину до курсора
        disp_x = self.get_display_width(self.text[self.cursor_y][: self.cursor_x])
        if disp_x < self.scroll_left:
            self.scroll_left = disp_x
        elif disp_x >= self.scroll_left + width:
            self.scroll_left = disp_x - width + 1

        # Гарантируем неотрицательные значения
        self.scroll_top = max(0, self.scroll_top)
        self.scroll_left = max(0, self.scroll_left)

    # 13. Вспомогательный метод
    def _ensure_cursor_in_bounds(self) -> None:
        """
        Clamp `cursor_x` / `cursor_y` so they always reference a valid position
        inside `self.text`.

        • Если буфер пуст → создаётся пустая строка `[""]`, и курсор ставится в (0,0).
        • `cursor_y` ограничивается диапазоном [0 … len(text)-1].
        • `cursor_x` ограничивается диапазоном [0 … len(current_line)].
        • После коррекции выводится отладочный лог.
        """
        # Пустой буфер – гарантируем хотя бы одну строку
        if not self.text:
            self.text.append("")

        max_y = len(self.text) - 1
        self.cursor_y = max(0, min(self.cursor_y, max_y))

        max_x = len(self.text[self.cursor_y])
        self.cursor_x = max(0, min(self.cursor_x, max_x))

        logging.debug("Cursor clamped → (%d,%d) [line_len=%d]",
                      self.cursor_y, self.cursor_x, max_x)

    # II. Изменение текста, влияющее на курсор:
    # handle_backspace(self)
    # handle_delete(self)
    # handle_tab(self) (через insert_text)
    # handle_smart_tab(self) (через insert_text или handle_block_indent)
    # handle_enter(self) (через insert_text)
    # insert_text(self, text: str) (основной метод вставки)
    # insert_text_at_position(self, text: str, row: int, col: int) (низкоуровневая вставка)
    # delete_selected_text_internal(self, start_y: int, start_x: int, end_y: int, end_x: int) (низкоуровневое удаление)
    # paste(self) (включает удаление выделения и вставку)
    # cut(self) (включает удаление выделения)
    # search_and_replace(self) (сбрасывает курсор)
    # undo(self) / redo(self) (восстанавливают позицию курсора)

    # ── Курсор: Backspace и Delete ────────────────────────────────────────
    # 1. key Backspace
    def handle_backspace(self) -> bool:
        """
        Handles the Backspace key.
        - If text is selected, deletes the selection.
        - Otherwise, if cursor is not at column 0, deletes character to the left.
        - Otherwise, if cursor is at column 0 and not the first line, merges with previous line.
        Returns True if any change to text, selection, cursor, scroll, or status occurred, False otherwise.
        """
        """
        Логика Backspace с полной поддержкой блочного выделения.

        - Если есть выделение – удалить *весь* диапазон (как Del).
        - Иначе:
            – курсор не в 0-й колонке → удалить символ слева;
            – курсор в 0-й колонке и строка не первая → склеить текущую с предыдущей.

        Все изменения заносятся в `action_history`, стек redo (`undone_actions`)
        очищается. После операции курсор и прокрутка гарантированно валидны.
        """
        logging.debug("handle_backspace triggered!")
        with self._state_lock:
            # Store initial state for comparison to determine if a redraw is needed
            original_cursor_pos = (self.cursor_y, self.cursor_x)
            original_scroll_pos = (self.scroll_top, self.scroll_left)
            original_selection_state = (self.is_selecting, self.selection_start, self.selection_end)
            original_modified_flag = self.modified
            original_status_message = self.status_message

            action_made_change_to_content = False  # Tracks if text/buffer structure changed

            if self.is_selecting:
                normalized_range = self._get_normalized_selection_range()
                if not normalized_range:
                    # This case implies an inconsistent selection state.
                    logging.warning("handle_backspace: is_selecting=True, but no valid normalized range.")
                    self.is_selecting = False  # Attempt to recover by clearing selection
                    self.selection_start = None
                    self.selection_end = None
                    self._set_status_message("Selection error cleared")
                    return True  # Status changed, redraw

                norm_start_coords, norm_end_coords = normalized_range

                # delete_selected_text_internal sets self.modified and cursor position
                deleted_segments = self.delete_selected_text_internal(
                    norm_start_coords[0], norm_start_coords[1],
                    norm_end_coords[0], norm_end_coords[1]
                )

                # Check if anything was actually deleted or if selection range was non-empty
                if deleted_segments or (norm_start_coords != norm_end_coords):
                    self.history.add_action({  
                        "type": "delete_selection",
                        "text": deleted_segments,
                        "start": norm_start_coords,
                        "end": norm_end_coords
                    })
                    action_made_change_to_content = True
                    self._set_status_message("Selection deleted")
                else:  # Selection was empty (e.g. just a cursor point)
                    self._set_status_message("Empty selection, nothing deleted")

                self.is_selecting = False  # Always clear selection after processing
                self.selection_start = None
                self.selection_end = None
                # self.modified is handled by delete_selected_text_internal

            elif self.cursor_x > 0:  # Cursor is not at the beginning of the line
                y, x = self.cursor_y, self.cursor_x

                if y >= len(self.text):  # Should not happen with a valid cursor
                    logging.error(f"handle_backspace: cursor_y {y} out of bounds for text length {len(self.text)}")
                    return False  # No change, inconsistent state

                current_line_content = self.text[y]
                deleted_char = current_line_content[x - 1]
                self.text[y] = current_line_content[:x - 1] + current_line_content[x:]
                self.cursor_x -= 1
                self.modified = True
                action_made_change_to_content = True

                self.history.add_action({
                    "type": "delete_char",
                    "text": deleted_char,
                    "position": (y, self.cursor_x),  # Position *after* deletion (where char was)
                })
                self._set_status_message("Character deleted")
                logging.debug(f"handle_backspace: Character '{deleted_char}' at original ({y},{x}) deleted.")

            elif self.cursor_y > 0:  # Cursor is at column 0, but not on the first line
                current_row_idx = self.cursor_y
                prev_row_idx = current_row_idx - 1

                text_moved_up = self.text[current_row_idx]
                new_cursor_x_pos = len(self.text[prev_row_idx])

                self.text[prev_row_idx] += text_moved_up
                del self.text[current_row_idx]

                self.cursor_y = prev_row_idx
                self.cursor_x = new_cursor_x_pos
                self.modified = True
                action_made_change_to_content = True

                self.history.add_action({
                    "type": "delete_newline",
                    "text": text_moved_up,
                    "position": (self.cursor_y, self.cursor_x),  # Cursor position after merge
                })
                self._set_status_message("Newline deleted (lines merged)")
                logging.debug(f"handle_backspace: Line {current_row_idx} merged into {prev_row_idx}.")
            else:
                # Cursor is at (0,0) - beginning of the file
                logging.debug("handle_backspace: At beginning of file – no action.")
                self._set_status_message("Beginning of file")
                # No content change, but status message might have changed.
                return self.status_message != original_status_message

            # Finalization steps if any content change occurred
            if action_made_change_to_content:
                self._ensure_cursor_in_bounds()  # Ensure cursor is valid after modification
                self._clamp_scroll()  # Adjust scroll if cursor moved out of view

            # Determine if a redraw is needed
            if (action_made_change_to_content or
                    self.cursor_y != original_cursor_pos[0] or self.cursor_x != original_cursor_pos[1] or
                    self.scroll_top != original_scroll_pos[0] or self.scroll_left != original_scroll_pos[1] or
                    self.is_selecting != original_selection_state[0] or  # Selection state changed
                    self.modified != original_modified_flag or  # Modified flag changed
                    self.status_message != original_status_message):  # Status message changed
                return True

            return False  # No perceivable change that warrants a redraw

    # 2. key Delete ----------------
    def handle_delete(self) -> bool:
        """
        Deletes the character under the cursor or the selected text.
        - If text is selected, deletes the selection.
        - Otherwise, if cursor is not at the end of the line, deletes character under cursor.
        - Otherwise, if cursor is at the end of a line (but not the last line), merges with the next line.
        Returns True if any change to text, selection, cursor, scroll, or status occurred, False otherwise.
        """
        with self._state_lock:
            # Store initial state for comparison to determine if a redraw is needed
            original_cursor_pos = (self.cursor_y, self.cursor_x)
            original_scroll_pos = (self.scroll_top, self.scroll_left)
            original_selection_state = (self.is_selecting, self.selection_start, self.selection_end)
            original_modified_flag = self.modified
            original_status_message = self.status_message

            action_made_change_to_content = False  # Tracks if text/buffer structure changed

            if self.is_selecting:
                normalized_range = self._get_normalized_selection_range()
                if not normalized_range:
                    logging.warning("handle_delete: is_selecting=True, but no valid normalized range.")
                    self.is_selecting = False  # Attempt to recover
                    self.selection_start = None
                    self.selection_end = None
                    self._set_status_message("Selection error cleared")
                    return True  # Status changed

                norm_start_coords, norm_end_coords = normalized_range

                # delete_selected_text_internal sets self.modified and cursor position
                deleted_segments = self.delete_selected_text_internal(
                    norm_start_coords[0], norm_start_coords[1],
                    norm_end_coords[0], norm_end_coords[1]
                )

                if deleted_segments or (norm_start_coords != norm_end_coords):  # Check if deletion occurred
                    self.history.add_action({ 
                        "type": "delete_selection",
                        "text": deleted_segments,
                        "start": norm_start_coords,
                        "end": norm_end_coords
                    })
                    logging.debug(f"Action history now has {len(self._action_history)} items. Last was: {self._action_history[-1]['type']}")
                    action_made_change_to_content = True
                    self._set_status_message("Selection deleted")
                else:
                    self._set_status_message("Empty selection, nothing deleted")

                self.is_selecting = False  # Always clear selection after processing
                self.selection_start = None
                self.selection_end = None
                # self.modified is handled by delete_selected_text_internal

            else:  # No selection, handle single character delete or newline merge
                y, x = self.cursor_y, self.cursor_x

                if y >= len(self.text):  # Cursor out of bounds
                    logging.error(f"handle_delete: cursor_y {y} out of bounds for text length {len(self.text)}")
                    return False  # No change, inconsistent state

                current_line_len = len(self.text[y])

                if x < current_line_len:
                    # Delete character under cursor (to the right)
                    deleted_char = self.text[y][x]
                    self.text[y] = self.text[y][:x] + self.text[y][x + 1:]
                    # Cursor position (x) does not change when deleting char at cursor
                    self.modified = True
                    action_made_change_to_content = True

                    self.history.add_action({
                        "type": "delete_char",
                        "text": deleted_char,
                        "position": (y, x)  # Position of the deleted character
                    })
                    self._set_status_message("Character deleted")
                    logging.debug(f"handle_delete: Character '{deleted_char}' at ({y},{x}) deleted.")

                elif y < len(self.text) - 1:  # Cursor is at the end of a line, but not the last line
                    # Merge with the next line (delete newline character)
                    next_line_content = self.text[y + 1]

                    # Cursor position (y,x) remains the same logically after merge
                    # Position for history is where the newline was (end of current line y)
                    pos_for_history = (y, current_line_len)

                    self.text[y] += self.text.pop(y + 1)
                    self.modified = True
                    action_made_change_to_content = True

                    self.history.add_action({
                        "type": "delete_newline",
                        "text": next_line_content,  # Content of the line that was merged up
                        "position": pos_for_history
                    })
                    self._set_status_message("Newline deleted (lines merged)")
                    logging.debug(f"handle_delete: Line {y + 1} merged into line {y}.")
                else:
                    # Cursor is at the end of the last line of the file
                    logging.debug("handle_delete: At end of file – no action.")
                    self._set_status_message("End of file")
                    return self.status_message != original_status_message  # Only redraw if status changed

            # Finalization steps if any content change occurred
            if action_made_change_to_content:
                self._ensure_cursor_in_bounds()
                self._clamp_scroll()

            # Determine if a redraw is needed
            if (action_made_change_to_content or
                    self.cursor_y != original_cursor_pos[0] or self.cursor_x != original_cursor_pos[1] or
                    self.scroll_top != original_scroll_pos[0] or self.scroll_left != original_scroll_pos[1] or
                    self.is_selecting != original_selection_state[0] or  # Selection state changed (e.g., cleared)
                    (self.selection_start != original_selection_state[1] and original_selection_state[
                        1] is not None) or  # Check actual coords
                    (self.selection_end != original_selection_state[2] and original_selection_state[2] is not None) or
                    self.modified != original_modified_flag or
                    self.status_message != original_status_message):
                return True

            return False  # No perceivable change that warrants a redraw

    # 3. ---- Курсор: smart tab ---------
    def handle_smart_tab(self) -> bool:
        """
        Handles smart tabbing behavior.
        - If text is selected, indents the selected block.
        - If cursor is not at the beginning of the line, inserts a standard tab/spaces.
        - If cursor is at the beginning of the line, copies indentation from the previous line
          or inserts a standard tab/spaces if no previous indentation to copy.
        Returns True if any change (text, selection, cursor, scroll, status) occurred, False otherwise.
        """
        if self.is_selecting:
            # handle_block_indent is expected to return bool indicating if a change occurred
            return self.handle_block_indent()

        # If not selecting, and cursor is not at the absolute beginning of the line
        if self.cursor_x > 0:
            # handle_tab calls insert_text, which returns bool
            return self.handle_tab()

        # Cursor is at the beginning of the line (self.cursor_x == 0) and no selection
        indentation_to_copy = ""
        if self.cursor_y > 0:  # Check if there's a previous line
            prev_line_idx = self.cursor_y - 1
            # Ensure prev_line_idx is valid (should be, if self.cursor_y > 0)
            if 0 <= prev_line_idx < len(self.text):
                prev_line_content = self.text[prev_line_idx]
                # Use regex to find leading whitespace
                match_result = re.match(r"^(\s*)", prev_line_content)
                if match_result:
                    indentation_to_copy = match_result.group(1)
            else:  # Should not happen if self.cursor_y > 0
                logging.warning(
                    f"handle_smart_tab: Invalid prev_line_idx {prev_line_idx} when cursor_y is {self.cursor_y}")

        text_to_insert = indentation_to_copy  # Use copied indentation by default

        if not indentation_to_copy:  # If no indentation to copy (e.g., first line, or prev line had no indent)
            tab_size = self.config.get("editor", {}).get("tab_size", 4)
            use_spaces = self.config.get("editor", {}).get("use_spaces", True)
            text_to_insert = " " * tab_size if use_spaces else "\t"

        if not text_to_insert:  # If, for some reason, text_to_insert is still empty (e.g. prev line was empty)
            # then insert_text("") will return False, which is correct.
            logging.debug(
                "handle_smart_tab: No text to insert for smart indent (e.g. previous line empty and no default tab configured).")
            return False  # No change will be made

        # insert_text handles history, self.modified, and returns True if text was inserted.
        return self.insert_text(text_to_insert)

    # 4. для key TAB вспомогательный метод ---------------------------------
    def handle_tab(self) -> bool:
        """
        Inserts standard tab characters (spaces or a tab char) at the current cursor position.
        Deletes selection if active before inserting.
        Returns True if text was inserted (always true if not an empty tab string), False otherwise.
        """
        tab_size = self.config.get("editor", {}).get("tab_size", 4)
        use_spaces = self.config.get("editor", {}).get("use_spaces", True)

        text_to_insert_val = " " * tab_size if use_spaces else "\t"

        if not text_to_insert_val:  # Should not happen with default config
            logging.warning("handle_tab: Tab string is empty, nothing to insert.")
            return False

        # self.insert_text handles active selection, history, and returns True if changes were made.
        return self.insert_text(text_to_insert_val)

    # 5. key Enter ---------------
    def handle_enter(self) -> bool:
        """
        Inserts a new line with smart auto-indentation for Python, Java, C, C++, Rust, JavaScript, and TypeScript.

        This method replicates the indentation of the previous line, and, depending on the programming language,
        automatically increases the indentation level if the previous line opens a new code block.

        - For Python: If the previous line ends with ":", one more indentation level is added.
        - For Java, C, C++, Rust, JavaScript, TypeScript: If the previous line ends with "{", one more indentation is added.
        - For other languages: Only the indentation of the previous line is copied.

        Returns:
            bool: True if the document was modified and requires redraw, False otherwise.

        Examples:
            # Python
            if something:
                [cursor here]

            # Java
            if (condition) {
                [cursor here]

            # Other lines:
                [just copy the previous indent]
        """
        current_line = self.text[self.cursor_y]
        left = current_line[:self.cursor_x]
        right = current_line[self.cursor_x:]

        # Determine current indentation (all leading spaces/tabs in the left part of the line)
        indent_match = re.match(r"[ \t]*", left)
        indent = indent_match.group() if indent_match else ""
        new_indent = indent

        # Editor configuration for indentation
        tab_size = self.config.get("editor", {}).get("tab_size", 4)
        use_spaces = self.config.get("editor", {}).get("use_spaces", True)
        extra_indent = " " * tab_size if use_spaces else "\t"

        # Python: increase indentation after lines ending with ':'
        if self.current_language == "python":
            if left.rstrip().endswith(":"):
                new_indent += extra_indent

        # C-like languages: increase indentation after lines ending with '{'
        elif self.current_language in {"java", "c", "cpp", "c++", "rust", "javascript", "typescript"}:
            if left.rstrip().endswith("{"):
                new_indent += extra_indent

        # Insert the new line with the calculated indentation
        self.text[self.cursor_y] = left
        self.text.insert(self.cursor_y + 1, new_indent + right)
        self.cursor_y += 1
        self.cursor_x = len(new_indent)
        self.modified = True
        self._set_status_message("Inserted line with smart auto-indent")
        return True

    # 6. Insert text at position -----------------------------------------------------------
    def insert_text_at_position(self, text: str, row: int, col: int) -> bool:  # Added return type bool
        """
        Low-level insertion of `text` at the logical position (row, col).
        DOES NOT add to action history - the caller is responsible for that.
        Cursor is set immediately after the inserted text.
        Raises IndexError if row or col is invalid for insertion.
        Returns True if text was non-empty and thus inserted, False otherwise.
        """
        if not text:
            logging.debug("insert_text_at_position: empty text -> no action, returning False")
            return False  # No change

        # Validate row index
        if not (0 <= row < len(self.text)):
            msg = f"insert_text_at_position: invalid row index {row} (buffer size {len(self.text)})"
            logging.error(msg)
            raise IndexError(msg)

        current_line_len = len(self.text[row])
        # Validate and clamp column index for insertion (allows insertion at end of line)
        if not (0 <= col <= current_line_len):
            # This case might be an error, but clamping is often a safe recovery.
            # For strictness, one might raise IndexError here too.
            logging.warning(
                f"insert_text_at_position: column {col} out of bounds for line {row} (len {current_line_len}). Clamping.")
            col = max(0, min(col, current_line_len))

        logging.debug(f"insert_text_at_position: text={text!r} at row={row}, col={col}")

        lines_to_insert = text.split('\n')

        original_line_prefix = self.text[row][:col]
        original_line_suffix = self.text[row][col:]  # "tail" of the original line

        # 1. Replace the current line: prefix + first line of text to insert
        self.text[row] = original_line_prefix + lines_to_insert[0]

        # 2. Insert intermediate lines (if any)
        # lines_to_insert[1:-1] - all lines between the first and the last
        for offset, line_content in enumerate(lines_to_insert[1:-1], start=1):
            self.text.insert(row + offset, line_content)

        # 3. Add the last line of text to insert + the original line's suffix
        if len(lines_to_insert) > 1:
            # Insert as a new line: last inserted line + original tail
            self.text.insert(row + len(lines_to_insert) - 1, lines_to_insert[-1] + original_line_suffix)
        else:
            # Single line insertion - just append the original tail
            self.text[row] += original_line_suffix

        # 4. Recalculate cursor position
        if len(lines_to_insert) == 1:
            # Stayed on the same logical line
            self.cursor_y = row
            self.cursor_x = col + len(lines_to_insert[0])  # Length of the inserted text (first/only line)
        else:
            # Moved to the last inserted logical line
            self.cursor_y = row + len(lines_to_insert) - 1
            self.cursor_x = len(lines_to_insert[-1])  # Length of the last inserted line segment

        self.modified = True
        logging.debug(f"insert_text_at_position: cursor now at (y={self.cursor_y}, x={self.cursor_x})")
        return True  # Text was inserted

    # 7. Main Insert Text ----------------------------------------------
    def insert_text(self, text: str) -> bool:
        """
        Main public method for text insertion.
        Handles active selection by deleting it first. If so, the deletion and
        insertion are grouped as one compound action for undo/redo purposes.
        Cursor is set after the inserted text.
        Returns True if text was inserted or selection was modified, False otherwise.
        """
        if not text and not self.is_selecting:
            logging.debug("insert_text: empty text and no selection, no change.")
            return False

        made_change_overall = False
        with self._state_lock:
            effective_insert_y, effective_insert_x = self.cursor_y, self.cursor_x
            original_status = self.status_message

            # Start a compound action to group deletion and insertion
            self.history.begin_compound_action()
            try:
                # 1. Handle active selection by deleting it first
                if self.is_selecting:
                    normalized_selection = self._get_normalized_selection_range()
                    if normalized_selection:
                        norm_start_coords, norm_end_coords = normalized_selection
                        logging.debug(f"insert_text: Deleting active selection from {norm_start_coords} to {norm_end_coords} before insertion.")

                        deleted_segments = self.delete_selected_text_internal(
                            norm_start_coords[0], norm_start_coords[1],
                            norm_end_coords[0], norm_end_coords[1]
                        )

                        # Record deletion action if something was actually deleted
                        if deleted_segments or (norm_start_coords != norm_end_coords):
                            self.history.add_action({
                                "type": "delete_selection",
                                "text": deleted_segments,
                                "start": norm_start_coords,
                                "end": norm_end_coords,
                            })
                            made_change_overall = True

                        # delete_selected_text_internal sets the new cursor position
                        effective_insert_y, effective_insert_x = self.cursor_y, self.cursor_x

                        self.is_selecting = False
                        self.selection_start = None
                        self.selection_end = None
                        logging.debug(f"insert_text: Selection processed. Cursor at ({self.cursor_y}, {self.cursor_x}).")

                # 2. Insert the new text (if there is any)
                if text:
                    insert_pos_for_history = (effective_insert_y, effective_insert_x)
                    
                    try:
                        if self.insert_text_at_position(text, effective_insert_y, effective_insert_x):
                            made_change_overall = True
                    except IndexError as e:
                        logging.error(f"insert_text: Error during insert_text_at_position: {e}", exc_info=True)
                        self._set_status_message(f"Insertion error: {e}")
                        # Важно завершить транзакцию даже при ошибке, чтобы очистить стек redo
                        return True

                    # Add "insert" action to history
                    self.history.add_action({
                        "type": "insert",
                        "text": text,
                        "position": insert_pos_for_history
                    })

            finally:
                # End the compound action. This will clear the redo stack once for the whole operation.
                self.history.end_compound_action()

            # 3. Final status update and logging
            if made_change_overall:
                if self.status_message == original_status:
                    self._set_status_message("Text inserted" if text else "Selection deleted")
                logging.debug(f"insert_text: Completed. Text '{text!r}' processed. Final cursor ({self.cursor_y}, {self.cursor_x}).")
            else:
                logging.debug(f"insert_text: No effective change made for text '{text!r}'.")

        return made_change_overall

    # 8. Delete selected text ---------------------------------------------------------------
    def delete_selected_text_internal(self, start_y: int, start_x: int, end_y: int, end_x: int) -> list[str]:
        """
        Low-level method: deletes text between normalized (start_y, start_x) and (end_y, end_x).
        Returns the deleted text as a list of strings (segments).
        Sets the cursor to (start_y, start_x).
        DOES NOT record an action in history. Sets self.modified = True.
        Assumes coordinates are normalized (start_y, start_x) <= (end_y, end_x).
        """
        logging.debug(f"delete_selected_text_internal: Deleting from ({start_y},{start_x}) to ({end_y},{end_x})")

        # Basic coordinate validation
        # Ensure start_y and end_y are within the bounds of self.text
        if not (0 <= start_y < len(self.text) and 0 <= end_y < len(self.text)):
            logging.error(
                f"delete_selected_text_internal: Invalid row indices for deletion: "
                f"start_y={start_y}, end_y={end_y} with text length {len(self.text)}"
            )
            return []  # Return empty if rows are out of bounds

        # Ensure start_x and end_x are within the bounds of their respective lines
        if not (0 <= start_x <= len(self.text[start_y]) and 0 <= end_x <= len(self.text[end_y])):
            logging.error(
                f"delete_selected_text_internal: Invalid column indices for deletion: "
                f"start_x={start_x} (line len {len(self.text[start_y])}), "
                f"end_x={end_x} (line len {len(self.text[end_y])})"
            )
            return []

        deleted_segments = []
        if start_y == end_y:
            # Deletion within a single line
            line_content = self.text[start_y]
            actual_start_x = min(start_x, end_x)
            actual_end_x = max(start_x, end_x)

            actual_start_x = min(actual_start_x, len(line_content))
            actual_end_x = min(actual_end_x, len(line_content))

            if actual_start_x < actual_end_x:
                deleted_segments.append(line_content[actual_start_x:actual_end_x])
                self.text[start_y] = line_content[:actual_start_x] + line_content[actual_end_x:]
            else:
                logging.debug(
                    "delete_selected_text_internal: Single line selection, but start_x >= end_x. No characters deleted.")
        else:
            # Multi-line deletion
            line_start_content = self.text[start_y]
            actual_start_x_on_first_line = min(start_x, len(line_start_content))
            deleted_segments.append(line_start_content[actual_start_x_on_first_line:])

            remaining_prefix_on_start_line = line_start_content[:actual_start_x_on_first_line]

            if end_y > start_y + 1:
                deleted_segments.extend(self.text[start_y + 1: end_y])

            line_end_content = self.text[end_y]
            actual_end_x_on_last_line = min(end_x, len(line_end_content))
            deleted_segments.append(line_end_content[:actual_end_x_on_last_line])

            remaining_suffix_on_end_line = line_end_content[actual_end_x_on_last_line:]

            self.text[start_y] = remaining_prefix_on_start_line + remaining_suffix_on_end_line
            del self.text[start_y + 1: end_y + 1]

        self.cursor_y = start_y
        self.cursor_x = start_x

        self.modified = True

        if not deleted_segments and start_y == end_y and start_x == end_x:
            logging.debug(
                f"delete_selected_text_internal: No actual characters deleted (empty selection at a point). Cursor at ({self.cursor_y},{self.cursor_x}).")
        else:
            logging.debug(
                f"delete_selected_text_internal: Deletion complete. Cursor at ({self.cursor_y},{self.cursor_x}). "
                f"Deleted segments count: {len(deleted_segments)}. First segment preview: '{deleted_segments[0][:50] if deleted_segments else ""}'"
            )
        return deleted_segments

    # 9. Paste ---------------
    def paste(self) -> bool:
        """
        Pastes text from the clipboard at the current cursor position.
        If text is selected, the selected text is replaced by the clipboard content.
        Manages action history for the entire paste operation (which might include
        a deletion and an insertion as a single logical step for undo).

        Returns:
            bool: True if the text, selection, cursor, scroll, or status message changed,
                  False otherwise.
        """
        with self._state_lock:
            # Store initial state for comparison
            original_cursor_pos = (self.cursor_y, self.cursor_x)
            original_scroll_pos = (self.scroll_top, self.scroll_left)
            original_selection_state = (self.is_selecting, self.selection_start, self.selection_end)
            original_modified_flag = self.modified
            original_status = self.status_message

            made_change_to_content = False  # Tracks if text content or buffer structure actually changed

            text_to_paste = self.internal_clipboard
            source_of_paste = "internal"

            if self.use_system_clipboard and self.pyclip_available:
                try:
                    system_clipboard_text = pyperclip.paste()
                    if system_clipboard_text:
                        text_to_paste = system_clipboard_text
                        source_of_paste = "system"
                        logging.debug(f"Pasting {len(text_to_paste)} chars from system clipboard.")
                    else:
                        logging.debug("System clipboard is empty, using internal clipboard content.")
                except pyperclip.PyperclipException as e:
                    logging.error(f"System clipboard error on paste: {e} – using internal clipboard.")
                except Exception as e:
                    logging.exception(f"Unexpected clipboard error on paste: {e} – using internal clipboard.")

            if not text_to_paste:
                self._set_status_message("Clipboard is empty")
                # Return True if status changed (it did), or if selection was active and might be implicitly cleared by some UIs
                return self.status_message != original_status

                # Normalize line endings from clipboard
            text_to_paste = text_to_paste.replace("\r\n", "\n").replace("\r", "\n")

            # Determine actual insertion point, which might change if selection is deleted
            effective_insert_y, effective_insert_x = self.cursor_y, self.cursor_x

            # --- Start of compound action for undo history ---
            # We will group deletion (if any) and insertion into a sequence
            # that should ideally be undone/redone together.
            # A more advanced undo system might use "compound actions".
            # For now, we add them sequentially, and `undone_actions.clear()` after the sequence.

            # 1. Handle active selection: delete it first
            if self.is_selecting:
                normalized_selection = self._get_normalized_selection_range()
                if normalized_selection:
                    norm_start_coords, norm_end_coords = normalized_selection

                    logging.debug(
                        f"paste: Deleting active selection from {norm_start_coords} to {norm_end_coords} before pasting.")

                    deleted_segments = self.delete_selected_text_internal(
                        norm_start_coords[0], norm_start_coords[1],
                        norm_end_coords[0], norm_end_coords[1]
                    )

                    # Record deletion action if something was actually deleted
                    if deleted_segments or (norm_start_coords != norm_end_coords):
                        self.history.add_action({
                            "type": "delete_selection",
                            "text": deleted_segments,
                            "start": norm_start_coords,
                            "end": norm_end_coords,
                        })
                        # Don't clear undone_actions yet, as insertion is part of this user action
                        made_change_to_content = True

                    # delete_selected_text_internal sets the cursor to norm_start_coords
                    effective_insert_y, effective_insert_x = self.cursor_y, self.cursor_x

                    self.is_selecting = False  # Selection is now gone
                    self.selection_start = None
                    self.selection_end = None

            # 2. Insert the text from clipboard at the effective position
            # Store position before low-level insert for this part of the history
            insert_pos_for_history = (effective_insert_y, effective_insert_x)

            try:
                # Use the low-level insert_text_at_position directly
                if self.insert_text_at_position(text_to_paste, effective_insert_y, effective_insert_x):
                    made_change_to_content = True  # Text was inserted
                # insert_text_at_position sets self.modified = True and updates cursor
            except IndexError as e:
                logging.error(f"paste: Error during insert_text_at_position: {e}")
                self._set_status_message(f"Paste insertion error: {e}")
                # If deletion happened, action_history already has it.
                # If insertion failed, the state might be after deletion but before successful paste.
                # We need to decide if we should attempt to undo the deletion part or leave as is.
                # For now, just report error.
                return True  # Status changed

            # Add "insert" action to history for the pasted text
            self.history.add_action({
                "type": "insert",
                "text": text_to_paste,
                "position": insert_pos_for_history
            })

            # Now that the compound paste operation (delete + insert) is complete, clear redo stack.
            if made_change_to_content:
                logging.debug("Content changed by paste() — action already recorded in history.")

            # Final status message
            if made_change_to_content:
                self._set_status_message(f"Pasted from {source_of_paste} clipboard")
            elif self.status_message == original_status:  # No content change, status not set by error
                self._set_status_message("Paste: No effective change (e.g., pasting empty over empty)")

            # Ensure cursor and scroll are valid
            if made_change_to_content:
                self._ensure_cursor_in_bounds()
                self._clamp_scroll()

            # Determine if a redraw is needed by comparing overall state
            if (made_change_to_content or
                    (self.cursor_y, self.cursor_x) != original_cursor_pos or
                    (self.scroll_top, self.scroll_left) != original_scroll_pos or
                    (self.is_selecting, self.selection_start, self.selection_end) != original_selection_state or
                    self.modified != original_modified_flag or
                    self.status_message != original_status):
                return True

            return False

    # 10. Cut -----------
    def cut(self) -> bool:
        """
        Cuts the selected text to the internal and (if enabled) system clipboard.
        The selected text is removed from the document.
        Manages action history for the deletion.

        Returns:
            bool: True if the text, selection, cursor, scroll, or status message changed,
                  False otherwise (e.g., if trying to cut with no selection and status didn't change).
        """
        with self._state_lock:
            # Store initial state for comparison
            original_cursor_pos = (self.cursor_y, self.cursor_x)
            original_scroll_pos = (self.scroll_top, self.scroll_left)
            original_selection_state = (self.is_selecting, self.selection_start, self.selection_end)
            original_modified_flag = self.modified
            original_status = self.status_message

            action_made_change_to_content = False  # Tracks if text content actually changed

            if not self.is_selecting:
                self._set_status_message("Nothing to cut (no selection)")
                return self.status_message != original_status  # Redraw if status message changed

            # Selection exists, proceed with cutting
            normalized_range = self._get_normalized_selection_range()
            if not normalized_range:  # Should ideally not happen if self.is_selecting is True
                logging.warning("cut: is_selecting=True, but no valid normalized range.")
                self.is_selecting = False  # Attempt to recover
                self.selection_start = None
                self.selection_end = None
                self._set_status_message("Cut error: Invalid selection state")
                return True  # Status changed

            norm_start_coords, norm_end_coords = normalized_range

            # delete_selected_text_internal sets self.modified and new cursor position
            deleted_text_segments = self.delete_selected_text_internal(
                norm_start_coords[0], norm_start_coords[1],
                norm_end_coords[0], norm_end_coords[1]
            )

            # Check if anything was actually deleted
            if not deleted_text_segments and norm_start_coords == norm_end_coords:
                # This means the selection was effectively a point (start == end)
                self._set_status_message("Nothing to cut (empty selection)")
                self.is_selecting = False  # Clear selection state
                self.selection_start = None
                self.selection_end = None
                # Return True if status message changed or selection state changed
                return (self.status_message != original_status or
                        (self.is_selecting, self.selection_start, self.selection_end) != original_selection_state)

            action_made_change_to_content = True  # Text was removed

            text_for_clipboard = "\n".join(deleted_text_segments)

            # Always copy to internal clipboard
            self.internal_clipboard = text_for_clipboard
            status_message_for_cut = "Cut to internal clipboard"  # Default message

            # Attempt to copy to system clipboard if enabled and available
            if self.use_system_clipboard and self.pyclip_available:
                try:
                    pyperclip.copy(text_for_clipboard)
                    status_message_for_cut = "Cut to system clipboard"
                    logging.debug("Text cut and copied to system clipboard successfully.")
                except pyperclip.PyperclipException as e:
                    logging.error(f"Failed to copy cut text to system clipboard: {str(e)}")
                    status_message_for_cut = "Cut to internal clipboard (system clipboard error)"
                except Exception as e:  # Catch any other unexpected error
                    logging.error(f"Unexpected error copying cut text to system clipboard: {e}", exc_info=True)
                    status_message_for_cut = "Cut to internal clipboard (unexpected system clipboard error)"

            # Add the deletion action to history
            self.history.add_action({
                "type": "delete_selection",
                "text": deleted_text_segments,
                "start": norm_start_coords,
                "end": norm_end_coords
            })

            # self.modified is already set to True by delete_selected_text_internal
            # Cursor position is also set by delete_selected_text_internal to norm_start_coords
            self.is_selecting = False  # Selection is gone after cut
            self.selection_start = None
            self.selection_end = None

            self._set_status_message(status_message_for_cut)

            # Ensure cursor and scroll are valid after the operation
            # Although delete_selected_text_internal sets cursor, _ensure_cursor_in_bounds is a good safeguard
            self._ensure_cursor_in_bounds()
            self._clamp_scroll()  # Scroll might need adjustment if cursor position changed significantly

            # Determine if a redraw is needed by comparing overall state
            # Since cut always involves text deletion and status change, it should always return True if successful.
            if (action_made_change_to_content or
                    (self.cursor_y, self.cursor_x) != original_cursor_pos or
                    (self.scroll_top, self.scroll_left) != original_scroll_pos or
                    (self.is_selecting, self.selection_start, self.selection_end) != original_selection_state or
                    self.modified != original_modified_flag or  # Check if modified flag state actually flipped
                    self.status_message != original_status):
                return True

            return False  # Should not be reached if cut was successful

    # 11. Undo
    def undo(self) -> bool:
        """Delegates the undo action to the History component."""
        return self.history.undo()
 
    # 12. Redo
    def redo(self) -> bool:
        """Delegates the redo action to the History component."""
        return self.history.redo()


    # III. Управление выделением (косвенно связано с видимым курсором, который обычно на конце выделения):
    # extend_selection_right(self)
    # extend_selection_left(self)
    # extend_selection_up(self)
    # extend_selection_down(self)
    # select_to_home(self)
    # select_to_end(self)
    # select_all(self)
    ############### Selection Handling ####################
    # 1. Selection Right
    def extend_selection_right(self) -> bool:
        """
        Extends the selection one character to the right.
        If no selection is active, starts a new selection from the current cursor position.
        Moves the cursor to the new end of the selection.
        Adjusts scroll if necessary.

        Returns:
            bool: True if the cursor position, selection state/boundaries, or scroll position changed,
                  False otherwise.
        """
        # Store initial state for comparison
        original_cursor_pos = (self.cursor_y, self.cursor_x)
        original_scroll_pos = (self.scroll_top, self.scroll_left)
        original_selection_start = self.selection_start
        original_selection_end = self.selection_end
        original_is_selecting_flag = self.is_selecting
        # No status message is typically set by this kind of fine-grained action

        changed_state = False

        with self._state_lock:
            current_line_idx = self.cursor_y
            if current_line_idx >= len(self.text):  # Should not happen with valid cursor
                logging.warning(f"extend_selection_right: cursor_y {current_line_idx} out of bounds.")
                return False  # No change possible

            current_line_content = self.text[current_line_idx]
            current_line_length = len(current_line_content)

            # If selection is not active, start it from the current cursor position.
            if not self.is_selecting:
                self.selection_start = (self.cursor_y, self.cursor_x)
                self.is_selecting = True
                # This itself is a state change if original_is_selecting_flag was False

            # Move the cursor logically one character to the right, if not at the end of the line.
            # This also becomes the new end of the selection.
            if self.cursor_x < current_line_length:
                self.cursor_x += 1
                # Skip over zero-width characters if any (though less common for simple right extension)
                while self.cursor_x < current_line_length and wcwidth(current_line_content[self.cursor_x]) == 0:
                    self.cursor_x += 1
            # If at the end of the line, cursor_x does not move further right on this line.
            # Extending selection to the next line is typically handled by extend_selection_down then extend_selection_left/right.
            # This method focuses on extending right on the *current* line or starting selection.

            # Update the end of the selection to the new cursor position.
            self.selection_end = (self.cursor_y, self.cursor_x)

            # Ensure scrolling is adjusted if the cursor moved out of view.
            self._clamp_scroll()

        # Determine if any relevant state actually changed.
        if ((self.cursor_y, self.cursor_x) != original_cursor_pos or
                (self.scroll_top, self.scroll_left) != original_scroll_pos or
                self.is_selecting != original_is_selecting_flag or
                self.selection_start != original_selection_start or
                self.selection_end != original_selection_end):
            changed_state = True
            logging.debug(
                f"extend_selection_right: New cursor ({self.cursor_y},{self.cursor_x}), "
                f"selection_end ({self.selection_end}). Changed: {changed_state}"
            )
        else:
            logging.debug("extend_selection_right: No change in cursor, scroll, or selection state.")

        return changed_state

    # 2. Selection Left --------------------
    def extend_selection_left(self) -> bool:
        """
        Extends the selection one character to the left.
        If no selection is active, starts a new selection from the current cursor position.
        Moves the cursor to the new end of the selection (which is to the left).
        Adjusts scroll if necessary.

        Returns:
            bool: True if the cursor position, selection state/boundaries, or scroll position changed,
                  False otherwise.
        """
        # Store initial state for comparison
        original_cursor_pos = (self.cursor_y, self.cursor_x)
        original_scroll_pos = (self.scroll_top, self.scroll_left)
        original_selection_start = self.selection_start
        original_selection_end = self.selection_end
        original_is_selecting_flag = self.is_selecting

        changed_state = False

        with self._state_lock:
            current_line_idx = self.cursor_y
            if current_line_idx >= len(self.text):  # Should not happen with valid cursor
                logging.warning(f"extend_selection_left: cursor_y {current_line_idx} out of bounds.")
                return False  # No change possible

            # If selection is not active, start it from the current cursor position.
            # When extending left, the initial cursor position becomes the 'anchor' or 'selection_start'
            # if we consider selection_end to be the moving part.
            # Or, if self.selection_start is the fixed point and self.selection_end moves with cursor:
            if not self.is_selecting:
                self.selection_start = (self.cursor_y, self.cursor_x)  # Anchor point
                self.is_selecting = True

            # Move the cursor logically one character to the left.
            if self.cursor_x > 0:
                self.cursor_x -= 1
                # If moving left lands on a zero-width character, keep moving left
                # until a non-zero-width character is found or beginning of line.
                # This ensures the selection "jumps over" combining characters.
                current_line_content = self.text[current_line_idx]
                while self.cursor_x > 0 and wcwidth(current_line_content[self.cursor_x]) == 0:
                    self.cursor_x -= 1
            # If at the beginning of the current line (self.cursor_x == 0),
            # this method does not currently extend to the previous line.
            # That would typically be handled by extend_selection_up then extend_selection_right/end.

            # Update the end of the selection to the new cursor position.
            # If selection_start was (y, X) and cursor moves to (y, X-1),
            # selection_end becomes (y, X-1).
            self.selection_end = (self.cursor_y, self.cursor_x)

            # Ensure scrolling is adjusted if the cursor moved out of view.
            self._clamp_scroll()

        # Determine if any relevant state actually changed.
        if ((self.cursor_y, self.cursor_x) != original_cursor_pos or
                (self.scroll_top, self.scroll_left) != original_scroll_pos or
                self.is_selecting != original_is_selecting_flag or
                self.selection_start != original_selection_start or  # Could change if is_selecting was false
                self.selection_end != original_selection_end):  # Will always change if cursor_x changed
            changed_state = True
            logging.debug(
                f"extend_selection_left: New cursor ({self.cursor_y},{self.cursor_x}), "
                f"selection_end ({self.selection_end}). Changed: {changed_state}"
            )
        else:
            logging.debug("extend_selection_left: No change in cursor, scroll, or selection state.")

        return changed_state

    # 3. Selection Up
    def extend_selection_up(self) -> bool:
        """
        Extends the selection one line upwards.
        If no selection is active, starts a new selection from the current cursor position.
        The cursor moves to the corresponding column in the line above, clamped by line length.
        The new cursor position becomes the (moving) end of the selection.

        Returns:
            bool: True if the cursor position, selection state/boundaries, or scroll position changed,
                  False otherwise.
        """
        # Store initial state for comparison
        original_cursor_pos = (self.cursor_y, self.cursor_x)
        original_scroll_pos = (self.scroll_top, self.scroll_left)
        original_selection_start = self.selection_start
        original_selection_end = self.selection_end
        original_is_selecting_flag = self.is_selecting

        changed_state = False

        with self._state_lock:
            # If selection is not active, start it from the current cursor position.
            # This current position becomes the 'anchor' (selection_start).
            if not self.is_selecting:
                self.selection_start = (self.cursor_y, self.cursor_x)
                self.is_selecting = True

            # Move the cursor logically one line up, if not already at the first line.
            if self.cursor_y > 0:
                self.cursor_y -= 1
                # Maintain the horizontal cursor column if possible, clamping to the new line's length.
                # self.cursor_x (the "desired" column) is preserved from the previous line.
                self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))
            # If at the first line (self.cursor_y == 0), no further upward movement is possible.

            # Update the end of the selection to the new cursor position.
            self.selection_end = (self.cursor_y, self.cursor_x)

            # Adjust scroll if necessary.
            self._clamp_scroll()

        # Determine if any relevant state actually changed.
        if ((self.cursor_y, self.cursor_x) != original_cursor_pos or
                (self.scroll_top, self.scroll_left) != original_scroll_pos or
                self.is_selecting != original_is_selecting_flag or
                self.selection_start != original_selection_start or  # Could change if is_selecting was false
                self.selection_end != original_selection_end):  # Will change if cursor_y or cursor_x changed
            changed_state = True
            logging.debug(
                f"extend_selection_up: New cursor ({self.cursor_y},{self.cursor_x}), "
                f"selection_end ({self.selection_end}). Changed: {changed_state}"
            )
        else:
            logging.debug("extend_selection_up: No change in cursor, scroll, or selection state.")

        return changed_state

    # 4. Selection Down
    def extend_selection_down(self) -> bool:
        """
        Extends the selection one line downwards.
        If no selection is active, starts a new selection from the current cursor position.
        The cursor moves to the corresponding column in the line below, clamped by line length.
        The new cursor position becomes the (moving) end of the selection.

        Returns:
            bool: True if the cursor position, selection state/boundaries, or scroll position changed,
                  False otherwise.
        """
        # Store initial state for comparison
        original_cursor_pos = (self.cursor_y, self.cursor_x)
        original_scroll_pos = (self.scroll_top, self.scroll_left)
        original_selection_start = self.selection_start
        original_selection_end = self.selection_end
        original_is_selecting_flag = self.is_selecting

        changed_state = False

        with self._state_lock:
            # If selection is not active, start it from the current cursor position.
            if not self.is_selecting:
                self.selection_start = (self.cursor_y, self.cursor_x)
                self.is_selecting = True

            # Move the cursor logically one line down, if not already at the last line.
            if self.cursor_y < len(self.text) - 1:
                self.cursor_y += 1
                # Maintain the horizontal cursor column if possible, clamping to the new line's length.
                self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))
            # If at the last line, no further downward movement.

            # Update the end of the selection to the new cursor position.
            self.selection_end = (self.cursor_y, self.cursor_x)

            # Adjust scroll if necessary.
            self._clamp_scroll()

        # Determine if any relevant state actually changed.
        if ((self.cursor_y, self.cursor_x) != original_cursor_pos or
                (self.scroll_top, self.scroll_left) != original_scroll_pos or
                self.is_selecting != original_is_selecting_flag or
                self.selection_start != original_selection_start or
                self.selection_end != original_selection_end):
            changed_state = True
            logging.debug(
                f"extend_selection_down: New cursor ({self.cursor_y},{self.cursor_x}), "
                f"selection_end ({self.selection_end}). Changed: {changed_state}"
            )
        else:
            logging.debug("extend_selection_down: No change in cursor, scroll, or selection state.")

        return changed_state

    # 5. Selection Home ---------------
    def select_to_home(self) -> bool:
        """
        Extends the selection from the current cursor position to the beginning of the current line.
        If no selection is active, starts a new selection.
        The cursor moves to the beginning of the line (column 0).

        Returns:
            bool: True if the cursor position, selection state/boundaries, or scroll position changed,
                  False otherwise.
        """
        # Store initial state for comparison
        original_cursor_pos = (self.cursor_y, self.cursor_x)
        original_scroll_pos = (self.scroll_top, self.scroll_left)  # Specifically scroll_left
        original_selection_start = self.selection_start
        original_selection_end = self.selection_end
        original_is_selecting_flag = self.is_selecting

        changed_state = False

        with self._state_lock:
            current_line_idx = self.cursor_y
            if current_line_idx >= len(self.text):  # Should not happen with valid cursor
                logging.warning(f"select_to_home: cursor_y {current_line_idx} out of bounds.")
                return False  # No change possible

            # If selection is not active, start it from the current cursor position.
            # This current position becomes the 'anchor' (selection_start).
            if not self.is_selecting:
                self.selection_start = (self.cursor_y, self.cursor_x)
                self.is_selecting = True

            # Move the cursor to the beginning of the current line (column 0).
            self.cursor_x = 0

            # Update the end of the selection to the new cursor position (beginning of the line).
            self.selection_end = (self.cursor_y, self.cursor_x)

            # Adjust horizontal scroll if necessary, as cursor moved to column 0.
            # _clamp_scroll will handle if self.cursor_x (now 0) is less than self.scroll_left.
            self._clamp_scroll()

        # Determine if any relevant state actually changed.
        if ((self.cursor_y, self.cursor_x) != original_cursor_pos or
                (self.scroll_top, self.scroll_left) != original_scroll_pos or  # Check both scroll dimensions
                self.is_selecting != original_is_selecting_flag or
                self.selection_start != original_selection_start or
                self.selection_end != original_selection_end):
            changed_state = True
            logging.debug(
                f"select_to_home: New cursor ({self.cursor_y},{self.cursor_x}), "
                f"selection_end ({self.selection_end}). Changed: {changed_state}"
            )
        else:
            logging.debug("select_to_home: No change in cursor, scroll, or selection state.")

        return changed_state

    # 6. Selection End
    def select_to_end(self) -> bool:
        """
        Extends the selection from the current cursor position to the end of the current line.
        If no selection is active, starts a new selection.
        The cursor moves to the end of the line.

        Returns:
            bool: True if the cursor position, selection state/boundaries, or scroll position changed,
                  False otherwise.
        """
        # Store initial state for comparison
        original_cursor_pos = (self.cursor_y, self.cursor_x)
        original_scroll_pos = (self.scroll_top, self.scroll_left)
        original_selection_start = self.selection_start
        original_selection_end = self.selection_end
        original_is_selecting_flag = self.is_selecting

        changed_state = False

        with self._state_lock:
            current_line_idx = self.cursor_y
            if current_line_idx >= len(self.text):  # Should not happen with valid cursor
                logging.warning(f"select_to_end: cursor_y {current_line_idx} out of bounds.")
                return False  # No change possible

            current_line_content = self.text[current_line_idx]
            current_line_length = len(current_line_content)

            # If selection is not active, start it from the current cursor position.
            # This current position becomes the 'anchor' (selection_start).
            if not self.is_selecting:
                self.selection_start = (self.cursor_y, self.cursor_x)
                self.is_selecting = True

            # Move the cursor to the end of the current line.
            self.cursor_x = current_line_length

            # Update the end of the selection to the new cursor position (end of the line).
            self.selection_end = (self.cursor_y, self.cursor_x)

            # Adjust scroll if necessary, as cursor may have moved far right.
            self._clamp_scroll()

        # Determine if any relevant state actually changed.
        if ((self.cursor_y, self.cursor_x) != original_cursor_pos or
                (self.scroll_top, self.scroll_left) != original_scroll_pos or
                self.is_selecting != original_is_selecting_flag or
                self.selection_start != original_selection_start or
                self.selection_end != original_selection_end):
            changed_state = True
            logging.debug(
                f"select_to_end: New cursor ({self.cursor_y},{self.cursor_x}), "
                f"selection_end ({self.selection_end}). Changed: {changed_state}"
            )
        else:
            logging.debug("select_to_end: No change in cursor, scroll, or selection state.")

        return changed_state

    # 7. Selection ALL
    def select_all(self) -> bool:
        """
        Selects all text in the document.
        Moves the cursor to the end of the selection.
        Sets a status message.
        Adjusts scroll to ensure the end of the selection (and cursor) is visible.

        Returns:
            bool: True, as this action always changes the selection state,
                  cursor position, potentially scroll, and status message,
                  thus requiring a redraw.
        """
        logging.debug("select_all called")

        # Store original state for comparison, though this action almost always changes it
        original_selection_state = (self.is_selecting, self.selection_start, self.selection_end)
        original_cursor_pos = (self.cursor_y, self.cursor_x)
        original_scroll_pos = (self.scroll_top, self.scroll_left)
        original_status = self.status_message

        with self._state_lock:  # Ensure atomicity of state changes
            if not self.text:  # Should not happen if text is always at least [""]
                self.text = [""]  # Ensure there's at least one line
                logging.warning("select_all: Text buffer was unexpectedly empty, initialized to [''].")

            # Set selection start to the beginning of the document
            self.selection_start = (0, 0)

            # Determine the end of the document
            # If the buffer is [""] (one empty line), last_line_idx is 0, len(self.text[0]) is 0.
            # So selection_end will be (0,0).
            # If buffer has content, e.g. ["abc", "de"], last_line_idx is 1, len(self.text[1]) is 2.
            # So selection_end will be (1,2).
            last_line_idx = max(0, len(self.text) - 1)
            self.selection_end = (last_line_idx, len(self.text[last_line_idx]))

            self.is_selecting = True  # Mark that selection is active

            # Move the cursor to the end of the new selection
            self.cursor_y, self.cursor_x = self.selection_end

            self._set_status_message("All text selected")

            # Adjust scroll to make the new cursor position (end of selection) visible
            self._clamp_scroll()

            # Determine if a redraw is needed by comparing relevant state aspects
        # For select_all, it's virtually guaranteed to change state.
        if (self.is_selecting != original_selection_state[0] or
                self.selection_start != original_selection_state[1] or
                self.selection_end != original_selection_state[2] or
                (self.cursor_y, self.cursor_x) != original_cursor_pos or
                (self.scroll_top, self.scroll_left) != original_scroll_pos or
                self.status_message != original_status):
            return True

        return False  # Should technically not be reached if "All text selected" is always set.

    ## Методы блочных отступов/комментирования ===============================================
    #
    # (также влияют на self.cursor_x/y после изменения выделения)
    # 1. handle_block_indent(self)
    # 2. handle_block_unindent(self)
    # 3. unindent_current_line(self)
    # 4. comment_lines(self, ...)
    # 5. uncomment_lines(self, ...)

    # 1.
    def handle_block_indent(self) -> bool:
        """
        Increases indentation for all lines within the current selection.
        Updates selection, cursor, modified status, and action history.

        Returns:
            bool: True if any lines were indented or if the status message changed,
                  indicating a redraw is needed. False if no selection was active.
        """
        if not self.is_selecting or not self.selection_start or not self.selection_end:
            self._set_status_message("No selection to indent.")
            return True  # Status message changed

        original_status = self.status_message
        original_selection_tuple = (self.is_selecting, self.selection_start, self.selection_end)
        # original_cursor_tuple = (self.cursor_y, self.cursor_x) # <--- УДАЛЕНО

        made_actual_text_change = False

        with self._state_lock:
            norm_range = self._get_normalized_selection_range()
            if not norm_range:
                logging.warning(
                    "handle_block_indent: Could not get normalized selection range despite active selection.")
                self._set_status_message("Selection error during indent.")
                return True

            start_coords, end_coords = norm_range
            start_y_idx, start_x_in_line_sel = start_coords
            end_y_idx, end_x_in_line_sel = end_coords

            tab_size = self.config.get("editor", {}).get("tab_size", 4)
            use_spaces = self.config.get("editor", {}).get("use_spaces", True)
            indent_string = " " * tab_size if use_spaces else "\t"
            indent_char_length = len(indent_string)

            undo_changes_list: List[Dict[str, Any]] = []
            indented_line_count = 0

            for current_y in range(start_y_idx, end_y_idx + 1):
                if current_y >= len(self.text):
                    continue

                original_line_content = self.text[current_y]
                self.text[current_y] = indent_string + original_line_content

                undo_changes_list.append({
                    "line_index": current_y,
                    "original_text": original_line_content,
                    "new_text": self.text[current_y]
                })
                indented_line_count += 1
                made_actual_text_change = True

            if made_actual_text_change:
                self.modified = True

                new_selection_start_x = start_x_in_line_sel + indent_char_length
                new_selection_end_x = end_x_in_line_sel + indent_char_length

                self.selection_start = (start_y_idx, new_selection_start_x)
                self.selection_end = (end_y_idx, new_selection_end_x)

                self.cursor_y, self.cursor_x = self.selection_end

                self.history.add_action({
                    "type": "block_indent",
                    "changes": undo_changes_list,
                    "indent_str_used": indent_string,
                    "start_y": start_y_idx,
                    "end_y": end_y_idx,
                    "selection_before": original_selection_tuple[1:],  # Сохраняем (start_coords, end_coords)
                    "cursor_before_no_selection": None,  # Так как выделение всегда есть
                    "selection_after": (self.is_selecting, self.selection_start, self.selection_end),
                    "cursor_after_no_selection": None  # Так как выделение остается
                })
                self._set_status_message(f"Indented {indented_line_count} line(s)")
                logging.debug(
                    f"Block indent: {indented_line_count} lines from {start_y_idx}-{end_y_idx} "
                    f"indented by '{indent_string}'. New selection: {self.selection_start} -> {self.selection_end}"
                )
                return True
            else:
                if self.status_message == original_status:
                    self._set_status_message(
                        "No lines selected for indent operation.")  # Или "Nothing to indent in selection"
                return self.status_message != original_status
        # Default return if somehow lock isn't acquired or other paths missed
        return False

    # 2.
    def handle_block_unindent(self) -> bool:
        """
        Decreases indentation for all lines within the current selection.
        Updates selection, cursor, modified status, and action history.

        Returns:
            bool: True if any lines were unindented or if the status message changed,
                  False otherwise (e.g., no selection or nothing to unindent).
        """
        if not self.is_selecting or not self.selection_start or not self.selection_end:
            self._set_status_message("No selection to unindent.")
            return True  # Status message changed

        original_status = self.status_message
        original_selection_tuple = (self.is_selecting, self.selection_start, self.selection_end)

        made_actual_text_change = False

        with self._state_lock:
            norm_range = self._get_normalized_selection_range()
            if not norm_range:
                logging.warning(
                    "handle_block_unindent: Could not get normalized selection range despite active selection.")
                self._set_status_message("Selection error during unindent.")
                return True

            start_coords, end_coords = norm_range
            start_y_idx, start_x_in_line_sel = start_coords
            end_y_idx, end_x_in_line_sel = end_coords

            tab_size = self.config.get("editor", {}).get("tab_size", 4)
            use_spaces = self.config.get("editor", {}).get("use_spaces", True)
            # Number of characters to attempt to remove for unindentation
            unindent_char_count_to_try = tab_size if use_spaces else 1

            undo_changes_list: List[Dict[str, Any]] = []
            unindented_line_count = 0

            # Store characters actually removed per line for accurate cursor/selection adjustment
            chars_removed_from_sel_start_line = 0
            chars_removed_from_sel_end_line = 0

            for current_y in range(start_y_idx, end_y_idx + 1):
                if current_y >= len(self.text):
                    continue

                original_line_content = self.text[current_y]
                line_to_modify = self.text[current_y]
                prefix_that_was_removed = ""

                if use_spaces:
                    actual_spaces_to_remove = 0
                    for i in range(min(len(line_to_modify), unindent_char_count_to_try)):
                        if line_to_modify[i] == ' ':
                            actual_spaces_to_remove += 1
                        else:
                            break
                    if actual_spaces_to_remove > 0:
                        prefix_that_was_removed = line_to_modify[:actual_spaces_to_remove]
                        self.text[current_y] = line_to_modify[actual_spaces_to_remove:]
                else:  # use_tabs
                    if line_to_modify.startswith('\t'):
                        prefix_that_was_removed = '\t'
                        self.text[current_y] = line_to_modify[1:]

                if prefix_that_was_removed:
                    undo_changes_list.append({
                        "line_index": current_y,
                        "original_text": original_line_content,
                        "new_text": self.text[current_y]
                    })
                    unindented_line_count += 1
                    made_actual_text_change = True
                    if current_y == start_y_idx:
                        chars_removed_from_sel_start_line = len(prefix_that_was_removed)
                    if current_y == end_y_idx:  # Could be same as start_y_idx
                        chars_removed_from_sel_end_line = len(prefix_that_was_removed)

            if made_actual_text_change:
                self.modified = True

                new_selection_start_x = max(0, start_x_in_line_sel - chars_removed_from_sel_start_line)
                new_selection_end_x = max(0, end_x_in_line_sel - chars_removed_from_sel_end_line)

                self.selection_start = (start_y_idx, new_selection_start_x)
                self.selection_end = (end_y_idx, new_selection_end_x)

                self.cursor_y, self.cursor_x = self.selection_end

                self.history.add_action({
                    "type": "block_unindent",  # Specific type for undo/redo
                    "changes": undo_changes_list,
                    # "unindent_str_len_map": {y: len_removed for y, len_removed in ...} # Optional, if redo needs it
                    "start_y": start_y_idx, "end_y": end_y_idx,
                    "selection_before": original_selection_tuple[1:],
                    "cursor_before_no_selection": None,
                    "selection_after": (self.is_selecting, self.selection_start, self.selection_end),
                    "cursor_after_no_selection": None
                })
                self._set_status_message(f"Unindented {unindented_line_count} line(s)")
                logging.debug(
                    f"Block unindent: {unindented_line_count} lines from {start_y_idx}-{end_y_idx} unindented. "
                    f"New selection: {self.selection_start} -> {self.selection_end}"
                )
                return True
            else:
                if self.status_message == original_status:
                    self._set_status_message("Nothing to unindent in selection.")
                return self.status_message != original_status

    # 3.
    def unindent_current_line(self) -> bool:
        """
        Decreases the indentation of the current line if there is no active selection.

        This method attempts to unindent the current line by removing either a configured
        number of leading spaces or a single tab character, depending on editor settings.
        If successful, the change is recorded in the undo history, the modified flag is set,
        and the status message is updated. If no unindentation is possible, an appropriate
        status message is set. This operation does nothing if there is an active selection.

        Returns:
            bool: True if the line was unindented or the status message changed (requiring a redraw),
                False otherwise.

        Side Effects:
            - Modifies the text buffer if unindentation occurs.
            - Updates cursor position and editor modified state.
            - Records the change in the undo history.
            - Updates the status message.

        Notes:
            This method is intended for single-line unindent only. For block unindent, see
            handle_smart_unindent or handle_block_unindent.
        """
        if self.is_selecting:
            # This action is intended for when there's no selection.
            # Block unindent is handled by handle_smart_unindent -> handle_block_unindent.
            return False

        original_status = self.status_message
        original_line_content = ""
        original_cursor_pos = (self.cursor_y, self.cursor_x)  # For history and change detection
        # made_text_change = False # <--- УДАЛЕНО

        with self._state_lock:
            current_y = self.cursor_y
            if current_y >= len(self.text):
                logging.warning(f"unindent_current_line: cursor_y {current_y} out of bounds.")
                return False

            original_line_content = self.text[current_y]  # Save for undo
            line_to_modify = self.text[current_y]

            if not line_to_modify or not (line_to_modify.startswith(' ') or line_to_modify.startswith('\t')):
                self._set_status_message("Nothing to unindent at line start.")
                return self.status_message != original_status

            tab_size = self.config.get("editor", {}).get("tab_size", 4)
            use_spaces = self.config.get("editor", {}).get("use_spaces", True)
            unindent_char_count_to_try = tab_size if use_spaces else 1

            chars_removed_from_line = 0

            if use_spaces:
                actual_spaces_to_remove = 0
                for i in range(min(len(line_to_modify), unindent_char_count_to_try)):
                    if line_to_modify[i] == ' ':
                        actual_spaces_to_remove += 1
                    else:
                        break
                if actual_spaces_to_remove > 0:
                    self.text[current_y] = line_to_modify[actual_spaces_to_remove:]
                    chars_removed_from_line = actual_spaces_to_remove
            else:  # use_tabs
                if line_to_modify.startswith('\t'):
                    self.text[current_y] = line_to_modify[1:]
                    chars_removed_from_line = 1

            if chars_removed_from_line > 0:
                self.modified = True
                # Adjust cursor: move left by the number of characters removed, but not before column 0
                self.cursor_x = max(0, self.cursor_x - chars_removed_from_line)

                self.history.add_action({
                    "type": "block_unindent",  # Re-use for consistency with undo/redo logic
                    "changes": [{
                        "line_index": current_y,
                        "original_text": original_line_content,
                        "new_text": self.text[current_y]
                    }],
                    "selection_before": None,  # No selection was active
                    "cursor_before_no_selection": original_cursor_pos,
                    "selection_after": None,
                    "cursor_after_no_selection": (self.cursor_y, self.cursor_x)
                })
                self._set_status_message("Line unindented.")
                logging.debug(
                    f"Unindented line {current_y}. Removed {chars_removed_from_line} char(s). Cursor at {self.cursor_x}")
                return True
            else:
                if self.status_message == original_status:
                    self._set_status_message("Nothing effectively unindented on current line.")
                return self.status_message != original_status

    # 4.  ───────────────────── Commenting lines ─────────────────────
    def comment_lines(self, start_y: int, end_y: int, comment_prefix: str) -> bool:
        """
        Comments a range of lines by prepending the comment_prefix.
        Avoids double-commenting if a line already starts with the prefix (after indent).
        Updates action history and modified status.

        Args:
            start_y (int): The starting line index (0-based).
            end_y (int): The ending line index (0-based).
            comment_prefix (str): The comment prefix to add (e.g., "// ", "# ").

        Returns:
            bool: True if any lines were actually commented, False otherwise.
                  (Status message changes are handled separately by the caller or here).
        """
        made_actual_text_change = False
        original_status = self.status_message  # To check if only status changes

        # Store original selection/cursor states for action history
        original_selection_tuple = (self.is_selecting, self.selection_start, self.selection_end)
        original_cursor_tuple = (self.cursor_y, self.cursor_x)  # if not selecting

        with self._state_lock:
            undo_changes_list: List[Dict[str, Any]] = []
            min_indent = float('inf')
            non_empty_lines_in_block_indices = []

            # First pass: determine minimum indentation of non-empty lines in the block
            for y_scan in range(start_y, end_y + 1):
                if y_scan >= len(self.text):
                    continue
                line_content_scan = self.text[y_scan]
                if line_content_scan.strip():  # If line is not blank
                    non_empty_lines_in_block_indices.append(y_scan)
                    indent_len = len(line_content_scan) - len(line_content_scan.lstrip())
                    min_indent = min(min_indent, indent_len)

            if not non_empty_lines_in_block_indices:  # All lines in selection are blank or whitespace
                min_indent = 0  # Add comment at the beginning of whitespace lines or col 0 for empty

            lines_actually_commented_count = 0

            # Store original texts before modification for undo
            original_texts_map = {
                y_iter: self.text[y_iter] for y_iter in range(start_y, end_y + 1) if y_iter < len(self.text)
            }

            for y_iter in range(start_y, end_y + 1):
                if y_iter >= len(self.text):
                    continue

                line_content_to_modify = self.text[y_iter]

                # Determine insertion position for the comment prefix
                # For non-blank lines: at min_indent
                # For blank/whitespace-only lines: at the start of non-space whitespace, or col 0
                insert_pos: int
                is_blank_line = not line_content_to_modify.strip()

                if is_blank_line:
                    # For blank lines, find first non-space char (e.g. tab) or end of string
                    first_non_space = 0
                    for i, char_in_line in enumerate(line_content_to_modify):
                        if char_in_line != ' ':
                            first_non_space = i
                            break
                    else:  # Line is all spaces or empty
                        first_non_space = len(line_content_to_modify)
                    insert_pos = first_non_space
                else:  # Non-blank line
                    insert_pos = int(min_indent)  # Ensure min_indent is int if not float('inf')

                # --- Check if already commented with the exact same prefix at insert_pos ---
                # This check needs to be robust.
                # We check if line[insert_pos:] starts with comment_prefix.
                already_commented = False
                if len(line_content_to_modify) >= insert_pos + len(comment_prefix):
                    if line_content_to_modify[insert_pos:].startswith(comment_prefix):
                        already_commented = True
                        logging.debug(f"Line {y_iter + 1} already commented with '{comment_prefix}', skipping.")

                if not already_commented:
                    self.text[y_iter] = (line_content_to_modify[:insert_pos] +
                                         comment_prefix +
                                         line_content_to_modify[insert_pos:])

                    undo_changes_list.append({
                        "line_index": y_iter,
                        "original_text": original_texts_map.get(y_iter, line_content_to_modify),
                        "new_text": self.text[y_iter]
                    })
                    lines_actually_commented_count += 1
                    made_actual_text_change = True

            if made_actual_text_change:
                self.modified = True

                # Adjust selection and cursor
                # If selection was active, its x-coordinates might shift by len(comment_prefix)
                # if the comment was inserted before or within the selection's x-range on those lines.
                if self.is_selecting and self.selection_start and self.selection_end:
                    s_y, s_x = self.selection_start
                    e_y, e_x = self.selection_end

                    # A simple shift if the comment was added at or before the selection start on the line
                    new_s_x = s_x
                    if s_y >= start_y and s_y <= end_y and insert_pos <= s_x:  # Check if insert_pos is defined if loop didn't run
                        new_s_x = s_x + len(comment_prefix)

                    new_e_x = e_x
                    if e_y >= start_y and e_y <= end_y and insert_pos <= e_x:
                        new_e_x = e_x + len(comment_prefix)

                    self.selection_start = (s_y, new_s_x)
                    self.selection_end = (e_y, new_e_x)
                    self.cursor_y, self.cursor_x = self.selection_end
                elif not self.is_selecting:  # Single line comment at cursor_y
                    if self.cursor_y >= start_y and self.cursor_y <= end_y and insert_pos <= self.cursor_x:
                        self.cursor_x += len(comment_prefix)

                self.history.add_action({
                    "type": "comment_block",  # Use a specific type
                    "changes": undo_changes_list,
                    "comment_prefix": comment_prefix,  # Store for redo/context
                    "start_y": start_y, "end_y": end_y,
                    "selection_before": original_selection_tuple[1:],  # (start_coords, end_coords)
                    "cursor_before_no_selection": original_cursor_tuple if not original_selection_tuple[0] else None,
                    "selection_after": (self.is_selecting, self.selection_start, self.selection_end),
                    "cursor_after_no_selection": (self.cursor_y, self.cursor_x) if not self.is_selecting else None
                })
                self._set_status_message(f"Commented {lines_actually_commented_count} line(s)")
                return True
            else:
                # No lines were actually commented (e.g., all were already commented)
                if self.status_message == original_status:
                    self._set_status_message("Selected lines already commented.")
                return self.status_message != original_status

    # 5.  ───────────────────── Uncommenting  lines ─────────────────────
    # Note: This method is used to uncomment lines that were previously commented with the same prefix.
    def uncomment_lines(self, start_y: int, end_y: int, comment_prefix: str) -> bool:
        """
        Uncomments a range of lines by removing the specified comment_prefix.
        Updates action history and modified status.

        Args:
            start_y (int): The starting line index (0-based).
            end_y (int): The ending line index (0-based).
            comment_prefix (str): The comment prefix to remove (e.g., "// ", "# ").

        Returns:
            bool: True if any lines were actually unindented, False otherwise.
                  (Status message changes are handled by the caller or set here and will
                   trigger redraw via main loop's status check).
        """
        made_actual_text_change = False
        original_status = self.status_message  # To check if only status changes

        # Store original selection/cursor states for action history
        original_selection_tuple = (self.is_selecting, self.selection_start, self.selection_end)
        original_cursor_tuple = (self.cursor_y, self.cursor_x)

        with self._state_lock:
            undo_changes_list = []
            prefix_to_remove_stripped = comment_prefix.strip()

            # For adjusting selection, track how much was removed from start/end lines of selection
            chars_removed_from_sel_start_line = 0
            chars_removed_from_sel_end_line = 0

            for y_iter in range(start_y, end_y + 1):
                if y_iter >= len(self.text):
                    continue

                original_line_text = self.text[y_iter]
                line_to_modify = self.text[y_iter]
                current_line_unindented = False

                # Determine leading whitespace
                leading_whitespace = ""
                for char_idx, char_val in enumerate(line_to_modify):
                    if char_val.isspace():
                        leading_whitespace += char_val
                    else:
                        break

                content_after_indent = line_to_modify[len(leading_whitespace):]
                prefix_actually_removed_len = 0

                if content_after_indent.startswith(
                        comment_prefix):  # Exact prefix match (including trailing space if any)
                    self.text[y_iter] = leading_whitespace + content_after_indent[len(comment_prefix):]
                    prefix_actually_removed_len = len(comment_prefix)
                    current_line_unindented = True
                elif content_after_indent.startswith(prefix_to_remove_stripped):
                    # If exact prefix (with space) didn't match, try stripped prefix.
                    # This handles cases where user might have `comment_prefix = "# "` but line is `#comment`
                    # or `comment_prefix = "#"` and line is `# comment`
                    # We should only remove the stripped prefix then.
                    # Check if there's a space after the stripped prefix that should also be removed
                    # if the original comment_prefix had a trailing space.
                    len_stripped = len(prefix_to_remove_stripped)
                    if comment_prefix.endswith(' ') and \
                            len(content_after_indent) > len_stripped and \
                            content_after_indent[len_stripped] == ' ':
                        self.text[y_iter] = leading_whitespace + content_after_indent[len_stripped + 1:]
                        prefix_actually_removed_len = len_stripped + 1
                    else:
                        self.text[y_iter] = leading_whitespace + content_after_indent[len_stripped:]
                        prefix_actually_removed_len = len_stripped
                    current_line_unindented = True

                if current_line_unindented:
                    made_actual_text_change = True
                    undo_changes_list.append({
                        "line_index": y_iter,
                        "original_text": original_line_text,
                        "new_text": self.text[y_iter]
                    })
                    if y_iter == original_selection_tuple[1][0] if original_selection_tuple[
                        1] else -1:  # start_y of selection
                        chars_removed_from_sel_start_line = prefix_actually_removed_len
                    if y_iter == original_selection_tuple[2][0] if original_selection_tuple[
                        2] else -1:  # end_y of selection
                        chars_removed_from_sel_end_line = prefix_actually_removed_len

            if made_actual_text_change:
                self.modified = True

                # Adjust selection and cursor
                if self.is_selecting and self.selection_start and self.selection_end:
                    s_y, s_x = self.selection_start
                    e_y, e_x = self.selection_end
                    # Adjust based on characters removed from the specific lines of selection start/end
                    self.selection_start = (s_y, max(0, s_x - chars_removed_from_sel_start_line))
                    self.selection_end = (e_y, max(0, e_x - chars_removed_from_sel_end_line))
                    self.cursor_y, self.cursor_x = self.selection_end
                elif not self.is_selecting:  # If it was a single line unindent without selection
                    # current_y should be self.cursor_y here
                    self.cursor_x = max(0, self.cursor_x - chars_removed_from_sel_start_line)  # Assuming single line op

                self.history.add_action({
                    "type": "uncomment_block",  # Or "block_unindent" if separating logic
                    "changes": undo_changes_list,
                    "comment_prefix": comment_prefix,  # Store for context if needed by redo
                    "start_y": start_y, "end_y": end_y,
                    "selection_before": original_selection_tuple[1:],  # (start_coords, end_coords)
                    "cursor_before_no_selection": original_cursor_tuple if not original_selection_tuple[0] else None,
                    "selection_after": (self.is_selecting, self.selection_start, self.selection_end),
                    "cursor_after_no_selection": (self.cursor_y, self.cursor_x) if not self.is_selecting else None
                })
                self._set_status_message(f"Uncommented {len(undo_changes_list)} line(s)")
                return True  # Indicates actual text change
            else:
                self._set_status_message(
                    f"Nothing to uncomment in lines {start_y + 1}-{end_y + 1} with prefix '{comment_prefix}'")
                return self.status_message != original_status  # True if status changed


    def get_char_width(self, char):
        """
        Calculates the display width of a character using wcwidth.
        Returns 1 for control characters or characters with ambiguous width (-1).
        Uses unicodedata to check if it's a control character.
        """
        if not isinstance(char, str) or len(char) != 1:
            return 1  # Неожиданный ввод, считаем ширину 1

        # Проверка на управляющие символы (кроме известных типа Tab, Enter)
        if unicodedata.category(char) in ('Cc', 'Cf'):  # Cc: Control, Cf: Format
            # Здесь можно добавить исключения для символов, которые хотим отображать (например, '\t')
            if char == '\t':
                # Ширина табуляции зависит от позиции курсора и tab_size,
                # но wcwidth('\t') обычно 0 или 1.
                # Для отрисовки Pygments токенов лучше вернуть wcwidth или 1.
                # Реальная отрисовка табов происходит в DrawScreen.
                width = wcwidth(char)
                return width if width >= 0 else 1
            return 0  # Управляющие символы обычно не отображаются и имеют 0 ширину
        # Проверка на символы нулевой ширины (например, диакритика)
        if unicodedata.combining(char):
            return 0  # Объединяющиеся символы имеют нулевую ширину

        width = wcwidth(char)
        # wcwidth возвращает -1 для символов, для которых ширина неопределена,
        # или 0 для символов нулевой ширины (которые мы уже обработали).
        # Для -1 или 0 (если не объединяющийся), возвращаем 1, чтобы курсор двигался.
        # Если wcwidth вернул >=0, возвращаем его.
        return width if width >= 0 else 1

    def get_string_width(self, text):
        """
        Calculates the display width of a string using wcswidth.
        Handles potential errors by summing individual character widths.
        """
        if not isinstance(text, str):
            logging.warning(f"get_string_width received non-string input: {type(text)}")
            return 0

        try:
            width = wcswidth(text)
            if width >= 0:
                return width
        except Exception as e:
            logging.warning(f"wcswidth failed for '{text[:20]}...': {e}. Falling back to char sum.")
            pass  # Fallback

        # Fallback: суммируем ширину каждого символа
        total_width = 0
        for char in text:
            total_width += self.get_char_width(char)
        return total_width

    def safe_open(self, filename: str, mode: str = "r", encoding: str | None = None, errors: str = "replace"):
        """
        Safely open a file in the given mode.
        • In binary mode, ignore encoding and errors.
        • In text mode, use the given encoding (or self.encoding) and the given errors policy.
        """
        try:
            if "b" in mode:
                # Binary mode: no encoding or errors
                return open(filename, mode)
            else:
                # Text mode: use specified or default encoding, with given errors policy
                return open(
                    filename,
                    mode,
                    encoding=encoding or self.encoding,
                    errors=errors,
                )
        except Exception as e:
            logging.error(f"Failed to safe_open file {filename!r} in mode {mode!r}: {e}")
            raise

    # =============== Open file ============================
    def open_file(self, filename_to_open: Optional[str] = None) -> bool:
        """
        Opens a specified file or prompts for one.
        Handles unsaved changes in the current buffer before proceeding.
        Detects file encoding, loads content, and updates editor state.

        Args:
            filename_to_open (Optional[str]): The path to the file to open.
                                             If None, the user will be prompted.

        Returns:
            bool: True if the editor's state changed significantly (new file loaded,
                  status message updated, prompt interaction occurred) requiring a redraw,
                  False otherwise (e.g., operation fully cancelled without status change).
        """
        logging.debug(f"open_file called. Requested filename: '{filename_to_open}'")

        # Store initial states to determine if a redraw is ultimately needed
        original_status = self.status_message
        original_filename_for_revert = self.filename
        original_text_tuple_for_revert = tuple(self.text)
        original_modified_flag_for_revert = self.modified

        status_changed_by_interaction = False

        try:
            # 1. Handle unsaved changes in the current buffer
            if self.modified:
                status_before_save_prompt = self.status_message
                ans = self.prompt("Current file has unsaved changes. Save now? (y/n): ")
                if self.status_message != status_before_save_prompt:
                    status_changed_by_interaction = True

                if ans and ans.lower().startswith("y"):
                    self.save_file()
                    if self.modified:
                        self._set_status_message("Open file cancelled: current file changes were not saved.")
                        logging.warning(
                            "Open file aborted: User chose to save, but 'save_file' did not clear 'modified' flag.")
                        return True
                elif ans and ans.lower().startswith("n"):
                    logging.info("Open file: User chose NOT to save current changes. Discarding them.")
                    self.modified = False
                else:
                    if not status_changed_by_interaction and self.status_message == original_status:
                        self._set_status_message("Open file cancelled by user at save prompt.")
                    logging.debug("Open file cancelled by user at 'save changes' prompt.")
                    return self.status_message != original_status or status_changed_by_interaction

            # 2. Determine the filename to open
            actual_filename_to_open = filename_to_open
            if not actual_filename_to_open:
                status_before_open_prompt = self.status_message
                actual_filename_to_open = self.prompt("Enter file name to open: ")
                if self.status_message != status_before_open_prompt:
                    status_changed_by_interaction = True

            if not actual_filename_to_open:
                if not status_changed_by_interaction and self.status_message == original_status:
                    self._set_status_message("Open file cancelled: no filename provided.")
                logging.debug("Open file cancelled: no filename provided by user.")
                return self.status_message != original_status or status_changed_by_interaction

            # 3. Validate filename and permissions
            if not self.validate_filename(actual_filename_to_open):
                # validate_filename sets its own status message
                return True  # Status changed

            if not os.path.exists(actual_filename_to_open):
                self.text = [""]
                self.filename = None
                self.modified = False
                self.encoding = "utf-8"
                self.history.clear() 
                self.history.add_action({
                    "type": "open_file_missing",
                    "attempted_path": actual_filename_to_open,
                    "content": [""],
                    "encoding": "utf-8"
                })
                self.set_initial_cursor_position()
                self._set_status_message(f"Error: File not found '{os.path.basename(actual_filename_to_open)}'")
                logging.warning(f"Open file failed: file not found at '{actual_filename_to_open}'")
                return True

            if os.path.isdir(actual_filename_to_open):
                self._set_status_message(f"Error: '{os.path.basename(actual_filename_to_open)}' is a directory.")
                logging.warning(f"Open file failed: path '{actual_filename_to_open}' is a directory.")
                return True

            if not os.access(actual_filename_to_open, os.R_OK):
                self._set_status_message(
                    f"Error: No read permissions for '{os.path.basename(actual_filename_to_open)}'.")
                logging.warning(f"Open file failed: no read permissions for '{actual_filename_to_open}'.")
                return True

            # 4. Detect file encoding and read content
            lines: Optional[List[str]] = None
            final_encoding_used: str = "utf-8"  # Default if all else fails

            try:
                sample_size_for_chardet = 1024 * 20
                raw_data_sample: bytes
                with self.safe_open(actual_filename_to_open, mode="rb") as f_binary:
                    raw_data_sample = f_binary.read(sample_size_for_chardet)

                if not raw_data_sample:
                    logging.info(f"File '{actual_filename_to_open}' is empty or could not be read for chardet.")
                    lines = [""]
                    final_encoding_used = self.encoding
                else:
                    chardet_result = chardet.detect(raw_data_sample)
                    encoding_guess = chardet_result.get("encoding")
                    confidence = chardet_result.get("confidence", 0.0)
                    logging.debug(
                        f"Chardet detected encoding '{encoding_guess}' with confidence {confidence:.2f} "
                        f"for '{actual_filename_to_open}'."
                    )

                    encodings_to_try_ordered: List[Tuple[Optional[str], str]] = []
                    if encoding_guess and confidence >= 0.75:
                        encodings_to_try_ordered.append((encoding_guess, "strict"))

                    # Add common fallbacks, ensuring UTF-8 is prominent
                    common_fallbacks = [("utf-8", "strict"), ("latin-1", "strict")]
                    if encoding_guess and (encoding_guess, "replace") not in encodings_to_try_ordered:
                        # Try detected encoding with 'replace' if strict fails for it or if confidence was low
                        if not (encoding_guess and confidence >= 0.75):
                            encodings_to_try_ordered.append((encoding_guess, "replace"))

                    for enc_fb, err_fb in common_fallbacks:
                        if (enc_fb, err_fb) not in encodings_to_try_ordered:
                            encodings_to_try_ordered.append((enc_fb, err_fb))

                    # Final absolute fallback
                    if ("utf-8", "replace") not in encodings_to_try_ordered:
                        encodings_to_try_ordered.append(("utf-8", "replace"))

                    seen_enc_err_pairs = set()
                    unique_encodings_to_try = []
                    for enc, err_handling in encodings_to_try_ordered:
                        if enc and (enc, err_handling) not in seen_enc_err_pairs:  # Ensure enc is not None
                            unique_encodings_to_try.append((enc, err_handling))
                            seen_enc_err_pairs.add((enc, err_handling))
                        elif not enc and ("utf-8",
                                          err_handling) not in seen_enc_err_pairs:  # If chardet returns None for encoding
                            unique_encodings_to_try.append(("utf-8", err_handling))  # Default to utf-8
                            seen_enc_err_pairs.add(("utf-8", err_handling))

                    for enc_attempt, error_policy in unique_encodings_to_try:
                        try:
                            logging.debug(
                                f"Attempting to read '{actual_filename_to_open}' with encoding '{enc_attempt}' (errors='{error_policy}')")
                            with self.safe_open(actual_filename_to_open, "r", encoding=enc_attempt,
                                                errors=error_policy) as f_text:
                                lines = f_text.read().splitlines()
                            final_encoding_used = enc_attempt if enc_attempt else "utf-8"
                            logging.info(
                                f"Successfully read '{actual_filename_to_open}' using encoding '{final_encoding_used}' with errors='{error_policy}'.")
                            break
                        except (UnicodeDecodeError, OSError, LookupError) as e_read:
                            logging.warning(
                                f"Failed to read '{actual_filename_to_open}' with encoding '{enc_attempt}' (errors='{error_policy}'): {e_read}")

                if lines is None:
                    self._set_status_message(
                        f"Error reading '{os.path.basename(actual_filename_to_open)}': Could not decode content.")
                    logging.error(f"All attempts to read and decode '{actual_filename_to_open}' failed.")
                    return True

            except Exception as e_detect_read:
                self._set_status_message(
                    f"Error during file processing for '{os.path.basename(actual_filename_to_open)}': {e_detect_read}")
                logging.exception(f"Failed during encoding detection or initial read for '{actual_filename_to_open}'")
                return True

            self.text = lines if lines is not None else [""]
            self.filename = actual_filename_to_open
            self.modified = False
            self.encoding = final_encoding_used

            self.set_initial_cursor_position()
            self.history.clear() 

            self._set_status_message(
                f"Opened '{os.path.basename(self.filename)}' (enc: {self.encoding}, {len(self.text)} lines)"
            )
            logging.info(
                f"File opened successfully: '{self.filename}', Encoding: {self.encoding}, Lines: {len(self.text)}"
            )

            self._lexer = None
            self.detect_language()
            self.git.update_git_info()

            return True

        except Exception as e_outer:
            self._set_status_message(f"Error opening file: {str(e_outer)[:70]}...")
            logging.exception(f"Unexpected error during open_file process for: {filename_to_open}")
            # Attempt to restore some semblance of original state if open failed badly
            self.filename = original_filename_for_revert
            self.text = list(original_text_tuple_for_revert)
            self.modified = original_modified_flag_for_revert
            # Could also try to restore lexer, cursor, scroll but it gets complex.
            # A full redraw with the error message is the main goal.
            return True

    # --- SAVE_FILE ---------------------------------------------
    def save_file(self) -> bool:
        """
        Saves the current document to its existing filename.
        If the filename is not set (e.g., for a new, unsaved buffer),
        this method invokes `save_file_as()` to prompt the user for a name.
        Updates editor state (modified status, potentially Git info, language detection).

        Returns:
            bool: True if the operation resulted in a change to the editor's state
                  (e.g., modified status changed, status message updated, or if
                  `save_file_as` was called and made changes), False otherwise.
        """
        logging.debug("save_file called")

        # Store initial state for comparison
        original_status = self.status_message
        original_modified_flag = self.modified
        # Filename should not change in a direct save, unless save_file_as is called
        original_filename = self.filename

        redraw_is_needed = False

        # 1. If no filename is set, delegate to save_file_as()
        if not self.filename or self.filename == "noname":
            logging.debug("save_file: Filename not set, invoking save_file_as().")
            # save_file_as() returns True if it made changes requiring a redraw
            return self.save_file_as()

            # 2. Validate existing filename and permissions (precautionary)
        # These checks are more critical for save_file_as, but good for robustness here too.
        if not self.validate_filename(self.filename):
            # validate_filename calls _set_status_message
            return True  # Status changed by validate_filename

        if os.path.isdir(self.filename):
            self._set_status_message(f"Cannot save: '{os.path.basename(self.filename)}' is a directory.")
            return True  # Status changed

        # Check for write permissions on the file itself if it exists,
        # or on its parent directory if it doesn't (though save usually implies it exists or can be created).
        target_path_exists = os.path.exists(self.filename)
        can_write = False
        if target_path_exists:
            if os.access(self.filename, os.W_OK):
                can_write = True
        else:  # File doesn't exist yet, check parent directory
            parent_dir = os.path.dirname(self.filename) or '.'  # Use current dir if no path part
            if os.access(parent_dir, os.W_OK):
                can_write = True

        if not can_write:
            self._set_status_message(f"No write permissions for '{os.path.basename(self.filename)}' or its directory.")
            return True  # Status changed

        # 3. Attempt to write the file to the existing path
        try:
            # _write_file is the low-level write operation.
            # It updates self.modified to False and calls detect_language/update_git_info.
            # It does not set a "Saved" status message itself.
            self._write_file(self.filename)

            # After successful _write_file:
            # self.filename is unchanged (unless _write_file unexpectedly changes it, which it shouldn't for 'save')
            # self.modified should be False

            self._set_status_message(f"Saved to {os.path.basename(self.filename)}")

            # Determine if a redraw is needed based on actual state changes
            if (self.modified != original_modified_flag or  # Typically True -> False
                    self.status_message != original_status or  # "Saved to..." is new
                    self.filename != original_filename):  # Should not change here but check
                redraw_is_needed = True

            return redraw_is_needed

        except Exception as e_write:  # Catch errors specifically from _write_file
            self._set_status_message(f"Error saving file '{os.path.basename(self.filename)}': {str(e_write)[:60]}...")
            logging.error(f"Failed to write file during Save '{self.filename}': {e_write}", exc_info=True)
            # self.modified might remain True if save failed
            return True  # Status message changed due to error

    def save_file_as(self) -> bool:
        """
        Saves the current document content to a new file name specified by the user.
        Handles prompts for the new filename and overwrite confirmation if the file exists.
        Updates editor state (filename, modified status, language detection, Git info).

        Returns:
            bool: True if the operation resulted in a change to the editor's state
                  (e.g., filename changed, modified status changed, status message updated,
                  or a redraw is needed due to prompt interactions), False otherwise
                  (e.g., if the operation was cancelled very early without any status change).
        """
        logging.debug("save_file_as called")

        # Store initial state for comparison to determine if a redraw is truly needed
        original_status = self.status_message
        original_filename = self.filename
        original_modified_flag = self.modified
        # Other states like cursor/scroll usually don't change directly from save_as,
        # but filename and modified status will.

        redraw_is_needed = False  # Accumulator for redraw reasons

        # Determine a default name for the prompt
        default_name_for_prompt = self.filename if self.filename and self.filename != "noname" \
            else self.config.get("editor", {}).get("default_new_filename", "new_file.txt")

        # 1. Prompt for the new filename
        status_before_filename_prompt = self.status_message
        new_filename_input = self.prompt(f"Save file as ({default_name_for_prompt}): ")
        if self.status_message != status_before_filename_prompt:
            redraw_is_needed = True  # Prompt interaction itself changed the status line

        if not new_filename_input:  # User cancelled (Esc or empty Enter)
            if not redraw_is_needed and self.status_message == original_status:  # Only set if prompt didn't change status
                self._set_status_message("Save as cancelled")
            return True  # Status changed by prompt or by this cancellation message

        # Use provided name or default if input was just whitespace
        new_filename_processed = new_filename_input.strip() or default_name_for_prompt

        # 2. Validate the new filename
        if not self.validate_filename(new_filename_processed):
            # validate_filename already calls _set_status_message with error
            return True  # Status was changed by validate_filename

        if os.path.isdir(new_filename_processed):
            self._set_status_message(f"Cannot save: '{os.path.basename(new_filename_processed)}' is a directory.")
            return True  # Status changed

        # 3. Handle existing file and permissions
        if os.path.exists(new_filename_processed):
            if not os.access(new_filename_processed, os.W_OK):
                self._set_status_message(
                    f"No write permissions for existing file: '{os.path.basename(new_filename_processed)}'")
                return True  # Status changed

            status_before_overwrite_prompt = self.status_message
            overwrite_choice = self.prompt(
                f"File '{os.path.basename(new_filename_processed)}' already exists. Overwrite? (y/n): ")
            if self.status_message != status_before_overwrite_prompt:
                redraw_is_needed = True

            if not overwrite_choice or overwrite_choice.lower() != 'y':
                if not redraw_is_needed and self.status_message == original_status:
                    self._set_status_message("Save as cancelled (file exists, not overwritten).")
                return True  # Status changed by prompt or cancellation
        else:
            # File does not exist, check if directory needs to be created
            target_dir = os.path.dirname(new_filename_processed)
            if target_dir and not os.path.exists(target_dir):  # If target_dir is empty, it's the current dir
                try:
                    os.makedirs(target_dir, exist_ok=True)
                    logging.info(f"Created missing directory for save as: {target_dir}")
                except Exception as e_mkdir:
                    self._set_status_message(f"Cannot create directory '{target_dir}': {e_mkdir}")
                    logging.error(f"Failed to create directory '{target_dir}': {e_mkdir}")
                    return True  # Status changed

            # Check write permissions for the target directory (or current if target_dir is empty)
            effective_target_dir = target_dir if target_dir else '.'
            if not os.access(effective_target_dir, os.W_OK):
                self._set_status_message(f"No write permissions for directory: '{effective_target_dir}'")
                return True  # Status changed

        # 4. Attempt to write the file
        try:
            # _write_file updates self.filename, self.modified, calls detect_language, update_git_info.
            # It does not set a status message itself, allowing this method to do so.
            self._write_file(new_filename_processed)

            # After successful _write_file:
            # self.filename is new_filename_processed
            # self.modified is False
            # self._lexer might have changed

            # toggle_auto_save might be called here if it's relevant after a save_as
            # If so, it might change status and redraw_is_needed should be True.
            # self.toggle_auto_save()

            self._set_status_message(f"Saved as {os.path.basename(new_filename_processed)}")

            # Check if any key state changed that would require a redraw beyond just status.
            # Filename change is significant. Modified flag change is also significant.
            if (self.filename != original_filename or
                    self.modified != original_modified_flag or
                    self.status_message != original_status):  # This will always be true due to set_status_message above
                redraw_is_needed = True

            return True  # Always true because status message is set and state changes.

        except Exception as e_write:  # Catch errors from _write_file
            self._set_status_message(
                f"Error saving file as '{os.path.basename(new_filename_processed)}': {str(e_write)[:60]}...")
            logging.error(f"Failed to write file during Save As '{new_filename_processed}': {e_write}", exc_info=True)
            # Restore original filename and modified status if save_as failed mid-way
            # (e.g., if _write_file partially updated them before failing)
            # This is tricky, _write_file should ideally be atomic or handle its own partial failure state.
            # For now, we assume if _write_file fails, self.filename might not have been updated yet.
            if self.filename == new_filename_processed:  # If _write_file updated filename before error
                self.filename = original_filename  # Try to revert
                self.modified = original_modified_flag  # Revert modified status
            return True  # Status message changed due to error

    # Метод _write_file является низкоуровневой операцией, предназначенной для фактической записи
    # содержимого в файл и обновления связанного с этим состояния редактора.
    def _write_file(self, target_filename: str) -> None:  # Changed return type to None
        """
        Low-level method to write the current buffer content to the specified target file.
        This method updates the editor's internal state related to the file
        (filename, modified status, language detection, Git info).
        It does NOT set a user-facing status message like "File saved"; that's the
        caller's responsibility (e.g., save_file, save_file_as).

        Args:
            target_filename (str): The absolute or relative path to the file to write.

        Raises:
            Exception: Propagates exceptions that occur during file writing (e.g., IOError, OSError).
        """
        logging.debug(
            f"_write_file: Attempting to write to target: '{target_filename}' with encoding '{self.encoding}'")
        try:
            # Prepare content with OS-specific line endings
            # os.linesep ensures consistency with how the OS expects newlines.
            content_to_write = os.linesep.join(self.text)

            # Use safe_open for writing
            with self.safe_open(target_filename, "w", encoding=self.encoding, errors="replace") as f:
                bytes_written = f.write(content_to_write)
                # Optionally, check if bytes_written matches expected to ensure full write, though f.write should raise error if not.
                logging.debug(
                    f"_write_file: Successfully wrote {bytes_written} bytes (from {len(content_to_write)} chars) to '{target_filename}'")

            # Update editor state after successful write
            # Only update filename if it actually changed (relevant for save_as calling this)
            if self.filename != target_filename:
                self.filename = target_filename
                # GitBridge to last_filename_context
                self.git.update_git_info()

            self.modified = False
            self.detect_language()

            # Update Git information as file state on disk has changed
            self.git.update_git_info()

            # Asynchronously run linter if the file is a Python file
            # This check is based on the currently detected lexer.
            if self._lexer and self._lexer.name.lower() in ["python", "python3", "py"]:
                logging.debug(f"_write_file: Python file saved, queueing async lint for '{target_filename}'")
                # Pass the content that was just written to ensure linter sees the saved state
                threading.Thread(
                    target=self.run_lint_async,
                    args=(content_to_write,),  # Pass the actual saved content
                    daemon=True,
                    name=f"LintThread-{os.path.basename(target_filename)}"
                ).start()

            # Note: self.toggle_auto_save() was removed from here.
            # The decision to toggle/restart auto-save should be made by the higher-level
            # save_file or save_file_as methods, as _write_file is just the writing part.

        except Exception as e:
            # Log the error and re-raise to allow the caller to handle it (e.g., set status message)
            logging.error(f"Failed to write file '{target_filename}': {e}", exc_info=True)
            # Do not change self.modified or self.filename here if write failed,
            # as the file on disk is not in the desired state.
            raise  # Re-raise the exception to be caught by save_file or save_file_as

    def revert_changes(self) -> bool:
        """
        Reverts unsaved changes by reloading the content from the last(f"User confirmed. 
                                Attempting to revert changes for '{self.filename}' by reloading.")
        """
        logging.debug("revert_changes called")

        original_status = self.status_message
        original_modified_flag_for_comparison = self.modified

        redraw_is_needed_due_to_interaction = False

        if not self.filename or self.filename == "noname":
            self._set_status_message("Cannot revert: file has not been saved yet (no filename).")
            logging.debug("Revert failed: current buffer is unnamed or has never been saved to disk.")
            return self.status_message != original_status

        if not os.path.exists(self.filename):
            self._set_status_message(f"Cannot revert: '{os.path.basename(self.filename)}' does not exist on disk.")
            logging.warning(f"Revert failed: File '{self.filename}' not found on disk.")
            return self.status_message != original_status

        if not self.modified:
            self._set_status_message(f"No unsaved changes to revert for '{os.path.basename(self.filename)}'.")
            logging.debug(f"Revert skipped: No modifications to revert for '{self.filename}'.")
            return self.status_message != original_status

        status_before_prompt = self.status_message
        confirmation = self.prompt(f"Revert all unsaved changes to '{os.path.basename(self.filename)}'? (y/n): ")

        if self.status_message != status_before_prompt:
            redraw_is_needed_due_to_interaction = True

        if not confirmation or confirmation.lower() != 'y':
            if not redraw_is_needed_due_to_interaction and self.status_message == original_status:
                self._set_status_message("Revert cancelled by user.")
            logging.debug("Revert operation cancelled by user or prompt timeout.")
            return self.status_message != original_status or redraw_is_needed_due_to_interaction

        logging.info(f"User confirmed. Attempting to revert changes for '{self.filename}' by reloading.")
        self.modified = False

        try:
            reloaded_successfully = self.open_file(self.filename)

            if reloaded_successfully:
                if not self.modified:
                    self._set_status_message(
                        f"Successfully reverted to saved version of '{os.path.basename(self.filename)}'.")
                    logging.info(f"Changes for '{self.filename}' reverted successfully.")
                else:
                    self._set_status_message(
                        f"Reverted '{os.path.basename(self.filename)}', but file still marked modified.")
                    logging.warning(f"Reverted '{self.filename}', but it's still marked as modified post-open.")
                return True
            else:
                self.modified = original_modified_flag_for_comparison
                logging.warning(
                    f"Revert: self.open_file call for '{self.filename}' returned False. Status: {self.status_message}")
                return self.status_message != original_status or redraw_is_needed_due_to_interaction

        except Exception as e:
            self._set_status_message(
                f"Error during revert operation for '{os.path.basename(self.filename)}': {str(e)[:70]}...")
            logging.exception(f"Unexpected error during revert process for file: {self.filename}")
            self.modified = original_modified_flag_for_comparison
            return True

    # -------------- Auto-save ------------------------------
    def toggle_auto_save(self) -> bool:
        """
        Toggles the auto-save feature on or off.
        The auto-save interval (in minutes) is read from `self.config` or defaults.
        Auto-save only occurs if a filename is set and there are modifications (`self.modified`).
        A background thread handles the periodic saving.

        This method itself primarily manages the `_auto_save_enabled` flag and the
        auto-save thread. It always sets a status message indicating the new state
        of auto-save, thus it usually implies a redraw is needed.

        Returns:
            bool: True, as this action always changes the status message to reflect
                  the new auto-save state, requiring a status bar update.
        """
        logging.debug(
            f"toggle_auto_save called. Current auto_save_enabled: {getattr(self, '_auto_save_enabled', False)}")
        original_status = self.status_message

        # Ensure attributes exist (usually set in __init__)
        if not hasattr(self, "_auto_save_enabled"):
            self._auto_save_enabled = False
        if not hasattr(self, "_auto_save_thread"):  # Thread object for the auto-save task
            self._auto_save_thread = None
        if not hasattr(self, "_auto_save_stop_event"):  # Event to signal thread to stop
            self._auto_save_stop_event = threading.Event()

        # Get the auto-save interval from config, defaulting if not found or invalid
        try:
            # Ensure interval is a positive number, representing minutes
            interval_minutes = float(self.config.get("settings", {}).get("auto_save_interval", 1.0))  # Default 1 min
            if interval_minutes <= 0:
                logging.warning(f"Invalid auto_save_interval ({interval_minutes} min) in config, defaulting to 1 min.")
                interval_minutes = 1.0
        except (ValueError, TypeError):
            logging.warning("Could not parse auto_save_interval from config, defaulting to 1 min.")
            interval_minutes = 1.0

        self._auto_save_interval = interval_minutes  # Store the current interval in minutes

        # Toggle the auto-save state
        self._auto_save_enabled = not self._auto_save_enabled

        if self._auto_save_enabled:
            # Auto-save is being enabled
            self._auto_save_stop_event.clear()  # Clear the stop signal for the new thread

            # Start the auto-save thread if it's not already running or if it died
            if self._auto_save_thread is None or not self._auto_save_thread.is_alive():

                def auto_save_task_runner():
                    """The actual task performed by the auto-save thread."""
                    logging.info(f"Auto-save thread started. Interval: {self._auto_save_interval} min.")
                    last_saved_text_hash = None  # Store hash of last saved content to detect changes

                    while not self._auto_save_stop_event.is_set():  # Loop until stop event is set
                        try:
                            # Wait for the specified interval or until stop event is set
                            # Convert interval from minutes to seconds for time.sleep
                            sleep_duration_seconds = max(1, int(self._auto_save_interval * 60))

                            # Wait in smaller chunks to be more responsive to stop_event
                            interrupted = self._auto_save_stop_event.wait(timeout=sleep_duration_seconds)
                            if interrupted:  # Stop event was set
                                logging.info("Auto-save thread received stop signal during wait.")
                                break

                                # Check again after sleep, in case state changed while sleeping
                            if not self._auto_save_enabled or self._auto_save_stop_event.is_set():
                                break

                            # Conditions for auto-saving:
                            # 1. Filename must be set (i.e., not a new, unsaved buffer)
                            # 2. Document must be modified
                            if not self.filename or self.filename == "noname":
                                logging.debug("Auto-save: Skipped, no filename set.")
                                continue

                            # Acquire lock to safely read self.text and self.modified
                            with self._state_lock:
                                if not self.modified:
                                    logging.debug("Auto-save: Skipped, no modifications.")
                                    continue

                                # Get current text and its hash
                                current_text_content = os.linesep.join(self.text)
                                current_text_hash = hash(current_text_content)

                                # Only save if content has actually changed since last auto-save
                                if current_text_hash == last_saved_text_hash:
                                    logging.debug("Auto-save: Skipped, content unchanged since last auto-save.")
                                    continue

                                # File is named, modified, and content has changed
                                temp_filename = self.filename  # Store before releasing lock for write
                                temp_encoding = self.encoding
                                temp_text_to_save = current_text_content
                                _temp_modified_flag_before_save = self.modified

                            # Perform file writing outside the main state lock if possible,
                            # though _write_file might acquire it again internally if it modifies shared state
                            # like self.modified. For simplicity here, direct write.
                            try:
                                logging.info(f"Auto-saving '{temp_filename}'...")
                                # Use safe_open directly or call a simplified _write_file_content
                                with self.safe_open(temp_filename, "w", encoding=temp_encoding, errors="replace") as f:
                                    f.write(temp_text_to_save)

                                # Update state after successful save
                                with self._state_lock:
                                    # Verify that the file saved is still the current one and text hasn't changed
                                    # during the write operation (unlikely for this simple model).
                                    if self.filename == temp_filename and hash(
                                            os.linesep.join(self.text)) == current_text_hash:
                                        self.modified = False  # Mark as no longer modified
                                        last_saved_text_hash = current_text_hash  # Update hash of saved content
                                        self._set_status_message(f"Auto-saved: {os.path.basename(temp_filename)}")
                                        logging.info(f"Auto-saved '{temp_filename}' successfully.")
                                    else:
                                        logging.warning(
                                            f"Auto-save: File context changed during write of '{temp_filename}'. Save may be stale.")
                                        # Don't change modified flag or last_saved_text_hash if context changed.

                            except Exception as e_write:
                                self._set_status_message(f"Auto-save error for '{temp_filename}': {e_write}")
                                logging.exception(f"Auto-save failed for '{temp_filename}'")
                                # Consider if _auto_save_enabled should be set to False on error

                        except Exception as e_thread_loop:
                            # Catch any other unexpected errors within the thread's loop
                            logging.exception(f"Unexpected error in auto-save thread loop: {e_thread_loop}")
                            # Potentially disable auto-save to prevent repeated errors
                            self._auto_save_enabled = False
                            self._auto_save_stop_event.set()  # Signal thread to terminate
                            self._set_status_message("Auto-save disabled due to an internal error.")
                            break  # Exit the loop

                    logging.info("Auto-save thread finished.")

                # Create and start the daemon thread for auto-saving
                self._auto_save_thread = threading.Thread(
                    target=auto_save_task_runner,
                    daemon=True,  # Thread will exit when the main program exits
                    name="AutoSaveThread"
                )
                self._auto_save_thread.start()

            # Set status message to indicate auto-save is now enabled
            self._set_status_message(f"Auto-save enabled (every {self._auto_save_interval:.1f} min)")
            logging.info(f"Auto-save feature has been enabled. Interval: {self._auto_save_interval:.1f} minutes.")
        else:
            # Auto-save is being disabled
            if self._auto_save_thread and self._auto_save_thread.is_alive():
                logging.debug("toggle_auto_save: Signaling auto-save thread to stop.")
                self._auto_save_stop_event.set()  # Signal the thread to stop
                # Optionally, wait for the thread to finish with a timeout
                # self._auto_save_thread.join(timeout=2.0)
                # if self._auto_save_thread.is_alive():
                #    logging.warning("Auto-save thread did not stop in time.")
            self._auto_save_thread = None  # Discard thread object

            self._set_status_message("Auto-save disabled")
            logging.info("Auto-save feature has been disabled.")

        # This method always changes the status message, so a redraw is needed.
        return self.status_message != original_status or True  # Force True because state change is significant

    def new_file(self) -> bool:
        """
        Creates a new, empty buffer (a new document).
        If the current buffer has unsaved changes, it prompts the user to save them.
        Resets various editor states like filename, lexer, Git info, encoding,
        history, cursor position, selection, and scroll.

        Returns:
            bool: True if the editor's state significantly changed (requiring a full redraw),
                  or if a status message was updated. False only if the operation was
                  cancelled very early without any state or status message change (unlikely).
        """
        logging.debug("new_file called")

        # Store initial states to determine if a redraw is ultimately needed
        original_status = self.status_message
        original_modified_flag = self.modified  # To see if 'n' in prompt changes it
        # Other states like text, filename, cursor will definitely change if new_file proceeds.

        redraw_is_needed = False  # Accumulator for redraw reasons

        # 1. Handle unsaved changes in the current buffer
        if self.modified:
            status_before_prompt = self.status_message  # Status before this specific prompt
            ans = self.prompt("Save changes before creating new file? (y/n): ")
            if self.status_message != status_before_prompt:
                redraw_is_needed = True  # Prompt interaction changed status

            if ans and ans.lower().startswith("y"):
                # User wants to save. save_file() returns True if it caused changes.
                if self.save_file():
                    redraw_is_needed = True

                # Crucially, check if 'self.modified' is still True after save_file() attempt.
                # If so, saving failed or was cancelled by the user during 'Save As'.
                if self.modified:
                    self._set_status_message("New file creation cancelled: unsaved changes were not saved.")
                    # Even if redraw_is_needed was false, setting status makes it true.
                    return True
            elif ans and ans.lower().startswith("n"):
                # User chose not to save. Discard current changes.
                logging.debug("New file creation: User chose 'no' to save. Discarding current changes.")
                self.modified = False
                if self.modified != original_modified_flag:  # If modified flag actually changed
                    redraw_is_needed = True
            else:
                # User cancelled the save prompt (Esc, Enter on empty, or invalid input)
                # If status wasn't already changed by the prompt itself to something new
                if not redraw_is_needed and self.status_message == original_status:
                    self._set_status_message("New file creation cancelled.")
                return True  # Status changed (either by prompt or by cancellation message)

        # 2. If we reached here, changes (if any) were handled. Proceed to create the new file state.
        # These actions below will definitely require a redraw.

        logging.debug("Proceeding to reset editor state for a new file.")

        self.text = [""]  # Start with a single empty line
        self.filename = None
        self.encoding = "UTF-8"  # Default encoding for new files
        # self.modified should be False at this point (either saved, discarded, or was already False)
        # but explicitly set it to ensure consistency for a new file.
        self.modified = False

        # Reset language-specific and version control information
        # _lexer will be re-detected by self.detect_language()
        if self._lexer is not None:  # If there was a lexer, changing to None or new one is a change
            redraw_is_needed = True  # Though detect_language will also imply this
        self._lexer = None
        self.git.reset_state() 

        # Reset cursor, scroll, selection, and history
        # set_initial_cursor_position itself implies a major visual reset.
        self.set_initial_cursor_position()
        self.history.clear() 
        self.history.add_action({
            "type": "new_file",
            "content": [""],
            "encoding": "UTF-8",
        })

        # Disable auto-save for a new, untitled file
        if self._auto_save_enabled:
            self._auto_save_enabled = False
            # Optionally set a status message about auto-save being off,
            # but "New file created" might be more prominent.
            logging.debug("Auto-save disabled for new untitled file.")
            # redraw_is_needed = True (if this status change was important to show immediately)

        # Re-detect language for the new (empty) buffer.
        # This will typically set TextLexer and clear the lru_cache for _get_tokenized_line.
        self.detect_language()
        self._set_status_message("New file created")
        # Given the extensive state reset (text, cursor, scroll, filename, lexer, etc.),
        # a redraw is always necessary after successfully reaching this point.
        return True

    def cancel_operation(self) -> bool:
        """
        Handles cancellation of specific ongoing states like an active lint panel,
        text selection, or search highlighting.
        Sets an appropriate status message if an operation was cancelled.

        Returns:
            bool: True if any specific state (lint panel visibility, selection active,
                  search highlights present) was actively cancelled AND the status message
                  was consequently updated. False if no such specific state was active to be
                  cancelled by this method call.
        """
        logging.debug(
            f"cancel_operation called. Panel: {self.lint_panel_active}, "
            f"Selecting: {self.is_selecting}, Highlights: {bool(self.highlighted_matches)}"
        )

        original_status = self.status_message
        action_cancelled_a_specific_state = False

        if self.lint_panel_active:
            self.lint_panel_active = False
            self.lint_panel_message = ""  # Clear the message when panel is explicitly closed
            self._set_status_message("Lint panel closed")
            logging.debug("cancel_operation: Lint panel closed.")
            action_cancelled_a_specific_state = True
        elif self.is_selecting:
            self.is_selecting = False
            self.selection_start = None
            self.selection_end = None
            self._set_status_message("Selection cancelled")
            logging.debug("cancel_operation: Selection cancelled.")
            action_cancelled_a_specific_state = True
        elif self.highlighted_matches:
            self.highlighted_matches = []
            # self.search_term = "" # Optional: reset search context on cancel
            # self.current_match_idx = -1
            self._set_status_message("Search highlighting cleared")
            logging.debug("cancel_operation: Search highlighting cleared.")
            action_cancelled_a_specific_state = True

        # Returns True if a specific state was cancelled AND status message changed as a result.
        # If only status changes without a specific state change (e.g. from "Ready" to "Nothing to cancel"),
        # that will be caught by the caller (handle_escape) if needed.
        # This method focuses on *cancelling an operation*.
        if action_cancelled_a_specific_state:
            logging.debug(f"Status changed from '{original_status}' to '{self.status_message}'")
        return action_cancelled_a_specific_state

    def handle_escape(self) -> bool:
        """
        Handles the Esc key press.
        Primarily attempts to cancel active states (lint panel, selection, search highlights)
        by calling self.cancel_operation().
        If no specific operation was cancelled, it may set a generic "Nothing to cancel" message
        or do nothing if a more relevant status is already present.

        The timestamp logic for double-press exit is removed from this version
        to align with standard Esc behavior (cancel only, no exit).

        Returns:
            bool: True if any state relevant for redraw changed (panel visibility, selection,
                  highlights, or status message), False otherwise.
        """
        original_status = self.status_message  # To check if status message actually changes
        action_taken_requiring_redraw = False

        logging.debug("handle_escape called.")

        # Attempt to cancel any ongoing specific operation.
        # cancel_operation() returns True if it cancelled something and set a status.
        if self.cancel_operation():
            action_taken_requiring_redraw = True
            logging.debug("handle_escape: cancel_operation handled the Esc press and indicated a change.")
        else:
            # cancel_operation() returned False, meaning no specific panel, selection,
            # or highlight was active to be cancelled by it.
            # In this case, a single Esc press with no active operation
            # should typically do nothing or, at most, clear a transient status message.
            # We will set a "Nothing to cancel" message only if no other important message is present.
            if self.status_message == original_status or self.status_message == "Ready" or not self.status_message:
                # If status was default or unchanged by cancel_operation (which it shouldn't be if it returned false),
                # then set a "nothing to cancel" message.
                # We could also choose to do absolutely nothing visually if there's nothing to cancel.
                # For now, let's set a message.
                self._set_status_message("Nothing to cancel")  # Or simply don't change status
                if self.status_message != original_status:
                    action_taken_requiring_redraw = True
            else:
                # Some other status message was already present (e.g. an error), leave it.
                # Redraw might still be needed if that status is new compared to before handle_escape.
                if self.status_message != original_status:
                    action_taken_requiring_redraw = True

            logging.debug("handle_escape: No specific operation to cancel. Status might be updated.")

        # The _last_esc_time attribute is no longer needed for double-press exit logic here.
        # If you still want to track it for other purposes, it can be updated:
        # setattr(self, "_last_esc_time", time.monotonic())

        return action_taken_requiring_redraw


    def exit_editor(self) -> None:
        """Attempts to gracefully exit the editor."""
        logging.debug("exit_editor: Attempting to exit editor.")

        # 1. Prompt user to save changes if needed.
        if self.modified:
            ans = self.prompt("Save changes before exiting? (y/n): ")
            if ans and ans.lower().startswith("y"):
                self.save_file()
                if self.modified:
                    self._set_status_message("Exit aborted: file not saved.")
                    return
            elif ans and ans.lower().startswith("n"):
                pass  # User chose not to save.
            else:
                self._set_status_message("Exit cancelled.")
                return

        logging.info("exit_editor: Proceeding with editor shutdown.")

        # 2. Stop background threads (e.g., auto-save).
        if hasattr(self, "_auto_save_stop_event"):
            self._auto_save_stop_event.set()
            if hasattr(self, "_auto_save_thread") and self._auto_save_thread and self._auto_save_thread.is_alive():
                self._auto_save_thread.join(timeout=0.1)
        # to stop Async Engine:
        if hasattr(self, 'async_engine'):
            self.async_engine.stop()

        # 3. Gracefully shut down all linters via the LinterBridge.
        # THIS IS THE FIX. All LSP-related logic is replaced by this single call.
        self.linter_bridge.shutdown()

        # 4. Gracefully terminate curses and exit the process.
        if threading.current_thread() is threading.main_thread():
            try:
                self.stdscr.keypad(False)
                curses.nocbreak()
                curses.echo()
                curses.endwin()
                # A final clear might be good on some terminals.
                if sys.platform != "win32":
                    os.system("clear")
                sys.exit(0)
            except Exception as e:
                logging.error(f"exit_editor: Error during terminal restoration: {e}", exc_info=True)
                sys.exit(1) # Exit with an error code if cleanup fails.
        else:
            logging.warning("exit_editor: Cannot restore terminal from a non-main thread.")
            sys.exit(0)


    # ------------------ Prompting for Input ------------------
    def prompt(self, message: str, max_len: int = 1024, timeout_seconds: int = 60) -> Optional[str]:
        """Displays a single-line input prompt in the status bar with a timeout.

        This method takes over the bottom line of the screen to display a prompt
        message and an input field for the user. It handles basic text editing
        within the input field, including Backspace, Delete, arrow keys, Home,
        End, and Tab. The prompt is confirmed with Enter or cancelled with Esc.
        A timeout mechanism is also in place. Window resize events during the
        prompt are handled by redrawing the prompt.

        The method uses `noutrefresh()` for screen updates and a final `doupdate()`
        to minimize flicker, especially if called frequently or if other parts of
        the UI might update.

        Args:
            message: The message to display before the input field.
            max_len: Maximum allowed length of the input buffer (character count).
            timeout_seconds: Timeout for waiting for input, in seconds.
                            If 0 or negative, no timeout (waits indefinitely).

        Returns:
            The user's input string (stripped of leading/trailing whitespace)
            if confirmed with Enter, or None if cancelled (Esc) or timed out.
        """
        logging.debug(
            f"Prompt called. Message: '{message}', Max length: {max_len}, Timeout: {timeout_seconds}s"
        )

        # Ensure cursor is visible for the prompt input field.
        # Store original visibility to restore it later.
        original_cursor_visibility = curses.curs_set(1)

        # Set stdscr to blocking mode with a timeout for this prompt.
        # nodelay(False) means get_wch() will block.
        self.stdscr.nodelay(False)
        if timeout_seconds > 0:
            self.stdscr.timeout(timeout_seconds * 1000)  # timeout is in milliseconds
        else:
            self.stdscr.timeout(-1)  # No timeout, block indefinitely

        input_buffer: List[str] = []  # Stores characters of the input
        cursor_char_pos: int = 0  # Cursor position as an index within input_buffer

        # Tab width for Tab key insertion, using editor's configuration.
        prompt_tab_width: int = self.config.get("editor", {}).get("tab_size", 4)

        input_result: Optional[str] = None  # Stores the final result (string or None)

        try:
            while True:  # Main loop for handling input within the prompt
                term_height, term_width = self.stdscr.getmaxyx()

                # Guard against invalid terminal dimensions (e.g., during rapid resize).
                if term_height <= 0:
                    logging.error("Prompt: Terminal height is zero or negative. Aborting prompt.")
                    # Attempt to restore terminal state before returning.
                    # This part of finally block will handle full restoration.
                    return None

                prompt_row = term_height - 1  # Prompt is always on the last line.

                # --- Prepare prompt message for display ---
                # Truncate the display message if it's too long for the available width,
                # leaving space for the input field.
                # Arbitrary minimum space for input field + cursor.
                min_space_for_input_and_cursor = 15
                max_allowed_msg_display_width = max(0, term_width - min_space_for_input_and_cursor)
                display_message_str = message

                if self.get_string_width(message) > max_allowed_msg_display_width:
                    # Use self.truncate_string if available for proper Unicode truncation.
                    # Otherwise, use basic slicing as a fallback.
                    if hasattr(self, 'truncate_string'):
                        display_message_str = self.truncate_string(message,
                                                                   max_allowed_msg_display_width - 3) + "..."  # -3 for "..."
                    else:
                        display_message_str = message[:max_allowed_msg_display_width - 3] + "..."

                display_message_screen_len = self.get_string_width(display_message_str)

                # --- Clear and redraw the prompt line ---
                try:
                    # Move to the prompt line and clear it.
                    self.stdscr.move(prompt_row, 0)
                    self.stdscr.clrtoeol()
                    # Draw the prompt message.
                    # Use a status color, or a default if not found.
                    prompt_message_color = self.colors.get("status", curses.A_NORMAL)
                    self.stdscr.addstr(prompt_row, 0, display_message_str, prompt_message_color)
                except curses.error as e_draw_msg:
                    logging.error(f"Prompt: Curses error during prompt message draw: {e_draw_msg}")
                    return None  # Cannot proceed if we can't draw the prompt message.

                current_input_text = "".join(input_buffer)
                # Calculate available screen width (in display cells) for the input text itself.
                input_field_start_x_on_screen = display_message_screen_len
                available_width_for_input_text = max(0, term_width - (
                            input_field_start_x_on_screen + 1))  # +1 for cursor space at end

                # --- Horizontal scrolling logic for the input text within the prompt ---
                width_before_cursor_in_buffer = self.get_string_width(current_input_text[:cursor_char_pos])
                full_input_text_width_in_buffer = self.get_string_width(current_input_text)

                # text_scroll_offset: how many display cells of the input_buffer are scrolled off to the left.
                text_scroll_offset = 0
                if full_input_text_width_in_buffer > available_width_for_input_text:
                    # If cursor is too far right to be visible, scroll text left.
                    # (-1) so the cursor itself is visible at the last position.
                    if width_before_cursor_in_buffer > text_scroll_offset + available_width_for_input_text - 1:
                        text_scroll_offset = width_before_cursor_in_buffer - (available_width_for_input_text - 1)
                    # If cursor is too far left (scrolled past it), adjust scroll.
                    elif width_before_cursor_in_buffer < text_scroll_offset:
                        text_scroll_offset = width_before_cursor_in_buffer

                # Determine the actual characters from input_buffer to display,
                # based on the calculated text_scroll_offset.
                display_start_char_idx_in_buffer = 0
                accumulated_scrolled_width = 0
                if text_scroll_offset > 0:
                    for i_scroll, char_scroll in enumerate(input_buffer):
                        char_w = self.get_char_width(char_scroll)
                        if accumulated_scrolled_width + char_w > text_scroll_offset:
                            # This char is partially or fully visible after the scroll.
                            display_start_char_idx_in_buffer = i_scroll
                            break
                        accumulated_scrolled_width += char_w
                        # If we iterate through all chars and still haven't met text_scroll_offset,
                        # it means scroll_offset is too large or text is empty.
                        # Setting display_start_char_idx_in_buffer to i_scroll+1 ensures it becomes len(input_buffer).
                        if i_scroll == len(input_buffer) - 1:  # Reached end of buffer
                            display_start_char_idx_in_buffer = len(input_buffer)

                # Construct the segment of the input text that will be visible on screen.
                visible_text_segment_to_draw = ""
                current_visible_segment_width_on_screen = 0
                for char_val in input_buffer[display_start_char_idx_in_buffer:]:
                    char_w = self.get_char_width(char_val)
                    if current_visible_segment_width_on_screen + char_w > available_width_for_input_text:
                        break  # Segment would exceed available width.
                    visible_text_segment_to_draw += char_val
                    current_visible_segment_width_on_screen += char_w

                # Calculate cursor's screen X position relative to the start of the displayed segment.
                # This is the width of the part of the visible segment that is *before* the cursor.
                cursor_screen_offset_within_visible_segment = self.get_string_width(
                    "".join(input_buffer[display_start_char_idx_in_buffer:cursor_char_pos])
                )

                # --- Draw the visible input text and position the actual curses cursor ---
                try:
                    if visible_text_segment_to_draw:
                        self.stdscr.addstr(prompt_row, input_field_start_x_on_screen, visible_text_segment_to_draw)

                    # Final screen X position for the curses cursor.
                    screen_cursor_x = input_field_start_x_on_screen + cursor_screen_offset_within_visible_segment
                    # Clamp cursor to be within the drawable area of the input field and terminal width.
                    # Ensure cursor is not drawn left of where the input field starts.
                    screen_cursor_x = max(input_field_start_x_on_screen, screen_cursor_x)
                    # Ensure cursor is not drawn beyond the right edge of the terminal.
                    if term_width > 0:  # term_width can be 0 if terminal is not properly initialized.
                        screen_cursor_x = min(screen_cursor_x, term_width - 1)

                    self.stdscr.move(prompt_row, screen_cursor_x)
                except curses.error as e_draw_input:
                    logging.error(f"Prompt: Curses error during input text/cursor draw: {e_draw_input}")
                    return None

                    # Use noutrefresh and doupdate instead of a single refresh.
                self.stdscr.noutrefresh()
                curses.doupdate()  # Apply changes to the physical screen.

                # --- Get key press ---
                key_event: Any = curses.ERR  # Initialize for timeout or error case.
                try:
                    key_event = self.stdscr.get_wch()
                    logging.debug(f"Prompt: get_wch() returned: {repr(key_event)} (type: {type(key_event)})")
                except curses.error as e_getch:
                    # Check if it's a timeout error (get_wch() raises error if no input within timeout).
                    if 'no input' in str(e_getch).lower() or e_getch.args[0] == 'no input':  # More robust check
                        logging.warning(f"Prompt: Input timed out after {timeout_seconds}s for: '{message}'")
                        input_result = None
                        break  # Exit the while loop on timeout.
                    else:  # Other curses error during get_wch.
                        logging.error(f"Prompt: Curses error on get_wch(): {e_getch}", exc_info=True)
                        input_result = None
                        break  # Exit the while loop on error.

                # --- Process key press ---
                if isinstance(key_event, int):  # Special key (e.g., arrows, F-keys) or non-ASCII char as int.
                    if key_event == 27:  # Esc key code.
                        logging.debug("Prompt: Esc (int) detected. Cancelling.")
                        input_result = None
                        break
                    elif key_event in (curses.KEY_ENTER, 10, 13):  # Enter/Return keys.
                        logging.debug(f"Prompt: Enter (int {key_event}) detected. Confirming.")
                        input_result = "".join(input_buffer).strip()
                        break
                    elif key_event in (curses.KEY_BACKSPACE, 127, 8):  # Backspace key (code can vary).
                        if cursor_char_pos > 0:
                            cursor_char_pos -= 1
                            input_buffer.pop(cursor_char_pos)
                    elif key_event == curses.KEY_DC:  # Delete character under cursor.
                        if cursor_char_pos < len(input_buffer):
                            input_buffer.pop(cursor_char_pos)
                    elif key_event == curses.KEY_LEFT:
                        cursor_char_pos = max(0, cursor_char_pos - 1)
                    elif key_event == curses.KEY_RIGHT:
                        cursor_char_pos = min(len(input_buffer), cursor_char_pos + 1)
                    elif key_event == curses.KEY_HOME:
                        cursor_char_pos = 0
                    elif key_event == curses.KEY_END:
                        cursor_char_pos = len(input_buffer)
                    elif key_event == curses.KEY_RESIZE:
                        logging.debug("Prompt: KEY_RESIZE detected. Redrawing prompt at start of loop.")
                        # Screen will be redrawn with new dimensions at the start of the next loop iteration.
                        continue
                    elif key_event == curses.ascii.TAB:  # Tab key.
                        tab_spaces_str = " " * prompt_tab_width
                        for char_in_tab_str in tab_spaces_str:
                            if len(input_buffer) < max_len:
                                input_buffer.insert(cursor_char_pos, char_in_tab_str)
                                cursor_char_pos += 1
                    elif 32 <= key_event < 1114112:  # Other integer that might be a printable Unicode char.
                        try:
                            char_to_insert_val = chr(key_event)
                            # Check if it's displayable and not a control char missed by earlier checks.
                            # wcswidth < 0 usually means non-printable or control.
                            if len(input_buffer) < max_len and wcswidth(char_to_insert_val) >= 0:
                                input_buffer.insert(cursor_char_pos, char_to_insert_val)
                                cursor_char_pos += 1
                        except ValueError:
                            logging.warning(f"Prompt: Could not convert integer key code {key_event} to char.")
                    else:  # Unhandled integer key.
                        logging.debug(f"Prompt: Ignored unhandled integer key: {key_event}")

                elif isinstance(key_event, str):  # String input (usually a single character or Esc sequence part).
                    # Handle multi-character strings if get_wch() might return them (e.g. paste, complex escape seq).
                    # For now, assuming single character or known sequences.
                    if key_event == '\x1b':  # Esc key sometimes comes as a string (e.g., part of an escape sequence).
                        logging.debug("Prompt: Esc (str) detected. Cancelling.")
                        input_result = None
                        break
                    elif key_event in ("\n", "\r"):  # Enter/Return as string.
                        logging.debug(f"Prompt: Enter (str '{repr(key_event)}') detected. Confirming.")
                        input_result = "".join(input_buffer).strip()
                        break
                    elif key_event == '\t':  # Tab as string.
                        tab_spaces_str = " " * prompt_tab_width
                        for char_in_tab_str in tab_spaces_str:
                            if len(input_buffer) < max_len:
                                input_buffer.insert(cursor_char_pos, char_in_tab_str)
                                cursor_char_pos += 1
                    # Process other characters if they are single and printable.
                    elif len(key_event) == 1:  # If it's a single character string
                        # isprintable() is a good first check, but wcswidth is more robust for display width.
                        if key_event.isprintable() and wcswidth(key_event) >= 0:
                            if len(input_buffer) < max_len:
                                input_buffer.insert(cursor_char_pos, key_event)
                                cursor_char_pos += 1
                        else:
                            logging.debug(f"Prompt: Ignored non-displayable/control string char: {repr(key_event)}")
                    else:  # Multi-character string not handled above (e.g., unparsed escape sequence).
                        logging.debug(f"Prompt: Ignored unhandled multi-character string input: {repr(key_event)}")
                # else key_event == curses.ERR (no input), which is handled by the try-except for get_wch().

        finally:
            # Restore terminal settings that were changed for the prompt duration.
            self.stdscr.nodelay(True)  # Restore non-blocking input for the main editor loop.
            self.stdscr.timeout(-1)  # Disable timeout for stdscr.
            curses.curs_set(original_cursor_visibility)  # Restore original cursor visibility.

            # Clear the prompt line from the status bar before returning control.
            # This is important so the main editor's status bar can be redrawn cleanly.
            term_height_final, _ = self.stdscr.getmaxyx()
            try:
                if term_height_final > 0:  # Ensure height is valid.
                    self.stdscr.move(term_height_final - 1, 0)
                    self.stdscr.clrtoeol()
                    self.stdscr.noutrefresh()  # Prepare this clear operation.
                    curses.doupdate()  # Apply it.
                    # The main editor loop will then perform a full redraw which
                    # will restore its own status bar content.
            except curses.error as e_final_clear_prompt:
                logging.warning(f"Prompt: Curses error during final status line clear: {e_final_clear_prompt}")
            curses.flushinp()  # Clear any unprocessed typeahead characters from terminal input buffer.

        return input_result

    # 2 ========== Search/Replace and Find ======================
    def search_and_replace(self) -> bool:
        """
        Searches for text using a regular expression and replaces occurrences.
        Prompts for search pattern and replacement text.
        This operation is not added to the undo/redo history; instead, the history is cleared.
        Returns True if any interaction (prompts, status change) or modification occurred,
        indicating a redraw is needed.
        """
        logging.debug("search_and_replace called")

        original_status = self.status_message
        status_changed_by_prompts = False  # Track if prompts themselves alter final status view
        # Clear previous search state immediately
        self.highlighted_matches = []
        self.search_matches = []
        self.search_term = ""  # Clear the term so F3 won't use the old one
        self.current_match_idx = -1
        # Initial redraw might be good here if clearing highlights should be immediate
        # but we'll rely on the return value for the main loop.

        # Prompt for search pattern
        status_before_search_prompt = self.status_message
        search_pattern_str = self.prompt("Search for (regex): ")
        if self.status_message != status_before_search_prompt:
            status_changed_by_prompts = True

        if not search_pattern_str:  # User cancelled (Esc or empty Enter)
            if not status_changed_by_prompts and self.status_message == original_status:
                self._set_status_message("Search/Replace cancelled")
            # Return True if status changed by prompt or by cancellation message
            return self.status_message != original_status

            # Prompt for replacement string
        # An empty replacement string is valid (means delete the matched pattern).
        status_before_replace_prompt = self.status_message
        replace_with_str = self.prompt("Replace with: ")  # `prompt` can return None if cancelled
        if self.status_message != status_before_replace_prompt:
            status_changed_by_prompts = True

        if replace_with_str is None:  # User cancelled the replacement prompt
            if not status_changed_by_prompts and self.status_message == original_status:
                self._set_status_message("Search/Replace cancelled (no replacement text)")
            return self.status_message != original_status

        # Compile the regex pattern
        compiled_regex_pattern: Optional[re.Pattern] = None
        try:
            # re.IGNORECASE is a common default, can be made configurable
            compiled_regex_pattern = re.compile(search_pattern_str, re.IGNORECASE)
            logging.debug(f"Compiled regex pattern: '{search_pattern_str}' with IGNORECASE")
        except re.error as e:
            error_msg = f"Regex error: {str(e)[:70]}"
            self._set_status_message(error_msg)
            logging.warning(f"Search/Replace failed due to regex error: {e}")
            return True  # Status changed due to error message

        # --- Perform replacement ---
        new_text_lines: List[str] = []
        total_replacements_count = 0
        line_processing_error_occurred = False

        # It's safer to operate on a copy if iterating and modifying
        # or build a new list directly as done here.
        # Lock is needed for reading self.text if it could be modified by another thread,
        # but here we are in the main thread of action.

        with self._state_lock:  # Access self.text safely
            current_text_snapshot = list(self.text)  # Work on a snapshot

        for line_idx, current_line in enumerate(current_text_snapshot):
            try:
                # Perform substitution on the current line
                # subn returns a tuple: (new_string, number_of_subs_made)
                new_line_content, num_subs_on_line = compiled_regex_pattern.subn(replace_with_str, current_line)
                new_text_lines.append(new_line_content)
                if num_subs_on_line > 0:
                    total_replacements_count += num_subs_on_line
            except Exception as e_sub:  # Catch errors during re.subn (e.g., complex regex on specific line)
                logging.error(f"Error replacing in line {line_idx + 1} ('{current_line[:50]}...'): {e_sub}")
                new_text_lines.append(current_line)  # Append original line in case of error on this line
                line_processing_error_occurred = True

        # --- Update editor state if replacements were made or errors occurred ---
        if total_replacements_count > 0 or line_processing_error_occurred:
            with self._state_lock:
                self.text = new_text_lines
                self.modified = True  # Document has been modified
                # Search and Replace is a major change, typically clears undo/redo history
                self.history.clear()
                self.history.add_action({
                    "type": "bulk_replace",
                    "replacements": total_replacements_count
                })
                logging.debug("Cleared undo/redo history after search/replace.")
                # Cursor position might be invalidated, reset to start of document or last known good pos.
                # For simplicity, let's move to the beginning of the file.
                self.cursor_y = 0
                self.cursor_x = 0
                self._ensure_cursor_in_bounds()  # Ensure it's valid
                self._clamp_scroll()  # Adjust scroll

            if line_processing_error_occurred:
                self._set_status_message(f"Replaced {total_replacements_count} occurrences with errors on some lines.")
                logging.warning("Search/Replace completed with errors on some lines.")
            else:
                self._set_status_message(f"Replaced {total_replacements_count} occurrence(s).")
                logging.info(f"Search/Replace successful: {total_replacements_count} replacements.")
            return True  # Text changed, status changed, cursor moved
        else:  # No replacements made and no errors
            self._set_status_message("No occurrences found to replace.")
            logging.info("Search/Replace: No occurrences found.")
            return True  # Status message changed

        # Fallback, should not be reached if logic is complete
        # return status_changed_by_prompts

    def _collect_matches(self, term: str) -> List[Tuple[int, int, int]]:
        """
        Finds all occurrences of `term` (case-insensitive) in `self.text`.
        Uses a state lock for safe access to `self.text`.

        Args:
            term (str): The search term.

        Returns:
            List[Tuple[int, int, int]]: A list of tuples, where each tuple is
                                         (row_index, column_start_index, column_end_index)
                                         for a match.
        """
        matches: List[Tuple[int, int, int]] = []
        if not term:  # If the search term is empty, no matches can be found.
            return matches

        # Perform a case-insensitive search
        search_term_lower = term.lower()
        term_length = len(term)  # Original term length for calculating end index

        # Use a lock only for accessing self.text to get a snapshot.
        # This minimizes the time the lock is held.
        text_snapshot: List[str]
        with self._state_lock:
            # Create a shallow copy of the list of lines.
            # The strings themselves are immutable, so this is safe.
            text_snapshot = list(self.text)

            # Perform the search on the snapshot without holding the lock for the entire loop.
        for row_index, line_content in enumerate(text_snapshot):
            current_search_start_column = 0
            line_content_lower = line_content.lower()  # Compare against the lowercased version of the line

            while True:
                # Find the next occurrence in line_content_lower,
                # but record indices based on the original line_content.
                found_at_index = line_content_lower.find(search_term_lower, current_search_start_column)

                if found_at_index == -1:  # No more matches in this line
                    break

                match_end_index = found_at_index + term_length  # End index is exclusive
                matches.append((row_index, found_at_index, match_end_index))

                # Advance the search position to after the current match to find subsequent matches.
                # If term_length is 0 (empty search term, though handled above),
                # this prevents an infinite loop by advancing by at least 1.
                current_search_start_column = match_end_index if term_length > 0 else found_at_index + 1

        if matches:
            logging.debug(f"Found {len(matches)} match(es) for search term '{term}'. First match at: {matches[0]}")
        else:
            logging.debug(f"No matches found for search term '{term}'.")

        return matches

    def find_prompt(self) -> bool:
        """
        Prompts the user for a search term, collects all matches,
        updates highlights, and navigates to the first match if found.
        Clears any previous search state before starting a new search.

        Returns:
            bool: True if the editor's visual state (cursor, scroll, highlights, status)
                  changed as a result of the find operation or user interaction
                  with the prompt, False otherwise (though unlikely for this method).
        """

        logging.debug("find_prompt called")

        # Store initial state to compare against for determining if a redraw is needed.
        original_status = self.status_message
        original_cursor_pos = (self.cursor_y, self.cursor_x)
        original_scroll_pos = (self.scroll_top, self.scroll_left)
        # Highlights will be cleared, so that's always a potential change.

        # 1. Clear previous search state and highlights.
        # This is a visual change if there were previous highlights.
        had_previous_highlights = bool(self.highlighted_matches)
        self.highlighted_matches = []
        self.search_matches = []
        self.search_term = ""  # Reset search term for a new search
        self.current_match_idx = -1

        redraw_needed_due_to_clearing = had_previous_highlights  # If highlights were cleared, redraw

        # 2. Prompt for the search term.
        # The prompt itself temporarily changes the status bar.
        status_before_prompt = self.status_message  # Could have been changed if highlights were cleared and status set

        # Prompting the user for input
        term_to_search = self.prompt("Find: ")  # self.prompt handles its own status line updates during input

        # Check if status message was altered by the prompt itself (e.g., timeout, or internal prompt messages)
        # or if it was restored to its state before the prompt.
        status_changed_by_prompt_interaction = (self.status_message != status_before_prompt)

        if not term_to_search:  # User cancelled the prompt (e.g., Esc) or entered nothing
            # If status after prompt is same as original status (before clearing highlights and prompt),
            # but user cancelled, set "Search cancelled".
            if not status_changed_by_prompt_interaction and self.status_message == original_status:
                self._set_status_message("Search cancelled")
            # A redraw is needed if highlights were cleared, or if status message changed.
            return redraw_needed_due_to_clearing or (self.status_message != original_status)

        # 3. A search term was entered.
        self.search_term = term_to_search  # Store the new search term

        # 4. Collect all matches for the new term.
        # _collect_matches reads self.text, so no direct visual change from this call itself.
        self.search_matches = self._collect_matches(self.search_term)

        # 5. Update highlights to show the new matches.
        # This is a visual change if new matches are found or if previous highlights are now gone.
        self.highlighted_matches = list(self.search_matches)  # Make a copy for highlighting

        # 6. Navigate and set status based on whether matches were found.
        if not self.search_matches:
            self._set_status_message(f"'{self.search_term}' not found")
            # Even if no matches, highlights were cleared/updated (to empty), so redraw likely.
            # And status message changed.
        else:
            self.current_match_idx = 0  # Go to the first match
            # _goto_match will update cursor_y, cursor_x, scroll_top, scroll_left
            self._goto_match(self.current_match_idx)
            self._set_status_message(
                f"Found {len(self.search_matches)} match(es) for '{self.search_term}'. Press F3 for next."
            )

        # Determine if overall state change warrants a redraw.
        # Changes could be: highlights changed, cursor/scroll changed by _goto_match, status message changed.
        if (redraw_needed_due_to_clearing or  # Highlights were cleared
                bool(self.highlighted_matches) or  # New highlights were added
                (self.cursor_y, self.cursor_x) != original_cursor_pos or
                (self.scroll_top, self.scroll_left) != original_scroll_pos or
                self.status_message != original_status):
            return True

        return False  # Should rarely be False, as status or highlights usually change.

    def find_next(self) -> bool:
        """
        Moves the cursor and view to the next search match in the current search results.
        If no search has been performed or no matches were found, it sets an appropriate status message.
        The list of highlighted matches (`self.highlighted_matches`) is not changed by this method;
        it's assumed to be managed by `find_prompt` or when the search term changes.

        Returns:
            bool: True if the cursor position, scroll, or status message changed, False otherwise.
        """

        original_status = self.status_message
        original_cursor_pos = (self.cursor_y, self.cursor_x)
        original_scroll_pos = (self.scroll_top, self.scroll_left)
        changed_state = False

        if not self.search_matches:
            # This means either no search was performed (self.search_term is empty)
            # or the last search yielded no results (self.search_term is set, but search_matches is empty).
            if not self.search_term:
                self._set_status_message("No search term. Use Find (e.g., Ctrl+F) first.")
            else:  # search_term exists, but no matches were found for it
                self._set_status_message(f"No matches found for '{self.search_term}'.")

            # Ensure no stale highlights if we reach here
            if self.highlighted_matches:  # If there were highlights from a previous successful search
                self.highlighted_matches = []
                changed_state = True  # Highlight state changed

            self.current_match_idx = -1  # Reset current match index

            if self.status_message != original_status:
                changed_state = True
            return changed_state

        # Proceed if there are matches
        # Increment current_match_idx, wrapping around if necessary
        self.current_match_idx = (self.current_match_idx + 1) % len(self.search_matches)

        # The _goto_match method should handle cursor and scroll adjustment
        # It does not return a flag, so we check changes after its call.
        self._goto_match(self.current_match_idx)

        self._set_status_message(
            f"Match {self.current_match_idx + 1} of {len(self.search_matches)} for '{self.search_term}'"
        )

        # Determine if a redraw is needed by comparing state
        if (self.cursor_y != original_cursor_pos[0] or self.cursor_x != original_cursor_pos[1] or
                self.scroll_top != original_scroll_pos[0] or self.scroll_left != original_scroll_pos[1] or
                self.status_message != original_status):
            changed_state = True

        return changed_state

    def validate_filename(self, filename: str) -> bool:
        """
        Validates the provided filename for basic correctness, length, and path restrictions.
        - Checks for empty or excessively long filenames.
        - Checks for invalid characters commonly disallowed in filenames.
        - Checks for reserved system names on Windows.
        - (Currently) restricts saving to the current working directory or its subdirectories.
        - Sets a status message and logs a warning if validation fails.

        Args:
            filename (str): The filename string to validate.

        Returns:
            bool: True if the filename is considered valid according to the defined rules,
                  False otherwise.
        """
        if not filename:
            self._set_status_message("Filename cannot be empty.")
            logging.warning("Validation failed: Filename is empty.")
            return False

        # Check for excessive length (common filesystem limit is 255 bytes/chars, but varies)
        # This is a general guideline.
        MAX_FILENAME_LEN = 255
        if len(filename) > MAX_FILENAME_LEN:
            self._set_status_message(f"Filename too long (max {MAX_FILENAME_LEN} chars).")
            logging.warning(f"Validation failed: Filename too long ({len(filename)} chars): '{filename[:50]}...'")
            return False

        # Strip leading/trailing whitespace, as these can cause issues or be invisible.
        stripped_filename = filename.strip()
        if not stripped_filename:  # If filename was only whitespace
            self._set_status_message("Filename cannot consist only of whitespace.")
            logging.warning("Validation failed: Filename is composed entirely of whitespace.")
            return False
        # Use the stripped version for further checks if you intend to save it stripped
        # or keep original `filename` and warn if it differs from `stripped_filename`.
        # For now, we'll validate the original `filename` for characters, but use `stripped_filename` for length logic.
        # It's generally better to operate on the `stripped_filename` for consistency.
        # Let's assume we operate on the original `filename` passed in, after initial checks.

        # Check for invalid characters in the filename component (basename).
        # Path separators are handled by path normalization and security checks later.
        basename_to_check = os.path.basename(filename)  # Check only the filename part for these chars
        # Common invalid characters for many filesystems (Windows, Linux, macOS)
        # Note: '/' and '\' are path separators, their presence in `basename_to_check` means
        # the input `filename` was likely just a name, not a path.
        # If `filename` is intended to be a full path, these chars are fine in the path part.
        # This regex is for the *name* part.
        invalid_chars_regex = r'[<>:"/\\|?*\x00-\x1F]'  # Control chars and common restricted symbols
        if re.search(invalid_chars_regex, basename_to_check):
            self._set_status_message(f"Filename '{basename_to_check}' contains invalid characters.")
            logging.warning(
                f"Validation failed: Filename part '{basename_to_check}' contains invalid characters: "
                f"Matched by regex '{invalid_chars_regex}'."
            )
            return False

        # Check for reserved system names (primarily a Windows concern).
        if os.name == 'nt':  # For Windows operating systems
            # Common reserved names (case-insensitive, without extension)
            windows_reserved_names = {
                "CON", "PRN", "AUX", "NUL",
                "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
                "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
            }
            # Get the name part without extension
            name_part_without_ext = os.path.splitext(basename_to_check)[0].upper()
            if name_part_without_ext in windows_reserved_names:
                self._set_status_message(f"Filename '{name_part_without_ext}' is a reserved system name on Windows.")
                logging.warning(
                    f"Validation failed: Filename '{filename}' (base: '{name_part_without_ext}') "
                    f"is a reserved system name on Windows."
                )
                return False

        # --- Path Security Checks (Attempt to restrict to current directory and subdirectories) ---
        # This section can be modified or removed if you want to allow saving anywhere.
        # Current implementation aims for basic safety by restricting path traversal.
        try:
            # Get the absolute, normalized path of the target filename.
            # os.path.abspath resolves relative paths and symbolic links (partially).
            # os.path.normpath cleans up ".." / "." and redundant separators.
            absolute_target_path = os.path.normpath(os.path.abspath(filename))

            # Get the absolute, normalized path of the current working directory.
            current_working_dir = os.path.normpath(os.path.abspath(os.getcwd()))

            # Disallow if the target path IS the current working directory itself (saving AS a directory).
            if absolute_target_path == current_working_dir:
                self._set_status_message("Cannot save: Target path is the current directory itself.")
                logging.warning(f"Validation failed: Attempt to save as current directory '{absolute_target_path}'.")
                return False

            # Check if the resolved absolute path is within the current working directory.
            # This is a common way to check for directory traversal.
            # It ensures that `absolute_target_path` is a child of `current_working_dir`.
            # os.path.commonpath (Python 3.5+) could also be used, but startswith is often sufficient.
            # We add os.sep to current_working_dir to ensure that "/ CWD /file" is valid,
            # but "/ CWD -suffix/file" is not considered inside "/ CWD /".
            if not absolute_target_path.startswith(current_working_dir + os.sep) and \
                    absolute_target_path != os.path.join(current_working_dir, os.path.basename(absolute_target_path)):
                # The second condition (absolute_target_path != os.path.join(...)) is a bit redundant
                # if startswith check is robust, but covers direct children in CWD more explicitly.
                # More simply, if we expect it to be a child or the file itself in CWD:
                # common_prefix = os.path.commonprefix([absolute_target_path, current_working_dir])
                # if common_prefix != current_working_dir:
                self._set_status_message(f"Path is outside the allowed directory: '{filename}'")
                logging.warning(
                    f"Validation failed: Path for '{filename}' resolved to '{absolute_target_path}', "
                    f"which is outside the current working directory '{current_working_dir}'."
                )
                return False

            # An additional check for ".." components in the *resolved* path, though normpath
            # should ideally resolve them. This is a stricter check.
            # If after normpath and abspath, ".." still exists, it implies a problematic path structure
            # or an attempt to write to a location that normpath couldn't fully simplify relative to root.
            if ".." in absolute_target_path.split(os.sep):
                self._set_status_message(f"Path appears to traverse upwards ('..'): '{filename}'")
                logging.warning(
                    f"Validation failed: Resolved path '{absolute_target_path}' for '{filename}' "
                    f"still contains '..' components after normalization."
                )
                return False

            logging.debug(
                f"Filename '{filename}' validated successfully. Resolved absolute path: '{absolute_target_path}'")
            return True

        except Exception as e_path:
            # Catch errors during path manipulation (e.g., path too long for OS functions, permission issues with abspath).
            self._set_status_message(f"Error validating file path: {str(e_path)[:70]}...")
            logging.error(f"Error validating filename path for '{filename}': {e_path}", exc_info=True)
            return False  # Treat any path processing error as a validation failure

    # =============выполнения команд оболочки Shell commands =================================
    def _execute_shell_command_async(self, cmd_list: List[str]) -> None:  # Added type hint
        """
        Executes a shell command in a separate thread and sends the result
        to the self._shell_cmd_q queue in a thread-safe manner.
        The result is a single string message summarizing the outcome.

        Args:
            cmd_list (List[str]): The command and its arguments as a list of strings.
        """
        # Initialize result variables for this execution
        captured_stdout: str = ""
        captured_stderr: str = ""
        result_message: str = ""  # This will be the final message sent to the queue
        exit_code: int = -1  # Default/unknown exit code

        process_handle: Optional[subprocess.Popen] = None  # To store Popen object for terminate/kill

        try:
            command_str_for_log = ' '.join(shlex.quote(c) for c in cmd_list)
            logging.debug(f"Async shell command: Preparing to execute: {command_str_for_log}")

            # Determine current working directory for the command
            # Prefer directory of the current file, fallback to os.getcwd()
            # Ensure self.filename is valid and exists if used for cwd
            cwd_path: str
            if self.filename and os.path.isfile(self.filename):  # Check if it's a file, not just exists
                cwd_path = os.path.dirname(os.path.abspath(self.filename))
            else:
                cwd_path = os.getcwd()
            logging.debug(f"Async shell command: Effective CWD: {cwd_path}")

            # Use subprocess.Popen for better control, especially for timeouts and stream handling
            process_handle = subprocess.Popen(
                cmd_list,
                stdout=subprocess.PIPE,  # Capture standard output
                stderr=subprocess.PIPE,  # Capture standard error
                text=True,  # Decode output as text (uses locale's encoding by default or specified)
                encoding="utf-8",  # Explicitly specify UTF-8 for decoding
                errors="replace",  # Replace undecodable characters
                cwd=cwd_path,  # Set the current working directory for the command
                universal_newlines=True  # Deprecated but often used with text=True for line ending normalization
                # For Python 3.7+, text=True implies universal_newlines=True effectively.
            )

            # Wait for the command to complete, with a timeout.
            # communicate() reads all output/error until EOF and waits for process to terminate.
            # Timeout is configurable via editor settings (e.g., self.config['shell']['timeout'])
            shell_timeout = self.config.get("shell", {}).get("timeout_seconds", 30)
            try:
                captured_stdout, captured_stderr = process_handle.communicate(timeout=shell_timeout)
                exit_code = process_handle.returncode
            except subprocess.TimeoutExpired:
                logging.warning(
                    f"Async shell command '{command_str_for_log}' timed out after {shell_timeout}s. Terminating.")
                result_message = f"Command timed out ({shell_timeout}s). Terminating."

                # Attempt to terminate and then kill the process
                try:
                    process_handle.terminate()  # Send SIGTERM
                    # Wait a bit for termination
                    try:
                        outs, errs = process_handle.communicate(timeout=5)  # Collect any final output
                        captured_stdout += outs if outs else ""
                        captured_stderr += errs if errs else ""
                    except subprocess.TimeoutExpired:  # Still didn't terminate
                        logging.warning(
                            f"Process '{command_str_for_log}' did not terminate gracefully, attempting kill.")
                        process_handle.kill()  # Send SIGKILL
                        # Try one last communicate to drain pipes after kill
                        try:
                            outs, errs = process_handle.communicate(timeout=1)
                            captured_stdout += outs if outs else ""
                            captured_stderr += errs if errs else ""
                        except Exception:
                            pass  # Ignore errors on communicate after kill
                except Exception as e_term:
                    logging.error(
                        f"Error during termination/kill of timed-out process '{command_str_for_log}': {e_term}")

                exit_code = process_handle.returncode if process_handle.returncode is not None else -2  # Indicate timeout/kill
                # Prepend to existing output/error if any was captured before timeout signal
                captured_stdout = f"(Output after timeout signal)\n{captured_stdout}"
                captured_stderr = f"(Error after timeout signal)\n{captured_stderr}"

            logging.debug(
                f"Async shell command '{command_str_for_log}' finished. "
                f"Exit code: {exit_code}. Stdout len: {len(captured_stdout)}. Stderr len: {len(captured_stderr)}."
            )

        except FileNotFoundError:
            # This occurs if the command executable itself is not found in PATH.
            result_message = f"Error: Executable not found: '{cmd_list[0]}'"
            logging.error(result_message)
            exit_code = -3  # Custom code for FileNotFoundError
        except Exception as e_exec:
            # Catch any other exceptions during Popen or initial setup.
            command_str_for_log_err = ' '.join(
                shlex.quote(c) for c in cmd_list) if 'cmd_list' in locals() else "Unknown command"
            logging.exception(f"Error executing shell command '{command_str_for_log_err}'")
            result_message = f"Execution error: {str(e_exec)[:80]}..."
            exit_code = -4  # Custom code for other execution errors

        finally:
            # Construct the final message based on outcome, if not already set by a major error.
            if not result_message:  # If no message was set by FileNotFoundError, Timeout, or general Exception
                if exit_code != 0:
                    # Command finished with a non-zero exit code (an error).
                    err_summary = captured_stderr.strip().splitlines()[0] if captured_stderr.strip() else "(no stderr)"
                    result_message = f"Command failed (code {exit_code}): {err_summary[:100]}"
                    if len(captured_stderr.strip().splitlines()) > 1 or len(err_summary) > 100:
                        result_message += "..."
                elif captured_stdout.strip():
                    # Command was successful (exit code 0) and produced output.
                    out_summary = captured_stdout.strip().splitlines()[0]
                    result_message = f"Command successful: {out_summary[:100]}"
                    if len(captured_stdout.strip().splitlines()) > 1 or len(out_summary) > 100:
                        result_message += "..."
                else:
                    # Command was successful (exit code 0) but produced no output.
                    result_message = "Command executed successfully (no output)."

            # Send the final result message to the queue for the main thread.
            try:
                self._shell_cmd_q.put(result_message)
                logging.debug(f"Async shell command result queued: '{result_message}'")
            except Exception as e_queue:
                logging.error(f"Failed to put shell command result into queue: {e_queue}", exc_info=True)

    def execute_shell_command(self) -> bool:
        """
        Prompts the user for a shell command, then executes it asynchronously.
        An initial status message "Running command..." is set.
        The actual result of the command will be displayed in the status bar later
        when processed from the _shell_cmd_q by the main loop.

        Returns:
            bool: True if the user provided a command and the process was initiated
                  (which involves setting a status message, thus needing a redraw).
                  False if the user cancelled the command prompt without entering a command
                  and the status message did not change from its original state.
        """
        logging.debug("execute_shell_command called")
        original_status = self.status_message
        status_changed_by_interaction = False

        # Prompt for the command
        # self.prompt handles its own temporary status line drawing.
        status_before_prompt = self.status_message
        command_str = self.prompt("Enter shell command: ")
        if self.status_message != status_before_prompt:  # If prompt itself changed status
            status_changed_by_interaction = True

        if not command_str:  # User cancelled or entered empty command at prompt
            if not status_changed_by_interaction and self.status_message == original_status:
                self._set_status_message("Shell command cancelled by user.")
            logging.debug("Shell command input cancelled by user or empty.")
            return self.status_message != original_status or status_changed_by_interaction

        # Parse the command string into a list of arguments
        cmd_list_args: List[str]
        try:
            cmd_list_args = shlex.split(command_str)
            if not cmd_list_args:  # Empty command after shlex.split (e.g., if input was only whitespace)
                self._set_status_message("Empty command entered.")
                logging.warning("Shell command execution failed: command was empty after parsing.")
                return True  # Status message changed
        except ValueError as e_shlex:  # Error during shlex.split (e.g., unmatched quotes)
            self._set_status_message(f"Command parse error: {e_shlex}")
            logging.error(f"Shell command parse error for '{command_str}': {e_shlex}")
            return True  # Status message changed

        # --- Set status to "Running command..." and start the thread ---
        # This message will be displayed while the command runs in the background.
        display_command_str = ' '.join(shlex.quote(c) for c in cmd_list_args)
        if len(display_command_str) > 60:  # Truncate for status bar
            display_command_str = display_command_str[:57] + "..."

        self._set_status_message(f"Running: {display_command_str}")

        # Start the command execution in a separate thread
        thread_name = f"ShellExecThread-{cmd_list_args[0]}-{int(time.time())}"
        command_execution_thread = threading.Thread(
            target=self._execute_shell_command_async,
            args=(cmd_list_args,),
            daemon=True,  # Thread will exit when the main program exits
            name=thread_name
        )
        command_execution_thread.start()

        logging.debug(f"Started shell command execution thread: {thread_name} for command: {cmd_list_args}")

        # The method has initiated an async action and set a status message.
        return True  # Status message changed, redraw needed.


    def toggle_insert_mode(self) -> bool:
        """
        Toggles between Insert and Replace (Overwrite) modes for text input.

        - Insert Mode (default): Characters are inserted at the cursor position,
          shifting existing characters to the right.
        - Replace Mode: Characters typed replace the character currently under
          the cursor. If at the end of the line, characters are appended.

        This method updates the `self.insert_mode` flag and sets a status message
        indicating the new mode.

        Returns:
            bool: True, as this action always changes the editor's mode and
                  updates the status message, thus requiring a redraw of the status bar.
        """
        original_status = self.status_message  # For robust True/False return
        original_insert_mode = self.insert_mode

        self.insert_mode = not self.insert_mode  # Toggle the mode

        mode_text_indicator = "Insert" if self.insert_mode else "Replace"

        logging.debug(f"Insert mode toggled. New mode: {mode_text_indicator}")
        self._set_status_message(f"Mode: {mode_text_indicator}")

        # Return True if the mode actually changed or if the status message changed.
        # Since _set_status_message is always called with a new mode indicator,
        # the status message will almost certainly change unless it was already displaying
        # the exact same "Mode: ..." message (highly unlikely for a toggle).
        if self.insert_mode != original_insert_mode or self.status_message != original_status:
            return True

        return False  # Should be rare, e.g. if status somehow didn't update to the new mode text

    # ==================== bracket =======================
    # this method designed to search for a matching bracket across multiple lines
    def find_matching_bracket_multiline(self, initial_char_y: int, initial_char_x: int) -> Optional[Tuple[int, int]]:
        """
        Searches for the matching bracket for the one at (initial_char_y, initial_char_x)
        across multiple lines.
        This is a simplified version and does NOT consider string literals or comments,
        which can lead to incorrect matches in source code.

        Args:
            initial_char_y (int): The row of the bracket to start searching from.
            initial_char_x (int): The column of the bracket to start searching from.

        Returns:
            Optional[Tuple[int, int]]: (row, col) of the matching bracket, or None if not found.
        """
        if not (0 <= initial_char_y < len(self.text) and 0 <= initial_char_x < len(self.text[initial_char_y])):
            return None  # Initial position is out of bounds

        char_at_cursor = self.text[initial_char_y][initial_char_x]

        brackets_map = {"(": ")", "{": "}", "[": "]", "<": ">", ")": "(", "}": "{", "]": "[", ">": "<"}
        open_brackets = "({[<"

        if char_at_cursor not in brackets_map:
            return None  # Character at cursor is not a bracket we handle

        target_match_char = brackets_map[char_at_cursor]
        level = 1  # Start at level 1, looking for the char that brings it to 0

        if char_at_cursor in open_brackets:
            # Search forward for the closing bracket
            current_y, current_x = initial_char_y, initial_char_x + 1
            while current_y < len(self.text):
                line = self.text[current_y]
                while current_x < len(line):
                    char = line[current_x]
                    if char == char_at_cursor:  # Found another opening bracket of the same type
                        level += 1
                    elif char == target_match_char:  # Found a potential matching closing bracket
                        level -= 1
                        if level == 0:
                            return (current_y, current_x)  # Match found
                    current_x += 1
                current_y += 1
                current_x = 0  # Reset column for the new line
        else:  # char_at_cursor is a closing bracket, search backward for the opening one
            current_y, current_x = initial_char_y, initial_char_x - 1
            while current_y >= 0:
                line = self.text[current_y]
                while current_x >= 0:
                    char = line[current_x]
                    if char == char_at_cursor:
                        level += 1
                    elif char == target_match_char:
                        level -= 1
                        if level == 0:
                            return (current_y, current_x)
                    current_x -= 1
                current_y -= 1
                if current_y >= 0:
                    current_x = len(self.text[current_y]) - 1

        return None  # No match found

    def highlight_matching_brackets(self) -> None:
        """
        Highlights the bracket at the cursor and its matching pair.

        This method searches for a bracket character at or immediately to the
        left of the current cursor position. If a bracket is found, it uses
        `find_matching_bracket_multiline` to locate its corresponding pair.
        If both brackets are found and are visible on the screen, they are
        highlighted using `curses.A_REVERSE`.

        The method accounts for:
            - Cursor position being at the end of a line or on an empty line.
            - Vertical and horizontal scrolling to determine visibility.
            - Display widths of characters (via `self.get_char_width` and `self.get_string_width`).

        This method is typically called as part of the main drawing cycle and
        modifies the screen directly using `self.stdscr.chgat()`. It does not
        perform `self.stdscr.refresh()` itself.

        Note:
            This implementation does NOT currently ignore brackets found within
            string literals or comments, which can lead to incorrect matches in
            source code.

        Args:
            None

        Returns:
            None
        """
        # 1. Get terminal dimensions and ensure basic conditions are met.
        term_height, term_width = self.stdscr.getmaxyx()
        # Bounds check for cursor position
        if not (0 <= self.cursor_y < len(self.text)):
            logging.debug("highlight_matching_brackets: Cursor Y (%d) is out of text bounds (0-%d).",
                          self.cursor_y, len(self.text) - 1)
            return

        # Check if cursor's line is visible on the screen
        if not (self.scroll_top <= self.cursor_y < self.scroll_top + self.visible_lines):
            logging.debug(
                "highlight_matching_brackets: Cursor's line (%d) is not currently visible on screen (scroll_top: %d, visible_lines: %d).",
                self.cursor_y, self.scroll_top, self.visible_lines
            )
            return

        current_line_text = self.text[self.cursor_y]
        if not current_line_text and self.cursor_x == 0:
            logging.debug("highlight_matching_brackets: Cursor is on an empty line at column 0.")
            return

        # 1.1 Find the bracket at or near the cursor
        brackets_map_chars = "(){}[]<>"
        bracket_pos = None

        if 0 <= self.cursor_x < len(current_line_text) and current_line_text[self.cursor_x] in brackets_map_chars:
            bracket_pos = (self.cursor_y, self.cursor_x)
            logging.debug(
                f"highlight_matching_brackets: Candidate bracket '{current_line_text[self.cursor_x]}' AT cursor ({self.cursor_y},{self.cursor_x}).")
        elif self.cursor_x > 0 and current_line_text[self.cursor_x - 1] in brackets_map_chars:
            bracket_pos = (self.cursor_y, self.cursor_x - 1)
            logging.debug(
                f"highlight_matching_brackets: Candidate bracket '{current_line_text[self.cursor_x - 1]}' LEFT of cursor ({self.cursor_y},{self.cursor_x - 1}).")
        else:
            logging.debug(
                f"highlight_matching_brackets: No suitable bracket found near cursor ({self.cursor_y},{self.cursor_x}) for matching.")
            return

        # 2. Find the matching bracket using the determined position
        bracket_char = self.text[bracket_pos[0]][bracket_pos[1]]
        match_coords = self.find_matching_bracket_multiline(bracket_pos[0], bracket_pos[1])

        if not match_coords:
            logging.debug(
                f"highlight_matching_brackets: No matching bracket found for '{bracket_char}' at ({bracket_pos[0]},{bracket_pos[1]}).")
            return

        match_y, match_x = match_coords
        if not (0 <= match_y < len(self.text) and 0 <= match_x < len(self.text[match_y])):
            logging.warning(
                f"highlight_matching_brackets: Matching bracket coords ({match_y},{match_x}) are out of text bounds.")
            return

        # 3. Calculate the display width of the line number column
        line_num_display_width = len(str(max(1, len(self.text)))) + 1
        if hasattr(self.drawer, '_text_start_x') and isinstance(self.drawer._text_start_x,
                                                                int) and self.drawer._text_start_x >= 0:
            line_num_display_width = self.drawer._text_start_x
        else:
            logging.debug(
                "highlight_matching_brackets: self.drawer._text_start_x not available or invalid, calculating line_num_display_width locally.")

        def get_screen_coords_for_highlight(text_row_idx: int, text_col_idx: int) -> Optional[tuple[int, int]]:
            """
            Calculates screen (y, x) for a text coordinate.

            Args:
                text_row_idx (int): The 0-based row index in the text buffer.
                text_col_idx (int): The 0-based character column index in the line.

            Returns:
                Optional[Tuple[int, int]]: The screen coordinates if visible, otherwise None.
            """
            if not (self.scroll_top <= text_row_idx < self.scroll_top + self.visible_lines):
                return None
            screen_y_coord = text_row_idx - self.scroll_top
            try:
                if not (0 <= text_row_idx < len(self.text)):
                    logging.warning(
                        f"get_screen_coords_for_highlight: text_row_idx {text_row_idx} out of bounds for self.text.")
                    return None
                clamped_text_col_idx = max(0, min(text_col_idx, len(self.text[text_row_idx])))
                prefix_width_unscrolled = self.get_string_width(self.text[text_row_idx][:clamped_text_col_idx])
            except IndexError:
                logging.warning(
                    f"get_screen_coords_for_highlight: IndexError accessing text for ({text_row_idx},{text_col_idx}).")
                return None
            screen_x_coord = line_num_display_width + prefix_width_unscrolled - self.scroll_left
            if text_col_idx >= len(self.text[text_row_idx]):
                logging.warning(
                    f"get_screen_coords_for_highlight: text_col_idx {text_col_idx} is at or past EOL for line {text_row_idx} (len {len(self.text[text_row_idx])}). Cannot get char width for highlighting.")
                return None
            char_display_width_at_coord = self.get_char_width(self.text[text_row_idx][text_col_idx])
            if char_display_width_at_coord <= 0:
                logging.debug(
                    f"get_screen_coords_for_highlight: Character at ({text_row_idx},{text_col_idx}) has width {char_display_width_at_coord}, not highlighting directly.")
                return None
            if screen_x_coord >= term_width or (screen_x_coord + char_display_width_at_coord) <= line_num_display_width:
                return None
            return screen_y_coord, max(line_num_display_width, screen_x_coord)

        # 4. Calculate screen coordinates for both brackets
        coords1_on_screen = get_screen_coords_for_highlight(bracket_pos[0], bracket_pos[1])
        coords2_on_screen = get_screen_coords_for_highlight(match_y, match_x)

        # 5. Highlight brackets if visible on screen
        highlight_attr = curses.A_REVERSE

        if coords1_on_screen:
            scr_y1, scr_x1 = coords1_on_screen
            char1_width = self.get_char_width(self.text[bracket_pos[0]][bracket_pos[1]])
            if scr_x1 < term_width and char1_width > 0:
                visible_cells_of_char1 = min(char1_width, term_width - scr_x1)
                if visible_cells_of_char1 > 0:
                    try:
                        self.stdscr.chgat(scr_y1, scr_x1, visible_cells_of_char1, highlight_attr)
                        logging.debug(
                            f"Highlighted bracket 1 ('{bracket_char}') at screen ({scr_y1},{scr_x1}) for {visible_cells_of_char1} cells, "
                            f"text ({bracket_pos[0]},{bracket_pos[1]})"
                        )
                    except curses.error as e:
                        logging.warning(f"Curses error highlighting bracket 1 at screen ({scr_y1},{scr_x1}): {e}")

        if coords2_on_screen:
            scr_y2, scr_x2 = coords2_on_screen
            char2_width = self.get_char_width(self.text[match_y][match_x])
            if scr_x2 < term_width and char2_width > 0:
                visible_cells_of_char2 = min(char2_width, term_width - scr_x2)
                if visible_cells_of_char2 > 0:
                    try:
                        self.stdscr.chgat(scr_y2, scr_x2, visible_cells_of_char2, highlight_attr)
                        logging.debug(
                            f"Highlighted bracket 2 ('{self.text[match_y][match_x]}') at screen ({scr_y2},{scr_x2}) for {visible_cells_of_char2} cells, "
                            f"text ({match_y},{match_x})"
                        )
                    except curses.error as e:
                        logging.warning(f"Curses error highlighting bracket 2 at screen ({scr_y2},{scr_x2}): {e}")

    # NEW scrolling
    # ==================== HELP ==================================
    def _build_help_lines(self) -> list[str]:
        # This method seems correct as is, based on your provided code.
        # It uses self.config to get keybindings.
        def _kb(action: str, default: str) -> str:
            """Return a prettified key‑binding string for *action*."""
            # Ensure self.config is accessed correctly
            raw = self.config.get("keybindings", {}).get(action, default)
            if isinstance(raw, int):  # If the binding is a direct int (curses code), use default string
                raw = default
            elif isinstance(raw, list):  # If it's a list, take the first string representation or default
                str_bindings = [item for item in raw if isinstance(item, str)]
                raw = str_bindings[0] if str_bindings else default

            parts = str(raw).strip().lower().split('+')
            formatted = []
            for part in parts:
                if part in {"ctrl", "alt", "shift"}:
                    formatted.append(part.capitalize())
                elif len(part) == 1 and part.isalpha():
                    formatted.append(part.upper())
                elif part.startswith("f") and part[1:].isdigit():
                    formatted.append(part.upper())
                else:
                    # Capitalize if fully alphabetic, otherwise use as is
                    formatted.append(part.capitalize() if part.isalpha() else part)
            return '+'.join(formatted)

        defaults = {
            "new_file": "F2", "open_file": "Ctrl+O", "save_file": "Ctrl+S",
            "save_as": "F5", "quit": "Ctrl+Q", "undo": "Ctrl+Z",
            "redo": "Ctrl+Y", "copy": "Ctrl+C", "cut": "Ctrl+X",
            "paste": "Ctrl+V", "select_all": "Ctrl+A", "delete": "Del",
            "goto_line": "Ctrl+G", "find": "Ctrl+F", "find_next": "F3",
            "search_and_replace": "F6", "lint": "F4", "git_menu": "F9",
            "help": "F1", "cancel_operation": "Esc", "tab": "Tab",
            "shift_tab": "Shift+Tab", "toggle_comment_block": "Ctrl+\\"
        }
        return [
            "                 ──  Sway-Pad Help  ──  ", "",
            "  File Operations:",
            f"    {_kb('new_file', defaults['new_file']):<22}: New file",
            f"    {_kb('open_file', defaults['open_file']):<22}: Open file",
            f"    {_kb('save_file', defaults['save_file']):<22}: Save",
            f"    {_kb('save_as', defaults['save_as']):<22}: Save as…",
            f"    {_kb('quit', defaults['quit']):<22}: Quit editor",
            "", "  Editing:",
            f"    {_kb('copy', defaults['copy']):<22}: Copy",
            f"    {_kb('cut', defaults['cut']):<22}: Cut",
            f"    {_kb('paste', defaults['paste']):<22}: Paste",
            f"    {_kb('select_all', defaults['select_all']):<22}: Select all",
            f"    {_kb('undo', defaults['undo']):<22}: Undo",
            f"    {_kb('redo', defaults['redo']):<22}: Redo",
            f"    {_kb('delete', defaults['delete']):<22}: Delete char/selection",
            "    Backspace             : Delete char left / selection",
            f"    {_kb('tab', defaults['tab']):<22}: Smart Tab / Indent block",
            f"    {_kb('shift_tab', defaults['shift_tab']):<22}: Smart Unindent / Unindent block",
            f"    {_kb('toggle_comment_block', defaults['toggle_comment_block']):<22}: Comment/Uncomment block/line",
            "", "  Navigation & Search:",
            f"    {_kb('goto_line', defaults['goto_line']):<22}: Go to line",
            f"    {_kb('find', defaults['find']):<22}: Find (prompt)",
            f"    {_kb('find_next', defaults['find_next']):<22}: Find next occurrence",
            f"    {_kb('search_and_replace', defaults['search_and_replace']):<22}: Search & Replace (regex)",
            "    Arrows, Home, End     : Cursor movement",
            "    PageUp, PageDown      : Scroll by page",
            "    Shift+Nav Keys        : Extend selection",
            "", "  Tools & Features:",
            f"    {_kb('lint', defaults['lint']):<22}: Diagnostics (LSP/Linters)",  # Updated
            f"    {_kb('git_menu', defaults['git_menu']):<22}: Git menu",
            f"    {_kb('help', defaults['help']):<22}: This help screen",
            f"    {_kb('cancel_operation', defaults['cancel_operation']):<22}: Cancel / Close Panel",  # Simplified
            "    Insert Key            : Toggle Insert/Replace mode",
            "", "   ────────────────────────────────────────────────────",
            "", "              Press any key to close help",  # Changed
            "", "               Licensed under the GPL v3 ",
            "", "               © 2025 Siergej Sobolewski",
        ]

    def show_help(self) -> bool:
        """Displays a centered, scrollable help window.
        Uses textual indicators for scrolling and adapts colors based on terminal capabilities.
        """

        lines = self._build_help_lines()

        if not lines:  # Should not happen if _build_help_lines is robust
            self._set_status_message("Error: Help content is empty.")
            return True

        term_h, term_w = self.stdscr.getmaxyx()

        # Calculate window dimensions for the help panel
        # Ensure some padding and that it doesn't exceed terminal dimensions
        text_max_width = 0
        if lines:  # Avoid error if lines is empty, though we check above
            text_max_width = max(len(line) for line in lines)

        # Add padding for borders and internal margins
        view_w = min(text_max_width + 6, term_w - 4)  # +2 for side borders, +4 for side margins
        view_h = min(len(lines) + 4, term_h - 4)  # +2 for top/bottom borders, +2 for top/bottom margins

        # Ensure minimum dimensions
        view_w = max(20, view_w)  # Minimum width for readability
        view_h = max(8, view_h)  # Minimum height

        # Center the help window
        view_y = max(0, (term_h - view_h) // 2)
        view_x = max(0, (term_w - view_w) // 2)

        # Re-check if dimensions are too small even after adjustments
        if view_h <= 4 or view_w <= 4:  # Need space for border and content
            self._set_status_message("Terminal too small for help.")
            return True

        original_cursor_visibility = curses.curs_set(0)  # Hide cursor for help screen

        # --- Color Attributes ---
        # Defaults for monochrome or very limited color terminals
        default_text_attr = curses.A_NORMAL
        default_bg_attr = curses.A_NORMAL  # Will be background of the help window
        default_border_attr = curses.A_BOLD
        default_scroll_attr = curses.A_BOLD | curses.A_REVERSE

        # Attempt to use nicer colors if available
        try:
            if curses.has_colors():
                # Use editor's pre-defined colors if they fit the semantic need,
                # or define new pairs if necessary and COLOR_PAIRS allows.
                # For simplicity, let's define specific pairs for help if > 8 colors.
                # Ensure these pair IDs don't clash with those in self.init_colors()
                # It's safer to use a range of pair IDs known to be free.
                # Example: use pairs starting from 30 upwards if 0-20 are used by init_colors.

                HELP_PAIR_ID_START = 30  # Arbitrary start for help-specific color pairs

                if curses.COLORS >= 256 and curses.COLOR_PAIRS > HELP_PAIR_ID_START + 2:
                    # 256+ color mode: e.g., light text on a dark grey background
                    curses.init_pair(HELP_PAIR_ID_START, 231, 236)  # fg: almost white, bg: dark grey
                    default_bg_attr = curses.color_pair(HELP_PAIR_ID_START)
                    default_text_attr = curses.color_pair(HELP_PAIR_ID_START)

                    curses.init_pair(HELP_PAIR_ID_START + 1, 250, 236)  # fg: lighter grey for border
                    default_border_attr = curses.color_pair(HELP_PAIR_ID_START + 1) | curses.A_BOLD

                    curses.init_pair(HELP_PAIR_ID_START + 2, 226, 236)  # fg: yellow for scroll indicators
                    default_scroll_attr = curses.color_pair(HELP_PAIR_ID_START + 2) | curses.A_BOLD
                elif curses.COLORS >= 8 and curses.COLOR_PAIRS > HELP_PAIR_ID_START + 2:
                    # 8/16 color mode: e.g., white text on blue background
                    curses.init_pair(HELP_PAIR_ID_START, curses.COLOR_WHITE, curses.COLOR_BLUE)
                    default_bg_attr = curses.color_pair(HELP_PAIR_ID_START)
                    default_text_attr = curses.color_pair(HELP_PAIR_ID_START)

                    curses.init_pair(HELP_PAIR_ID_START + 1, curses.COLOR_CYAN, curses.COLOR_BLUE)
                    default_border_attr = curses.color_pair(HELP_PAIR_ID_START + 1) | curses.A_BOLD

                    # For scroll indicator, use existing reverse or a specific pair
                    curses.init_pair(HELP_PAIR_ID_START + 2, curses.COLOR_BLACK, curses.COLOR_WHITE)  # Black on White
                    default_scroll_attr = curses.color_pair(HELP_PAIR_ID_START + 2)
                # If fewer than 8 colors or not enough pairs, the A_NORMAL defaults will be used.
        except curses.error as e_color:
            logging.warning(f"SwayEditor.show_help: Curses error initializing help colors: {e_color}. Using defaults.")
        except Exception as e_gen_color:
            logging.error(f"SwayEditor.show_help: General error initializing help colors: {e_gen_color}", exc_info=True)

        try:
            win = curses.newwin(view_h, view_w, view_y, view_x)
            win.keypad(True)
            win.bkgd(" ", default_bg_attr)  # Apply the background to the new window

            content_display_height = view_h - 2  # Available height for text lines (excluding borders)
            max_lines_on_screen = content_display_height
            total_content_lines = len(lines)
            max_scroll_offset = max(0, total_content_lines - max_lines_on_screen)
            current_scroll_top = 0

            SCROLL_UP_INDICATOR = "↑ (PgUp/k)"  # More informative
            SCROLL_DN_INDICATOR = "↓ (PgDn/j)"

            while True:
                win.erase()  # Clear window content for this frame

                # Draw border
                win.attron(default_border_attr)
                win.border()
                win.attroff(default_border_attr)

                # Display visible portion of help lines
                for i in range(max_lines_on_screen):
                    line_idx_in_buffer = current_scroll_top + i
                    if line_idx_in_buffer < total_content_lines:
                        line_to_display = lines[line_idx_in_buffer]
                        # Truncate line if it's wider than the content area of the help window
                        # Content area width: view_w - 2 (for borders) - 2 (for L/R margins)
                        drawable_text_width = view_w - 4
                        if len(line_to_display) > drawable_text_width:
                            # A simple truncation; consider wcwidth for Unicode if needed here
                            line_to_display = line_to_display[:drawable_text_width - 3] + "..."

                        try:
                            # Draw text with left/right margin of 1 char inside the border
                            win.addstr(i + 1, 2, line_to_display, default_text_attr)
                        except curses.error:  # Curses can fail if trying to write outside window
                            pass

                # Draw scroll indicators if scrolling is possible
                if current_scroll_top > 0:
                    try:
                        # Position at top-right corner, inside border
                        win.addstr(1, view_w - (len(SCROLL_UP_INDICATOR) + 2), SCROLL_UP_INDICATOR, default_scroll_attr)
                    except curses.error:
                        pass

                if current_scroll_top < max_scroll_offset:
                    try:
                        # Position at bottom-right corner, inside border
                        win.addstr(view_h - 2, view_w - (len(SCROLL_DN_INDICATOR) + 2), SCROLL_DN_INDICATOR,
                                   default_scroll_attr)
                    except curses.error:
                        pass

                # Optional: Display scroll position (e.g., "Line X/Y")
                if max_scroll_offset > 0:
                    # Display at bottom-left corner, inside border
                    scroll_pos_info = f"{(current_scroll_top + 1)}-{min(current_scroll_top + max_lines_on_screen, total_content_lines)}/{total_content_lines}"
                    try:
                        win.addstr(view_h - 2, 2, scroll_pos_info, default_scroll_attr)
                    except curses.error:
                        pass

                win.refresh()
                key_press = win.getch()

                if key_press in (curses.KEY_UP, ord('k'), ord('K')):
                    current_scroll_top = max(0, current_scroll_top - 1)
                elif key_press in (curses.KEY_DOWN, ord('j'), ord('J')):
                    current_scroll_top = min(max_scroll_offset, current_scroll_top + 1)
                elif key_press == curses.KEY_PPAGE:
                    current_scroll_top = max(0, current_scroll_top - max_lines_on_screen)
                elif key_press == curses.KEY_NPAGE:
                    current_scroll_top = min(max_scroll_offset, current_scroll_top + max_lines_on_screen)
                elif key_press in (curses.KEY_HOME, ord('g')):  # 'g' like in less/vim
                    current_scroll_top = 0
                elif key_press in (curses.KEY_END, ord('G')):  # 'G' like in less/vim
                    current_scroll_top = max_scroll_offset
                elif key_press == curses.KEY_RESIZE:
                    # Re-calculate dimensions and redraw on resize
                    term_h, term_w = self.stdscr.getmaxyx()
                    view_w = min(text_max_width + 6, term_w - 4)
                    view_w = max(20, view_w)
                    view_h = min(len(lines) + 4, term_h - 4)
                    view_h = max(8, view_h)
                    view_y = max(0, (term_h - view_h) // 2)
                    view_x = max(0, (term_w - view_w) // 2)
                    try:
                        win.resize(view_h, view_w)
                        win.mvwin(view_y, view_x)
                    except curses.error:
                        pass
                    content_display_height = view_h - 2
                    max_lines_on_screen = content_display_height
                    max_scroll_offset = max(0, total_content_lines - max_lines_on_screen)
                    current_scroll_top = min(current_scroll_top, max_scroll_offset)
                else:
                    # Any other key closes the help window
                    break

        except curses.error as e_curses_help:
            logging.error(f"SwayEditor.show_help: Curses error in help window main loop: {e_curses_help}",
                          exc_info=True)
            self._set_status_message(f"Help display error: {e_curses_help}")
        except Exception as e_general_help:
            logging.error(f"SwayEditor.show_help: General error in help window: {e_general_help}", exc_info=True)
            self._set_status_message(f"Help display error: {e_general_help}")
        finally:
            if original_cursor_visibility is not None and original_cursor_visibility != curses.ERR:
                try:
                    curses.curs_set(original_cursor_visibility)
                except curses.error:
                    pass

            # Explicitly clear the main screen and refresh to remove help window artifacts
            # and ensure the editor UI is fully redrawn.
            try:
                self.stdscr.clear()
                self.stdscr.refresh()  # This might be too aggressive if main loop handles redraw well
            except curses.error:
                pass

            self._set_status_message("Help closed")
            self._force_full_redraw = True  # Signal main loop to redraw everything
            return True  # Indicates status changed or a major UI interaction happened.

    # AI
    def request_ai_explanation(self) -> bool:
        """Sends selected text to the AI for an explanation."""
        selected_text = self.get_selected_text()
        if not selected_text:
            self._set_status_message("No text selected to explain.")
            return True # Статус изменился

        prompt_text = f"Please explain the following code snippet:\n\n```\n{selected_text}\n```"
        
        # Отправляем задачу в асинхронный движок
        self.async_engine.submit_task({
            "type": "ai_chat",
            "prompt": prompt_text
        })
        
        self._set_status_message("Sent request to AI assistant...")
        logging.info("Submitted AI explanation task.")
        return True # Статус изменился

    # ==================== QUEUE PROCESSING =======================
    def _process_all_queues(self) -> bool:
        """
        Processes messages from all internal queues (general status, shell commands, etc.)
        and delegates Git-specific queue processing to the GitBridge component.

        This method updates the editor's state based on these messages, primarily by
        setting the status message. It's called on each iteration of the main loop.

        Returns:
            bool: True if any message was processed that resulted in a change to
                the editor's state (like self.status_message), thus requiring a redraw.
                Returns False otherwise.
        """
        any_state_changed_by_queues = False

        # --- 1. General status message queue (_msg_q) ---
        # This queue is populated by _set_status_message and handles general UI feedback.
        try:
            while True:
                # Process all available messages without blocking
                msg_item = self._msg_q.get_nowait()
                if self.status_message != str(msg_item):
                    self.status_message = str(msg_item)
                    any_state_changed_by_queues = True
                logging.debug(f"Processed message from main queue: '{self.status_message}'")
        except queue.Empty:
            pass  # The queue is empty, which is the normal case.

        # --- 2. Shell command results queue (_shell_cmd_q) ---
        try:
            while True:
                shell_result_msg = self._shell_cmd_q.get_nowait()
                if self.status_message != str(shell_result_msg):
                    self.status_message = str(shell_result_msg)
                    any_state_changed_by_queues = True
                logging.debug(f"Processed shell command result: '{self.status_message}'")
        except queue.Empty:
            pass

        # --- 3. Delegate Git queue processing to the GitBridge component ---
        if self.git.process_queues():
            any_state_changed_by_queues = True

        # --- 4. Delegate Linter/LSP queue processing to the LinterBridge component ---
        # This is the corrected part. We call the public method on our bridge component.
        if self.linter_bridge.process_lsp_queue():
            any_state_changed_by_queues = True

        # --- 5. For ASYNC ENGINE QUEUE ---
        try:
            while True:
                async_result = self._async_results_q.get_nowait()
                logging.debug(f"Processed async result: {async_result}")
                
                # Обрабатываем результат
                if async_result.get("type") == "ai_reply":
                    # Например, показываем ответ в панели или статус-баре
                    reply_text = async_result.get("text", "AI response was empty.")
                    # Давайте для простоты покажем его в статус-баре
                    self._set_status_message(f"AI: {reply_text[:100]}")
                elif async_result.get("type") == "task_error":
                    error_msg = async_result.get("error", "Unknown async error.")
                    self._set_status_message(f"Async Error: {error_msg[:100]}")
                
                any_state_changed_by_queues = True

        except queue.Empty:
            pass

        # --- 6. Lint panel auto-hide logic ---
        if self.lint_panel_active and self.lint_panel_message:
            if hasattr(self, "drawer") and hasattr(self.drawer, "_keep_lint_panel_alive"):
                self.drawer._keep_lint_panel_alive()

        return any_state_changed_by_queues


    # =====================  Main editor loop  ============================
    def run(self) -> None:
        """
        The main event loop of the editor.

        This loop continuously performs three main tasks:
        1. Processes background events from queues (e.g., Git status, linter results).
        2. Handles user input from the keyboard.
        3. Redraws the screen if any state has changed.

        The loop is optimized to redraw only when necessary and is throttled by a
        target FPS to prevent excessive CPU usage. It also includes robust error
        handling to keep the editor responsive even if a component fails.
        """
        logging.info("Editor main loop started.")

        # Set the terminal to non-blocking mode for responsive input.
        self.stdscr.nodelay(True)
        # Enable keypad mode to properly interpret special keys (arrows, F-keys, etc.).
        self.stdscr.keypad(True)

        needs_redraw = True  # Force an initial draw when the editor starts.
        last_draw_time = 0.0  # Timestamp of the last screen draw.
        
        # Read target FPS from config, with a safe default.
        try:
            # Use a more descriptive variable name.
            target_fps = int(self.config.get("editor", {}).get("target_fps", 30))
            if target_fps <= 0:
                target_fps = 30 # Ensure FPS is a positive number.
        except (ValueError, TypeError):
            target_fps = 30 # Fallback if config value is invalid.
            
        min_frame_time = 1.0 / target_fps

        while True:
            try:
                # --- 1. Process background queues ---
                # This block is now cleaner and more direct.
                if self._process_all_queues():
                    needs_redraw = True

                # --- 2. Get and handle user input ---
                # This logic remains largely the same, but let's refine error handling.
                key_input = self.keybinder.get_key_input_tty_safe()

                if key_input != curses.ERR:
                    logging.debug(f"Raw key from get_wch(): {repr(key_input)} (type: {type(key_input).__name__})")
                    
                    # Check for state changes from input handling.
                    if self.keybinder.handle_input(key_input):
                        needs_redraw = True

                # --- 3. Draw the screen if needed and FPS allows ---
                current_time = time.monotonic()
                # The check `self._force_full_redraw` is now handled inside DrawScreen,
                # but we still need to trigger the draw if the flag is set.
                # We can rely on `needs_redraw` which should be set by methods that
                # also set _force_full_redraw.
                if needs_redraw and (current_time - last_draw_time >= min_frame_time):
                    
                    # The drawer component handles all drawing logic.
                    self.drawer.draw()

                    last_draw_time = current_time
                    needs_redraw = False  # Reset the flag after a successful draw.

                # --- 4. Brief sleep to yield CPU and control loop speed ---
                # This prevents the loop from spinning at 100% CPU.
                # A small sleep is better than no sleep.
                time.sleep(0.005)

            except KeyboardInterrupt:
                # User pressed Ctrl+C (if not ignored by signal handler)
                logging.info("KeyboardInterrupt received in main loop, initiating exit.")
                self.exit_editor()
                return  # Exit the `run` method and the loop.

            except curses.error as e:
                # Catch curses-specific errors that might occur in the loop.
                # The "no input" error from get_wch is expected and handled by `get_key_input...`,
                # so we only log other curses errors.
                if "no input" not in str(e).lower():
                    logging.error("A Curses error occurred in the main loop: %s", e, exc_info=True)
                    self._set_status_message(f"UI Error: {e}")
                    curses.flushinp()  # Clear any pending, potentially problematic input.
                    needs_redraw = True

            except Exception as e:
                # A catch-all for any other unexpected errors to prevent the editor from crashing.
                logging.critical("An unhandled exception occurred in the main loop: %s", e, exc_info=True)
                self._set_status_message("Critical loop error! Check logs.")
                # It's often safer to attempt a redraw to show the error, then maybe exit.
                needs_redraw = True
                # For a critical error, you might want to break the loop after a short delay.
                # time.sleep(2)
                # self.exit_editor()
                # return


def main_curses_function(stdscr):
    """
    Initializes and runs the SwayPad editor inside the curses wrapper context.

    This function sets up locale and signal handling, initializes the editor,
    processes command-line arguments, and starts the main event loop. It is
    intended to be passed to `curses.wrapper()` as the main entry point
    for the terminal-based editor.

    Signal handling is used to ignore suspension (Ctrl+Z) and interrupt (Ctrl+C)
    where supported, preventing the editor from being stopped unexpectedly by these keys.

    Locale is set to the system default to support correct character width calculations
    and other locale-dependent behaviors.

    If a filename is provided via the command line, it is loaded into the editor;
    otherwise, an empty buffer is used.

    Any unhandled exceptions during editor execution are logged in detail to
    multiple places (log files, stderr, and a dedicated critical error log file),
    and the exception is re-raised to ensure curses restores the terminal.

    Args:
        stdscr (curses.window): The curses standard screen object provided by curses.wrapper().

    Raises:
        Exception: Any unhandled exception encountered during the editor's lifetime is re-raised
            after logging for proper cleanup and debugging.
    """
    # Signal handling (Ctrl+Z, Ctrl+C)
    try:
        # These might not work on all platforms (e.g. Windows) or in all terminal emulators.
        if hasattr(signal, 'SIGTSTP'):
            signal.signal(signal.SIGTSTP, signal.SIG_IGN)  # Ignore Ctrl+Z (suspend)
        if hasattr(signal, 'SIGINT'):
            signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ignore Ctrl+C (interrupt)
        logger.debug("Attempted to set SIGTSTP and SIGINT to ignore.")
    except Exception as e_signal:
        logger.warning(f"Couldn't ignore SIGTSTP/SIGINT: {e_signal}")

    # Set locale for character width calculations (wcwidth) and other locale-sensitive operations.
    # An empty string "" uses the system's default locale settings.
    try:
        locale.setlocale(locale.LC_ALL, "")
        logger.info(f"Locale set successfully to: {locale.getlocale()}.")
    except locale.Error as e_locale:
        logger.error(
            f"Failed to set system locale: {e_locale}. "
            f"Character width calculations and other locale-sensitive operations might be incorrect.",
            exc_info=True
        )

    # Editor instantiation and run
    try:
        editor = SwayEditor(stdscr)  # Pass the curses screen object

        # Handle command-line arguments (e.g., open a file specified at startup)
        if len(sys.argv) > 1:
            filename_arg = sys.argv[1]
            logger.info(f"Attempting to open file from command line argument: '{filename_arg}'")
            # open_file handles its own error reporting and status messages.
            # It also returns a bool indicating if a redraw is needed, but main_curses_function
            # doesn't directly use it; editor.run() handles the redraw loop.
            editor.open_file(filename_arg)
        else:
            logger.info("No file specified on command line. Starting with a new, empty buffer.")
            # The editor is initialized with an empty buffer by default in SwayEditor.__init__.

        # Start the main editor loop
        logger.debug("Starting editor's main run() loop.")
        editor.run()  # This call will block until the editor exits.

    except Exception:  # Catch any unhandled exceptions during editor operation
        # This logging will go to the configured handlers (file, console, error.log)
        logger.critical("Unhandled exception during editor execution (inside curses.wrapper):", exc_info=True)

        # Attempt to print a user-friendly message directly to stderr as a last resort,
        # in case curses is already teared down or logging to console is not ERROR/CRITICAL.
        # This might not be visible if curses hasn't been properly ended.
        print("\nCRITICAL ERROR: An unexpected error occurred during editor operation.", file=sys.stderr)
        print("Please check 'editor.log' and 'error.log' (if enabled) for details.", file=sys.stderr)

        # Also try to log the traceback to a separate critical error file for robustness
        critical_error_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "critical_run_error.log")
        try:
            with open(critical_error_log_path, "a", encoding="utf-8", errors="replace") as error_file:
                error_file.write(f"\n{'=' * 20} CRITICAL ERROR: {time.asctime()} {'=' * 20}\n")
                error_file.write("Error occurred inside curses.wrapper (main_curses_function):\n")
                traceback.print_exc(file=error_file)  # Print full traceback to this file
                error_file.write(f"\n{'=' * 60}\n")
            print(f"Detailed critical error traceback also logged to: '{critical_error_log_path}'", file=sys.stderr)
        except Exception as log_write_err:
            print(f"Failed to write detailed critical error log to '{critical_error_log_path}': {log_write_err}",
                  file=sys.stderr)
            # As a very last resort, print traceback to stderr if file logging failed
            print("\n--- Traceback (also attempted to log to file) ---", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            print("--- End Traceback ---", file=sys.stderr)

        # It's generally good to re-raise or exit with an error code if curses wrapper content fails badly.
        # However, sys.exit() here might prevent curses.wrapper's own cleanup.
        # The exception will propagate out of main_curses_function, and curses.wrapper
        # should then handle terminal restoration. The outer try-except around curses.wrapper
        # in __main__ will catch this.
        raise  # Re-raise the exception to be caught by the __main__ block's try-except


# --- Main Script Entry Point ---
if __name__ == "__main__":
    # 1. Perform initial setup that doesn't depend on curses (like signal handling, config loading, logging setup)

    # Attempt to ignore SIGTSTP (Ctrl+Z, suspend) if on a platform that supports it.
    # This is often done to prevent the editor from being suspended accidentally.
    if hasattr(signal, 'SIGTSTP'):
        try:
            signal.signal(signal.SIGTSTP, signal.SIG_IGN)
        except Exception as e:  # More specific: RuntimeError, ValueError
            print(f"Warning: Could not set SIGTSTP to ignore: {e}", file=sys.stderr)
    # SIGINT (Ctrl+C) is typically handled by KeyboardInterrupt within the application.

    # Load application configuration first, as logging setup might depend on it.
    app_config = {}  # Default to empty config if loading fails
    try:
        app_config = load_config()  # Assumes load_config() is defined
        # A very basic print, as proper logging isn't set up yet.
        print("Configuration loaded for logging.", file=sys.stderr if sys.stderr else sys.stdout)
    except Exception as e_cfg:
        print(f"ERROR: Failed to load configuration: {e_cfg}. Using fallback defaults for logging.", file=sys.stderr)
        # Define minimal config structure for logging if load_config failed
        app_config = {
            "logging": {
                "file_level": "DEBUG",
                "console_level": "WARNING",
                "log_to_console": True
            }
        }

    # Set up the enhanced logging system using the loaded (or fallback) configuration.
    setup_logging(app_config)

    # Now that logging is configured, subsequent log messages will go to the configured handlers.
    logger.info("Sway-Pad editor starting up...")  # This will now use the configured logger.

    # 2. Initialize and run the curses-based application.
    # curses.wrapper handles curses initialization, calls main_curses_function,
    # and ensures curses is properly shut down (terminal restored) on exit or error.
    try:
        curses.wrapper(main_curses_function)
        logger.info("Sway-Pad editor shut down gracefully.")
    except Exception as e_wrapper:  # Catch any unhandled exceptions that escape curses.wrapper
        # This is a last-ditch effort to log critical failures.
        # The logger configured by setup_logging should catch this.
        logger.critical("CRITICAL: Unhandled exception at the outermost level (after curses.wrapper).", exc_info=True)

        # Also print to stderr for immediate visibility, as curses might have already exited.
        print("\nCRITICAL ERROR: An unhandled exception occurred outside the main editor loop.", file=sys.stderr)
        print(f"Error: {e_wrapper}", file=sys.stderr)
        print("Please check 'editor.log' and 'error.log' (if enabled) for detailed traceback.", file=sys.stderr)

        # Attempt to log to a dedicated critical error file again, just in case.
        outer_error_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "critical_outer_error.log")
        try:
            with open(outer_error_log_path, "a", encoding="utf-8", errors="replace") as err_f:
                err_f.write(f"\n{'=' * 20} CRITICAL OUTER ERROR: {time.asctime()} {'=' * 20}\n")
                traceback.print_exc(file=err_f)
                err_f.write(f"\n{'=' * 60}\n")
            print(f"Detailed critical error traceback also logged to: '{outer_error_log_path}'", file=sys.stderr)
        except Exception as log_final_err:
            print(f"Failed to write final critical error log: {log_final_err}", file=sys.stderr)
            print("\n--- Final Traceback (also attempted to log to file) ---", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            print("--- End Final Traceback ---", file=sys.stderr)

        sys.exit(1)  # Exit with an error code
