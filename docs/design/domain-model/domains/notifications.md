<!-- GENERATED from domain-model.dbml by .claude/skills/domain-model/scripts/domain_model.py - do not edit by hand; edit the .dbml and regenerate. -->

# Notifications

[← Overview](../overview.md)

✅ built: 1 · 📋 planned: 3

Alerts raised by other domains: who received them, and how they are delivered.

- A **notification** is usually about one vehicle.
- It belongs to the organization owning the truck at that moment, and reaches each **recipient** (a person) once, with that person's seen and read state (NT-09, NT-10). Channels at launch: the app and portal inbox, push and e-mail; SMS later (NT-11).
- Each **organization** switches push and e-mail per kind of alert; only its exceptions to the defaults are stored (NT-12).

## Diagram

```mermaid
erDiagram
  notifications {
    bigint notification_id PK
    uuid organization_id FK "planned"
    uuid vehicle_id FK
  }
  notification_recipients {
    bigint notification_recipient_id PK
    bigint notification_id FK
    uuid user_id FK
  }
  organization_notification_settings {
    uuid organization_notification_setting_id PK
    uuid organization_id FK
  }
  organization_notification_setting_history {
    bigint history_id PK
    uuid organization_notification_setting_id FK
    uuid changed_by FK
  }
  notifications }o..|| organizations : "organization_id"
  notifications }o--o| vehicles : "vehicle_id"
  notification_recipients }o..|| notifications : "notification_id"
  notification_recipients }o..|| users : "user_id"
  organization_notification_settings }o..|| organizations : "organization_id"
  organization_notification_setting_history }o..o| organization_notification_settings : "organization_notification_setting_id"
  organization_notification_setting_history }o..o| users : "changed_by"
```

Only key columns are shown. Solid line = built link, dashed = planned. Tables from other domains (no columns): [organizations](identity.md#organizations), [users](identity.md#users), [vehicles](vehicles.md#vehicles).

## Tables

### notifications

**No. 43** · ✅ built · owner: **customer** · features: F-A2, F-A3, F-A4, F-J1, F-J3

One alert (battery, anomaly, SOH, device offline ...). One generic table;
each alert type is an enum value plus a payload shape.
Reviewed last (NT-08) and settled in NT-09: required organization, a generic
subject link that opens the right screen, read state per person in the
recipients table. Append-only; no change history, no soft delete.
Check constraint (NT-09): (subject_type IS NULL) = (subject_id IS NULL).

| Column | Type | Null | Key | References | Meaning | Example |
|---|---|---|---|---|---|---|
| `notification_id` | bigint | no | PK |  | Auto-increasing ID; also the polling cursor (after_id). | `5821` |
| `organization_id` | uuid | no | FK | [organizations](identity.md#organizations).organization_id (on delete restrict) | **📋 planned (NT-09)**: Organization the alert belongs to, written once: the truck's owner at that moment (DM-24 case C). | `3f6c2a1e-8b4d-4e2a-9c1f-0a7d5b2e4c11` |
| `notification_type` | notificationtype | no |  |  | Kind of event that raised it. | `BATTERY_ALERT` |
| `severity` | notificationseverity | no |  |  | How urgent it is, independent of type. | `WARNING` |
| `vehicle_id` | uuid | yes | FK | [vehicles](vehicles.md#vehicles).vehicle_id (on delete cascade) | The truck it concerns, for filtering by truck (NTF-01); NULL when no truck is concerned. Planned (NT-09): its foreign key becomes ON DELETE RESTRICT, like every other link (today CASCADE would delete alerts with their truck). | `7a4c1e9b-3d2f-4b8a-a6c5-1e0d9f8b7a44` |
| `subject_type` | varchar(30) | yes |  |  | **📋 planned (NT-09)**: What the alert is about, and so which screen the app opens (NTF-02): VEHICLE, TELEMATIC, CHARGING_SESSION, SUPPORT_CASE, TRIP, GEOFENCE, WALLET... A new kind adds a value, not a column. No foreign key (it points to different tables); the target always exists since rows are never hard-deleted (DM-25). Opening it applies that screen's own access check. | `TRIP` |
| `subject_id` | uuid | yes |  |  | **📋 planned (NT-09)**: ID of that object; set exactly when subject_type is set. | `2e1c8a5f-0b7d-4e9c-b4a3-6d5f1e8c2b76` |
| `title` | varchar(200) | no |  |  | Short summary. | `Pin còn 20%` |
| `body` | varchar(500) | no |  |  | Longer description. | `Xe 51D-123.45 còn 20% pin. Trạm gần nhất: Trạm sạc G3 Bình Dương (8.4 km).` |
| `payload` | jsonb | no |  |  | Type-specific details as JSON. | `{"threshold": 20, "soc": 19.8, "nearest_station_id": "4b9d6f3a-2e8c-4a1b-9d5e-7f0c3a8b2d88", "distance_m": 8400}` |
| `created_at` | timestamptz | no |  |  | When the notification was raised. | `2026-09-15T08:30:00Z` |
| `read_at` | timestamptz | yes |  |  | **🗑️ to be removed (NT-09)**: When an operator marked it read; moves to the recipients table, because one alert reaches several people. | `NULL` |

**Enum values**

- `notificationtype`: BATTERY_ALERT, ANOMALY_ALERT, SOH_ALERT, DEVICE_OFFLINE_ALERT, SOS_ALERT, GEOFENCE_ALERT, NO_DRIVER_CHECK_IN_ALERT (📋 planned), OUTSIDE_DRIVER_CHECK_IN (📋 planned), NO_TRIP_STARTED (📋 planned), LOW_WALLET_BALANCE (📋 planned), TOP_UP_RECEIVED (📋 planned), CHARGING_RECEIPT (📋 planned)
- `notificationseverity`: INFO, WARNING, CRITICAL

**Indexes**

- `ix_notifications_vehicle_id` (vehicle_id)
- `ix_notifications_organization_cursor` (organization_id, notification_id) - Planned (NT-09): an organization's alerts after a cursor

**Referenced by**

- [notification_recipients](#notification_recipients).notification_id (planned)

### notification_recipients

**No. 44** · 📋 planned · owner: **customer** · features: F-A2, F-F3

Who received an alert, and whether they saw and read it (NT-10). The inbox is
per person across all their organizations (opening an alert switches to its
organization), so the row reads its organization through the alert. Seen
clears the badge count; read is the tap that opens the alert's screen. Mark
all as read sets both on every unread row and is idempotent (NT-04). No
change history, no soft delete.

| Column | Type | Null | Key | References | Meaning | Example |
|---|---|---|---|---|---|---|
| `notification_recipient_id` | bigint | no | PK |  | Auto-increasing internal ID; alerts x people is a large number, so bigint. | `918273` |
| `notification_id` | bigint | no | FK | [notifications](#notifications).notification_id (on delete restrict) | The alert. | `5821` |
| `user_id` | uuid | no | FK | [users](identity.md#users).user_id (on delete restrict) | The person who receives it: someone whose role and data scope include the alert (NTF-06, roles only until plans exist, BL-16), plus the driver checked in to the truck, even from another organization (NT-07). | `9b2e7d4a-1c3f-4a8e-b6d2-5e0f1a9c3d22` |
| `seen_at` | timestamptz | yes |  |  | When the person opened their notification list after it arrived; clears the badge count. | `2026-10-12T00:20:00Z` |
| `read_at` | timestamptz | yes |  |  | When the person tapped it and opened its screen (NT-04: unread is read_at IS NULL). | `NULL` |
| `created_at` | timestamptz | no |  |  | When it reached their inbox (UTC). | `2026-10-12T00:15:00Z` |

**Indexes**

- `uq_notification_recipients_notification_user` (notification_id, user_id) unique
- `ix_notification_recipients_inbox` (user_id, notification_id) - A person's inbox, polled with the cursor (NT-02)
- `ix_notification_recipients_unseen` (user_id) - WHERE seen_at IS NULL: the badge count

### organization_notification_settings

**No. 45** · 📋 planned · owner: **customer** · features: F-F3

An organization's push and e-mail switch for one kind of alert (NTF-05,
NT-12). The app and portal inbox always shows every alert (NT-03); this only
switches push and e-mail. Only exceptions are stored: a kind with no row uses
the default from code (sensible defaults for new organizations, e.g. push on
for every kind, e-mail on for CRITICAL kinds). No per-person choice (NT-03).
SMS is added here when it comes (deferred.md 96). Change history on: turning
a channel off is the ORG_ADMIN's decision.

🔍 = tracked column: a change to it copies the whole old row into [organization_notification_setting_history](#organization_notification_setting_history).

| Column | Type | Null | Key | References | Meaning | Example |
|---|---|---|---|---|---|---|
| `organization_notification_setting_id` | uuid | no | PK |  | Internal ID of the setting. | `3d9f5b1e-7a2c-4e8d-9b6f-2c5e8a1d4b77` |
| `organization_id` | uuid | no | FK 🔍 | [organizations](identity.md#organizations).organization_id (on delete restrict) | The organization the switch belongs to. | `3f6c2a1e-8b4d-4e2a-9c1f-0a7d5b2e4c11` |
| `notification_type` | varchar(40) | no | 🔍 |  | The kind of alert, a notificationtype value; each kind belongs to one service, so switching a kind switches that part of the service (NT-03). | `GEOFENCE_ALERT` |
| `push_enabled` | boolean | no | 🔍 |  | Send this kind as a push to the organization's recipients. | `true` |
| `email_enabled` | boolean | no | 🔍 |  | Send it by e-mail to recipients who have an address on file. | `false` |
| `created_at` | timestamptz | no | 🔍 |  | When the row was created (UTC). | `2026-10-01T02:00:00Z` |
| `updated_at` | timestamptz | no | 🔍 |  | When the row was last changed (UTC). | `2026-10-05T07:00:00Z` |

**Indexes**

- `uq_organization_notification_settings_type` (organization_id, notification_type) unique

**Referenced by**

- [organization_notification_setting_history](#organization_notification_setting_history).organization_notification_setting_id (planned)

### organization_notification_setting_history

**No. 45.h** · 📋 planned · owner: **customer** · features: F-F3 · change history of [organization_notification_settings](#organization_notification_settings)

Every earlier version of a row of `organization_notification_settings`: a copy of the whole row, taken just before a change and written by a database trigger in the same transaction. Generated by the domain-model tool from `@tracked *`; never written by hand.

| Column | Type | Null | Key | References | Meaning | Example |
|---|---|---|---|---|---|---|
| `history_id` | bigint | no | PK |  | Auto-increasing ID of the history row. | `1024` |
| `organization_notification_setting_id` | uuid | yes | FK | [organization_notification_settings](#organization_notification_settings).organization_notification_setting_id (on delete restrict) | Value before the change (organization_notification_settings.organization_notification_setting_id). | `3d9f5b1e-7a2c-4e8d-9b6f-2c5e8a1d4b77` |
| `organization_id` | uuid | yes |  |  | Value before the change (organization_notification_settings.organization_id). | `3f6c2a1e-8b4d-4e2a-9c1f-0a7d5b2e4c11` |
| `notification_type` | varchar(40) | yes |  |  | Value before the change (organization_notification_settings.notification_type). | `GEOFENCE_ALERT` |
| `push_enabled` | boolean | yes |  |  | Value before the change (organization_notification_settings.push_enabled). | `true` |
| `email_enabled` | boolean | yes |  |  | Value before the change (organization_notification_settings.email_enabled). | `false` |
| `created_at` | timestamptz | yes |  |  | Value before the change (organization_notification_settings.created_at). | `2026-10-01T02:00:00Z` |
| `updated_at` | timestamptz | yes |  |  | Value before the change (organization_notification_settings.updated_at). | `2026-10-05T07:00:00Z` |
| `changed_at` | timestamptz | no |  |  | When this version of the row was replaced. | `2026-09-10T07:15:00Z` |
| `changed_by` | uuid | yes | FK | [users](identity.md#users).user_id (on delete restrict) | User who made the change; NULL when the system made it. | `9b2e7d4a-1c3f-4a8e-b6d2-5e0f1a9c3d22` |
| `change_reason` | varchar(200) | no |  |  | Why the row was changed, set by the application for the transaction: typed by the person for an administrative decision, a fixed text for a routine action. When the application sets none, the trigger records 'Unspecified change' (DM-29). | `Customer moved to a new office` |

**Indexes**

- `ix_organization_notification_setting_history_organization_notification_setting_id_time` (organization_notification_setting_id, changed_at)
