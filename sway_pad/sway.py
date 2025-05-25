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
import termios
import curses.ascii
import signal 

from pygments import lex
from pygments.lexer import RegexLexer
from pygments.lexers import get_lexer_by_name, guess_lexer, TextLexer
from pygments.token import Token, Comment, Name, Punctuation
from wcwidth import wcwidth, wcswidth
from typing import Callable, Tuple, Optional, List, Dict, Any
from collections import OrderedDict 


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
    if os.name == 'nt': # Apply only on Windows
        os.environ.setdefault("PYTHONLEGACYWINDOWSSTDIO", "1")
    
    logging.debug(
        f"Default I/O encoding set: PYTHONIOENCODING='{os.environ.get('PYTHONIOENCODING')}', "
        f"PYTHONLEGACYWINDOWSSTDIO='{os.environ.get('PYTHONLEGACYWINDOWSSTDIO', 'Not Set (or Not Windows)')}'"
    )

_set_default_encoding() # Call once at module load time


# --- Dictionary Deep Merge Utility ---
def deep_merge(base: Dict[Any, Any], override: Dict[Any, Any]) -> Dict[Any, Any]:
    """
    Recursively merges the 'override' dictionary into the 'base' dictionary.
    - If a key exists in both dictionaries and both values are dictionaries,
      it recursively merges them.
    - Otherwise, the value from the 'override' dictionary takes precedence.
    - The original 'base' dictionary is not modified; a new dictionary is returned.

    Args:
        base (Dict[Any, Any]): The base dictionary.
        override (Dict[Any, Any]): The dictionary to merge over the base.

    Returns:
        Dict[Any, Any]: A new dictionary representing the merged result.
    """
    result = dict(base) # Start with a shallow copy of the base
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


# --- Safe Subprocess Execution Utility ---
def safe_run(
    cmd: List[str], 
    cwd: Optional[str] = None, 
    timeout: Optional[float] = None, # Added explicit timeout parameter
    **kwargs: Any
) -> subprocess.CompletedProcess:
    """
    Provides a safer and more convenient way to run external commands using subprocess.run.
    - Captures stdout and stderr.
    - Decodes output as UTF-8 text, replacing decoding errors.
    - Does not raise an exception for non-zero exit codes (check=False).
    - Allows specifying a current working directory (cwd) and a timeout.

    Args:
        cmd (List[str]): The command and its arguments as a list of strings.
        cwd (Optional[str]): The working directory for the command. Defaults to None (current dir).
        timeout (Optional[float]): Timeout in seconds for the command. Defaults to None (no timeout).
        **kwargs: Additional keyword arguments to pass to subprocess.run.

    Returns:
        subprocess.CompletedProcess: An object containing information about the completed process,
                                     including returncode, stdout, and stderr.
    """
    # Ensure text=True is compatible with encoding and errors (it is)
    # For Python 3.7+, universal_newlines=True is implied by text=True.
    # For older versions, explicitly setting universal_newlines might be useful
    # if cross-platform line ending normalization is desired from the start,
    # but text=True usually handles this.
    
    # Ensure 'check' is not overridden by kwargs to be True if we want to always handle non-zero returns manually
    if 'check' in kwargs:
        logging.warning("safe_run: 'check' in kwargs is overriding default False. Caller must handle CalledProcessError.")
    
    effective_kwargs = {
        'capture_output': True,
        'text': True,
        'check': False, # Default to False, caller should check result.returncode
        'encoding': 'utf-8',
        'errors': 'replace',
        **kwargs # User-provided kwargs can override defaults (except those explicitly set above)
    }
    if cwd is not None:
        effective_kwargs['cwd'] = cwd
    if timeout is not None:
        effective_kwargs['timeout'] = timeout
        
    try:
        return subprocess.run(cmd, **effective_kwargs)
    except FileNotFoundError as e_fnf:
        # Construct a CompletedProcess object to mimic failure if command not found
        logging.error(f"safe_run: Command '{cmd[0]}' not found: {e_fnf}")
        return subprocess.CompletedProcess(args=cmd, returncode=127, stdout="", stderr=str(e_fnf))
    except subprocess.TimeoutExpired as e_timeout:
        logging.warning(f"safe_run: Command '{' '.join(cmd)}' timed out after {timeout}s: {e_timeout.stderr or e_timeout.stdout or ''}")
        # TimeoutExpired exception has stdout and stderr attributes
        return subprocess.CompletedProcess(args=cmd, returncode=-9, # Or signal.SIGKILL equivalent
                                           stdout=e_timeout.stdout.decode('utf-8', 'replace') if e_timeout.stdout else "", 
                                           stderr=e_timeout.stderr.decode('utf-8', 'replace') if e_timeout.stderr else "")
    except Exception as e_general:
        # Catch other potential errors from subprocess.run
        logging.error(f"safe_run: Error running command '{' '.join(cmd)}': {e_general}", exc_info=True)
        return subprocess.CompletedProcess(args=cmd, returncode=-1, stdout="", stderr=str(e_general))


# --- Enhanced Logging Setup Function ---
def setup_logging(config: Optional[Dict[str, Any]] = None) -> None:
    """
    Configures logging for the application with multiple handlers and levels.
    - Main log file (e.g., editor.log) for DEBUG and above.
    - Optional console output for WARNING and above (or configurable).
    - Optional separate error log file (e.g., error.log) for ERROR and above.
    - Optional key event tracing to keytrace.log.

    Args:
        config (Optional[Dict[str, Any]]): Application configuration dictionary.
                                           If provided, can be used to customize log levels.
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
            log_filename = os.path.join(tempfile.gettempdir(), "sway2_editor.log")
            print(f"Logging to temporary file: '{log_filename}'", file=sys.stderr)

    file_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)-8s - %(name)-15s - %(message)s (%(filename)s:%(lineno)d)"
    )
    file_handler = None
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            log_filename, maxBytes=2*1024*1024, backupCount=5, encoding='utf-8'
        )
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(log_file_level)
    except Exception as e_fh:
        print(f"Error setting up file logger for '{log_filename}': {e_fh}. File logging may be impaired.", file=sys.stderr)

    # --- Console Handler ---
    log_to_console_enabled = logging_config.get("log_to_console", True)
    console_handler = None
    if log_to_console_enabled:
        console_level_str = logging_config.get("console_level", "WARNING").upper()
        console_log_level = getattr(logging, console_level_str, logging.WARNING)
        
        console_formatter = logging.Formatter("%(levelname)-8s - %(name)-12s - %(message)s") # Slightly shorter name field
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
                error_log_filename, maxBytes=1*1024*1024, backupCount=3, encoding='utf-8'
            )
            error_file_handler.setFormatter(file_formatter) 
            error_file_handler.setLevel(logging.ERROR)
        except Exception as e_efh:
            print(f"Error setting up separate error log '{error_log_filename}': {e_efh}.", file=sys.stderr)

    # Configure the root logger
    root_logger = logging.getLogger()
    root_logger.handlers = [] # Clear existing root handlers to avoid duplicates

    if file_handler:
        root_logger.addHandler(file_handler)
    if console_handler:
        root_logger.addHandler(console_handler)
    if error_file_handler:
        root_logger.addHandler(error_file_handler)
        
    root_logger.setLevel(log_file_level) # Set root to the most verbose level needed by file handlers

    # --- Key Event Logger ---
    key_event_logger = logging.getLogger("sway2.keyevents") 
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
                key_trace_filename, maxBytes=1*1024*1024, backupCount=3, encoding='utf-8'
            )
            key_trace_formatter = logging.Formatter("%(asctime)s - %(message)s")
            key_trace_handler.setFormatter(key_trace_formatter)
            key_event_logger.addHandler(key_trace_handler)
            # Do not enable propagate for key_event_logger unless you want key traces in the main log too.
            logging.info("Key event tracing enabled, logging to '%s'.", key_trace_filename) # Use root logger for this info
        except Exception as e_keytrace:
            logging.error(f"Failed to set up key trace logging: {e_keytrace}", exc_info=True) # Use root logger
            key_event_logger.disabled = True 
    else:
        key_event_logger.addHandler(logging.NullHandler()) 
        key_event_logger.disabled = True 
        logging.debug("Key event tracing is disabled.") # Use root logger

    logging.info("Logging setup complete. Root logger level: %s.", logging.getLevelName(root_logger.level))
    if file_handler:
        logging.info(f"File logging to '{log_filename}' at level: {logging.getLevelName(file_handler.level)}.")
    if console_handler:
        logging.info(f"Console logging to stderr at level: {logging.getLevelName(console_handler.level)}.")
    if error_file_handler:
        logging.info(f"Error logging to 'error.log' at level: ERROR.")


# --- Global Loggers ---
# These are defined at the module level after setup_logging is defined.
# They will be configured when setup_logging() is called from the __main__ block.
logger = logging.getLogger("sway2") # Main application logger
KEY_LOGGER = logging.getLogger("sway2.keyevents") # Key event logger for specific key traces


# --- File Icon Retrieval Function ---
def get_file_icon(filename: Optional[str], config: Dict[str, Any]) -> str:
    """
    Returns an icon for a file based on its name and extension, according to the configuration.
    It prioritizes full filename matches (for files like 'Makefile', 'Dockerfile')
    and then extension matches.

    Args:
        filename (Optional[str]): The full name or path of the file. Can be None for new/untitled files.
        config (Dict[str, Any]): The editor's configuration dictionary, expected to contain
                                 "file_icons" and "supported_formats" sections.

    Returns:
        str: A string representing the icon for the file.
    """
    file_icons = config.get("file_icons", {})
    default_icon = file_icons.get("default", "❓") # Default if no specific icon is found
    text_icon = file_icons.get("text", "📝")       # Specific default for text-like or new files

    if not filename: # Handles new, unsaved files or None input
        return text_icon # Use text icon for new/untitled files

    # Normalize filename for matching (lowercase)
    filename_lower = filename.lower()
    base_name_lower = os.path.basename(filename_lower) # e.g., "myfile.txt" from "/path/to/myfile.txt"

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
                elif ext_or_name.startswith(".") and base_name_lower == ext_or_name.lower(): # Handles .gitignore, .gitattributes
                    return file_icons.get(icon_key, default_icon)


    # 2. Check for extension matches
    # Handle complex extensions like ".tar.gz" by checking parts of the extension.
    # We can get all "extensions" by splitting by dot.
    # Example: "myfile.tar.gz" -> parts ["myfile", "tar", "gz"]
    # We want to check for ".gz", ".tar.gz"
    
    name_parts = base_name_lower.split('.')
    if len(name_parts) > 1: # If there is at least one dot
        # Iterate from the longest possible extension to the shortest
        # e.g., for "file.tar.gz", check ".tar.gz", then ".gz"
        for i in range(1, len(name_parts)):
            # Construct extension like ".gz", ".tar.gz"
            current_extension_to_check = "." + ".".join(name_parts[i:])
            
            for icon_key, defined_extensions in supported_formats.items():
                if isinstance(defined_extensions, list):
                    # Convert defined extensions to lowercase for comparison
                    lower_defined_extensions = [ext.lower() for ext in defined_extensions]
                    # Check if our current_extension_to_check (e.g. ".tar.gz") is in the list
                    # or if a simple extension (e.g. ".gz") is in the list
                    # The items in `defined_extensions` should not include the leading dot here.
                    # So, current_extension_to_check without leading dot:
                    ext_to_match = current_extension_to_check[1:] # Remove leading dot

                    if ext_to_match in lower_defined_extensions:
                        return file_icons.get(icon_key, default_icon)
            
            # If a multi-part extension didn't match, try just the last part as a fallback
            # This is covered if single extensions like "gz" are listed for the icon_key.
            # If current_extension_to_check was ".gz", it would have been matched above if "gz" is in the list.

    # 3. If no specific match by full name or extension, return the generic text icon
    #    or a more generic default if text icon is also not found (though unlikely).
    #    The problem description implied returning text_icon as a final fallback.
    #    Using `default_icon` might be more appropriate if truly nothing matched.
    #    Let's stick to text_icon as the ultimate fallback if other logic fails.
    logging.debug(f"get_file_icon: No specific icon found for '{filename}'. Falling back to text icon.")
    return text_icon


# --- Git Information Retrieval Function ---
def get_git_info(file_path_context: Optional[str]) -> Tuple[str, str, str]:
    """
    Synchronously retrieves basic Git repository information (current branch, user name,
    commit count, and dirty status) relevant to the given file path context.
    This function is intended ONLY for synchronous use during editor initialization
    to get a quick initial Git status. The asynchronous version _fetch_git_info_async
    is preferred for subsequent updates.

    Args:
        file_path_context (Optional[str]): The path to the current file. If None or invalid,
                                         the current working directory is used as context.

    Returns:
        Tuple[str, str, str]: A tuple containing (branch_name_with_dirty_star, user_name, commit_count_str).
                              Returns ("", "", "0") if not in a Git repo or if Git is not found.
    """
    branch_name: str = ""
    user_name_git: str = ""
    commit_count_str: str = "0"
    
    # 1. Determine the directory to check for a Git repository.
    # If file_path_context is a valid existing file, use its directory.
    # Otherwise, fall back to the current working directory.
    # This version does not search upwards for .git to keep it fast for init.
    repo_dir_candidate: str
    if file_path_context and os.path.isfile(file_path_context): # Check if it's an actual file
        repo_dir_candidate = os.path.dirname(os.path.abspath(file_path_context))
    else:
        repo_dir_candidate = os.getcwd()

    # 2. Check for the presence of a .git directory.
    if not os.path.isdir(os.path.join(repo_dir_candidate, ".git")):
        logging.debug(
            f"get_git_info: No .git directory found in '{repo_dir_candidate}'. "
            f"Context path was: '{file_path_context}'."
        )
        return "", "", "0"

    logging.debug(f"get_git_info: Found .git directory in '{repo_dir_candidate}'. Proceeding with Git commands.")

    # --- Helper for running git commands synchronously with a short timeout ---
    def run_sync_git_cmd(cmd_parts: List[str], timeout_secs: int = 2) -> subprocess.CompletedProcess:
        # Using subprocess.run directly here for synchronous execution.
        # safe_run could also be used if it's modified to allow check=True or if we check returncode.
        try:
            return subprocess.run(
                cmd_parts,
                capture_output=True,
                text=True,
                check=True, # Raise CalledProcessError on non-zero exit
                cwd=repo_dir_candidate,
                encoding='utf-8',
                errors='replace',
                timeout=timeout_secs
            )
        except FileNotFoundError: # 'git' command itself not found
            raise # Re-raise to be caught by the outer try-except
        except subprocess.CalledProcessError as e:
            logging.warning(f"Git command '{' '.join(cmd_parts)}' failed with code {e.returncode}: {e.stderr.strip()[:100]}")
            raise # Re-raise
        except subprocess.TimeoutExpired:
            logging.warning(f"Git command '{' '.join(cmd_parts)}' timed out after {timeout_secs}s.")
            raise # Re-raise
        except Exception as e_run: # Other potential errors
            logging.error(f"Error running git command '{' '.join(cmd_parts)}': {e_run}", exc_info=True)
            raise

    try:
        # 3. Determine the current branch name.
        try:
            # Preferred: 'git branch --show-current' (Git 2.22+)
            result = run_sync_git_cmd(["git", "branch", "--show-current"])
            branch_name = result.stdout.strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            try:
                # Fallback 1: 'git symbolic-ref --short HEAD'
                result = run_sync_git_cmd(["git", "symbolic-ref", "--short", "HEAD"])
                branch_name = result.stdout.strip()
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                try:
                    # Fallback 2: 'git rev-parse --short HEAD' (for detached HEAD)
                    result = run_sync_git_cmd(["git", "rev-parse", "--short", "HEAD"])
                    branch_name = result.stdout.strip()[:7] # Typically 7 chars
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    branch_name = "detached" # Default if all else fails
                    logging.warning(f"Git: Could not determine branch for '{repo_dir_candidate}', assuming detached or error.")
        
        if not branch_name: # If somehow still empty after all attempts (e.g., initial commit missing)
            branch_name = "unborn" # Or some other indicator for a repo with no commits yet

        # 4. Check for dirty repository (uncommitted changes).
        try:
            result_dirty = run_sync_git_cmd(["git", "status", "--porcelain", "--ignore-submodules"])
            if result_dirty.stdout.strip(): # Any output means changes
                if '*' not in branch_name: # Avoid double asterisks
                    branch_name += "*"
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            logging.warning(f"Git: 'status --porcelain' failed or timed out for '{repo_dir_candidate}'. Dirty status might be inaccurate.")
            # Do not append '*' if status check fails, to avoid false positives.

        # 5. Get Git user name.
        try:
            result_user = run_sync_git_cmd(["git", "config", "user.name"])
            user_name_git = result_user.stdout.strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            logging.debug(f"Git: Could not retrieve user.name for '{repo_dir_candidate}'.")
            user_name_git = "" # Default to empty

        # 6. Get commit count on the current HEAD.
        try:
            result_commits = run_sync_git_cmd(["git", "rev-list", "--count", "HEAD"])
            if result_commits.stdout.strip().isdigit():
                commit_count_str = result_commits.stdout.strip()
            else:
                commit_count_str = "0" # If not a digit (e.g. error message, or unborn branch)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            logging.debug(f"Git: 'rev-list --count HEAD' failed or timed out for '{repo_dir_candidate}'. Commit count set to 0.")
            commit_count_str = "0" # Default on error or if HEAD doesn't exist (e.g. empty repo)

    except FileNotFoundError: # This catches if 'git' executable is not found by run_sync_git_cmd
        logging.warning("get_git_info: 'git' executable not found. Ensure Git is installed and in PATH.")
        return "", "", "0" # Return defaults if Git is not available
    except subprocess.TimeoutExpired: # If any command inside timed out and was re-raised
        logging.warning(f"get_git_info: A Git command timed out for '{repo_dir_candidate}'. Returning potentially incomplete info.")
        # Return whatever was gathered so far, which might be partial or default.
        # The individual try-except blocks for commands should set defaults on timeout.
    except Exception as e_main: # Catch any other unexpected errors
        logging.error(f"get_git_info: Unexpected error fetching synchronous Git info for '{repo_dir_candidate}': {e_main}", exc_info=True)
        return "error", "", "0" # Indicate fetch error in branch name

    logging.debug(f"get_git_info: Fetched synchronous Git info for '{repo_dir_candidate}': Branch='{branch_name}', User='{user_name_git}', Commits='{commit_count_str}'")
    return branch_name, user_name_git, commit_count_str


# --- Configuration Loading (if defined in this file) ---
def load_config() -> dict:
    """
    Loads configuration from 'config.toml' using minimal defaults if the file is not found or is invalid.
    """    
    minimal_default = {
        "colors": {
            "error": "red",
            "status": "bright_white", # curses.COLOR_WHITE + curses.A_BOLD 
            "green": "green"          # Git-info
        },
        # Font Family and Size in curses meta info for GUI
        "fonts": {"font_family": "monospace", "font_size": 12},
        "keybindings": {
            "delete": "del",
            "paste": "ctrl+v",
            "copy": "ctrl+c",
            "cut": "ctrl+x",
            "undo": "ctrl+z",
            "redo": "shift+z", 
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
            "help": "f1"     
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
            "javascript": "📜", # js, mjs, cjs, jsx
            "typescript": "📑", # ts, tsx
            "php": "🐘", 
            "ruby": "♦️", # ruby, rbw, gems
            "css": "🎨",  # css
            "html": "🌐",  # html, htm
            "json": "📊",
            "yaml": "⚙️",  # yml, yaml
            "xml": "📰",  
            "markdown": "📋", # md, markdown
            "text": "📝",     # txt, log, rst 
            "shell": "💫",    # sh, bash, zsh, ksh, fish
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
            "git": "🔖",      # .gitignore, .gitattributes)
            "notebook": "📒", # .ipynb
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
            "image": "🖼️",    # jpg, jpeg, png, gif, bmp, svg, webp
            "audio": "🎵",    # mp3, wav, ogg, flac
            "video": "🎞️",    # mp4, mkv, avi, mov, webm
            "archive": "📦",  # zip, tar, gz, rar, 7z
            "font": "🖋️",     # ttf, otf, woff, woff2
            "binary": "⚙️",    # .exe, .dll, .so, .o, .bin, .app 
            "document": "📄",  # .doc, .docx, .odt, .pdf, .ppt, .pptx, .odp
            "folder": "📁",   # Icon for directories (not used by get_file_icon, but useful for file managers)
            "folder_open": "📂", # likewise
            "default": "❓"   # Default icon if nothing fits
        },
        "supported_formats": { 
            "python": ["py", "pyw", "pyc", "pyd"], # pyc/pyd - binary but related to python
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
        "git": {
            "enabled": True # Enables/disables Git integration
        },
         "settings": {
            "auto_save_interval": 5, # Autosave interval in minutes (0 to disable)
            "show_git_info": True # display Git information in the status bar
        }
    }

    config_path = "config.toml"
    user_config = {}

    # Trying to load user config
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                file_content = f.read()
                user_config = toml.loads(file_content)
                logging.debug(f"Loaded user config from {config_path}")
        except FileNotFoundError:
            logging.warning(f"Config file '{config_path}' not found. Using minimal defaults.")
        except toml.TomlDecodeError as e:
            logging.error(f"TOML parse error in {config_path}: {str(e)}")
            logging.error("Falling back to minimal defaults.")
        except Exception as e:
            logging.error(f"Unexpected error reading {config_path}: {str(e)}")
            logging.error("Falling back to minimal defaults.")
    else:
        logging.warning(f"Config file '{config_path}' not found. Using minimal defaults.")


    # Merge defaults with user configuration
    # Deep merge preserves user subdictionaries
    final_config = deep_merge(minimal_default, user_config)

    # Additional check for key sections (can be removed if deep_merge always creates them)
    for key, default_section in minimal_default.items():
        if key not in final_config:
            final_config[key] = default_section
        elif isinstance(default_section, dict): # Убедимся, что все ключи из дефолтной секции есть
            for sub_key, sub_val in default_section.items():
                 if sub_key not in final_config[key]:
                      final_config[key][sub_key] = sub_val

    logging.debug("Final config loaded successfully")
    return final_config


## Class SwayEditor  --------------------------------------------------------
class SwayEditor:
    """The main class of the Sway-Pad editor."""
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
        # Ensure _last_status_msg_sent attribute exists (usually set in __init__ or first call)
        if not hasattr(self, "_last_status_msg_sent"):
            self._last_status_msg_sent = None

        if is_lint_status:
            # Handle messages related to the linter.
            original_panel_active = self.lint_panel_active
            original_panel_message = self.lint_panel_message

            # Update the linter panel's full message content if provided.
            if full_lint_output is not None:
                # It's generally safe to assign to self attributes from different threads 
                # if reads/writes are simple assignments and not complex operations,
                # and if the main UI thread is the primary reader for drawing.
                # For more complex state, a lock might be needed around these attributes.
                self.lint_panel_message = str(full_lint_output) # Ensure it's a string
                logging.debug(f"Linter panel message updated: '{self.lint_panel_message[:100]}...'")
            
            logging.debug(f"Linter status bar message: '{message_for_statusbar}'")
            
            # Queue the (short) status bar message.
            # Avoid queuing duplicate consecutive messages for the status bar.
            if message_for_statusbar != self._last_status_msg_sent:
                try:
                    # The message queue is thread-safe.
                    self._msg_q.put_nowait(str(message_for_statusbar)) 
                    self._last_status_msg_sent = message_for_statusbar 
                except queue.Full:
                    logging.error("Status message queue is full (linter message). Dropping message.")
                except Exception as e:
                    logging.error(f"Failed to add linter status message to queue: {e}", exc_info=True)

            # Decide whether to activate the linter panel.
            if activate_lint_panel_if_issues and self.lint_panel_message:
                # Define messages that indicate no linting issues were found.
                # These could be made configurable or expanded.
                no_issues_substrings = [ # Check for substrings to be more robust
                    "no issues found", 
                    "нет проблем" # Russian for "no problems"
                ]
                
                # Check if the panel message indicates that there are actual issues.
                # Convert to lower for case-insensitive check.
                panel_message_lower = self.lint_panel_message.strip().lower()
                has_actual_issues = True # Assume issues unless a "no issues" message is found
                for no_issue_msg_part in no_issues_substrings:
                    if no_issue_msg_part in panel_message_lower:
                        has_actual_issues = False
                        break
                
                if has_actual_issues:
                    if not self.lint_panel_active:
                        self.lint_panel_active = True
                        logging.debug("Linter panel activated due to detected issues.")
                else:
                    # No actual issues found, ensure panel is not active (or deactivate if it was).
                    # Optionally, one might choose to keep it open if user explicitly opened it,
                    # but for activate_lint_panel_if_issues, deactivating on "no issues" is common.
                    # if self.lint_panel_active:
                    #    self.lint_panel_active = False
                    #    logging.debug("Linter panel deactivated as no issues were found.")
                    logging.debug("No linting issues found; panel not automatically activated (or remains as is).")
            # If not activating on issues, or no message, panel state remains as is unless explicitly changed elsewhere.

            # If panel state or message changed, it implies a redraw is needed.
            # This method doesn't return a bool, the main loop detects changes via queue or attribute polling.

        else: # Handle regular (non-linter) status messages.
            # Prevent queuing duplicate consecutive messages for the status bar.
            if message_for_statusbar == self._last_status_msg_sent:
                logging.debug(f"Skipping duplicate status message: '{message_for_statusbar}'")
                return 
            
            try:
                self._msg_q.put_nowait(str(message_for_statusbar)) # Ensure message is a string
                self._last_status_msg_sent = message_for_statusbar # Update last sent message
                logging.debug(f"Queued status message for status bar: '{message_for_statusbar}'")
            except queue.Full:
                logging.error("Status message queue is full. Dropping message.")
            except Exception as e:
                logging.error(f"Failed to add status message to queue: '{message_for_statusbar}': {e}", exc_info=True)

    # Add to your imports at the top of the file if you decide to keep self._token_cache as OrderedDict
    # from collections import OrderedDict 
    # If you completely remove self._token_cache for Pygments, this import might not be needed for this specific cache.
    def __init__(self, stdscr):
        """
        Initialize the editor. Called from curses.wrapper().
        Sets up terminal, curses, configuration, editor state, and keybindings.
        """
        # --- Terminal Setup: Disable IXON/IXOFF and canonical mode ---
        # This allows Ctrl+S, Ctrl+Q, and other control sequences to be captured by the application.
        try:
            fd = sys.stdin.fileno()
            termios_attrs = termios.tcgetattr(fd)
            termios_attrs[0] &= ~(termios.IXON | termios.IXOFF)  # Disable Ctrl+S / Ctrl+Q flow control
            termios_attrs[3] &= ~termios.ICANON                  # Disable canonical mode (line buffering)
            termios.tcsetattr(fd, termios.TCSANOW, termios_attrs)
            logging.debug("Terminal IXON/IXOFF and ICANON successfully disabled – Ctrl+S/Q/Z now usable by app.")
        except Exception as e_termios: # Catch specific termios.error or general Exception
            logging.warning(f"Could not set terminal attributes (IXON/IXOFF, ICANON): {e_termios}")

        # --- Basic Curses Initialization ---
        self.stdscr = stdscr
        self.stdscr.keypad(True)  # Enable support for special keys (F-keys, arrows, etc.)
        curses.raw()              # Enable raw mode (input characters are available one by one)
        curses.noecho()           # Do not echo typed characters to the screen
        curses.curs_set(1)        # Set cursor visibility (1 = normal, 0 = invisible, 2 = very visible)

        # --- Load Configuration and Initialize Settings/Buffers ---
        try:
            self.config = load_config() # Load from config.toml or use defaults
        except Exception as e_config:
            logging.error(f"Failed to load configuration: {e_config}. Using minimal fallback defaults.", exc_info=True)
            self.config = { # Fallback configuration
                "editor": {"use_system_clipboard": True, "tab_size": 4, "use_spaces": True, "default_new_filename": "untitled.txt"},
                "keybindings": {},
                "colors": {}, # Colors will be initialized by init_colors
                "git": {"enabled": True},
                "settings": {"auto_save_interval": 1, "show_git_info": True},
                "file_icons": {"text": "📝", "default": "❓"},
                "supported_formats": {}
            }

        self.colors: dict[str, int] = {} # To store curses color pair attributes
        self.init_colors() # Initialize color pairs

        # Clipboard settings
        self.use_system_clipboard = self.config.get("editor", {}).get("use_system_clipboard", True)
        self.pyclip_available = self._check_pyclip_availability() # Check if pyperclip and system utils are working
        if not self.pyclip_available and self.use_system_clipboard:
            logging.warning("pyperclip/system clipboard utilities are unavailable. System clipboard integration disabled.")
            self.use_system_clipboard = False # Fallback to internal clipboard only
        self.internal_clipboard: str = ""

        # Auto-save settings and state
        self._auto_save_thread: Optional[threading.Thread] = None
        self._auto_save_enabled: bool = False
        self._auto_save_stop_event = threading.Event() # For cleanly stopping the auto-save thread
        try:
            self._auto_save_interval = float(self.config.get("settings", {}).get("auto_save_interval", 1.0)) # In minutes
            if self._auto_save_interval <= 0:
                logging.warning(f"Invalid auto_save_interval ({self._auto_save_interval} min), defaulting to 1.0 min.")
                self._auto_save_interval = 1.0
        except (ValueError, TypeError):
            logging.warning("Could not parse auto_save_interval, defaulting to 1.0 min.")
            self._auto_save_interval = 1.0

        # Editor mode and status
        self.insert_mode: bool = True       # True for insert, False for replace/overwrite
        self.status_message: str = "Ready"  # Current message for the status bar
        self._last_status_msg_sent: Optional[str] = None # To prevent duplicate status messages
        self._msg_q: queue.Queue[Any] = queue.Queue() # General message queue for status updates from threads

        # Linter panel state
        self.lint_panel_message: Optional[str] = None # Full message content for the linter panel
        self.lint_panel_active: bool = False          # Whether the linter panel is currently visible

        # Undo/Redo history
        self.action_history: List[dict[str, Any]] = []
        self.undone_actions: List[dict[str, Any]] = []

        # Threading and Queues for background tasks
        self._state_lock = threading.RLock()       # Reentrant lock for protecting shared editor state
        self._shell_cmd_q: queue.Queue[str] = queue.Queue() # Queue for results from shell commands
        self._git_q: queue.Queue[Tuple[str, str, str]] = queue.Queue() # Queue for Git info updates (branch, user, commits)
        self._git_cmd_q: queue.Queue[str] = queue.Queue() # Queue for results from Git commands

        # Text buffer and cursor state
        self.text: List[str] = [""]    # Document content, list of strings (lines)
        self.cursor_x: int = 0         # Horizontal cursor position (character index within the line)
        self.cursor_y: int = 0         # Vertical cursor position (line index)
        self.scroll_top: int = 0       # Topmost visible line index in the editor window
        self.scroll_left: int = 0      # Leftmost visible character column (display width offset)
        self.modified: bool = False    # True if the buffer has unsaved changes
        self.encoding: str = "UTF-8"   # Default file encoding
        self.filename: Optional[str] = None # Current filename, None if new/untitled

        # Selection state
        self.selection_start: Optional[Tuple[int, int]] = None # (row, col) of selection start
        self.selection_end: Optional[Tuple[int, int]] = None   # (row, col) of selection end (moving part)
        self.is_selecting: bool = False                        # True if selection is active

        # Search state
        self.search_term: str = ""
        self.search_matches: List[Tuple[int, int, int]] = [] # List of (row, start_col, end_col)
        self.current_match_idx: int = -1
        self.highlighted_matches: List[Tuple[int, int, int]] = [] # Matches currently highlighted on screen

        # Git integration state
        self.git_info: Tuple[str, str, str] = ("", "", "0") # (branch, user_name, commit_count_str)
        self._last_git_filename: Optional[str] = None # Filename for which Git info was last fetched

        # Syntax highlighting (Pygments)
        self._lexer: Optional[TextLexer] = None # Current Pygments lexer instance
        # self._token_cache is managed by @functools.lru_cache on _get_tokenized_line method
        # If you were using a manual dict cache for Pygments:
        # from collections import OrderedDict
        # self._token_cache: OrderedDict[Tuple[int, int, int, bool], List[Tuple[str, int]]] = OrderedDict()
        # However, with lru_cache on _get_tokenized_line, this instance variable for Pygments tokens is not strictly needed.

        # Screen drawing related
        self.visible_lines: int = 0 # Number of lines visible in the text area
        self.last_window_size: Tuple[int, int] = (0, 0) # (height, width)
        self.drawer = DrawScreen(self) # Drawing helper class instance

        # Load keybindings and setup action map
        try:
            self.keybindings = self._load_keybindings()
            logging.debug("Keybindings loaded successfully in __init__.")
        except Exception as e_kb_init:
            logging.critical(f"CRITICAL ERROR during keybinding setup in __init__: {e_kb_init}", exc_info=True)
            # Depending on severity, might want to raise to stop editor or try to continue with defaults
            raise # Re-raise to indicate critical failure

        self.action_map = self._setup_action_map()
        logging.debug("Action map set up successfully in __init__.")

        # Set initial cursor and scroll positions
        self.set_initial_cursor_position()

        # Fetch initial Git info if enabled
        if self.config.get("git", {}).get("enabled", True) and self.config.get("settings", {}).get("show_git_info", True):
            logging.debug("Git integration enabled, fetching initial synchronous Git info.")
            try:
                # Use current working directory if no filename yet for initial Git context
                # get_git_info handles None filename by using os.getcwd()
                self.git_info = get_git_info(self.filename) 
                logging.debug(f"Initial synchronous Git info fetched: {self.git_info}")
            except Exception as e_git_init:
                self.git_info = ("", "", "0") # Default on error
                logging.error(f"Failed to get initial synchronous Git info during init: {e_git_init}", exc_info=True)
        else:
            self.git_info = ("", "", "0") # Default if Git is disabled or info display is off
            logging.debug("Git integration or info display is disabled. Git info set to default.")

        # Set locale for character handling (e.g., wcwidth)
        try:
            locale.setlocale(locale.LC_ALL, "") # Use system's default locale settings
            logging.debug(f"Locale set to: {locale.getlocale()}")
        except locale.Error as e_locale:
            logging.error(f"Failed to set system locale: {e_locale}. Character width calculations might be affected.", exc_info=True)

        logging.info("SwayEditor initialized successfully.")


    # ───────────────────── Keybinding Initialization ─────────────────────
    def _load_keybindings(self) -> Dict[str, int]:
        """
        Reads the [keybindings] section from config.toml and the default keybindings,
        then parses them into a dictionary mapping action names to key codes.

        Returns:
            Dict[str, int]: A dictionary where keys are action names (e.g., "save_file")
                            and values are the integer key codes.
        """
        default_keybindings = {
            "delete": "del",
            "paste": "ctrl+v",
            "copy": "ctrl+c",
            "cut": "ctrl+x",
            "undo": "ctrl+z",
            "redo": "shift+z",
            "new_file": "f2",
            "open_file": "ctrl+o",
            "save_file": "ctrl+s",       # Default is string
            "save_as": "f5",
            "select_all": "ctrl+a",
            "quit": "ctrl+q",            # Default is string
            "goto_line": "ctrl+g",
            "git_menu": "f9",
            "help": "f1",
            "find": "ctrl+f",
            "find_next": "f3",
            "search_and_replace": "f6",
            "cancel_operation": "esc",
            "tab": "tab",
            "shift_tab": "shift+tab",
            "lint": "f4",
            "comment_selected_lines": "ctrl+/", # Default is string
            "uncomment_selected_lines": "shift+/", # Default is string
        }
        
        user_keybindings_config = self.config.get("keybindings", {})
        parsed_keybindings: Dict[str, int] = {}

        for action, default_value in default_keybindings.items():
            # Get value from user config; if not present, use default.
            # The value can be a string (e.g., "ctrl+s") or an integer (e.g., 19).
            key_value_from_config: Union[str, int] = user_keybindings_config.get(action, default_value) # type: ignore

            if not key_value_from_config: 
                logging.debug(f"Keybinding for action '{action}' is disabled or empty in configuration.")
                continue
            
            try:
                # _decode_keystring can handle both strings and integers.
                # If key_value_from_config is already an int, _decode_keystring will return it directly.
                key_code = self._decode_keystring(key_value_from_config) 
                parsed_keybindings[action] = key_code
            except ValueError as e:
                logging.error(
                    f"Error parsing keybinding for action '{action}' with value '{key_value_from_config!r}': {e}. "
                    f"This action might not be triggerable via keyboard."
                )
            except Exception as e_unhandled:
                 logging.error(
                    f"Unexpected error parsing keybinding for action '{action}' with value '{key_value_from_config!r}': {e_unhandled}",
                    exc_info=True
                )

        logging.debug(f"Loaded and parsed keybindings (action -> key_code): {parsed_keybindings}")
        return parsed_keybindings

    # ----- Decode ----------- 
    def _decode_keystring(self, key_input: Union[str, int]) -> int: # Accept int or str
        """
        Converts a human-readable key string (e.g., "ctrl+s", "f1", "shift+/")
        or an integer key code directly into its corresponding curses integer key code.
        """
        if isinstance(key_input, int): 
            # If it's already an integer (presumably a valid key code), return it directly.
            # This handles cases where config provides direct key codes like 19 for Ctrl+S.
            logging.debug(f"_decode_keystring: Received integer key code {key_input}, returning as is.")
            return key_input

        # If it's a string, proceed with parsing
        if not isinstance(key_input, str):
            raise ValueError(f"Invalid key_input type: {type(key_input)}. Expected str or int.")

        original_key_string = key_input
        processed_key_string = key_input.strip().lower()
        
        if not processed_key_string:
            raise ValueError("Key string cannot be empty.")

        # Map of named keys to their curses constants or common ordinal values
        # These should all be lowercase for matching processed_key_string.
        named_keys_map: Dict[str, int] = {
            'f1': curses.KEY_F1,  'f2': curses.KEY_F2,  'f3': curses.KEY_F3,
            'f4': getattr(curses, 'KEY_F4', 268),  'f5': getattr(curses, 'KEY_F5', 269),
            'f6': getattr(curses, 'KEY_F6', 270),  'f7': getattr(curses, 'KEY_F7', 271),
            'f8': getattr(curses, 'KEY_F8', 272),  'f9': getattr(curses, 'KEY_F9', 273),
            'f10': getattr(curses, 'KEY_F10', 274), 'f11': getattr(curses, 'KEY_F11', 275),
            'f12': getattr(curses, 'KEY_F12', 276),
            'left': curses.KEY_LEFT,     'right': curses.KEY_RIGHT,
            'up': curses.KEY_UP,         'down': curses.KEY_DOWN,
            'home': curses.KEY_HOME,     'end': getattr(curses, 'KEY_END', curses.KEY_LL),
            'pageup': curses.KEY_PPAGE,  'pgup': curses.KEY_PPAGE,
            'pagedown': curses.KEY_NPAGE, 'pgdn': curses.KEY_NPAGE,
            'delete': curses.KEY_DC,     'del': curses.KEY_DC,
            'backspace': curses.KEY_BACKSPACE, 
            'insert': getattr(curses, 'KEY_IC', 331),
            'tab': curses.ascii.TAB, 
            'enter': curses.KEY_ENTER, 
            'return': curses.KEY_ENTER,
            'space': ord(' '),
            'esc': 27,                   'escape': 27,
            'shift+left': curses.KEY_SLEFT,    'sleft': curses.KEY_SLEFT,
            'shift+right': curses.KEY_SRIGHT,  'sright': curses.KEY_SRIGHT,
            'shift+up': getattr(curses, 'KEY_SR', 337),
            'shift+down': getattr(curses, 'KEY_SF', 336),
            'shift+home': curses.KEY_SHOME,
            'shift+end': curses.KEY_SEND,
            'shift+pageup': getattr(curses, 'KEY_SPPAGE', getattr(curses, 'KEY_SPREVIOUS', 337)),
            'shift+pagedown': getattr(curses, 'KEY_SNPAGE', getattr(curses, 'KEY_SNEXT', 336)),
            'shift+tab': getattr(curses, 'KEY_BTAB', 353),
            '/': ord('/'), 
            '?': ord('?'), 
        }


        # Direct match for named keys (including those with "shift+" prefix defined above)
        if processed_key_string in named_keys_map:
            return named_keys_map[processed_key_string]

        # --- Handle combinations like "ctrl+s", "alt+f1", "ctrl+shift+a" ---
        parts = processed_key_string.split('+')
        base_key_part = parts[-1] 
        modifier_parts = set(parts[:-1]) 

        base_key_code: int

        
        # Explicit handling for specific combinations before generic parsing
        if "ctrl" in modifier_parts and base_key_part == "/":
            base_key_code = 31 
            modifier_parts.remove("ctrl") 
        elif "shift" in modifier_parts and base_key_part == "/":
            base_key_code = ord('?') 
            modifier_parts.remove("shift")
        elif base_key_part in named_keys_map: 
            base_key_code = named_keys_map[base_key_part]
        elif len(base_key_part) == 1:   
            if "shift" in modifier_parts and 'a' <= base_key_part <= 'z':
                base_key_code = ord(base_key_part.upper())
                modifier_parts.remove("shift") 
            else:
                base_key_code = ord(base_key_part) 
        else:
            # This is where "19" would fail if it's not an int already
            raise ValueError(f"Unknown base key '{base_key_part}' in key string '{original_key_string}'")

        if "ctrl" in modifier_parts:
            # This check is now after explicit ctrl+/
            if 'a' <= chr(base_key_code).lower() <= 'z': # Check if it's a letter
                # For Ctrl+A to Ctrl+Z (or Ctrl+a to Ctrl+z)
                # If base_key_code is already uppercase (e.g. from Shift+a), convert to lowercase first
                char_lower = chr(base_key_code).lower()
                base_key_code = ord(char_lower) - ord('a') + 1
            # Other Ctrl combinations (e.g., Ctrl+]) are usually specific codes returned by get_wch()
            # and should be specified as integers in the config if not covered by named_keys.
            # If base_key_code was already set (e.g. 31 for Ctrl+/), this block is skipped.

        if "alt" in modifier_parts:
            base_key_code |= 0x200 
            logging.debug(f"Applied custom Alt modifier (|=0x200) to key '{base_key_part}', resulting code: {base_key_code}")
        
        if "shift" in modifier_parts: # If shift is still left after specific handling
            logging.warning(f"Potentially unhandled 'shift' modifier for base key '{base_key_part}' (resulting code {base_key_code}) in '{original_key_string}'. Key might not work as expected unless get_wch() returns this specific code.")

        return base_key_code

    # ───────────────────── Action Map Setup ─────────────────────
    def _setup_action_map(self) -> Dict[int, Callable[..., Any]]:
        """
        Creates a mapping from integer key codes to editor action methods.
        Combines keybindings from configuration/defaults with built-in curses key handlers.
        """
        # Map of action names (strings) to their corresponding methods in the editor.
        action_to_method_map: Dict[str, Callable] = {
            "open_file":  self.open_file,
            "save_file":  self.save_file,
            "save_as":    self.save_file_as,
            "new_file":   self.new_file,
            "git_menu":   self.integrate_git,
            "copy":       self.copy,
            "cut":        self.cut,
            "paste":      self.paste,
            "undo":       self.undo,
            "redo":       self.redo,
            "handle_home":self.handle_home, # Note: KEY_HOME is also handled below
            "handle_end": self.handle_end,   # Note: KEY_END is also handled below
            "show_lint_panel": self.show_lint_panel, # Could be same as "lint"
            "extend_selection_right": self.extend_selection_right,
            "extend_selection_left":  self.extend_selection_left,
            "select_to_home":         self.select_to_home,
            "select_to_end":          self.select_to_end,
            "extend_selection_up":    self.extend_selection_up,
            "extend_selection_down":  self.extend_selection_down,
            "find":                   self.find_prompt,
            "find_next":              self.find_next,
            "search_and_replace":     self.search_and_replace,
            "goto_line":              self.goto_line,
            "help":                   self.show_help,
            "cancel_operation":       self.handle_escape, # Changed from self.cancel_operation
            "select_all":             self.select_all,
            "delete":                 self.handle_delete, # Note: KEY_DC is also handled below
            "quit":                   self.exit_editor,
            "tab":                    self.handle_smart_tab,
            "shift_tab":              self.handle_smart_unindent,
            "lint":                   self.run_lint_async, 
            "comment_selected_lines": self.do_comment_block,
            "uncomment_selected_lines": self.do_uncomment_block,
        }

        # Final map: key_code (int) -> method (Callable)
        final_key_action_map: Dict[int, Callable] = {}

        # Populate from self.keybindings (which came from _load_keybindings)
        for action_name, key_code_value in self.keybindings.items():
            method_callable = action_to_method_map.get(action_name)
            if method_callable:
                if key_code_value is not None: # Should always be an int after _decode_keystring
                    if key_code_value in final_key_action_map and final_key_action_map[key_code_value] != method_callable:
                        logging.warning(
                            f"Keybinding conflict! Key code {key_code_value} for action '{action_name}' "
                            f"(method '{method_callable.__name__}') is already assigned to method "
                            f"'{final_key_action_map[key_code_value].__name__}'. "
                            f"The binding for '{action_name}' will overwrite the previous one."
                        )
                    final_key_action_map[key_code_value] = method_callable
                    logging.debug(
                        f"Mapped from config/defaults: Action '{action_name}' (key code {key_code_value}) "
                        f"-> Method '{method_callable.__name__}'"
                    )
            else:
                logging.warning(
                    f"Action '{action_name}' found in keybindings configuration but no corresponding method "
                    f"defined in action_to_method_map. This keybinding will be ignored."
                )
        
        # Built-in default handlers for common curses KEY_* constants.
        # These act as fallbacks if not explicitly overridden by self.keybindings.
        # `setdefault` ensures that user-defined bindings take precedence if the key code is the same.
        builtin_curses_key_handlers: Dict[int, Callable] = {
            curses.KEY_UP:        self.handle_up,
            curses.KEY_DOWN:      self.handle_down,
            curses.KEY_LEFT:      self.handle_left,
            curses.KEY_RIGHT:     self.handle_right,
            curses.KEY_HOME:      self.handle_home,    # Note: "handle_home" action can also map here
            curses.KEY_END:       self.handle_end,     # Note: "handle_end" action can also map here
            curses.KEY_PPAGE:     self.handle_page_up,
            curses.KEY_NPAGE:     self.handle_page_down,
            curses.KEY_BACKSPACE: self.handle_backspace, 
            curses.KEY_DC:        self.handle_delete,  # Delete character
            curses.KEY_ENTER:     self.handle_enter,   # Covers most Enter/Return scenarios
            10:                   self.handle_enter,   # ASCII LF (common for Enter)
            13:                   self.handle_enter,   # ASCII CR (less common, but good to cover)
            curses.KEY_SLEFT:     self.extend_selection_left,
            curses.KEY_SRIGHT:    self.extend_selection_right,
            # Shift+Up/Down might have varying KEY constants (KEY_SR/SF, KEY_SPREVIOUS/SNEXT)
            getattr(curses, 'KEY_SR', 337):       self.extend_selection_up, 
            getattr(curses, 'KEY_SF', 336):       self.extend_selection_down,
            getattr(curses, 'KEY_SPREVIOUS', 337):self.extend_selection_up, # Alias for KEY_SR on some systems
            getattr(curses, 'KEY_SNEXT', 336):    self.extend_selection_down, # Alias for KEY_SF
            curses.KEY_SHOME:     self.select_to_home,
            curses.KEY_SEND:      self.select_to_end,
            # Esc (27) should map to handle_escape if that's the desired Esc behavior.
            # This is handled by "cancel_operation" in self.keybindings.
            # 27: self.handle_escape, 
            getattr(curses, 'KEY_IC', 331):     self.toggle_insert_mode, # Insert key
            curses.KEY_RESIZE:    self.handle_resize,    # Window resize event
            getattr(curses, 'KEY_BTAB', 353):   self.handle_smart_unindent, # Shift+Tab
            # curses.ascii.TAB (9) is handled by "tab" in self.keybindings.
        }
        
        # Apply built-in handlers, using setdefault to not overwrite user/default config bindings
        # if they happen to map to the same key code.
        for key_code, method_callable in builtin_curses_key_handlers.items():
            if key_code not in final_key_action_map: # Only add if not already mapped by user config
                final_key_action_map[key_code] = method_callable
                logging.debug(f"Mapped built-in: Key code {key_code} -> Method '{method_callable.__name__}'")
            elif final_key_action_map[key_code] != method_callable:
                logging.debug(
                    f"Built-in handler for key code {key_code} (method '{method_callable.__name__}') "
                    f"was overridden by user/default config for method "
                    f"'{final_key_action_map[key_code].__name__}'."
                )

        # Ensure F4 for linting is present if not overridden.
        # This handles the case where 'lint' or 'show_lint_panel' might be the action name.
        f4_key_code = getattr(curses, 'KEY_F4', 268)
        if f4_key_code not in final_key_action_map:
            lint_method = action_to_method_map.get("lint") or action_to_method_map.get("show_lint_panel")
            if lint_method:
                final_key_action_map[f4_key_code] = lint_method
                logging.debug(f"Mapped fallback F4 (key code {f4_key_code}) to lint method '{lint_method.__name__}'.")

        # Log the final complete action map
        final_map_log_str = {k: v.__name__ for k, v in final_key_action_map.items()}
        logging.debug(f"Final constructed action map (Key Code -> Method Name): {final_map_log_str}")
        
        return final_key_action_map
        
 
    # ─────────────────────  Get comment prefix for current language ─────────────────────
    def get_line_comment_prefix(self) -> Optional[str]:
        """
        Returns the inline comment prefix for the current language.
        Priority: information from the Pygments lexer, then rules on lexer aliases.
        Returns None if the prefix is ​​not defined or the language uses block comments.
        """
        if self._lexer is None:
            self.detect_language()

        if not self._lexer:
            logging.warning("get_line_comment_prefix: Lexer is not defined.")
            return None

        lexer_instance = self._lexer # self._lexer is an instance of the lexer
        lexer_name = lexer_instance.name.lower() # basic lexer name
        lexer_aliases = [alias.lower() for alias in lexer_instance.aliases] # Lexer Alliances
        
        logging.debug(f"get_line_comment_prefix: Determining for lexer '{lexer_name}', aliases: {lexer_aliases}")

        # 1. Trying to get from an attribute (rare, but it happens)
        # This attribute is not standard for all Pygments lexers.
        if hasattr(lexer_instance, 'comment_single_prefix'):   # or another name, if there is one
            prefix = getattr(lexer_instance, 'comment_single_prefix', None)
            if isinstance(prefix, str) and prefix:
                logging.info(f"Using comment prefix '{prefix}' from lexer attribute 'comment_single_prefix' for '{lexer_name}'")
                return prefix

        # 2. Analyze lexer tokens (more complex, but potentially accurate way)
        # This requires understanding the internal structure of lexer rules.
        # Many lexers inherit from RegexLexer.
        # Look for a rule for Comment.Single or Comment.Line.
        # This is an EXPERIMENTAL approach and may not work for all lexers.
        if isinstance(lexer_instance, RegexLexer):
            try:
                # Check the main groups of tokens where comments may be
                for state_name in ['root', 'comment', 'comments']: # Общие имена состояний
                    if state_name in lexer_instance.tokens:
                        rules = lexer_instance.tokens[state_name]
                        for rule in rules:
                            # A rule is a tuple: (regex, token_type, new_state) or (regex, token_type)
                            # We are interested in token_type == Comment.Single or Comment.Line
                            if len(rule) >= 2 and rule[1] in (Comment.Single, Comment.Line):
                                regex_pattern = rule[0]
                                # Trying to extract a prefix from a regular expression.
                                # This is very simplistic and depends on how the regex is written.
                                # Example: r'#.*$' -> '#', r'//.*$' -> '//'
                                # This is very fragile!
                                if isinstance(regex_pattern, str):
                                    if regex_pattern.startswith('#') and regex_pattern.endswith(('.*$', '.*?\n', '.*')):
                                        logging.info(f"Deduced comment prefix '# ' from lexer token rule for '{lexer_name}'")
                                        return "# "
                                    if regex_pattern.startswith('//') and regex_pattern.endswith(('.*$', '.*?\n', '.*')):
                                        logging.info(f"Deduced comment prefix '// ' from lexer token rule for '{lexer_name}'")
                                        return "// "
                                    if regex_pattern.startswith('--') and regex_pattern.endswith(('.*$', '.*?\n', '.*')):
                                        logging.info(f"Deduced comment prefix '-- ' from lexer token rule for '{lexer_name}'")
                                        return "-- "
                                    # ... any popury :) ...
            except Exception as e:
                logging.debug(f"Error trying to deduce comment prefix from lexer tokens for '{lexer_name}': {e}")
                pass # failed, let's move on to the rules for aliases

        # 3. Rules based on lexer aliases (more reliable fallback)
        # Using set for quick membership check.
        all_names_to_check = set([lexer_name] + lexer_aliases)

        # Languages ​​with "#"
        hash_comment_langs = {'python', 'py', 'sage', 'cython', 
                              'ruby', 'rb', 'perl', 'pl', 
                              'bash', 'sh', 'zsh', 'ksh', 'fish', 'shell', 'ash',
                              'makefile', 'dockerfile', 'conf', 'cfg', 'ini', # ini/conf часто # или ;
                              'r', 'yaml', 'yml', 'toml', 
                              'gdscript', 'nim', 'julia', 'jl', 'cmake',
                              'tcl', 'awk', 'sed', 'powershell', 'ps1',
                              'gitconfig', 'gitignore', 'gitattributes', # Git-специфичные файлы
                              'sls', # SaltStack
                              'pp', # Puppet
                              'tf', 'tfvars' # Terraform
                             }
        if not all_names_to_check.isdisjoint(hash_comment_langs):
            # If there is an intersection (i.e. at least one of all_names_to_check is in hash_comment_langs)
            # Special case for INI/CONF - may be and ;
            if 'ini' in all_names_to_check or 'conf' in all_names_to_check:
                # If it's INI/CONF, ; is more canonical, but # also occurs.
                # Let's give priority to ';' for INI/CONF for now, if it's lower.
                # If we get here, then '# '
                logging.info(f"Using comment prefix '# ' for lexer '{lexer_name}' (matched in hash_comment_langs, possibly ini/conf)")
                return "# " # TODO: More complex logic can be made for ini/conf 
            logging.info(f"Using comment prefix '# ' for lexer '{lexer_name}' (matched in hash_comment_langs)")
            return "# "

        # Languages ​​with  "//"
        slash_comment_langs = {'javascript', 'js', 'jsx', 'jsonc', # JSONC (JSON with comments)
                               'typescript', 'ts', 'tsx', 
                               'java', 'kotlin', 'kt', 
                               'c', 'cpp', 'cxx', 'cc', 'objective-c', 'objc', 'objective-c++', 'objcpp',
                               'c#', 'csharp', 'cs', 
                               'go', 'golang', 'swift', 'dart', 'rust', 'rs', 'scala', 
                               'groovy', 'haxe', 'pascal', 'objectpascal', 'delphi', 
                               'php',
                               'glsl', 'hlsl', 'shader', 
                               'd', 'vala', 'ceylon', 'crystal', 'chapel',
                               'processing'
                              }
        if not all_names_to_check.isdisjoint(slash_comment_langs):
            logging.info(f"Using comment prefix '// ' for lexer '{lexer_name}' (matched in slash_comment_langs)")
            return "// "
        
        # Languages ​​with  "--"
        double_dash_comment_langs = {'sql', 'plpgsql', 'tsql', 'mysql', 'postgresql', 'sqlite',
                                     'lua', 'haskell', 'hs', 'ada', 'vhdl', 'elm'}
        if not all_names_to_check.isdisjoint(double_dash_comment_langs):
            logging.info(f"Using comment prefix '-- ' for lexer '{lexer_name}' (matched in double_dash_comment_langs)")
            return "-- "
            
        # Languages ​​with "%"
        percent_comment_langs = {'erlang', 'erl', 'prolog', 'plg', 'latex', 'tex', 
                                 'matlab', 'octave', 'scilab', 'postscript'}
        if not all_names_to_check.isdisjoint(percent_comment_langs):
            logging.info(f"Using comment prefix '% ' for lexer '{lexer_name}' (matched in percent_comment_langs)")
            return "% "

        # Languages ​​with  ";"
        semicolon_comment_langs = {'clojure', 'clj', 'lisp', 'common-lisp', 'elisp', 'emacs-lisp', 
                                   'scheme', 'scm', 'racket', 'rkt', 
                                   'autolisp', 'asm', 'nasm', 'masm', 'nix', # NixOS configuration
                                   'ini', 'properties', 'desktop' # .desktop files, .properties often use ; or #
                                  } 
        if not all_names_to_check.isdisjoint(semicolon_comment_langs):
            logging.info(f"Using comment prefix '; ' for lexer '{lexer_name}' (matched in semicolon_comment_langs)")
            return "; " # For INI/properties this is more canonical than #
            
        # Languages ​​with "!" (Fortran)
        exclamation_comment_langs = {'fortran', 'f90', 'f95', 'f03', 'f08', 'f', 'for'}
        if not all_names_to_check.isdisjoint(exclamation_comment_langs):
            logging.info(f"Using comment prefix '! ' for lexer '{lexer_name}' (matched in exclamation_comment_langs)")
            return "! "

        # Languages ​​with  "REM" or "'" (Basic-like)
        rem_comment_langs = {'vb.net', 'vbnet', 'vbs', 'vbscript', 'basic', 'qbasic', 'freebasic', 'visual basic'}
        if not all_names_to_check.isdisjoint(rem_comment_langs):
            # VB.Net and VBScript use single quote '
            # Old BASIC may have used REM
            logging.info(f"Using comment prefix '\' ' for lexer '{lexer_name}' (matched in rem_comment_langs)")
            return "' " 
            
        # Languages ​​where inline comments are uncommon or block comments are used
        block_comment_only_langs = {'html', 'htm', 'xhtml', 'xml', 'xsd', 'xsl', 'xslt', 'plist', 'rss', 'atom', 'svg', 'vue', 'django', 'jinja', 'jinja2',
                                    'css', 'scss', 'less', 'sass', # Sass (SCSS syntax) can //, but pure Sass cannot.
                                    'json', # Standard JSON does not support comments
                                    'markdown', 'md', 'rst', 
                                    'text', 'txt', 'plaintext', 'log',
                                    'bibtex', 'bib',
                                    'diff', 'patch'
                                   }
        if not all_names_to_check.isdisjoint(block_comment_only_langs):
            logging.info(f"Line comments are not typical or well-defined for lexer '{lexer_name}' (matched in block_comment_only_langs). Returning None.")
            return None
            
        # If this is TextLexer, then there are no comments
        if isinstance(lexer_instance, TextLexer):
            logging.info(f"Lexer is TextLexer ('{lexer_name}'), no line comments. Returning None.")
            return None

        # If we got here, the prefix was not found according to the known rules :))
        logging.warning(f"get_line_comment_prefix: No line comment prefix rule found for lexer '{lexer_name}' (aliases: {lexer_aliases}). Returning None.")
        return None


    def _determine_lines_to_toggle_comment(self) -> Optional[tuple[int, int]]:
        """Defines a range of lines to comment/uncomment."""
        if self.is_selecting and self.selection_start and self.selection_end:
            norm_range = self._get_normalized_selection_range()
            if not norm_range: return None
            start_coords, end_coords = norm_range
            
            start_y = start_coords[0]
            end_y = end_coords[0]
            
            if end_coords[1] == 0 and end_y > start_y:
                end_y -=1    # Do not include the row where the selection ends in column zero unless it is the only row
            return start_y, end_y
        else:
            return self.cursor_y, self.cursor_y


#-This method "toggle_comment_block"  is a single hotkey: The user only needs to remember one combination (e.g. Ctrl+/) for both operations.
# the editor decides what to do. This behavior is common in many modern IDEs (VS Code, JetBrains IDEs, etc.):
# Temporarily disabled and use do_comment_block(self) and do_uncomment_block(self) - Explicit actions)
    def toggle_comment_block(self):
        """
        Comments or uncomments the selected block of lines or the current line.
        Determines the action (comment/uncomment) automatically.
        """
        comment_prefix = self.get_line_comment_prefix()
        if not comment_prefix:
            self._set_status_message("Line comments not supported for this language.")
            return

        line_range = self._determine_lines_to_toggle_comment()
        if line_range is None:
            self._set_status_message("No lines selected to comment/uncomment.")
            return
        
        start_y, end_y = line_range

        with self._state_lock:
            # Определяем, нужно комментировать или раскомментировать.
            # Если ХОТЯ БЫ ОДНА строка в диапазоне НЕ закомментирована (или закомментирована другим префиксом),
            # то комментируем ВЕСЬ блок.
            # Иначе (ВСЕ строки УЖЕ закомментированы ЭТИМ префиксом), то раскомментируем ВЕСЬ блок.
            
            all_lines_are_commented_with_this_prefix = True
            non_empty_lines_exist = False

            for y in range(start_y, end_y + 1):
                if y >= len(self.text): continue
                line = self.text[y]
                if line.strip(): # Если строка не пустая (не только пробелы)
                    non_empty_lines_exist = True
                    # Проверяем, начинается ли НЕПУСТАЯ строка с отступа + префикса
                    stripped_line = line.lstrip()
                    if not stripped_line.startswith(comment_prefix.strip()): # Сравниваем без пробелов вокруг префикса
                        all_lines_are_commented_with_this_prefix = False
                        break 
                # Пустые строки или строки только из пробелов не влияют на решение "все ли закомментированы",
                # но они будут обработаны (закомментированы или оставлены как есть при раскомментировании).

            if not non_empty_lines_exist and (end_y > start_y or not self.text[start_y].strip()):
                # Если все строки в диапазоне пустые или только из пробелов,
                # или если выделена одна пустая строка, то комментируем.
                action_to_perform = "comment"
            elif all_lines_are_commented_with_this_prefix:
                action_to_perform = "uncomment"
            else:
                action_to_perform = "comment"

            logging.debug(f"Toggle comment: Action decided: {action_to_perform} for lines {start_y}-{end_y}")

            if action_to_perform == "comment":
                self.comment_lines(start_y, end_y, comment_prefix)
            else: # "uncomment"
                self.uncomment_lines(start_y, end_y, comment_prefix)

    # ───────────────────── Comment/Uncomment Block ─────────────────────
    #   def toggle_comment_block(self): podobny
    def do_comment_block(self):
        """Always comments the selected block or the current line."""
        comment_prefix = self.get_line_comment_prefix()
        if not comment_prefix:
            self._set_status_message("Line comments not supported for this language.")
            return

        line_range = self._determine_lines_to_toggle_comment()
        if line_range is None:
            self._set_status_message("No lines selected to comment.")
            return
        
        start_y, end_y = line_range
        logging.debug(f"do_comment_block: Commenting lines {start_y}-{end_y}")
        self.comment_lines(start_y, end_y, comment_prefix)

    # ───────────────────── Uncommenting block ─────────────────────
    def do_uncomment_block(self):
        """Always uncomment the selected block or the current line."""
        comment_prefix = self.get_line_comment_prefix()
        if not comment_prefix:
            # No message needed, because if commenting is not supported,
            # then uncommenting is not supported either. do_comment_block will already show it.
            # Can be added if you want an explicit message for Shift+/
            self._set_status_message("Line comments not supported for this language (for uncomment).")
            return

        line_range = self._determine_lines_to_toggle_comment()
        if line_range is None:
            self._set_status_message("No lines selected to uncomment.")
            return
        
        start_y, end_y = line_range
        logging.debug(f"do_uncomment_block: Uncommenting lines {start_y}-{end_y}")
        self.uncomment_lines(start_y, end_y, comment_prefix)

    # ───────────────────── Commenting and uncommenting lines ─────────────────────
    def comment_lines(self, start_y: int, end_y: int, comment_prefix: str):
        with self._state_lock: 
            original_texts = {} 
            min_indent = float('inf')
            non_empty_lines_in_block_indices = []

            for y in range(start_y, end_y + 1):
                if y >= len(self.text): continue
                line = self.text[y]
                if line.strip(): 
                    non_empty_lines_in_block_indices.append(y)
                    indent_len = len(line) - len(line.lstrip())
                    min_indent = min(min_indent, indent_len)
            
            if not non_empty_lines_in_block_indices: 
                min_indent = 0 

            changes_for_undo = []
            selection_before_op = (self.is_selecting, self.selection_start, self.selection_end) if self.is_selecting else None

            # Keep the original cursor state if there is no selection
            cursor_before_op_no_selection = (self.cursor_y, self.cursor_x) if not self.is_selecting else None


            new_selection_start_x_offset = len(comment_prefix)
            new_selection_end_x_offset = len(comment_prefix)


            for y in range(start_y, end_y + 1):
                if y >= len(self.text): continue
                
                original_texts[y] = self.text[y] 
                line_content = self.text[y]
                current_line_is_empty_or_whitespace = not line_content.strip()
                
                insert_pos = 0
                if current_line_is_empty_or_whitespace:
                    # For empty lines, insert at the beginning (after existing spaces)
                    # or just insert the prefix if the line is completely empty.
                    insert_pos = len(line_content) - len(line_content.lstrip(' ')) # Position of first non-whitespace character (or end of line)
                else: 
                    insert_pos = min_indent

                self.text[y] = line_content[:insert_pos] + comment_prefix + line_content[insert_pos:]
                changes_for_undo.append({
                    "line_index": y, 
                    "original_text": original_texts[y], 
                    "new_text": self.text[y]
                })

            # Adjusting selection and cursor
            if self.is_selecting and self.selection_start and self.selection_end:
                s_y, s_x = self.selection_start
                e_y, e_x = self.selection_end
                
                # Shift the x coordinates of the selection. If the line was empty and became "# ", x doesn't change much.
                # If the line had min_indent, then x is shifted by len(comment_prefix).
                # This is a simplification. The exact adjustment is difficult.
                # For now, if the start_y line was not empty, shift s_x.
                if start_y in non_empty_lines_in_block_indices or not self.text[s_y][:s_x].strip(): # if there is no text before the cursor or it is not an empty line
                    self.selection_start = (s_y, s_x + new_selection_start_x_offset)
                
                if end_y in non_empty_lines_in_block_indices or not self.text[e_y][:e_x].strip():
                     self.selection_end = (e_y, e_x + new_selection_end_x_offset)
                
                self.cursor_y, self.cursor_x = self.selection_end
            elif cursor_before_op_no_selection: # if there is no selection, we only adjust the cursor
                # If the current line was not empty, move the cursor
                if self.cursor_y in non_empty_lines_in_block_indices:
                    self.cursor_x += new_selection_start_x_offset
                # If the line was empty and became "#", place the cursor after the prefix
                elif not original_texts[self.cursor_y].strip():
                     self.cursor_x = len(comment_prefix)

            self.modified = True
            self.action_history.append({
                "type": "comment_block",
                "changes": changes_for_undo, 
                "comment_prefix": comment_prefix,
                "start_y": start_y, "end_y": end_y, 
                "selection_before": selection_before_op,
                "cursor_before_no_selection": cursor_before_op_no_selection,
                # Save the state AFTER for redo
                "selection_after": (self.is_selecting, self.selection_start, self.selection_end) if self.is_selecting else None,
                "cursor_after_no_selection": (self.cursor_y, self.cursor_x) if not self.is_selecting else None
            })
            self.undone_actions.clear()
            self._set_status_message(f"Commented lines {start_y+1}-{end_y+1}")

    # This method is used to uncomment lines that were previously commented with the same prefix.
    def uncomment_lines(self, start_y: int, end_y: int, comment_prefix: str):
        with self._state_lock: 
            original_texts = {}
            changes_for_undo = []
            prefix_to_remove_stripped = comment_prefix.strip() 
            
            selection_before_op = (self.is_selecting, self.selection_start, self.selection_end) if self.is_selecting else None
            cursor_before_op_no_selection = (self.cursor_y, self.cursor_x) if not self.is_selecting else None
            
            max_removed_len_at_sel_start = 0
            max_removed_len_at_sel_end = 0


            for y in range(start_y, end_y + 1):
                if y >= len(self.text): continue
                
                original_texts[y] = self.text[y]
                line = self.text[y]
                
                lstripped_line = line.lstrip()
                indent_len = len(line) - len(lstripped_line)
                removed_this_line_len = 0

                if lstripped_line.startswith(prefix_to_remove_stripped):
                    len_to_check_for_space = len(prefix_to_remove_stripped)
                    
                    # Checking if the space after the prefix needs to be removed
                    remove_extra_space = False
                    if comment_prefix.endswith(' ') and not prefix_to_remove_stripped.endswith(' '):
                        if len(lstripped_line) > len_to_check_for_space and lstripped_line[len_to_check_for_space] == ' ':
                            remove_extra_space = True
                    
                    chars_to_actually_remove_from_lstripped = len_to_check_for_space + (1 if remove_extra_space else 0)
                    self.text[y] = line[:indent_len] + lstripped_line[chars_to_actually_remove_from_lstripped:]
                    removed_this_line_len = chars_to_actually_remove_from_lstripped
                    
                    changes_for_undo.append({
                        "line_index": y,
                        "original_text": original_texts[y],
                        "new_text": self.text[y]
                    })

                    if y == start_y: max_removed_len_at_sel_start = removed_this_line_len
                    if y == end_y: max_removed_len_at_sel_end = removed_this_line_len


            if changes_for_undo: 
                self.modified = True
                
                # Adjusting selection and cursor
                if self.is_selecting and self.selection_start and self.selection_end:
                    s_y, s_x = self.selection_start
                    e_y, e_x = self.selection_end
                    self.selection_start = (s_y, max(0, s_x - max_removed_len_at_sel_start))
                    self.selection_end = (e_y, max(0, e_x - max_removed_len_at_sel_end))
                    self.cursor_y, self.cursor_x = self.selection_end
                elif cursor_before_op_no_selection:
                    self.cursor_x = max(0, self.cursor_x - max_removed_len_at_sel_start) # use delete on current line

                self.action_history.append({
                    "type": "uncomment_block",
                    "changes": changes_for_undo,
                    "comment_prefix": comment_prefix, 
                    "start_y": start_y, "end_y": end_y,
                    "selection_before": selection_before_op,
                    "cursor_before_no_selection": cursor_before_op_no_selection,
                    "selection_after": (self.is_selecting, self.selection_start, self.selection_end) if self.is_selecting else None,
                    "cursor_after_no_selection": (self.cursor_y, self.cursor_x) if not self.is_selecting else None
                })
                self.undone_actions.clear()
                self._set_status_message(f"Uncommented lines {start_y+1}-{end_y+1}")
            else:
                self._set_status_message(f"Nothing to uncomment in lines {start_y+1}-{end_y+1}")


    # --------------------- Input Handler --------------------
    def handle_input(self, key: int | str) -> bool:
        """
        Handles all key presses.
        Returns True if a redraw is needed, False otherwise.
        Supports: Unicode characters, special keys, hotkeys, arrows, etc.
        """
        logging.debug("handle_input → key = %r (%s)", key, type(key).__name__)
        
        action_caused_visual_change = False 

        with self._state_lock:
            try:
                logging.debug("Received key code from get_wch(): %r (type: %s)", key, type(key).__name__)

                original_status = self.status_message # Store current status to detect change
                original_modified_flag = self.modified

                # ── 1. Printable character (received as a string from get_wch()) ─────
                #    OR Enter key if it comes as '\n' string
                if isinstance(key, str) and len(key) == 1:
                    if key == '\n': # Explicitly handle newline character from get_wch() as Enter
                        self.handle_enter()
                        action_caused_visual_change = True # Enter always changes content
                    elif wcswidth(key) > 0: # For other printable string characters
                        action_caused_visual_change = self.insert_text(key) 
                    else:
                        self._set_status_message(f"Ignored zero-width or non-handled char: {repr(key)}")
                        action_caused_visual_change = True # Status message was set
                    return action_caused_visual_change # Return the outcome

                # ── 2. Hotkey from action_map (special keys or Ctrl/Alt combinations as int) ─────
                if isinstance(key, int) and key in self.action_map:
                    logging.debug(f"Key {key} found in action_map. Calling method: {self.action_map[key].__name__}")
                    
                    # Some methods may return a flag themselves, others may not.
                    # We can wrap the call or check the state after.
                    # For simplicity, we assume that the action_map methods return a bool or we check the state.
                    # For example, if self.action_map[key]() returned a bool:
                    # action_caused_visual_change = self.action_map[key]()
                    
                    # Alternatively, check the state before and after:
                    old_state = (self.cursor_y, self.cursor_x, self.scroll_top, self.scroll_left, tuple(self.text), self.modified, self.is_selecting, self.selection_start, self.selection_end, self.status_message)
                    
                    method_to_call = self.action_map[key]
                    # If the method is a navigation method, it should return the flag itself.
                    # For other methods, we can rely on the state change.
                    if method_to_call in (self.handle_up, self.handle_down, self.handle_left, self.handle_right, 
                                          self.handle_home, self.handle_end, self.handle_page_up, self.handle_page_down):
                        action_caused_visual_change = method_to_call()
                    else:
                        method_to_call() # call methods like save, open, etc.
                        # Check if anything important has changed
                        new_state = (self.cursor_y, self.cursor_x, self.scroll_top, self.scroll_left, tuple(self.text), self.modified, self.is_selecting, self.selection_start, self.selection_end, self.status_message)
                        if new_state != old_state:
                            action_caused_visual_change = True
                        elif self.status_message != original_status:   # if only the status has changed
                            action_caused_visual_change = True
                        # self.modified - flag is also a good indicator
                        if self.modified != original_modified_flag and self.modified:
                            action_caused_visual_change = True

                    return action_caused_visual_change

                # ── 3. Printable character (received as an integer code) ─────
                if isinstance(key, int) and 32 <= key < 1114112: 
                    try:
                        char_from_code = chr(key)
                        logging.debug(f"Integer key {key} not in action_map. Treating as char: '{repr(char_from_code)}'")
                        if wcswidth(char_from_code) > 0:
                            action_caused_visual_change = self.insert_text(char_from_code)
                        else:
                            logging.debug(f"Integer key {key} (char '{repr(char_from_code)}') is zero-width or non-printable, not inserting.")
                            self._set_status_message(f"Ignored non-printable/zero-width key code: {key} ('{repr(char_from_code)}')")
                            action_caused_visual_change = True 
                    except ValueError: 
                        logging.warning(f"Invalid ordinal for chr(): {key}. Cannot convert to character.")
                        self._set_status_message(f"Invalid key code: {key}")
                        action_caused_visual_change = True
                    except Exception as e:
                        logging.error(f"Error processing integer key {key} as char: {e}", exc_info=True)
                        self._set_status_message(f"Error with key code {key}")
                        action_caused_visual_change = True
                    return action_caused_visual_change

                # ── 4. Other unhandled special integer keys or string sequences ─────
                if isinstance(key, int): 
                    KEY_LOGGER.debug("Unhandled integer key code, not in action_map or printable range: %r", key)
                    self._set_status_message(f"Unhandled key code: {key}")
                    action_caused_visual_change = True
                elif isinstance(key, str): 
                    KEY_LOGGER.debug("Unhandled string key (possible escape sequence): %r", key)
                    self._set_status_message(f"Unhandled key sequence: {repr(key)}")
                    action_caused_visual_change = True
                else: 
                    KEY_LOGGER.debug("Completely unhandled key: %r (type: %s)", key, type(key).__name__)
                    self._set_status_message(f"Unhandled input: {repr(key)}")
                    action_caused_visual_change = True
                
                return action_caused_visual_change

            except Exception as e: 
                logging.exception("Input handler critical error")
                self._set_status_message(f"Input handler error (see log): {str(e)[:50]}")
                return True # Assume redraw needed after critical error
        
        return False # Default if no path taken (should not happen)

    def draw_screen(self, *a, **kw):
        """Старое имя метода – делегируем новому DrawScreen."""
        return self.drawer.draw(*a, **kw)


    # ───────────────────── Flake8 Linter Integration ─────────────────────
    def run_flake8_on_code(self, code_string: str, filename: Optional[str] = "<buffer>") -> None:
        """
        Runs Flake8 analysis on the provided Python code string in a separate thread.
        Results (errors/warnings or a 'no issues' message) are posted to
        self.lint_panel_message (for the lint panel) and a summary to the status bar
        via self._set_status_message.

        This method itself does not return a redraw status, as its primary work is asynchronous.
        The asynchronous part will trigger UI updates via _set_status_message.

        Args:
            code_string (str): The Python source code to be checked.
            filename (Optional[str]): The name of the file (used for logging and by Flake8).
                                      Defaults to "<buffer>" if not provided or None.
        """
        effective_filename_for_flake8 = filename if filename and filename != "noname" else "<buffer>"
        
        # Performance guard: limit analysis for very large files/strings
        # Check string length first to avoid potentially expensive encoding of huge strings.
        if len(code_string) > 750_000: # Approx. character limit
            try:
                # Check byte size after encoding as a secondary check
                if len(code_string.encode('utf-8', errors='ignore')) > 1_000_000: # 1MB limit
                    msg = "Flake8: File too large for analysis (max ~1MB)."
                    logging.warning(f"{msg} (File: {effective_filename_for_flake8})")
                    self._set_status_message(
                        message_for_statusbar=msg, 
                        is_lint_status=True, 
                        full_lint_output=msg, 
                        activate_lint_panel_if_issues=True # Show panel with this size limit message
                    )
                    return # Do not proceed with linting
            except Exception as e_encode:
                logging.warning(f"Could not check byte size of code string for Flake8 due to encoding error: {e_encode}")
                # Proceed with caution if size check fails, or could also return here.

        tmp_file_path: Optional[str] = None # Path to the temporary file used for Flake8

        try:
            # Create a temporary file with a .py suffix so Flake8 recognizes it as Python code.
            # delete=False is necessary because Flake8 needs to open the file by its name.
            # The file will be deleted in the _run_flake8_thread's finally block.
            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.py', delete=False, 
                encoding='utf-8', errors='replace'
            ) as tmp_flake8_file:
                tmp_file_path = tmp_flake8_file.name
                tmp_flake8_file.write(code_string)
            logging.debug(f"Python code for Flake8 analysis written to temporary file: {tmp_file_path}")
        except Exception as e_tempfile:
            logging.exception(f"Failed to create temporary file for Flake8 analysis: {e_tempfile}")
            error_msg = "Flake8: Error creating temporary file for analysis."
            self._set_status_message(
                message_for_statusbar=error_msg, 
                is_lint_status=True, 
                full_lint_output=f"{error_msg}\nDetails: {str(e_tempfile)}", 
                activate_lint_panel_if_issues=True
            )
            return # Cannot proceed without a temporary file

        # --- Inner function to run Flake8 in a separate thread ---
        def _run_flake8_in_thread():
            # This nonlocal reference is crucial for the finally block to access tmp_file_path
            nonlocal tmp_file_path 
            
            final_panel_output: str = "Flake8: Analysis did not run."
            final_statusbar_msg: str = "Flake8: Not run."
            activate_panel_on_completion: bool = False 

            try:
                flake8_cmd_parts = [
                    sys.executable, "-m", "flake8",
                    "--isolated",  # Ignore user/project Flake8 config files
                    # Example: Get max line length from editor config's 'flake8' section
                    f"--max-line-length={self.config.get('flake8', {}).get('max_line_length', 88)}",
                    # TODO: Add more configurable Flake8 options from self.config if desired
                    # e.g., --select=E,W,F --ignore=E123,W404
                    # select_codes = self.config.get('flake8', {}).get('select')
                    # if select_codes: flake8_cmd_parts.extend(["--select", select_codes])
                    # ignore_codes = self.config.get('flake8', {}).get('ignore')
                    # if ignore_codes: flake8_cmd_parts.extend(["--ignore", ignore_codes])
                    str(tmp_file_path)  # The temporary file to analyze
                ]
                logging.debug(f"Executing Flake8 command: {' '.join(shlex.quote(p) for p in flake8_cmd_parts)}")

                flake8_process = subprocess.run(
                    flake8_cmd_parts,
                    capture_output=True, # Capture stdout and stderr
                    text=True,           # Decode output as text (UTF-8 by default if not specified)
                    check=False,         # Do not raise CalledProcessError for non-zero exit codes
                    timeout=self.config.get('flake8', {}).get('timeout', 20), # Timeout for the command
                    encoding='utf-8',    # Explicitly set encoding for output decoding
                    errors='replace'     # How to handle decoding errors
                )

                stdout_raw = flake8_process.stdout.strip()
                stderr_raw = flake8_process.stderr.strip()

                if stderr_raw:
                    logging.warning(
                        f"Flake8 produced stderr output for temp file '{tmp_file_path}' "
                        f"(original: '{effective_filename_for_flake8}'):\n{stderr_raw}"
                    )

                if flake8_process.returncode == 0:
                    # Flake8 found no issues (or all issues were filtered out)
                    final_panel_output = f"Flake8 ({effective_filename_for_flake8}): No issues found."
                    final_statusbar_msg = "Flake8: No issues."
                    activate_panel_on_completion = False # No need to show panel if no issues
                else:
                    # Non-zero return code: Flake8 found issues or encountered an error.
                    if stdout_raw: # Flake8 usually outputs issues to stdout
                        # Replace the temporary file path in the output with the effective filename
                        # for better user readability.
                        if tmp_file_path: # Check if tmp_file_path is not None
                            # Careful with simple replace if filenames could be substrings of code.
                            # Using tmp_file_path + ":" is a bit safer.
                            final_panel_output = stdout_raw.replace(tmp_file_path + ":", effective_filename_for_flake8 + ":")
                        else:
                            final_panel_output = stdout_raw
                        
                        issue_lines = final_panel_output.splitlines()
                        num_issues = len(issue_lines)
                        first_issue_summary = (issue_lines[0][:70] + "..." if issue_lines and len(issue_lines[0]) > 70 
                                               else (issue_lines[0] if issue_lines else ""))
                        final_statusbar_msg = f"Flake8: {num_issues} issue(s). ({first_issue_summary})"
                        activate_panel_on_completion = True
                    elif stderr_raw: # No stdout, but stderr might indicate a Flake8 configuration error
                        final_panel_output = (f"Flake8 ({effective_filename_for_flake8}) execution error:\n{stderr_raw}")
                        final_statusbar_msg = "Flake8: Execution error."
                        activate_panel_on_completion = True
                    else: # Non-zero code, but no output on stdout or stderr - unusual
                        final_panel_output = (f"Flake8 ({effective_filename_for_flake8}): Unknown error "
                                              f"(exit code {flake8_process.returncode}), no output.")
                        final_statusbar_msg = f"Flake8: Unknown error (code {flake8_process.returncode})."
                        activate_panel_on_completion = True
                
            except FileNotFoundError: # If sys.executable or flake8 module itself is not found
                error_message = "Flake8: Executable or module not found. Please ensure Flake8 is installed ('pip install flake8')."
                logging.error(error_message)
                final_panel_output = error_message
                final_statusbar_msg = "Flake8: Not found."
                activate_panel_on_completion = True
            except subprocess.TimeoutExpired:
                timeout_message = f"Flake8 ({effective_filename_for_flake8}): Analysis timed out."
                logging.warning(timeout_message)
                final_panel_output = timeout_message
                final_statusbar_msg = "Flake8: Timeout."
                activate_panel_on_completion = True
            except Exception as e_runtime: # Catch any other runtime errors during Flake8 execution
                logging.exception(
                    f"Runtime error during Flake8 execution for temp file '{tmp_file_path}' "
                    f"(original: '{effective_filename_for_flake8}'): {e_runtime}"
                )
                short_err_msg = f"Flake8 runtime error: {str(e_runtime)[:60]}..."
                full_err_msg = f"Flake8 ({effective_filename_for_flake8}) internal runtime error:\n{traceback.format_exc()}"
                final_panel_output = full_err_msg
                final_statusbar_msg = short_err_msg
                activate_panel_on_completion = True
            finally:
                # Ensure the temporary file is deleted
                if tmp_file_path and os.path.exists(tmp_file_path):
                    try:
                        os.remove(tmp_file_path)
                        logging.debug(f"Temporary Flake8 file '{tmp_file_path}' deleted successfully.")
                    except Exception as e_remove_tmp:
                        logging.warning(f"Failed to delete temporary Flake8 file '{tmp_file_path}': {e_remove_tmp}")
            
            # Update the UI with the results via the main thread's queue/status mechanism
            # This call to _set_status_message is made from the background thread.
            # It updates attributes and puts a message in a queue, which is thread-safe.
            # The main loop processes the queue and updates curses UI elements.
            self._set_status_message(
                message_for_statusbar=final_statusbar_msg, 
                is_lint_status=True, 
                full_lint_output=final_panel_output,
                activate_lint_panel_if_issues=activate_panel_on_completion
            )
            # The show_lint_panel method isn't called directly here;
            # _set_status_message handles the self.lint_panel_active flag.

        # --- Start the Flake8 analysis in the background thread ---
        linter_thread = threading.Thread(
            target=_run_flake8_in_thread, 
            daemon=True, # Thread will exit automatically when the main program exits
            name=f"Flake8LintThread-{os.path.basename(effective_filename_for_flake8)}"
        )
        linter_thread.start()
        # This method (run_flake8_on_code) now returns, the thread does the work.


    def run_lint_async(self, code: Optional[str] = None) -> bool:
        """
        Asynchronously runs Flake8 analysis for the current buffer's content
        if the detected language is Python.
        Sets initial status messages indicating the linting process has started.
        The actual lint results are processed and displayed once the async task completes.

        Args:
            code (Optional[str]): Optionally, the Python source code to check.
                                  If None, the current content of `self.text` is used.

        Returns:
            bool: True if an action was taken that changed the status message (e.g.,
                  "Analysis started" or "Not a Python file"), indicating a redraw is needed.
                  False if no action was taken (e.g., lexer not yet determined and then
                  determined not to be Python without any prior status change).
        """
        logging.debug(f"run_lint_async called. Provided code: {'Yes' if code is not None else 'No'}")
        original_status = self.status_message
        # original_lint_panel_active = self.lint_panel_active # To check if panel state changed *by this call*
        # original_lint_panel_message = self.lint_panel_message

        # 1. Ensure the lexer is determined for the current buffer.
        if self._lexer is None:
            self.detect_language() 
            # If detect_language() itself changed state (e.g. cleared LRU cache),
            # it doesn't directly return a redraw flag to here.
            # The main loop handles redraws based on key input or queue processing.

        # 2. Check if the current language is Python.
        is_python_language = False
        if self._lexer: # Lexer should be set after detect_language()
            lexer_name_lower = self._lexer.name.lower()
            python_lexer_names = {'python', 'python3', 'py', 'cython', 'ipython', 'sage'} 
            if lexer_name_lower in python_lexer_names:
                is_python_language = True
        
        if not is_python_language:
            message = "Flake8: Analysis is only available for Python files."
            full_output_for_panel = message 
            logging.info(
                f"run_lint_async: Skipping linting. Current lexer: "
                f"'{self._lexer.name if self._lexer else "None"}' is not Python."
            )
            # Display this informational message in the lint panel and status bar.
            self._set_status_message(
                message_for_statusbar=message, 
                is_lint_status=True, 
                full_lint_output=full_output_for_panel,
                activate_lint_panel_if_issues=True # Show panel with this info message
            )
            return self.status_message != original_status # Redraw if status changed
        
        # 3. If code is not passed as an argument, get it from the current editor buffer.
        code_to_lint: str
        if code is None:
            # This read should be thread-safe if other parts modify self.text with _state_lock
            with self._state_lock:
                code_to_lint = os.linesep.join(self.text) # Use OS-specific newlines for consistency
        else:
            code_to_lint = code

        # 4. Initiate the Flake8 analysis by calling run_flake8_on_code.
        filename_display = self.filename or "<unsaved buffer>"
        logging.debug(f"run_lint_async: Queuing Flake8 analysis for '{filename_display}'")
        
        # Set an initial status message indicating that analysis is starting.
        # This message will also appear in the lint panel if it's activated.
        self._set_status_message(
            message_for_statusbar="Flake8: Analysis started...", 
            is_lint_status=True, 
            full_lint_output="Flake8: Analysis in progress...", # Initial panel message
            activate_lint_panel_if_issues=True # Show panel with "in progress" state
        )
        
        # Call the method that runs Flake8 asynchronously
        self.run_flake8_on_code(code_to_lint, self.filename) 

        # The method has set a status message ("Analysis started..."), so a redraw is needed.
        return self.status_message != original_status


    def show_lint_panel(self) -> bool:
        """
        Activates or deactivates the linter panel display based on current state
        and whether there is a lint message to show.

        - If there is no lint message (self.lint_panel_message is empty/None),
          this method ensures the panel is deactivated (self.lint_panel_active = False).
        - If there is a lint message, this method ensures the panel is activated
          (self.lint_panel_active = True), making it visible.

        This method is typically called when a user action (e.g., pressing a hotkey like F4)
        intends to explicitly show or refresh the view of the lint panel, or potentially
        to hide it if it's already visible and the user action implies a toggle.
        However, the primary mechanism for activating the panel upon new lint results
        might be through _set_status_message if activate_lint_panel_if_issues is True.

        The actual drawing of the panel's content is handled by the DrawScreen class
        during the draw cycle, based on the self.lint_panel_active flag and
        the content of self.lint_panel_message.

        Returns:
            bool: True if the panel's active state (self.lint_panel_active) was
                  changed by this specific call to show_lint_panel.
                  False if the panel's active state remained unchanged by this call
                  (e.g., it was already active and a message exists, or it was inactive
                  and no message exists).
        """
        logging.debug(
            f"show_lint_panel called. Current state: active={self.lint_panel_active}, "
            f"message_exists={bool(self.lint_panel_message)}"
        )
        
        original_panel_active_state = self.lint_panel_active
        
        if not self.lint_panel_message:
            # Condition: There is no lint message to display.
            # Action: Ensure the panel is not active.
            if self.lint_panel_active: # If it was active but shouldn't be (no message)
                self.lint_panel_active = False
                logging.debug("show_lint_panel: Deactivating panel as there is no lint message.")
                # A status message like "Lint panel hidden" could be set here if desired,
                # but typically the visual disappearance of the panel is enough feedback.
        else:
            # Condition: There IS a lint message to display.
            # Action: Ensure the panel IS active so the message can be seen.
            if not self.lint_panel_active: # If it was inactive but should be (message exists)
                self.lint_panel_active = True
                logging.debug("show_lint_panel: Activating panel to display lint message.")
            # If it was already active and a message exists, no change to self.lint_panel_active here.
            # The content of the panel will be updated by DrawScreen based on self.lint_panel_message.

        # Determine if the call to this method resulted in a change to the panel's active state.
        if self.lint_panel_active != original_panel_active_state:
            logging.debug(f"show_lint_panel: Panel active state changed from {original_panel_active_state} to {self.lint_panel_active}.")
            return True # The active state of the panel was changed.
        
        logging.debug(f"show_lint_panel: Panel active state ({self.lint_panel_active}) remained unchanged.")
        return False # The active state of the panel was not changed by this call.


    ############### Clipboard Handling ####################    
    def _check_pyclip_availability(self) -> bool: # Added return type hint
        """
        Checks the availability of the pyperclip library and underlying system clipboard utilities.
        This is typically called once during editor initialization.
        """
        # First, check if system clipboard usage is enabled in the configuration.
        if not self.config.get("editor", {}).get("use_system_clipboard", True):
            logging.debug("System clipboard usage is disabled by editor configuration.")
            return False # Not available because it's turned off by config

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
        except ImportError: # Should not happen if pyperclip is a listed dependency
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
            , exc_info=True) # Include stack trace for unexpected errors
            return False


    def get_selected_text(self):
        """Возвращает выделенный текст."""
        if not self.is_selecting or self.selection_start is None or self.selection_end is None:
            return ""
        start_row, start_col = self.selection_start
        end_row, end_col = self.selection_end

        # Нормализация: начало должно быть раньше конца
        if start_row > end_row or (start_row == end_row and start_col > end_col):
            start_row, start_col, end_row, end_col = end_row, end_col, start_row, start_col

        selected_lines = []
        if start_row == end_row:
            # Убедимся, что индексы в пределах строки
            line = self.text[start_row]
            start_col = max(0, min(start_col, len(line)))
            end_col = max(0, min(end_col, len(line)))
            selected_lines.append(line[start_col:end_col])
        else:
            # Первая строка (от start_col до конца)
            line = self.text[start_row]
            start_col = max(0, min(start_col, len(line)))
            selected_lines.append(line[start_col:])
            # Средние строки
            for row in range(start_row + 1, end_row):
                if 0 <= row < len(self.text): # Проверка на всякий случай
                    selected_lines.append(self.text[row])
            # Последняя строка (от начала до end_col)
            if 0 <= end_row < len(self.text): # Проверка на всякий случай
                line = self.text[end_row]
                end_col = max(0, min(end_col, len(line)))
                selected_lines.append(line[:end_col])
            # Если end_row > len(self.text)-1 (что не должно произойти при нормализации)
            elif end_row == len(self.text) and end_col == 0:
                 # Особый случай, когда выделение до конца последней строки и переходит на новую
                 pass # Ничего не добавляем, так как end_col=0 на несуществующей строке
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
        original_status = self.status_message # Store to check for actual change
        
        selected_text = self.get_selected_text() # Retrieves text based on self.selection_start/end
        
        if not selected_text:
            # No text was selected (or selection was empty).
            self._set_status_message("Nothing to copy")
            return self.status_message != original_status # Redraw if status changed

        # Text was selected, proceed with copying.
        self.internal_clipboard = selected_text
        current_status_update = "Copied to internal clipboard" # Default message

        if self.use_system_clipboard and self.pyclip_available:
            try:
                pyperclip.copy(selected_text)
                current_status_update = "Copied to system clipboard"
                logging.debug("Selected text copied to system clipboard successfully.")
            except pyperclip.PyperclipException as e:
                logging.error(f"Failed to copy to system clipboard: {str(e)}")
                current_status_update = "Copied to internal clipboard (system clipboard error)"
            except Exception as e: # Catch any other unexpected errors from pyperclip or underlying tools
                 logging.error(f"Unexpected error copying to system clipboard: {e}", exc_info=True)
                 current_status_update = "Copied to internal clipboard (unexpected system clipboard error)"
        
        self._set_status_message(current_status_update)
        
        # Return True if the status message actually changed from its original state.
        # This handles the case where the new status message might be the same as the old one
        # (e.g., if "Nothing to copy" was already the status).
        return self.status_message != original_status


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
            original_text_tuple = tuple(self.text)
            original_cursor_pos = (self.cursor_y, self.cursor_x)
            original_scroll_pos = (self.scroll_top, self.scroll_left)
            original_selection_state = (self.is_selecting, self.selection_start, self.selection_end)
            original_modified_flag = self.modified
            original_status = self.status_message
            
            action_made_change_to_content = False # Tracks if text content actually changed

            if not self.is_selecting:
                self._set_status_message("Nothing to cut (no selection)")
                return self.status_message != original_status # Redraw if status message changed

            # Selection exists, proceed with cutting
            normalized_range = self._get_normalized_selection_range()
            if not normalized_range: # Should ideally not happen if self.is_selecting is True
                logging.warning("cut: is_selecting=True, but no valid normalized range.")
                self.is_selecting = False # Attempt to recover
                self.selection_start = None
                self.selection_end = None
                self._set_status_message("Cut error: Invalid selection state")
                return True # Status changed

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
                self.is_selecting = False # Clear selection state
                self.selection_start = None
                self.selection_end = None
                # Return True if status message changed or selection state changed
                return (self.status_message != original_status or
                        (self.is_selecting, self.selection_start, self.selection_end) != original_selection_state)

            action_made_change_to_content = True # Text was removed
            
            text_for_clipboard = "\n".join(deleted_text_segments)
            
            # Always copy to internal clipboard
            self.internal_clipboard = text_for_clipboard
            status_message_for_cut = "Cut to internal clipboard" # Default message

            # Attempt to copy to system clipboard if enabled and available
            if self.use_system_clipboard and self.pyclip_available:
                try:
                    pyperclip.copy(text_for_clipboard)
                    status_message_for_cut = "Cut to system clipboard"
                    logging.debug("Text cut and copied to system clipboard successfully.")
                except pyperclip.PyperclipException as e:
                    logging.error(f"Failed to copy cut text to system clipboard: {str(e)}")
                    status_message_for_cut = "Cut to internal clipboard (system clipboard error)"
                except Exception as e: # Catch any other unexpected error
                    logging.error(f"Unexpected error copying cut text to system clipboard: {e}", exc_info=True)
                    status_message_for_cut = "Cut to internal clipboard (unexpected system clipboard error)"
            
            # Add the deletion action to history
            self.action_history.append({
                "type": "delete_selection", 
                "text": deleted_text_segments,
                "start": norm_start_coords, 
                "end": norm_end_coords
            })
            self.undone_actions.clear() # Cut is a new atomic action, clear redo stack
            
            # self.modified is already set to True by delete_selected_text_internal
            # Cursor position is also set by delete_selected_text_internal to norm_start_coords
            
            self.is_selecting = False # Selection is gone after cut
            self.selection_start = None
            self.selection_end = None
            
            self._set_status_message(status_message_for_cut)
            
            # Ensure cursor and scroll are valid after the operation
            # Although delete_selected_text_internal sets cursor, _ensure_cursor_in_bounds is a good safeguard
            self._ensure_cursor_in_bounds()
            self._clamp_scroll() # Scroll might need adjustment if cursor position changed significantly

            # Determine if a redraw is needed by comparing overall state
            # Since cut always involves text deletion and status change, it should always return True if successful.
            if (action_made_change_to_content or
                (self.cursor_y, self.cursor_x) != original_cursor_pos or
                (self.scroll_top, self.scroll_left) != original_scroll_pos or
                (self.is_selecting, self.selection_start, self.selection_end) != original_selection_state or
                self.modified != original_modified_flag or # Check if modified flag state actually flipped
                self.status_message != original_status):
                return True
            
            return False # Should not be reached if cut was successful
        

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
            original_text_tuple = tuple(self.text)
            original_cursor_pos = (self.cursor_y, self.cursor_x)
            original_scroll_pos = (self.scroll_top, self.scroll_left)
            original_selection_state = (self.is_selecting, self.selection_start, self.selection_end)
            original_modified_flag = self.modified
            original_status = self.status_message
            
            made_change_to_content = False # Tracks if text content or buffer structure actually changed

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
                    
                    logging.debug(f"paste: Deleting active selection from {norm_start_coords} to {norm_end_coords} before pasting.")
                    
                    deleted_segments = self.delete_selected_text_internal(
                        norm_start_coords[0], norm_start_coords[1],
                        norm_end_coords[0], norm_end_coords[1]
                    )
                    
                    # Record deletion action if something was actually deleted
                    if deleted_segments or (norm_start_coords != norm_end_coords):
                        self.action_history.append({
                            "type": "delete_selection", 
                            "text": deleted_segments,
                            "start": norm_start_coords, 
                            "end": norm_end_coords,     
                        })
                        # Don't clear undone_actions yet, as insertion is part of this user action
                        made_change_to_content = True
                    
                    # delete_selected_text_internal sets the cursor to norm_start_coords
                    effective_insert_y, effective_insert_x = self.cursor_y, self.cursor_x
                    
                    self.is_selecting = False # Selection is now gone
                    self.selection_start = None
                    self.selection_end = None
            
            # 2. Insert the text from clipboard at the effective position
            # Store position before low-level insert for this part of the history
            insert_pos_for_history = (effective_insert_y, effective_insert_x)

            try:
                # Use the low-level insert_text_at_position directly
                if self.insert_text_at_position(text_to_paste, effective_insert_y, effective_insert_x):
                    made_change_to_content = True # Text was inserted
                # insert_text_at_position sets self.modified = True and updates cursor
            except IndexError as e:
                logging.error(f"paste: Error during insert_text_at_position: {e}")
                self._set_status_message(f"Paste insertion error: {e}")
                # If deletion happened, action_history already has it.
                # If insertion failed, the state might be after deletion but before successful paste.
                # We need to decide if we should attempt to undo the deletion part or leave as is.
                # For now, just report error.
                return True # Status changed

            # Add "insert" action to history for the pasted text
            self.action_history.append({
                "type": "insert",
                "text": text_to_paste,
                "position": insert_pos_for_history 
            })
            
            # Now that the compound paste operation (delete + insert) is complete, clear redo stack.
            if made_change_to_content:
                self.undone_actions.clear()
            
            # Final status message
            if made_change_to_content:
                self._set_status_message(f"Pasted from {source_of_paste} clipboard")
            elif self.status_message == original_status: # No content change, status not set by error
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
        
    ############### Selection Handling ####################
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
            if current_line_idx >= len(self.text): # Should not happen with valid cursor
                logging.warning(f"extend_selection_right: cursor_y {current_line_idx} out of bounds.")
                return False # No change possible

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
        if ( (self.cursor_y, self.cursor_x) != original_cursor_pos or
             (self.scroll_top, self.scroll_left) != original_scroll_pos or
             self.is_selecting != original_is_selecting_flag or
             self.selection_start != original_selection_start or
             self.selection_end != original_selection_end ):
            changed_state = True
            logging.debug(
                f"extend_selection_right: New cursor ({self.cursor_y},{self.cursor_x}), "
                f"selection_end ({self.selection_end}). Changed: {changed_state}"
            )
        else:
            logging.debug("extend_selection_right: No change in cursor, scroll, or selection state.")
            
        return changed_state


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
            if current_line_idx >= len(self.text): # Should not happen with valid cursor
                logging.warning(f"extend_selection_left: cursor_y {current_line_idx} out of bounds.")
                return False # No change possible

            # If selection is not active, start it from the current cursor position.
            # When extending left, the initial cursor position becomes the 'anchor' or 'selection_start'
            # if we consider selection_end to be the moving part.
            # Or, if self.selection_start is the fixed point and self.selection_end moves with cursor:
            if not self.is_selecting:
                self.selection_start = (self.cursor_y, self.cursor_x) # Anchor point
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
        if ( (self.cursor_y, self.cursor_x) != original_cursor_pos or
             (self.scroll_top, self.scroll_left) != original_scroll_pos or
             self.is_selecting != original_is_selecting_flag or
             self.selection_start != original_selection_start or # Could change if is_selecting was false
             self.selection_end != original_selection_end ):      # Will always change if cursor_x changed
            changed_state = True
            logging.debug(
                f"extend_selection_left: New cursor ({self.cursor_y},{self.cursor_x}), "
                f"selection_end ({self.selection_end}). Changed: {changed_state}"
            )
        else:
            logging.debug("extend_selection_left: No change in cursor, scroll, or selection state.")
            
        return changed_state


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
        original_scroll_pos = (self.scroll_top, self.scroll_left) # Specifically scroll_left
        original_selection_start = self.selection_start
        original_selection_end = self.selection_end
        original_is_selecting_flag = self.is_selecting
        
        changed_state = False

        with self._state_lock:
            current_line_idx = self.cursor_y
            if current_line_idx >= len(self.text): # Should not happen with valid cursor
                logging.warning(f"select_to_home: cursor_y {current_line_idx} out of bounds.")
                return False # No change possible

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
        if ( (self.cursor_y, self.cursor_x) != original_cursor_pos or
             (self.scroll_top, self.scroll_left) != original_scroll_pos or # Check both scroll dimensions
             self.is_selecting != original_is_selecting_flag or
             self.selection_start != original_selection_start or
             self.selection_end != original_selection_end ):
            changed_state = True
            logging.debug(
                f"select_to_home: New cursor ({self.cursor_y},{self.cursor_x}), "
                f"selection_end ({self.selection_end}). Changed: {changed_state}"
            )
        else:
            logging.debug("select_to_home: No change in cursor, scroll, or selection state.")
            
        return changed_state

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
            if current_line_idx >= len(self.text): # Should not happen with valid cursor
                logging.warning(f"select_to_end: cursor_y {current_line_idx} out of bounds.")
                return False # No change possible

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
        if ( (self.cursor_y, self.cursor_x) != original_cursor_pos or
             (self.scroll_top, self.scroll_left) != original_scroll_pos or
             self.is_selecting != original_is_selecting_flag or
             self.selection_start != original_selection_start or
             self.selection_end != original_selection_end ):
            changed_state = True
            logging.debug(
                f"select_to_end: New cursor ({self.cursor_y},{self.cursor_x}), "
                f"selection_end ({self.selection_end}). Changed: {changed_state}"
            )
        else:
            logging.debug("select_to_end: No change in cursor, scroll, or selection state.")
            
        return changed_state
    

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
        
        with self._state_lock: # Ensure atomicity of state changes
            if not self.text: # Should not happen if text is always at least [""]
                self.text = [""] # Ensure there's at least one line
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
            
            self.is_selecting = True # Mark that selection is active

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
        
        return False # Should technically not be reached if "All text selected" is always set.


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
        if ( (self.cursor_y, self.cursor_x) != original_cursor_pos or
             (self.scroll_top, self.scroll_left) != original_scroll_pos or
             self.is_selecting != original_is_selecting_flag or
             self.selection_start != original_selection_start or # Could change if is_selecting was false
             self.selection_end != original_selection_end ):      # Will change if cursor_y or cursor_x changed
            changed_state = True
            logging.debug(
                f"extend_selection_up: New cursor ({self.cursor_y},{self.cursor_x}), "
                f"selection_end ({self.selection_end}). Changed: {changed_state}"
            )
        else:
            logging.debug("extend_selection_up: No change in cursor, scroll, or selection state.")
            
        return changed_state


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
        if ( (self.cursor_y, self.cursor_x) != original_cursor_pos or
             (self.scroll_top, self.scroll_left) != original_scroll_pos or
             self.is_selecting != original_is_selecting_flag or
             self.selection_start != original_selection_start or
             self.selection_end != original_selection_end ):
            changed_state = True
            logging.debug(
                f"extend_selection_down: New cursor ({self.cursor_y},{self.cursor_x}), "
                f"selection_end ({self.selection_end}). Changed: {changed_state}"
            )
        else:
            logging.debug("extend_selection_down: No change in cursor, scroll, or selection state.")
            
        return changed_state


    # def undo(self):
    #     """
    #     Отменяет последнее действие из истории, восстанавливая текст и позицию курсора.
    #     Поддерживает типы действий: insert, delete_char, delete_newline, delete_selection,
    #     block_indent, block_unindent.
    #     """
    #     with self._state_lock:
    #         if not self.action_history:
    #             self._set_status_message("Нечего отменять")
    #             return
    #         last_action = self.action_history.pop()
    #         action_type = last_action.get("type")
            
    #         # Сохраняем текущее состояние для возможного отката при ошибке
    #         current_state_snapshot = {
    #             "text": [line for line in self.text],
    #             "cursor_y": self.cursor_y, "cursor_x": self.cursor_x,
    #             "is_selecting": self.is_selecting, 
    #             "selection_start": self.selection_start, "selection_end": self.selection_end
    #         }

    #         try:
    #             if action_type == "insert":
    #                 text_to_remove = last_action["text"]
    #                 row, col = last_action["position"] 
    #                 lines_to_remove_list = text_to_remove.split('\n')
    #                 num_lines_in_removed_text = len(lines_to_remove_list)
    #                 if not (0 <= row < len(self.text)): raise IndexError(f"Undo insert: Invalid row {row}")
                    
    #                 if num_lines_in_removed_text == 1:
    #                     current_line_content = self.text[row]
    #                     self.text[row] = current_line_content[:col] + current_line_content[col + len(text_to_remove):]
    #                 else:
    #                     prefix_on_first_line = self.text[row][:col]
    #                     end_row_affected_by_insert = row + num_lines_in_removed_text - 1
    #                     if end_row_affected_by_insert >= len(self.text): raise IndexError(f"Undo insert: Invalid end_row {end_row_affected_by_insert}")
    #                     len_last_inserted_line_segment = len(lines_to_remove_list[-1])
    #                     suffix_on_last_line = self.text[end_row_affected_by_insert][len_last_inserted_line_segment:]
    #                     self.text[row] = prefix_on_first_line + suffix_on_last_line
    #                     del self.text[row + 1 : end_row_affected_by_insert + 1]
    #                 self.cursor_y, self.cursor_x = row, col 

    #             elif action_type == "delete_char":
    #                 y, x = last_action["position"] 
    #                 deleted_char = last_action["text"] 
    #                 if not (0 <= y < len(self.text) and 0 <= x <= len(self.text[y])): raise IndexError(f"Undo delete_char: Invalid position ({y},{x})")
    #                 current_line = self.text[y]
    #                 self.text[y] = current_line[:x] + deleted_char + current_line[x:]
    #                 self.cursor_y, self.cursor_x = y, x 

    #             elif action_type == "delete_newline":
    #                 y, x = last_action["position"] 
    #                 moved_up_content = last_action["text"] 
    #                 if not (0 <= y < len(self.text) and 0 <= x <= len(self.text[y])): raise IndexError(f"Undo delete_newline: Invalid position ({y},{x})")
    #                 current_line_content = self.text[y]
    #                 self.text[y] = current_line_content[:x] 
    #                 self.text.insert(y + 1, moved_up_content) 
    #                 self.cursor_y, self.cursor_x = y, x 

    #             elif action_type == "delete_selection":
    #                 deleted_text_segments = last_action["text"] 
    #                 start_y, start_x = last_action["start"] 
    #                 text_to_restore_str = "\n".join(deleted_text_segments)
    #                 self.insert_text_at_position(text_to_restore_str, start_y, start_x) # insert_text_at_position ставит курсор
    #                 self.cursor_y, self.cursor_x = start_y, start_x # Переустанавливаем в начало для undo

    #             elif action_type == "block_indent":
    #                 original_selection = last_action.get("original_selection")
    #                 indent_str = last_action["indent_str"]
    #                 indent_len = len(indent_str)
    #                 for y_idx in range(last_action["start_line"], last_action["end_line"] + 1):
    #                     if y_idx < len(self.text) and self.text[y_idx].startswith(indent_str):
    #                         self.text[y_idx] = self.text[y_idx][indent_len:]
    #                 if original_selection:
    #                     self.is_selecting, self.selection_start, self.selection_end = True, original_selection[0], original_selection[1]
    #                     if self.selection_end: self.cursor_y, self.cursor_x = self.selection_end
                    
    #             elif action_type == "block_unindent":
    #                 original_selection = last_action.get("original_selection")
    #                 for change in last_action["changes"]:
    #                     if change["line_index"] < len(self.text):
    #                         self.text[change["line_index"]] = change["original_text"] # Восстанавливаем исходный текст строки
    #                 if original_selection:
    #                     self.is_selecting, self.selection_start, self.selection_end = True, original_selection[0], original_selection[1]
    #                     if self.selection_end: self.cursor_y, self.cursor_x = self.selection_end

    #             elif action_type == "comment_block" or action_type == "uncomment_block":
    #                 changes = last_action["changes"] 
    #                 selection_state = last_action.get("selection_before")
    #                 cursor_state_no_sel = last_action.get("cursor_before_no_selection")

    #                 for change_item in reversed(changes): 
    #                     idx = change_item["line_index"]
    #                     if idx < len(self.text):
    #                         self.text[idx] = change_item["original_text"]
                    
    #                 if selection_state:
    #                      self.is_selecting, self.selection_start, self.selection_end = selection_state
    #                      if self.is_selecting and self.selection_end: self.cursor_y, self.cursor_x = self.selection_end
    #                 elif cursor_state_no_sel:
    #                     self.is_selecting = False
    #                     self.selection_start, self.selection_end = None, None
    #                     self.cursor_y, self.cursor_x = cursor_state_no_sel
    #                 else: # На всякий случай сброс
    #                     self.is_selecting = False
    #                     self.selection_start, self.selection_end = None, None
    #             else:
    #                 logging.warning(f"Undo: Неизвестный тип действия: {action_type}")
    #                 self.action_history.append(last_action) 
    #                 return # Не смогли обработать, выходим

    #         except Exception as e:
    #             logging.exception(f"Undo: Ошибка во время undo для типа действия {action_type}: {e}")
    #             self._set_status_message(f"Undo не удалось для {action_type}: {str(e)[:80]}...")
    #             self.action_history.append(last_action) 
    #             # Откат к состоянию до попытки undo
    #             self.text = current_state_snapshot["text"]
    #             self.cursor_y, self.cursor_x = current_state_snapshot["cursor_y"], current_state_snapshot["cursor_x"]
    #             self.is_selecting = current_state_snapshot["is_selecting"]
    #             self.selection_start, self.selection_end = current_state_snapshot["selection_start"], current_state_snapshot["selection_end"]
    #             return

    #         self.undone_actions.append(last_action) 
    #         self.modified = True 
            
    #         # Общий сброс выделения, если оно не было явно восстановлено
    #         if action_type not in ["block_indent", "block_unindent", "comment_block", "uncomment_block"] or \
    #            (action_type in ["block_indent", "block_unindent"] and not last_action.get("original_selection")) or \
    #            (action_type in ["comment_block", "uncomment_block"] and not last_action.get("selection_before")):
    #             self.is_selecting = False 
    #             self.selection_start = None
    #             self.selection_end = None

    #         self._set_status_message("Действие отменено")


    def undo(self) -> bool:
        """
        Undoes the last action from the action_history stack.
        Restores the text, cursor position, and selection state to what it was
        before the last action was performed.

        Returns:
            bool: True if the editor's state (text, cursor, scroll, selection, modified flag,
                  or status message) changed as a result of the undo operation, False otherwise.
        """
        with self._state_lock:
            original_status = self.status_message # For checking if status message changes

            if not self.action_history:
                self._set_status_message("Nothing to undo")
                return self.status_message != original_status # Redraw if status message changed

            # Store current state to compare against after undoing the action
            pre_undo_text_tuple = tuple(self.text)
            pre_undo_cursor_pos = (self.cursor_y, self.cursor_x)
            pre_undo_scroll_pos = (self.scroll_top, self.scroll_left)
            pre_undo_selection_state = (self.is_selecting, self.selection_start, self.selection_end)
            pre_undo_modified_flag = self.modified

            last_action = self.action_history.pop()
            action_type = last_action.get("type")
            changed_by_this_undo_operation = False # Flag if this undo altered content/selection/cursor

            logging.debug(f"Undo: Attempting to undo action of type '{action_type}'")

            try:
                if action_type == "insert":
                    # To undo an insert, we delete the inserted text.
                    # 'text' is the text that was inserted.
                    # 'position' is (row, col) where insertion started.
                    text_that_was_inserted = last_action["text"]
                    row, col = last_action["position"]
                    
                    lines_inserted = text_that_was_inserted.split('\n')
                    num_lines_in_inserted_text = len(lines_inserted)

                    if not (0 <= row < len(self.text)):
                        raise IndexError(f"Undo insert: Invalid start row {row} for current text.")

                    if num_lines_in_inserted_text == 1: # Single-line insert
                        # Delete the single line of text that was inserted
                        len_inserted = len(text_that_was_inserted)
                        if not (0 <= col <= len(self.text[row]) - len_inserted): # Check if deletion is possible
                             raise IndexError(f"Undo insert: Text mismatch or invalid col for deletion on line {row}.")
                        self.text[row] = self.text[row][:col] + self.text[row][col + len_inserted:]
                    else: # Multi-line insert
                        # First line: remove the first segment of inserted text
                        # The original line content before this insert was prefix + suffix.
                        # After insert, it became: prefix + lines_inserted[0]
                        # And then lines_inserted[1]...lines_inserted[-2]
                        # And then lines_inserted[-1] + suffix
                        
                        # This part needs careful reconstruction of what was there *before* the insert.
                        # The action should ideally store enough info to revert, e.g. text_before_split, text_after_join
                        # For now, a simplified deletion based on what was inserted:
                        end_row_affected_by_insert = row + num_lines_in_inserted_text - 1
                        if end_row_affected_by_insert >= len(self.text):
                            raise IndexError(f"Undo insert: End row {end_row_affected_by_insert} out of bounds for current text.")

                        # Assume line 'row' now contains self.text[row][:col] + lines_inserted[0]
                        # and line 'end_row_affected_by_insert' contains lines_inserted[-1] + (original_suffix from line 'row')
                        # To revert: line 'row' should become self.text[row][:col] + (original_suffix from line 'row' which is now at end of end_row_affected_by_insert)
                        
                        # This is complex. A simpler action for "insert" might be "delete_range" for undo.
                        # Using the provided mock's logic as a base, but it's not fully robust for multi-line without more info.
                        # For simplicity, let's assume the mock logic for multi-line deletion is a placeholder
                        # and a real implementation would restore the original state more directly.
                        # The following is a more direct attempt to remove the inserted lines:
                        original_suffix_from_line_row = self.text[end_row_affected_by_insert][len(lines_inserted[-1]):]
                        self.text[row] = self.text[row][:col] + original_suffix_from_line_row
                        del self.text[row + 1 : end_row_affected_by_insert + 1]
                        
                    self.cursor_y, self.cursor_x = row, col # Restore cursor to where insertion started
                    changed_by_this_undo_operation = True

                elif action_type == "delete_char":
                    # To undo a delete_char, we re-insert the character.
                    # 'text' is the character that was deleted.
                    # 'position' is (y,x) where the character was (and where cursor stayed).
                    y, x = last_action["position"]
                    deleted_char = last_action["text"]
                    if not (0 <= y < len(self.text) and 0 <= x <= len(self.text[y])):
                         raise IndexError(f"Undo delete_char: Invalid position ({y},{x}) for insertion.")
                    self.text[y] = self.text[y][:x] + deleted_char + self.text[y][x:]
                    self.cursor_y, self.cursor_x = y, x # Cursor remains at the position of re-inserted char
                    changed_by_this_undo_operation = True
                
                elif action_type == "delete_newline":
                    # To undo a delete_newline (merge), we re-split the line.
                    # 'text' is the content of the line that was merged up.
                    # 'position' is (y,x) where cursor ended after merge (end of line y).
                    y, x_at_split_point = last_action["position"]
                    moved_up_content = last_action["text"]
                    if not (0 <= y < len(self.text) and 0 <= x_at_split_point <= len(self.text[y])):
                        raise IndexError(f"Undo delete_newline: Invalid position ({y},{x_at_split_point}) for split.")
                    
                    current_line_content = self.text[y]
                    self.text[y] = current_line_content[:x_at_split_point] # Line y keeps content before split
                    self.text.insert(y + 1, moved_up_content) # Re-insert the moved_up_content as new line y+1
                    self.cursor_y, self.cursor_x = y, x_at_split_point # Cursor goes to split point
                    changed_by_this_undo_operation = True

                elif action_type == "delete_selection":
                    # To undo a delete_selection, we re-insert the deleted text segments.
                    # 'text' is a list of deleted string segments.
                    # 'start' is (start_y, start_x) where deletion began and cursor was placed.
                    deleted_text_segments = last_action["text"] # list[str]
                    start_y, start_x = last_action["start"]
                    
                    text_to_restore_str = "\n".join(deleted_text_segments)
                    # insert_text_at_position will set cursor to end of inserted text.
                    if self.insert_text_at_position(text_to_restore_str, start_y, start_x):
                        changed_by_this_undo_operation = True
                    # For undo, we want cursor at the beginning of what was re-inserted.
                    self.cursor_y, self.cursor_x = start_y, start_x 
                
                elif action_type in ("block_indent", "block_unindent", "comment_block", "uncomment_block"):
                    # These actions store 'changes': list of {"line_index", "original_text", "new_text"}
                    # and 'selection_before', 'cursor_before_no_selection'.
                    # To undo, we restore the "original_text" for each change.
                    changes = last_action.get("changes", [])
                    if not changes:
                        logging.warning(f"Undo ({action_type}): No 'changes' data found in action.")
                    
                    for change_item in reversed(changes): # Restore in reverse order of application
                        idx = change_item["line_index"]
                        if idx < len(self.text):
                            if self.text[idx] != change_item["original_text"]:
                                self.text[idx] = change_item["original_text"]
                                changed_by_this_undo_operation = True
                        else:
                            logging.warning(f"Undo ({action_type}): Line index {idx} out of bounds. Skipping change.")
                    
                    # Restore selection and cursor state as it was *before* the original operation
                    selection_state_before_op = last_action.get("selection_before")
                    cursor_state_no_sel_before_op = last_action.get("cursor_before_no_selection")

                    if selection_state_before_op:
                        (current_is_selecting, current_sel_start, current_sel_end) = (self.is_selecting, self.selection_start, self.selection_end)
                        self.is_selecting, self.selection_start, self.selection_end = selection_state_before_op
                        if self.is_selecting and self.selection_end:
                            self.cursor_y, self.cursor_x = self.selection_end
                        if (current_is_selecting, current_sel_start, current_sel_end) != selection_state_before_op:
                             changed_by_this_undo_operation = True # Selection state itself changed
                    elif cursor_state_no_sel_before_op:
                        (current_is_selecting, current_cursor_y, current_cursor_x) = (self.is_selecting, self.cursor_y, self.cursor_x)
                        self.is_selecting = False
                        self.selection_start, self.selection_end = None, None
                        self.cursor_y, self.cursor_x = cursor_state_no_sel_before_op
                        if (current_is_selecting or 
                            (current_cursor_y, current_cursor_x) != cursor_state_no_sel_before_op):
                            changed_by_this_undo_operation = True
                    else: # Fallback
                        current_is_selecting = self.is_selecting
                        self.is_selecting = False
                        self.selection_start, self.selection_end = None, None
                        if current_is_selecting : changed_by_this_undo_operation = True
                    
                    if not changes and not changed_by_this_undo_operation: # If no line changes and no sel/cursor change
                        pass # No actual change by this undo operation
                    else:
                        if not changed_by_this_redo_operation: # If text didn't change but sel/cursor did
                             changed_by_this_redo_operation = True
                
                else:
                    logging.warning(f"Undo: Unknown action type '{action_type}'. Cannot undo.")
                    self.action_history.append(last_action) # Put it back if not handled
                    self._set_status_message(f"Undo failed: Unknown action type '{action_type}'")
                    return True # Status changed

            except IndexError as e:
                logging.error(f"Undo: IndexError during undo of '{action_type}': {e}", exc_info=True)
                self._set_status_message(f"Undo error for '{action_type}': Index out of bounds.")
                self.action_history.append(last_action) # Put action back
                return True # Status changed
            except Exception as e:
                logging.exception(f"Undo: Unexpected error during undo of '{action_type}': {e}")
                self._set_status_message(f"Undo error for '{action_type}': {str(e)[:60]}...")
                self.action_history.append(last_action)
                return True # Status changed

            self.undone_actions.append(last_action)
            
            # Determine `self.modified` state:
            # If action_history is now empty, it means we've undone all changes back to the last saved state.
            # Otherwise, the file is still considered modified.
            # This assumes the initial state (when file loaded/created) had self.modified = False.
            if not self.action_history:
                self.modified = False # Undone all changes to the point of last save/new file
                logging.debug("Undo: History empty, file reverted to last saved state (modified=False).")
            else:
                self.modified = True # Still modifications compared to last save
            
            # Ensure cursor and scroll are valid after any operation
            self._ensure_cursor_in_bounds()
            scroll_changed_by_clamp = self._clamp_scroll_and_check_change(pre_undo_scroll_pos)

            # Determine if a redraw is needed
            final_redraw_needed = False
            if (changed_by_this_undo_operation or # Core content/selection/cursor changed by the undo logic itself
                tuple(self.text) != pre_undo_text_tuple or # Check text content explicitly
                (self.cursor_y, self.cursor_x) != pre_undo_cursor_pos or
                scroll_changed_by_clamp or
                (self.is_selecting, self.selection_start, self.selection_end) != pre_undo_selection_state or
                self.modified != pre_undo_modified_flag): # Modified flag itself changed
                final_redraw_needed = True
            
            if final_redraw_needed:
                self._set_status_message("Action undone")
                logging.debug(f"Undo successful and state changed for action type '{action_type}'.")
            else:
                if self.status_message == original_status : # Only set if no other status (like error) was set
                    self._set_status_message("Undo: No effective change from current state")
                logging.debug(f"Undo for action type '{action_type}' resulted in no effective change from current state.")
            
            return final_redraw_needed or (self.status_message != original_status)


    def redo(self) -> bool:
        """
        Redoes the last undone action from the undone_actions stack.
        Restores the text, cursor position, and selection state to what it was
        after the original action was performed.

        Returns:
            bool: True if the editor's state (text, cursor, scroll, selection, modified flag,
                  or status message) changed as a result of the redo operation, False otherwise.
        """
        with self._state_lock:
            original_status = self.status_message # For checking if status message actually changes

            if not self.undone_actions:
                self._set_status_message("Nothing to redo")
                return self.status_message != original_status # Redraw if status changed

            # Store current state to compare against after redoing the action
            pre_redo_text_tuple = tuple(self.text)
            pre_redo_cursor_pos = (self.cursor_y, self.cursor_x)
            pre_redo_scroll_pos = (self.scroll_top, self.scroll_left)
            pre_redo_selection_state = (self.is_selecting, self.selection_start, self.selection_end)
            pre_redo_modified_flag = self.modified

            action_to_redo = self.undone_actions.pop()
            action_type = action_to_redo.get("type")
            changed_by_this_redo_operation = False # Flag to track if this specific redo operation altered content/selection/cursor

            logging.debug(f"Redo: Attempting to redo action of type '{action_type}'")

            try:
                if action_type == "insert":
                    text_to_insert = action_to_redo["text"]
                    row, col = action_to_redo["position"]
                    # insert_text_at_position updates cursor and self.modified
                    if self.insert_text_at_position(text_to_insert, row, col):
                        changed_by_this_redo_operation = True
                    # Cursor is set by insert_text_at_position

                elif action_type == "delete_char":
                    y, x = action_to_redo["position"] # Position where char was, cursor stays
                    if not (0 <= y < len(self.text) and 0 <= x < len(self.text[y])):
                        raise IndexError(f"Redo delete_char: Invalid position ({y},{x}) for current text state.")
                    # Re-perform the deletion
                    self.text[y] = self.text[y][:x] + self.text[y][x + 1:]
                    self.cursor_y, self.cursor_x = y, x 
                    self.modified = True
                    changed_by_this_redo_operation = True

                elif action_type == "delete_newline":
                    y, x_after_merge = action_to_redo["position"] # Position where cursor ended up
                    # To redo, we merge line y+1 into line y.
                    if not (0 <= y < len(self.text) - 1): # Must have line y and y+1
                        raise IndexError(f"Redo delete_newline: Not enough lines to merge at line {y+1}.")
                    self.text[y] += self.text.pop(y + 1)
                    self.cursor_y, self.cursor_x = y, x_after_merge
                    self.modified = True
                    changed_by_this_redo_operation = True

                elif action_type == "delete_selection":
                    start_y, start_x = action_to_redo["start"]
                    end_y, end_x = action_to_redo["end"]
                    # delete_selected_text_internal updates cursor and self.modified
                    deleted_segments = self.delete_selected_text_internal(start_y, start_x, end_y, end_x)
                    if deleted_segments or (start_y,start_x) != (end_y,end_x) : # if something was deleted
                        changed_by_this_redo_operation = True
                    # Cursor is set by delete_selected_text_internal

                elif action_type in ("block_indent", "block_unindent", "comment_block", "uncomment_block"):
                    # These actions store 'changes': list of {"line_index", "original_text", "new_text"}
                    # and 'selection_after', 'cursor_after_no_selection'.
                    # For redo, we re-apply the "new_text" from each change item.
                    changes = action_to_redo.get("changes", [])
                    if not changes: # Should not happen if action was recorded correctly
                        logging.warning(f"Redo ({action_type}): No 'changes' data found in action.")
                    
                    for change_item in changes:
                        idx = change_item["line_index"]
                        if idx < len(self.text):
                            if self.text[idx] != change_item["new_text"]: # Apply only if different
                                self.text[idx] = change_item["new_text"]
                                changed_by_this_redo_operation = True
                        else:
                            logging.warning(f"Redo ({action_type}): Line index {idx} out of bounds. Skipping.")
                    
                    # Restore selection and cursor state as it was *after* the original operation
                    selection_state_after_op = action_to_redo.get("selection_after")
                    cursor_state_no_sel_after_op = action_to_redo.get("cursor_after_no_selection")

                    if selection_state_after_op:
                        (current_is_selecting, current_sel_start, current_sel_end) = (self.is_selecting, self.selection_start, self.selection_end)
                        self.is_selecting, self.selection_start, self.selection_end = selection_state_after_op
                        if self.is_selecting and self.selection_end:
                            self.cursor_y, self.cursor_x = self.selection_end
                        if (current_is_selecting, current_sel_start, current_sel_end) != selection_state_after_op:
                            changed_by_this_redo_operation = True
                    elif cursor_state_no_sel_after_op:
                        (current_is_selecting, current_cursor_y, current_cursor_x) = (self.is_selecting, self.cursor_y, self.cursor_x)
                        self.is_selecting = False
                        self.selection_start, self.selection_end = None, None
                        self.cursor_y, self.cursor_x = cursor_state_no_sel_after_op
                        if (current_is_selecting or 
                            (current_cursor_y, current_cursor_x) != cursor_state_no_sel_after_op):
                            changed_by_this_redo_operation = True
                    else: # Fallback if no specific cursor/selection state was stored
                        current_is_selecting = self.is_selecting
                        self.is_selecting = False 
                        self.selection_start, self.selection_end = None, None
                        if current_is_selecting : changed_by_this_redo_operation = True
                        # Cursor might have been implicitly set by line changes, or might need explicit placement.
                        # For block operations, usually ends up at end of selection or a predictable spot.

                    if changes: # If there were changes applied
                        self.modified = True 
                        if not changed_by_this_redo_operation: # If text didn't change but selection/cursor did
                             changed_by_this_redo_operation = True # ensure it's true
                
                else:
                    logging.warning(f"Redo: Unknown action type '{action_type}'. Cannot redo.")
                    self.undone_actions.append(action_to_redo) # Put it back if not handled
                    self._set_status_message(f"Redo failed: Unknown action type '{action_type}'")
                    return True # Status changed

            except IndexError as e:
                logging.error(f"Redo: IndexError during redo of '{action_type}': {e}", exc_info=True)
                self._set_status_message(f"Redo error for '{action_type}': Index out of bounds.")
                self.undone_actions.append(action_to_redo) # Put action back on undone_actions
                return True # Status changed, state might be inconsistent, redraw needed
            except Exception as e:
                logging.exception(f"Redo: Unexpected error during redo of '{action_type}': {e}")
                self._set_status_message(f"Redo error for '{action_type}': {str(e)[:60]}...")
                self.undone_actions.append(action_to_redo)
                return True # Status changed

            # If redo was successful (or at least attempted without throwing unhandled exception from this block)
            self.action_history.append(action_to_redo) 
            
            # `modified` flag should generally be True after a redo,
            # as it implies a change from the state before the preceding undo.
            if changed_by_this_redo_operation: # If the specific redo logic confirmed a content/selection/cursor change
                self.modified = True # Ensure modified is set if the action was substantive
            
            # Ensure cursor and scroll are valid after any operation
            self._ensure_cursor_in_bounds()
            # Call _clamp_scroll and check if it induced a change
            scroll_changed_by_clamp = self._clamp_scroll_and_check_change(pre_redo_scroll_pos)

            # Determine if a redraw is needed by comparing overall state
            final_redraw_needed = False
            if (changed_by_this_redo_operation or
                (self.cursor_y, self.cursor_x) != pre_redo_cursor_pos or
                scroll_changed_by_clamp or
                (self.is_selecting, self.selection_start, self.selection_end) != pre_redo_selection_state or
                self.modified != pre_redo_modified_flag): # Check if modified flag itself changed status
                final_redraw_needed = True
            
            if final_redraw_needed:
                self._set_status_message("Action redone")
                logging.debug(f"Redo successful and state changed for action type '{action_type}'.")
            else: 
                # This case implies the redo operation resulted in the exact same state as before it ran
                # (e.g., redoing a no-op action, or action_to_redo was flawed and didn't change anything)
                # AND status message hasn't changed from original_status yet.
                if self.status_message == original_status : # Only set if no other status (like error) was set
                    self._set_status_message("Redo: No effective change from current state")
                logging.debug(f"Redo for action type '{action_type}' resulted in no effective change from current state.")
            
            # Return True if a redraw is needed due to state changes OR if status message changed
            return final_redraw_needed or (self.status_message != original_status)

    # вспомогательный метод 
    def _clamp_scroll_and_check_change(self, original_scroll_tuple: Tuple[int, int]) -> bool:
        """
        Calls _clamp_scroll and returns True if scroll_top or scroll_left changed
        from the provided original_scroll_tuple.
        """
        old_st, old_sl = original_scroll_tuple
        self._clamp_scroll() # This method updates self.scroll_top and self.scroll_left
        return self.scroll_top != old_st or self.scroll_left != old_sl
    

    #  Это вспомогательный метод - функция, которая только читает состояние выделения 
    # (self.is_selecting, self.selection_start, self.selection_end) и не изменяет никакого состояния редактора. 
    # Его задача — вернуть нормализованные координаты или None.
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
            logging.debug(f"_get_normalized_selection_range: Swapped selection points. Original: (({sy1},{sx1}), ({sy2},{sx2})), Normalized: (({norm_start_y},{norm_start_x}), ({norm_end_y},{norm_end_x}))")
        else:
            # Original order is already normalized
            norm_start_y, norm_start_x = sy1, sx1
            norm_end_y, norm_end_x = sy2, sx2
            logging.debug(f"_get_normalized_selection_range: Selection points already normalized: (({norm_start_y},{norm_start_x}), ({norm_end_y},{norm_end_x}))")
        
        return ((norm_start_y, norm_start_x), (norm_end_y, norm_end_x))
    
    
    def insert_text_at_position(self, text: str, row: int, col: int) -> bool: # Added return type bool
        """
        Low-level insertion of `text` at the logical position (row, col).
        DOES NOT add to action history - the caller is responsible for that.
        Cursor is set immediately after the inserted text.
        Raises IndexError if row or col is invalid for insertion.
        Returns True if text was non-empty and thus inserted, False otherwise.
        """
        if not text:
            logging.debug("insert_text_at_position: empty text -> no action, returning False")
            return False # No change

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
            logging.warning(f"insert_text_at_position: column {col} out of bounds for line {row} (len {current_line_len}). Clamping.")
            col = max(0, min(col, current_line_len))

        logging.debug(f"insert_text_at_position: text={text!r} at row={row}, col={col}")

        lines_to_insert = text.split('\n')

        original_line_prefix = self.text[row][:col]
        original_line_suffix = self.text[row][col:] # "tail" of the original line

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
            self.cursor_x = col + len(lines_to_insert[0]) # Length of the inserted text (first/only line)
        else:
            # Moved to the last inserted logical line
            self.cursor_y = row + len(lines_to_insert) - 1
            self.cursor_x = len(lines_to_insert[-1]) # Length of the last inserted line segment

        self.modified = True
        logging.debug(f"insert_text_at_position: cursor now at (y={self.cursor_y}, x={self.cursor_x})")
        return True # Text was inserted

    
    def insert_text(self, text: str) -> bool:
        """
        Main public method for text insertion.
        Handles active selection by deleting it first (records 'delete_selection' action).
        Then inserts the new text (records 'insert' action).
        Manages action history for undo/redo.
        Cursor is set after the inserted text.
        Returns True if text was actually inserted or selection was modified/deleted, False otherwise.
        """
        if not text: # If inserting an empty string, only selection deletion might occur.
            if self.is_selecting: # If selection exists, deleting it is a change.
                logging.debug("insert_text: empty text, but selection exists. Deleting selection.")
                # Fall through to selection deletion logic.
            else:
                logging.debug("insert_text: empty text and no selection, no change.")
                return False

        made_change_overall = False
        with self._state_lock:
            effective_insert_y, effective_insert_x = self.cursor_y, self.cursor_x
            original_status = self.status_message # To check if status changes by side effect

            # 1. Handle active selection: delete it first
            if self.is_selecting:
                normalized_selection = self._get_normalized_selection_range()
                if normalized_selection:
                    norm_start_coords, norm_end_coords = normalized_selection
                    # The deletion will be the first part of the "insert" operation conceptually
                    # if text is not empty. If text is empty, this is just a deletion.
                    
                    logging.debug(f"insert_text: Deleting active selection from {norm_start_coords} to {norm_end_coords} before insertion.")
                    
                    deleted_segments = self.delete_selected_text_internal(
                        norm_start_coords[0], norm_start_coords[1],
                        norm_end_coords[0], norm_end_coords[1]
                    )
                    
                    # Record deletion action if something was actually deleted
                    if deleted_segments or (norm_start_coords != norm_end_coords):
                        self.action_history.append({
                            "type": "delete_selection",
                            "text": deleted_segments,
                            "start": norm_start_coords,
                            "end": norm_end_coords,
                        })
                        # Do not clear undone_actions here if text insertion will follow,
                        # as both deletion and insertion are part of one compound user action.
                        # However, if `text` is empty, this is the only action.
                        if not text: # If only deleting selection due to empty text insert
                            self.undone_actions.clear()
                        
                        made_change_overall = True # Selection was deleted/modified
                    
                    # delete_selected_text_internal should have set the cursor
                    effective_insert_y, effective_insert_x = self.cursor_y, self.cursor_x

                    self.is_selecting = False
                    self.selection_start = None
                    self.selection_end = None
                    logging.debug(f"insert_text: Selection processed. Cursor at ({self.cursor_y}, {self.cursor_x}).")
            
            # 2. Insert the new text (if any)
            if text: # Only proceed if there's actual text to insert
                # Store position before low-level insert for history, relative to after selection deletion
                insert_pos_for_history = (effective_insert_y, effective_insert_x)
                
                # Logging before calling the low-level insertion
                if 0 <= effective_insert_y < len(self.text):
                    logging.debug(f"insert_text: About to call low-level insert at ({effective_insert_y},{effective_insert_x}), line[{effective_insert_y}] = {self.text[effective_insert_y]!r}")
                else: # Should not happen if buffer always has at least one line
                    logging.debug(f"insert_text: About to call low-level insert at ({effective_insert_y},{effective_insert_x}). Buffer length: {len(self.text)}")

                try:
                    if self.insert_text_at_position(text, effective_insert_y, effective_insert_x):
                        made_change_overall = True # Text was inserted by low-level function
                    # insert_text_at_position sets self.modified = True
                except IndexError as e:
                    logging.error(f"insert_text: Error during insert_text_at_position: {e}")
                    self._set_status_message(f"Insertion error: {e}")
                    return True # Status changed, so redraw needed

                # Logging after low-level insertion
                if 0 <= self.cursor_y < len(self.text):
                    logging.debug(f"insert_text: After low-level insert, line[{self.cursor_y}] = {self.text[self.cursor_y]!r}. Cursor at ({self.cursor_y}, {self.cursor_x}).")
                else:
                    logging.debug(f"insert_text: After low-level insert. Cursor at ({self.cursor_y}, {self.cursor_x}). Buffer length: {len(self.text)}")

                # Add "insert" action to history
                self.action_history.append({
                    "type": "insert",
                    "text": text,
                    "position": insert_pos_for_history 
                })
                # If a selection was deleted AND text was inserted, these are part of one logical user operation.
                # undone_actions should be cleared only once at the end of such a compound operation.
                self.undone_actions.clear() 
            
            if made_change_overall:
                # Set status message only if no other more specific message (like an error) was set
                if self.status_message == original_status: 
                    self._set_status_message("Text inserted" if text else "Selection deleted")
                logging.debug(f"insert_text: Completed. Text '{text!r}' processed. Final cursor ({self.cursor_y}, {self.cursor_x}).")
            else: # No change (e.g. inserting empty text with no selection)
                 logging.debug(f"insert_text: No effective change made for text '{text!r}'.")

        return made_change_overall


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

        self.modified = True # Обновляем статус модификации


    # Увеличивает отступ для выделенных строк.
    def handle_block_unindent(self) -> bool:
        """
        Decreases indentation for the selected lines.
        Returns True if any line was unindented or status message changed, False otherwise.
        """
        if not self.is_selecting or not self.selection_start or not self.selection_end:
            # This method should only be called when there's an active selection.
            # If called otherwise, it's a logical error or no-op.
            return False 

        made_change = False # Flag to track if any text was actually modified
        original_status = self.status_message
        
        # Preserve original selection and cursor to compare for changes later
        # This helps determine if a redraw is truly needed beyond just status message.
        original_sel_start_coords = self.selection_start
        original_sel_end_coords = self.selection_end
        original_cursor_y, original_cursor_x = self.cursor_y, self.cursor_x

        with self._state_lock:
            norm_range = self._get_normalized_selection_range()
            if not norm_range: 
                # Should not happen if is_selecting and selection_start/end are valid
                logging.warning("handle_block_unindent: Could not get normalized selection range despite active selection.")
                return False

            start_coords, end_coords = norm_range
            start_y, start_x_orig_sel = start_coords # Original selection start x
            end_y, end_x_orig_sel = end_coords     # Original selection end x
            
            tab_size = self.config.get("editor", {}).get("tab_size", 4)
            use_spaces = self.config.get("editor", {}).get("use_spaces", True)
            
            # Determine what to remove: tab_size spaces or one tab character
            # unindent_width_chars is the number of *characters* to try to remove (1 for tab, tab_size for spaces)
            unindent_width_chars = tab_size if use_spaces else 1 
            
            changes_for_undo_list = [] # Stores dicts: {"line_index": y, "original_text": ..., "new_text": ...}
            lines_actually_unindented_count = 0

            # Store original text of lines that might be affected for precise undo
            original_line_texts_map = {
                y_idx: self.text[y_idx] for y_idx in range(start_y, end_y + 1) if y_idx < len(self.text)
            }

            for y_iter in range(start_y, end_y + 1):
                if y_iter >= len(self.text): 
                    continue

                current_line_content = self.text[y_iter]
                prefix_removed_this_line = ""
                
                if use_spaces:
                    chars_actually_removed = 0
                    # Count leading spaces up to unindent_width_chars (tab_size)
                    for i in range(min(len(current_line_content), unindent_width_chars)):
                        if current_line_content[i] == ' ':
                            chars_actually_removed += 1
                        else:
                            break
                    if chars_actually_removed > 0:
                        prefix_removed_this_line = current_line_content[:chars_actually_removed]
                        self.text[y_iter] = current_line_content[chars_actually_removed:]
                else: # use_tabs
                    if current_line_content.startswith('\t'):
                        prefix_removed_this_line = '\t'
                        self.text[y_iter] = current_line_content[1:]
                
                if prefix_removed_this_line: # If something was actually removed from this line
                    changes_for_undo_list.append({
                        "line_index": y_iter,
                        "original_text": original_line_texts_map.get(y_iter, current_line_content), # Fallback, though should exist
                        "new_text": self.text[y_iter]
                    })
                    lines_actually_unindented_count += 1
                    made_change = True # Overall modification happened
            
            if made_change:
                self.modified = True
                
                # Adjust selection and cursor based on the unindentation.
                # This is a complex part, as different lines might have unindented by different amounts
                # or not at all. A simple approach is to reduce x-coordinates by a fixed amount if
                # the line was part of the unindent operation, but this can be inaccurate.
                
                # For simplicity, let's adjust by the configured unindent_width_chars if the lines were start/end of selection.
                # A more robust way would be to calculate the actual width change of the prefix removed.
                # Let's assume a simple shift for now, or rely on the user re-adjusting selection.

                # Simplified cursor/selection adjustment:
                # If the line containing selection_start was unindented, adjust selection_start.x
                sel_start_y, sel_start_x = self.selection_start if self.selection_start else (0,0) # type: ignore
                sel_end_y, sel_end_x = self.selection_end if self.selection_end else (0,0) # type: ignore

                # Check if the first line of selection was actually unindented
                if any(change["line_index"] == sel_start_y for change in changes_for_undo_list):
                    # How much was actually removed from this specific line?
                    # This requires finding the change for sel_start_y.
                    # For simplicity, we'll use unindent_width_chars, but this is an approximation.
                    # A more precise way is to sum wcwidth of the removed prefix for that line.
                    sel_start_x = max(0, sel_start_x - unindent_width_chars) 
                
                if any(change["line_index"] == sel_end_y for change in changes_for_undo_list):
                    sel_end_x = max(0, sel_end_x - unindent_width_chars)

                self.selection_start = (sel_start_y, sel_start_x)
                self.selection_end = (sel_end_y, sel_end_x)
                
                # Typically, cursor is at the end of the selection after an operation
                self.cursor_y, self.cursor_x = self.selection_end 

                self.action_history.append({
                    "type": "uncomment_block", # Re-using "uncomment_block" as it's a line-by-line restoration
                                             # Alternatively, a dedicated "block_unindent" type for undo.
                                             # Using "uncomment_block" implies original_text in changes for undo.
                    "changes": changes_for_undo_list,
                    "comment_prefix": "", # Not relevant here, but part of "uncomment_block" structure
                    "start_y": start_y, "end_y": end_y, # Range of lines attempted
                    "selection_before": (True, original_sel_start_coords, original_sel_end_coords),
                    "cursor_before_no_selection": None, # Was a selection op
                    "selection_after": (self.is_selecting, self.selection_start, self.selection_end),
                    "cursor_after_no_selection": None
                })
                self.undone_actions.clear()
                self._set_status_message(f"Unindented {lines_actually_unindented_count} line(s)")
                logging.debug(
                    f"Block unindent: {lines_actually_unindented_count} lines from {start_y}-{end_y} unindented. "
                    f"New selection: {self.selection_start} -> {self.selection_end}"
                )
                return True # Text changed and status changed
            else:
                self._set_status_message("Nothing to unindent in selection")
                # Check if status actually changed from original to determine redraw
                return self.status_message != original_status 

    def handle_block_unindent(self) -> bool:
        """
        Decreases indentation for the selected lines.
        Returns True if any line was unindented or status message changed, False otherwise.
        """
        if not self.is_selecting or not self.selection_start or not self.selection_end:
            # This method should only be called when there's an active selection.
            # If called otherwise, it's a logical error or no-op.
            return False 

        made_change = False # Flag to track if any text was actually modified
        original_status = self.status_message
        
        # Preserve original selection and cursor to compare for changes later
        # This helps determine if a redraw is truly needed beyond just status message.
        original_sel_start_coords = self.selection_start
        original_sel_end_coords = self.selection_end
        original_cursor_y, original_cursor_x = self.cursor_y, self.cursor_x

        with self._state_lock:
            norm_range = self._get_normalized_selection_range()
            if not norm_range: 
                # Should not happen if is_selecting and selection_start/end are valid
                logging.warning("handle_block_unindent: Could not get normalized selection range despite active selection.")
                return False

            start_coords, end_coords = norm_range
            start_y, start_x_orig_sel = start_coords # Original selection start x
            end_y, end_x_orig_sel = end_coords     # Original selection end x
            
            tab_size = self.config.get("editor", {}).get("tab_size", 4)
            use_spaces = self.config.get("editor", {}).get("use_spaces", True)
            
            # Determine what to remove: tab_size spaces or one tab character
            # unindent_width_chars is the number of *characters* to try to remove (1 for tab, tab_size for spaces)
            unindent_width_chars = tab_size if use_spaces else 1 
            
            changes_for_undo_list = [] # Stores dicts: {"line_index": y, "original_text": ..., "new_text": ...}
            lines_actually_unindented_count = 0

            # Store original text of lines that might be affected for precise undo
            original_line_texts_map = {
                y_idx: self.text[y_idx] for y_idx in range(start_y, end_y + 1) if y_idx < len(self.text)
            }

            for y_iter in range(start_y, end_y + 1):
                if y_iter >= len(self.text): 
                    continue

                current_line_content = self.text[y_iter]
                prefix_removed_this_line = ""
                
                if use_spaces:
                    chars_actually_removed = 0
                    # Count leading spaces up to unindent_width_chars (tab_size)
                    for i in range(min(len(current_line_content), unindent_width_chars)):
                        if current_line_content[i] == ' ':
                            chars_actually_removed += 1
                        else:
                            break
                    if chars_actually_removed > 0:
                        prefix_removed_this_line = current_line_content[:chars_actually_removed]
                        self.text[y_iter] = current_line_content[chars_actually_removed:]
                else: # use_tabs
                    if current_line_content.startswith('\t'):
                        prefix_removed_this_line = '\t'
                        self.text[y_iter] = current_line_content[1:]
                
                if prefix_removed_this_line: # If something was actually removed from this line
                    changes_for_undo_list.append({
                        "line_index": y_iter,
                        "original_text": original_line_texts_map.get(y_iter, current_line_content), # Fallback, though should exist
                        "new_text": self.text[y_iter]
                    })
                    lines_actually_unindented_count += 1
                    made_change = True # Overall modification happened
            
            if made_change:
                self.modified = True
                
                # Adjust selection and cursor based on the unindentation.
                # This is a complex part, as different lines might have unindented by different amounts
                # or not at all. A simple approach is to reduce x-coordinates by a fixed amount if
                # the line was part of the unindent operation, but this can be inaccurate.
                
                # For simplicity, let's adjust by the configured unindent_width_chars if the lines were start/end of selection.
                # A more robust way would be to calculate the actual width change of the prefix removed.
                # Let's assume a simple shift for now, or rely on the user re-adjusting selection.

                # Simplified cursor/selection adjustment:
                # If the line containing selection_start was unindented, adjust selection_start.x
                sel_start_y, sel_start_x = self.selection_start if self.selection_start else (0,0) # type: ignore
                sel_end_y, sel_end_x = self.selection_end if self.selection_end else (0,0) # type: ignore

                # Check if the first line of selection was actually unindented
                if any(change["line_index"] == sel_start_y for change in changes_for_undo_list):
                    # How much was actually removed from this specific line?
                    # This requires finding the change for sel_start_y.
                    # For simplicity, we'll use unindent_width_chars, but this is an approximation.
                    # A more precise way is to sum wcwidth of the removed prefix for that line.
                    sel_start_x = max(0, sel_start_x - unindent_width_chars) 
                
                if any(change["line_index"] == sel_end_y for change in changes_for_undo_list):
                    sel_end_x = max(0, sel_end_x - unindent_width_chars)

                self.selection_start = (sel_start_y, sel_start_x)
                self.selection_end = (sel_end_y, sel_end_x)
                
                # Typically, cursor is at the end of the selection after an operation
                self.cursor_y, self.cursor_x = self.selection_end 

                self.action_history.append({
                    "type": "uncomment_block", # Re-using "uncomment_block" as it's a line-by-line restoration
                                             # Alternatively, a dedicated "block_unindent" type for undo.
                                             # Using "uncomment_block" implies original_text in changes for undo.
                    "changes": changes_for_undo_list,
                    "comment_prefix": "", # Not relevant here, but part of "uncomment_block" structure
                    "start_y": start_y, "end_y": end_y, # Range of lines attempted
                    "selection_before": (True, original_sel_start_coords, original_sel_end_coords),
                    "cursor_before_no_selection": None, # Was a selection op
                    "selection_after": (self.is_selecting, self.selection_start, self.selection_end),
                    "cursor_after_no_selection": None
                })
                self.undone_actions.clear()
                self._set_status_message(f"Unindented {lines_actually_unindented_count} line(s)")
                logging.debug(
                    f"Block unindent: {lines_actually_unindented_count} lines from {start_y}-{end_y} unindented. "
                    f"New selection: {self.selection_start} -> {self.selection_end}"
                )
                return True # Text changed and status changed
            else:
                self._set_status_message("Nothing to unindent in selection")
                # Check if status actually changed from original to determine redraw
                return self.status_message != original_status 
    
    # Уменьшает отступ текущей строки, если нет выделения.
    def unindent_current_line(self) -> bool:
        """
        Decreases the indentation of the current line if there is no selection.
        Returns True if the line was unindented or status message changed, False otherwise.
        """
        if self.is_selecting: # This action is for non-selection cases
            return False # No change made by this specific handler

        original_status = self.status_message
        original_line_content = ""
        original_cursor_x = self.cursor_x
        made_change = False

        with self._state_lock:
            y = self.cursor_y
            if y >= len(self.text): 
                return False # Cursor out of bounds, no change

            original_line_content = self.text[y]
            line = self.text[y]

            if not line or not (line.startswith(' ') or line.startswith('\t')):
                self._set_status_message("Nothing to unindent at line start")
                return self.status_message != original_status

            tab_size = self.config.get("editor", {}).get("tab_size", 4)
            use_spaces = self.config.get("editor", {}).get("use_spaces", True)
            
            removed_prefix_len = 0 # Length in characters of the removed prefix
            original_prefix_for_history = ""

            if use_spaces:
                chars_to_remove_count = 0
                # Count how many leading spaces to remove, up to tab_size
                for i in range(min(len(line), tab_size)):
                    if line[i] == ' ':
                        chars_to_remove_count += 1
                    else:
                        break
                if chars_to_remove_count > 0:
                    original_prefix_for_history = line[:chars_to_remove_count]
                    self.text[y] = line[chars_to_remove_count:]
                    removed_prefix_len = chars_to_remove_count
                    self.cursor_x = max(0, self.cursor_x - removed_prefix_len)
                    made_change = True
            else: # use_tabs
                if line.startswith('\t'):
                    original_prefix_for_history = '\t'
                    self.text[y] = line[1:]
                    removed_prefix_len = 1 # A tab character is 1 char, its display width varies
                    # Adjust cursor_x. If cursor was after the tab, it moves left by the display width of the tab.
                    # This is tricky. For simplicity, just by 1 logical char if it was > 0.
                    # A more accurate adjustment would consider the tab's display width at its original position.
                    self.cursor_x = max(0, self.cursor_x - 1) 
                    made_change = True

            if made_change:
                self.modified = True
                # For consistency with block_unindent, use a similar action structure
                # The "changes" item for undo should store line index and the actual prefix removed.
                self.action_history.append({
                    "type": "block_unindent", # Using the same type for undo/redo simplicity
                    "changes": [{"line_index": y, "original_text": original_line_content, "new_text": self.text[y]}], # More complete info for undo
                    # Storing original selection as if it was a single point for consistency
                    "original_selection": ((y, original_cursor_x), (y, original_cursor_x)),
                    "final_selection": ((y, self.cursor_x), (y, self.cursor_x)),
                    "cursor_before_no_selection": (y, original_cursor_x),
                    "cursor_after_no_selection": (self.cursor_y, self.cursor_x)
                })
                self.undone_actions.clear()
                self._set_status_message("Line unindented")
                logging.debug(f"Unindented line {y}. Removed prefix of length {removed_prefix_len}. Cursor at {self.cursor_x}")
                return True
            else:
                # This case might be reached if line started with non-space/non-tab indent,
                # or if use_spaces is true and line starts with tabs (or vice-versa).
                # Or if the "Nothing to unindent at line start" was already set.
                if self.status_message == original_status: # Avoid overwriting more specific message
                    self._set_status_message("Nothing effectively unindented")
                return self.status_message != original_status
        return False # Should ideally not be reached if logic above is complete


    def handle_smart_unindent(self) -> bool:
        """Handles smart unindent. Returns True if text/selection changed."""
        """
        Интеллектуальное уменьшение отступа (аналог Shift+Tab).
        Если есть выделение - уменьшает отступ у всех выделенных строк.
        Если нет выделения - уменьшает отступ у текущей строки.
        """
        if self.is_selecting:
            return self.handle_block_unindent() # This should return bool
        else:
            return self.unindent_current_line() # This should return bool


    @functools.lru_cache(maxsize=20000) # Настройте maxsize по необходимости
    def _get_tokenized_line(self, line_content: str, lexer_id: int, is_text_lexer: bool) -> List[Tuple[str, int]]:
        """
        Tokenizes a single line of content using the current lexer (identified by id).
        This method is memoized using lru_cache.
        `lexer_id` is used to ensure cache invalidation if the lexer object changes.
        `is_text_lexer` is a boolean flag for a common special case.
        """
        # Эта функция должна быть независимой от состояния self._lexer напрямую, 
        # но мы передаем lexer_id, чтобы lru_cache мог различать вызовы для разных лексеров.
        # Текущий self._lexer будет использоваться для фактической токенизации.
        
        # Если self._lexer не определен, это проблема, но lru_cache вызовется с lexer_id=None (или id(None))
        # Лучше убедиться, что лексер есть, перед вызовом этой функции из apply_syntax_highlighting_with_pygments.
        if self._lexer is None: # Защита, хотя вызывающий код должен это обеспечить
            logging.warning("_get_tokenized_line called with self._lexer being None. Returning default.")
            return [(line_content, curses.color_pair(0))] # Возвращаем дефолт

        # Проверяем, соответствует ли переданный lexer_id текущему self._lexer.id
        # Это важно, если self._lexer может измениться между вызовами, которые lru_cache считает одинаковыми.
        # Однако, если lexer_id передается как id(self._lexer) из вызывающего кода, эта проверка здесь избыточна.

        token_color_map = { # Копируем или получаем доступ к карте цветов
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
            Token.Generic.Emph: curses.color_pair(3) | curses.A_BOLD, Token.Generic.Strong: curses.color_pair(2) | curses.A_BOLD,
            Token.Generic.Prompt: curses.color_pair(7), Token.Generic.Output: curses.color_pair(0),
        }
        default_color = curses.color_pair(0)
        tokenized_segments = []

        # Используем is_text_lexer флаг (переданный как аргумент) для TextLexer
        if is_text_lexer:
            # logging.debug(f"Pygments _get_tokenized_line: Using TextLexer direct passthrough for content: '{line_content[:70]}'")
            if not line_content:
                tokenized_segments.append(("", default_color))
            else:
                tokenized_segments.append((line_content, default_color))
        else:
            try:
                # logging.debug(f"Pygments _get_tokenized_line: Lexing with '{self._lexer.name}': '{line_content[:70]}'")
                raw_tokens = list(lex(line_content, self._lexer)) # Используем текущий self._lexer

                if not raw_tokens and line_content:
                    tokenized_segments.append((line_content, default_color))
                elif not line_content:
                    tokenized_segments.append(("", default_color))
                else:
                    for token_type, text_value in raw_tokens:
                        color_attr = default_color
                        current_type = token_type
                        while current_type:
                            if current_type in token_color_map:
                                color_attr = token_color_map[current_type]
                                break
                            current_type = current_type.parent
                        tokenized_segments.append((text_value, color_attr))
            except Exception as e:
                logging.error(f"Pygments _get_tokenized_line: Error tokenizing: '{line_content[:70]}'. Error: {e}", exc_info=True)
                tokenized_segments = [(line_content, default_color)]
        
        return tokenized_segments

    def apply_syntax_highlighting_with_pygments(self, lines: list[str], line_indices: list[int]):
        """
        Applies syntax highlighting by calling a memoized function for each line.
        """
        if self._lexer is None:
            self.detect_language() # Ensures self._lexer is set
            if self._lexer is None: # Should not happen if detect_language works
                 logging.error("Pygments apply_syntax: Lexer is still None after detect_language.")
                 # Fallback: return all lines with default color
                 default_color = curses.color_pair(0)
                 return [[(line_content, default_color)] for line_content in lines]

        logging.debug(f"Pygments apply_syntax: Using lexer '{self._lexer.name}' (id: {id(self._lexer)})")
        
        highlighted_lines_result = []
        current_lexer_id = id(self._lexer)
        is_text_lexer_instance = isinstance(self._lexer, TextLexer)

        # Очищаем кэш lru_cache, если лексер изменился.
        # Это важно, т.к. _get_tokenized_line кэширует на основе lexer_id.
        # Если сам объект self._lexer поменялся (например, открыли файл другого типа),
        # то старый кэш для _get_tokenized_line (который зависел от старого lexer_id)
        # будет недействителен для нового лексера.
        # `lru_cache` сам по себе не знает, что `self._lexer` внутри `_get_tokenized_line` изменился,
        # если мы не передаем ему что-то, что отражает это изменение.
        # Передача `lexer_id` в `_get_tokenized_line` решает эту проблему.
        # Но если мы хотим быть абсолютно уверены при смене лексера, можно явно сбросить кэш:
        # if hasattr(self, '_last_lexer_id_for_cache') and self._last_lexer_id_for_cache != current_lexer_id:
        #     self._get_tokenized_line.cache_clear()
        #     logging.info(f"Lexer changed from id {self._last_lexer_id_for_cache} to {current_lexer_id}. Cleared _get_tokenized_line cache.")
        # self._last_lexer_id_for_cache = current_lexer_id


        for line_content, line_idx_val in zip(lines, line_indices):
            # line_hash и line_idx_val не нужны как параметры для _get_tokenized_line,
            # так как lru_cache будет кэшировать на основе line_content.
            # Мы передаем current_lexer_id, чтобы кэш был специфичен для текущего лексера.
            
            # Logging cache info for _get_tokenized_line can be done via its cache_info() method if needed for debugging
            # Example: logging.debug(self._get_tokenized_line.cache_info())
            
            tokenized_segments = self._get_tokenized_line(line_content, current_lexer_id, is_text_lexer_instance)
            highlighted_lines_result.append(tokenized_segments)
        
        return highlighted_lines_result


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
        self.highlighted_matches = [] # Сбрасываем подсветку поиска
        self.search_matches = []
        self.search_term = ""
        self.current_match_idx = -1


    def init_colors(self):
        """Создаём цветовые пары curses и заполняем self.colors."""
        if not curses.has_colors():
            self.colors = {}
            return
        curses.start_color()
        curses.use_default_colors()
        bg = -1  # прозрачный фон

        # Базовые цветовые пары (подправлены по теме)
        curses.init_pair(1, curses.COLOR_WHITE,    bg)  # comment -> белый
        curses.init_pair(2, curses.COLOR_BLUE,     bg)  # keyword -> синий
        curses.init_pair(3, curses.COLOR_GREEN,    bg)  # string -> зелёный
        curses.init_pair(4, curses.COLOR_MAGENTA,  bg)  # literal, number -> магента
        curses.init_pair(5, curses.COLOR_CYAN,     bg)  # decorator, tag -> циан
        curses.init_pair(6, curses.COLOR_WHITE,    bg)  # operator, variable -> белый
        curses.init_pair(7, curses.COLOR_YELLOW,   bg)  # builtin, line_number -> жёлтый
        curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_RED)    # error -> чёрный на красном
        curses.init_pair(9, curses.COLOR_BLACK, curses.COLOR_YELLOW) # search_highlight -> чёрный на жёлтом
        # Дополнительные пары для статуса и Git
        curses.init_pair(10, curses.COLOR_WHITE,   bg)  # status (bright_white)
        curses.init_pair(11, curses.COLOR_RED,     bg)  # status_error (красный текст)
        curses.init_pair(12, curses.COLOR_GREEN,   bg)  # git_info (зелёный текст)
        curses.init_pair(13, curses.COLOR_GREEN,   bg)  # git_dirty (будет использован с A_BOLD)

        self.colors = {
            "comment":   curses.color_pair(1),
            "keyword":   curses.color_pair(2),
            "string":    curses.color_pair(3),
            "literal":   curses.color_pair(4),
            "number":    curses.color_pair(4),
            "type":      curses.color_pair(7),  # классы/типы – жёлтым
            "decorator": curses.color_pair(5),
            "tag":       curses.color_pair(5),
            "operator":  curses.color_pair(6),
            "variable":  curses.color_pair(6),
            "builtins":  curses.color_pair(7),
            "line_number": curses.color_pair(7),
            "error":     curses.color_pair(8) | curses.A_BOLD,
            "status":    curses.color_pair(10) | curses.A_BOLD,
            "status_error": curses.color_pair(11) | curses.A_BOLD,
            "search_highlight": curses.color_pair(9),
            "git_info":  curses.color_pair(12),
            "git_dirty": curses.color_pair(13) | curses.A_BOLD,
            # Дополнительные цвета/сокращения
            "green":     curses.color_pair(12),  # зелёный (используем пару12 как green)
        }


    def detect_language(self):
        """
        Detects the file language based on extension or content and sets the lexer.
        Clears the Pygments tokenization LRU cache if the lexer changes.
        """
        new_lexer = None
        # Store the id of the old lexer to detect if it has changed.
        old_lexer_id = id(self._lexer) if self._lexer else None
        
        try:
            if self.filename and self.filename != "noname":
                extension = os.path.splitext(self.filename)[1].lower().lstrip(".")
                if extension:
                    try:
                        new_lexer = get_lexer_by_name(extension)
                        logging.debug(f"Pygments: Detected language by extension: {extension} -> {new_lexer.name}")
                    except Exception: # Pygments raises various exceptions for unknown lexers (e.g., ClassNotFound)
                        logging.debug(f"Pygments: No lexer found for extension '{extension}'. Trying content guess.")
                        # If extension didn't help, try guessing from content
                        # Use a slice of the text to avoid reading huge files into memory for guessing
                        # Taking first 200 lines or up to 10000 characters, whichever is smaller/comes first.
                        content_sample = "\n".join(self.text[:200])[:10000] 
                        try:
                            new_lexer = guess_lexer(content_sample, stripall=True)
                            logging.debug(f"Pygments: Guessed language by content: {new_lexer.name}")
                        except Exception: # guess_lexer can also fail (e.g., pygments.util.ClassNotFound)
                             logging.debug("Pygments: Content guesser failed. Falling back to TextLexer.")
                             new_lexer = TextLexer()
                else: # No extension, try to guess from content
                     content_sample = "\n".join(self.text[:200])[:10000]
                     try:
                        new_lexer = guess_lexer(content_sample, stripall=True)
                        logging.debug(f"Pygments: Guessed language by content (no extension): {new_lexer.name}")
                     except Exception:
                         logging.debug("Pygments: Content guesser failed (no extension). Falling back to TextLexer.")
                         new_lexer = TextLexer()
            else: # No filename (e.g., new buffer), try to guess from content
                content_sample = "\n".join(self.text[:200])[:10000]
                try:
                    new_lexer = guess_lexer(content_sample, stripall=True)
                    logging.debug(f"Pygments: Guessed language by content (no file name): {new_lexer.name}")
                except Exception:
                     logging.debug("Pygments: Content guesser failed (no file name). Falling back to TextLexer.")
                     new_lexer = TextLexer()

        except Exception as e: # Catch any other unexpected error during lexer detection logic
            logging.error(f"Failed to detect language for '{self.filename or "buffer"}': {e}", exc_info=True)
            new_lexer = TextLexer()  # Fallback to a plain text lexer in case of any error

        # Set the new lexer (it will be TextLexer if all attempts above failed)
        self._lexer = new_lexer
        # Get the id of the new (or potentially unchanged) lexer object.
        new_lexer_id = id(self._lexer)

        # If the lexer object instance has actually changed (i.e., their memory IDs are different),
        # we need to clear the LRU cache associated with the _get_tokenized_line method.
        # This is because its cached results are specific to the lexer_id that was active 
        # when those results were cached.
        if old_lexer_id != new_lexer_id:
             logging.info(f"Pygments: Lexer changed from (id: {old_lexer_id}) to '{self._lexer.name}' (id: {new_lexer_id}). Clearing _get_tokenized_line LRU cache.")
             # Ensure the _get_tokenized_line method and its cache_clear() attribute/method exist,
             # which they should if it's correctly decorated with @functools.lru_cache.
             if hasattr(self, '_get_tokenized_line') and hasattr(self._get_tokenized_line, 'cache_clear'):
                 self._get_tokenized_line.cache_clear()
             else:
                 # This case should ideally not be reached if the editor's setup is correct.
                 # It indicates a potential issue with the _get_tokenized_line method or its decorator.
                 logging.warning("_get_tokenized_line method or its cache_clear attribute not found. LRU cache for tokenization might not be cleared, potentially leading to incorrect highlighting if the lexer type truly changed.")
        else:
            logging.debug(f"Pygments: Lexer remained '{self._lexer.name}' (id: {new_lexer_id}). LRU cache for _get_tokenized_line not cleared.")
        
        # Note regarding the old manual cache `self._token_cache`:
        # If _get_tokenized_line (decorated with @lru_cache) is now the sole mechanism
        # for caching Pygments tokenization results, then the instance variable `self._token_cache`
        # (which was a dict or OrderedDict) is no longer needed for this specific purpose.
        # It could be removed from SwayEditor.__init__ and any direct manipulations
        # in methods like apply_syntax_highlighting_with_pygments, provided it's not used
        # for other caching purposes within the editor.


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
        self.text[row] = line[:col] + line[col + 1 :]
        self.modified = True
        logging.debug("delete_char_internal: removed %r at (%s,%s)", removed, row, col)
        return removed


    def handle_resize(self):
        """
        Обрабатывает событие изменения размера окна.
        Вызывается при изменении размера окна терминала.
        """
        logging.debug("handle_resize called")
        try:
            self.height, self.width = self.stdscr.getmaxyx()
            self._set_status_message(f"Resized to {self.width}x{self.height}")
            self._draw_buffer()
        except Exception as e:
            logging.error(f"Error in handle_resize: {e}", exc_info=True)
            self._set_status_message("Resize error (see log)")


    def _draw_buffer(self) -> None:
        """
        Paint visible part of self.text to `stdscr`.
        Handles both vertical (scroll_top) and horizontal (scroll_left) scrolling.
        """
        logging.debug(
            "Drawing buffer: cur=(%s,%s) scroll=(%s,%s)",
            self.cursor_x, self.cursor_y, self.scroll_left, self.scroll_top
        )
        try:
            # ── draw lines ──────────────────────────────────────────────
            for scr_y, line in enumerate(
                self.text[self.scroll_top : self.scroll_top + self.height]
            ):
                # Cut line by horizontal scroll, then clip to window width ‑ 1
                visible = line[self.logical_offset_by_width(line, self.scroll_left) :]
                # soft‑clip by width
                out, w_acc = "", 0
                for ch in visible:
                    w = wcwidth(ch)
                    if w < 0:       # skip control char
                        continue
                    if w_acc + w > self.width - 1:
                        break
                    out += ch
                    w_acc += w

                self.stdscr.move(scr_y, 0)
                self.stdscr.clrtoeol()
                self.stdscr.addstr(scr_y, 0, out)

            # ── place cursor ───────────────────────────────────────────
            scr_y = self.cursor_y - self.scroll_top
            # width up to logical cursor, then minus horizontal scroll
            scr_x = self.get_display_width(
                self.text[self.cursor_y][: self.cursor_x]
            ) - self.scroll_left

            # clamp to window
            scr_y = max(0, min(scr_y, self.height - 1))
            scr_x = max(0, min(scr_x, self.width - 1))
            self.stdscr.move(scr_y, scr_x)

        except Exception as exc:
            logging.error("Error in _draw_buffer: %s", exc, exc_info=True)
            self._set_status_message("Draw error (see log)")


    def logical_offset_by_width(self, line: str, cells: int) -> int:
        """
        Return index in *line* such that rendered width == *cells*.
        Используется, когда нужно получить логический индекс
        символа, с которого виден экран после горизонтального скролла.
        """
        acc = 0
        for i, ch in enumerate(line):
            w = wcwidth(ch)
            if w < 0:
                continue
            if acc + w > cells:
                return i
            acc += w
        return len(line)



    # ===================== Курсор: страничные и домашние клавиши ======================

    # `array up`
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
            
        self._clamp_scroll() # Always call to ensure scroll is correct
        
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
                     changed = True # Status changed, so redraw needed

        return changed
        
    # `array down`
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

    # `array left (<-) `
    def handle_left(self) -> bool:
        """
        Move cursor one position to the left.
        Returns True if cursor or scroll position changed, False otherwise.
        """
        old_y, old_x = self.cursor_y, self.cursor_x
        old_scroll_left = self.scroll_left
        old_scroll_top = self.scroll_top # For line jumps
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
            logging.debug("cursor ← (%d,%d), scroll: (%d,%d)", self.cursor_y, self.cursor_x, self.scroll_top, self.scroll_left)
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

    # `array right (->)`
    def handle_right(self) -> bool:
        """
        Move cursor one position to the right.
        Returns True if cursor or scroll position changed, False otherwise.
        """
        old_y, old_x = self.cursor_y, self.cursor_x
        old_scroll_left = self.scroll_left
        old_scroll_top = self.scroll_top # For line jumps
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
                logging.debug("cursor → (%d,%d), scroll: (%d,%d)", self.cursor_y, self.cursor_x, self.scroll_top, self.scroll_left)
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
        except IndexError: # Should not happen if cursor_y is always valid
            logging.exception("Error in handle_right (IndexError)")
            self._set_status_message("Cursor error (see log)")
            return True # Assume redraw needed
        except Exception:
            logging.exception("Error in handle_right")
            self._set_status_message("Cursor error (see log)")
            return True # Assume redraw needed

    # ── Курсор: прокрутка и ограничение ────────────────────────────────
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
        if width < 0:          # Means string contains non‑printables
            width = 0
            for ch in text:
                w = wcwidth(ch)
                width += max(w, 0)   # non‑printables add 0
        return width

    def _ensure_cursor_in_bounds(self) -> None:
        """
        Clamp `cursor_x` / `cursor_y` so they always reference a valid position
        inside `self.text`.

        • Если буфер пуст → создаётся пустая строка `[""]`, и курсор ставится в (0, 0).  
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


    # ────────── Курсор: Home/End , pUp/pDown ──────────
    # key HOME
    def handle_home(self) -> bool:
        """
        Moves the cursor to the beginning of the current line.
        Implements "smart home" behavior:
        - First press: moves to the first non-whitespace character (or column 0 if no indent).
        - Second press (if already at indent): moves to absolute column 0.
        Returns True if the cursor or scroll position changed, False otherwise.
        """
        original_cursor_x = self.cursor_x
        original_scroll_left = self.scroll_left # To check if _clamp_scroll changes it
        changed_state = False

        with self._state_lock:
            # Ensure cursor_y is valid, though it shouldn't change here
            if self.cursor_y >= len(self.text):
                logging.warning(f"handle_home: cursor_y {self.cursor_y} out of bounds.")
                return False # No change possible

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
                changed_state = True # Status message change also implies redraw

        return changed_state

    # key END
    def handle_end(self) -> bool:
        """
        Moves the cursor to the end of the current line.
        Returns True if the cursor or scroll position changed, False otherwise.
        """
        original_cursor_x = self.cursor_x
        original_scroll_left = self.scroll_left # To check if _clamp_scroll changes it
        changed_state = False

        with self._state_lock:
            if self.cursor_y >= len(self.text):
                logging.warning(f"handle_end: cursor_y {self.cursor_y} out of bounds.")
                return False # No change possible

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

    # key page-up
    def handle_page_up(self) -> bool:
        """
        Moves the cursor and view up by approximately one screen height of text.
        The cursor attempts to maintain its horizontal column, clamped by line length.
        Returns True if the cursor or scroll position changed, False otherwise.
        """
        # Store initial state for comparison
        original_cursor_pos = (self.cursor_y, self.cursor_x)
        original_scroll_pos = (self.scroll_top, self.scroll_left) # scroll_left might change via _clamp_scroll
        original_status = self.status_message
        changed_state = False

        with self._state_lock:
            # Get current window dimensions to determine text area height
            # self.visible_lines should already store this (height - 2)
            # Ensure self.visible_lines is up-to-date if window size can change dynamically.
            if self.visible_lines <= 0: # Should not happen in a usable editor state
                 logging.warning("handle_page_up: visible_lines is not positive, cannot page.")
                 return False

            page_height = self.visible_lines # Number of text lines visible on screen

            # Calculate the new scroll_top position
            # We want to move scroll_top up by page_height, but not less than 0.
            new_scroll_top = max(0, self.scroll_top - page_height)
            
            # Calculate how many lines the view actually scrolled
            # This can be less than page_height if we hit the top of the file.
            lines_scrolled_view = self.scroll_top - new_scroll_top

            # Move the cursor position by the same number of lines the view scrolled.
            # The cursor_y should not go below 0.
            new_cursor_y = max(0, self.cursor_y - lines_scrolled_view)
            
            # If scroll_top changed, or if cursor_y changed due to view scroll, update them.
            # This logic attempts to move the cursor relative to its position on the screen.
            # A simpler PageUp often just moves scroll_top and places cursor on the new top visible line.
            # Let's stick to the "move cursor by one page, then adjust scroll" mental model.

            # Alternative: Move cursor by a page, then adjust scroll.
            new_cursor_y_candidate = max(0, self.cursor_y - page_height)

            # Set new cursor_y and scroll_top
            # If new_cursor_y_candidate is different, it means a significant jump
            if new_cursor_y_candidate != self.cursor_y or new_scroll_top != self.scroll_top:
                self.cursor_y = new_cursor_y_candidate
                # self.scroll_top = new_scroll_top # _clamp_scroll will handle this primarily
            
            # Ensure cursor_x is valid for the new line
            # self.cursor_x (desired column) is maintained from before the jump.
            if self.cursor_y < len(self.text): # Check if cursor_y is valid index
                 self.cursor_x = min(self.cursor_x, len(self.text[self.cursor_y]))
            else: # Should not happen if cursor_y is clamped to max(0, ...)
                 self.cursor_x = 0
            
            # _clamp_scroll will ensure cursor_y is visible and adjust scroll_top and scroll_left
            self._clamp_scroll()

        # Determine if any relevant state actually changed.
        if ( (self.cursor_y, self.cursor_x) != original_cursor_pos or
             (self.scroll_top, self.scroll_left) != original_scroll_pos ):
            changed_state = True
            logging.debug(
                f"handle_page_up: New cursor ({self.cursor_y},{self.cursor_x}), "
                f"scroll ({self.scroll_top},{self.scroll_left}). Changed: {changed_state}"
            )
        else:
            logging.debug("handle_page_up: No change in cursor or scroll state.")

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
                    # If status changed back to Ready, it's still a change from original_status if original_status wasn't Ready
                    if self.status_message != original_status and not changed_state: # Avoid double-setting changed_state
                        changed_state = True 
            
        return changed_state
    
    # key page-down
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
            if max_y_idx < 0 : max_y_idx = 0 # Handle empty text [""] case

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
        if ( (self.cursor_y, self.cursor_x) != original_cursor_pos or
             (self.scroll_top, self.scroll_left) != original_scroll_pos ):
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

    # ── Курсор: Backspace и Delete ────────────────────────────────────────
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
        with self._state_lock:
            # Store initial state for comparison to determine if a redraw is needed
            original_text_tuple = tuple(self.text) # For checking if text content actually changed
            original_cursor_pos = (self.cursor_y, self.cursor_x)
            original_scroll_pos = (self.scroll_top, self.scroll_left)
            original_selection_state = (self.is_selecting, self.selection_start, self.selection_end)
            original_modified_flag = self.modified
            original_status_message = self.status_message
            
            action_made_change_to_content = False # Tracks if text/buffer structure changed

            if self.is_selecting:
                normalized_range = self._get_normalized_selection_range()
                if not normalized_range:
                    # This case implies an inconsistent selection state.
                    logging.warning("handle_backspace: is_selecting=True, but no valid normalized range.")
                    self.is_selecting = False # Attempt to recover by clearing selection
                    self.selection_start = None
                    self.selection_end = None
                    self._set_status_message("Selection error cleared")
                    return True # Status changed, redraw

                norm_start_coords, norm_end_coords = normalized_range
                
                # delete_selected_text_internal sets self.modified and cursor position
                deleted_segments = self.delete_selected_text_internal(
                    norm_start_coords[0], norm_start_coords[1],
                    norm_end_coords[0], norm_end_coords[1]
                )
                
                # Check if anything was actually deleted or if selection range was non-empty
                if deleted_segments or (norm_start_coords != norm_end_coords):
                    self.action_history.append({
                        "type": "delete_selection",
                        "text": deleted_segments,
                        "start": norm_start_coords,
                        "end": norm_end_coords,
                    })
                    self.undone_actions.clear() # A new atomic action
                    action_made_change_to_content = True
                    self._set_status_message("Selection deleted")
                else: # Selection was empty (e.g. just a cursor point)
                    self._set_status_message("Empty selection, nothing deleted")

                self.is_selecting = False # Always clear selection after processing
                self.selection_start = None
                self.selection_end = None
                # self.modified is handled by delete_selected_text_internal

            elif self.cursor_x > 0: # Cursor is not at the beginning of the line
                y, x = self.cursor_y, self.cursor_x
                
                if y >= len(self.text): # Should not happen with a valid cursor
                    logging.error(f"handle_backspace: cursor_y {y} out of bounds for text length {len(self.text)}")
                    return False # No change, inconsistent state

                current_line_content = self.text[y]
                deleted_char = current_line_content[x - 1]
                self.text[y] = current_line_content[:x - 1] + current_line_content[x:]
                self.cursor_x -= 1
                self.modified = True
                action_made_change_to_content = True
                
                self.action_history.append({
                    "type": "delete_char", 
                    "text": deleted_char,
                    "position": (y, self.cursor_x), # Position *after* deletion (where char was)
                })
                self.undone_actions.clear()
                self._set_status_message("Character deleted")
                logging.debug(f"handle_backspace: Character '{deleted_char}' at original ({y},{x}) deleted.")

            elif self.cursor_y > 0: # Cursor is at column 0, but not on the first line
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

                self.action_history.append({
                    "type": "delete_newline", 
                    "text": text_moved_up,    
                    "position": (self.cursor_y, self.cursor_x), # Cursor position after merge
                })
                self.undone_actions.clear()
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
                self._ensure_cursor_in_bounds() # Ensure cursor is valid after modification
                self._clamp_scroll()            # Adjust scroll if cursor moved out of view

            # Determine if a redraw is needed
            if (action_made_change_to_content or
                self.cursor_y != original_cursor_pos[0] or self.cursor_x != original_cursor_pos[1] or
                self.scroll_top != original_scroll_pos[0] or self.scroll_left != original_scroll_pos[1] or
                self.is_selecting != original_selection_state[0] or # Selection state changed
                self.modified != original_modified_flag or # Modified flag changed
                self.status_message != original_status_message): # Status message changed
                return True
            
            return False # No perceivable change that warrants a redraw


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
            original_text_tuple = tuple(self.text)
            original_cursor_pos = (self.cursor_y, self.cursor_x)
            original_scroll_pos = (self.scroll_top, self.scroll_left)
            original_selection_state = (self.is_selecting, self.selection_start, self.selection_end)
            original_modified_flag = self.modified
            original_status_message = self.status_message
            
            action_made_change_to_content = False # Tracks if text/buffer structure changed

            if self.is_selecting:
                normalized_range = self._get_normalized_selection_range()
                if not normalized_range:
                    logging.warning("handle_delete: is_selecting=True, but no valid normalized range.")
                    self.is_selecting = False # Attempt to recover
                    self.selection_start = None
                    self.selection_end = None
                    self._set_status_message("Selection error cleared")
                    return True # Status changed

                norm_start_coords, norm_end_coords = normalized_range
                
                # delete_selected_text_internal sets self.modified and cursor position
                deleted_segments = self.delete_selected_text_internal(
                    norm_start_coords[0], norm_start_coords[1],
                    norm_end_coords[0], norm_end_coords[1]
                )
                
                if deleted_segments or (norm_start_coords != norm_end_coords): # Check if deletion occurred
                    self.action_history.append({
                        "type": "delete_selection",
                        "text": deleted_segments,
                        "start": norm_start_coords,
                        "end": norm_end_coords
                    })
                    self.undone_actions.clear() # New atomic action
                    action_made_change_to_content = True
                    self._set_status_message("Selection deleted")
                else:
                    self._set_status_message("Empty selection, nothing deleted")
                
                self.is_selecting = False # Always clear selection after processing
                self.selection_start = None
                self.selection_end = None
                # self.modified is handled by delete_selected_text_internal
            
            else: # No selection, handle single character delete or newline merge
                y, x = self.cursor_y, self.cursor_x
                
                if y >= len(self.text): # Cursor out of bounds
                    logging.error(f"handle_delete: cursor_y {y} out of bounds for text length {len(self.text)}")
                    return False # No change, inconsistent state

                current_line_len = len(self.text[y])

                if x < current_line_len:
                    # Delete character under cursor (to the right)
                    deleted_char = self.text[y][x]
                    self.text[y] = self.text[y][:x] + self.text[y][x + 1:]
                    # Cursor position (x) does not change when deleting char at cursor
                    self.modified = True
                    action_made_change_to_content = True
                    
                    self.action_history.append({
                        "type": "delete_char",
                        "text": deleted_char,
                        "position": (y, x) # Position of the deleted character
                    })
                    self.undone_actions.clear()
                    self._set_status_message("Character deleted")
                    logging.debug(f"handle_delete: Character '{deleted_char}' at ({y},{x}) deleted.")

                elif y < len(self.text) - 1: # Cursor is at the end of a line, but not the last line
                    # Merge with the next line (delete newline character)
                    next_line_content = self.text[y + 1]
                    
                    # Cursor position (y,x) remains the same logically after merge
                    # Position for history is where the newline was (end of current line y)
                    pos_for_history = (y, current_line_len) 

                    self.text[y] += self.text.pop(y + 1)
                    self.modified = True
                    action_made_change_to_content = True
                    
                    self.action_history.append({
                        "type": "delete_newline",
                        "text": next_line_content, # Content of the line that was merged up
                        "position": pos_for_history 
                    })
                    self.undone_actions.clear()
                    self._set_status_message("Newline deleted (lines merged)")
                    logging.debug(f"handle_delete: Line {y+1} merged into line {y}.")
                else:
                    # Cursor is at the end of the last line of the file
                    logging.debug("handle_delete: At end of file – no action.")
                    self._set_status_message("End of file")
                    return self.status_message != original_status_message # Only redraw if status changed

            # Finalization steps if any content change occurred
            if action_made_change_to_content:
                self._ensure_cursor_in_bounds()
                self._clamp_scroll()

            # Determine if a redraw is needed
            if (action_made_change_to_content or
                self.cursor_y != original_cursor_pos[0] or self.cursor_x != original_cursor_pos[1] or
                self.scroll_top != original_scroll_pos[0] or self.scroll_left != original_scroll_pos[1] or
                self.is_selecting != original_selection_state[0] or # Selection state changed (e.g., cleared)
                (self.selection_start != original_selection_state[1] and original_selection_state[1] is not None) or # Check actual coords
                (self.selection_end != original_selection_state[2] and original_selection_state[2] is not None) or
                self.modified != original_modified_flag or
                self.status_message != original_status_message):
                return True
            
            return False # No perceivable change that warrants a redraw


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
            return [] # Return empty if rows are out of bounds

        # Ensure start_x and end_x are within the bounds of their respective lines
        if not (0 <= start_x <= len(self.text[start_y]) and 0 <= end_x <= len(self.text[end_y])):
            logging.error(
                f"delete_selected_text_internal: Invalid column indices for deletion: "
                f"start_x={start_x} (line len {len(self.text[start_y])}), "
                f"end_x={end_x} (line len {len(self.text[end_y])})"
            )
            # Decide on recovery: clamp, return error, or rely on caller normalization.
            # For an internal method, usually expect valid inputs or raise error.
            # Given it's low-level, returning empty on bad input is safer than crashing.
            return []


        deleted_segments = []

        if start_y == end_y:
            # Deletion within a single line
            line_content = self.text[start_y]
            # Ensure start_x and end_x are ordered for slicing, though normalization should handle this
            actual_start_x = min(start_x, end_x)
            actual_end_x = max(start_x, end_x)
            
            # Ensure slice indices are valid for the line
            actual_start_x = min(actual_start_x, len(line_content))
            actual_end_x = min(actual_end_x, len(line_content))

            if actual_start_x < actual_end_x: # Only if there's something to delete
                deleted_segments.append(line_content[actual_start_x:actual_end_x])
                self.text[start_y] = line_content[:actual_start_x] + line_content[actual_end_x:]
            else:
                logging.debug("delete_selected_text_internal: Single line selection, but start_x >= end_x. No characters deleted.")
        else:
            # Multi-line deletion

            # Part from the first line (start_y)
            line_start_content = self.text[start_y]
            # Ensure start_x is within bounds
            actual_start_x_on_first_line = min(start_x, len(line_start_content))
            deleted_segments.append(line_start_content[actual_start_x_on_first_line:])
            
            remaining_prefix_on_start_line = line_start_content[:actual_start_x_on_first_line]

            # Full lines between start_y and end_y (exclusive of start_y, exclusive of end_y)
            if end_y > start_y + 1:
                # Add all lines from start_y + 1 up to (but not including) end_y
                deleted_segments.extend(self.text[start_y + 1 : end_y]) 
            
            # Part from the last line (end_y)
            line_end_content = self.text[end_y]
            # Ensure end_x is within bounds
            actual_end_x_on_last_line = min(end_x, len(line_end_content))
            deleted_segments.append(line_end_content[:actual_end_x_on_last_line])
            
            remaining_suffix_on_end_line = line_end_content[actual_end_x_on_last_line:]

            # Update the text buffer:
            # The first line of the selection becomes the prefix + suffix
            self.text[start_y] = remaining_prefix_on_start_line + remaining_suffix_on_end_line
            
            # Delete the lines that were fully consumed or merged
            # These are lines from (start_y + 1) up to and including end_y
            # Example: delete from (0,5) to (2,5)
            #   line 0: prefix + suffix_of_line_2
            #   delete lines: 1 (original index), 2 (original index)
            #   In terms of current list indices after line 0 is modified:
            #   delete self.text[start_y + 1] through self.text[end_y - (start_y + 1) + (start_y + 1)]
            #   which is self.text[start_y + 1 : end_y + 1] (relative to original indices)
            del self.text[start_y + 1 : end_y + 1] # Python slice end is exclusive
        
        # Set cursor to the beginning of the deleted (normalized) selection
        self.cursor_y = start_y
        self.cursor_x = start_x # Logical start_x of the selection on the new combined line
        
        self.modified = True # Mark buffer as modified
        
        if not deleted_segments and start_y == end_y and start_x == end_x:
             logging.debug(f"delete_selected_text_internal: No actual characters deleted (empty selection at a point). Cursor at ({self.cursor_y},{self.cursor_x}).")
        else:
             logging.debug(
                f"delete_selected_text_internal: Deletion complete. Cursor at ({self.cursor_y},{self.cursor_x}). "
                f"Deleted segments count: {len(deleted_segments)}. First segment preview: '{deleted_segments[0][:50] if deleted_segments else ""}'"
             )
        return deleted_segments

    # ── Курсор: smart tab ─────────────────────────────────────────────
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
        if self.cursor_y > 0: # Check if there's a previous line
            prev_line_idx = self.cursor_y - 1
            # Ensure prev_line_idx is valid (should be, if self.cursor_y > 0)
            if 0 <= prev_line_idx < len(self.text):
                prev_line_content = self.text[prev_line_idx]
                # Use regex to find leading whitespace
                match_result = re.match(r"^(\s*)", prev_line_content)
                if match_result:
                    indentation_to_copy = match_result.group(1)
            else: # Should not happen if self.cursor_y > 0
                logging.warning(f"handle_smart_tab: Invalid prev_line_idx {prev_line_idx} when cursor_y is {self.cursor_y}")
        
        text_to_insert = indentation_to_copy # Use copied indentation by default
        
        if not indentation_to_copy: # If no indentation to copy (e.g., first line, or prev line had no indent)
            tab_size = self.config.get("editor", {}).get("tab_size", 4)
            use_spaces = self.config.get("editor", {}).get("use_spaces", True)
            text_to_insert = " " * tab_size if use_spaces else "\t"
        
        if not text_to_insert: # If, for some reason, text_to_insert is still empty (e.g. prev line was empty)
                               # then insert_text("") will return False, which is correct.
            logging.debug("handle_smart_tab: No text to insert for smart indent (e.g. previous line empty and no default tab configured).")
            return False # No change will be made

        # insert_text handles history, self.modified, and returns True if text was inserted.
        return self.insert_text(text_to_insert)
    
    # для key TAB вспомогательный метод
    def handle_tab(self) -> bool:
        """
        Inserts standard tab characters (spaces or a tab char) at the current cursor position.
        Deletes selection if active before inserting.
        Returns True if text was inserted (always true if not an empty tab string), False otherwise.
        """
        tab_size = self.config.get("editor", {}).get("tab_size", 4)
        use_spaces = self.config.get("editor", {}).get("use_spaces", True)
        
        text_to_insert_val = " " * tab_size if use_spaces else "\t"
        
        if not text_to_insert_val: # Should not happen with default config
            logging.warning("handle_tab: Tab string is empty, nothing to insert.")
            return False

        # self.insert_text handles active selection, history, and returns True if changes were made.
        return self.insert_text(text_to_insert_val)

    def handle_char_input(self, key):
        """
        Handles all character‐input, separating control keys (Ctrl+…) from printable text.
        """
        try:
            #  Control keys (ASCII < 32)
            if isinstance(key, int) and key < 32:
                self.handle_control_key(key)
                return

            # Printable characters
            if isinstance(key, int):
                char = chr(key)
                self.insert_text(char)
            elif isinstance(key, str) and len(key) == 1:
                self.insert_text(key)
            else:
                logging.warning(f"handle_char_input received unexpected type: {type(key)}")

        except Exception as e:
            logging.exception(f"Error handling character input: {str(e)}")
            self._set_status_message(f"Input error: {str(e)[:80]}...")


    def handle_control_key(self, key_code):
        """
        Map control‐key codes to editor actions.
        """
        if key_code == 19:   # Ctrl+S
            self.save_file()
        elif key_code == 6:  # Ctrl+F
            self.find_prompt()
        elif key_code == 15: # Ctrl+O
            self.open_file()
        elif key_code == 3:  # Ctrl+Q
            self.exit_editor()
        else:
            logging.debug(f"Unhandled control key: {key_code}")


    def handle_enter(self) -> bool:
        """
        Handles the Enter key.
        If text is selected, it's deleted by insert_text first. 
        Then a newline character is inserted.
        Manages action history via the insert_text method.
        Returns True as these actions (deletion of selection or insertion of newline) 
        always change the text content or selection state.
        """
        changed_state = False
        with self._state_lock:
            # self.insert_text will handle:
            # 1. Deleting selection if active (and recording "delete_selection" action).
            # 2. Inserting the newline character (and recording "insert" action).
            # 3. Returning True if any modification occurred.
            if self.insert_text("\n"):
                changed_state = True
            
        if changed_state:
            logging.debug("Handled Enter key: Text or selection modified.")
        else:
            # This case should ideally not be reached if insert_text correctly inserts a newline
            # or if a selection was present (which would be cleared).
            logging.debug("Handled Enter key: No effective change (unexpected for Enter).")
            
        return changed_state # Should always be True due to newline insertion
    

    def parse_key(self, key_str: str) -> int:
        """
        Преобразует строку-описание горячей клавиши в curses-код.
        """
        if isinstance(key_str, int):
            return key_str

        key_str = key_str.strip().lower()
        if not key_str:
            raise ValueError("Пустая строка для горячей клавиши")

        # Сначала проверяем на точное совпадение в named, включая shift-комбинации
        # (они должны быть в нижнем регистре в named)
        named = {
            "del": getattr(curses, 'KEY_DC', 330),
            "delete": getattr(curses, 'KEY_DC', 330),
            "backspace": getattr(curses, 'KEY_BACKSPACE', 127), # Обычно 263 в keypad(True)
            "tab": ord("\t"), # curses.KEY_TAB (обычно 9)
            "enter": ord("\n"), # curses.KEY_ENTER (обычно 10 или 13)
            "space": ord(" "),
            "esc": 27, # curses.KEY_EXIT или 27
            "escape": 27,
            "up": getattr(curses, 'KEY_UP', 259),
            "down": getattr(curses, 'KEY_DOWN', 258),
            "left": getattr(curses, 'KEY_LEFT', 260),
            "right": getattr(curses, 'KEY_RIGHT', 261),
            "home": getattr(curses, 'KEY_HOME', 262),
            "end": getattr(curses, 'KEY_END', 360), # Может быть KEY_LL или специфичный для терминала
            "pageup": getattr(curses, 'KEY_PPAGE', 339),
            "pgup": getattr(curses, 'KEY_PPAGE', 339),
            "pagedown": getattr(curses, 'KEY_NPAGE', 338),
            "pgdn": getattr(curses, 'KEY_NPAGE', 338),
            "insert": getattr(curses, 'KEY_IC', 331),
            # Явные Shift-комбинации для спец. клавиш, которые curses может возвращать
            "sright": getattr(curses, 'KEY_SRIGHT', 402), # Shift+Right
            "sleft": getattr(curses, 'KEY_SLEFT', 393),   # Shift+Left
            "shome": getattr(curses, 'KEY_SHOME', 391),   # Shift+Home
            "send": getattr(curses, 'KEY_SEND', 386),    # Shift+End
            "spgup": getattr(curses, 'KEY_SPREVIOUS', 337), # Shift+PgUp (KEY_SR в некоторых системах)
            "spgdn": getattr(curses, 'KEY_SNEXT', 336),     # Shift+PgDn (KEY_SF в некоторых системах)
            # Добавим ваши:
            "shift+pgup": getattr(curses, 'KEY_SPREVIOUS', 337),
            "shift+pgdn": getattr(curses, 'KEY_SNEXT', 336),
        }
        named.update({f"f{i}": getattr(curses, f"KEY_F{i}", 256 + i) for i in range(1, 13)}) # KEY_F(i) обычно > 256

        if key_str in named:
            return named[key_str]

        parts = key_str.split('+')
        modifiers = []
        base_key_str = ""

        for part in parts:
            if part in ("ctrl", "alt", "shift"):
                modifiers.append(part)
            else:
                if base_key_str: # Уже нашли одну "базовую" клавишу
                    raise ValueError(f"Несколько базовых клавиш в хоткее: {key_str}")
                base_key_str = part
        
        if not base_key_str:
            raise ValueError(f"Не найдена базовая клавиша в хоткее: {key_str}")

        # Обработка базовой клавиши
        base_code: int
        if base_key_str in named: # Если "z" в "shift+z" это, например, "del"
            base_code = named[base_key_str]
        elif len(base_key_str) == 1:
            base_code = ord(base_key_str) # "z" -> ord('z')
        else: # Если базовая клавиша не одиночный символ и не в named (напр. "f1")
            raise ValueError(f"Неизвестная базовая клавиша: {base_key_str} в {key_str}")

        # Применение модификаторов
        # curses не имеет универсальных кодов для Alt+<буква> или Shift+<буква> (кроме заглавных).
        # Ctrl+<буква> обычно это ord(буква) & 0x1F или ord(буква) - ord('a') + 1.

        is_ctrl = "ctrl" in modifiers
        is_alt = "alt" in modifiers # Alt часто реализуется через Esc-префикс, get_wch() может вернуть строку
        is_shift = "shift" in modifiers

        if is_alt:
            # Alt часто не генерирует один int код, а меняет байтовую последовательность.
            # Если get_wch() возвращает строку для Alt-комбинаций, этот парсер не сможет
            # их перевести в int, если только нет спец. кодов от curses (редко).
            # Можно зарезервировать диапазон для Alt, как вы делали (base_key | 0x200)
            # но это будет работать, только если ваш input loop генерирует такие int'ы.
            # Для Alt+X (где X это буква), эмуляторы терминала часто шлют Esc + X.
            # parse_key здесь должен вернуть то, что ОЖИДАЕТ action_map.
            # Если action_map ожидает кастомные коды для Alt, то здесь их надо генерировать.
            # Например, если base_code это ord('x'), то alt+x -> ord('x') | 0x200.
            # Однако, если base_code это KEY_LEFT, то alt+left может быть другим.
            # Ваша логика `return base_key | 0x200` была для `alt+...`.
            # Но она стояла ПЕРЕД разделением на части.
            # Если key_str был "alt+a", то base_key парсился из "a".
            # Это можно оставить, но после обработки ctrl/shift.
            # Либо, ваш get_wch() должен возвращать такие коды.
            # Для простоты, если Alt, то это скорее всего не одиночный int от getch().
            # Если вы хотите мапить "alt+x" на что-то, это лучше делать через term_mappings или строки.
            logging.warning(f"Парсинг Alt-комбинаций ('{key_str}') может быть не универсальным и зависит от терминала/get_wch.")
            # Если вы определили кастомные коды для Alt, то применяйте их.
            # base_code |= 0x200 # Пример вашего предыдущего подхода

        if is_ctrl:
            if 'a' <= base_key_str <= 'z':
                char_code = ord(base_key_str)
                # Ctrl+буква (a-z) -> 1-26
                # Ctrl+Shift+буква (A-Z) -> кастомный диапазон, например 257-282
                # (ord(ch) - ord('a') + 1)
                ctrl_val = char_code - ord('a') + 1
                if is_shift:
                    # Пример: Ctrl+Shift+A = 257 (0x101)
                    # Предполагаем, что такие коды не конфликтуют с curses.KEY_*
                    # Это уже было в вашей логике:
                    base_code = ctrl_val | 0x100 # 256 + (1..26)
                else:
                    base_code = ctrl_val
            # elif base_key_str == '[': base_code = 27 # Ctrl+[ -> Esc
            # ... другие специальные Ctrl комбинации ...
            else:
                raise ValueError(f"Ctrl можно применять только к буквам a-z в {key_str} (или нужны явные маппинги)")
        
        elif is_shift: # Shift без Ctrl
            if 'a' <= base_key_str <= 'z':
                # shift+z -> ord('Z')
                base_code = ord(base_key_str.upper())
            # Для Shift + спец.клавиша (например, Shift+Tab) - они должны быть в `named` как "shift+tab"
            # или ваш get_wch() должен возвращать для них спец. код, который вы положите в `named`
            # или напрямую в `action_map`.
            # Эта ветка не должна ловить "shift+pgup", т.к. он уже в named.
            # Если мы здесь, значит это что-то типа "shift+1" или "shift+]"
            # Для них curses обычно не генерирует спец. коды, а возвращает сами символы ('!', '}')
            # Если base_key_str это '1', то ord(base_key_str.upper()) даст ошибку.
            # Значит, `shift+<не_буква>` должен быть явно определен в `named` или `term_mappings`.
            # Если мы дошли сюда с `shift+<не_буква>`, и его нет в `named`, это ошибка.
            elif not ('a' <= base_key_str <= 'z'):
                 # Если это "shift+f1", то "f1" уже должно было быть обработано `named`.
                 # Если это "shift+enter", то "enter" в `named`, но shift+enter - другой код.
                 # Это сложно сделать универсально. Лучше явные маппинги.
                raise ValueError(f"Shift с не-буквенной клавишей '{base_key_str}' должен быть явно определен в 'named_keys' (например, 'shift+tab')")

        # Обработка Alt в конце, если другие модификаторы уже применены
        if is_alt:
             base_code |= 0x200 # Ваш предыдущий подход для кастомных Alt кодов

        return base_code
    

    def get_char_width(self, char):
        """
        Calculates the display width of a character using wcwidth.
        Returns 1 for control characters or characters with ambiguous width (-1).
        Uses unicodedata to check if it's a control character.
        """
        if not isinstance(char, str) or len(char) != 1:
            return 1 # Неожиданный ввод, считаем ширину 1

        # Проверка на управляющие символы (кроме известных типа Tab, Enter)
        if unicodedata.category(char) in ('Cc', 'Cf'): # Cc: Control, Cf: Format
             # Здесь можно добавить исключения для символов, которые хотим отображать (например, '\t')
             if char == '\t':
                 # Ширина табуляции зависит от позиции курсора и tab_size,
                 # но wcwidth('\t') обычно 0 или 1.
                 # Для отрисовки Pygments токенов лучше вернуть wcwidth или 1.
                 # Реальная отрисовка табов происходит в DrawScreen.
                 width = wcwidth(char)
                 return width if width >= 0 else 1
             return 0 # Управляющие символы обычно не отображаются и имеют 0 ширину
        # Проверка на символы нулевой ширины (например, диакритика)
        if unicodedata.combining(char):
             return 0 # Объединяющиеся символы имеют нулевую ширину

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
            pass # Fallback

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

    #=============== Open file ============================
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
                        logging.warning("Open file aborted: User chose to save, but 'save_file' did not clear 'modified' flag.")
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
                return True   # Status changed
            
            if not os.path.exists(actual_filename_to_open):
                self._set_status_message(f"Error: File not found '{os.path.basename(actual_filename_to_open)}'")
                logging.warning(f"Open file failed: file not found at '{actual_filename_to_open}'")
                return True
            
            if os.path.isdir(actual_filename_to_open):
                self._set_status_message(f"Error: '{os.path.basename(actual_filename_to_open)}' is a directory.")
                logging.warning(f"Open file failed: path '{actual_filename_to_open}' is a directory.")
                return True
            
            if not os.access(actual_filename_to_open, os.R_OK):
                self._set_status_message(f"Error: No read permissions for '{os.path.basename(actual_filename_to_open)}'.")
                logging.warning(f"Open file failed: no read permissions for '{actual_filename_to_open}'.")
                return True

            # 4. Detect file encoding and read content
            lines: Optional[List[str]] = None
            final_encoding_used: str = "utf-8" # Default if all else fails
            
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
                        if enc and (enc, err_handling) not in seen_enc_err_pairs: # Ensure enc is not None
                            unique_encodings_to_try.append((enc, err_handling))
                            seen_enc_err_pairs.add((enc,err_handling))
                        elif not enc and ("utf-8", err_handling) not in seen_enc_err_pairs: # If chardet returns None for encoding
                             unique_encodings_to_try.append(("utf-8", err_handling)) # Default to utf-8
                             seen_enc_err_pairs.add(("utf-8",err_handling))

                    for enc_attempt, error_policy in unique_encodings_to_try:
                        try:
                            logging.debug(f"Attempting to read '{actual_filename_to_open}' with encoding '{enc_attempt}' (errors='{error_policy}')")
                            with self.safe_open(actual_filename_to_open, "r", encoding=enc_attempt, errors=error_policy) as f_text:
                                lines = f_text.read().splitlines()
                            final_encoding_used = enc_attempt if enc_attempt else "utf-8"
                            logging.info(f"Successfully read '{actual_filename_to_open}' using encoding '{final_encoding_used}' with errors='{error_policy}'.")
                            break 
                        except (UnicodeDecodeError, OSError, LookupError) as e_read:
                            logging.warning(f"Failed to read '{actual_filename_to_open}' with encoding '{enc_attempt}' (errors='{error_policy}'): {e_read}")
                
                if lines is None:
                    self._set_status_message(f"Error reading '{os.path.basename(actual_filename_to_open)}': Could not decode content.")
                    logging.error(f"All attempts to read and decode '{actual_filename_to_open}' failed.")
                    return True

            except Exception as e_detect_read:
                self._set_status_message(f"Error during file processing for '{os.path.basename(actual_filename_to_open)}': {e_detect_read}")
                logging.exception(f"Failed during encoding detection or initial read for '{actual_filename_to_open}'")
                return True

            self.text = lines if lines is not None else [""] 
            self.filename = actual_filename_to_open
            self.modified = False
            self.encoding = final_encoding_used

            self.set_initial_cursor_position() 
            self.action_history.clear()
            self.undone_actions.clear()
            
            self._set_status_message(
                f"Opened '{os.path.basename(self.filename)}' (enc: {self.encoding}, {len(self.text)} lines)"
            )
            logging.info(
                f"File opened successfully: '{self.filename}', Encoding: {self.encoding}, Lines: {len(self.text)}"
            )

            self._lexer = None 
            self.detect_language() 
            self.update_git_info() 
            
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


#================= SAVE_FILE ================================= 
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
            return True # Status changed by validate_filename

        if os.path.isdir(self.filename):
            self._set_status_message(f"Cannot save: '{os.path.basename(self.filename)}' is a directory.")
            return True # Status changed

        # Check for write permissions on the file itself if it exists,
        # or on its parent directory if it doesn't (though save usually implies it exists or can be created).
        target_path_exists = os.path.exists(self.filename)
        can_write = False
        if target_path_exists:
            if os.access(self.filename, os.W_OK):
                can_write = True
        else: # File doesn't exist yet, check parent directory
            parent_dir = os.path.dirname(self.filename) or '.' # Use current dir if no path part
            if os.access(parent_dir, os.W_OK):
                can_write = True
        
        if not can_write:
            self._set_status_message(f"No write permissions for '{os.path.basename(self.filename)}' or its directory.")
            return True # Status changed
        
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
                self.status_message != original_status or   # "Saved to..." is new
                self.filename != original_filename):        # Should not change here but check
                redraw_is_needed = True
            
            return redraw_is_needed

        except Exception as e_write: # Catch errors specifically from _write_file
            self._set_status_message(f"Error saving file '{os.path.basename(self.filename)}': {str(e_write)[:60]}...")
            logging.error(f"Failed to write file during Save '{self.filename}': {e_write}", exc_info=True)
            # self.modified might remain True if save failed
            return True # Status message changed due to error


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
        
        redraw_is_needed = False # Accumulator for redraw reasons

        # Determine a default name for the prompt
        default_name_for_prompt = self.filename if self.filename and self.filename != "noname" \
            else self.config.get("editor", {}).get("default_new_filename", "new_file.txt")

        # 1. Prompt for the new filename
        status_before_filename_prompt = self.status_message
        new_filename_input = self.prompt(f"Save file as ({default_name_for_prompt}): ")
        if self.status_message != status_before_filename_prompt:
            redraw_is_needed = True # Prompt interaction itself changed the status line

        if not new_filename_input: # User cancelled (Esc or empty Enter)
            if not redraw_is_needed and self.status_message == original_status: # Only set if prompt didn't change status
                self._set_status_message("Save as cancelled")
            return True # Status changed by prompt or by this cancellation message

        # Use provided name or default if input was just whitespace
        new_filename_processed = new_filename_input.strip() or default_name_for_prompt

        # 2. Validate the new filename
        if not self.validate_filename(new_filename_processed):
            # validate_filename already calls _set_status_message with error
            return True # Status was changed by validate_filename

        if os.path.isdir(new_filename_processed):
            self._set_status_message(f"Cannot save: '{os.path.basename(new_filename_processed)}' is a directory.")
            return True # Status changed

        # 3. Handle existing file and permissions
        if os.path.exists(new_filename_processed):
            if not os.access(new_filename_processed, os.W_OK):
                self._set_status_message(f"No write permissions for existing file: '{os.path.basename(new_filename_processed)}'")
                return True # Status changed

            status_before_overwrite_prompt = self.status_message
            overwrite_choice = self.prompt(f"File '{os.path.basename(new_filename_processed)}' already exists. Overwrite? (y/n): ")
            if self.status_message != status_before_overwrite_prompt:
                redraw_is_needed = True
            
            if not overwrite_choice or overwrite_choice.lower() != 'y':
                if not redraw_is_needed and self.status_message == original_status:
                    self._set_status_message("Save as cancelled (file exists, not overwritten).")
                return True # Status changed by prompt or cancellation
        else:
            # File does not exist, check if directory needs to be created
            target_dir = os.path.dirname(new_filename_processed)
            if target_dir and not os.path.exists(target_dir): # If target_dir is empty, it's the current dir
                try:
                    os.makedirs(target_dir, exist_ok=True)
                    logging.info(f"Created missing directory for save as: {target_dir}")
                except Exception as e_mkdir:
                    self._set_status_message(f"Cannot create directory '{target_dir}': {e_mkdir}")
                    logging.error(f"Failed to create directory '{target_dir}': {e_mkdir}")
                    return True # Status changed
            
            # Check write permissions for the target directory (or current if target_dir is empty)
            effective_target_dir = target_dir if target_dir else '.'
            if not os.access(effective_target_dir, os.W_OK):
                self._set_status_message(f"No write permissions for directory: '{effective_target_dir}'")
                return True # Status changed

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
                self.status_message != original_status): # This will always be true due to set_status_message above
                redraw_is_needed = True
            
            return True # Always true because status message is set and state changes.

        except Exception as e_write: # Catch errors from _write_file
            self._set_status_message(f"Error saving file as '{os.path.basename(new_filename_processed)}': {str(e_write)[:60]}...")
            logging.error(f"Failed to write file during Save As '{new_filename_processed}': {e_write}", exc_info=True)
            # Restore original filename and modified status if save_as failed mid-way
            # (e.g., if _write_file partially updated them before failing)
            # This is tricky, _write_file should ideally be atomic or handle its own partial failure state.
            # For now, we assume if _write_file fails, self.filename might not have been updated yet.
            if self.filename == new_filename_processed: # If _write_file updated filename before error
                 self.filename = original_filename # Try to revert
                 self.modified = original_modified_flag # Revert modified status
            return True # Status message changed due to error
        

    # Метод _write_file является низкоуровневой операцией, предназначенной для фактической записи 
    # содержимого в файл и обновления связанного с этим состояния редактора. 
    def _write_file(self, target_filename: str) -> None: # Changed return type to None
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
        logging.debug(f"_write_file: Attempting to write to target: '{target_filename}' with encoding '{self.encoding}'")
        try:
            # Prepare content with OS-specific line endings
            # os.linesep ensures consistency with how the OS expects newlines.
            content_to_write = os.linesep.join(self.text)
            
            # Use safe_open for writing
            with self.safe_open(target_filename, "w", encoding=self.encoding, errors="replace") as f:
                bytes_written = f.write(content_to_write)
                # Optionally, check if bytes_written matches expected to ensure full write, though f.write should raise error if not.
                logging.debug(f"_write_file: Successfully wrote {bytes_written} bytes (from {len(content_to_write)} chars) to '{target_filename}'")

            # Update editor state after successful write
            # Only update filename if it actually changed (relevant for save_as calling this)
            if self.filename != target_filename:
                self.filename = target_filename
                # If filename changes, Git info needs re-evaluation for the new path context
                self._last_git_filename = None # Force git info update for new filename
            
            self.modified = False # File is now saved, so it's not modified relative to disk

            # Re-detect language if filename or content (implicitly, though content is assumed same before save)
            # might have changed characteristics that affect lexer choice (e.g. new extension from save_as)
            self.detect_language() # This will clear lru_cache if lexer changes

            # Update Git information as file state on disk has changed
            self.update_git_info()

            # Asynchronously run linter if the file is a Python file
            # This check is based on the currently detected lexer.
            if self._lexer and self._lexer.name.lower() in ["python", "python3", "py"]:
                logging.debug(f"_write_file: Python file saved, queueing async lint for '{target_filename}'")
                # Pass the content that was just written to ensure linter sees the saved state
                threading.Thread(
                    target=self.run_lint_async, 
                    args=(content_to_write,), # Pass the actual saved content
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
            raise # Re-raise the exception to be caught by save_file or save_file_as


    def revert_changes(self) -> bool:
        """
        Reverts unsaved changes by reloading the content from the last(f"User confirmed. Attempting to revert changes for '{self.filename}' by reloading.")
        
        # The open_file method will handle:
        # - Reading the file from disk.
        # - Resetting self.text, self.cursor_y, self.cursor_x, self.scroll_top, self.scroll_left.
        # - Clearing self.action_history and self.undone_actions.
        # - Setting self.modified to False.
        # - Calling self.detect_language() and self.update_git_info().
        # - Setting its own status message (e.g., "Opened ...").
        # - Returning True if it made significant changes requiring saved version
        of the current file. If the file has never been saved, or does not exist on disk,
        this operation cannot be performed.
        Prompts the user for confirmation before reverting if there are unsaved changes.

        Returns:
            bool: True if the editor's state changed (text content reloaded, status updated,
                  cursor/scroll reset by open_file, or prompt interaction occurred), 
                  False otherwise (e.g., operation was cancelled very early without any
                  status change, or if there were no changes to revert and status remained same).
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
                    self._set_status_message(f"Successfully reverted to saved version of '{os.path.basename(self.filename)}'.")
                    logging.info(f"Changes for '{self.filename}' reverted successfully.")
                else:
                    self._set_status_message(f"Reverted '{os.path.basename(self.filename)}', but file still marked modified.")
                    logging.warning(f"Reverted '{self.filename}', but it's still marked as modified post-open.")
                return True 
            else:
                self.modified = original_modified_flag_for_comparison 
                logging.warning(f"Revert: self.open_file call for '{self.filename}' returned False. Status: {self.status_message}")
                return self.status_message != original_status or redraw_is_needed_due_to_interaction

        except Exception as e:
            self._set_status_message(f"Error during revert operation for '{os.path.basename(self.filename)}': {str(e)[:70]}...")
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
        logging.debug(f"toggle_auto_save called. Current auto_save_enabled: {getattr(self, '_auto_save_enabled', False)}")
        original_status = self.status_message

        # Ensure attributes exist (usually set in __init__)
        if not hasattr(self, "_auto_save_enabled"):
            self._auto_save_enabled = False
        if not hasattr(self, "_auto_save_thread"): # Thread object for the auto-save task
            self._auto_save_thread = None
        if not hasattr(self, "_auto_save_stop_event"): # Event to signal thread to stop
            self._auto_save_stop_event = threading.Event()

        # Get the auto-save interval from config, defaulting if not found or invalid
        try:
            # Ensure interval is a positive number, representing minutes
            interval_minutes = float(self.config.get("settings", {}).get("auto_save_interval", 1.0)) # Default 1 min
            if interval_minutes <= 0:
                logging.warning(f"Invalid auto_save_interval ({interval_minutes} min) in config, defaulting to 1 min.")
                interval_minutes = 1.0 
        except (ValueError, TypeError):
            logging.warning("Could not parse auto_save_interval from config, defaulting to 1 min.")
            interval_minutes = 1.0
        
        self._auto_save_interval = interval_minutes # Store the current interval in minutes

        # Toggle the auto-save state
        self._auto_save_enabled = not self._auto_save_enabled

        if self._auto_save_enabled:
            # Auto-save is being enabled
            self._auto_save_stop_event.clear() # Clear the stop signal for the new thread

            # Start the auto-save thread if it's not already running or if it died
            if self._auto_save_thread is None or not self._auto_save_thread.is_alive():
                
                def auto_save_task_runner():
                    """The actual task performed by the auto-save thread."""
                    logging.info(f"Auto-save thread started. Interval: {self._auto_save_interval} min.")
                    last_saved_text_hash = None # Store hash of last saved content to detect changes

                    while not self._auto_save_stop_event.is_set(): # Loop until stop event is set
                        try:
                            # Wait for the specified interval or until stop event is set
                            # Convert interval from minutes to seconds for time.sleep
                            sleep_duration_seconds = max(1, int(self._auto_save_interval * 60))
                            
                            # Wait in smaller chunks to be more responsive to stop_event
                            interrupted = self._auto_save_stop_event.wait(timeout=sleep_duration_seconds)
                            if interrupted: # Stop event was set
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
                                temp_filename = self.filename # Store before releasing lock for write
                                temp_encoding = self.encoding
                                temp_text_to_save = current_text_content
                                temp_modified_flag_before_save = self.modified

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
                                    if self.filename == temp_filename and hash(os.linesep.join(self.text)) == current_text_hash:
                                        self.modified = False # Mark as no longer modified
                                        last_saved_text_hash = current_text_hash # Update hash of saved content
                                        self._set_status_message(f"Auto-saved: {os.path.basename(temp_filename)}")
                                        logging.info(f"Auto-saved '{temp_filename}' successfully.")
                                    else:
                                        logging.warning(f"Auto-save: File context changed during write of '{temp_filename}'. Save may be stale.")
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
                            self._auto_save_stop_event.set() # Signal thread to terminate
                            self._set_status_message("Auto-save disabled due to an internal error.")
                            break # Exit the loop

                    logging.info("Auto-save thread finished.")

                # Create and start the daemon thread for auto-saving
                self._auto_save_thread = threading.Thread(
                    target=auto_save_task_runner, 
                    daemon=True, # Thread will exit when the main program exits
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
                self._auto_save_stop_event.set() # Signal the thread to stop
                # Optionally, wait for the thread to finish with a timeout
                # self._auto_save_thread.join(timeout=2.0) 
                # if self._auto_save_thread.is_alive():
                #    logging.warning("Auto-save thread did not stop in time.")
            self._auto_save_thread = None # Discard thread object

            self._set_status_message("Auto-save disabled")
            logging.info("Auto-save feature has been disabled.")

        # This method always changes the status message, so a redraw is needed.
        return self.status_message != original_status or True # Force True because state change is significant
    

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
        original_modified_flag = self.modified # To see if 'n' in prompt changes it
        # Other states like text, filename, cursor will definitely change if new_file proceeds.
        
        redraw_is_needed = False # Accumulator for redraw reasons

        # 1. Handle unsaved changes in the current buffer
        if self.modified:
            status_before_prompt = self.status_message # Status before this specific prompt
            ans = self.prompt("Save changes before creating new file? (y/n): ")
            if self.status_message != status_before_prompt:
                redraw_is_needed = True # Prompt interaction changed status

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
                if self.modified != original_modified_flag: # If modified flag actually changed
                    redraw_is_needed = True
            else: 
                # User cancelled the save prompt (Esc, Enter on empty, or invalid input)
                # If status wasn't already changed by the prompt itself to something new
                if not redraw_is_needed and self.status_message == original_status:
                    self._set_status_message("New file creation cancelled.")
                return True # Status changed (either by prompt or by cancellation message)
        
        # 2. If we reached here, changes (if any) were handled. Proceed to create the new file state.
        # These actions below will definitely require a redraw.
        
        logging.debug("Proceeding to reset editor state for a new file.")

        self.text = [""]  # Start with a single empty line
        self.filename = None
        self.encoding = "UTF-8" # Default encoding for new files
        # self.modified should be False at this point (either saved, discarded, or was already False)
        # but explicitly set it to ensure consistency for a new file.
        self.modified = False 

        # Reset language-specific and version control information
        # _lexer will be re-detected by self.detect_language()
        if self._lexer is not None: # If there was a lexer, changing to None or new one is a change
             redraw_is_needed = True # Though detect_language will also imply this
        self._lexer = None 
        self._last_git_filename = None # Reset for Git info updates
        if self.git_info != ("", "", "0"): redraw_is_needed = True
        self.git_info = ("", "", "0") 

        # Reset cursor, scroll, selection, and history
        # set_initial_cursor_position itself implies a major visual reset.
        self.set_initial_cursor_position() 
        self.action_history.clear()
        self.undone_actions.clear()
        
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
        This method is primarily called by handle_escape.

        Returns:
            bool: True if any specific state (lint panel visibility, selection active,
                  search highlights present) was actively cancelled AND the status message
                  was updated as a result. False if no such specific state was active to be
                  cancelled by this method call, or if the status message did not change.
        """
        logging.debug(
            f"cancel_operation called. Panel: {self.lint_panel_active}, "
            f"Selecting: {self.is_selecting}, Highlights: {bool(self.highlighted_matches)}"
        )
        
        original_status = self.status_message 
        action_cancelled_a_specific_state = False # Tracks if a UI element state changed

        if self.lint_panel_active:
            self.lint_panel_active = False
            self.lint_panel_message = "" # Clear the message when panel is explicitly closed
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
        elif self.highlighted_matches: # Check if the list is non-empty
            self.highlighted_matches = [] # Clear the highlights
            # Optionally reset search_term and current_match_idx if needed for stricter cancel
            # self.search_term = ""
            # self.current_match_idx = -1
            self._set_status_message("Search highlighting cleared")
            logging.debug("cancel_operation: Search highlighting cleared.")
            action_cancelled_a_specific_state = True
        
        # Return True if a specific state was cancelled OR if the status message changed.
        # This ensures that if only the status changes (e.g. from "Ready" to "Lint panel closed"),
        # it's still considered a change that might need a redraw.
        return action_cancelled_a_specific_state or (self.status_message != original_status)
    

    def handle_escape(self) -> bool:
        """
        Universal Esc key handler with context-dependent behavior.
        1. Attempts to cancel active states (lint panel, selection, search highlights)
           by calling self.cancel_operation(). If an operation was cancelled (and it returns True),
           this method also returns True.
        2. If no active state was cancelled by the first step (cancel_operation returned False):
           - A second Esc press within a short interval (e.g., 1.5s) initiates editor exit.
           - A single Esc press (or the first Esc of a potential double-press sequence
             that didn't cancel anything specific) sets a generic "Operation Cancelled" status message.
        
        The timestamp for detecting double-press (`_last_esc_time`) is updated
        after determining the action for the current Esc press.

        Returns:
            bool: True if any state relevant for redraw changed (panel visibility, selection,
                  highlights, status message, or if exit was initiated), False otherwise.
        """
        now = time.monotonic()
        # Get the time of the *previous* Esc press. Default to 0.0 if not set (e.g., first Esc ever or after long pause).
        time_of_previous_esc = getattr(self, "_last_esc_time", 0.0) 
        
        original_status = self.status_message # Store to check if final status message differs
        action_taken_requiring_redraw = False

        # --- Part 1: Attempt to cancel an active operation using cancel_operation ---
        if self.cancel_operation(): # cancel_operation now returns True if it did something (cancelled state or changed status)
            action_taken_requiring_redraw = True
            logging.debug("handle_escape: cancel_operation handled the Esc press and indicated a change.")
            # Update _last_esc_time *after* cancel_operation.
            # This allows this Esc (which performed a cancel) to be the first Esc
            # in a potential double-press sequence if the next Esc follows quickly.
            self._last_esc_time = now 
            return action_taken_requiring_redraw

        # --- Part 2: No active operation was cancelled by cancel_operation() (it returned False) ---
        # Now, this Esc press can either be the first of a double-press sequence for exit,
        # or a single Esc that should set a generic "Cancelled" message.
        
        # Check if this Esc press forms a "double press" with the previous one
        if (now - time_of_previous_esc) < 1.5: # Threshold for double press in seconds
            logging.debug("handle_escape: Double Esc detected (and no prior operation was cancelled by the first Esc of this pair). Attempting to exit.")
            
            self.exit_editor() # Handles save prompts and then calls sys.exit().
                               # Does not return a value for redraw as it's a terminal action.
                               # If user cancels exit (e.g., at save prompt), control returns here.
            
            # Attempting exit is a significant interaction. Assume a redraw might be needed
            # to clear any UI artifacts from prompts shown by exit_editor, or if exit was cancelled
            # and exit_editor set a status message.
            action_taken_requiring_redraw = True 
        else:
            # This is a single Esc press, and there was no specific operation 
            # (panel, selection, highlight) that cancel_operation could handle.
            # Set a generic "Operation Cancelled" status message.
            logging.debug("handle_escape: Single Esc (no active operation to cancel, not a double press). Setting generic 'Cancelled' status.")
            self._set_status_message("Operation Cancelled") 
            # Setting a status message implies a need for redraw if it's different from the original.
            # This will be caught by the final check against original_status.

        # Update the timestamp for *this* Esc press, making it the "previous" 
        # for the next potential Esc press. This is crucial for the double-press logic.
        self._last_esc_time = now
            
        # Final check: even if the core logic didn't set action_taken_requiring_redraw directly,
        # if the status message changed from its original state at the start of this method,
        # a redraw is certainly needed.
        if self.status_message != original_status:
            action_taken_requiring_redraw = True
            
        return action_taken_requiring_redraw
    

    def exit_editor(self) -> None: # This method either exits or returns; no bool for redraw needed by caller
        """
        Attempts to exit the editor.
        - Prompts to save any unsaved changes if `self.modified` is True.
        - If saving is chosen but fails, the exit is aborted to prevent data loss.
        - If exit is not cancelled, stops background threads and closes curses gracefully before exiting.
        """
        logging.debug("exit_editor: Attempting to exit editor.")
        
        # 1. Check for unsaved changes and prompt the user if necessary.
        if self.modified:
            original_status = self.status_message # Store status before prompt
            ans = self.prompt("Save changes before exiting? (y/n): ")
            status_changed_by_prompt = (self.status_message != original_status)

            if ans and ans.lower().startswith("y"):
                logging.debug("exit_editor: User chose to save changes.")
                # Attempt to save the file. save_file() itself handles save_as if needed
                # and sets status messages for success/failure.
                # save_file() returns True if it made changes that might need a redraw.
                self.save_file() # Let save_file handle its own status updates
                
                # CRITICAL CHECK: After attempting to save, is the file still modified?
                # If so, saving failed or was cancelled by the user (e.g., during 'Save As' prompt).
                if self.modified:
                    self._set_status_message("Exit aborted: file not saved. Please save or discard changes.")
                    logging.warning("exit_editor: Exit aborted because 'save_file' did not result in a saved state (self.modified is still True).")
                    return # Abort exit to prevent data loss
                else:
                    logging.debug("exit_editor: Changes saved successfully.")
                    # Proceed to exit
            elif ans and ans.lower().startswith("n"):
                logging.debug("exit_editor: User chose NOT to save changes. Proceeding with exit.")
                # Proceed to exit without saving
            else:
                # User cancelled the save prompt (e.g., pressed Esc, Enter on empty, or invalid input).
                if not status_changed_by_prompt and self.status_message == original_status:
                    self._set_status_message("Exit cancelled by user prompt.")
                logging.debug("exit_editor: Exit cancelled by user at save prompt.")
                return # Abort exit

        # If we reach here, either:
        # - There were no modifications.
        # - User chose 'y' to save, and saving was successful (self.modified is False).
        # - User chose 'n' not to save.

        logging.info("exit_editor: Proceeding with editor shutdown.")

        # 2. Stop background threads (e.g., auto-save).
        # Signal the auto-save thread to stop.
        if hasattr(self, '_auto_save_enabled') and self._auto_save_enabled:
            self._auto_save_enabled = False # Primary flag to stop loop
            if hasattr(self, '_auto_save_stop_event'):
                self._auto_save_stop_event.set() # Signal event
            logging.debug("exit_editor: Signaled auto-save thread to stop.")
            # Give a very brief moment for the thread to acknowledge, but don't block UI for long.
            if hasattr(self, '_auto_save_thread') and self._auto_save_thread and self._auto_save_thread.is_alive():
                self._auto_save_thread.join(timeout=0.1) # Brief wait
                if self._auto_save_thread.is_alive():
                    logging.warning("exit_editor: Auto-save thread still alive after brief join attempt.")
        # Other threads should be handled similarly if they exist.
        # Daemon threads will terminate when the main program exits.

        # 3. Gracefully terminate curses.
        # Ensure this is called from the main thread to prevent curses errors.
        if threading.current_thread() is threading.main_thread():
            logging.debug("exit_editor: Running in main thread, attempting to call curses.endwin().")
            try:
                self.stdscr.keypad(False) # Turn off keypad mode
                curses.nocbreak()         # Restore cbreak/cooked mode (opposite of raw)
                curses.echo()             # Restore echoing of typed characters
                curses.endwin()           # Restore terminal to original state
                logging.info("exit_editor: curses.endwin() called successfully. Terminal restored.")
            except curses.error as e:
                logging.error(f"exit_editor: Curses error during curses.endwin(): {e}")
            except Exception as e: # Catch any other unexpected error
                logging.error(f"exit_editor: Unexpected error during curses.endwin(): {e}", exc_info=True)
        else:
            # This scenario (calling endwin from non-main thread) should ideally be avoided.
            logging.warning("exit_editor: Attempting to call curses.endwin() from a non-main thread. This is risky. Skipping direct call.")
            # One might try to signal the main thread to call endwin, but that's complex.

        # 4. Terminate the program.
        logging.info("exit_editor: Exiting program with sys.exit(0).")
        sys.exit(0)


    def prompt(self, message: str, max_len: int = 1024, timeout_seconds: int = 60) -> Optional[str]:
        """
        Displays a single-line input prompt in the status bar with a timeout.

        Features:
        - Enter: Confirms and returns the stripped input string.
        - Esc: Cancels and returns None.
        - Tab: Inserts a tab equivalent (currently 4 spaces).
        - Backspace, Delete, Left/Right Arrows, Home, End: Standard text editing.
        - Resize: Redraws the prompt according to the new screen size.
        - Timeout: Returns None if no input is confirmed within the timeout.

        Args:
            message (str): The message to display before the input field.
            max_len (int): Maximum allowed length of the input buffer.
            timeout_seconds (int): Timeout for waiting for input, in seconds.

        Returns:
            Optional[str]: The user's input string (stripped) if confirmed, 
                           or None if cancelled or timed out.
        """
        logging.debug(
            f"Prompt called. Message: '{message}', Max length: {max_len}, Timeout: {timeout_seconds}s"
        )
        
        # locale.setlocale(locale.LC_CTYPE, '') # Typically set once globally at app start
        # If not set globally, uncommenting here might be needed for wcwidth/char display.
        # For now, assuming it's set globally.

        original_cursor_visibility = curses.curs_set(1) # Make cursor visible for prompt
        # curses.echo() # noecho() is usually set globally for the editor; prompt handles its own echo.
        
        # Set stdscr to blocking mode with a timeout for this prompt
        self.stdscr.nodelay(False) 
        self.stdscr.timeout(timeout_seconds * 1000) # Timeout in milliseconds

        input_buffer: List[str] = [] # Stores characters of the input
        cursor_char_pos: int = 0     # Cursor position within the input_buffer (index)
        
        # Tab width for Tab key insertion (could be made configurable)
        prompt_tab_width: int = self.config.get("editor", {}).get("tab_size", 4)
        
        input_result: Optional[str] = None # Stores the final result

        try:
            while True:
                term_height, term_width = self.stdscr.getmaxyx()
                prompt_row = term_height - 1 # Prompt on the last line

                # Truncate display message if too long for the available width
                # Leave some space for the input field itself (e.g., 10 chars at least)
                max_display_message_width = max(0, term_width - 10 - 1) # -1 for cursor
                display_message_str = message
                if len(message) > max_display_message_width: # Basic length check for prompt message
                    display_message_str = message[:max_display_message_width - 3] + "..."
                
                display_message_len = self.editor.get_string_width(display_message_str) # Use editor's width calc

                # --- Clear and redraw the prompt line ---
                try:
                    self.stdscr.move(prompt_row, 0)
                    self.stdscr.clrtoeol()
                    # Draw the prompt message
                    self.stdscr.addstr(prompt_row, 0, display_message_str, self.colors.get("status", 0))
                except curses.error as e_draw:
                    logging.error(f"Prompt: Curses error during prompt draw (message): {e_draw}")
                    # If drawing fails, it might be hard to recover, abort prompt
                    return None 

                current_input_text = "".join(input_buffer)
                # Available screen width for the input text itself
                available_text_width = term_width - (display_message_len + 1) # +1 for potential cursor space
                
                # --- Horizontal scrolling logic for the input text ---
                visible_input_text_segment = current_input_text
                cursor_screen_offset_relative_to_segment_start = self.editor.get_string_width(current_input_text[:cursor_char_pos])
                
                current_input_text_width = self.editor.get_string_width(current_input_text)

                h_scroll_offset = 0 # How many display cells of the input text are scrolled left
                if current_input_text_width > available_text_width:
                    # If cursor is too far to the right to be visible
                    if cursor_screen_offset_relative_to_segment_start > available_text_width -1 : # -1 for cursor itself
                        h_scroll_offset = cursor_screen_offset_relative_to_segment_start - (available_text_width -1)
                    # If cursor is too far to the left (should ensure it's visible)
                    # This part needs to ensure cursor_screen_offset_relative_to_segment_start - h_scroll_offset >=0
                    # A simpler approach for now might be to just show tail if too long.

                    # Simplified viewport: show segment of text that fits, trying to keep cursor in view
                    # This is complex to do perfectly with variable width characters.
                    # The original logic was a good attempt. Let's refine slightly.
                    
                    # Re-calculating visible part based on keeping cursor in view
                    # Start by assuming full text is visible
                    text_view_start_char_idx = 0
                    text_view_end_char_idx = len(input_buffer)
                    
                    # Adjust viewport if text is wider than available space
                    if current_input_text_width > available_text_width:
                        # Try to center cursor or keep it visible
                        # Calculate width from cursor to end of text
                        width_cursor_to_end = self.editor.get_string_width(current_input_text[cursor_char_pos:])
                        # Calculate width from start of text to cursor
                        width_start_to_cursor = cursor_screen_offset_relative_to_segment_start

                        # If cursor is near the right edge of what's shown
                        while width_start_to_cursor - self.editor.get_string_width(input_buffer[text_view_start_char_idx]) > available_text_width -1 and text_view_start_char_idx < cursor_char_pos :
                            text_view_start_char_idx +=1
                        
                        # If cursor is near the left edge of what's shown (less common to scroll this way in prompt)
                        # (More complex logic for full bi-directional scrolling in prompt omitted for brevity)

                    visible_input_text_segment = "".join(input_buffer[text_view_start_char_idx:])
                    # Recalculate cursor position relative to the *new* start of the visible segment
                    cursor_screen_offset_relative_to_segment_start = self.editor.get_string_width("".join(input_buffer[text_view_start_char_idx:cursor_char_pos]))

                    # Truncate visible_input_text_segment if it's still too long for the screen
                    # This is a final clip to prevent curses errors
                    temp_visible_text = ""
                    current_visible_width = 0
                    for char_in_seg in visible_input_text_segment:
                        char_w = self.editor.get_char_width(char_in_seg)
                        if current_visible_width + char_w > available_text_width:
                            break
                        temp_visible_text += char_in_seg
                        current_visible_width += char_w
                    visible_input_text_segment = temp_visible_text

                # --- Draw the input text and position cursor ---
                try:
                    self.stdscr.addstr(prompt_row, display_message_len, visible_input_text_segment)
                    # Cursor position on screen: after message, plus offset within visible segment
                    screen_cursor_x = display_message_len + cursor_screen_offset_relative_to_segment_start
                    # Clamp cursor to be within the line and window bounds
                    screen_cursor_x = max(display_message_len, min(screen_cursor_x, term_width - 1))
                    self.stdscr.move(prompt_row, screen_cursor_x)
                except curses.error as e_draw_input:
                    logging.error(f"Prompt: Curses error during input text draw: {e_draw_input}")
                    # Abort if drawing input fails
                    return None
                    
                self.stdscr.refresh() # Refresh screen to show prompt and input

                # --- Get key press ---
                key_code: Any = curses.ERR # Initialize to curses.ERR for timeout case
                try:
                    key_code = self.stdscr.get_wch() # This can return int or str
                    logging.debug(f"Prompt: get_wch() returned: {repr(key_code)} (type: {type(key_code)})")
                except curses.error as e_getch:
                    # This typically means a timeout ("no input") or other curses error
                    if 'no input' in str(e_getch).lower():
                        logging.warning(f"Prompt: Input timed out after {timeout_seconds}s for message: '{message}'")
                        input_result = None # Timeout
                        break # Exit the while loop
                    else:
                        logging.error(f"Prompt: Curses error on get_wch(): {e_getch}", exc_info=True)
                        input_result = None # Undetermined error
                        break # Exit the while loop
                
                # --- Process key press ---
                if isinstance(key_code, int):
                    if key_code == 27: # Esc key (typically int 27)
                        logging.debug("Prompt: Esc (int) detected. Cancelling.")
                        input_result = None
                        break
                    elif key_code in (curses.KEY_ENTER, 10, 13): # Enter/Return keys
                        logging.debug(f"Prompt: Enter (int {key_code}) detected. Confirming.")
                        input_result = "".join(input_buffer).strip()
                        break
                    elif key_code in (curses.KEY_BACKSPACE, 127, 8): # Backspace (127 is often DEL, 8 is ASCII BS)
                        if cursor_char_pos > 0:
                            cursor_char_pos -= 1
                            input_buffer.pop(cursor_char_pos)
                    elif key_code == curses.KEY_DC: # Delete character under cursor (conceptually, to the right)
                        if cursor_char_pos < len(input_buffer):
                            input_buffer.pop(cursor_char_pos)
                            # Cursor position doesn't change relative to characters before it
                    elif key_code == curses.KEY_LEFT:
                        cursor_char_pos = max(0, cursor_char_pos - 1)
                    elif key_code == curses.KEY_RIGHT:
                        cursor_char_pos = min(len(input_buffer), cursor_char_pos + 1)
                    elif key_code == curses.KEY_HOME:
                        cursor_char_pos = 0
                    elif key_code == curses.KEY_END:
                        cursor_char_pos = len(input_buffer)
                    elif key_code == curses.KEY_RESIZE:
                        logging.debug("Prompt: KEY_RESIZE detected. Redrawing prompt.")
                        # Screen will be redrawn at the start of the loop
                        # No status message needed here, main loop handles resize status if needed.
                        continue 
                    elif key_code == curses.ascii.TAB: # Tab key (ord('\t') or curses.ascii.TAB)
                        tab_spaces = " " * prompt_tab_width
                        for char_in_tab in tab_spaces: # Insert multiple spaces for tab
                            if len(input_buffer) < max_len:
                                input_buffer.insert(cursor_char_pos, char_in_tab)
                                cursor_char_pos += 1
                    elif 32 <= key_code < 1114112 : # Other integer that might be a printable char
                        try:
                            char_to_insert = chr(key_code)
                            if len(input_buffer) < max_len and wcswidth(char_to_insert) >= 0 : # check if printable
                                input_buffer.insert(cursor_char_pos, char_to_insert)
                                cursor_char_pos += 1
                        except ValueError:
                             logging.warning(f"Prompt: Could not convert int key {key_code} to char.")
                    else:
                        logging.debug(f"Prompt: Ignored integer key: {key_code}")

                elif isinstance(key_code, str):
                    if key_code == '\x1b': # Esc key (sometimes comes as string)
                        logging.debug("Prompt: Esc (str) detected. Cancelling.")
                        input_result = None
                        break
                    elif key_code in ("\n", "\r"): # Enter/Return as string
                        logging.debug(f"Prompt: Enter (str '{repr(key_code)}') detected. Confirming.")
                        input_result = "".join(input_buffer).strip()
                        break
                    elif key_code == '\t': # Tab as string
                        tab_spaces = " " * prompt_tab_width
                        for char_in_tab in tab_spaces:
                            if len(input_buffer) < max_len:
                                input_buffer.insert(cursor_char_pos, char_in_tab)
                                cursor_char_pos += 1
                    elif len(key_code) == 1 and key_code.isprintable(): # Single printable character
                        if len(input_buffer) < max_len:
                            input_buffer.insert(cursor_char_pos, key_code)
                            cursor_char_pos += 1
                    # Could also handle multi-character paste here if get_wch() ever returns that
                    # (though it's not standard for get_wch()).
                    else:
                        logging.debug(f"Prompt: Ignored string key: {repr(key_code)}")
                # else key_code == curses.ERR, handled by the try-except for get_wch()

        finally:
            # Restore terminal settings that were changed for the prompt
            self.stdscr.nodelay(True)  # Restore non-blocking input for the main editor loop
            self.stdscr.timeout(-1)    # Disable timeout
            # curses.noecho()          # Should still be in effect from editor's global settings
            curses.curs_set(original_cursor_visibility) # Restore original cursor visibility
            
            # Clear the prompt line from the status bar before returning
            term_height, _ = self.stdscr.getmaxyx()
            try:
                if term_height > 0: # Ensure height is valid
                    self.stdscr.move(term_height - 1, 0)
                    self.stdscr.clrtoeol()
                # A full redraw by the main loop will typically follow, which will
                # redraw the normal status bar. Calling self.stdscr.refresh() here
                # might cause a flicker if main loop also refreshes immediately.
                # However, if prompt returns and main loop doesn't redraw *immediately*,
                # prompt artifacts could remain.
                # For now, let main loop's draw handle full status bar restoration.
            except curses.error as e_final_clear:
                 logging.warning(f"Prompt: Curses error during final status line clear: {e_final_clear}")

            curses.flushinp() # Clear any unprocessed typeahead characters from terminal input buffer

        return input_result


    # ========== Search/Replace and Find ======================
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
        status_changed_by_prompts = False # Track if prompts themselves alter final status view

        # Clear previous search state immediately
        self.highlighted_matches = []
        self.search_matches = []
        self.search_term = "" # Clear the term so F3 won't use the old one
        self.current_match_idx = -1
        # Initial redraw might be good here if clearing highlights should be immediate
        # but we'll rely on the return value for the main loop.

        # Prompt for search pattern
        status_before_search_prompt = self.status_message
        search_pattern_str = self.prompt("Search for (regex): ")
        if self.status_message != status_before_search_prompt:
            status_changed_by_prompts = True

        if not search_pattern_str: # User cancelled (Esc or empty Enter)
            if not status_changed_by_prompts and self.status_message == original_status:
                self._set_status_message("Search/Replace cancelled")
            # Return True if status changed by prompt or by cancellation message
            return self.status_message != original_status 

        # Prompt for replacement string
        # An empty replacement string is valid (means delete the matched pattern).
        status_before_replace_prompt = self.status_message
        replace_with_str = self.prompt("Replace with: ") # `prompt` can return None if cancelled
        if self.status_message != status_before_replace_prompt:
            status_changed_by_prompts = True

        if replace_with_str is None: # User cancelled the replacement prompt
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
            return True # Status changed due to error message

        # --- Perform replacement ---
        new_text_lines: List[str] = []
        total_replacements_count = 0
        line_processing_error_occurred = False
        
        # It's safer to operate on a copy if iterating and modifying
        # or build a new list directly as done here.
        # Lock is needed for reading self.text if it could be modified by another thread,
        # but here we are in the main thread of action.
        
        with self._state_lock: # Access self.text safely
            current_text_snapshot = list(self.text) # Work on a snapshot

        for line_idx, current_line in enumerate(current_text_snapshot):
            try:
                # Perform substitution on the current line
                # subn returns a tuple: (new_string, number_of_subs_made)
                new_line_content, num_subs_on_line = compiled_regex_pattern.subn(replace_with_str, current_line)
                new_text_lines.append(new_line_content)
                if num_subs_on_line > 0:
                    total_replacements_count += num_subs_on_line
            except Exception as e_sub: # Catch errors during re.subn (e.g., complex regex on specific line)
                logging.error(f"Error replacing in line {line_idx + 1} ('{current_line[:50]}...'): {e_sub}")
                new_text_lines.append(current_line) # Append original line in case of error on this line
                line_processing_error_occurred = True

        # --- Update editor state if replacements were made or errors occurred ---
        if total_replacements_count > 0 or line_processing_error_occurred:
            with self._state_lock:
                self.text = new_text_lines
                self.modified = True  # Document has been modified
                # Search and Replace is a major change, typically clears undo/redo history
                self.action_history.clear()
                self.undone_actions.clear()
                logging.debug("Cleared undo/redo history after search/replace.")
                # Cursor position might be invalidated, reset to start of document or last known good pos.
                # For simplicity, let's move to the beginning of the file.
                self.cursor_y = 0
                self.cursor_x = 0
                self._ensure_cursor_in_bounds() # Ensure it's valid
                self._clamp_scroll()            # Adjust scroll

            if line_processing_error_occurred:
                self._set_status_message(f"Replaced {total_replacements_count} occurrences with errors on some lines.")
                logging.warning("Search/Replace completed with errors on some lines.")
            else:
                self._set_status_message(f"Replaced {total_replacements_count} occurrence(s).")
                logging.info(f"Search/Replace successful: {total_replacements_count} replacements.")
            return True # Text changed, status changed, cursor moved
        else: # No replacements made and no errors
            self._set_status_message("No occurrences found to replace.")
            logging.info("Search/Replace: No occurrences found.")
            return True # Status message changed
            
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
        if not term: # If the search term is empty, no matches can be found.
            return matches

        # Perform a case-insensitive search
        search_term_lower = term.lower()
        term_length = len(term) # Original term length for calculating end index

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
            line_content_lower = line_content.lower() # Compare against the lowercased version of the line

            while True:
                # Find the next occurrence in line_content_lower,
                # but record indices based on the original line_content.
                found_at_index = line_content_lower.find(search_term_lower, current_search_start_column)
                
                if found_at_index == -1: # No more matches in this line
                    break
                
                match_end_index = found_at_index + term_length # End index is exclusive
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
        self.search_term = ""      # Reset search term for a new search
        self.current_match_idx = -1
        
        redraw_needed_due_to_clearing = had_previous_highlights # If highlights were cleared, redraw

        # 2. Prompt for the search term.
        # The prompt itself temporarily changes the status bar.
        status_before_prompt = self.status_message # Could have been changed if highlights were cleared and status set
        
        # Prompting the user for input
        term_to_search = self.prompt("Find: ") # self.prompt handles its own status line updates during input

        # Check if status message was altered by the prompt itself (e.g., timeout, or internal prompt messages)
        # or if it was restored to its state before the prompt.
        status_changed_by_prompt_interaction = (self.status_message != status_before_prompt)
        
        if not term_to_search: # User cancelled the prompt (e.g., Esc) or entered nothing
            # If status after prompt is same as original status (before clearing highlights and prompt),
            # but user cancelled, set "Search cancelled".
            if not status_changed_by_prompt_interaction and self.status_message == original_status:
                self._set_status_message("Search cancelled")
            # A redraw is needed if highlights were cleared, or if status message changed.
            return redraw_needed_due_to_clearing or (self.status_message != original_status)

        # 3. A search term was entered.
        self.search_term = term_to_search # Store the new search term
        
        # 4. Collect all matches for the new term.
        # _collect_matches reads self.text, so no direct visual change from this call itself.
        self.search_matches = self._collect_matches(self.search_term)
        
        # 5. Update highlights to show the new matches.
        # This is a visual change if new matches are found or if previous highlights are now gone.
        self.highlighted_matches = list(self.search_matches) # Make a copy for highlighting
        
        # 6. Navigate and set status based on whether matches were found.
        if not self.search_matches:
            self._set_status_message(f"'{self.search_term}' not found")
            # Even if no matches, highlights were cleared/updated (to empty), so redraw likely.
            # And status message changed.
        else:
            self.current_match_idx = 0 # Go to the first match
            # _goto_match will update cursor_y, cursor_x, scroll_top, scroll_left
            self._goto_match(self.current_match_idx) 
            self._set_status_message(
                f"Found {len(self.search_matches)} match(es) for '{self.search_term}'. Press F3 for next."
            )
        
        # Determine if overall state change warrants a redraw.
        # Changes could be: highlights changed, cursor/scroll changed by _goto_match, status message changed.
        if (redraw_needed_due_to_clearing or # Highlights were cleared
            bool(self.highlighted_matches) or # New highlights were added
            (self.cursor_y, self.cursor_x) != original_cursor_pos or
            (self.scroll_top, self.scroll_left) != original_scroll_pos or
            self.status_message != original_status):
            return True
            
        return False # Should rarely be False, as status or highlights usually change.
    

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
            else: # search_term exists, but no matches were found for it
                self._set_status_message(f"No matches found for '{self.search_term}'.")
            
            # Ensure no stale highlights if we reach here
            if self.highlighted_matches: # If there were highlights from a previous successful search
                self.highlighted_matches = []
                changed_state = True # Highlight state changed

            self.current_match_idx = -1 # Reset current match index
            
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
    
    def _goto_match(self, match_index: int) -> None: # Added type hint and English docstring
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
            return # Invalid index or no matches to go to

        # 2. Get the coordinates of the target match.
        # search_matches stores tuples: (row_index, column_start_index, column_end_index)
        target_row, target_col_start, _ = self.search_matches[match_index] # We only need start for cursor

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
        if target_col_start > len(self.text[target_row]): # Allow being at the end of the line
            logging.error(f"_goto_match: Match col_start {target_col_start} is out of bounds for line {target_row} (len {len(self.text[target_row])}).")
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
        
        if self.visible_lines > 0: # visible_lines should be height - 2 (status/number bars)
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
        if not stripped_filename: # If filename was only whitespace
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
        basename_to_check = os.path.basename(filename) # Check only the filename part for these chars
        # Common invalid characters for many filesystems (Windows, Linux, macOS)
        # Note: '/' and '\' are path separators, their presence in `basename_to_check` means
        # the input `filename` was likely just a name, not a path.
        # If `filename` is intended to be a full path, these chars are fine in the path part.
        # This regex is for the *name* part.
        invalid_chars_regex = r'[<>:"/\\|?*\x00-\x1F]' # Control chars and common restricted symbols
        if re.search(invalid_chars_regex, basename_to_check):
            self._set_status_message(f"Filename '{basename_to_check}' contains invalid characters.")
            logging.warning(
                f"Validation failed: Filename part '{basename_to_check}' contains invalid characters: "
                f"Matched by regex '{invalid_chars_regex}'."
            )
            return False

        # Check for reserved system names (primarily a Windows concern).
        if os.name == 'nt': # For Windows operating systems
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
                 self._set_status_message(f"Cannot save: Target path is the current directory itself.")
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

            logging.debug(f"Filename '{filename}' validated successfully. Resolved absolute path: '{absolute_target_path}'")
            return True

        except Exception as e_path:
            # Catch errors during path manipulation (e.g., path too long for OS functions, permission issues with abspath).
            self._set_status_message(f"Error validating file path: {str(e_path)[:70]}...")
            logging.error(f"Error validating filename path for '{filename}': {e_path}", exc_info=True)
            return False # Treat any path processing error as a validation failure


    # =============выполнения команд оболочки Shell commands =================================
    def _execute_shell_command_async(self, cmd_list: List[str]) -> None: # Added type hint
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
        result_message: str = "" # This will be the final message sent to the queue
        exit_code: int = -1      # Default/unknown exit code

        process_handle: Optional[subprocess.Popen] = None # To store Popen object for terminate/kill

        try:
            command_str_for_log = ' '.join(shlex.quote(c) for c in cmd_list)
            logging.debug(f"Async shell command: Preparing to execute: {command_str_for_log}")

            # Determine current working directory for the command
            # Prefer directory of the current file, fallback to os.getcwd()
            # Ensure self.filename is valid and exists if used for cwd
            cwd_path: str
            if self.filename and os.path.isfile(self.filename): # Check if it's a file, not just exists
                cwd_path = os.path.dirname(os.path.abspath(self.filename))
            else:
                cwd_path = os.getcwd()
            logging.debug(f"Async shell command: Effective CWD: {cwd_path}")

            # Use subprocess.Popen for better control, especially for timeouts and stream handling
            process_handle = subprocess.Popen(
                cmd_list,
                stdout=subprocess.PIPE,    # Capture standard output
                stderr=subprocess.PIPE,    # Capture standard error
                text=True,                 # Decode output as text (uses locale's encoding by default or specified)
                encoding="utf-8",          # Explicitly specify UTF-8 for decoding
                errors="replace",          # Replace undecodable characters
                cwd=cwd_path,              # Set the current working directory for the command
                universal_newlines=True    # Deprecated but often used with text=True for line ending normalization
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
                logging.warning(f"Async shell command '{command_str_for_log}' timed out after {shell_timeout}s. Terminating.")
                result_message = f"Command timed out ({shell_timeout}s). Terminating."
                
                # Attempt to terminate and then kill the process
                try:
                    process_handle.terminate() # Send SIGTERM
                    # Wait a bit for termination
                    try:
                        outs, errs = process_handle.communicate(timeout=5) # Collect any final output
                        captured_stdout += outs if outs else ""
                        captured_stderr += errs if errs else ""
                    except subprocess.TimeoutExpired: # Still didn't terminate
                        logging.warning(f"Process '{command_str_for_log}' did not terminate gracefully, attempting kill.")
                        process_handle.kill() # Send SIGKILL
                        # Try one last communicate to drain pipes after kill
                        try:
                            outs, errs = process_handle.communicate(timeout=1)
                            captured_stdout += outs if outs else ""
                            captured_stderr += errs if errs else ""
                        except Exception:
                            pass # Ignore errors on communicate after kill
                except Exception as e_term:
                    logging.error(f"Error during termination/kill of timed-out process '{command_str_for_log}': {e_term}")
                
                exit_code = process_handle.returncode if process_handle.returncode is not None else -2 # Indicate timeout/kill
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
            exit_code = -3 # Custom code for FileNotFoundError
        except Exception as e_exec:
            # Catch any other exceptions during Popen or initial setup.
            command_str_for_log_err = ' '.join(shlex.quote(c) for c in cmd_list) if 'cmd_list' in locals() else "Unknown command"
            logging.exception(f"Error executing shell command '{command_str_for_log_err}'")
            result_message = f"Execution error: {str(e_exec)[:80]}..."
            exit_code = -4 # Custom code for other execution errors

        finally:
            # Construct the final message based on outcome, if not already set by a major error.
            if not result_message: # If no message was set by FileNotFoundError, Timeout, or general Exception
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
        if self.status_message != status_before_prompt: # If prompt itself changed status
            status_changed_by_interaction = True

        if not command_str: # User cancelled or entered empty command at prompt
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
                return True # Status message changed
        except ValueError as e_shlex: # Error during shlex.split (e.g., unmatched quotes)
            self._set_status_message(f"Command parse error: {e_shlex}")
            logging.error(f"Shell command parse error for '{command_str}': {e_shlex}")
            return True # Status message changed

        # --- Set status to "Running command..." and start the thread ---
        # This message will be displayed while the command runs in the background.
        display_command_str = ' '.join(shlex.quote(c) for c in cmd_list_args)
        if len(display_command_str) > 60: # Truncate for status bar
            display_command_str = display_command_str[:57] + "..."
        
        self._set_status_message(f"Running: {display_command_str}")
        
        # Start the command execution in a separate thread
        thread_name = f"ShellExecThread-{cmd_list_args[0]}-{int(time.time())}"
        command_execution_thread = threading.Thread(
            target=self._execute_shell_command_async,
            args=(cmd_list_args,),
            daemon=True, # Thread will exit when the main program exits
            name=thread_name
        )
        command_execution_thread.start()

        logging.debug(f"Started shell command execution thread: {thread_name} for command: {cmd_list_args}")
        
        # The method has initiated an async action and set a status message.
        return True # Status message changed, redraw needed.


    # ==================== GIT ===============================
    def _run_git_command_async(self, cmd_list: List[str], command_name: str) -> None: # Added type hints
        """
        Executes a Git command in a separate thread and sends the result message
        to the self._git_cmd_q queue.

        Args:
            cmd_list (List[str]): The Git command and its arguments as a list.
            command_name (str): A display name for the command (e.g., "status", "commit")
                                used in logging and status messages.
        """
        result_message: str = "" # Final message to be queued

        try:
            # Determine the working directory for the Git command.
            # Prefer the directory of the current file if it's a valid, existing file.
            # Otherwise, use the current working directory of the editor process.
            repo_dir_path: str
            if self.filename and os.path.isfile(self.filename): # Check if it's an actual file
                repo_dir_path = os.path.dirname(os.path.abspath(self.filename))
            else:
                repo_dir_path = os.getcwd()
            
            # Further ensure this directory is part of a Git repository before running commands
            # (though integrate_git should ideally check this first for menu commands).
            # For robustness, individual git commands could also check.
            # For now, proceed with repo_dir_path.

            logging.debug(f"Async Git command: Running 'git {command_name}' in directory: '{repo_dir_path}'")
            command_str_for_log = ' '.join(shlex.quote(c) for c in cmd_list) # For logging

            # Use the safe_run utility function to execute the Git command.
            # safe_run handles subprocess.run with consistent encoding and error handling.
            # It also accepts a timeout, if implemented in safe_run or passed via kwargs.
            # For Git operations, a timeout might be beneficial.
            # Example: timeout = self.config.get("git", {}).get("command_timeout", 60)
            git_process_result = safe_run(cmd_list, cwd=repo_dir_path) # Pass cwd to safe_run

            if git_process_result.returncode == 0:
                # Command executed successfully
                result_message = f"Git {command_name}: Successful."
                
                # For certain commands that modify repository state or fetch new info,
                # queue an update for the editor's Git information display.
                if command_name.lower() in ["pull", "commit", "push", "fetch", "merge", "rebase", "checkout", "reset"]:
                    try:
                        self._git_cmd_q.put("request_git_info_update") # Special message type
                        logging.debug(f"Async Git command: Queued 'request_git_info_update' after successful 'git {command_name}'.")
                    except queue.Full:
                        logging.error("Async Git command: _git_cmd_q full, could not queue git info update request.")

                # Append a summary of stdout if it's not empty (useful for commands like status, diff, log)
                stdout_content = git_process_result.stdout.strip()
                if stdout_content:
                    first_line_of_stdout = stdout_content.splitlines()[0] if stdout_content else ""
                    # Truncate for status bar display
                    summary_preview = (first_line_of_stdout[:90] + "..." 
                                       if len(first_line_of_stdout) > 90 or '\n' in stdout_content # Check if multiline
                                       else first_line_of_stdout[:90])
                    result_message += f" Output: {summary_preview}"
                    # Full output could be logged or made available through another mechanism if needed.
                    logging.debug(f"Async Git command 'git {command_name}' stdout:\n{stdout_content}")
            else:
                # Command failed (non-zero exit code)
                stderr_content = git_process_result.stderr.strip()
                error_summary = stderr_content.splitlines()[0] if stderr_content else "(no stderr output)"
                result_message = (
                    f"Git {command_name} error (code {git_process_result.returncode}): "
                    f"{error_summary[:100]}"
                )
                if len(error_summary) > 100 or '\n' in stderr_content:
                    result_message += "..."
                logging.error(
                    f"Async Git command 'git {command_name}' (in '{repo_dir_path}') failed. "
                    f"Exit code: {git_process_result.returncode}. Stderr:\n{stderr_content}"
                )

        except FileNotFoundError: # If 'git' executable itself is not found
            result_message = "Git error: 'git' executable not found. Please ensure Git is installed and in your PATH."
            logging.error(result_message)
        except subprocess.TimeoutExpired: # If safe_run or subprocess.run has a timeout that expires
            result_message = f"Git {command_name}: Command timed out."
            logging.warning(result_message + f" Command: {command_str_for_log if 'command_str_for_log' in locals() else 'Unknown'}")
        except Exception as e_git_exec: # Catch any other unexpected exceptions
            command_str_for_log_err = ' '.join(shlex.quote(c) for c in cmd_list) if 'cmd_list' in locals() else "Unknown Git command"
            logging.exception(f"Unexpected error during async Git command '{command_name}' ({command_str_for_log_err})")
            result_message = f"Git {command_name} error: {str(e_git_exec)[:80]}..."

        # Send the final result message to the Git command queue for processing by the main thread.
        try:
             self._git_cmd_q.put(result_message)
             logging.debug(f"Async Git command: Result message queued: '{result_message}'")
        except queue.Full:
             logging.error(f"Async Git command: _git_cmd_q is full. Dropping result: '{result_message}'")
        except Exception as e_queue_put:
             logging.error(f"Async Git command: Failed to put result into _git_cmd_q: {e_queue_put}", exc_info=True)


    # Methods that are primarily UI or async triggers - they set status, so return True
    def integrate_git(self) -> bool:
        """
        Shows the Git menu, prompts for a Git command, and executes it asynchronously.
        Returns True if a status message was set or an interaction occurred, 
        indicating a potential need for redraw. False otherwise (e.g., Git disabled).
        """
        logging.debug("integrate_git called")
        original_status = self.status_message
        redraw_needed = False

        try:
            # Check if Git integration is enabled in the config
            if not self.config.get("git", {}).get("enabled", True):
                self._set_status_message("Git integration is disabled in config.")
                logging.debug("Git menu called but integration is disabled.")
                return self.status_message != original_status # Redraw if status changed

            # Determine the repository directory
            repo_dir = os.path.dirname(self.filename) if self.filename and os.path.exists(self.filename) else os.getcwd()
            
            # Check if the determined path is actually a Git repository
            if not os.path.isdir(os.path.join(repo_dir, ".git")):
                self._set_status_message("Not a Git repository.")
                logging.debug(f"Git menu called but {repo_dir} is not a git repository.")
                return self.status_message != original_status

            commands = {
                "1": ("status", ["git", "status", "--short"]),
                "2": ("commit", None),  # Commit message is prompted separately
                "3": ("push",   ["git", "push"]),
                "4": ("pull",   ["git", "pull"]),
                "5": ("diff",   ["git", "diff", "--no-color", "--unified=0"]),
                # Add more commands as needed, e.g., log, branch, checkout
            }

            # Construct the options string for the prompt
            opts_str_parts = []
            for k, v_tuple in commands.items(): # Iterate over items correctly
                opts_str_parts.append(f"{k}:{v_tuple[0]}")
            opts_str = " ".join(opts_str_parts)
            
            # Prompt user for a Git command choice
            # The prompt itself will change the status message temporarily
            prompt_message = f"Git menu [{opts_str}] → "
            current_status_before_prompt = self.status_message
            choice = self.prompt(prompt_message)
            if self.status_message != current_status_before_prompt:
                redraw_needed = True


            if not choice or choice not in commands:
                # If prompt was cancelled or choice is invalid, set status and indicate redraw if status changed
                if not choice and not redraw_needed : # If prompt was cancelled without changing status line by itself
                     self._set_status_message("Git menu cancelled")
                     redraw_needed = True
                elif choice and choice not in commands:
                     self._set_status_message("Invalid Git command choice")
                     redraw_needed = True
                else: # Status was already changed by prompt, or no change needed
                    pass
                logging.debug(f"Git menu cancelled or invalid choice: {choice}")
                return redraw_needed

            command_name, cmd_args_template = commands[choice]
            cmd_list_to_run = [] # Initialize to an empty list

            if command_name == "commit":
                # Special handling for commit, as it requires a message
                current_status_before_commit_prompt = self.status_message
                commit_msg = self.prompt("Commit message: ")
                if self.status_message != current_status_before_commit_prompt:
                    redraw_needed = True

                if not commit_msg: # User cancelled commit message input
                    self._set_status_message("Commit cancelled (no message)")
                    logging.debug("Git commit cancelled: no message provided.")
                    return True # Status changed
                
                # Construct the commit command list
                # Using -a to stage all modified/deleted files, and -m for message
                cmd_list_to_run = ["git", "commit", "-a", "-m", commit_msg]
            elif cmd_args_template is not None:
                # For other commands, use the predefined argument list template
                cmd_list_to_run = list(cmd_args_template) # Create a copy

            # Execute the command if cmd_list_to_run is populated
            if cmd_list_to_run:
                thread_name = f"GitExecThread-{command_name}-{int(time.time())}"
                threading.Thread(
                    target=self._run_git_command_async,
                    args=(cmd_list_to_run, command_name),
                    daemon=True,
                    name=thread_name
                ).start()
                # Set a temporary status message; the actual result will come from the queue
                self._set_status_message(f"Running git {command_name}...")
                redraw_needed = True # Status message changed
                logging.debug(f"Started Git command thread: {thread_name} for {cmd_list_to_run}")
            else:
                # This case should not be reached if commands dictionary is well-defined
                logging.warning(f"Git menu: No command list generated for choice '{choice}' (command_name: '{command_name}')")
                if self.status_message == original_status and not redraw_needed:
                    self._set_status_message("Git menu internal error: command not prepared.")
                    redraw_needed = True
            
            return redraw_needed

        except Exception as e:
            logging.error(f"Error in integrate_git: {e}", exc_info=True)
            self._set_status_message("Git menu error (see log)")
            return True # Status changed due to error

    
    # ==================== GIT INFO ===============================       
    def _fetch_git_info_async(self, file_path_context: Optional[str]) -> None:
        """
        Fetches Git repository information (branch, user, commit count, dirty status)
        asynchronously in a separate thread.
        The result tuple (branch_name, user_name, commit_count_str) is put into self._git_q.

        Args:
            file_path_context (Optional[str]): The path to the current file, used to determine
                                             the Git repository context. Can be None if no file
                                             is currently associated with the buffer.
        """
        branch_name: str = ""
        user_name_git: str = ""
        commit_count_str: str = "0" # Default to "0" if not found or error
        
        repo_root_dir: Optional[str] = None

        try:
            # 1. Determine the starting directory for finding the Git repository.
            start_dir_for_search: str
            if file_path_context and os.path.isfile(file_path_context):
                start_dir_for_search = os.path.dirname(os.path.abspath(file_path_context))
            else:
                start_dir_for_search = os.getcwd()

            # 2. Find the root of the Git repository.
            current_search_dir = start_dir_for_search
            while True:
                if os.path.isdir(os.path.join(current_search_dir, ".git")):
                    repo_root_dir = current_search_dir
                    break
                parent_dir = os.path.dirname(current_search_dir)
                if parent_dir == current_search_dir: 
                    break
                current_search_dir = parent_dir
            
            if not repo_root_dir:
                logging.debug(f"_fetch_git_info_async: No .git directory found upwards from '{start_dir_for_search}'.")
                self._git_q.put(("", "", "0"))
                return

            logging.debug(f"_fetch_git_info_async: Found Git repository root at: '{repo_root_dir}'")

            # --- Helper for running git commands (uses self.safe_run if it's part of the class, or global safe_run) ---
            # Assuming safe_run is accessible. If it's a method of SwayEditor, use self.safe_run
            # If it's a global function, use safe_run directly.
            # Based on your initial code, safe_run is a global function.
            def run_git_cmd_local(cmd_parts: List[str], timeout_secs: int = 5) -> subprocess.CompletedProcess:
                return safe_run(cmd_parts, cwd=repo_root_dir, timeout=timeout_secs)

            # 3. Determine the current branch name.
            current_git_cmd_for_error: List[str] = [] # For error reporting
            try:
                current_git_cmd_for_error = ["git", "branch", "--show-current"]
                branch_result = run_git_cmd_local(current_git_cmd_for_error)
                if branch_result.returncode == 0 and branch_result.stdout.strip():
                    branch_name = branch_result.stdout.strip()
                else: 
                    raise subprocess.CalledProcessError(
                        branch_result.returncode, current_git_cmd_for_error, 
                        output=branch_result.stdout, stderr=branch_result.stderr
                    )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                try:
                    current_git_cmd_for_error = ["git", "symbolic-ref", "--short", "HEAD"]
                    branch_result = run_git_cmd_local(current_git_cmd_for_error)
                    if branch_result.returncode == 0 and branch_result.stdout.strip():
                         branch_name = branch_result.stdout.strip()
                    else: 
                        raise subprocess.CalledProcessError(
                            branch_result.returncode, current_git_cmd_for_error, 
                            output=branch_result.stdout, stderr=branch_result.stderr
                        )
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    try:
                        current_git_cmd_for_error = ["git", "rev-parse", "--short", "HEAD"]
                        commit_hash_result = run_git_cmd_local(current_git_cmd_for_error)
                        if commit_hash_result.returncode == 0 and commit_hash_result.stdout.strip():
                            branch_name = commit_hash_result.stdout.strip()[:7]
                        else:
                            branch_name = "detached" 
                    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                        branch_name = "detached/err"
                        logging.warning(f"Git: Could not determine branch or detached HEAD in '{repo_root_dir}'.")
                    except Exception as e_rev_parse:
                        logging.error(f"Git: Unexpected error getting detached HEAD in '{repo_root_dir}': {e_rev_parse}", exc_info=True)
                        branch_name = "error"
                except Exception as e_sym_ref:
                    logging.error(f"Git: Unexpected error getting branch via symbolic-ref in '{repo_root_dir}': {e_sym_ref}", exc_info=True)
                    branch_name = "error"
            except FileNotFoundError: 
                 logging.warning("Git executable not found during async branch check.")
                 self._git_q.put(("", "", "0"))
                 return
            except Exception as e_branch_initial:
                logging.error(f"Git: Unexpected error getting initial branch info in '{repo_root_dir}': {e_branch_initial}", exc_info=True)
                branch_name = "error"

            # 4. Check if the repository is dirty.
            try:
                dirty_check_result = run_git_cmd_local(["git", "status", "--porcelain", "--ignore-submodules"])
                if dirty_check_result.returncode == 0 and dirty_check_result.stdout.strip():
                    if '*' not in branch_name: 
                         branch_name += "*"
                    logging.debug(f"Git: Repository '{repo_root_dir}' is dirty.")
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e_status:
                logging.warning(f"Git: 'status --porcelain' failed or timed out in '{repo_root_dir}': {e_status}")
            except FileNotFoundError: # Should be caught above
                 pass
            except Exception as e_status_other:
                logging.error(f"Git: Unexpected error getting status in '{repo_root_dir}': {e_status_other}", exc_info=True)

            # 5. Get Git user name.
            try:
                user_name_result = run_git_cmd_local(["git", "config", "user.name"])
                if user_name_result.returncode == 0 and user_name_result.stdout.strip():
                    user_name_git = user_name_result.stdout.strip()
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e_user:
                 logging.debug(f"Git: Could not get user.name in '{repo_root_dir}': {e_user}")
                 user_name_git = "" 
            except FileNotFoundError: # Should be caught above
                 pass
            except Exception as e_user_other:
                logging.error(f"Git: Unexpected error getting user.name in '{repo_root_dir}': {e_user_other}", exc_info=True)
                user_name_git = "error"

            # 6. Get commit count on the current HEAD.
            try:
                commit_count_result = run_git_cmd_local(["git", "rev-list", "--count", "HEAD"])
                if (commit_count_result.returncode == 0 and 
                    commit_count_result.stdout.strip() and 
                    commit_count_result.stdout.strip().isdigit()):
                    commit_count_str = commit_count_result.stdout.strip()
                else:
                    commit_count_str = "0" 
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e_commits:
                logging.debug(f"Git: 'rev-list --count HEAD' failed or timed out in '{repo_root_dir}': {e_commits}")
                commit_count_str = "0" 
            except FileNotFoundError: # Should be caught above
                 pass
            except Exception as e_commits_other:
                logging.error(f"Git: Unexpected error getting commit count in '{repo_root_dir}': {e_commits_other}", exc_info=True)
                commit_count_str = "error"

        except FileNotFoundError: 
            logging.warning("Git executable not found (initial check in _fetch_git_info_async). No Git info will be fetched.")
            branch_name, user_name_git, commit_count_str = "", "", "0"
        except Exception as e_outer: 
            logging.exception(f"Outer error fetching Git info in async thread for context '{file_path_context}': {e_outer}")
            branch_name, user_name_git, commit_count_str = "fetch_error", "", "0"

        # 7. Queue the fetched Git information.
        try:
            self._git_q.put((branch_name, user_name_git, commit_count_str))
            logging.debug(f"Async Git info fetched and queued: Branch='{branch_name}', User='{user_name_git}', Commits='{commit_count_str}'")
        except queue.Full:
            logging.error("Git info queue (_git_q) is full. Dropping fetched Git info.")
        except Exception as e_queue:
            logging.error(f"Failed to put fetched Git info into queue: {e_queue}", exc_info=True)


    def _handle_git_info(self, git_data):
        """
        Обрабатывает и форматирует информацию о git для статус-бара.
        git_data: tuple (branch, user, commits)
        """
        with self._state_lock:
            self.git_info = git_data

        branch, user, commits = git_data
        git_status_msg = ""
        if branch or user or commits != "0":
            # Определяем цвет для Git инфо в статус-баре
            git_color = self.colors.get("git_info", curses.color_pair(12))
            if '*' in branch:
                git_color = self.colors.get("git_dirty", curses.color_pair(13))
            # Форматируем строку статуса Git
            git_status_msg = f"Git: {branch}"
            if commits != "0":
                git_status_msg += f" ({commits} commits)"
            # Можно добавить user по желанию:
            # if user: git_status_msg += f" by {user}"
            # Удаляем неотображаемые символы
            git_status_msg = ''.join(c if c.isprintable() else '?' for c in git_status_msg)
            # Сохраняем статус, можно также отрисовать в статус-бар
            self.status_message = git_status_msg
            logging.debug(f"Git status updated: {git_status_msg}")
        else:
            self.status_message = "Git: (no repo)"
            logging.debug("Git status updated: no repo found.")


    # === GOTO LINE ============================================================
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
        
        status_after_prompt = self.status_message # Status might have been restored by prompt's finally block

        if not raw_input_str: # User cancelled the prompt (e.g., pressed Esc or Enter on empty input)
            # If the prompt itself set a new status (e.g. "Prompt timeout"), that's a change.
            # If prompt restored original_status, but user cancelled, set "Goto cancelled".
            if status_after_prompt == original_status:
                self._set_status_message("Goto cancelled")
            # Return True if status message is different from what it was *before* the prompt.
            return self.status_message != original_status

        target_line_num_one_based: Optional[int] = None
        total_lines = len(self.text)
        if total_lines == 0: # Should not happen if self.text always has at least [""]
            self._set_status_message("Cannot go to line: buffer is empty")
            return self.status_message != original_status

        try:
            if raw_input_str.endswith('%'):
                percentage_str = raw_input_str.rstrip('%')
                if not percentage_str: # Just '%' was entered
                    raise ValueError("Percentage value missing.")
                percentage = float(percentage_str)
                if not (0 <= percentage <= 100):
                    self._set_status_message("Percentage out of range (0-100)")
                    return True # Status changed
                # Calculate target line (1-based), ensuring it's within [1, total_lines]
                # round() handles .5 by rounding to the nearest even number in Python 3.
                # int(val + 0.5) is a common way to round half up for positive numbers.
                # For percentages, simple rounding is usually fine.
                target_line_num_one_based = max(1, min(total_lines, round(total_lines * percentage / 100.0)))
                if target_line_num_one_based == 0 and total_lines > 0 : target_line_num_one_based = 1 # Ensure at least line 1
                logging.debug(f"Goto: Percentage {percentage}%, target line {target_line_num_one_based}")
            elif raw_input_str.startswith(('+', '-')):
                if len(raw_input_str) == 1: # Just '+' or '-' was entered
                    raise ValueError("Relative offset value missing.")
                relative_offset = int(raw_input_str)
                # Current line is 0-based (self.cursor_y), target is 1-based
                target_line_num_one_based = (self.cursor_y + 1) + relative_offset
                logging.debug(f"Goto: Relative offset {relative_offset}, from line {self.cursor_y + 1}, target line {target_line_num_one_based}")
            else:
                target_line_num_one_based = int(raw_input_str)
                logging.debug(f"Goto: Absolute target line {target_line_num_one_based}")

            # Validate the calculated target_line_num_one_based
            if target_line_num_one_based is None: # Should not happen if parsing logic is complete
                 raise ValueError("Line number could not be determined.")
            if not (1 <= target_line_num_one_based <= total_lines):
                self._set_status_message(f"Line number out of range (1–{total_lines})")
                return True # Status changed

            # Convert 1-based target to 0-based for internal use
            target_y_zero_based = target_line_num_one_based - 1

            # Only proceed if the target is different from the current line
            if target_y_zero_based == self.cursor_y and self.cursor_x == min(self.cursor_x, len(self.text[target_y_zero_based])):
                 # If already on the target line and x is valid for it (or will be clamped to valid)
                 # No actual cursor line change, but check if x needs clamping or status needs update.
                 self.cursor_x = min(self.cursor_x, len(self.text[target_y_zero_based])) # Ensure x is valid
                 self._clamp_scroll() # Ensure scroll is correct for current position
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
            
            self._clamp_scroll() # Adjust scroll to make the new cursor position visible

            # Check if cursor or scroll actually changed
            if (self.cursor_y, self.cursor_x) != original_cursor_pos or \
               self.scroll_top != original_scroll_top:
                self._set_status_message(f"Moved to line {target_line_num_one_based}")
                return True
            else:
                # This case should be rare if logic above for "already at line" is correct.
                # It means target was same as current, and clamp_scroll did nothing.
                # However, the prompt was shown.
                if status_after_prompt != original_status: # If prompt itself set a lasting status
                    return True 
                # If prompt restored status, but we set a new one (e.g. "already at line")
                self._set_status_message(f"At line {target_line_num_one_based} (no change)")
                return self.status_message != original_status

        except ValueError as ve: # Handles errors from int(), float(), or custom raises
            logging.warning(f"Goto: Invalid input format '{raw_input_str}': {ve}")
            self._set_status_message(f"Invalid format: {raw_input_str[:30]}")
            return True # Status changed due to error message
        except Exception as e: # Catch any other unexpected errors
            logging.error(f"Unexpected error in goto_line for input '{raw_input_str}': {e}", exc_info=True)
            self._set_status_message(f"Goto error: {str(e)[:60]}...")
            return True # Status changed due to error message

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
        original_status = self.status_message # For robust True/False return
        original_insert_mode = self.insert_mode

        self.insert_mode = not self.insert_mode # Toggle the mode

        mode_text_indicator = "Insert" if self.insert_mode else "Replace"
        
        logging.debug(f"Insert mode toggled. New mode: {mode_text_indicator}")
        self._set_status_message(f"Mode: {mode_text_indicator}")
        
        # Return True if the mode actually changed or if the status message changed.
        # Since _set_status_message is always called with a new mode indicator,
        # the status message will almost certainly change unless it was already displaying
        # the exact same "Mode: ..." message (highly unlikely for a toggle).
        if self.insert_mode != original_insert_mode or self.status_message != original_status:
            return True
        
        return False # Should be rare, e.g. if status somehow didn't update to the new mode text


    # ==================== bracket =======================
    # метод, предназначенный для поиска парной скобки по нескольким строкам.
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
            return None # Initial position is out of bounds

        char_at_cursor = self.text[initial_char_y][initial_char_x]
        
        brackets_map = {"(": ")", "{": "}", "[": "]", ")": "(", "}": "{", "]": "["}
        open_brackets = "({["
        # close_brackets = ")}]" # Not directly used in this simplified search logic a lot

        if char_at_cursor not in brackets_map:
            return None # Character at cursor is not a bracket we handle

        target_match_char = brackets_map[char_at_cursor]
        level = 1 # Start at level 1, looking for the char that brings it to 0

        if char_at_cursor in open_brackets:
            # Search forward for the closing bracket
            current_y, current_x = initial_char_y, initial_char_x + 1
            while current_y < len(self.text):
                line = self.text[current_y]
                while current_x < len(line):
                    char = line[current_x]
                    if char == char_at_cursor: # Found another opening bracket of the same type
                        level += 1
                    elif char == target_match_char: # Found a potential matching closing bracket
                        level -= 1
                        if level == 0:
                            return (current_y, current_x) # Match found
                    current_x += 1
                current_y += 1
                current_x = 0 # Reset column for the new line
        else: # char_at_cursor is a closing bracket, search backward for the opening one
            current_y, current_x = initial_char_y, initial_char_x - 1
            while current_y >= 0:
                line = self.text[current_y]
                # If current_x became -1 from previous line, adjust to end of this line
                if current_x < 0 : current_x = len(line) -1 
                
                while current_x >= 0:
                    char = line[current_x]
                    if char == char_at_cursor: # Found another closing bracket of the same type
                        level += 1
                    elif char == target_match_char: # Found a potential matching opening bracket
                        level -= 1
                        if level == 0:
                            return (current_y, current_x) # Match found
                    current_x -= 1
                current_y -= 1
                # For the next line (previous one), start searching from its end.
                # current_x will be set to len(line) - 1 at the start of the inner loop.
                # No explicit current_x reset needed here as it's handled by inner loop init/condition
        
        return None # No match found


    def highlight_matching_brackets(self) -> None:
        """
        Highlights the bracket at the cursor and its matching pair, searching across lines.
        The highlighting is applied directly to the screen using stdscr.chgat.
        It relies on `find_matching_bracket_multiline` to find the pair.
        """
        # 1. Ensure cursor is within text and visible on screen.
        term_height, term_width = self.stdscr.getmaxyx()
        # visible_lines is typically term_height - 2 (for status bar, line numbers)
        # Ensure self.visible_lines is correctly maintained by the editor.
        
        if not (0 <= self.cursor_y < len(self.text)):
            logging.debug("highlight_matching_brackets: Cursor Y out of text bounds.")
            return

        # Check if the cursor's line is visible (vertically scrolled into view)
        if not (self.scroll_top <= self.cursor_y < self.scroll_top + self.visible_lines):
            logging.debug("highlight_matching_brackets: Cursor's line is not currently visible on screen.")
            return

        current_line_text = self.text[self.cursor_y]
        if not current_line_text: # Empty line
            logging.debug("highlight_matching_brackets: Cursor is on an empty line.")
            return

        # 2. Determine the bracket character at/near the cursor.
        # If cursor is at the end of the line, check the char before it.
        # If cursor is on a char, check that char.
        char_y, char_x = self.cursor_y, self.cursor_x
        
        if char_x >= len(current_line_text): # Cursor is just after the last character
            if char_x > 0 : # Try character to the left of cursor if at EOL
                char_x_to_check = char_x -1
                if current_line_text[char_x_to_check] in ")]}": # Only look for closing if at EOL
                     char_x = char_x_to_check
                else: return # Not on/after a closing bracket at EOL
            else: return # Empty line or cursor at 0 on empty line

        if char_x < 0 : return # Should not happen with valid cursor_x

        # Check if char_x is now valid for the possibly adjusted char_x
        if char_x >= len(current_line_text):
            return


        bracket_char_at_cursor = self.text[char_y][char_x]
        brackets_to_match = "(){}[]"
        if bracket_char_at_cursor not in brackets_to_match:
            # If not directly on a bracket, check char to the left (common for closing bracket highlighting)
            if char_x > 0:
                char_x_left = char_x -1
                char_left_of_cursor = self.text[char_y][char_x_left]
                if char_left_of_cursor in brackets_to_match:
                    char_x = char_x_left # Use the bracket to the left
                    bracket_char_at_cursor = char_left_of_cursor
                else:
                    logging.debug(f"highlight_matching_brackets: No bracket at or immediately left of cursor ({char_y},{self.cursor_x}).")
                    return
            else:
                logging.debug(f"highlight_matching_brackets: No bracket at cursor ({char_y},{char_x}) and no char to the left.")
                return
        
        # At this point, (char_y, char_x) points to a bracket character.

        # 3. Find the matching bracket using the multiline finder.
        match_coords = self.find_matching_bracket_multiline(char_y, char_x)
        if not match_coords:
            logging.debug(f"highlight_matching_brackets: No matching bracket found for '{bracket_char_at_cursor}' at ({char_y},{char_x}).")
            return # No pair found

        match_y, match_x = match_coords
        
        # Ensure matched coordinates are valid (should be, if find_matching_bracket_multiline is correct)
        if not (0 <= match_y < len(self.text) and 0 <= match_x < len(self.text[match_y])):
            logging.warning(f"highlight_matching_brackets: Matching bracket coords ({match_y},{match_x}) out of bounds.")
            return

        # 4. Calculate screen coordinates for both brackets.
        # This helper needs to be part of the class or defined locally with access to self.
        # Assuming line_num_width is available or calculated (e.g., self._text_start_x from DrawScreen)
        line_num_display_width = getattr(self.drawer, '_text_start_x', len(str(len(self.text))) + 1)

        def get_screen_coords(r_idx: int, c_idx: int) -> Optional[Tuple[int, int]]:
            """Calculates screen (y,x) for a text coordinate (r_idx, c_idx)."""
            if not (self.scroll_top <= r_idx < self.scroll_top + self.visible_lines):
                return None # Row is not visible

            screen_y_coord = r_idx - self.scroll_top
            
            # Calculate display width of the part of the line before the character
            # This is the character's logical start column on screen, before horizontal scroll.
            char_logical_screen_x_unscrolled = self.editor.get_string_width(self.text[r_idx][:c_idx])
            
            # Apply line number width and horizontal scroll
            screen_x_coord = line_num_display_width + char_logical_screen_x_unscrolled - self.scroll_left
            
            # Check if horizontally visible
            char_display_width = self.editor.get_char_width(self.text[r_idx][c_idx])
            if screen_x_coord + char_display_width <= line_num_display_width or screen_x_coord >= term_width:
                return None # Character is not horizontally visible
                
            return screen_y_coord, max(line_num_display_width, screen_x_coord) # Clamp to text area start

        coords1_on_screen = get_screen_coords(char_y, char_x)
        coords2_on_screen = get_screen_coords(match_y, match_x)

        # 5. Highlight both brackets if they are visible on screen.
        # Use A_BOLD or A_REVERSE or a specific color pair for highlighting.
        highlight_attr = curses.A_REVERSE # Example attribute

        highlight_applied = False
        if coords1_on_screen:
            scr_y1, scr_x1 = coords1_on_screen
            char1_width = self.editor.get_char_width(self.text[char_y][char_x])
            if scr_x1 < term_width and char1_width > 0 : # Ensure it's within bounds and has width
                try:
                    self.stdscr.chgat(scr_y1, scr_x1, char1_width, highlight_attr)
                    highlight_applied = True
                    logging.debug(f"Highlighted bracket 1 at screen ({scr_y1},{scr_x1}), text ({char_y},{char_x})")
                except curses.error as e:
                    logging.warning(f"Curses error highlighting bracket 1 at screen ({scr_y1},{scr_x1}): {e}")
        
        if coords2_on_screen:
            scr_y2, scr_x2 = coords2_on_screen
            char2_width = self.editor.get_char_width(self.text[match_y][match_x])
            if scr_x2 < term_width and char2_width > 0:
                try:
                    self.stdscr.chgat(scr_y2, scr_x2, char2_width, highlight_attr)
                    highlight_applied = True
                    logging.debug(f"Highlighted bracket 2 at screen ({scr_y2},{scr_x2}), text ({match_y},{match_x})")
                except curses.error as e:
                    logging.warning(f"Curses error highlighting bracket 2 at screen ({scr_y2},{scr_x2}): {e}")
        
        # if highlight_applied:
            # self.stdscr.refresh() # The main draw loop should handle refresh.
            # No need to refresh here, as this is usually called within the main draw cycle.

        # Важные моменты и дальнейшие улучшения:
        # Игнорирование строк и комментариев в find_matching_bracket_multiline: Это самое большое ограничение текущей реализации. Для полноценной работы в редакторе кода необходимо добавить логику, которая бы пропускала скобки внутри строковых литералов (например, "this is a (string)") и комментариев (например, /* a (comment) */ или # a (comment)). Это можно сделать:
        # Простым конечным автоматом: Отслеживать состояние "внутри строки", "внутри однострочного комментария", "внутри многострочного комментария" во время сканирования.
        # Использованием Pygments: Если возможно, получить информацию о токенах для сканируемых строк и проверять тип токена (Comment.*, String.*), прежде чем обрабатывать скобку. Это может быть медленно, если токенизировать каждую строку на лету во время поиска.
        # Производительность find_matching_bracket_multiline: Для очень больших файлов сканирование большого количества строк может быть медленным. Можно ограничить глубину поиска (например, не более N строк вверх/вниз).
        # line_num_display_width: В highlight_matching_brackets я предположил, что ширина области номеров строк (line_num_display_width) доступна, например, через self.drawer._text_start_x. Убедитесь, что это значение корректно получается.
        # Обновление экрана: highlight_matching_brackets применяет chgat напрямую. Этот метод обычно вызывается внутри основного цикла отрисовки DrawScreen.draw(), поэтому отдельный refresh() здесь не нужен.


    # ==================== HELP ==================================
    # Displays a help window with keybindings and editor features.
    def show_help(self) -> bool:
        """
        Displays a pop-up help window with keybindings.
        This function takes over the screen for the duration of the help display.
        It always returns True, indicating that the screen state has changed (at least temporarily)
        and a redraw by the main loop (after help closes) is beneficial to ensure consistency,
        and also because it sets a status message.
        """
        logging.debug("show_help called")
        original_status = self.status_message # Store to see if it needs to be restored or if it changes
        
        # Help text lines - ensure they are reasonably formatted for a terminal window
        # Using key names as defined in config or defaults for better user understanding.
        # This would be even better if dynamically generated from self.keybindings,
        # but for now, a static list is simpler.

        # Retrieve key strings from config for major functions to display in help
        kb = self.config.get("keybindings", {})
        
        # Helper to format keybinding string for display
        def get_kb_display(action_name: str, default_key: str) -> str:
            key_str = kb.get(action_name, default_key)
            if not key_str: return "Disabled"
            # Make it more readable, e.g., "ctrl+s" -> "Ctrl+S"
            parts = key_str.lower().split('+')
            formatted_parts = []
            for part in parts:
                if part in ["ctrl", "alt", "shift"]:
                    formatted_parts.append(part.capitalize())
                elif len(part) == 1 and 'a' <= part <= 'z':
                    formatted_parts.append(part.upper())
                elif part.startswith("f") and part[1:].isdigit():
                     formatted_parts.append(part.upper())
                else: # del, esc, tab etc.
                    formatted_parts.append(part.capitalize() if len(part) > 1 else part)
            return "+".join(formatted_parts)

        help_lines = [
            "  ──  Sway-Pad Help  ──  ",
            "",
            "  File Operations:",
            f"    {get_kb_display('new_file', 'F2'):<18}: New file",
            f"    {get_kb_display('open_file', 'Ctrl+O'):<18}: Open file",
            f"    {get_kb_display('save_file', 'Ctrl+S'):<18}: Save",
            f"    {get_kb_display('save_as', 'F5'):<18}: Save as…",
            f"    {get_kb_display('quit', 'Ctrl+Q'):<18}: Quit editor",
            "",
            "  Editing:",
            f"    {get_kb_display('undo', 'Ctrl+Z'):<18}: Undo",
            f"    {get_kb_display('redo', 'Shift+Z'):<18}: Redo",
            f"    {get_kb_display('copy', 'Ctrl+C'):<18}: Copy",
            f"    {get_kb_display('cut', 'Ctrl+X'):<18}: Cut",
            f"    {get_kb_display('paste', 'Ctrl+V'):<18}: Paste",
            f"    {get_kb_display('select_all', 'Ctrl+A'):<18}: Select all",
            f"    {get_kb_display('delete', 'Del'):<18}: Delete char/selection",
            "    Backspace            : Delete char left / selection",
            "",
            "  Navigation & Search:",
            f"    {get_kb_display('goto_line', 'Ctrl+G'):<18}: Go to line",
            f"    {get_kb_display('find', 'Ctrl+F'):<18}: Find (prompt)",
            f"    {get_kb_display('find_next', 'F3'):<18}: Find next occurrence",
            f"    {get_kb_display('search_and_replace', 'F6'):<18}: Search & Replace (regex)",
            "    Arrows, Home, End    : Cursor movement",
            "    PageUp, PageDown     : Scroll by page",
            "    Shift+Nav Keys       : Extend selection",
            "",
            "  Tools & Features:",
            f"    {get_kb_display('lint', 'F4'):<18}: Run Linter (Python)",
            f"    {get_kb_display('git_menu', 'F9'):<18}: Git menu",
            f"    {get_kb_display('tab', 'Tab'):<18}: Smart Tab / Indent block",
            f"    {get_kb_display('shift_tab', 'Shift+Tab'):<18}: Smart Unindent / Unindent block",
            f"    {get_kb_display('comment_selected_lines', 'Ctrl+/'):<18}: Comment block/line",
            f"    {get_kb_display('uncomment_selected_lines', 'Shift+/'):<18}: Uncomment block/line",
            f"    {get_kb_display('help', 'F1'):<18}: This help screen",
            f"    {get_kb_display('cancel_operation', 'Esc'):<18}: Cancel operation / selection",
            "    Insert Key           : Toggle Insert/Replace mode",
            "",
            "  ──────────────────────────",
            "    © 2025 Siergej Sobolewski — Sway-Pad",
            "    Licensed under the GPLv3",
            "",
            "    Press any key to close this help window.",
        ]

        # Calculate dimensions for the help window
        # Add padding for border and internal margins
        num_lines_text = len(help_lines)
        help_window_height = num_lines_text + 2 # +1 top border, +1 bottom border
        
        # Calculate max width needed for text lines + padding
        # Ensure self.get_string_width is robust for this context
        try:
            max_text_line_width = 0
            for line_in_help in help_lines:
                 # Using a basic len for width calculation if get_string_width is problematic here
                 # or assuming help text is mostly ASCII for simplicity in this calculation.
                 # For full Unicode width, self.editor.get_string_width would be better.
                 line_in_help_width = len(line_in_help) # Fallback if get_string_width fails
                 if hasattr(self.editor, 'get_string_width'):
                     try:
                         line_in_help_width = self.editor.get_string_width(line_in_help)
                     except Exception:
                         pass # Use len as fallback
                 max_text_line_width = max(max_text_line_width, line_in_help_width)
        except Exception:
            max_text_line_width = 70 # Fallback width

        help_window_width = max_text_line_width + 4 # +2 left_padding/border, +2 right_padding/border
        
        # Get terminal dimensions
        term_height, term_width = self.stdscr.getmaxyx()

        # Ensure help window fits within the terminal
        help_window_height = min(help_window_height, term_height - 2) # Leave space if terminal is too small
        help_window_width = min(help_window_width, term_width - 2)

        # Calculate top-left position for centering the help window
        start_y_pos = max(0, (term_height - help_window_height) // 2)
        start_x_pos = max(0, (term_width - help_window_width) // 2)

        help_win = None # curses window object for help

        try:
            # Create a new window for the help screen
            help_win = curses.newwin(help_window_height, help_window_width, start_y_pos, start_x_pos)
            help_win.bkgd(' ', self.colors.get("status", curses.color_pair(0)|curses.A_REVERSE)) # Use a distinct background
            help_win.border() # Draw a border around the help window

            # Display the help text, line by line
            # Text starts at row 1, col 2 inside the help_win (due to border and padding)
            for i, text_line in enumerate(help_lines):
                if i >= help_window_height - 2: # Stop if text exceeds window height (after borders)
                    break
                # Truncate text line if it's wider than the drawable area in the help window
                drawable_text_width = help_window_width - 4 # -2 for left border/pad, -2 for right
                
                # Use a simpler truncation for help text, or self.truncate_string if available and robust
                display_line = text_line
                if hasattr(self.editor, 'truncate_string'):
                    display_line = self.editor.truncate_string(text_line, drawable_text_width)
                else: # Basic truncation
                    if len(text_line) > drawable_text_width: # Approximation
                        display_line = text_line[:drawable_text_width]

                try:
                    help_win.addstr(i + 1, 2, display_line)
                except curses.error as e_addstr:
                    logging.warning(f"Curses error drawing help text line {i} ('{display_line}'): {e_addstr}")
                    # Continue trying to draw other lines if one fails

            # Refresh the help window to show it on screen (non-blocking)
            help_win.noutrefresh()

        except curses.error as e_newwin:
            logging.error(f"Curses error creating or drawing help window: {e_newwin}")
            self._set_status_message(f"Error displaying help: {str(e_newwin)[:70]}...")
            if help_win: del help_win # Clean up if partially created
            return True # Status message changed

        # Hide the main editor cursor while help is shown
        previous_cursor_visibility = 1 # Default to visible
        try:
            previous_cursor_visibility = curses.curs_set(0) # 0 = invisible
        except curses.error:
            logging.warning("Curses: Could not hide cursor for help screen (terminal may not support).")

        # Refresh the main screen (underneath help) and then the help window on top
        self.stdscr.noutrefresh()
        if help_win: # Ensure help_win was created
            help_win.noutrefresh()
        curses.doupdate() # Perform the actual screen update

        # Wait for any key press to close the help window
        # Temporarily make getch blocking for the help screen
        key_pressed_to_close = -1
        if help_win: # Only wait for key if window was successfully created
            help_win.nodelay(False) # Make getkey blocking for help_win
            help_win.keypad(True)   # Ensure special keys are captured if needed (though any key closes)
            try:
                key_pressed_to_close = help_win.getch() # Wait for a key
                KEY_LOGGER.debug(f"Help window closed by key code: {key_pressed_to_close}")
            except curses.error as e_getch:
                logging.error(f"Curses error getting key to close help: {e_getch}")
            finally:
                 help_win.nodelay(True) # Restore non-blocking mode if it was changed for stdscr
                 help_win.keypad(False) # Not strictly necessary as it's being deleted

        # Cleanup: restore cursor visibility, delete help window, and refresh main screen
        redraw_main_screen_after_help = False
        try:
            if help_win:
                del help_win # Delete the help window object
                help_win = None 
                redraw_main_screen_after_help = True # Flag that main screen needs full redraw

            try:
                curses.curs_set(previous_cursor_visibility)
            except curses.error:
                logging.warning("Curses: Could not restore cursor visibility (terminal may not support).")

            curses.flushinp() # Clear any pending input characters

            if redraw_main_screen_after_help:
                # self.drawer.draw() will be called by the main loop if this returns True
                pass
            
            if self.status_message == original_status: # If help display didn't set an error
                 self._set_status_message("Help closed") # Or restore original_status
            
            return True # Always true, as screen was taken over and status likely changed

        except Exception as e_cleanup: # Catch errors during cleanup
            logging.critical(f"Critical error during help window cleanup: {e_cleanup}", exc_info=True)
            # Try to restore terminal state as much as possible
            try: curses.curs_set(1)
            except: pass
            self._set_status_message("Critical error closing help (see log)")
            return True # Indicate a redraw is needed for the error message

    # ==================== QUEUE PROCESSING =======================
    def _process_all_queues(self) -> bool:
        """
        Processes messages from all internal queues (general status, shell commands, Git info/commands).
        Updates editor state based on these messages (e.g., self.status_message, self.git_info).

        Returns:
            bool: True if any message was processed that resulted in a change to
                  self.status_message or self.git_info (which would require a status bar redraw),
                  False otherwise.
        """
        any_state_changed_by_queues = False # Flag to track if any relevant state was altered

        # --- 1. General status message queue (_msg_q) ---
        # This queue can receive simple string messages or structured tuples.
        # It's primarily used by _set_status_message.
        # The main loop also checks self.status_message directly against its last known value,
        # but processing here ensures we capture changes from queued items.
        
        # Store status before processing this queue
        status_before_msg_q = self.status_message
        items_from_msg_q = 0
        while True:
            try:
                msg_item = self._msg_q.get_nowait()
                items_from_msg_q += 1
                # The primary effect of _msg_q is to update self.status_message.
                # _set_status_message puts strings here. The main loop directly updates
                # self.status_message when it processes this queue (as per original logic).
                # Let's adhere to that: this loop consumes, and the main loop part below sets self.status_message.
                # However, the provided code directly sets self.status_message here. Let's refine.

                # Revised logic: _set_status_message should be the *only* direct writer to self.status_message
                # or this method needs to be careful.
                # For now, let's assume messages in _msg_q are *intended* for self.status_message.
                
                if isinstance(msg_item, str):
                    # Direct string message for status bar
                    # This check ensures we only mark change if the actual message differs
                    if self.status_message != msg_item:
                        self.status_message = msg_item
                        # any_state_changed_by_queues will be set later by comparing with original_status_overall
                    logging.debug(f"Processed string from _msg_q for status: '{msg_item}'")
                elif isinstance(msg_item, tuple): # Handling structured messages if any (e.g. future use)
                    msg_type, *msg_data = msg_item
                    logging.warning(f"Processing tuple from _msg_q: type='{msg_type}', data='{msg_data}'. Not fully handled yet.")
                    # Example: if msg_type == "special_status": self.status_message = msg_data[0]
                else:
                    logging.warning(f"Unknown item type '{type(msg_item)}' from _msg_q ignored: {repr(msg_item)[:100]}")

            except queue.Empty:
                break # No more messages in _msg_q
        if items_from_msg_q > 0 and self.status_message != status_before_msg_q:
            any_state_changed_by_queues = True


        # --- 2. Shell command results queue (_shell_cmd_q) ---
        status_before_shell_q = self.status_message
        items_from_shell_q = 0
        while True:
            try:
                shell_result_msg = self._shell_cmd_q.get_nowait()
                items_from_shell_q +=1
                if self.status_message != str(shell_result_msg):
                    self.status_message = str(shell_result_msg) # Update status with shell command result
                logging.debug(f"Processed shell command result from _shell_cmd_q: '{self.status_message}'")
            except queue.Empty:
                break
        if items_from_shell_q > 0 and self.status_message != status_before_shell_q:
            any_state_changed_by_queues = True


        # --- 3. Git information updates queue (_git_q) ---
        # This queue receives (branch, user, commits) tuples from _fetch_git_info_async.
        original_git_info_tuple = self.git_info # For comparison
        items_from_git_q = 0
        while True:
            try:
                git_info_data = self._git_q.get_nowait()
                items_from_git_q +=1
                if isinstance(git_info_data, tuple) and len(git_info_data) == 3:
                    # _handle_git_info will update self.git_info and potentially self.status_message via _msg_q
                    # So, we call it, and then check if self.git_info or self.status_message changed.
                    status_before_handle_git = self.status_message
                    self._handle_git_info(git_info_data) # This might put a message in _msg_q
                    if self.git_info != original_git_info_tuple or self.status_message != status_before_handle_git:
                         any_state_changed_by_queues = True # Mark change if git_info or status changed
                else:
                    logging.warning(f"Unknown data format from _git_q ignored: {repr(git_info_data)}")
            except queue.Empty:
                break
        # If _handle_git_info put something in _msg_q, the first loop for _msg_q might have missed it if it ran before this.
        # This is a potential race if _handle_git_info queues to _msg_q.
        # A cleaner way: _handle_git_info directly sets self.status_message or returns a message.
        # For now, let's assume the effect of _handle_git_info on status is caught by main loop's status check.


        # --- 4. Git command results queue (_git_cmd_q) ---
        # This queue receives status messages from _run_git_command_async or "request_git_info_update".
        status_before_git_cmd_q = self.status_message
        items_from_git_cmd_q = 0
        while True:
            try:
                git_command_result_msg = self._git_cmd_q.get_nowait()
                items_from_git_cmd_q += 1
                if isinstance(git_command_result_msg, str):
                    if git_command_result_msg == "request_git_info_update":
                        logging.debug("Processing 'request_git_info_update' from _git_cmd_q.")
                        self.update_git_info() # This will launch an async task; doesn't change status immediately
                        # No direct status change here, but update_git_info might lead to one via _git_q.
                    else: # It's a status message from a git command
                        if self.status_message != git_command_result_msg:
                            self.status_message = git_command_result_msg
                        logging.debug(f"Processed Git command status from _git_cmd_q: '{self.status_message}'")
                else:
                    logging.warning(f"Unknown item type from _git_cmd_q ignored: {repr(git_command_result_msg)}")
            except queue.Empty:
                break
        if items_from_git_cmd_q > 0 and self.status_message != status_before_git_cmd_q:
             any_state_changed_by_queues = True
             
        return any_state_changed_by_queues


    # =============  Главный цикл редактора  =========================================
    def run(self) -> None:
        """
        Main editor loop. Handles input, processes queues, and manages screen redraws.
        Attempts to redraw only when necessary based on state changes or explicit flags.
        """
        logging.info("Editor main loop started.")
        
        self.stdscr.nodelay(True) # Set non-blocking input for get_wch()
        self.stdscr.keypad(True)  # Enable interpretation of special keys (arrows, F-keys)

        needs_redraw   = True  # Force an initial draw when the editor starts
        last_draw_time = 0.0   # Timestamp of the last screen draw
        # Target FPS for screen updates, adjust for desired responsiveness vs. CPU usage.
        # Higher FPS means more frequent redraws if needed, lower means less CPU.
        FPS = self.config.get("editor", {}).get("target_fps", 30) # Make FPS configurable

        # Track the last known status message to detect changes from queue processing
        # that might not be caught by the return value of _process_all_queues.
        # Initialize with current status, or a value that ensures first check triggers.
        last_known_status_message = object() # Unique object to ensure first comparison is different

        while True:
            # --- 1. Process background queues ---
            # _process_all_queues updates self.status_message, self.git_info etc.
            # It returns True if it processed any item that *might* have changed state.
            # We also check self.status_message directly for changes.
            
            status_before_queue_processing = self.status_message # For precise change detection
            queues_processed_something = False
            try:
                if self._process_all_queues():
                    queues_processed_something = True
                
                # If status message changed due to queue processing, flag for redraw.
                if self.status_message != status_before_queue_processing:
                    needs_redraw = True
                    last_known_status_message = self.status_message # Update tracker
                elif queues_processed_something:
                    # If queues did something but status text itself didn't change,
                    # other state like self.git_info (affecting status bar) might have.
                    # Or an async task was launched. For safety, flag redraw.
                    needs_redraw = True
            except Exception as e_queue_proc: 
                logging.exception("Error during _process_all_queues")
                self._set_status_message("Error processing background tasks (see log)")
                # Error in queue processing should force a redraw to show the error status.
                curses.flushinp() # Clear any pending input that might be causing issues
                needs_redraw = True 

            # --- 2. Get and handle user input ---
            try:
                key_input = self.stdscr.get_wch() # Can be int (special key, char code) or str (char)
                
                if key_input != curses.ERR: # curses.ERR (-1) means no input was available
                    logging.debug(f"Raw key from get_wch(): {repr(key_input)} (type: {type(key_input).__name__})")
                    
                    status_before_input_handling = self.status_message
                    
                    # handle_input returns True if it made a change requiring redraw
                    if self.handle_input(key_input): 
                        needs_redraw = True
                    
                    # Also, if status message was changed by handle_input (even if it returned False for other reasons)
                    if self.status_message != status_before_input_handling:
                        needs_redraw = True
                        last_known_status_message = self.status_message # Update tracker
                
            except KeyboardInterrupt: # Ctrl+C typically
                logging.info("KeyboardInterrupt received, initiating exit.")
                self.exit_editor() # Handles save prompts and sys.exit()
                return # Exit the main loop and thus the run() method
            except curses.error as e_curses_input:
                # "no input" is an expected non-error when nodelay(True) is set
                if "no input" not in str(e_curses_input).lower(): 
                    logging.error("Curses input error in main loop: %s", e_curses_input, exc_info=True)
                    self._set_status_message(f"Input error: {e_curses_input}")
                    curses.flushinp()
                    needs_redraw = True
            except Exception as e_input_generic: 
                logging.exception("Unhandled error during input processing in main loop.")
                self._set_status_message("Input processing error (see log)")
                curses.flushinp()
                needs_redraw = True

            # --- 3. Draw the screen if needed and FPS allows ---
            current_time = time.monotonic() # Use monotonic clock for reliable time differences
            if needs_redraw and (current_time - last_draw_time >= 1.0 / FPS):
                try:
                    self.drawer.draw() # Call the main drawing routine
                except curses.error as e_curses_draw:
                    logging.error("Curses error during screen drawing: %s", e_curses_draw, exc_info=True)
                    # Try to set status, but drawing itself might be failing
                    self._set_status_message("Screen draw error (see log)")
                except Exception as e_draw_generic: # Catch any other error during drawing
                    logging.exception("Unhandled error during screen drawing.")
                    self._set_status_message("Critical draw error (see log)")
                
                last_draw_time = current_time
                needs_redraw = False # Reset flag after a successful (or attempted) draw

            # --- 4. Brief sleep to yield CPU and control loop speed ---
            # This also contributes to the overall responsiveness and feel.
            # Too short: high CPU. Too long: laggy.
            time.sleep(0.005) # 5ms, adjust as needed (e.g., 0.01 for ~100 FPS cap if drawing is fast)


## Class DrawScreen ------------------------------------------------------
class DrawScreen:
    """
    Класс для отрисовки экрана редактора.
    Содержит логику построения интерфейса с использованием curses.
    """
    
    MIN_WINDOW_WIDTH = 20
    MIN_WINDOW_HEIGHT = 5
    DEFAULT_TAB_WIDTH = 4 # Не используется напрямую в этих методах, но оставлена

    def __init__(self, editor: Any): # Используем Any для editor для примера
        self.editor = editor
        self.stdscr = editor.stdscr
        self.colors = editor.colors 
        # _text_start_x должен быть инициализирован где-то, например, в _draw_line_numbers
        # Для этого примера установим значение по умолчанию.
        self._text_start_x = 0 
        # Убедимся, что editor.visible_lines существует
        if not hasattr(self.editor, 'visible_lines'):
            self.editor.visible_lines = self.stdscr.getmaxyx()[0] - 2 # Примерное значение

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
                     self.stdscr.move(r, self._text_start_x) # self._text_start_x - начало текстовой области
                     self.stdscr.clrtoeol()
            except curses.error as e:
                 logging.warning(f"Curses error clearing text area in _draw_text_with_syntax_highlighting: {e}")
            return

        visible_content_data = self._get_visible_content_and_highlight()
        if not visible_content_data:
            logging.debug("DrawScreen _draw_text_with_syntax_highlighting: No visible content from _get_visible_content_and_highlight.")
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


    def _draw_single_line(self, screen_row: int, line_data: Tuple[int, List[Tuple[str, int]]], window_width: int):
        """
        Draws a single line of text with syntax highlighting.
        :param screen_row: The screen row (y-coordinate) to draw on.
        :param line_data: Tuple (line_index, tokens_for_this_line).
                         line_index - index of the line in self.editor.text.
                         tokens_for_this_line - list of tokens [(text, attr), ...].
        :param window_width: Current width of the terminal window.
        """
        line_index, tokens_for_this_line = line_data
        
        try:
            self.stdscr.move(screen_row, self._text_start_x)
            self.stdscr.clrtoeol()
        except curses.error as e:
            logging.error(f"Curses error clearing line at ({screen_row}, {self._text_start_x}): {e}")
            return

        current_screen_x = self._text_start_x # Current drawing X position on the screen
        logical_char_offset_in_line = 0 # Keeps track of the logical character offset from the start of the full line

        for token_text_content, token_color_attribute in tokens_for_this_line:
            if not token_text_content:
                continue

            token_start_logical_col_abs = logical_char_offset_in_line
            token_width_logical = self.editor.get_string_width(token_text_content)
            
            # Calculate where this token *would* start on screen if there were no horizontal scrolling or window clipping
            token_ideal_screen_start_x = self._text_start_x + (token_start_logical_col_abs - self.editor.scroll_left)
            
            # Determine the visible part of the token
            # chars_to_skip_from_token_start: how many display cells of the token are scrolled off to the left
            chars_to_skip_from_token_start = 0
            if token_ideal_screen_start_x < self._text_start_x:
                chars_to_skip_from_token_start = self._text_start_x - token_ideal_screen_start_x
            
            # actual_draw_x_for_token: where the (potentially clipped) token will start drawing on screen
            actual_draw_x_for_token = max(self._text_start_x, token_ideal_screen_start_x)

            # available_width_for_token: how much screen space is left from actual_draw_x_for_token to the window edge
            available_width_for_token = window_width - actual_draw_x_for_token
            
            if available_width_for_token <= 0: # Token starts beyond the right edge of the window
                logical_char_offset_in_line += token_width_logical
                break # No more tokens on this line will be visible

            # visible_token_width_on_screen: how much of the token's width can actually be displayed
            # It's the token's original width, minus parts scrolled off left, limited by available screen space
            visible_token_width_on_screen = max(0, token_width_logical - chars_to_skip_from_token_start)
            visible_token_width_on_screen = min(visible_token_width_on_screen, available_width_for_token)

            if visible_token_width_on_screen > 0:
                # We need to find the substring of token_text_content that corresponds to
                # [chars_to_skip_from_token_start, chars_to_skip_from_token_start + visible_token_width_on_screen]
                # This is tricky with wcwidth. For simplicity, we might still have to iterate chars if clipping.
                
                # Simplified: if the token fits entirely or is only clipped by window edge, try addnstr
                # This doesn't perfectly handle clipping of a wide char in the middle.
                
                start_char_idx_in_token = 0
                current_skipped_width = 0
                if chars_to_skip_from_token_start > 0:
                    for i, char_val in enumerate(token_text_content):
                        char_w = self.editor.get_char_width(char_val)
                        if current_skipped_width + char_w > chars_to_skip_from_token_start:
                            break
                        current_skipped_width += char_w
                        start_char_idx_in_token = i + 1
                
                text_to_draw = ""
                accumulated_width_drawn = 0
                for char_val in token_text_content[start_char_idx_in_token:]:
                    char_w = self.editor.get_char_width(char_val)
                    if accumulated_width_drawn + char_w > visible_token_width_on_screen:
                        break
                    text_to_draw += char_val
                    accumulated_width_drawn += char_w

                if text_to_draw:
                    try:
                        # logging.debug(f"    Drawing token part: screen_y={screen_row}, x={actual_draw_x_for_token}, text='{text_to_draw.replace(chr(9),'/t/')}', width={self.editor.get_string_width(text_to_draw)}, attr={token_color_attribute}")
                        self.stdscr.addstr(screen_row, actual_draw_x_for_token, text_to_draw, token_color_attribute)
                    except curses.error as e:
                        # logging.warning(f"    Curses error drawing token part at ({screen_row}, {actual_draw_x_for_token}): {e}. Text='{text_to_draw}'")
                        # Fallback to char-by-char for this problematic token segment if addstr fails
                        current_char_draw_x = actual_draw_x_for_token
                        for char_in_fallback in text_to_draw:
                            if current_char_draw_x < window_width:
                                try:
                                    self.stdscr.addch(screen_row, current_char_draw_x, char_in_fallback, token_color_attribute)
                                    current_char_draw_x += self.editor.get_char_width(char_in_fallback)
                                except curses.error:
                                    break # Stop drawing this line if addch fails
                            else:
                                break
                        logical_char_offset_in_line += token_width_logical # Still advance full token width
                        continue # To next token, as this one had issues

            logical_char_offset_in_line += token_width_logical
            current_screen_x = actual_draw_x_for_token + self.editor.get_string_width(text_to_draw) # Update screen x for next token
            if current_screen_x >= window_width:
                 break # Reached end of screen
            
#     def _draw_single_line(self, screen_row: int, line_data: Tuple[int, List[Tuple[str, int]]], window_width: int):
#         """
#         Отрисовывает одну строку текста с подсветкой синтаксиса.
#         :param screen_row: Экранная строка (y-координата), куда рисовать.
#         :param line_data: Кортеж (line_index, tokens_for_this_line).
#                          line_index - индекс строки в self.editor.text.
#                          tokens_for_this_line - список токенов [(text, attr), ...].
#         :param window_width: Текущая ширина окна терминала.
#         """
#         line_index, tokens_for_this_line = line_data
        
#         # Очищаем строку перед отрисовкой (от self._text_start_x до конца)
#         try:
#             self.stdscr.move(screen_row, self._text_start_x)
#             self.stdscr.clrtoeol()
#         except curses.error as e:
#             logging.error(f"Curses error clearing line at ({screen_row}, {self._text_start_x}): {e}")
#             return # Не можем продолжить, если не можем очистить строку

#         original_line_text_for_log = self.editor.text[line_index] if line_index < len(self.editor.text) else "LINE_INDEX_OUT_OF_BOUNDS"
#         logging.debug(
#             f"  DrawScreen _draw_single_line: Line {line_index} (screen_row {screen_row}), "
#             f"Original content: '{original_line_text_for_log[:70].replace(chr(9), '/t/')}{'...' if len(original_line_text_for_log)>70 else ''}'"
#         )
#         logging.debug(
#             f"    DrawScreen _draw_single_line: Tokens: "
#             f"{[(token_text.replace(chr(9), '/t/'), token_attr) for token_text, token_attr in tokens_for_this_line if isinstance(token_text, str)]}"
#         )

#         logical_char_col_abs = 0  # Суммарная *логическая ширина* символов от начала строки (с учетом wcwidth)
        
#         for token_index, (token_text_content, token_color_attribute) in enumerate(tokens_for_this_line):
#             logging.debug(
#                 f"      DrawScreen _draw_single_line: Token {token_index}: text='{token_text_content.replace(chr(9),'/t/')}', attr={token_color_attribute}"
#             )
#             if not token_text_content:
#                 logging.debug("        DrawScreen _draw_single_line: Skipping empty token.")
#                 continue

#             for char_index_in_token, char_to_render in enumerate(token_text_content):
#                 char_printed_width = self.editor.get_char_width(char_to_render)
                
#                 logging.debug(
#                     f"        DrawScreen _draw_single_line: Char '{char_to_render.replace(chr(9),'/t/')}' (idx_in_token {char_index_in_token}), "
#                     f"current_logical_col_abs_BEFORE_this_char={logical_char_col_abs}, char_width={char_printed_width}"
#                 )

#                 if char_printed_width == 0: 
#                     logging.debug("          DrawScreen _draw_single_line: Skipping zero-width char.")
#                     continue # logical_char_col_abs не увеличивается для символов нулевой ширины

#                 # Идеальная стартовая X координата символа на экране (относительно начала окна)
#                 char_ideal_screen_start_x = self._text_start_x + (logical_char_col_abs - self.editor.scroll_left)
#                 # Идеальная конечная X координата символа на экране
#                 char_ideal_screen_end_x = char_ideal_screen_start_x + char_printed_width

#                 # Проверяем, видим ли мы этот символ на экране
#                 is_char_visible_on_screen = (char_ideal_screen_end_x > self._text_start_x and
#                                              char_ideal_screen_start_x < window_width)

#                 if is_char_visible_on_screen:
#                     # Реальная X координата для отрисовки (не левее начала текстовой области)
#                     actual_draw_x = max(self._text_start_x, char_ideal_screen_start_x)

#                     if actual_draw_x < window_width: # Убедимся, что мы не пытаемся рисовать за пределами окна
#                         try:
#                             logging.debug(
#                                 f"          DrawScreen _draw_single_line: DRAWING Char '{char_to_render.replace(chr(9),'/t/')}' "
#                                 f"at screen ({screen_row}, {actual_draw_x}), "
#                                 f"ideal_X={char_ideal_screen_start_x}, "
#                                 f"final_attr={token_color_attribute}"
#                             )
#                             self.stdscr.addch(screen_row, actual_draw_x, char_to_render, token_color_attribute)
#                         except curses.error as e:
#                             # Если ошибка при отрисовке символа (например, за пределами экрана справа),
#                             # прекращаем отрисовку этой строки.
#                             logging.warning(
#                                 f"          DrawScreen _draw_single_line: CURSES ERROR drawing char '{char_to_render.replace(chr(9),'/t/')}' (ord: {ord(char_to_render) if len(char_to_render)==1 else 'multi'}) "
#                                 f"at ({screen_row}, {actual_draw_x}) with attr {token_color_attribute}. Error: {e}. Stopping line draw."
#                             )
#                             return # Прерываем отрисовку всей строки
#                     else:
#                         # Символ начинается за пределами правой границы окна
#                         logging.debug(
#                             f"          DrawScreen _draw_single_line: Char '{char_to_render.replace(chr(9),'/t/')}' not drawn, actual_draw_x={actual_draw_x} >= window_width={window_width}."
#                         )
#                         # Если символ уже не помещается, нет смысла продолжать для этой строки
#                         return 
#                 else:
#                     # Символ полностью вне видимой области (слева или справа)
#                     logging.debug(
#                         f"          DrawScreen _draw_single_line: Char '{char_to_render.replace(chr(9),'/t/')}' not visible. "
#                         f"Ideal screen X range: [{char_ideal_screen_start_x} - {char_ideal_screen_end_x}). "
#                         f"Visible text area X range: [{self._text_start_x} - {window_width-1}]."
#                     )
                
#                 logical_char_col_abs += char_printed_width # Увеличиваем логическую позицию на ширину символа
                
#                 # Проверка, не вышли ли мы за правую границу окна по логической ширине
#                 # Если следующий символ начнется за пределами окна, нет смысла продолжать
#                 next_char_ideal_screen_start_x_check = self._text_start_x + (logical_char_col_abs - self.editor.scroll_left)
#                 if next_char_ideal_screen_start_x_check >= window_width:
#                     logging.debug(
#                         f"        DrawScreen _draw_single_line: Next char would start at or beyond window width "
#                         f"({next_char_ideal_screen_start_x_check} >= {window_width}). Breaking inner char loop."
#                     )
#                     break # Прерываем цикл по символам в текущем токене
            
#             # Если внутренний цикл (по символам) был прерван (break), то прерываем и внешний (по токенам)
#             else: # Этот 'else' относится к 'for char_index_in_token...'
#                 continue # Продолжаем со следующим токеном
#             logging.debug(f"      DrawScreen _draw_single_line: Broken from char loop, breaking token loop as well.")
#             break # Прерываем цикл по токенам
            
#         logging.debug(f"    DrawScreen _draw_single_line: Finished processing tokens for line {line_index}. Final logical_char_col_abs = {logical_char_col_abs}")

# #----

    def draw(self):
        """Основной метод отрисовки экрана."""
        try:
            # 1. Обрабатываем фоновые очереди (auto-save, shell, git и т.п.)
            self.editor._process_all_queues()
            height, width = self.stdscr.getmaxyx()

            # 2. Проверяем минимальный размер окна
            if height < 5 or width < 20:
                self._show_small_window_error(height, width)
                self.editor.last_window_size = (height, width)
                self.stdscr.refresh()
                return

            # 3. Если размер окна изменился, пересчитываем видимые строки
            if (height, width) != self.editor.last_window_size:
                self.editor.visible_lines = max(1, height - 2)
                self.editor.last_window_size = (height, width)
                self.editor.scroll_left = 0
                self._adjust_vertical_scroll()
                logging.debug(f"Window resized to {width}x{height}. Visible lines: {self.editor.visible_lines}. Scroll left reset.")

            # 4. Очищаем экран
            self.stdscr.clear()

            # 5. Рисуем основной интерфейс
            self._draw_line_numbers()
            self._draw_text_with_syntax_highlighting()
            self._draw_search_highlights()
            self._draw_selection()
            self._draw_matching_brackets()
            self._draw_status_bar()

            # 6. Рисуем всплывающую панель линтера (если она активна)
            self._draw_lint_panel()   # <--- Вставить здесь обязательно!

            # 7. Позиционируем курсор (если панель не активна)
            if not getattr(self.editor, 'lint_panel_active', False):
                self._position_cursor()

            # 8. Обновляем экран
            self._update_display()

        except curses.error as e:
            logging.error(f"Curses error in DrawScreen.draw(): {e}", exc_info=True)
            self.editor._set_status_message(f"Draw error: {str(e)[:80]}...")

        except Exception as e:
            logging.exception("Unexpected error in DrawScreen.draw()")
            self.editor._set_status_message(f"Draw error: {str(e)[:80]}...")


    def _show_small_window_error(self, height, width):
        """Отображает сообщение о слишком маленьком окне."""
        msg = f"Window too small ({width}x{height}). Minimum is 20x5."
        try:
            self.stdscr.clear() # Очищаем перед сообщением
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
        max_line_num_digits = len(str(max(1, max_line_num))) # Минимум 1 цифра для пустых файлов
        line_num_width = max_line_num_digits + 1 # +1 для пробела после номера

        # Проверяем, помещаются ли номера строк в ширину окна
        if line_num_width >= width:
             logging.warning(f"Window too narrow to draw line numbers ({width} vs {line_num_width})")
             # Если не помещаются, пропускаем отрисовку номеров
             self._text_start_x = 0 # Текст начинается с 0-й колонки
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
                line_num_str = f"{line_idx + 1:>{max_line_num_digits}} " # Выравнивание по правому краю + пробел
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
        """Накладывает подсветку найденных совпадений."""
        if not self.editor.highlighted_matches:
            return # Нет совпадений для подсветки

        # Цвет для подсветки поиска (например, A_REVERSE или специальная пара)
        search_color = self.colors.get("search_highlight", curses.A_REVERSE)
        height, width = self.stdscr.getmaxyx()
        line_num_width = len(str(max(1, len(self.editor.text)))) + 1 # Ширина номера строки + пробел
        text_area_width = max(1, width - line_num_width)

        # Итерируем по всем совпадениям, которые нужно подсветить
        for match_row, match_start_idx, match_end_idx in self.editor.highlighted_matches:
            # Проверяем, находится ли строка с совпадением в видимой области
            if match_row < self.editor.scroll_top or match_row >= self.editor.scroll_top + self.editor.visible_lines:
                continue # Строка не на экране, пропускаем

            screen_y = match_row - self.editor.scroll_top # Экранная строка для этого совпадения
            line = self.editor.text[match_row] # Оригинальная строка текста

            # Позиция X на экране, где начинается совпадение
            match_screen_start_x_before_scroll = self.editor.get_string_width(line[:match_start_idx])
            match_screen_start_x = line_num_width + match_screen_start_x_before_scroll - self.editor.scroll_left

            # Позиция X на экране, где заканчивается совпадение (или начинается следующий символ)
            match_screen_end_x_before_scroll = self.editor.get_string_width(line[:match_end_idx])
            match_screen_end_x = line_num_width + match_screen_end_x_before_scroll - self.editor.scroll_left

            # Определяем видимую часть совпадения на экране
            # Начальная X для отрисовки подсветки (не меньше, чем _text_start_x)
            draw_start_x = max(line_num_width, match_screen_start_x)

            # Конечная X для отрисовки подсветки (не больше, чем правый край окна)
            draw_end_x = min(width, match_screen_end_x)

            # Рассчитываем реальную ширину подсветки на экране
            highlight_width_on_screen = max(0, draw_end_x - draw_start_x)

            # Применяем атрибут подсветки, если видимая ширина больше 0
            if highlight_width_on_screen > 0:
                try:
                    # Итерируем по символам оригинальной строки
                    current_char_screen_x = line_num_width - self.editor.scroll_left # Начальная X для первого символа строки
                    for char_idx, char in enumerate(line):
                        char_width = self.editor.get_char_width(char)
                        char_screen_end_x = current_char_screen_x + char_width

                        # Если символ находится в диапазоне совпадения и виден на экране
                        if match_start_idx <= char_idx < match_end_idx and \
                           current_char_screen_x < width and char_screen_end_x > line_num_width: # Проверка видимости

                            # Координаты отрисовки символа на экране
                            draw_char_x = max(line_num_width, current_char_screen_x)
                            draw_char_width = min(char_width, width - draw_char_x)

                            if draw_char_width > 0:
                               try:
                                  # Подсвечиваем отдельный символ
                                  # chgat(y, x, num_chars, attr). num_chars=1 для одного символа
                                  self.stdscr.chgat(screen_y, draw_char_x, 1, search_color) # Подсвечиваем одну ячейку
                               except curses.error as e:
                                  # Ловим ошибку для отдельного символа
                                  logging.warning(f"Curses error highlighting single char at ({screen_y}, {draw_char_x}): {e}")
                        current_char_screen_x += char_width # Сдвигаем X для следующего символа
                except curses.error as e:
                    logging.error(f"Curses error applying search highlight: {e}")
 


    def _draw_selection(self):
        """Накладывает подсветку выделенного текста."""
        # Проверяем, активно ли выделение и заданы ли его границы
        if not self.editor.is_selecting or not self.editor.selection_start or not self.editor.selection_end:
            return # Нет активного выделения

        # Получаем координаты начала и конца выделения
        start_y, start_x = self.editor.selection_start
        end_y, end_x = self.editor.selection_end

        # Нормализуем координаты, чтобы начало всегда было "раньше" конца
        if start_y > end_y or (start_y == end_y and start_x > end_x):
            start_y, start_x, end_y, end_x = end_y, end_x, start_y, start_x

        # Получаем размеры окна и информацию о номерах строк
        height, width = self.stdscr.getmaxyx()
        line_num_width = len(str(max(1, len(self.editor.text)))) + 1
        text_area_width = max(1, width - line_num_width)

        # Цвет подсветки выделения (используем инверсию по умолчанию)
        selection_color = curses.A_REVERSE # Инвертирование цвета

        # Итерируем по строкам, которые попадают в диапазон выделения
        for y in range(start_y, end_y + 1):
            # Проверяем, находится ли текущая строка в видимой области
            if y < self.editor.scroll_top or y >= self.editor.scroll_top + self.editor.visible_lines:
                continue # Строка не на экране, пропускаем

            screen_y = y - self.editor.scroll_top # Экранная строка

            # Определяем начальный и конечный индекс символа для выделения в текущей строке.
            sel_start_char_idx = start_x if y == start_y else 0
            sel_end_char_idx = end_x if y == end_y else len(self.editor.text[y])

            # Проверка, что выделение в строке вообще существует (start_x < end_x или start_idx < end_idx)
            if sel_start_char_idx >= sel_end_char_idx:
                continue

            line = self.editor.text[y] # Оригинальная строка текста

            # Экранная позиция начала выделения в этой строке
            sel_screen_start_x_before_scroll = self.editor.get_string_width(line[:sel_start_char_idx])
            sel_screen_start_x = line_num_width + sel_screen_start_x_before_scroll - self.editor.scroll_left

            # Экранная позиция конца выделения в этой строке
            sel_screen_end_x_before_scroll = self.editor.get_string_width(line[:sel_end_char_idx])
            sel_screen_end_x = line_num_width + sel_screen_end_x_before_scroll - self.editor.scroll_left

            draw_start_x = max(line_num_width, sel_screen_start_x)
            draw_end_x = min(width, sel_screen_end_x)

            highlight_width_on_screen = max(0, draw_end_x - draw_start_x)

            if highlight_width_on_screen > 0:
                try:

                    self.stdscr.chgat(screen_y, draw_start_x, highlight_width_on_screen, selection_color)

                except curses.error as e:
                    logging.error(f"Curses error applying selection highlight at ({screen_y}, {draw_start_x}) with width {highlight_width_on_screen}: {e}")
 

    def _draw_matching_brackets(self):
        """Вызывает highlight_matching_brackets для отрисовки."""
        self.editor.highlight_matching_brackets()


    def truncate_string(self, s: str, max_width: int) -> str:
        """
        Обрезает строку s так, чтобы её
        визуальная ширина (wcwidth) не превышала max_width.
        """
        result = ""
        curr = 0
        for ch in s:
            w = wcwidth(ch)
            if w < 0:
                w = 1
            if curr + w > max_width:
                break
            result += ch
            curr += w
        return result


    def _draw_status_bar(self) -> None:
        """Рисует статус-бар, избегая ERR от addnstr()."""

        logging.debug("Drawing status bar")
        try:
            h, w = self.stdscr.getmaxyx()
            if h <= 0 or w <= 1:
                return

            y = h - 1
            max_col = w - 1

            # ── цвета ──────────────────────────────────────────────────────────
            c_norm = self.colors.get("status", curses.color_pair(10) | curses.A_BOLD)
            c_err = self.colors.get("status_error", curses.color_pair(11) | curses.A_BOLD)
            c_git = self.colors.get("git_info", curses.color_pair(12))
            c_dirty = self.colors.get("git_dirty", curses.color_pair(13) | curses.A_BOLD)

            self.stdscr.move(y, 0)
            self.stdscr.clrtoeol()
            self.stdscr.bkgdset(" ", c_norm)

            # ── формируем текст блоков ─────────────────────────────────────────
            icon = get_file_icon(self.editor.filename, self.editor.config)
            fname = os.path.basename(self.editor.filename) if self.editor.filename else "No Name"
            lexer_name = self.editor._lexer.name if self.editor._lexer else "plain text"

            left = f" {icon} {fname}{'*' if self.editor.modified else ''} | {lexer_name} | UTF-8 | Ln {self.editor.cursor_y+1}/{len(self.editor.text)}, Col {self.editor.cursor_x+1} | {'INS' if self.editor.insert_mode else 'REP'} "

            g_branch, _, g_commits = self.editor.git_info
            git_txt = f"Git: {g_branch} ({g_commits})" if g_branch else ""
            git_col = c_dirty if "*" in g_branch else c_git

            msg = self.editor.status_message or "Ready"
            msg_col = c_err if msg.startswith("Error") else c_norm

            # ── ширины блоков ───────────────────────────────────────────────────
            gw_left = self.editor.get_string_width(left)
            gw_msg = self.editor.get_string_width(msg)
            gw_git = self.editor.get_string_width(git_txt)

            # ── левый блок ──────────────────────────────────────────────────────
            x = 0
            self.stdscr.addnstr(y, x, left, min(gw_left, max_col - x), c_norm)
            x += gw_left

            # ── git блок справа ─────────────────────────────────────────────────
            if git_txt:
                x_git = max_col - gw_git
                self.stdscr.addnstr(y, x_git, git_txt, gw_git, git_col)
                right_limit = x_git
            else:
                right_limit = max_col

            # ── сообщение по центру (обрезка если нужно) ────────────────────────
            space_for_msg = right_limit - x
            if space_for_msg > 0:
                msg = self.truncate_string(msg, space_for_msg)
                gw_msg = self.editor.get_string_width(msg)
                x_msg = x + (space_for_msg - gw_msg) // 2
                self.stdscr.addnstr(y, x_msg, msg, gw_msg, msg_col)

        except Exception as e:
            logging.error(f"Error in _draw_status_bar: {e}", exc_info=True)
            self.editor._set_status_message("Status bar error (see log)")


    def _position_cursor(self) -> None:
        """Позиционирует курсор на экране, не позволяя ему «улетать» за Git-статус."""
        height, width = self.stdscr.getmaxyx()
        max_row       = height - 2                 # последняя строка текста (height-1 – статус-бар)
        line_num_width = len(str(max(1, len(self.editor.text)))) + 1  # «NN␠»
        text_area_width = max(1, width - line_num_width)

        # --- 1. Корректируем внутренние координаты ---------------------------------
        self.editor.cursor_y = max(0, min(self.editor.cursor_y, len(self.editor.text) - 1))
        current_line         = self.editor.text[self.editor.cursor_y]
        self.editor.cursor_x = max(0, min(self.editor.cursor_x, len(current_line)))

        cursor_line_idx = self.editor.cursor_y
        cursor_char_idx = self.editor.cursor_x

        # --- 2. Вертикальная прокрутка ----------------------------------------------
        screen_y = cursor_line_idx - self.editor.scroll_top
        if screen_y < 0:
            self.editor.scroll_top = cursor_line_idx
            screen_y = 0
        elif screen_y >= self.editor.visible_lines:
            self.editor.scroll_top = min(
                len(self.editor.text) - self.editor.visible_lines,
                cursor_line_idx - self.editor.visible_lines + 1
            )
            self.editor.scroll_top = max(0, self.editor.scroll_top)
            screen_y = self.editor.visible_lines - 1
        screen_y = max(0, min(screen_y, max_row))

        # --- 3. Горизонтальная прокрутка --------------------------------------------
        cursor_px_before_scroll = self.editor.get_string_width(current_line[:cursor_char_idx])
        current_cursor_screen_x = line_num_width + cursor_px_before_scroll - self.editor.scroll_left

        view_start_x = line_num_width
        view_end_x   = width - 1                   # последний допустимый столбец

        if current_cursor_screen_x < view_start_x:
            self.editor.scroll_left = cursor_px_before_scroll
        elif current_cursor_screen_x > view_end_x:
            self.editor.scroll_left = max(0, cursor_px_before_scroll - text_area_width + 1)

        # --- 4. Итоговые экранные координаты ----------------------------------------
        final_cursor_screen_x = (
            line_num_width
            + cursor_px_before_scroll
            - self.editor.scroll_left
        )
        draw_cursor_x = max(view_start_x, min(view_end_x, final_cursor_screen_x))

        # --- 5. Перемещаем курсор ----------------------------------------------------
        try:
            logging.debug(f"Positioning cursor: screen_y={screen_y}, draw_cursor_x={draw_cursor_x}. Logical: ({self.editor.cursor_y}, {self.editor.cursor_x}). Line: '{current_line}'")
            self.stdscr.move(screen_y, draw_cursor_x)
        except curses.error:
            # запасной вариант – ставим в начало строки, если что-то пошло не так
            try:
                self.stdscr.move(screen_y, view_start_x)
            except curses.error:
                pass

    def _adjust_vertical_scroll(self):
        """
        Adjusts vertical scroll (scroll_top) to ensure the cursor is visible.
        Called after window resize or other events that might push cursor off-screen.
        """
        height, width = self.stdscr.getmaxyx()
        text_area_height = max(1, height - 2)

        # Если общее количество строк меньше видимой области, scroll_top всегда 0
        if len(self.editor.text) <= text_area_height:
             self.editor.scroll_top = 0
             return

        # Текущая экранная Y позиция курсора (относительно начала видимой области)
        screen_y = self.editor.cursor_y - self.editor.scroll_top

        # Если курсор выше или ниже видимой области
        if screen_y < 0:
             # Сдвигаем scroll_top так, чтобы строка с курсором стала первой видимой
             self.editor.scroll_top = self.editor.cursor_y
             logging.debug(f"Adjusted vertical scroll: cursor above view. New scroll_top: {self.editor.scroll_top}")
        elif screen_y >= text_area_height:
             # Сдвигаем scroll_top так, чтобы строка с курсором стала последней видимой
             self.editor.scroll_top = self.editor.cursor_y - text_area_height + 1
             logging.debug(f"Adjusted vertical scroll: cursor below view. New scroll_top: {self.editor.scroll_top}")

        # Убеждаемся, что scroll_top не выходит за допустимые пределы (0 до len(text) - visible_lines)
        self.editor.scroll_top = max(0, min(self.editor.scroll_top, len(self.editor.text) - text_area_height))
        logging.debug(f"Final adjusted scroll_top: {self.editor.scroll_top}")


    def _update_display(self):
        """Обновляет физический экран."""
        try:
            # noutrefresh() - подготавливает обновление в памяти
            self.stdscr.noutrefresh()
            # doupdate() - выполняет все подготовленные обновления на физическом экране
            curses.doupdate()
        except curses.error as e:
            logging.error(f"Curses doupdate error: {e}")
            pass # Продолжаем, надеясь, что главный цикл обработает




def main_curses_function(stdscr): # Renamed from 'main' to avoid conflict with script entry point
    """
    Initializes locale, and editor, then runs the main editor loop.
    This function is intended to be passed to curses.wrapper.
    """
    # Signal handling (Ctrl+Z, Ctrl+C)
    try:
        # These might not work on all platforms (e.g. Windows) or in all terminal emulators.
        if hasattr(signal, 'SIGTSTP'):
            signal.signal(signal.SIGTSTP, signal.SIG_IGN)  # Ignore Ctrl+Z (suspend)
        if hasattr(signal, 'SIGINT'):
            signal.signal(signal.SIGINT, signal.SIG_IGN)   # Ignore Ctrl+C (interrupt)
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
        editor = SwayEditor(stdscr) # Pass the curses screen object

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
        editor.run() # This call will block until the editor exits.

    except Exception as e_editor: # Catch any unhandled exceptions during editor operation
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
                error_file.write(f"\n{'='*20} CRITICAL ERROR: {time.asctime()} {'='*20}\n")
                error_file.write("Error occurred inside curses.wrapper (main_curses_function):\n")
                traceback.print_exc(file=error_file) # Print full traceback to this file
                error_file.write(f"\n{'='*60}\n")
            print(f"Detailed critical error traceback also logged to: '{critical_error_log_path}'", file=sys.stderr)
        except Exception as log_write_err:
            print(f"Failed to write detailed critical error log to '{critical_error_log_path}': {log_write_err}", file=sys.stderr)
            # As a very last resort, print traceback to stderr if file logging failed
            print("\n--- Traceback (also attempted to log to file) ---", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            print("--- End Traceback ---", file=sys.stderr)

        # It's generally good to re-raise or exit with an error code if curses wrapper content fails badly.
        # However, sys.exit() here might prevent curses.wrapper's own cleanup.
        # The exception will propagate out of main_curses_function, and curses.wrapper
        # should then handle terminal restoration. The outer try-except around curses.wrapper
        # in __main__ will catch this.
        raise # Re-raise the exception to be caught by the __main__ block's try-except


# --- Main Script Entry Point ---
if __name__ == "__main__":
    # 1. Perform initial setup that doesn't depend on curses (like signal handling, config loading, logging setup)
    
    # Attempt to ignore SIGTSTP (Ctrl+Z, suspend) if on a platform that supports it.
    # This is often done to prevent the editor from being suspended accidentally.
    if hasattr(signal, 'SIGTSTP'):
        try:
            signal.signal(signal.SIGTSTP, signal.SIG_IGN)
        except Exception as e: # More specific: RuntimeError, ValueError
            print(f"Warning: Could not set SIGTSTP to ignore: {e}", file=sys.stderr)
    # SIGINT (Ctrl+C) is typically handled by KeyboardInterrupt within the application.

    # Load application configuration first, as logging setup might depend on it.
    app_config = {} # Default to empty config if loading fails
    try:
        app_config = load_config() # Assumes load_config() is defined
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
    logger.info("Sway-Pad editor starting up...") # This will now use the configured logger.

    # 2. Initialize and run the curses-based application.
    # curses.wrapper handles curses initialization, calls main_curses_function,
    # and ensures curses is properly shut down (terminal restored) on exit or error.
    try:
        curses.wrapper(main_curses_function)
        logger.info("Sway-Pad editor shut down gracefully.")
    except Exception as e_wrapper: # Catch any unhandled exceptions that escape curses.wrapper
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
                err_f.write(f"\n{'='*20} CRITICAL OUTER ERROR: {time.asctime()} {'='*20}\n")
                traceback.print_exc(file=err_f)
                err_f.write(f"\n{'='*60}\n")
            print(f"Detailed critical error traceback also logged to: '{outer_error_log_path}'", file=sys.stderr)
        except Exception as log_final_err:
            print(f"Failed to write final critical error log: {log_final_err}", file=sys.stderr)
            print("\n--- Final Traceback (also attempted to log to file) ---", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            print("--- End Final Traceback ---", file=sys.stderr)
            
        sys.exit(1) # Exit with an error code
