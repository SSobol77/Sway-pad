# sway_pad/__init__.py

__version__ = "0.1.0"

from .sway import (
    SwayEditor,
    run_pylint_on_code,
    deep_merge,
    load_config,
    get_file_icon,
    main
)

# Optional: Expose key components through package level imports
__all__ = [
    'SwayEditor',
    'run_pylint_on_code',
    'deep_merge',
    'load_config',
    'get_file_icon',
    'main'
]
