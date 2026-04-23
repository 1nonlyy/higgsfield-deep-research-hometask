import sys
from pathlib import Path


def pytest_configure() -> None:
    # Allow `import runner...` and `import scorer...` from `eval-framework/`.
    repo_root = Path(__file__).resolve().parents[1]
    eval_framework_dir = repo_root / "eval-framework"
    sys.path.insert(0, str(eval_framework_dir))

