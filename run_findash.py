"""PyInstaller entry point — a thin launcher around ``findash.__main__.main``."""

import sys

from findash.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
