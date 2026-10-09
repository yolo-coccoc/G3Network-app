# Backend coding conventions

> Scope: `backend/` — Python 3.12, FastAPI, async SQLAlchemy, and related
> background workers/entrypoints.
>
> This document focuses on naming, distinguishing object kinds, and
> converting data between layers. Rules about transactions, database,
> logging, and migrations live in
> [`backend-runtime-conventions.md`](./backend-runtime-conventions.md);
> domain boundaries in [`domain-boundaries.md`](./domain-boundaries.md) — both
> apply alongside this document.

## 1. General principle

A name must let the reader answer three questions:

1. Which business object is this?
2. What technical kind is it?
3. Which layer or boundary is it used at?

Preferred naming pattern:

```text
<BusinessObjectName><TechnicalRole>
```

Examples:

```text
VehicleModel
VehicleCreateRequest
VehicleResponse
VehicleReference
VehicleNotFoundError
```

New code must follow this convention. Existing code is migrated gradually
whenever it's touched by a related task; don't do a bulk rename unless
necessary.

## 2. Basic naming rules

| Element | Rule | Example |
|---|---|---|
| Module/file | `snake_case` | `charging_sessions.py` |
| Function/method | `snake_case` | `find_vehicle_by_vin()` |
| Variable/parameter | `snake_case` | `vehicle_record` |
| Class | `PascalCase` | `VehicleModel` |
| Enum member | `UPPER_SNAKE_CASE` | `VehicleStatus.ACTIVE` |
| Constant | `UPPER_SNAKE_CASE` | `DEFAULT_PAGE_SIZE` |
| Private helper | starts with `_` | `_clean_update_values()` |

Don't use overly generic names when an object could appear in multiple
contexts:

```text
data, item, result, model, schema, service, manager, handler, utils
```

Short names are fine within a very small scope where the context is
completely clear, but objects that cross multiple layers must have a name
that describes their role.

Don't name a variable the same as, or easily confused with, a Python
built-in like `id`, `type`, `input`, `list`, `filter`, `format`.

## 3. SQLAlchemy model

ORM classes use the `Model` suffix:

```python
class VehicleModel(Base):
    ...


class TelematicModel(Base):
    ...


class TelemetryModel(Base):
    ...


class ChargingSessionModel(Base):
    ...


class ChargingSessionEventModel(Base):
    ...
```

Class names must use a singular business noun. Table names still use the
plural per the database convention:

```python
class VehicleModel(Base):
    __tablename__ = "vehicles"
```

Every table must have an internal ID. Don't use a business key like `vin`,
`license_plate`, or `telematic_serial` as the primary key. Foreign keys must
also reference the internal ID.

## 4. Pydantic schema

### 4.1 HTTP request

Data received from the API uses the `Request` suffix:

```python
class VehicleCreateRequest(BaseModel):
    ...


class VehicleUpdateRequest(BaseModel):
    ...
```

Don't use an overly generic name like `Create`, `Update`, or `Base` for a
public schema.

A base class meant only for internal inheritance should be private or
clearly signal its role:

```python
class _VehicleInputFields(BaseModel):
    ...
```

### 4.2 HTTP response

Data returned by the API uses the `Response` suffix:

```python
class VehicleResponse(BaseModel):
    ...


class VehicleListResponse(BaseModel):
    ...
```

If pagination needs to be emphasized, `VehiclePageResponse` may be used.
Pick one consistent style within each API group.

### 4.3 Schema for external messages

The name must express the message's source or role:

```python
class TelemetryMessage(BaseModel):
    ...


class TelemetryEnvelope(BaseModel):
    ...


class TelemetryPayload(BaseModel):
    ...
```

Don't reuse one HTTP schema for an MQTT message or vice versa if the two
contracts serve different purposes.

## 5. Internal DTOs and value objects

### 5.1 Internal DTO

A DTO carries data between layers or domains. The name must express its
purpose:

```text
Reference  minimal reference to an object
Summary    summarized data
Mapping    result of mapping between objects
Lookup     result of a lookup
Target     destination object of a processing flow
```

Example:

```python
@dataclass(frozen=True)
class VehicleReference:
    vehicle_id: UUID
    vin: str
```

`VehicleResponse` is the HTTP contract; `VehicleReference` is the internal
contract. Never pass a `VehicleModel` or an HTTP response schema to another
domain.

An internal DTO doesn't have to be Pydantic. Use `dataclass(frozen=True)`
when the data is already inside the backend and you only need a typed,
immutable object, with no need for JSON serialization or OpenAPI.

A small, purely-domain DTO can live in `types.py`. If a domain has many
public internal contracts, only create a `contracts.py` when genuinely
needed — don't create an empty file or one that just collects names for the
sake of structure.

### 5.2 Value object

A value object represents a business concept, is compared by value, is
usually immutable, and can protect its own invariants:

```python
@dataclass(frozen=True)
class Vin:
    value: str
```

Fitting examples:

```text
Vin, LicensePlate, GeoPoint, Money, DateRange, BatteryPercentage
```

A DTO is "data that needs to be passed around"; a value object is "a
business concept with rules". A value object can be implemented as a
dataclass, but not every dataclass is a value object.

## 6. Enum and exception

Enums use the object name plus the concept:

```python
class VehicleStatus(str, Enum):
    ...


class TelematicStatus(str, Enum):
    ...


class ChargingSessionStatus(str, Enum):
    ...
```

Exceptions use the pattern `<Object><Condition>Error`:

```python
VehicleNotFoundError
VehicleConflictError
TelematicNotFoundError
ChargingStationNotFoundError
ChargingTopologyConflictError
```

Only the service layer raises domain exceptions. Every domain exception
inherits from exactly one shared base in `app/libs/common/errors.py`, which
decides its HTTP status: `NotFoundError` (404), `ConflictError` (409 —
duplicates and disallowed state transitions), `InvalidInputError` (400 —
input that passes schema validation but breaks a business rule),
`UpstreamUnavailableError` (502). A domain may add its own root
(`DriverError(DomainError)`) and combine it with a base
(`class DriverNotFoundError(DriverError, NotFoundError)`).

Conversion to HTTP happens only in the HTTP layer: `app/api/main.py`
registers one handler per base, so routers **don't** wrap service calls in
`try/except` for these. A router catches a domain exception only when that
endpoint needs a different status than the base implies.

## 7. Functions and methods

### 7.1 Service

Services use verb + business object:

```python
create_vehicle()
get_vehicle()
list_vehicles()
update_vehicle()
soft_delete_vehicle()
```

Meaningful prefixes:

```text
get_       get a single object, usually expected to exist
find_      search, may return None
list_      return a collection
count_     count
resolve_   look up or map via a repository/service
create_    create
update_    update
soft_delete_ soft-delete
has_/is_   boolean check with no side effect (has_active_session_on_connector)
```

Don't name a function `get_` if it writes data. If the operation is a soft
delete, spell out `soft_delete_` instead of just `delete_`.

### 7.2 Repository

The repository is called through a domain-scoped module alias:

```python
import app.domains.vehicles.repository as vehicle_repository

vehicle_record = await vehicle_repository.find_by_vin(db_session, vin)
```

Functions inside the repository can be short since the module already
expresses the domain:

```text
get_by_id()
find_by_vin()
find_by_license_plate()
list_all()
count()
insert()
update_fields()
soft_delete()
```

Avoid names that contain both the module and the domain, such as:

```python
repo_get_vehicle_by_id()
repo_create_vehicle()
```

**Preposition: use `_by_<field>`, never `_for_`**, for any lookup/count
scoped by a field or parent (`get_by_id`, `find_by_vin`,
`count_connectors_by_station_id`). A plain `count_<children>(db,
<parent>_id)` with no suffix is fine when there is only one natural count
for that child entity (`count_evses(db, station_id)`, `count_connectors(db,
evse_id)`); add the `_by_<field>` suffix only to disambiguate when the same
child entity already has another count at a different scope
(`count_connectors_by_station_id` vs. the EVSE-scoped `count_connectors`
above — both count connectors, at different levels).

**Positional vs. keyword-only parameters: decided by count, not by
meaning.** A function with exactly one non-`db` parameter is always
positional, regardless of what that parameter identifies — the function's
own name already says what it is, so there is nothing to disambiguate
(`get_station_by_id(db, station_id)`, `soft_delete_evse(db, evse_id)`,
`count_evses(db, station_id)`, `get_latest_vehicle_telemetry(db,
vehicle_id)`). Reserve `*` (keyword-only) for functions with **two or more**
non-`db` parameters where position could plausibly be ambiguous or
mis-ordered — same-typed fields in a `create_*`/`insert_*` function
(`create_charging_station(db, *, ocpp_identity, display_name, ...)`), or a
`list_*` function's `offset`/`limit` pair (`list_charging_stations(db, *,
offset, limit)`). A composite business-identity lookup is the one exception:
its parts stay positional together, since the function name (`_by_identity`)
already explains them (`get_evse_by_identity(db, station_id, ocpp_evse_id,
*, include_deleted=True)` — only the trailing optional flag is keyword-only).
Don't add `*` to a single-parameter function "for clarity" — check the rest
of the domain's `repository.py` first; single-argument positional is the
dominant style across every domain in this backend.

### 7.3 Router

Router handlers don't use generic names like `create`, `get`, `update`,
`delete`, `list_items`. Use a domain-qualified name with the `endpoint`
suffix:

```python
create_vehicle_endpoint()
get_vehicle_endpoint()
list_vehicles_endpoint()
update_vehicle_endpoint()
soft_delete_vehicle_endpoint()
```

Service and router names should be distinguishable when searching code or
reading a stack trace.

## 8. Variables, parameters, collections, and booleans

### 8.1 Variable names by role

```python
vehicle_create_request
vehicle_update_request
vehicle_record
vehicle_response
query_result
db_session
telemetry_message
charging_session_record
```

Avoid:

```python
data
item
result
model
schema
session
```

Prefer `db_session` over `db` when a function has multiple kinds of sessions
or there's a risk of confusion with the database engine.

### 8.2 Singular and plural

A single object uses the singular:

```python
vehicle_record
telematic_model
```

A collection uses the plural:

```python
vehicles
telematics
charging_sessions
```

### 8.3 Boolean

Booleans should have a prefix:

```python
is_active
is_deleted
has_vehicle
can_retry
should_close
```

Avoid ambiguous boolean names like `active`, `deleted`, `retry` when they
aren't inside a clear enough context.

### 8.4 ID, timestamp, and units

An ID must state the kind of object clearly:

```python
vehicle_id
telematic_id
station_id
evse_id
connector_id
session_id
```

**`_id` means a system ID** (owner decision, 2026-10-03): a UUID (or BIGINT)
generated by this system and meaningless outside it. A table's primary key is
`<singular>_id`, never a bare `id` (a bare `id` makes `a.id = b.id` joins
silently wrong and multi-table results ambiguous), and a foreign key keeps the
same name as the key it points to. A **real-world identifier** is named for what
it is and never ends in a bare `_id`: `tax_code`, `vin`, `license_plate`,
`telematic_serial`, `phone_number`, `citizen_id_number`. An identifier issued
by an external protocol or system keeps its source prefix so it is never
mistaken for ours: `ocpp_evse_id`, `ocpp_connector_id`, `ocpp_transaction_id`
are the charger's own numbers.

Timestamps use the `_at` suffix:

```python
created_at
updated_at
recorded_at
received_at
deleted_at
```

When a unit could cause confusion, put the unit in the name:

```python
meter_start_wh
distance_km
energy_kwh
duration_seconds
battery_temperature_celsius
```

Standard domain abbreviations are kept as-is: `UUID`, `VIN`, `EVSE`, `OCPP`,
`MQTT`, `GPS`, `HTTP`.

## 9. Import rules and layer boundary

Import a module via a domain-scoped alias, in the `import ... as` form
(the dominant style in this codebase):

```python
import app.domains.vehicles.repository as vehicle_repository
import app.domains.vehicles.service as vehicle_service
```

Don't import individual functions from another module's service/repository
(`from app.domains.x.service import create_x`): the alias keeps the owning
module visible at every call site.

Don't use wildcard imports. `__init__.py` only contains a module docstring —
never export or import objects.

Between two domains, only call the public service of the domain that owns
the data. Never import `models.py` or `repository.py` of another domain
directly.

Layer responsibilities:

```text
router      HTTP request/response, status code, HTTPException
service     business rule and orchestration
repository  database query, no business rule
schemas     request/response or external message validation
models      SQLAlchemy mapping
types       enum, value object, small internal DTOs
```

## 10. Object conversion rules

A function name must express the source or destination of the conversion:

```text
to_<target>          pure mapping, no I/O
build_<target>       assemble a composite object or enrich data
resolve_<object>     look up/map via a repository or service
parse_<object>       convert raw data into an object
normalize_<field>    normalize a value
serialize_<object>   convert an object to JSON/bytes
calculate_<thing>    business calculation
```

Example of a pure mapping:

```python
def to_vehicle_reference(
    vehicle_record: VehicleModel,
) -> VehicleReference:
    """Convert an ORM model into a vehicle reference DTO."""
    return VehicleReference(
        vehicle_id=vehicle_record.vehicle_id,
        vin=vehicle_record.vin,
    )
```

Example of a response with enrichment and I/O:

```python
async def build_telematic_response(
    db_session: AsyncSession,
    telematic_record: TelematicModel,
) -> TelematicResponse:
    """Build a telematic response and enrich it with the VIN from the vehicles domain."""
    ...
```

Don't use ambiguous names like:

```text
convert(), transform(), map_data(), _response()
```

A pure mapper must never call the database, another service, or commit
itself. If a function both queries and builds an object, use `resolve_` or
`build_` and document the side effect in the docstring.

Don't put HTTP conversion logic into an ORM model, and don't let a schema
import a SQLAlchemy model. Conversion logic should live in the service, or
in a dedicated mapper module if there are enough mappers to warrant one.

Standard data flow:

```text
Repository: database → ORM Model
Service:    Model → DTO/Response
Router:     Response → HTTP
```

## 11. Docstrings, comments, and tests

The backend docstring/comment rules in
[`backend-runtime-conventions.md`](./backend-runtime-conventions.md) still
apply:

- Docstrings and comments are written in English.
- A module must describe its responsibility, scope, and limitations.
- A class must describe its role and important state.
- A function must describe its behavior, `Args`, `Returns`, `Raises`, and any side effects.
- A comment explains the reason, an invariant, transaction/concurrency/timeout behavior, or a workaround; it doesn't restate each line of code.

Test names must describe the behavior and outcome:

```python
async def test_create_vehicle_rejects_duplicate_vin():
    ...


async def test_find_active_vehicle_ignores_soft_deleted_record():
    ...
```

## 12. Checklist for adding or modifying backend code

- [ ] The class name expresses both the object and its technical role.
- [ ] A request/response schema isn't being used as an internal DTO.
- [ ] An ORM model isn't passed to another domain.
- [ ] Functions clearly distinguish query, command, resolve, and mapping.
- [ ] Variable names aren't overly generic when the object crosses multiple layers.
- [ ] ID, timestamp, boolean, and unit names are clear.
- [ ] Imports from other modules use a domain-scoped alias.
- [ ] The router contains no business rule.
- [ ] The service doesn't import FastAPI or raise `HTTPException`.
- [ ] The repository doesn't commit/rollback and contains no business policy.
- [ ] A pure mapper performs no I/O.
- [ ] Docstrings/comments were updated when the logic changed.
- [ ] `make check` passes (ruff, import-linter, mypy, smoke tests, domain-model check); `make backend-test-integration` too for schema/repository changes.
