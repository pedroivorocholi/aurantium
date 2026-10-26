"""PyInstaller entry point — a thin launcher around ``aurantium.__main__.main``."""

import sys

from aurantium.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
