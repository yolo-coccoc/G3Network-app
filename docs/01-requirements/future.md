# Future Components — Deferred Components

> This file records components/layers/tech/components temporarily skipped at this time in order to complete the MVP sooner.
> Whenever a component is skipped for the reason "not needed right now, but will definitely need to be added later," record it here.

---

## Purpose

- Avoid scattering placeholders throughout the source code (creates noise, hard to maintain)
- Keep a single centralized source of truth for what has been deferred
- Make it easy to review when starting the next phase

---

## List of deferred components

The items below are actual deferral decisions made in the repo, not placeholders.

### 1. API Gateway / Reverse Proxy (Traefik/Nginx)

- **Short description**: An intermediary layer between frontend and backend, handling routing, rate limiting, and authentication at the edge.
- **Purpose/role in the system**:
  - Protect the backend from malicious requests
  - Reduce backend load via edge caching
  - Centralized logging and monitoring for all API calls
  - SSL termination
- **Reason for deferral**: Not needed in the dev environment — the frontend calls the backend directly via localhost. Will be reconsidered when building `docker-compose.prod.yml`.
- **Related planner/feature**: Not directly related to a specific feature; a general infrastructure component.
- **Date recorded**: 2026-07-24
- **Additional notes**: Already proposed in CLAUDE.md Section 1 (Reverse proxy / API Gateway).

---

### 2. MQTT QoS 1+ with Retry and Duplicate Detection

- **Short description**: Upgrade MQTT from QoS 0 to QoS 1 or QoS 2, along with retry logic and a duplicate detection mechanism.
- **Purpose/role in the system**:
  - Ensure messages are delivered at least once (QoS 1) or exactly once (QoS 2)
  - Automatic retry on network failure or broker unavailability
  - Duplicate detection to avoid inserting duplicate messages into the database
  - Zero data loss when the network or process encounters an error
- **Reason for deferral**: The MVP focuses on proving the basic flow works, assuming an ideal network. QoS 0 is enough to test the end-to-end flow. Retry and duplicate detection will be added during production rollout.
- **Related planner/feature**: `backend-telemetry-ingestion.md` (AD-02, FM-01, FM-02)
- **Date recorded**: 2026-07-25
- **Additional notes**: Need to weigh the trade-off between reliability and performance. QoS 1+ will increase latency and reduce throughput.

---

### 3. Dead-Letter Queue (DLQ)

- **Short description**: A queue storing messages that could not be processed after multiple retries, for later investigation and manual handling.
- **Purpose/role in the system**:
  - No message loss on serious errors (parse error, validation error, DB schema mismatch)
  - Allows replaying messages after fixing a bug or updating the schema
  - Audit trail for debugging production issues
  - Separates failed messages from the main flow so performance is unaffected
- **Reason for deferral**: The MVP doesn't need a DLQ because volume is low and debugging can be done via logs. Will be added when scaling to production with high volume.
- **Related planner/feature**: `backend-telemetry-ingestion.md` (AD-02)
- **Date recorded**: 2026-07-25
- **Additional notes**: The DLQ could be implemented with a JSON file (simple) or a DB table (queryable). Needs a cleanup policy (delete after 7-30 days).

---

### 4. Persistent Queue

- **Short description**: A queue stored on disk instead of in-memory, ensuring no message loss on process crash or restart.
- **Purpose/role in the system**:
  - Survive process restart or crash
  - No loss of messages still in the queue when deploying a new version
  - Allows a queue larger than RAM capacity
  - Replay messages from the queue when needed
- **Reason for deferral**: The MVP uses an in-memory asyncio.Queue, which is enough for the demo. A process crash will lose messages in the queue, but that's acceptable with QoS 0. A persistent queue (SQLite, Redis, or Kafka) will be added when higher reliability is needed.
- **Related planner/feature**: `backend-telemetry-ingestion.md` (AD-02)
- **Date recorded**: 2026-07-25
- **Additional notes**: Weigh SQLite (simple, local), Redis (fast, needs extra infra), or Kafka (scales well, complex).

---

### 5. Cache Layer for Telematic Mapping

- **Short description**: An in-memory cache (Redis or dict) storing the telematic_serial → (telematic_id, vehicle_id) mapping to avoid querying the DB on every batch.
- **Purpose/role in the system**:
  - Reduces the number of DB queries from N queries/batch to 1 query/batch (or 0 on a cache hit)
  - Increases ingest throughput
  - Reduces DB load
  - Lower latency per message
- **Reason for deferral**: The active MVP currently processes messages one at a time and doesn't need anything beyond a simple lookup. When the batch path is re-enabled or volume increases, caching will be evaluated together with a benchmark to reduce repeated lookups and DB load.
- **Related planner/feature**: `backend-telemetry-ingestion.md` (AD-02)
- **Date recorded**: 2026-07-25
- **Additional notes**: If a cache is used, it needs a TTL (5-10 minutes) and invalidation when a telematic is assigned to/removed from a vehicle. Consider Redis if the cache needs to be shared across multiple worker instances.

---

### 6. Unique Constraint for message_uuid

- **Short description**: Add a unique constraint on the message_uuid column in the vehicle_telemetry table to guarantee no duplicate messages.
- **Purpose/role in the system**:
  - Prevents inserting duplicate messages (same message_uuid)
  - Database-level guarantee (not dependent on application logic)
  - Supports idempotency on retry
- **Reason for deferral**: TimescaleDB requires a unique constraint to include the partition key (recorded_at). A (message_uuid, recorded_at) constraint doesn't prevent duplicates if a message is retried with a different recorded_at. More complex duplicate detection logic is needed (e.g., a separate table tracking processed message_uuid). Will be added when QoS 1+ is implemented.
- **Related planner/feature**: `backend-telemetry-ingestion.md` (AD-02)
- **Date recorded**: 2026-07-25
- **Additional notes**: Could use a dedicated `processed_messages` table to track processed message_uuid values, with a TTL (e.g., 24h) to avoid unbounded growth.

---

### 7. Advanced Duplicate Detection

- **Short description**: More sophisticated logic for detecting and handling duplicate messages, not relying solely on the database unique constraint.
- **Purpose/role in the system**:
  - Detects duplicates right before insert (without waiting for a DB constraint violation)
  - Supports idempotency for retry logic
  - Handles the edge case of a message retried with a different recorded_at
  - Can ignore the duplicate or update the existing record
- **Reason for deferral**: The MVP uses QoS 0, so there is no retry and no need for duplicate detection. Will be added when upgrading to QoS 1+.
- **Related planner/feature**: `backend-telemetry-ingestion.md` (AD-02)
- **Date recorded**: 2026-07-25
- **Additional notes**: Could be implemented via:
  - An in-memory Bloom filter (fast, has false positives)
  - A processed_messages table with a TTL
  - A Redis SET with EXPIRE

---

### 8. Exponential Backoff for Retry

- **Short description**: Retry logic with an exponentially increasing delay (1s, 2s, 4s, 8s...) on transient errors.
- **Purpose/role in the system**:
  - Avoids retry spam when the DB or broker is down
  - Reduces load on a system that is already having issues
  - Increases the chance of success once the system recovers
  - Circuit breaker pattern to fail fast
- **Reason for deferral**: The MVP has no retry logic. The batch worker stops on error and needs a manual restart. Retry with exponential backoff will be added when higher reliability is needed.
- **Related planner/feature**: `backend-telemetry-ingestion.md` (AD-02)
- **Date recorded**: 2026-07-25
- **Additional notes**: Needs a max retry config (e.g., 3-5 attempts) and a max backoff (e.g., 60s). After retries are exhausted, route to the DLQ.

---

### 9. PostGIS Geography for telemetry location

- **Short description**: Replace the two `latitude`/`longitude` columns with a `geography(Point, 4326)` column, or add a synced geography column.
- **Purpose/role in the system**: Supports spatial indexing, radius queries, geofencing, and efficient trip history.
- **Reason for deferral**: The telemetry MVP planner decided to store coordinates using two `DOUBLE PRECISION` columns to prove out the ingest flow first.
- **Related planner/feature**: `backend-telemetry-ingestion.md` (AD-02, FM-01, FM-02)
- **Date recorded**: 2026-07-26
- **Additional notes**: Needs a migration of existing data and a decision between geometry and geography before implementation.

---

### 10. Foreign key from vehicles to fleet

- **Short description**: Convert `vehicles.fleet_id` to a UUID internal ID and create a foreign key to the table owned by the fleet domain.
- **Purpose/role in the system**: Ensures the integrity of vehicle-to-fleet assignment and complies with the rule that foreign keys must always reference internal IDs.
- **Reason for deferral**: The fleet domain and table haven't been implemented yet; no relationship placeholder should be added in source before the target model exists.
- **Related planner/feature**: FM-01…FM-07
- **Date recorded**: 2026-07-26
- **Additional notes**: When fleet is implemented, a migration is needed to convert the current `String(36)` data to UUID and add the constraint.

---

### 11. Import-linter in CI

- **Short description**: Configure `import-linter` to automatically check import boundaries between bounded contexts.
- **Purpose/role in the system**: Prevents a domain from directly importing another domain's `models.py`/`repository.py` and turns the architectural convention into a CI constraint.
- **Reason for deferral**: The CI/CD platform hasn't been chosen yet, and the import-linter package/config doesn't exist in the backend yet.
- **Related planner/feature**: General architecture rules in `CLAUDE.md`.
- **Date recorded**: 2026-07-26
- **Additional notes**: Implementation requires adding the dependency via `uv`, a config contract, and the corresponding CI job.

---

### 12. Alembic filter for objects managed by PostGIS/TimescaleDB

- **Short description**: Add `include_object` to Alembic to skip `spatial_ref_sys` and internal indexes created by TimescaleDB.
- **Purpose/role in the system**: Makes `alembic check` and autogenerate reflect only the schema managed by the application, avoiding migrations that would drop extension-owned objects.
- **Reason for deferral**: The MVP hasn't settled on CI/CD yet and migrations are currently reviewed/run manually; the Alembic head is still correct.
- **Related planner/feature**: `backend-telemetry-ingestion.md` (AD-02), general database configuration.
- **Date recorded**: 2026-07-26
- **Additional notes**: Before enabling `alembic check` in CI or using autogenerate for a new migration, this item must be completed.

---

### 13. Reconciling telematic serial between MQTT topic and payload

- **Short description**: Parse `{telematic_serial}` from the MQTT topic and reject the message if it doesn't match the `telematic_serial` in the JSON payload.
- **Purpose/role in the system**: Prevents a message from being attributed to the wrong device when the topic and payload disagree, and helps control device identity.
- **Reason for deferral**: The MVP assumes the telematic publishes to the correct topic and payload per spec.
- **Related planner/feature**: `backend-telemetry-ingestion.md` (AD-02).
- **Date recorded**: 2026-07-26
- **Additional notes**: Should be implemented alongside MQTT authentication/authorization before the production environment.

---

### 14. Making the `telematics.last_seen_at` update precise

- **Short description**: Only update `last_seen_at`, `updated_at`, and the row count when the new timestamp is actually greater than the current value.
- **Purpose/role in the system**: Keeps `updated_at` semantically correct and makes the `telematics_updated` metric/log reflect the number of devices that actually changed.
- **Reason for deferral**: The current discrepancy only affects metadata/logs; it doesn't move `last_seen_at` backward and doesn't block the MVP ingest flow.
- **Related planner/feature**: `backend-telemetry-ingestion.md` (AD-02).
- **Date recorded**: 2026-07-26
- **Additional notes**: Need to consider a suitable batch SQL approach that still keeps a single update per batch.

---

### 15. Distinguishing a nonexistent telematic from one not yet assigned to a vehicle

- **Short description**: Batch lookup returns every device including those with a null `vehicle_id`, so the service can log/report metrics for the two provisioning states separately.
- **Purpose/role in the system**: Helps operations distinguish an invalid serial from a valid device that just hasn't been assigned to a vehicle yet.
- **Reason for deferral**: Both cases are safely skipped in the MVP, and there's no provisioning operations dashboard yet.
- **Related planner/feature**: `backend-telemetry-ingestion.md` (AD-02), AD-05.
- **Date recorded**: 2026-07-26
- **Additional notes**: Implementation requires changing the mapping return type to `tuple[UUID, UUID | None]` and adding a dedicated metric.

---

### 16. Expanded automated backend test suite

- **Short description**: Add pytest, pytest-asyncio, test fixtures, and unit/integration tests for the backend.
- **Purpose/role in the system**: Protects transaction boundaries, API validation, repository queries, MQTT ingestion, the batch window, and graceful shutdown from regressions.
- **Reason for deferral**: A minimal smoke/unit test suite already exists; the rest — integration tests against PostgreSQL/TimescaleDB, MQTT/OCPP end-to-end tests, coverage, and shared test fixtures — is deferred to avoid weighing down the initial phase.
- **Related planner/feature**: The whole backend; priority on `backend-telemetry-ingestion.md` (AD-02) and vehicles AD-05.
- **Date recorded**: 2026-07-26
- **Additional notes**: `pytest`, `pytest-asyncio`, and `make backend-test` are already in place. Before setting up CI/CD, an isolated test database needs to be added and a coverage threshold decided.

---

### 24. Clear separation of configuration between `config.py` and `.env` — Completed

- **Short description**: Standardize the boundary between the schema/default configuration in `backend/app/libs/common/config.py` and the environment-specific runtime values in `backend/.env`.
- **Purpose/role in the system**:
  - `config.py` is the source of truth for variable names, data types, validation, default values, and how configuration is accessed via `Settings`.
  - `.env` only holds values that change per environment, such as the database URL, broker connection, credentials, and runtime tuning; it holds no business rules or application logic.
  - All backend source code and Alembic access configuration via `app.libs.common.config.settings`, without calling `os.getenv()` or `load_dotenv()` directly.
- **Reason for deferral**: The initial MVP already had a Pydantic-based `Settings`, but there was still a `DEBUG` variable that didn't match `APP_DEBUG`, an undeclared/unused `CORS_ORIGINS` in `.env.example`, Alembic had its own separate mechanism for reading `.env`, and some queue/batch/MQTT defaults were duplicated in source.
- **Related planner/feature**: Shared backend configuration; `backend/app/libs/common/config.py`, `backend/.env.example`, `backend/app/libs/db/migrations/env.py`, AD-02.
- **Date recorded**: 2026-07-30
- **Additional notes**: `DEBUG` was renamed to `APP_DEBUG`, the unused `CORS_ORIGINS` (no middleware consumed it) was removed, Alembic was switched to use the same `Settings`, the MQTT will and shared pagination policy were moved into configuration, the default password was removed from `config.py`, and the `.env.example` files were standardized. `DATABASE_URL` must now be provided from the environment; the remaining values have safe defaults in `config.py` and can be overridden in `.env`.
- **Date completed**: 2026-07-30

---

### 17. Centralized observability for telemetry ingestion

- **Short description**: Complete the log and metrics collection pipeline for
  the telemetry worker so it can be monitored outside the process, with
  history retention and alerting.
- **Purpose/role in the system**:
  - Collect JSON logs from `stderr` into a centralized system such as Grafana
    Loki, ELK, or an equivalent cloud service.
  - Apply retention, search, dashboards, and alerts for ingest errors,
    skipped/dropped messages, batch latency, and worker-stopped conditions.
  - Expose metrics via an HTTP endpoint or Prometheus exporter so a monitoring
    system can scrape them.
  - Persist metrics durably across restarts and aggregate figures from
    multiple worker instances.
- **Reason for deferral**: The MVP currently only provides JSON structured
  logging via Python's `StreamHandler`. Logs are only written to `stderr` for
  viewing in the terminal or via `docker logs`; there's no file logging, and
  no log shipping/retention/dashboard/alerting. Metrics/counters in the MQTT
  consumer and batch worker were removed to keep ingestion minimal, so there
  is no endpoint/exporter or process-local figures yet.
- **Related planner/feature**: `backend-telemetry-ingestion.md` steps 13-14
  (AD-02, FM-01, FM-02).
- **Date recorded**: 2026-07-27
- **Additional notes**: Step 14 moved JSON logging up to the telemetry
  entrypoint, so startup/MQTT/worker/shutdown logs now share the same output
  contract. What remains under this item is log shipping, retention,
  dashboards/alerting, and redesigning metrics from scratch if an exporter is
  needed. When implementing, avoid attaching both the old handler and the JSON
  handler (which would duplicate output), and avoid logging the same traceback
  at multiple boundaries without adding new context. The backend observability
  stack must be decided before adding any new dependency or infrastructure.

---

### 18. API to assign/unassign a telematic device for a vehicle

- **Short description**: Add the provisioning use case allowing an Admin to
  assign, change, or remove a telematic from a vehicle; telemetry already has
  the `telematics` table, foreign key, and unique constraint, but the vehicles
  domain has no corresponding business API yet.
- **Purpose/role in the system**:
  - Completes the "assign device" part of the Vehicle Management feature.
  - Ensures a vehicle has at most one telematic and prevents a device from
    being assigned incorrectly.
  - Allows operating ingestion without having to insert/update the mapping via
    manual SQL.
  - Has a clear contract for replace/unassign, conflicts, and nonexistent
    device/vehicle.
- **Reason for deferral**: The initial vehicles CRUD MVP excluded
  provisioning from scope; telemetry ingestion currently only needs the
  mapping to exist for lookup, and no Admin workflow for device management has
  been built yet.
- **Related planner/feature**: `backend-crud-vehicles.md`,
  `backend-telemetry-ingestion.md` (AD-05, AD-02).
- **Date recorded**: 2026-07-27
- **Additional notes**: Since the telematic model/repository belongs to the
  telemetry domain, vehicles must not import these internal modules directly.
  Before implementing, decide the router/use-case owner; if vehicles
  orchestrates it, it must call the public API in `telemetry/service.py`. The
  operation must be atomic and must translate unique/FK `IntegrityError` into
  a clear domain conflict.

---

### 19. Structured logging, request metrics, and a real health check for the API process

- **Short description**: Apply the logging/observability output contract
  already established for telemetry to the FastAPI API process and HTTP
  domains such as vehicles.
- **Purpose/role in the system**:
  - Configure JSON logging at the FastAPI lifespan/process boundary so
    startup, shutdown, and router/service/repository errors share the same
    format.
  - Record structured context for important requests/operations without
    logging sensitive data.
  - Provide counters/latency/error metrics for CRUD/API instead of just an
    access log.
  - Make health/readiness reflect the database and lifecycle instead of always
    returning `{"status": "healthy"}` as long as the Python process is
    running.
- **Reason for deferral**: Step 13 only implemented the formatter and
  process-local metrics for the telemetry worker. `app.api.main` doesn't call
  `configure_logging()` yet; vehicles has no structured operation logs/metrics,
  and `/health` doesn't verify dependencies or readiness.
- **Related planner/feature**: `backend-crud-vehicles.md`, the API backend in
  general, and item 17 of `future.md`.
- **Date recorded**: 2026-07-27
- **Additional notes**: Need to settle the boundary between access logs,
  business audit logs, and application error logs to avoid duplicate logging.
  `/live` and `/ready` could be split when deployed via an orchestrator; avoid
  querying heavy dependencies on every health request.

---

### 20. Standardizing source documentation and OpenAPI examples for the vehicles domain

- **Short description**: Review vehicles' module/class/function docstrings,
  comments, and schema examples against the new convention in `CLAUDE.md`.
- **Purpose/role in the system**:
  - Vietnamese docstrings/comments accurately describe business rules,
    transaction ownership, exceptions, and partial updates.
  - `VehicleCreate`, `VehicleUpdate`, and response/list schemas have
    consistent examples for Swagger and planners to use as a contract.
  - Removes outdated/incorrect descriptions such as field names, HTTP methods,
    or behavior that no longer match the implementation.
- **Reason for deferral**: The vehicles domain was implemented before the
  detailed Vietnamese docstring/comment convention was finalized. Many
  docstrings are currently short and in English; `json_schema_extra` examples
  were dropped at some point in history, while telemetry schemas already have
  detailed examples.
- **Related planner/feature**: `backend-crud-vehicles.md` (AD-05), the coding
  convention rules in `CLAUDE.md`.
- **Date recorded**: 2026-07-27
- **Additional notes**: This is documentation debt, not an API behavior
  change. When doing this work, use the current source as the source of truth
  and do not restore old examples if a field/enum has changed.

---

### 21. Syncing the planner and acceptance evidence for the vehicles domain

- **Short description**: Rewrite `backend-crud-vehicles.md` following the
  planner structure already standardized for telemetry, reflecting the actual
  implementation/migrations and recording smoke/integration test evidence.
- **Purpose/role in the system**:
  - Clearly distinguishes steps that are implemented, steps that were only
    manually tested, and parts that don't exist yet.
  - Fixes outdated contracts around `plate_number`/`license_plate`,
    `team_id`/`fleet_id`, UUID, `PUT`/`PATCH`, HTTP conflict status, and
    timezone.
  - Makes the document a trustworthy reference for the next CRUD domain
    planner.
- **Reason for deferral**: The vehicles planner is still in "Planned" status
  and most of its checklist hasn't been updated even though the source already
  exists; the current audit prioritizes finishing the telemetry planner and
  only records the gap for vehicles.
- **Related planner/feature**: `backend-crud-vehicles.md` (AD-05).
- **Date recorded**: 2026-07-27
- **Additional notes**: Do not mark a test as passing just because the source
  exists. Automated tests are already tracked collectively under item 16; this
  item focuses on the accuracy and traceability of the planner.

---

### 22. Health/readiness and graceful drain for telemetry ingestion

- **Short description**: Re-add the health/readiness endpoint and the
  mechanism to stop accepting MQTT and drain the queue with a timeout for
  telemetry ingestion, for when the system needs to run in production.
- **Purpose/role in the system**:
  - Lets the orchestrator know the process is ready to receive data and
    detects a failed consumer/worker.
  - Reduces loss of telemetry already received into the queue during a
    planned deploy or shutdown.
  - Provides a shutdown timeout and a clear lifecycle state.
- **Reason for deferral**: The MVP deliberately keeps the process, task, and
  queue entirely in RAM, accepting the loss of data still in the queue on
  shutdown, to keep the lifecycle and batch worker simpler.
- **Related planner/feature**:
  `backend-telemetry-ingestion.md` (AD-02), step 14.
- **Date recorded**: 2026-07-27
- **Additional notes**: When re-implementing this, base the
  liveness/readiness contract and drain timeout on the actual deployment
  environment; do not restore the old orchestration verbatim without
  confirming it's still appropriate.

---

### 23. Startup probe and lifecycle failure propagation for telemetry ingestion

- **Short description**: Re-add the startup dependency check and a clear API
  lifecycle so the entrypoint can monitor the consumer/worker without
  accessing private state.
- **Purpose/role in the system**:
  - Checks the database via the shared `async_session_factory` before
    accepting MQTT, to fail fast if the DB isn't ready.
  - Retrieves the exception from the background task to avoid "Task exception
    was never retrieved" and lets the process exit non-zero when the
    consumer/worker dies unexpectedly.
  - Provides a public lifecycle API such as `MessageWorker.wait()` or a clear
    task-handle mechanism, instead of the entrypoint accessing `worker._task`
    directly.
  - Distinguishes signal-triggered shutdown from runtime-failure shutdown in
    the log and exit code.
- **Reason for deferral**: The MVP currently prioritizes a very short
  entrypoint: create the RAM queue, start the consumer/worker, wait for the
  first task to finish, then clean up. When the DB/service fails, the worker
  still rolls back the transaction and logs the traceback, but the entrypoint
  currently ignores the exception of the task that finished after
  `asyncio.wait()`. So `run()` can clean up normally and the process can exit
  with code 0, leaving the supervisor unaware the worker died and unable to
  restart/alert correctly. This is a gap in lifecycle failure propagation, not
  a deliberate choice to ignore errors within a transaction; the startup probe
  and detailed propagation are deferred to keep the MVP's MQTT → DB flow
  short.
- **Related planner/feature**:
  `backend-telemetry-ingestion.md` (AD-02), steps 14-15.
- **Date recorded**: 2026-07-28
- **Additional notes**: Should be implemented together with item 22 if
  preparing to run under an orchestrator or if a reliable alert/exit code is
  needed. When adding this back, read `task.exception()` or await task
  completion in the entrypoint, distinguish a signal shutdown from a task
  failure, and check both the consumer task and the worker task. Keep the
  lifecycle API public, avoid accessing `worker._task`, and don't pull back
  the entire old runtime orchestration unless necessary.

### 25. Batch processing for telemetry ingestion

- **Short description**: Re-enable the batch-based telemetry processing path,
  including the batch window, batch lookup, and bulk insert into TimescaleDB.
- **Purpose/role in the system**:
  - Reduces the number of transactions and database round-trips as throughput
    increases.
  - Looks up the telematic mapping once for many messages.
  - Leverages PostgreSQL Core bulk insert to optimize ingest performance.
  - Allows measuring and choosing the trade-off between per-message latency
    and throughput.
- **Reason for deferral**: The MVP currently prioritizes low latency, an
  easy-to-observe processing flow, and per-message error isolation; there's no
  benchmark yet showing that batching is needed at the current volume. The
  batch window would also add unnecessary latency for the demo.
- **Related planner/feature**: `backend-telemetry-ingestion.md` Step 16
  (AD-02, FM-01, FM-02).
- **Date recorded**: 2026-07-30
- **Additional notes**: The batch code is still kept in
  `backend/app/domains/telemetry/ingestion/batch_worker.py`,
  `telemetry.service.process_batch()`,
  `telematics.service.resolve_mappings_by_serial()`, and
  `telemetry.repository.bulk_insert_telemetry()`, but the active entrypoint
  doesn't call it. Before re-enabling it, benchmark a representative workload,
  settle the transaction/failure semantics and backpressure, and update the
  corresponding smoke/E2E tests.

### 26. Extended charging sessions business logic

- **Short description**: Add business logic that isn't part of the MVP for
  storing charging sessions, including RFID/idToken authorization,
  driver/vehicle mapping, remote start/stop, pricing, payment, webhooks,
  overdue handling, and debt.
- **Purpose/role in the system**:
  - Decides who is allowed to start/stop a session and links the session to a
    person/vehicle.
  - Controls a session remotely via the `charging_stations` OCPP transport.
  - Bills, collects payment, and manages debt after a session ends.
- **Reason for deferral**: The MVP currently only needs `charging_stations` to
  receive device data and `charging_sessions` to store events, meter values,
  and session lifecycle; there isn't yet a stable enough provider, tariff, or
  identity contract, or business rules, to implement this safely.
- **Related planner/feature**: `docs/02-planners/backend-charging.md`,
  AD-03 and S-02; the corresponding billing/payment items in
  `docs/01-requirements/feature-list.md`.
- **Date recorded**: 2026-07-31
- **Additional notes**: Do not create a `charging_remote_commands`, tariff,
  payment, debt, or authorization placeholder table in the MVP. When resuming
  this work, the charging planner and `CLAUDE.md` must be updated before
  writing code.

### 27. Reliability and technical status path of the charging MVP

- **Short description**: Restore the non-happy-path handling for charging,
  including reconnect, the connection registry, offline/heartbeat timeout,
  retry, duplicate/idempotency handling, out-of-order events, interruptions,
  reconciliation conflicts, technical status history, and raw OCPP payload
  auditing.
- **Purpose/role in the system**:
  - Protects the session lifecycle when the WebSocket or station loses
    connection.
  - Allows message replay, duplicate detection, and prevents state from moving
    backward.
  - Tracks station/EVSE/connector technical status independently of the
    transaction lifecycle.
  - Explains and reconciles meter resets, payload conflicts, or late-arriving
    data.
- **Reason for deferral**: The new `backend-charging-mvp-ideal.md` planner
  fixes the assumptions of online/active status, in-order message arrival, and
  no duplicates, in order to reduce the amount of code that needs to be
  understood in the local MVP. The old source is no longer a contract that
  must be preserved in the MVP source; leftover pieces kept as comments can be
  cleaned up/removed. Individual pieces must not be re-enabled on their own
  without a full reliability contract.
- **Related planner/feature**: `backend-charging.md`,
  `backend-charging-mvp-ideal.md`, AD-03 and S-02.
- **Date recorded**: 2026-08-02
- **Additional notes**: When resuming this work, design the migration for
  history/idempotency, timeout settings, a network-failure simulator, and
  duplicate/reconnect tests before going to production.

### 28. Topology metadata and charging helpers excluded from the ideal MVP

- **Short description**: Remove from the active model/API the topology
  metadata not needed for the local happy path, including
  manufacturer/model/serial/firmware/location info, administrative status,
  connection status, technical status, capability, connector type/power
  rating, and status/heartbeat timestamps; also remove the location helper,
  status filter, and the corresponding CRUD parameters.
- **Purpose/role in the system**:
  - Device metadata serves station/EVSE/connector profile administration
    beyond the session lifecycle.
  - Location/capability/power rating serve the map, finding a suitable
    station, and device policy when integrating real devices.
  - Administrative/technical/connection snapshots serve monitoring and
    technical status history, independently of `Started →
    Updated/MeterValues → Ended`.
- **Reason for deferral**: The MVP assumes topology is pre-provisioned and
  that station, EVSE, and connector are always online/active; these fields
  don't participate in resolving identity or storing the session lifecycle.
  Per the decision on 2026-08-02, legacy source may be deleted instead of
  having to be kept as comments. The active schema/API only keeps identity,
  topology FKs, timestamps, and soft-delete.
- **Related planner/feature**: `backend-charging-mvp-ideal.md` Steps 1-2,
  `backend-charging.md`, AD-03, and the S-02 lifecycle section.
- **Date recorded**: 2026-08-02
- **Additional notes**: When resuming this work, the real-device contract,
  PostGIS location, capability schema, power rating/connector type, status
  snapshot, and CRUD/monitoring API must all be settled before creating a
  migration. Do not restore individual fields piecemeal or create a
  placeholder table/status enum in the MVP.

### 29. Query optimization and code-quality cleanup for simulator/telemetry

- **Short description**: Address technical debt that doesn't block the MVP,
  including an N+1 query when building the telematic response, unifying the
  `error_codes` contract across the MQTT payload/schema/database, and
  tightening error handling/type checking in the simulator.
- **Purpose/role in the system**:
  - Reduces the number of database queries when the telematic list API has to
    resolve the VIN of many devices; currently each item can trigger an
    additional vehicle query.
  - Keeps a single error representation from the telemetry message all the
    way to the JSONB database and API, avoiding a situation where the payload
    accepts `list[str]` but the data is stored as an object wrapped in
    `{"codes": [...]}` without a clear contract.
  - Helps the simulator distinguish network/HTTP/JSON errors from programming
    errors, while keeping the JSON response type precise enough for mypy to
    check.
  - Makes simulator data configurable instead of depending on a fixed device
    prefix, VIN, API/broker address, and transaction identity hardcoded in
    source.
- **Reason for deferral**: The local MVP has a small number of devices, the
  list API isn't on the ingest hot path, and the simulator mainly serves
  manual smoke testing. None of these issues break the current telemetry
  lifecycle, but they will affect latency, error diagnosability, and test
  reliability as the number of devices grows. Changing `error_codes` may also
  require a migration or API versioning, so it shouldn't be changed
  unilaterally while the MVP contract hasn't been finalized.
- **Related planner/feature**: `backend-telemetry-ingestion.md` (AD-02),
  `backend-crud-telematics.md` (if added), the local simulator, and the coding
  convention in `CLAUDE.md`.
- **Date recorded**: 2026-08-04
- **Additional notes**:
  - `telematics.service._response()` currently calls a separate vehicle
    lookup for each item in `list_telematics()`. When resuming this, prefer a
    bulk lookup or an appropriate query projection; don't let the service
    directly import another domain's repository/model if the solution needs
    to cross a domain boundary.
  - Need to decide whether `error_codes` is a list of error codes, an object
    with a `codes` field, or a versioned schema, before changing the
    model/migration/API.
  - `simulator/telematic_simulator.py` should catch specific exceptions at the
    HTTP and JSON layers; `simulator/seed_simulator_devices.py` should use a
    clear response schema/type instead of an `Any` value from `json.loads()`.
  - The device prefix, VIN, license plate, API URL, MQTT endpoint, and
    transaction ID should become configuration/CLI arguments with validation.
    When implementing this, keep the simulator deterministic when replay is
    needed and avoid logging sensitive information.

### 30. Assess whether the `telematics` domain can be merged into `vehicles`

- **Short description**: Consider moving the telematic device profile and the
  device ↔ vehicle mapping into the same `vehicles` bounded context, similar
  to how `charging_stations` owns the entire Station → EVSE → Connector
  topology. This is only a possible future restructuring; the two domains
  currently remain independent.
- **Purpose/role in the system**:
  - `vehicles` could own the vehicle profile, the device mounted on the
    vehicle, and the mapping lifecycle within a single public service.
  - The telemetry ingestion flow could resolve serial and vehicle within the
    same bounded context, reducing one cross-domain dependency direction.
  - The architecture would be simpler if a telematic is just a device
    dependent on a vehicle with no independent management business logic.
- **Reason for deferral**: `telematics` and `vehicles` are currently kept
  separate because a telematic can be provisioned ahead of time, swapped
  during a vehicle's lifecycle, or managed as an independent device; ingestion
  is also currently using the public `telematics` service to resolve
  `(telematic_id, vehicle_id)`. There's no decision yet that the MVP only
  supports a single fixed telematic per vehicle.
- **Conditions for merging**: Confirm a new contract for the number of devices
  per vehicle, provisioning, device replace/unassign, mapping history, device
  status, and data ownership. If an independent device lifecycle or multiple
  device types are still needed, keep the domains separate.
- **Work needed before implementation**: Review `CLAUDE.md`, the backend
  planners, `feature-list.md`, dependency rules, the `telematics` public
  service, the MQTT ingestion flow, schema/repository/model, and migrations.
  Do not merge by simply renaming a directory or creating a temporary
  forwarding layer.
- **Related planner/feature**: backend domain structure, AD-02, AD-05, and
  the telemetry/vehicles/telematics planners.
- **Date recorded**: 2026-08-03

### 31. Operational error handling and observability for the OCPP gateway

- **Short description**: Add an operational error-handling policy for the
  OCPP gateway beyond the happy path, including mapping database errors
  during the handshake, an HTTP `503` response, logging disconnects with the
  close code, and telemetry/metrics for per-station errors.
- **Purpose/role in the system**:
  - Distinguishes an unprovisioned station, database unavailability, and a
    handler failure.
  - Provides enough information for operations, alerting, and investigating
    real connection failures.
  - Standardizes closing or keeping the connection after an error instead of
    letting the framework handle it by default.
- **Reason for deferral**: The OCPP MVP assumes the station is
  pre-provisioned, the database is up, input is valid, and the WebSocket is
  stable. The active path currently lets a database handshake error propagate
  at the process boundary, skips a dedicated `503` response, uses an
  assertion for the invariant after the handshake, and skips detailed logging
  when a station closes the connection normally, in order to keep the gateway
  short and focused on the `Started → Updated/MeterValues → Ended` flow.
- **Related planner/feature**:
  `docs/02-planners/backend-charging-mvp-ideal.md` Step 4, AD-03 and S-02.
- **Date recorded**: 2026-08-04
- **Additional notes**: When implementing for production or the reliability
  path, the error contract, health/readiness signal, close-code policy,
  structured metrics/logging, and a database outage test must all be settled
  before enabling individual branches one at a time.

### 32. Guarding against a transaction ID being reused after a terminal session

- **Short description**: Add a guard for the case where a Charging Station
  resends the same `transaction_id` after the previous session has already
  `completed`, especially when the simulator or a real device reuses the ID
  for a new session.
- **Purpose/role in the system**:
  - Preserves the invariant that each `(station_id, ocpp_transaction_id)` pair
    represents exactly one charging session.
  - Prevents a new session's `Started` from mutating an already-completed
    aggregate or creating ambiguous data while the database has no
    corresponding unique constraint.
  - Prevents a late-arriving `Updated`, `Ended`, or `MeterValues` from
    updating the meter, events, and `updated_at` of a terminal session.
- **Reason for deferral**: The charging MVP currently assumes transaction
  messages arrive in order, without duplicates, and without ID reuse; the
  active service doesn't yet have full branches to distinguish duplicates,
  conflicts, stale events, and a reused transaction ID. It has been observed
  that the simulator can send `Started` with the same ID after the previous
  session completed, so the raw OCPP `eventType`, the actual migration, and
  the database constraint all need review before finalizing the behavior.
- **Contract to settle when resuming this work**:
  - A `Started` with the same `(station_id, transaction_id)` as an active
    session is a duplicate/no-op or a conflict, depending on the fingerprint.
  - A `Started` with the same key as a terminal session must be rejected; a
    new session must use a new transaction ID.
  - Events and meter values for a terminal session may only be
    ACKed/no-opped if identified as a valid duplicate; new or conflicting
    data must be rejected/conflicted and must not modify the aggregate.
- **Work needed before implementation**: Confirm the
  `uq_charging_sessions_station_transaction` unique constraint on the running
  database, add OCPP tests for duplicate/reuse after `Ended`, add terminal
  guard tests for every event and meter path, and log the raw event type
  along with the station/transaction identity at a level sufficient for
  investigation without recording sensitive raw payloads.
- **Related planner/feature**: `backend-charging.md`,
  `backend-charging-mvp-ideal.md`, AD-03 and S-02.
- **Date recorded**: 2026-08-04

### 33. Expanded telemetry monitoring and alerting API

- **Short description**: Add an API to read the full telemetry history, a
  vehicle/station map, aggregated connector status, battery/anomaly alerts,
  and a mechanism to push alert thresholds to devices.
- **Purpose/role in the system**:
  - Provides historical data for dashboards and trip tracing.
  - Aggregates fleet, charging station, and connector status for an
    operations screen.
  - Detects abnormal SOC/battery temperature, prevents duplicate alerts, and
    syncs alert configuration with the telematic.
- **Reason for deferral**: The current baseline only needs telemetry
  ingestion and an API to read a vehicle's latest record. There's no stable
  contract yet for history queries, connector snapshots, notifications,
  device ACKs, or fleet-based access control; no alert table, API, or MQTT
  command placeholder should be created in the MVP.
- **Related planner/feature**: `backend-telemetry-query-api.md`,
  `backend-telemetry-ingestion.md`, FM-01, FM-02, and items 3.1/3.7/4.5 in
  `docs/01-requirements/feature-list.md`.
- **Date recorded**: 2026-09-15
- **Additional notes**: When resuming this work, the scope of
  history/map/connector and the alert lifecycle must be settled separately
  before creating a migration. Do not assume these APIs already exist just
  because telemetry data is already stored in TimescaleDB.

---

## Update rules

1. **When to record an entry**: When a developer or AI agent decides to skip/remove a component for the reason "not needed right now, but will definitely need to be added later".
2. **When not to record an entry**: When the component genuinely isn't needed for the system (no plan to add it in the future).
3. **Format**: Add a new entry following the template above, numbered sequentially.
4. **Review**: Periodically (e.g., every sprint) review this file to plan implementation.
