"""PostToolUse hook: Prettier-format a web portal file right after Claude edits it.

Reads the hook payload (JSON) from stdin. Only files under ``web-portal/`` are
touched, with the portal's own Prettier (the same tool ``make format`` runs),
from inside ``web-portal/`` so ``.prettierignore`` keeps generated files
(the API client, the lockfile) untouched; ``--ignore-unknown`` skips file
types Prettier does not format. The hook never fails the tool call: when the
portal's dependencies are not installed it does nothing, and a formatting
problem surfaces in ``make check``.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    """Format the edited file when it belongs to the web portal."""
    payload = json.load(sys.stdin)
    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path") or ""
    project_dir = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or ".")
    path = Path(file_path)
    if not path.is_file():
        return
    try:
        relative = path.resolve().relative_to(project_dir.resolve())
    except ValueError:
        return
    if relative.parts[0] != "web-portal" or "node_modules" in relative.parts:
        return
    portal_dir = project_dir / "web-portal"
    prettier = portal_dir / "node_modules" / ".bin" / "prettier"
    if not prettier.is_file():
        return
    subprocess.run(
        [str(prettier), "--write", "--ignore-unknown", "--log-level", "silent", str(path.resolve())],
        cwd=portal_dir,
        check=False,
        capture_output=True,
        timeout=60,
    )


if __name__ == "__main__":
    main()
