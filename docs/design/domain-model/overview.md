<!-- GENERATED from domain-model.dbml by .claude/skills/domain-model/scripts/domain_model.py - do not edit by hand; edit the .dbml and regenerate. -->

# Domain model: overview

This is the domain model of the G3 Network backend: every table, which
domain owns it, who owns its data, and how it links to the rest.

**Status:** ✅ built (in the code today) · 📋 planned (a feature needs it)
· 🆕 proposed (not in the feature list, but the model needs it).

**Data owner:** *customer* = belongs to one organization (normally a customer; a G3
company can own such rows too, e.g. G3 Mobility's trucks) and carries
`organization_id`; *internal* = our own data, shared across organizations;
*two-party* = a G3 asset used by a customer (e.g. a charging session);
*undecided* = waiting on an open decision.

**How to use:** edit `domain-model.dbml` only, then run
`uv run --project backend --with pydbml --with openpyxl python .claude/skills/domain-model/scripts/domain_model.py generate`
from the repository root. `... check` verifies that built tables still match
the backend models and that these views are up to date.

## At a glance

**55 tables in 14 domains:** 50 main, 4 history (N.h, generated), 1 state · 18 built, 26 planned, 11 proposed.

## Domain map

```mermaid
flowchart LR
  identity["Identity<br/>6 planned · 8 proposed"]:::planned
  vehicles["Vehicles<br/>1 built · 1 planned · 1 proposed"]:::partial
  telematics["Telematics<br/>1 built"]:::built
  telemetry["Telemetry<br/>1 built"]:::built
  drivers["Drivers<br/>2 built · 1 proposed"]:::partial
  fleet["Fleet<br/>3 built · 1 planned"]:::partial
  charging_stations["Charging stations<br/>5 built · 1 planned"]:::partial
  charging_sessions["Charging sessions<br/>3 built"]:::built
  notifications["Notifications<br/>1 built"]:::built
  support["Support<br/>1 built · 2 planned"]:::partial
  policy["Policy<br/>4 planned"]:::planned
  billing["Billing<br/>8 planned · 1 proposed"]:::planned
  scoring["Scoring<br/>1 planned"]:::planned
  unassigned["Unassigned<br/>2 planned"]:::planned
  telematics --> vehicles
  telemetry --> telematics
  telemetry --> vehicles
  drivers -.-> identity
  drivers --> vehicles
  fleet --> vehicles
  fleet -.-> identity
  charging_stations -.-> drivers
  charging_sessions --> charging_stations
  charging_sessions -.-> vehicles
  charging_sessions -.-> drivers
  notifications --> vehicles
  support --> vehicles
  support --> drivers
  policy -.-> identity
  policy -.-> vehicles
  policy -.-> fleet
  policy -.-> charging_sessions
  billing -.-> charging_stations
  billing -.-> charging_sessions
  billing -.-> drivers
  billing -.-> vehicles
  scoring -.-> drivers
  unassigned -.-> vehicles
  unassigned -.-> drivers
  vehicles -.-> identity
  classDef built fill:#C8E6C9,stroke:#2E7D32,color:#1B5E20
  classDef partial fill:#FFE0B2,stroke:#EF6C00,color:#6D3100
  classDef planned fill:#ECEFF1,stroke:#78909C,color:#37474F,stroke-dasharray:5 5
```

Green = fully built · orange = partly built · grey dashed = not built yet. An arrow **A → B** means A's data points to B's; solid = at least one such link is built, dashed = all planned.

Not drawn, to keep the map readable: 12 domains also point to **identity** through `organization_id` (organizations): vehicles, telemetry, drivers, fleet, charging_stations, charging_sessions, notifications, support, policy, billing, scoring, unassigned.

## Domains

| Domain | Status | ✅ | 📋 | 🆕 | Tables |
|---|---|---|---|---|---|
| [Identity](domains/identity.md) | planned | 0 | 6 | 8 | 1 organizations, 1.h organization_history, 2 users, 2.h user_history, 3 user_state, 4 memberships, 4.h membership_history, 5 user_credentials, 6 user_sessions, 7 one_time_codes, 8 user_consents, 9 legal_documents, 10 user_role_assignments, 11 access_audit_logs |
| [Vehicles](domains/vehicles.md) | partial | 1 | 1 | 1 | 12 vehicles, 12.h vehicle_history, 13 vehicle_ownerships |
| [Telematics](domains/telematics.md) | built | 1 | 0 | 0 | 14 telematics |
| [Telemetry](domains/telemetry.md) | built | 1 | 0 | 0 | 15 vehicle_telemetry |
| [Drivers](domains/drivers.md) | partial | 2 | 0 | 1 | 16 drivers, 17 driver_vehicle_assignments, 18 charging_credentials |
| [Fleet](domains/fleet.md) | partial | 3 | 1 | 0 | 19 fleets, 20 fleet_vehicle_memberships, 21 geofences, 22 fleet_user_assignments |
| [Charging stations](domains/charging_stations.md) | partial | 5 | 1 | 0 | 23 charging_stations, 24 charging_evses, 25 charging_connectors, 26 charging_ocpp_messages, 27 charging_station_configuration_entries, 28 charging_reservations |
| [Charging sessions](domains/charging_sessions.md) | built | 3 | 0 | 0 | 29 charging_sessions, 30 charging_session_events, 31 charging_session_measurements |
| [Notifications](domains/notifications.md) | built | 1 | 0 | 0 | 32 notifications |
| [Support](domains/support.md) | partial | 1 | 2 | 0 | 33 support_cases, 34 repair_partners, 35 maintenance_bookings |
| [Policy](domains/policy.md) | planned | 0 | 4 | 0 | 36 charging_policies, 37 charging_policy_versions, 38 charging_policy_assignments, 39 policy_violations |
| [Billing](domains/billing.md) | planned | 0 | 8 | 1 | 40 tariffs, 41 payments, 42 wallets, 43 wallet_transactions, 44 invoices, 45 invoice_lines, 46 subscription_plans, 47 plan_features, 48 subscriptions |
| [Scoring](domains/scoring.md) | planned | 0 | 1 | 0 | 49 driver_scores |
| [Unassigned](domains/unassigned.md) | planned | 0 | 2 | 0 | 50 promotion_campaigns, 51 trips |

## Data ownership

| Owner | Meaning | Tables |
|---|---|---|
| **customer** | Belongs to one organization (normally a customer; a G3 company can own such rows too); carries `organization_id`. | [memberships](domains/identity.md#memberships), [membership_history](domains/identity.md#membership_history), [user_role_assignments](domains/identity.md#user_role_assignments), [vehicles](domains/vehicles.md#vehicles), [vehicle_history](domains/vehicles.md#vehicle_history), [vehicle_ownerships](domains/vehicles.md#vehicle_ownerships), [vehicle_telemetry](domains/telemetry.md#vehicle_telemetry), [drivers](domains/drivers.md#drivers), [driver_vehicle_assignments](domains/drivers.md#driver_vehicle_assignments), [charging_credentials](domains/drivers.md#charging_credentials), [fleets](domains/fleet.md#fleets), [fleet_vehicle_memberships](domains/fleet.md#fleet_vehicle_memberships), [geofences](domains/fleet.md#geofences), [fleet_user_assignments](domains/fleet.md#fleet_user_assignments), [notifications](domains/notifications.md#notifications), [support_cases](domains/support.md#support_cases), [maintenance_bookings](domains/support.md#maintenance_bookings), [charging_policy_assignments](domains/policy.md#charging_policy_assignments), [policy_violations](domains/policy.md#policy_violations), [payments](domains/billing.md#payments), [wallets](domains/billing.md#wallets), [wallet_transactions](domains/billing.md#wallet_transactions), [invoices](domains/billing.md#invoices), [invoice_lines](domains/billing.md#invoice_lines), [subscriptions](domains/billing.md#subscriptions), [driver_scores](domains/scoring.md#driver_scores), [trips](domains/unassigned.md#trips) |
| **internal** | Our own data (the organization running the platform), shared across all organizations. | [organizations](domains/identity.md#organizations), [organization_history](domains/identity.md#organization_history), [users](domains/identity.md#users), [user_history](domains/identity.md#user_history), [user_state](domains/identity.md#user_state), [user_credentials](domains/identity.md#user_credentials), [user_sessions](domains/identity.md#user_sessions), [one_time_codes](domains/identity.md#one_time_codes), [user_consents](domains/identity.md#user_consents), [legal_documents](domains/identity.md#legal_documents), [access_audit_logs](domains/identity.md#access_audit_logs), [charging_stations](domains/charging_stations.md#charging_stations), [charging_evses](domains/charging_stations.md#charging_evses), [charging_connectors](domains/charging_stations.md#charging_connectors), [charging_ocpp_messages](domains/charging_stations.md#charging_ocpp_messages), [charging_station_configuration_entries](domains/charging_stations.md#charging_station_configuration_entries), [repair_partners](domains/support.md#repair_partners), [charging_policies](domains/policy.md#charging_policies), [charging_policy_versions](domains/policy.md#charging_policy_versions), [tariffs](domains/billing.md#tariffs), [subscription_plans](domains/billing.md#subscription_plans), [plan_features](domains/billing.md#plan_features), [promotion_campaigns](domains/unassigned.md#promotion_campaigns) |
| **two-party** | A G3 asset used by a customer (both have a stake). | [charging_reservations](domains/charging_stations.md#charging_reservations), [charging_sessions](domains/charging_sessions.md#charging_sessions), [charging_session_events](domains/charging_sessions.md#charging_session_events), [charging_session_measurements](domains/charging_sessions.md#charging_session_measurements) |
| **undecided** | Waiting on an open decision. | [telematics](domains/telematics.md#telematics) |

## Open decisions

| ID | Question | Affects |
|---|---|---|
| D3 | When a vehicle **changes owner**, does its old data stay with the previous owner? | `vehicle_ownerships`, `organization_id` on telemetry |
| D5 | Who pays for a fleet driver's charge: the **driver's wallet, the fleet's wallet**, or it depends? | `wallets`, `payments` |
| D6 | Who owns the **telematic device**: the customer, or G3 as part of the subscription? | `telematics` owner |
