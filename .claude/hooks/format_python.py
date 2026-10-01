"""PostToolUse hook: ruff-format a Python file right after Claude edits it.

Reads the hook payload (JSON) from stdin. Only files under ``backend/`` or
``simulator/`` are touched, with the same commands as ``make format`` (sort
imports, then format). Unused imports are deliberately NOT removed here: an
agent often adds an import in one edit and its first use in the next.
The hook never fails the tool call; a ruff problem surfaces in ``make check``.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    """Format the edited file when it is project Python source."""
    payload = json.load(sys.stdin)
    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path") or ""
    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or ".")
    path = Path(file_path)
    if path.suffix != ".py" or not path.is_file():
        return
    try:
        relative = path.resolve().relative_to(project_dir.resolve())
    except ValueError:
        return
    if relative.parts[0] not in ("backend", "simulator"):
        return
    backend_dir = project_dir / "backend"
    for ruff_args in (["check", "--select", "I", "--fix", "-q"], ["format", "-q"]):
        subprocess.run(
            ["uv", "run", "-q", "ruff", *ruff_args, str(path)],
            cwd=backend_dir,
            check=False,
            capture_output=True,
            timeout=60,
        )


if __name__ == "__main__":
    main()
