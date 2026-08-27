"""Entry point so `python -m kravein` works."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
