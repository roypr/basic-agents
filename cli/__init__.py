"""CLI layer: interactive permission prompts and the REPL.

This package is a consumer of the core permission layer. It owns all terminal
I/O (``input()``/``print()``); ``core`` never touches the terminal.
"""
