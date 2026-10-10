"""Registers every domain's ORM models on ``Base.metadata``.

SQLAlchemy resolves a foreign key to another domain's table only when that
table's model module has been imported in the same process. A process that
imports just its own domain (the OCPP gateway, a worker) would otherwise fail
at flush time with ``NoReferencedTableError`` (for example a command row that
references ``users``). Importing this module once at the process boundary
(an entrypoint, Alembic's ``env.py``) loads every model module, without any
domain importing another domain's models. It exports nothing.
"""

import app.domains.batteries.models  # noqa: F401
import app.domains.billing.models  # noqa: F401
import app.domains.charging_sessions.models  # noqa: F401
import app.domains.charging_stations.models  # noqa: F401
import app.domains.drivers.models  # noqa: F401
import app.domains.fleet.models  # noqa: F401
import app.domains.identity.models  # noqa: F401
import app.domains.notifications.models  # noqa: F401
import app.domains.support.models  # noqa: F401
import app.domains.telematics.models  # noqa: F401
import app.domains.telemetry.models  # noqa: F401
import app.domains.vehicles.models  # noqa: F401
import app.domains.warranties.models  # noqa: F401
