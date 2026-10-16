"""Write the API's OpenAPI document to a file without starting the server.

Used by ``make api-client`` and ``make api-client-check``
(``docs/planners/web-portal-scaffold.md`` D6): the portal's typed client in
``frontend/packages/api-client/`` is generated from this document. Importing the app
only builds its routes; no lifespan runs and no database connection is
opened. Settings are still loaded, so ``backend/.env`` (at least
``DATABASE_URL``) must exist, as for every other backend command.
"""

import json
import sys
from pathlib import Path

from app.api.main import app
from app.api.startup import register_all_hooks


def serialize_openapi_document() -> str:
    """Return the API's OpenAPI document as stable, indented JSON text.

    Returns:
        The document FastAPI builds from the mounted routers, with a
        trailing newline; the same code always gives the same text, so the
        committed copy can be compared byte for byte.
    """
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    """Write the document to the path given as the only command-line argument.

    Registers every cross-domain hook first, like every other process (CV-21);
    the hooks do not change the document.

    Raises:
        SystemExit: When the argument is missing.
    """
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m app.api.openapi_export <output.json>")
    register_all_hooks()
    Path(sys.argv[1]).write_text(serialize_openapi_document(), encoding="utf-8")


if __name__ == "__main__":
    main()
