from __future__ import annotations

import runpy
from pathlib import Path


def main() -> None:
    target = Path(__file__).resolve().parent / "eval-framework" / "smoke_test.py"
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()
