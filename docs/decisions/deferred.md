# Deferred work

> Work deliberately left for later to reach the MVP sooner (formerly `future.md`; renamed 2026-10-04, item numbers unchanged — "deferred item 27" and the older "`future.md` item 27" mean the same entry).
> Whenever a component is skipped for the reason "not needed right now, but will definitely need to be added later," record it here.

---

## Purpose

- Avoid scattering placeholders throughout the source code (creates noise, hard to maintain)
- Keep a single centralized source of truth for what has been deferred
- Make it easy to review when starting the next phase

---

## List of deferred components

The items below are actual deferral decisions made in the repo, not placeholders.
Items that were resolved, completed or superseded are moved, with their
number unchanged, to [deferred-resolved.md](./deferred-resolved.md) (currently
items 9, 10, 12, 14, 18, 24, 35, 47, 49, 54, 63 and 82-85), so a reference like
"`deferred.md` item 10" can be found there.

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
- **Related planner/feature**: `backend-telemetry-ingestion.md` (F-A1, F-A5)
- **Date recorded**: 2026-07-25
- **Additional notes**: Need to weigh the trade-off between reliability and performance. QoS 1+ will increase latency and reduce throughput. When this is revisited, reconsider it together with transport-level security (TLS/mTLS on the MQTT connection, item 36) rather than in isolation — raising QoS alone doesn't improve delivery guarantees without also changing the publisher (currently hardcoded to QoS 0 in `simulator/telematic_simulator.py`) and doesn't address message authenticity/integrity, which is a separate concern from delivery reliability.

---

### 3. Dead-Letter Queue (DLQ)

- **Short description**: A queue storing messages that could not be processed after multiple retries, for later investigation and manual handling.
- **Purpose/role in the system**:
  - No message loss on serious errors (parse error, validation error, DB schema mismatch)
  - Allows replaying messages after fixing a bug or updating the schema
  - Audit trail for debugging production issues
  - Separates failed messages from the main flow so performance is unaffected
- **Reason for deferral**: The MVP doesn't need a DLQ because volume is low and debugging can be done via logs. Will be added when scaling to production with high volume.
- **Related planner/feature**: `backend-telemetry-ingestion.md` (F-A1)
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
- **Related planner/feature**: `backend-telemetry-ingestion.md` (F-A1)
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
- **Related planner/feature**: `backend-telemetry-ingestion.md` (F-A1)
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
- **Related planner/feature**: `backend-telemetry-ingestion.md` (F-A1)
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
- **Related planner/feature**: `backend-telemetry-ingestion.md` (F-A1)
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
- **Related planner/feature**: `backend-telemetry-ingestion.md` (F-A1)
- **Date recorded**: 2026-07-25
- **Additional notes**: Needs a max retry config (e.g., 3-5 attempts) and a max backoff (e.g., 60s). After retries are exhausted, route to the DLQ.

---

### 11. Import-linter in CI

- **Short description**: Configure `import-linter` to automatically check import boundaries between bounded contexts.
- **Purpose/role in the system**: Prevents a domain from directly importing another domain's `models.py`/`repository.py` and turns the architectural convention into a CI constraint.
- **Reason for deferral**: The CI/CD platform hasn't been chosen yet, and the import-linter package/config doesn't exist in the backend yet.
- **Related planner/feature**: General architecture rules in `CLAUDE.md`.
- **Date recorded**: 2026-07-26
- **Additional notes**: Implementation requires adding the dependency via `uv`, a config contract, and the corresponding CI job.
- **Update 2026-10-01**: the dependency and the contracts now exist (`[tool.importlinter]` in `backend/pyproject.toml`, one `forbidden` contract per domain, forbidding every module except `service`, `types` and `exceptions`) and run locally in `make lint`/`make check` and the git pre-commit hook. Only the CI job remains, blocked on the CI platform decision.

---

### 13. Reconciling telematic serial between MQTT topic and payload

- **Short description**: Parse `{telematic_serial}` from the MQTT topic and reject the message if it doesn't match the `telematic_serial` in the JSON payload.
- **Purpose/role in the system**: Prevents a message from being attributed to the wrong device when the topic and payload disagree, and helps control device identity.
- **Reason for deferral**: The MVP assumes the telematic publishes to the correct topic and payload per spec.
- **Related planner/feature**: `backend-telemetry-ingestion.md` (F-A1).
- **Date recorded**: 2026-07-26
- **Additional notes**: Should be implemented alongside MQTT authentication/authorization before the production environment.

---

### 15. Distinguishing a nonexistent telematic from one not yet assigned to a vehicle

- **Short description**: The serial lookup (`telematics.service.resolve_mapping_by_serial`, used by ingestion) returns every device including those with a null `vehicle_id`, so the service can log/report metrics for the two provisioning states separately.
- **Purpose/role in the system**: Helps operations distinguish an invalid serial from a valid device that just hasn't been assigned to a vehicle yet.
- **Reason for deferral**: Both cases are safely skipped in the MVP, and there's no provisioning operations dashboard yet.
- **Related planner/feature**: `backend-telemetry-ingestion.md` (F-A1), F-F2.
- **Date recorded**: 2026-07-26
- **Additional notes**: Implementation requires making `TelematicVehicleMapping.vehicle_id` (`telematics/types.py`) `UUID | None` and adding a dedicated metric.

---

### 16. Expanded automated backend test suite

- **Short description**: Add pytest, pytest-asyncio, test fixtures, and unit/integration tests for the backend.
- **Purpose/role in the system**: Protects transaction boundaries, API validation, repository queries, MQTT ingestion, the batch window, and graceful shutdown from regressions.
- **Reason for deferral**: A minimal smoke/unit test suite already exists; the rest — integration tests against PostgreSQL/TimescaleDB, MQTT/OCPP end-to-end tests, coverage, and shared test fixtures — is deferred to avoid weighing down the initial phase.
- **Related planner/feature**: The whole backend; priority on `backend-telemetry-ingestion.md` (F-A1) and vehicles F-F2.
- **Date recorded**: 2026-07-26
- **Additional notes**: `pytest`, `pytest-asyncio`, and `make backend-test` are already in place. Before setting up CI/CD, an isolated test database needs to be added and a coverage threshold decided.

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
  (F-A1, F-A5).
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
  general, and item 17 of `deferred.md`.
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
- **Related planner/feature**: `backend-crud-vehicles.md` (F-F2), the coding
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
- **Related planner/feature**: `backend-crud-vehicles.md` (F-F2).
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
  `backend-telemetry-ingestion.md` (F-A1), step 14.
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
  `backend-telemetry-ingestion.md` (F-A1), steps 14-15.
- **Date recorded**: 2026-07-28
- **Additional notes**: Should be implemented together with item 22 if
  preparing to run under an orchestrator or if a reliable alert/exit code is
  needed. When adding this back, read `task.exception()` or await task
  completion in the entrypoint, distinguish a signal shutdown from a task
  failure, and check both the consumer task and the worker task. Keep the
  lifecycle API public, avoid accessing `worker._task`, and don't pull back
  the entire old runtime orchestration unless necessary.
- **Update 2026-10-01 — lifecycle half done**: `MessageWorker.start()` now
  returns its task (the entrypoint no longer reads `worker._task`), and
  `telemetry/ingestion/entrypoint.py` treats only the shutdown signal as a
  clean end: a consumer or worker task that stops first has its exception
  re-raised after cleanup (or a `RuntimeError` when it stopped without
  one), so the process exits with code 1. Still open: the startup database
  probe before accepting MQTT.

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
  (F-A1, F-A5).
- **Date recorded**: 2026-07-30
- **Additional notes**: Before building it, benchmark a representative
  workload, settle the transaction/failure semantics and backpressure, and
  add smoke/E2E tests.
- **Update 2026-10-01 — dormant code removed**: per the "no preemptive
  batching" rule (`.claude/rules/backend-runtime-conventions.md`), the unused
  implementation was deleted rather than kept. Its shape, for whoever picks
  this up: a `BatchWorker` drained the queue every `TELEMETRY_FLUSH_INTERVAL`
  seconds or `TELEMETRY_BATCH_SIZE` messages; `telemetry.service.process_batch`
  resolved all serials in one `telematics` call
  (`resolve_mappings_by_serial`, an `IN (...)` lookup), skipped unmapped
  messages, and wrote the rest with one Core multi-row
  `INSERT ... ON CONFLICT DO NOTHING` (`bulk_insert_telemetry`), returning
  processed/skipped counts. It never ran alert detection - a revived version
  must. The code is in git history before commit "refactor: cross-domain
  foundations" (2026-10-01).

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
- **Related planner/feature**: `docs/planners/done/backend-charging.md`,
  F-G2 and F-B2; the corresponding billing/payment items in
  `docs/product/feature-list.md`.
- **Date recorded**: 2026-07-31
- **Additional notes**: Do not create a `charging_remote_commands`, tariff,
  payment, debt, or authorization placeholder table in the MVP. When resuming
  this work, the charging planner and `CLAUDE.md` must be updated before
  writing code.

- **Update 2026-09-24 (`docs/planners/backend-ocpp16-charger-integration.md`)**: the `idTag` presented at a charger is now stored on the session (`charging_sessions.id_tag`) and `Authorize`/`StartTransaction` accept every tag (decision D7). There is still no tag registry, no driver/vehicle mapping and no remote start/stop (see items 62 and 74).

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
  `backend-charging-mvp-ideal.md`, F-G2 and F-B2.
- **Date recorded**: 2026-08-02
- **Additional notes**: When resuming this work, design the migration for
  history/idempotency, timeout settings, a network-failure simulator, and
  duplicate/reconnect tests before going to production.
  **2026-09-17 update (F-B2)**: four scoped correctness fixes landed on
  top of this deferral without reopening it - `seq_no` is now persisted
  (enabling future dedup, not implementing it), a session's status can
  only move forward (an event for an already-`COMPLETED` session is
  refused rather than silently applied), a stale `MeterValues` can no
  longer overwrite a newer reading, and OCPP `measurand`/`unitOfMeasure`
  are read instead of assuming everything is Wh. Accepted consequence of
  the COMPLETED-session guard: a station that never received its `Ended`
  ACK will retry and get a repeated `CALLERROR` on every retry, since no
  queue/backoff/idempotent-replay exists yet - that remains this item's
  job. See `docs/planners/done/backend-charging-ingest-fixes.md`.

- **Update 2026-09-24 (`docs/planners/backend-ocpp16-charger-integration.md`)**: two pieces are done. (1) Raw OCPP payload auditing: `charging_ocpp_messages` stores every frame, both directions, verbatim (no read API or retention, item 79). (2) For OCPP 1.6J, `MeterValues`/`StopTransaction` find their session in the database by `(station, transactionId)`, so the per-connection EVSE→session map no longer breaks a reconnect (the 2.0.1 adapter still has that in-memory map). Everything else stays deferred: retry, duplicate/idempotency, out-of-order recovery, orphaned sessions after a reboot mid-session (a `StartTransaction` on a connector that already has an `active` session only logs a warning), back-filled offline records for a completed session, stale connector status after a disconnect (item 76), and offline detection beyond the derived `is_online`. The policy is to be decided from real-charger logs (planner Step 10).

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
  `backend-charging.md`, F-G2, and the F-B2 lifecycle section.
- **Date recorded**: 2026-08-02
- **Additional notes**: When resuming this work, the real-device contract,
  PostGIS location, capability schema, power rating/connector type, status
  snapshot, and CRUD/monitoring API must all be settled before creating a
  migration. Do not restore individual fields piecemeal or create a
  placeholder table/status enum in the MVP.
- **Partial resolution (2026-09-17)**: the *directory/descriptive* subset —
  location (PostGIS geography), power rating, connector standard, operating
  hours, and maintenance status — is now implemented on `charging_stations`
  per F-C1 (migration `0005_station_directory_fields`), as simple
  station-level aggregate fields rather than modeled per EVSE/connector.
  Everything else this item covers remains deferred, unchanged:
  manufacturer/model/serial/firmware, administrative/connection/technical
  status (live OCPP-derived state — that's item 27's reliability path), the
  capability schema, and the location/status *search* helpers (radius
  search, status filter).
- **Further partial resolution (2026-09-17)**: the location *search*
  helper is also done — F-D1's `GET /charging-stations/nearby` (`ST_DWithin`
  radius search filtered by connector standard/power/maintenance status).
  Per-connector *status* now exists too (F-C2, `ChargingConnectorStatus` on
  `ChargingConnectorModel`), but it isn't a *status filter* on the nearby
  search yet - see item 49. Administrative/connection status and the
  capability schema remain fully deferred.

- **Further partial resolution (2026-09-24, `docs/planners/backend-ocpp16-charger-integration.md`)**: the charger's device metadata (vendor, model, serial, firmware, protocol version, last boot), connection liveness (`last_seen_at`, with `is_online` derived at read time — no stored flag, no sweeper) and, for 1.6J, the status of the whole charger (connector 0) plus per-connector `errorCode`/`vendorErrorCode`/`info` are now stored; the charger's capabilities are captured (not modelled) as `GetConfiguration` snapshots (`SupportedFeatureProfiles`). Still deferred: administrative status, technical status history, a capability *schema*, and per-gun power/connector standard (item 80).

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
- **Related planner/feature**: `backend-telemetry-ingestion.md` (F-A1),
  `backend-crud-telematics.md` (if added), the local simulator, and the coding
  convention in `CLAUDE.md`.
- **Date recorded**: 2026-08-04
- **Additional notes**:
  - `telematics.service.build_telematic_response()` calls a separate
    vehicle lookup for each item in `list_telematics()`. When resuming this, prefer a
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
- **Related planner/feature**: backend domain structure, F-A1, F-F2, and
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
  `docs/planners/done/backend-charging-mvp-ideal.md` Step 4, F-G2 and F-B2.
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
  `backend-charging-mvp-ideal.md`, F-G2 and F-B2.
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
  `backend-telemetry-ingestion.md`, F-A2, F-A3, F-A4, F-C2, and related items
  in `docs/product/feature-list.md`.
- **Date recorded**: 2026-09-15
- **Additional notes**: When resuming this work, the scope of
  history/map/connector and the alert lifecycle must be settled separately
  before creating a migration. Do not assume these APIs already exist just
  because telemetry data is already stored in TimescaleDB.
- **Partial resolution (2026-09-17)**: F-A2's slice of this item is done —
  the `notifications` domain (generic table + JSONB payload,
  `GET /api/v1/notifications?after_id=`) and SOC-threshold-crossing
  detection in `telemetry/service.py` (now `telemetry/detection.py`). F-A4's slice is also done — high
  battery temperature, sudden voltage drop, and new device error codes are
  detected in the same per-message flow and raise `ANOMALY_ALERT`
  notifications; see `docs/planners/done/backend-anomaly-detection.md`. F-A5's
  slice is also done — a bounded time-range telemetry history query
  (`GET /telemetry/vehicles/{id}/history`), not full/unbounded history and
  not a map; see `docs/planners/done/backend-telemetry-query-api.md` §2.2.
  Still open from this item: the vehicle/station map, aggregated connector
  status, and any device-ACK/MQTT-command mechanism. See items 37, 40, 41
  and 42-48 below.
- **Update 2026-10-01**: more of this item now exists (`docs/planners/done/backend-happy-path-completion.md`): the vehicle "map" data (`GET /telemetry/vehicles/{id}/latest` with `is_online`, fleet member positions via `GET /telemetry/fleets/{fleet_id}/vehicles/latest`), a per-station connector status view (`GET /charging-stations/{id}/connectors`) and per-station `available_connector_count` in the directory and nearby search, and geofence alerts (F-A5). Still open: a status view aggregated across all stations or a fleet, the device ACK (item 52) and pushing alert thresholds to devices (item 59).

### 34. Batched connector-count query for the station directory list endpoint

- **Short description**: `charging_stations` list endpoint (F-C1) computes
  `connector_count` with one query per station on the page (via
  `repository.count_connectors_by_station_id`), instead of one
  grouped/batched query for the whole page.
- **Purpose/role in the system**: A grouped query (group by station, `IN`
  over the page's station IDs) would return every station's connector count
  in a single round-trip instead of N, reducing DB load and latency as the
  number of stations per page and request volume grow.
- **Reason for deferral**: A batched version was written and then explicitly
  reverted per request — this MVP's station count is small, the list
  endpoint isn't on any hot path, and batching mechanisms across the backend
  should be added only when throughput actually needs them (same principle
  already applied to telematics' mapping lookup, item 5, and its list-query
  N+1, item 29). Adding batching ahead of an actual need adds complexity and
  a second code path to maintain for no current benefit.
- **Related planner/feature**: F-C1, `charging_stations/repository.py`,
  `charging_stations/service.py::list_charging_stations`.
- **Date recorded**: 2026-09-17
- **Additional notes**: When resuming this work, reintroduce a
  `count_connectors_by_station_ids(db, station_ids) -> dict[UUID, int]`
  repository function (grouped query, `station_id IN (...)`, `GROUP BY
  station_id`, filling in `0` for stations with no active connectors) and
  have `list_charging_stations` call it once instead of looping per station.
  Benchmark first rather than assuming it's needed.

---

### 36. NF-01/NF-04/NF-06 hardening for F-A1 telemetry ingestion

- **Short description**: The non-functional requirements originally named for F-A1 —
  NF-01 (ingestion latency ≤30s p95, target ≤10s), NF-04 (scale from 300 to 1,200+
  concurrent vehicles/devices by 2029 without an architecture change), and NF-06
  (per-device mTLS/certificate identity, revocable) — are not implemented or measured.
- **Purpose/role in the system**: Latency instrumentation and a load test would validate
  F-A1 meets its stated SLO and capacity target; device-level TLS/mTLS would replace
  today's MQTT connection, which supports at most optional username/password and no
  transport-level device identity, certificate issuance, or revocation.
- **Reason for deferral**: This backend's current scope is an MVP/POC — non-functional
  concerns like performance and scale are explicitly out of scope until a production
  rollout is planned, and only a basic (or no) security posture is required for now. No
  latency metrics, load-test harness, or device PKI exists. This mirrors the same
  intentional carve-out already made explicit for OCPP in the former `tech-decisions.md` (now decision IS-05: "OCPP
  security MVP" — dev allows unauthenticated/non-TLS connections in an isolated
  environment; production must finalize its own security profile).
- **Related planner/feature**: F-A1, `telemetry` domain/ingestion, EMQX broker
  configuration; QoS/transport security should be reconsidered together (item 2).
- **Date recorded**: 2026-09-17
- **Additional notes**: Don't treat the current MVP posture as a production security or
  performance baseline. Before a production rollout: add structured latency logging
  around ingestion (publish → DB commit) for NF-01, run a load test simulating 1,200+
  concurrent publishers for NF-04, and configure EMQX for per-device TLS client
  certificates (issuance/rotation/revocation process still needs to be designed) for
  NF-06.

---

### 37. Live station occupancy/online signal for "nearest available station"

- **Short description**: A live occupied/free and online/offline signal per
  station/EVSE/connector, so F-A2's nearest-station lookup can answer
  "available" for real instead of approximating it as "not soft-deleted and
  not under maintenance."
- **Purpose/role in the system**: F-A2's stated output is "distance to the
  nearest *available* station" — today's `find_nearest_operational_station`
  filters on `deleted_at`/`maintenance_status` only, both admin-set and
  never updated by OCPP. A vehicle could be pointed at a station where every
  connector is already occupied.
- **Reason for deferral**: No OCPP `StatusNotification`/`Heartbeat` handler
  exists, and neither `ChargingEvseModel` nor `ChargingConnectorModel` has a
  status column (see `docs/decisions/deferred.md` items 27/28, the
  charging MVP's always-online assumption). Building this is charging-domain
  scope, not something to bolt onto the nearest-station query.
- **Related planner/feature**: F-A2, F-C1, `charging_stations`,
  `docs/planners/done/backend-notifications.md`.
- **Date recorded**: 2026-09-17
- **Additional notes**: When resuming, this likely means an OCPP
  `StatusNotification` handler plus a status column on `ChargingConnectorModel`
  (or `ChargingEvseModel`), then changing `find_nearest_station_by_location`'s
  filter to also require "available" in the new sense. Revisit alongside
  items 27/28 rather than in isolation.
- **Partial resolution (2026-09-17)**: The status column and
  `StatusNotification` handler this item called for now exist (F-C2,
  `ChargingConnectorModel.status`). Still open: changing
  `find_nearest_station_by_location` (F-A2) and the new
  `list_nearby_stations`/`count_nearby_stations` (F-D1) to actually filter
  on it — see item 49 for that narrower remaining piece. The
  online/offline (heartbeat) half of this item is also still open.

- **Partial resolution (2026-09-24)**: an `is_online` flag (derived from `last_seen_at`) now exists on stations, but nothing consumes it: F-A2/F-D1 still don't filter on it or on connector status, and a connector's last status is not invalidated when its charger goes offline — see items 49 and 76.
- **Update 2026-10-01**: the occupancy half is built through item 49 (an available station needs at least one connector whose last status is `Available`). The online half was deliberately not wired in (decision D3 of `docs/planners/done/backend-happy-path-completion.md`): `is_online` is returned by the nearby search but not required, because the connector status is the availability signal. What is left of this item is item 76 (stale statuses of an offline charger).

### 38. True per-trip de-duplication for F-A2 battery alerts

- **Short description**: F-A2's spec says "1 alert per threshold per trip";
  the current implementation approximates this as "1 alert per threshold
  crossing" (previous SOC above the threshold, current at-or-below it),
  since no trip concept exists in the backend.
- **Purpose/role in the system**: A trip boundary would let the same
  threshold alert again on a new trip even without an intervening full
  charge back above it (e.g. a short top-up that doesn't clear the
  threshold but starts a new trip) - the crossing-only rule can't
  distinguish that from "still the same low-battery trip."
- **Reason for deferral**: F-A9 (Empty-trip detection), which would
  introduce the only trip concept anywhere in this backend, is itself still
  📋 Planned. No trip table, trip ID, or trip-boundary detection exists to
  key a per-trip de-dup state on.
- **Related planner/feature**: F-A2, F-A9, `telemetry`,
  `docs/planners/done/backend-notifications.md`.
- **Date recorded**: 2026-09-17
- **Additional notes**: When F-A9 lands, revisit
  `detect_battery_alert_level` in `telemetry/detection.py` - it may need a
  trip ID parameter and per-(vehicle, trip, threshold) state instead of
  purely comparing consecutive readings.

### 39. Hysteresis/re-arm margin for F-A2 threshold crossing

- **Short description**: A small SOC margin (e.g. 2%) a vehicle must climb
  back above a threshold before that threshold can alert again, instead of
  the current bare crossing rule re-arming the instant SOC ticks back above
  the line.
- **Purpose/role in the system**: Guards against a noisy raw SOC sensor
  oscillating across a threshold (20.1 → 19.9 → 20.1 → 19.9 → ...) and
  producing a new alert on every oscillation.
- **Reason for deferral**: Not needed yet - telemetry's `soc` is a single
  reported value per message from the BMS, not observed to be noisy in the
  simulator or in current use. Adding a margin now would be a constant with
  nothing to tune it against. Revisit if real device data shows this
  oscillation happening.
- **Related planner/feature**: F-A2, `telemetry/detection.py::detect_battery_alert_level`.
- **Date recorded**: 2026-09-17
- **Additional notes**: If added, the margin should be a named setting
  (e.g. `BATTERY_ALERT_REARM_MARGIN_PERCENT`), not a bare literal, per this
  backend's config-over-hardcoding convention.

### 40. Recipient scoping for notifications

- **Short description**: Notifications are currently vehicle-scoped only
  (a nullable `vehicle_id` column) with no concept of which user/role should
  see them - there is no filter by driver, fleet, or operator account.
- **Purpose/role in the system**: F-A2 says the driver receives early
  alerts and the fleet manager receives alerts from the 20% threshold up -
  today's API returns every notification to any caller.
- **Reason for deferral**: No `identity` domain (auth & RBAC) exists yet in
  this repo (see `domain-boundaries.md`); recipient scoping depends on it.
  Building an ad hoc scoping mechanism now would be redone once `identity`
  lands.
- **Related planner/feature**: F-A2, `notifications`, `identity` (future
  domain), `docs/planners/done/backend-notifications.md`.
- **Date recorded**: 2026-09-17
- **Additional notes**: When `identity` lands, `notifications` likely needs
  a recipient/audience column or join table, and the list endpoint needs an
  auth-derived filter instead of returning everything.

### 41. Push and multi-channel delivery for notifications (F-F3)

- **Short description**: `notifications` today is backend-storage plus
  admin-portal polling only - no push notification, in-app real-time
  delivery, or SMS, and no per-channel/per-threshold configuration.
- **Purpose/role in the system**: F-F3 (Multi-channel notifications) names
  push/in-app/SMS as the delivery channels for F-A2/F-B5/F-J3, with SMS
  reserved as a fallback for critical alerts.
- **Reason for deferral**: There is no mobile app in this repo's scope
  (the driver-facing app doesn't exist yet), so push/in-app delivery has no
  client to deliver to; SMS needs a third-party gateway decision. The
  polling API this item builds on top of is the storage layer a future
  push mechanism would reuse.
- **Related planner/feature**: F-F3, F-A2, F-B5, F-J3, `notifications`
  (future multi-channel work).
- **Date recorded**: 2026-09-17
- **Additional notes**: When a mobile/driver app exists, revisit whether
  `notifications`' polling API stays as-is (the app polls too) or gets a
  push layer (e.g. FCM/APNs) added alongside it - both can coexist, since
  the table is the source of truth either way.

### 42. Device error-code catalog for F-A4 fault classification

- **Short description**: A vendor-confirmed mapping from telematic error
  codes (the opaque strings in `TelemetryMessage.errors`, e.g. `"E001"`) to
  a fault category, so F-A4's "cell/module fault" and "motor fault"
  triggers can be told apart instead of both collapsing into one generic
  `DEVICE_FAULT` anomaly.
- **Purpose/role in the system**: F-A4 names cell/module fault and motor
  fault as two of its four triggers. Without a catalog, `telemetry/
  detection.py::detect_new_error_codes` can only say "a new error code
  appeared," not which subsystem it belongs to - an operator reading a
  `DEVICE_FAULT` notification can't tell a battery fire precursor from a
  minor motor fault from the alert alone.
- **Reason for deferral**: `docs/design/specifications/mqtt-spec.md` defines `errors`
  as opaque strings with no code catalog anywhere in this repo's contracts.
  Inventing a code-to-category mapping without the device vendor's
  documentation would be a guess baked into the backend's business logic.
- **Related planner/feature**: F-A4, `telemetry/detection.py`,
  `docs/planners/done/backend-anomaly-detection.md`.
- **Date recorded**: 2026-09-17
- **Additional notes**: When the vendor's error code catalog is available,
  split `VehicleAnomalyType.DEVICE_FAULT` into distinct types (e.g.
  `CELL_MODULE_FAULT`, `MOTOR_FAULT`) and route each code to the right one
  in `detect_new_error_codes`, or a successor function.

### 43. Vendor-validated, configurable F-A4 anomaly thresholds

- **Short description**: `HIGH_BATTERY_TEMPERATURE_THRESHOLD_CELSIUS`
  (60.0°C) and `VOLTAGE_DROP_THRESHOLD_VOLTS` (50.0V) in `telemetry/
  types.py` are engineering defaults picked for the MVP, not values
  confirmed against the actual battery pack/BMS specification, and they are
  module-level constants rather than settings.
- **Purpose/role in the system**: Accurate thresholds directly affect a
  fire-safety-relevant Must feature - too high risks missing a real
  anomaly, too low risks alert fatigue. Configurability would let different
  vehicle/battery models use different thresholds without a code change.
- **Reason for deferral**: No vendor/BMS specification was available at
  implementation time; a wrong guess dressed up as a `Settings` field would
  look more authoritative than it is. `BATTERY_ALERT_THRESHOLDS` (F-A2) set
  the precedent of starting with plain constants before promoting values to
  configuration.
- **Related planner/feature**: F-A4, `telemetry/types.py`,
  `docs/planners/done/backend-anomaly-detection.md`.
- **Date recorded**: 2026-09-17
- **Additional notes**: When resuming, confirm real thresholds with the
  vehicle/battery vendor, then decide whether they stay as constants or move
  into `app.libs.common.config.Settings` (e.g. if different fleets/vehicle
  models need different values).

### 44. Re-alert/escalation for a persisting F-A4 anomaly

- **Short description**: F-A4 anomalies use the same "alert once on entry,
  silent while it persists" crossing rule as F-A2's SOC thresholds - a
  battery stuck at 90°C for an hour raises exactly one notification, the
  same as a battery that briefly touched 61°C and recovered.
- **Purpose/role in the system**: For a fire-safety-relevant anomaly, an
  operator who misses or is slow to act on the first alert gets no reminder
  that the condition is still active.
- **Reason for deferral**: No cooldown/re-alert state exists anywhere in
  this backend yet, and adding one means an extra query (last notification
  of this type for this vehicle) on the telemetry ingestion hot path -
  correctly scoped alongside item 39 (F-A2's hysteresis/re-arm margin,
  which has the same "not needed until real device data shows a need"
  reasoning) rather than added speculatively.
- **Related planner/feature**: F-A4, F-A2 item 39,
  `telemetry/detection.py::detect_high_battery_temperature`.
- **Date recorded**: 2026-09-17
- **Additional notes**: When resuming, consider a time-based cooldown (e.g.
  re-alert if still anomalous after N minutes) or a severity-tier escalation
  (as F-A2 uses across SOC levels) rather than re-alerting on every message.

### 45. High-motor-temperature detector for F-A4

- **Short description**: `vehicle_telemetry.motor_temperature` is ingested
  and stored but has no F-A4 anomaly detector - only battery temperature,
  voltage drop, and new device error codes are detected today.
- **Purpose/role in the system**: A sustained high motor temperature is a
  plausible failure precursor alongside the battery-side anomalies F-A4
  already covers.
- **Reason for deferral**: F-A4's spec names four triggers - high battery
  temperature, sudden voltage drop, cell/module fault, motor fault - and
  motor *temperature* is not one of them; treating it as a proxy for "motor
  fault" would be inventing a trigger the spec doesn't ask for. Recorded
  here as a candidate rather than implemented speculatively.
- **Related planner/feature**: F-A4, `telemetry/detection.py`,
  `docs/planners/done/backend-anomaly-detection.md`.
- **Date recorded**: 2026-09-17
- **Additional notes**: If motor fault detection is confirmed to belong
  here, decide the threshold and whether it maps to "motor fault" or is a
  genuinely new, fifth trigger - don't conflate it with item 42's
  error-code-based fault classification without confirming that's correct.

### 46. True trip segmentation for F-A5 "trip replay"

- **Short description**: F-A5's "trip replay" output is currently served as
  a bounded time-range telemetry history query
  (`GET /telemetry/vehicles/{id}/history`) returning raw points for the
  caller to draw a polyline from - not segmented trips with a start, an
  end, and a trip identity.
- **Purpose/role in the system**: A real trip boundary (ignition-on to
  ignition-off, or an idle-gap heuristic) would let the fleet portal list
  "trips" directly instead of the caller inferring boundaries from a flat
  point list, and would let a trip be referenced by ID (for F-A6/F-A8
  reporting, audit, or the repossession-process usage context F-A5's PRD
  entry mentions).
- **Reason for deferral**: This backend has no trip concept anywhere - item
  38 already notes that F-A9 (Empty-trip detection, still 📋 Planned) would
  be the *only* place a trip boundary/ID gets introduced. Inventing a trip
  concept here (e.g. an idle-gap threshold) ahead of F-A9 risks a second,
  conflicting definition of "trip" in the same backend.
- **Related planner/feature**: F-A5, F-A9, item 38,
  `docs/planners/done/backend-telemetry-query-api.md` §2.2.
- **Date recorded**: 2026-09-17
- **Additional notes**: When F-A9 lands its trip concept, revisit whether
  `GET .../history` should accept a `trip_id` alongside (or instead of) a
  raw time range, and whether trip boundaries should be persisted or
  computed on read.

### 48. TimescaleDB retention policy for `vehicle_telemetry`

- **Short description**: F-A5's "trip history retained ≥6 months" constraint
  currently holds only because nothing deletes rows from `vehicle_telemetry`
  - there is no TimescaleDB retention/`drop_chunks` policy, and no explicit
  decision on how long data is kept beyond "at least" 6 months.
- **Purpose/role in the system**: A retention policy bounds storage growth
  on a high-frequency hypertable (one row every 5-10s per vehicle) and turns
  an implicit "we haven't deleted anything yet" into an explicit, auditable
  guarantee.
- **Reason for deferral**: No storage-growth pressure yet at the current
  device count; adding a retention/compression policy now would be a guess
  at a retention window without real volume data to size it against.
- **Related planner/feature**: F-A5, F-A1, `docs/design/architecture.md`.
- **Date recorded**: 2026-09-17
- **Additional notes**: When resuming, add a TimescaleDB
  `add_retention_policy`/compression policy via a migration once real
  storage volume justifies it, and confirm the actual retention window (the
  PRD only says "≥6 months," not an upper bound) as a business decision.

### 50. F-J1's per-device health dashboard (SIM/power status, firmware view)

- **Short description**: F-J1's stated output is a "per-device dashboard —
  last-seen, firmware version, SIM/data status, power status." Only
  last-seen (via the F-J1/F-J3 silence monitor's alert) and firmware
  version (already a plain CRUD field on `TelematicModel`) exist; SIM/data
  status and power status have no field anywhere in this backend.
- **Purpose/role in the system**: Operations/admin need a live per-device
  view to triage a device issue (weak signal vs. dead SIM vs. no power)
  before dispatching anyone - today's alert only says "silent," not why.
- **Reason for deferral**: The MQTT contract (`mqtt-spec.md`) never defined
  SIM/carrier or power-rail fields, and no telematic hardware spec commits
  to sending them (same root gap as F-G1's unresolved Tri-Ring spec,
  "Items needing confirmation" #1). Inventing fields the device may never
  actually populate would be guessing at a hardware contract.
- **Related planner/feature**: F-J1, F-G1, `telematics`.
- **Date recorded**: 2026-09-17
- **Additional notes**: When the Tri-Ring telematics spec is confirmed,
  check whether it exposes SIM/carrier signal or power-rail status; if so,
  extend the MQTT contract and `TelematicModel`/`vehicle_telemetry`
  accordingly and surface them on a per-device endpoint. Don't build a
  "dashboard" UI here regardless - that's the admin portal's job; this
  repo's part is only the data.
- **Update 2026-10-01**: the last-seen half of the dashboard data is built (`docs/planners/done/backend-happy-path-completion.md`): every telematics device response carries `last_seen_at`, `is_online`, `is_silent` (the monitor's own rule, `telematics/monitoring/silence_rule.py`) and `last_signal_strength_dbm` (from the newest telemetry), all derived at read time. SIM/data status and power status still have no source, as described above.

### 51. F-J3's power-loss-vs-signal-loss discrimination

- **Short description**: F-J3 requires "distinguish sudden power loss from
  ordinary signal loss." The F-J1/F-J3 monitor built this round only knows
  "no telemetry for N minutes" - it cannot tell a dead battery/pulled fuse
  apart from a truck in a tunnel or a dead cell zone.
- **Purpose/role in the system**: A power-loss/tamper signal likely
  triggers a different (more urgent, possibly security/repossession-adjacent
  per F-J3's own PRD note) response than routine signal loss - conflating
  them under one alert type risks the wrong response or alert fatigue from
  treating every dead zone as tamper.
- **Reason for deferral**: No signal exists to make the distinction. A
  per-device MQTT Last Will (the standard MQTT idiom for "this specific
  client went ungracefully offline") would need each *telematic device* to
  be its own MQTT client with its own LWT - today only the *backend's own*
  consumer process has one (`MQTT_STATUS_TOPIC_TEMPLATE`,
  `app/domains/telemetry/ingestion/mqtt_consumer.py`), which says nothing
  about any individual vehicle. Alternatively, an on-device battery/power
  field in the MQTT payload (same gap as item 50) would let the backend
  tell the two apart directly.
- **Related planner/feature**: F-J3, F-J1, item 50, `telematics/monitoring/`.
- **Date recorded**: 2026-09-17
- **Additional notes**: When resuming, this likely needs one of: (a) each
  telematic device publishing its own MQTT LWT so the broker can report
  ungraceful disconnects per device (an EMQX/broker-topology decision, not
  purely a backend one), or (b) a power-status field in the MQTT payload
  (needs the same hardware-spec confirmation as item 50). Don't guess a
  proxy heuristic (e.g. "GPS didn't move" ≠ "lost power") without
  confirming it's an acceptable approximation first.

### 52. Confirmation of applied device configuration (F-J2)

- **Short description**: F-J2's constraint says "confirmation of applied
  config." Nothing in this round confirms anything - `telematics
  .telemetry_interval_seconds`/`config_pushed_at` mean only "the last
  interval we successfully handed to the broker," never "the interval the
  device is actually running."
- **Purpose/role in the system**: Without this, an operator pushing a
  config change has no way to know whether it took effect, or whether the
  device is still silently running its old interval (or firmware default).
- **Reason for deferral**: `mqtt-spec.md` 2.3 defines no ack/reported-config
  topic. A successful publish today proves only that the broker accepted
  the message (QoS 1 PUBACK), not that any device received or applied it.
- **Related planner/feature**: F-J2, `telematics/commands/`,
  `docs/planners/done/backend-telematics-config-push.md`.
- **Date recorded**: 2026-09-17
- **Additional notes**: Needs a `.../command/ack` (or reported-config)
  topic in the contract, a consumer for it, a desired/reported column pair
  on `telematics` (today there's only one column, meaning "desired"), and
  a reconciliation sweep. This is the keystone gap - items 53 and 56 below
  are not meaningfully buildable without it first.

### 53. Configuration rollback (F-J2)

- **Short description**: F-J2's constraint says "rollback supported." Not
  built - there is no previous-value history on `telematics`, only the
  current desired interval.
- **Purpose/role in the system**: Lets ops revert a bad config push (e.g.
  an interval that overwhelms ingestion) without knowing the prior value
  by hand.
- **Reason for deferral**: Rolling back into silence is indistinguishable
  from doing nothing without item 52's confirmation signal to know the
  rollback actually landed.
- **Related planner/feature**: F-J2, item 52.
- **Date recorded**: 2026-09-17
- **Additional notes**: Needs per-device previous-value history (a simple
  "previous interval" column, or a small history table) plus item 52.

### 55. Reliable command delivery via an outbox table and dispatcher worker (F-J2)

- **Short description**: The config push is fire-and-forget: a short-lived
  MQTT client publishes once inside the HTTP request, with no retry, no
  queue, and no dispatcher process.
- **Purpose/role in the system**: A transient broker outage today just
  fails the HTTP request (502); nothing retries the push later.
- **Reason for deferral**: There is no device consuming commands and no
  ack (item 52), so a retry has nothing to converge toward yet - building
  an outbox now would reliably deliver a message nobody reads.
- **Related planner/feature**: F-J2, item 52.
- **Date recorded**: 2026-09-17
- **Additional notes**: The shape when needed: a `telematic_commands` row
  written in the same transaction as the HTTP request, a separate
  entrypoint process draining it with retry/backoff, at-least-once
  delivery. This also closes the current publish-then-commit crash window
  (a process crash between a successful MQTT publish and the DB commit
  leaves the DB under-claiming what was actually sent - accepted as the
  safer direction for now, since the next push reconverges it).

### 56. Command audit history (F-J2)

- **Short description**: No row-per-push audit trail exists - only the
  device's current desired interval and last-push timestamp.
- **Purpose/role in the system**: "Who pushed what config, when" for
  operational troubleshooting and accountability.
- **Reason for deferral**: Without item 52's confirmation signal, a
  history row would record only "bytes were sent," not an outcome - low
  value on its own. There's also no `identity`/auth domain yet to fill in
  "who."
- **Related planner/feature**: F-J2, item 52.
- **Date recorded**: 2026-09-17
- **Additional notes**: Revisit once both item 52 (outcome) and an
  `identity` domain (actor) exist.

### 57. Per-device MQTT identity and ACL enforcement for the command topic (F-J2, NF-06)

- **Short description**: `mqtt-spec.md` section 7's ACL rules (a device may
  only subscribe to its own command topic; only the backend may publish to
  it) are written but not enforced anywhere - `infra/docker-compose.yml`
  runs EMQX with stock config, no ACL file, anonymous connections allowed.
- **Purpose/role in the system**: Without enforcement, any MQTT client can
  subscribe to any device's command topic (eavesdropping) or publish a
  forged command to it (spoofing) - a real risk once F-J2 pushes something
  more consequential than a publish interval.
- **Reason for deferral**: Same broker-hardening gap as item 36 (NF-06
  mTLS for F-A1 *ingestion*) - this is the outbound-command half of that
  same unfinished work, not a new class of problem.
- **Related planner/feature**: F-J2, item 36.
- **Date recorded**: 2026-09-17
- **Additional notes**: Address both items together when EMQX gets real
  auth/ACL configuration - don't solve the command topic's exposure in
  isolation from the telemetry topic's.

### 58. Retained backend-to-device commands (F-J2)

- **Short description**: `MQTT_COMMAND_RETAIN` defaults to `False`.
  Setting it `True` would let a reconnecting device pick up its latest
  config without a re-push.
- **Purpose/role in the system**: Convenience for a device that reconnects
  after being offline during a config push.
- **Reason for deferral**: A retained command with no ack (item 52) and no
  expiry would be redelivered to every future connection forever, with no
  way to detect a device that already acted on a stale one.
- **Related planner/feature**: F-J2, item 52.
- **Date recorded**: 2026-09-17
- **Additional notes**: Revisit once item 52 exists, so a device can
  report it already applied a retained command.

### 59. Additional device config keys - local alert thresholds (F-J2)

- **Short description**: F-J2's stated output names "send frequency,
  local alert thresholds"; only send frequency (`set_telemetry_interval`)
  is implemented.
- **Purpose/role in the system**: Let ops tune a device's own on-board
  alert thresholds (e.g. a local low-battery buzzer) without a firmware
  update.
- **Reason for deferral**: No device-side threshold semantics are defined
  anywhere - same hardware-contract gap as items 50/51 (the unresolved
  Tri-Ring telematics spec). Don't invent threshold fields the firmware
  may never read.
- **Related planner/feature**: F-J2, F-G1, items 50, 51.
- **Date recorded**: 2026-09-17
- **Additional notes**: Once the Tri-Ring spec (or its OBD/CAN fallback)
  is confirmed, check whether it exposes any on-device alerting the
  backend could configure.

### 60. Configurable electricity tariff for F-A6

- **Short description**: F-A6's own constraint says "cost formula must be
  configurable (electricity price varies)." This round hardcodes a flat
  `ENERGY_COST_PER_KWH_VND = 3000.0` constant in `telemetry/types.py`.
- **Purpose/role in the system**: Real electricity pricing varies by
  tenant, time-of-day, and region; a flat constant can't reflect that.
- **Reason for deferral**: Per the user's explicit instruction for this
  round, the constant was hardcoded with an explanatory comment rather
  than building tariff configuration - the same treatment already applied
  to F-A6/F-C6's other engineering defaults.
- **Related planner/feature**: F-A6, `docs/planners/done/backend-operating-energy-reports.md`.
- **Date recorded**: 2026-09-17
- **Additional notes**: When resumed, likely a per-tenant/time-of-use
  tariff table rather than a single setting - a `Settings`-level override
  (e.g. `TELEMETRY_ENERGY_COST_PER_KWH_VND`) would only half-satisfy the
  constraint (one global scalar, still no time-of-use).
- **Update 2026-10-01**: the constant is gone: the price is now the setting `TELEMETRY_ENERGY_COST_PER_KWH_VND` (default 3000.0, read at call time by the per-vehicle and fleet reports) - the "one global scalar" half this item anticipated. A time-of-use or per-tenant tariff is still open (and overlaps F-C8's tariff design).

### 61. Vendor-confirmed battery capacity and a vehicle-model catalog

- **Short description**: `vehicles.battery_capacity_kwh` is a manually
  entered, per-vehicle nullable field; when absent, F-A6/F-C6 substitute
  `DEFAULT_BATTERY_CAPACITY_KWH = 75.0`, an engineering guess. There is no
  vehicle-model catalog mapping `make`/`model`/`year` to a real spec.
- **Purpose/role in the system**: Every kWh, cost, and efficiency number
  F-A6/F-C6 report for a vehicle with no recorded capacity is an estimate
  built on a guessed pack size (flagged via `is_default_battery_capacity`,
  but still an estimate).
- **Reason for deferral**: No vendor/spec data source exists to populate
  this from; same "engineering default, not vendor-confirmed" family as
  `SOH_ALERT_THRESHOLD_PERCENT` and the F-A4 thresholds.
- **Related planner/feature**: F-A6, F-C6, F-A3, F-A4.
- **Date recorded**: 2026-09-17
- **Additional notes**: A `make`/`model`/`year` → capacity lookup table
  would let new vehicles default sensibly instead of falling back to one
  fleet-wide constant, once real vendor specs are available.

### 62. Station-metered per-customer energy and NF-10 3-way reconciliation (the "real" F-C6)

- **Short description**: F-C6's constraint requires a 3-way reconciliation
  (connector–vehicle–payment, <1% deviation, NF-10). This round's
  telemetry-based method (SOC rises) cannot meet that - it measures energy
  into the pack, not kWh billed at a station meter, excludes
  charger/conversion losses (typically 5-15%), and can't attribute energy
  to a specific station or session.
- **Purpose/role in the system**: Billing/reconciliation ultimately needs
  a station-metered figure tied to an actual charging session, not a
  telemetry-derived proxy.
- **Reason for deferral**: `charging_sessions` has no vehicle, customer,
  or driver identity column at all, and the OCPP `TransactionEvent`
  handler never reads the `idToken` field that would carry one (it's
  absorbed by a catch-all kwarg) - this is the same blocker recorded when
  F-A6 was first scoped and F-A6/F-C6 deliberately avoided reopening it
  this round.
- **Related planner/feature**: F-C6, F-A6, F-B3 (session-data portion).
- **Date recorded**: 2026-09-17
- **Additional notes**: Needs, at minimum: a vehicle_id (or idToken)
  column on `charging_sessions`, an OCPP `Authorize`/`idToken` handler
  (currently unhandled entirely), and a decision on what identity a
  station-issued RFID/token actually maps to (a vehicle? a driver? a
  fleet account?) before the column can be populated meaningfully.

- **Update 2026-09-24**: `charging_sessions` now has an `id_tag` column (OCPP 1.6J), the first identity-bearing field on a session, but it is an unvalidated string: there is still no vehicle/customer/driver identity and no `Authorize` validation, and the 2.0.1 path still ignores `idToken`. The blocker described here is unchanged.

### 64. SOC dead-band or current-integration energy method for F-A6/F-C6

- **Short description**: Summing raw SOC deltas over-counts energy at high
  sampling frequency - a device dithering between two adjacent integer SOC
  values (e.g. `61 → 60 → 61 → 60`) contributes to *both* the discharge
  and the charge sum on every dither, so higher-frequency telemetry makes
  the over-count worse, not better.
- **Purpose/role in the system**: Accuracy of F-A6's consumed-energy and
  F-C6's charged-energy figures.
- **Reason for deferral**: Fixing this needs either a dead-band (ignore
  deltas below a noise threshold) or integrating
  `battery_current × battery_voltage × dt`, and neither has been validated
  against real device noise characteristics yet - guessing a dead-band
  width without real data would trade one unverified assumption for
  another.
- **Related planner/feature**: F-A6, F-C6.
- **Date recorded**: 2026-09-17
- **Additional notes**: `battery_current`/`battery_voltage` are already
  ingested per message (nullable) - the current-integration method could
  reuse them once their reliability across real devices is validated.

### 65. TimescaleDB continuous aggregate for the F-A6/F-C6 report window

- **Short description**: `get_vehicle_window_summary` scans raw
  `vehicle_telemetry` rows for every report call (a `lag()` window fold
  over the requested range, up to 31 days).
- **Purpose/role in the system**: Query latency/cost at higher telemetry
  volume or wider report windows.
- **Reason for deferral**: Per the runtime conventions' "never
  preemptively optimize" rule - no benchmark has shown this scan is too
  slow. The existing `ix_vehicle_telemetry_vehicle_time` index already
  lets PostgreSQL do an index scan into a streaming window aggregate with
  no sort node.
- **Related planner/feature**: F-A6, F-C6.
- **Date recorded**: 2026-09-17
- **Additional notes**: A continuous aggregate isn't a drop-in win here
  regardless - a delta fold isn't bucket-decomposable (the delta spanning
  two buckets is invisible to per-bucket sums), so it would need to
  materialize per-bucket first/last SOC and odometer and reconcile
  boundaries at query time. Only pursue this after a real benchmark shows
  the raw scan is the bottleneck.

### 66. Duplicate `Started` surfaces a raw `IntegrityError`, not a domain exception (F-B2)

- **Short description**: F-B2's fix #2 guards `Updated`/`Ended` against an
  already-`COMPLETED` session, but a duplicate `Started` for the same
  `(station_id, ocpp_transaction_id)` pair never reaches that guard - it
  goes straight to `repository.create_session`, hits
  `uq_charging_sessions_station_transaction`, and raises a raw
  `IntegrityError` that propagates uncaught out of
  `ingest_transaction_event`. This is a live violation of
  `backend-runtime-conventions.md`'s own stated rule: *"A DB unique
  constraint is the last line of defense; an `IntegrityError` must be
  converted into the appropriate domain error"* - every other domain in
  this backend (`vehicles`, `telematics`) already does this conversion;
  `charging_sessions` doesn't, for this one path.
- **Purpose/role in the system**: An uncaught `IntegrityError` still
  produces the same *observable* outcome as F-B2's other guards (OCPP's
  generic exception handling turns it into a `CALLERROR`), but it's an
  accident of that generic handling, not a deliberate, attributable
  rejection - and it would surface as a raw driver exception (not
  `ChargingSessionStateError`) to any future non-OCPP caller of this
  service (e.g. a REST ingest path).
- **Reason for deferral**: Identified during F-B2's design as an optional
  fifth fix and deliberately left out of that round's approved scope
  (correctness fixes #1-#4 only) to keep the diff focused; it is a small,
  independent, low-risk fix (catch `IntegrityError` around
  `repository.create_session`, re-raise `ChargingSessionStateError`) that
  doesn't touch the reliability path in item 27 above.
- **Related planner/feature**: F-B2,
  `docs/planners/done/backend-charging-ingest-fixes.md`.
- **Date recorded**: 2026-09-18
- **Additional notes**: Reuses the `ChargingSessionStateError` F-B2 already
  introduced - no new exception type needed. A test analogous to the
  existing `test_ingest_transaction_event_rejects_repeated_ended` (stub
  `repository.create_session` to raise `IntegrityError`, assert the
  service converts it) should accompany the fix.

### 67. F-A9's driver-declaration and automatic-inference halves

- **Short description**: F-A9 (Empty-trip/deadhead detection) asks for a
  driver to declare a trip's load status (loaded/empty), the system to
  infer it automatically from per-km battery consumption vs. a
  vehicle's reference consumption curve, and a flag raised when the two
  disagree. None of this was built when the `drivers` domain was added
  (F-E4) - the domain itself now exists, but F-A9 stays unimplemented.
- **Purpose/role in the system**: Feeds the Phase 2 backhaul-optimization
  feature (per F-A9's own note, out of scope here) with empty-km data,
  and would be the vehicle for "Items needing confirmation" #1 (mismatch
  handling) once built.
- **Reason for deferral**: Confirmed by research (2026-09-18): this
  backend has **no trip concept anywhere** (no `trip_id`, no trip table,
  no start/end-boundary detection - `deferred.md` items 38 and 46 already
  document this and explicitly reserve "inventing a trip concept" for
  whenever F-A9 is built, warning against a second, conflicting
  definition), **no driver identity/auth on any request** (every endpoint
  in this backend, including the new `drivers` domain, is
  unauthenticated), and **no per-vehicle-model reference consumption
  curve** exists to compare against. Three options were weighed when
  `drivers` was built: (a) declaration-only, tagging a vehicle's CURRENT
  load status (mirrors the existing `vehicles.status` enum-on-a-row
  pattern, needs no trip concept); (b) declaration + a crude inference
  proxy (reuse F-A6's SOC-based energy calculation over an arbitrary time
  window instead of a true trip, compared against one hardcoded
  fleet-wide reference-consumption constant); (c) build a real trip model
  first. Per explicit instruction, **none were built this round** -
  F-A9 was suspended entirely rather than picking a partial option.
- **Related planner/feature**: F-A9, F-E4, `drivers`, `telemetry`, items
  38, 46, `docs/planners/done/backend-crud-drivers.md`.
- **Date recorded**: 2026-09-18
- **Additional notes**: When resumed, option (a) (declaration-only,
  vehicle-tagged) is the cheapest and was the recommended starting point
  during design - it needs no trip concept and matches the PRD's "≤2
  taps" simplicity, though it only satisfies the driver-declaration half.
  The automatic-inference half genuinely cannot honor the PRD's "per-km
  over this trip" framing without a trip boundary; option (b)'s
  arbitrary-time-window proxy is a real accuracy compromise, not a true
  substitute, and should be presented to the user as such rather than
  silently assumed equivalent.

---

### 68. F-I4's repair/rescue partner directory and dispatch routing

- **Short description**: F-I4 (repair & rescue network dispatch) was
  considered alongside F-I1/F-I2 when the `support` domain was built, but
  not implemented - no partner directory table, no nearest-partner
  routing, and no dispatch/acceptance-SLA tracking exist.
- **Purpose/role in the system**: An SOS case (F-I2) is supposed to be
  "forwarded to F-I4"; without it, an SOS case is recorded but never
  auto-routed to an actual repair/rescue partner - CSKH has to do that
  entirely outside the system today.
- **Reason for deferral**: Scoped out by explicit user decision to build
  only F-I1+F-I2 this round. F-I4 needs real new surface area (a partner
  directory with region/capability/coverage/hours, a nearest-partner
  PostGIS lookup cloning `charging_stations`' GIST-indexed pattern, and a
  1:N dispatch-with-its-own-acceptance-SLA table) that wasn't built
  speculatively ahead of a concrete task.
- **Related planner/feature**: F-I4, F-I2, `support`,
  `docs/planners/done/backend-support-cases.md`.
- **Date recorded**: 2026-09-18
- **Additional notes**: When resumed, `support_cases` (F-I1/F-I2's table)
  already carries everything a dispatch needs to reference (`case_id`,
  `vehicle_id`, `location`). Add `support_partners` (with a GIST-indexed
  `location`, unlike `support_cases.location` which deliberately has none)
  and `support_dispatches` (case → partner, its own
  `acceptance_due_at`/`accepted_at`/`eta_at`, a partial unique index so
  only one dispatch per case is "live" at a time). "Available" can only
  honestly mean an operator-maintained flag - no real-time partner
  integration exists to feed a live signal.

---

### 69. F-I3's maintenance-scheduling booking

- **Short description**: F-I3 (maintenance scheduling) was considered
  alongside F-I1/F-I2/F-I4 for the `support` domain but not implemented.
- **Purpose/role in the system**: Lets a driver book a workshop slot from
  a maintenance reminder and stores the resulting history per vehicle.
- **Reason for deferral**: Lowest priority in the set (Could · P1.5) and
  genuinely a different entity from a support case/ticket (a bookable-slot
  calendar, not a case lifecycle) - no slot/calendar inventory concept
  exists anywhere in this backend, and its own upstream trigger (F-F4
  maintenance reminders) has no owning domain at all yet either.
- **Related planner/feature**: F-I3, F-F4, `support`.
- **Date recorded**: 2026-09-18
- **Additional notes**: Needs F-F4 (or at least a decision on which domain
  owns maintenance reminders) resolved first, plus a workshop/slot
  inventory data source that doesn't exist yet.

---

### 70. SLA-breach monitor and escalation for support cases

- **Short description**: F-I1/F-I2's SLA is currently a stored deadline
  (`response_due_at`) plus a computed `is_sla_breached` flag, read only
  when a case is fetched. Nothing proactively watches for a breach.
- **Purpose/role in the system**: A ticket or SOS case that breaches its
  response SLA with nobody currently viewing it goes unnoticed until
  someone happens to `GET` it.
- **Reason for deferral**: No cooldown/re-alert/escalation infrastructure
  exists anywhere in this backend yet (same gap noted for F-A4 in item
  44), and there is no CSKH identity/`identity` domain to route an
  escalation to - building a monitor with nothing to notify would be
  premature. `support_cases.response_due_at` already has a partial index
  (`WHERE first_responded_at IS NULL`) specifically so this query is cheap
  once a monitor is justified.
- **Related planner/feature**: F-I1, F-I2, `support`, item 44 (same
  "alert once, no re-alert" pattern class),
  `telematics/monitoring/` (the closest existing periodic-worker
  precedent, F-J1/F-J3).
- **Date recorded**: 2026-09-18
- **Additional notes**: When `identity` lands and there's someone to
  notify, clone `telematics/monitoring/device_health_monitor.py`'s
  periodic-sweep shape rather than inventing a new worker pattern.

---

### 71. Support case ownership without authentication

- **Short description**: `support_cases.driver_id` is entirely
  client-supplied and unverified - there is no session/auth to confirm the
  caller is actually that driver.
- **Purpose/role in the system**: F-I1 implies a driver only creates/sees
  their own tickets; today any caller can attribute a case to any
  `driver_id` and list/read any case.
- **Reason for deferral**: No `identity` domain (auth & RBAC) exists yet
  in this backend - matches every other domain's current unauthenticated
  state, not a gap specific to `support`.
- **Related planner/feature**: F-I1, F-F1 (`identity`), `support`.
- **Date recorded**: 2026-09-18
- **Additional notes**: When `identity` lands, `list_support_cases`/
  `get_support_case` need an auth-derived filter instead of returning any
  case to any caller - the same shape as item 40's notifications
  recipient-scoping gap.

---

### 72. F-E2's fleet KPI dashboard

- **Short description**: F-E2 (fleet KPI dashboard: km/kWh/cost-per-km/
  SOH/utilization/alerts, aggregated and per-vehicle) was scoped alongside
  F-E1 when the `fleet` domain was built, but not implemented.
- **Purpose/role in the system**: A fleet manager comparing vehicles or
  wanting an aggregate view needs more than F-E1's plain vehicle list.
- **Reason for deferral**: Needs two changes in domains `fleet` doesn't
  own, deferred by explicit user decision to keep this round to fleet
  CRUD + F-E1 only: (1) `telemetry.get_vehicle_operating_report` currently
  returns `VehicleOperatingReportResponse`, an HTTP response schema -
  coding-conventions §5.1 forbids passing one across a domain boundary, so
  `fleet` cannot call it as-is; (2) `notifications` has no
  count-by-vehicle function for an "alerts" column. Per item 63's mandate,
  the rollup must call `telemetry`'s per-vehicle function rather than
  duplicating its SOC-fold query - it does not exist as a callable DTO
  today.
- **Related planner/feature**: F-E2, `fleet`, `telemetry`, `notifications`,
  item 63, `docs/planners/done/backend-crud-fleet.md`.
- **Date recorded**: 2026-09-18
- **Additional notes**: When resumed: (1) extract a frozen-dataclass DTO
  (e.g. `VehicleOperatingSummary` in `telemetry/types.py`) out of
  `get_vehicle_operating_report`, with the existing endpoint building its
  HTTP response from that DTO - zero behavior change to the existing
  F-A6 endpoint; (2) add `notifications.count_notifications_by_vehicle`.
  Fleet-level ratios must be recomputed from summed numerators/
  denominators, never averaged per-vehicle averages, matching F-A6's own
  precedent. "Utilization rate" has no honest backing data yet (no trip/
  ignition/duty concept, per item 46) - ship `distance_per_day_km`
  instead of inventing a field with that name. F-E3 and F-A8 remain
  separately blocked (items above) even once this item is resolved.
- **Update 2026-10-01**: blocker (1) is resolved: `telemetry.service.resolve_vehicle_operating_summary` returns a frozen `VehicleOperatingSummary` DTO, and the fleet rollup exists as `GET /telemetry/fleets/{fleet_id}/operating-report[?format=csv]` (item 63) - F-E2's km/kWh/cost-per-km columns, aggregated and per vehicle, with export. Blocker (2) is only partly resolved: `notifications` has an unread count per vehicle (`GET /notifications/unread-count?vehicle_id=`), returned as an HTTP schema, not a count over a time window as a DTO. Still missing: SOH and alert columns in the fleet view, a time-bucketed fleet view, and "utilization rate". Ownership changed too: `telemetry → fleet` now exists (decision D7 of `docs/planners/done/backend-happy-path-completion.md`), so a dashboard inside `fleet` calling `telemetry` would form a cycle - extend the telemetry-owned fleet views instead, or decide a new owner first.

### 73. OCPP transport security for real chargers

- **Short description**: Encrypt and authenticate the charger's WebSocket
  connection: `wss://` (TLS certificates on the gateway or a terminating
  proxy), per-charger credentials (Basic Auth or client certificate), or a
  VPN/private APN for the charger SIMs as the fallback.
- **Purpose/role in the system**: Charging data and control commands cross
  public 4G; today the URL identity is the only check, so anyone who knows a
  provisioned identity can impersonate that charger.
- **Reason for deferral**: The vendor has not confirmed whether the charger
  supports `wss://` or which authentication scheme (open question 4 in the decision log);
  building the wrong scheme would be wasted work. Dev mode intentionally
  allows no TLS/no auth (decision IS-05, decision D12 of the planner).
- **Related planner/feature**: `docs/planners/backend-ocpp16-charger-integration.md`, F-G2, NF-05.
- **Date recorded**: 2026-09-24
- **Additional notes**: Resume once the vendor answers. Changing the HMI
  default password (`77777777`) is an on-site procedure, not part of this
  item. Also decides whether a reverse proxy/TLS terminator becomes part of
  the infra (currently none, by decision).

### 74. CSMS remote commands over OCPP and the command channel

- **Short description**: Let the backend send commands to a connected charger:
  `RemoteStartTransaction`/`RemoteStopTransaction`, on-demand
  `GetConfiguration`/`ChangeConfiguration`, `TriggerMessage`, `Reset`,
  `UnlockConnector`, `ChangeAvailability`, and, if the charger supports the
  profile, Smart Charging, firmware/diagnostics and reservation.
- **Purpose/role in the system**: Scan-to-charge (F-H1), load balancing
  (F-C7), remote reservation (F-C4), tuning the `MeterValues` interval and
  recovering stuck guns all need the CSMS to initiate calls.
- **Reason for deferral**: The OCPP gateway is a separate OS process from the
  API, so an HTTP request needs a cross-process channel to reach the open
  socket (a design decision of its own). Which optional OCPP profiles the
  charger supports is unconfirmed. The 1.6J planner builds only an automatic
  post-boot `GetConfiguration` (decision D10).
- **Related planner/feature**: `docs/planners/backend-ocpp16-charger-integration.md`, item 26, F-G2, F-H1, F-C4, F-C7.
- **Date recorded**: 2026-09-24
- **Additional notes**: `python-ocpp`'s receive loop is sequential; a call
  must never be awaited inside a handler (schedule a task). Until this
  exists, use an external OCPP test tool (e.g. SteVe) for probes that need
  commands. Several of the spec's acceptance items (8, 9, 12-16) cannot be
  verified through this backend without it.

### 75. Charger fault alerting and error-code catalog

- **Short description**: Turn `errorCode`/`vendorErrorCode` from
  `StatusNotification` into actions: notifications, automatic support tickets,
  blocking a gun (`GroundFailure`, `HighTemperature`), and suspending billing
  and flagging the session as suspect on meter faults (`PowerMeterFailure`
  and the vendor meter codes). Includes the catalog mapping the vendor's 80
  internal codes to descriptions.
- **Purpose/role in the system**: Operations must learn about charger faults
  without reading the database; billing must not trust a session measured
  by a failing meter.
- **Reason for deferral**: The planner only stores the error fields
  (nothing is discarded). The vendor's mapping table is incomplete
  (duplicate/blank entries) and has been requested (open question 4 in the decision log);
  which faults raise which alert is an operations policy decision.
  `notification_type` has no charging types yet.
- **Related planner/feature**: `docs/planners/backend-ocpp16-charger-integration.md`, F-C2, F-J1, notifications, `support`.
- **Date recorded**: 2026-09-24
- **Additional notes**: Stop reasons #66-#69 in the manual are normal or
  BMS-initiated completions, not faults - the catalog must classify them.

### 76. Stale connector status and online-aware availability

- **Short description**: Handle a charger that goes offline: flag or
  invalidate its last-reported connector statuses, and make F-A2/F-D1
  "available" depend on `is_online` and connector status.
- **Purpose/role in the system**: An offline charger keeps its last status
  forever (the vendor's own platform shows the same trap), so drivers could
  be routed to an unreachable or occupied charger.
- **Reason for deferral**: The 1.6J planner exposes a derived `is_online`
  and stores the full status but does not change any availability query
  (extends items 37 and 49). What "available" means (any available
  connector, or a minimum count) is a business decision.
- **Related planner/feature**: `docs/planners/backend-ocpp16-charger-integration.md`, items 37 and 49, F-A2, F-D1, F-C2.
- **Date recorded**: 2026-09-24
- **Additional notes**: `ChargingConnectorStatus` now carries the 1.6J
  values in addition to 2.0.1's `Occupied` (decision D4). The busy rule when
  this is built: a gun is free only when `Available`; `Preparing`,
  `Charging`, `SuspendedEV`, `SuspendedEVSE`, `Finishing` and `Occupied` are
  busy, and `Suspended*` are normal, not faults.
- **Update 2026-10-01**: half of this item is built through item 49: F-A2/F-D1 "available" now requires at least one connector whose last status is `Available`. Requiring `is_online` was decided against for now (decision D3 of `docs/planners/done/backend-happy-path-completion.md`), and last statuses are still not invalidated when a charger goes offline (`GET /charging-stations/{id}/connectors` returns them as last reported). Both 1.6J and 2.0.1 stations now report a meaningful `is_online` (the raw log stamps `last_seen_at` for every inbound frame of either protocol). The open part is unchanged: stale-status handling, and whether "available" should also need the charger online.

### 77. Non-transaction (station-level, clock-aligned) metering

- **Short description**: Handle `MeterValues` without a `transactionId`,
  such as clock-aligned samples (target 900 s) used for time-of-use tariffs.
- **Purpose/role in the system**: Energy per time-of-day slot and
  station-level readings that don't belong to a charging session.
- **Reason for deferral**: The tariff design (F-C8) does not exist; sessions
  are the only metering unit today. Such messages are kept only in the raw
  OCPP log for now and counted in a debug log.
- **Related planner/feature**: `docs/planners/backend-ocpp16-charger-integration.md`, F-C8, F-C5, NF-19.
- **Date recorded**: 2026-09-24
- **Additional notes**: Requires setting `ClockAlignedDataInterval` and
  `MeterValuesAlignedData` on the charger, i.e. item 74.

### 78. OCPP 2.0.1 parity for the 1.6J work

- **Short description**: Give the 2.0.1 path what the 1.6J path gets:
  `BootNotification` handling and device info, stop reason, `idToken`,
  non-energy measurements, connector error details.
- **Purpose/role in the system**: Consistent data regardless of protocol.
- **Reason for deferral**: No 2.0.1 hardware exists; only the simulator uses
  that path (decision D13). The 2.0.1 gateway changes only where shared code
  forces it (raw log, negotiation, last-seen, unified energy storage).
- **Related planner/feature**: `docs/planners/backend-ocpp16-charger-integration.md`, F-G2, F-B2, item 62.
- **Date recorded**: 2026-09-24
- **Additional notes**: The unified `charging_session_measurements` table
  already accepts non-energy measurands; only the 2.0.1 extraction is missing.
- **Update 2026-10-01**: `BootNotification` (vendor, model, serial, firmware and `last_boot_at` stored through `ocpp_state_service.record_charger_boot`; heartbeat interval `CHARGING_OCPP_HEARTBEAT_INTERVAL_SECONDS` returned) and `Heartbeat` are now handled by the 2.0.1 adapter too - decision D13 of the 1.6J planner was reopened for this by `docs/planners/done/backend-happy-path-completion.md`, and the 2.0.1 simulator sends a `BootNotification` first. Still open: stop reason, `idToken`, non-energy measurands (so a 2.0.1 session's SoC/power summary fields stay null) and a post-boot configuration capture.

### 79. Raw OCPP message log: read API and retention

- **Short description**: A read-only API (or admin query tool) for
  `charging_ocpp_messages`, and a TimescaleDB retention policy for it.
- **Purpose/role in the system**: Dispute investigation and vendor-deviation
  analysis without direct database access; bounded storage growth.
- **Reason for deferral**: Nothing consumes it beyond SQL yet. The frames
  contain RFID `idTag`s (later VINs), so any API needs access control, which
  the backend does not have (no `identity` domain yet). Retention needs a
  business decision (billing evidence may need years, cf. NF-19/NF-11).
- **Related planner/feature**: `docs/planners/backend-ocpp16-charger-integration.md`, item 48 (similar retention question),
  NF-11.
- **Date recorded**: 2026-09-24
- **Additional notes**: Do not log raw frames to application logs.

### 80. Per-gun power and connector standard in the station directory

- **Short description**: Model power rating and connector standard per
  connector/EVSE instead of one value per station, including power sharing
  between guns.
- **Purpose/role in the system**: A 240 kW dual-gun charger delivers about
  120 kW per gun when both are in use; the vendor's other units show `GBT`
  connectors, so a mixed-standard station is possible.
- **Reason for deferral**: `power_rating_kw` and `connector_standard` are
  deliberately station-level aggregates (F-C1, item 28); F-D1's filters read
  them. Fine for the MVP; only visible with real data.
- **Related planner/feature**: `docs/planners/backend-ocpp16-charger-integration.md`, F-C1, F-D1, item 28.
- **Date recorded**: 2026-09-24
- **Additional notes**: Needs the real power-allocation behaviour verified
  first (`Power.Offered` per gun, Step 7b).

### 81. Unverified assumptions about the charger's OCPP message set and start flow

- **Short description**: Three things about what the Willdigits charger sends that the
  specification documents do not settle, to be confirmed from the real charger's raw log
  (planner Step 10) and only then acted on:
  1. **The documents give each message's purpose, not its fields.** The field lists the
     backend relies on (for example `StartTransaction` carrying `connectorId`, `idTag`,
     `meterStart`, `timestamp`) come from the OCPP 1.6 JSON schema shipped with the `ocpp`
     library, not from the vendor manual or the G3 handover document.
  2. **The only step-by-step session sequence in the documents is the app/QR flow, not the
     card flow.** The handover document's reference session (§5.1, "Chuỗi message của một phiên
     hoàn chỉnh"; summary §4.4) starts with `RemoteStartTransaction` after the driver scans a QR
     code. What a **card tap** produces (`Authorize` → `StartTransaction`, or no `Authorize` at
     all because of a local whitelist / `LocalAuthorizeOffline` / Autocharge) is described in
     neither document in OCPP terms; the backend's card-flow behaviour follows the OCPP 1.6
     standard, not the vendor.
  3. **The message list is what the spec promises, not what the charger sends.** The documents
     name seven charger-initiated messages (`BootNotification`, `Heartbeat`,
     `StatusNotification`, `Authorize`, `StartTransaction`, `StopTransaction`, `MeterValues`)
     plus `DataTransfer` and `FirmwareStatusNotification`. The gateway handles the seven;
     `DataTransfer`, `FirmwareStatusNotification` and the standard-but-unmentioned
     `DiagnosticsStatusNotification` are answered `CALLERROR NotImplemented` and kept in the raw
     message log.
- **Purpose/role in the system**: Avoids building on a guess. A wrong assumption about the
  start flow (point 2) or an unhandled message the charger insists on (point 3) would show up
  as a session that never starts or a charger that misbehaves, and point 1 decides whether the
  backend reads the fields it expects.
- **Reason for deferral**: It cannot be answered without the hardware, and the planner's rule is
  to replace assumptions with real logs before changing the design (decision D14, Step 10).
  Adding speculative handlers now would be code for messages nobody has seen.
- **Related planner/feature**: `docs/planners/backend-ocpp16-charger-integration.md` (Step 10), F-G2, F-B2, F-H1, open question 4 in the decision log,
  `docs/design/specifications/charging-station-specification-summary.md` §4.1 and §4.4.
- **Date recorded**: 2026-09-25
- **Additional notes**: When the real charger is connected, use the Step 10 evidence queries 1
  (messages by action) and 2 (errors the gateway answered). Then: (a) confirm the field names
  and value forms in the raw frames; (b) note whether a card tap sends `Authorize` and record
  the observed card-flow sequence in the spec summary; (c) add a minimal acknowledgement handler
  only for a message actually seen (for example `FirmwareStatusNotification` or
  `DiagnosticsStatusNotification` need only an empty reply; `DataTransfer` needs a documented
  `status`). The QR/app flow itself needs `RemoteStartTransaction`, which is item 74.

### 86. Move geofences from the fleet to the customer account

- **Short description**: `geofences` belongs to a fleet today (`geofences.fleet_id`, applied to
  the fleet's current member vehicles). Once customer accounts exist, a geofence should belong to
  the customer account that defined it (`geofences.account_id`, already designed as a planned
  column in `domain-model.dbml`) and be applicable to any of that account's vehicles or fleets.
- **Purpose/role in the system**: The domain model's tenant is the customer account (decision D1
  of the domain model): a customer with several fleets, or an individual truck owner with no
  fleet, should not have to duplicate an area per fleet, and a vehicle that is in no fleet is
  never checked today. Account ownership is also what recipient scoping of `GEOFENCE_ALERT`s and
  access control on the geofence API will need (items 40, 71 - same identity gap).
- **Reason for deferral**: There is no customer account table and no `identity` domain yet;
  decision D6 of `docs/planners/done/backend-happy-path-completion.md` scoped geofences to a fleet as the closest existing owner rather than block
  F-A5. Adding `account_id` now would be a placeholder column with no table to reference.
- **Related planner/feature**: `docs/planners/done/backend-happy-path-completion.md` (D6, D7), F-A5, F-F1 (`identity`), `fleet`, `telemetry`
  (`telemetry/geofencing.py`), item 47 (resolved), item 40.
- **Date recorded**: 2026-10-01
- **Additional notes**: When customer accounts land: add `account_id` (FK to
  `customer_accounts`), decide whether `fleet_id` stays as an optional narrower scope, and
  change the containment lookup used by ingestion from "the vehicle's current fleet" to "the
  vehicle's account (and fleet, if kept)". Follow the DBML-first procedure in
  `.claude/rules/database.md`; `fleet.service.list_geofences_containing` is the only query to
  change on the read side.

### 87. Battery pack, module and cell management

- **Short description**: Track the parts inside a battery: its packs, modules and cells, each
  with its own ID and position, so a battery can be taken apart, broken or worn parts removed,
  and a new battery assembled from the good ones (with a record of where each part came from).
- **Purpose/role in the system**: The battery is up to two thirds of a truck's price; managing it
  part by part supports cell-level diagnostics, single-part replacement, rebuilt batteries and
  their resale value.
- **Reason for deferral**: Owner decision (2026-10-04, VH-08 in `docs/decisions/decision-log.md`):
  the battery is managed as one asset first (`battery_models`, `batteries`); the part level needs
  part IDs from the manufacturer and the BMS, which we do not have yet.
- **Related planner/feature**: BAT-01 (`docs/product/features/features.yaml`), MON-07, VH-08.
- **Date recorded**: 2026-10-04
- **Additional notes**: Expected shape: a `battery_components` table (component type, serial,
  position, the battery it currently sits in, with fitting history); a rebuilt battery is a new
  `batteries` row whose components came from older ones. The `batteries` design must not block
  this.

### 88. Battery leasing

- **Short description**: G3 owns batteries and leases them to the customers whose trucks carry
  them (battery as a service), separately from the truck.
- **Purpose/role in the system**: Lowers the truck's purchase price for customers; G3 keeps the
  battery as its own asset and charges for its use.
- **Reason for deferral**: Owner decision (2026-10-04, VH-08 in `docs/decisions/decision-log.md`):
  the data model already allows it (`batteries.organization_id` is the battery's own owner, which
  may differ from the truck's owner), but the leasing contract, its pricing and billing are not
  designed.
- **Related planner/feature**: BAT-01 (`docs/product/features/features.yaml`), the billing domain
  (subscriptions, invoices), VH-08.
- **Date recorded**: 2026-10-04
- **Additional notes**: When designed: who sees the battery's data when its owner and the
  truck's owner differ (both parties, like a charging session), the lease terms and how they are
  invoiced.

### 89. Per-truck ADAS equipment flag

- **Short description**: Record whether a truck is fitted with ADAS (advanced driver assistance:
  forward collision, lane departure, tailgating warnings), as an option fitted per truck rather
  than a property of its model.
- **Purpose/role in the system**: The data inputs (IN-32) ask for an "has ADAS" flag to choose a
  truck's plan, and the in-cab ADAS alerts feature (SAF-04) only works on fitted trucks.
- **Reason for deferral**: Owner decision (2026-10-04, VH-15 in `docs/decisions/decision-log.md`):
  ADAS is optional and comes later; no truck has it today.
- **Related planner/feature**: SAF-04 (`docs/product/features/features.yaml`), Data IN-32, VH-15.
- **Date recorded**: 2026-10-04
- **Additional notes**: If the ADAS unit is a registered device (like the T-Box), "fitted" can be
  derived from that device instead of a flag on `vehicles`.

---

## Update rules

1. **When to record an entry**: When a developer or AI agent decides to skip/remove a component for the reason "not needed right now, but will definitely need to be added later".
2. **When not to record an entry**: When the component genuinely isn't needed for the system (no plan to add it in the future).
3. **Format**: Add a new entry following the template above, numbered sequentially.
4. **Review**: Periodically (e.g., every sprint) review this file to plan implementation.
5. **Closing an entry**: When an item is resolved, completed or superseded, add its resolution note and move the whole entry to `deferred-resolved.md`, keeping its number; numbers are never reused.
