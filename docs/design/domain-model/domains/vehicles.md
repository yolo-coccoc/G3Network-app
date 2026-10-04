<!-- GENERATED from domain-model.dbml by .claude/skills/domain-model/scripts/domain_model.py - do not edit by hand; edit the .dbml and regenerate. -->

# Vehicles

[← Overview](../overview.md)

✅ built: 1 · 📋 planned: 1

The truck itself: identity (VIN, plate), specs, and provisioning state.

- A **vehicle** belongs to one organization at a time (`organization_id`, `owned_since`); earlier owners are in its change history, and the view `vehicle_ownership_periods` lists every period (VH-10).
- **After a sale (VH-11):** rows recorded under the previous owner (telemetry and what is built from it) stay theirs; the truck's condition (health, faults, maintenance, warranties) and lifetime totals (distance, energy, charge cycles) follow the truck.
- **One owner only (owner decision, 2026-10-03):** a truck (and a driver profile) belongs to exactly one organization in the system. When parties cooperate (e.g. an owner-driver operating under a transport company's licence), they agree between themselves and declare the owner to us; their cooperation terms are outside our responsibility and are not modelled.

## Diagram

```mermaid
erDiagram
  vehicles {
    uuid vehicle_id PK
    uuid organization_id FK "planned"
  }
  vehicle_history {
    bigint history_id PK
    uuid vehicle_id FK
    uuid changed_by FK
  }
  vehicles }o..|| organizations : "organization_id"
  telematics |o--o| vehicles : "vehicle_id"
  vehicle_telemetry }o--|| vehicles : "vehicle_id"
  driver_vehicle_assignments }o--|| vehicles : "vehicle_id"
  driving_sessions }o..|| vehicles : "vehicle_id"
  fleet_vehicle_memberships }o--|| vehicles : "vehicle_id"
  charging_sessions }o..o| vehicles : "vehicle_id"
  notifications }o--o| vehicles : "vehicle_id"
  support_cases }o--o| vehicles : "vehicle_id"
  maintenance_bookings }o..|| vehicles : "vehicle_id"
  charging_policy_assignments }o..o| vehicles : "vehicle_id"
  policy_violations }o..|| vehicles : "vehicle_id"
  subscriptions }o..|| vehicles : "vehicle_id"
  trips }o..|| vehicles : "vehicle_id"
  vehicle_history }o..o| vehicles : "vehicle_id"
  vehicle_history }o..o| users : "changed_by"
```

Only key columns are shown. Solid line = built link, dashed = planned. Tables from other domains (no columns): [charging_policy_assignments](policy.md#charging_policy_assignments), [charging_sessions](charging_sessions.md#charging_sessions), [driver_vehicle_assignments](drivers.md#driver_vehicle_assignments), [driving_sessions](drivers.md#driving_sessions), [fleet_vehicle_memberships](fleet.md#fleet_vehicle_memberships), [maintenance_bookings](support.md#maintenance_bookings), [notifications](notifications.md#notifications), [organizations](identity.md#organizations), [policy_violations](policy.md#policy_violations), [subscriptions](billing.md#subscriptions), [support_cases](support.md#support_cases), [telematics](telematics.md#telematics), [trips](unassigned.md#trips), [users](identity.md#users), [vehicle_telemetry](telemetry.md#vehicle_telemetry).

## Tables

### vehicles

**No. 13** · ✅ built · owner: **customer** · features: F-F2, F-A6

Profile of one electric truck: what it is and the decisions about it. It has
no state table: everything the truck reports comes through its T-Box and is
kept message by message in vehicle_telemetry.

🔍 = tracked column: a change to it copies the whole old row into [vehicle_history](#vehicle_history).

| Column | Type | Null | Key | References | Meaning | Example |
|---|---|---|---|---|---|---|
| `vehicle_id` | uuid | no | PK |  | Internal ID of the vehicle. | `7a4c1e9b-3d2f-4b8a-a6c5-1e0d9f8b7a44` |
| `organization_id` | uuid | no | FK 🔍 | [organizations](identity.md#organizations).organization_id (on delete restrict) | **📋 planned (VH-07)**: Organization that owns the vehicle now; changes when ownership is transferred. Earlier owners are in vehicle_history; the ownership periods come from the view vehicle_ownership_periods (VH-10). | `3f6c2a1e-8b4d-4e2a-9c1f-0a7d5b2e4c11` |
| `owned_since` | timestamptz | no | 🔍 |  | **📋 planned (VH-10)**: When the current owner took the truck (handover date, effective date of a transfer). A period ends at the next owner's owned_since. The first owner's value is the truck's handover date. | `2026-06-01T00:00:00Z` |
| `license_plate` | varchar(20) | no | 🔍 |  | Registration plate. A truck is registered in the system only once it has a plate. Editable: Vietnamese plates follow the owner (Circular 24/2023/TT-BCA), so a transferred truck gets a new plate and an old plate can reappear on another truck; the change history keeps earlier plates. Unique among vehicles neither deleted nor DECOMMISSIONED. | `51D-123.45` |
| `vin` | varchar(17) | no | 🔍 |  | 17-character chassis number (VIN); the vehicle's business key. Editable so a typing mistake can be corrected (the app warns the user to check it before saving); the change history keeps earlier values. Other tables point to vehicle_id, never to the VIN. Unique among vehicles not deleted. | `LZGJLGR4XNX000123` |
| `make` | varchar(50) | no | 🔍 |  | Manufacturer. Replaced by a link to vehicle_models once that catalog is designed (VH-08). | `Tri-Ring` |
| `model` | varchar(50) | no | 🔍 |  | Model line. Replaced by a link to vehicle_models once that catalog is designed (VH-08). | `EVT-400` |
| `year` | integer | no | 🔍 |  | Manufacturing year. | `2025` |
| `status` | vehiclestatus | no | 🔍 |  | Service status, always set by a person (VH-05). ACTIVE: in service. MAINTENANCE: being maintained or repaired. DECOMMISSIONED: has left the system (scrapped, sold outside our service, or permanently retired); kept for history and reports, receives no new data. Whether a truck is idle or sending no data is not stored: it is computed from telemetry and the T-Box. | `ACTIVE` |
| `status_reason` | varchar(200) | yes | 🔍 |  | **📋 planned (DM-19)**: Why the vehicle is in its current status; NULL when ACTIVE. | `Brake system repair at the Binh Duong workshop` |
| `activation_status` | vehicleactivationstatus | no |  |  | **🗑️ to be removed (VH-06)**: Progress through device provisioning, a one-way ladder that never noticed a removed T-Box. Activation is computed instead: device fitted now from telematics, data received from vehicle_telemetry. | `ACTIVATED` |
| `battery_capacity_kwh` | float8 | yes | 🔍 |  | Nominal usable pack capacity in kWh, not adjusted for SOH; NULL if unknown (reports fall back to a default). Moves to the battery tables once they are designed (VH-08). | `282.0` |
| `created_at` | timestamptz | no | 🔍 |  | When the row was created (UTC). | `2026-09-01T02:00:00Z` |
| `updated_at` | timestamptz | no | 🔍 |  | When the row was last changed (UTC). | `2026-09-10T07:15:00Z` |
| `deleted_at` | timestamptz | yes | 🔍 |  | Soft-delete time, only for a vehicle registered by mistake; a real truck that leaves is DECOMMISSIONED, never deleted. NULL while the row is live. | `NULL` |

**Enum values**

- `vehiclestatus`: ACTIVE, ~~INACTIVE~~ (to be removed), MAINTENANCE, DECOMMISSIONED
- `vehicleactivationstatus`: PENDING, DEVICE_ASSIGNED, ACTIVATED

**Indexes**

- `uq_vehicles_live_vin` (vin) unique - Planned (VH-07), replaces the full unique constraint: WHERE deleted_at IS NULL
- `uq_vehicles_live_license_plate` (license_plate) unique - Planned (VH-07), replaces the full unique constraint: WHERE deleted_at IS NULL AND status <> 'DECOMMISSIONED'
- `ix_vehicles_status` (status)

**Referenced by**

- [telematics](telematics.md#telematics).vehicle_id
- [vehicle_telemetry](telemetry.md#vehicle_telemetry).vehicle_id
- [driver_vehicle_assignments](drivers.md#driver_vehicle_assignments).vehicle_id
- [driving_sessions](drivers.md#driving_sessions).vehicle_id (planned)
- [fleet_vehicle_memberships](fleet.md#fleet_vehicle_memberships).vehicle_id
- [charging_sessions](charging_sessions.md#charging_sessions).vehicle_id (planned)
- [notifications](notifications.md#notifications).vehicle_id
- [support_cases](support.md#support_cases).vehicle_id
- [maintenance_bookings](support.md#maintenance_bookings).vehicle_id (planned)
- [charging_policy_assignments](policy.md#charging_policy_assignments).vehicle_id (planned)
- [policy_violations](policy.md#policy_violations).vehicle_id (planned)
- [subscriptions](billing.md#subscriptions).vehicle_id (planned)
- [trips](unassigned.md#trips).vehicle_id (planned)
- [vehicle_history](#vehicle_history).vehicle_id (planned)

### vehicle_history

**No. 13.h** · 📋 planned · owner: **customer** · features: F-F2, F-A6 · change history of [vehicles](#vehicles)

Every earlier version of a row of `vehicles`: a copy of the whole row, taken just before a change and written by a database trigger in the same transaction. Generated by the domain-model tool from `@tracked *`; never written by hand.

| Column | Type | Null | Key | References | Meaning | Example |
|---|---|---|---|---|---|---|
| `history_id` | bigint | no | PK |  | Auto-increasing ID of the history row. | `1024` |
| `vehicle_id` | uuid | yes | FK | [vehicles](#vehicles).vehicle_id (on delete restrict) | Value before the change (vehicles.vehicle_id). | `7a4c1e9b-3d2f-4b8a-a6c5-1e0d9f8b7a44` |
| `organization_id` | uuid | yes |  |  | Value before the change (vehicles.organization_id). | `3f6c2a1e-8b4d-4e2a-9c1f-0a7d5b2e4c11` |
| `owned_since` | timestamptz | yes |  |  | Value before the change (vehicles.owned_since). | `2026-06-01T00:00:00Z` |
| `license_plate` | varchar(20) | yes |  |  | Value before the change (vehicles.license_plate). | `51D-123.45` |
| `vin` | varchar(17) | yes |  |  | Value before the change (vehicles.vin). | `LZGJLGR4XNX000123` |
| `make` | varchar(50) | yes |  |  | Value before the change (vehicles.make). | `Tri-Ring` |
| `model` | varchar(50) | yes |  |  | Value before the change (vehicles.model). | `EVT-400` |
| `year` | integer | yes |  |  | Value before the change (vehicles.year). | `2025` |
| `status` | vehiclestatus | yes |  |  | Value before the change (vehicles.status). | `ACTIVE` |
| `status_reason` | varchar(200) | yes |  |  | Value before the change (vehicles.status_reason). | `Brake system repair at the Binh Duong workshop` |
| `battery_capacity_kwh` | float8 | yes |  |  | Value before the change (vehicles.battery_capacity_kwh). | `282.0` |
| `created_at` | timestamptz | yes |  |  | Value before the change (vehicles.created_at). | `2026-09-01T02:00:00Z` |
| `updated_at` | timestamptz | yes |  |  | Value before the change (vehicles.updated_at). | `2026-09-10T07:15:00Z` |
| `deleted_at` | timestamptz | yes |  |  | Value before the change (vehicles.deleted_at). | `NULL` |
| `changed_at` | timestamptz | no |  |  | When this version of the row was replaced. | `2026-09-10T07:15:00Z` |
| `changed_by` | uuid | yes | FK | [users](identity.md#users).user_id (on delete restrict) | User who made the change; NULL when the system made it. | `9b2e7d4a-1c3f-4a8e-b6d2-5e0f1a9c3d22` |
| `change_reason` | varchar(200) | no |  |  | Why the row was changed, set by the application for the transaction: typed by the person for an administrative decision, a fixed text for a routine action. A change without a reason fails. | `Customer moved to a new office` |

**Enum values**

- `vehiclestatus`: ACTIVE, MAINTENANCE, DECOMMISSIONED

**Indexes**

- `ix_vehicle_history_vehicle_id_time` (vehicle_id, changed_at)
