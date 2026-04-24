"""Standalone long-horizon audit lab.

This package is isolated from the live trading runtime. It may read price CSVs
and strategy signal functions, but it must not import live order, broker, or
Telegram modules.
"""

