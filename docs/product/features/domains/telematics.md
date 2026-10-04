<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# DEV — Telematics devices

*Thiết bị telematics* · [← Feature catalog](../README.md)

The on-board devices that send vehicle data: registry, mapping to vehicles, health, security and remote configuration. Backend domain: `telematics`.

## Checklist

- [ ] **DEV-01** [Device registry](#dev-01) — Backend ⬜ · Portal ⬜
- [ ] **DEV-02** [Device-to-vehicle assignment](#dev-02) — Backend ⬜ · Portal ⬜
- [ ] **DEV-03** [Telematics data integration](#dev-03) — Backend ⬜
- [ ] **DEV-04** [Device health dashboard](#dev-04) — Backend ⬜ · Portal ⬜
- [ ] **DEV-05** [Device silence alert](#dev-05) — Backend ⬜ · Portal ⬜
- [ ] **DEV-06** [Tamper and power-loss detection](#dev-06) — Backend ⬜ · Portal ⬜
- [ ] **DEV-07** [Remote device configuration](#dev-07) — Backend ⬜ · Portal ⬜
- [ ] **DEV-08** [Device identity certificates](#dev-08) — Backend ⬜ · Portal ⬜

## Features

<a id="dev-01"></a>

### DEV-01 Device registry

*Danh mục thiết bị* · Must · P1.0 · Internal only

Keep every telematics device: serial, IMEI, SIM/ICCID, firmware and status.

**Value:** Operations know which device is where and in what condition.

**Users:** Operations

**Capabilities:**

- Create, edit, search and retire devices
- Unique serial; retired devices keep their history
- Each device has its own owner, the truck's owner or G3; a device the seller owns moves with the truck when it is sold

**Status:** Backend ⬜ · Portal ⬜

**Needed by:** [DEV-02](#dev-02), [DEV-04](#dev-04), [DEV-08](#dev-08)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-19 · Data IN-20 · Data IN-21 · PRD F-G1 · Decision TX-07  
**Old codes:** F-G1

<a id="dev-02"></a>

### DEV-02 Device-to-vehicle assignment

*Gắn thiết bị với xe* · Must · P1.0 · Internal only

Link a device to a truck at handover, replace it when needed, and keep the history of which device served which truck.

**Value:** Data from a device always lands on the right truck.

**Users:** Operations

**Capabilities:**

- One live device per truck
- Assign, unassign and replace, with history

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [DEV-01](#dev-01), [VEH-01](vehicles.md#veh-01)  
**Needed by:** [DEV-05](#dev-05), [DEV-07](#dev-07), [VEH-05](vehicles.md#veh-05)  
**Also touches:** `vehicles`  
**Related tables:** — (after the database review)  
**Sources:** Data IN-19 · PRD F-G1 · Decision TX-02 · Decision TX-03  
**Old codes:** F-G1

<a id="dev-03"></a>

### DEV-03 Telematics data integration

*Tích hợp dữ liệu telematics* · Must · P1.0 · Internal only

Receive data from the Tri-Ring on-board unit over a defined, versioned contract, switchable between a simulator and real trucks.

**Value:** Every monitoring feature depends on this; the swappable interface lets us build before the real spec is final.

**Users:** System (automatic)

**Capabilities:**

- Versioned message contract (fields, units, frequency)
- Simulator and real device behind the same interface
- All core fields received in full

**Status:** Backend ⬜

**Needed by:** [MON-01](telemetry.md#mon-01)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-G1 · Data IN-17 · Prerequisite 1  
**Old codes:** F-G1

**Open questions:**

- The Tri-Ring telematics spec (fields, frequency, protocol, BMS access) is not signed off; fallback is a third-party OBD/CAN gateway.

<a id="dev-04"></a>

### DEV-04 Device health dashboard

*Dashboard sức khỏe thiết bị* · Must · P1.0 · Internal only

Per-device view of last seen, online/silent, firmware, SIM and data status, power, storage, GNSS and signal; share of healthy devices.

**Value:** Problems are fixed before customers notice missing data.

**Users:** Operations

**Capabilities:**

- Online and silent flags derived from the latest data
- SIM/ICCID, data, power, storage and GNSS status
- Share of healthy devices across the network

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [DEV-01](#dev-01), [MON-01](telemetry.md#mon-01)  
**Also touches:** `telemetry`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-J1 · Data IN-21 · Data IN-22 · Data OUT-55 · deferred.md 50  
**Old codes:** F-J1

<a id="dev-05"></a>

### DEV-05 Device silence alert

*Cảnh báo thiết bị mất tín hiệu* · Must · P1.0 · Internal only

Alert operations when a truck has sent no data for longer than a set time, once per silence.

**Value:** A broken or removed device is noticed within hours, not weeks.

**Users:** System (automatic), Operations

**Capabilities:**

- Periodic check of the newest data per active device
- One alert per silence episode; configurable threshold

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [DEV-02](#dev-02), [NTF-01](notifications.md#ntf-01)  
**Needed by:** [DEV-06](#dev-06)  
**Also touches:** `telemetry`, `notifications`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-J1 · PRD F-J3 · Data OUT-56  
**Old codes:** F-J1, F-J3

<a id="dev-06"></a>

### DEV-06 Tamper and power-loss detection

*Phát hiện tháo thiết bị & mất nguồn* · Must · P1.0 · Internal only

Tell apart a sudden power cut (possible removal), a lost signal and a truck that is simply switched off, and alert on suspected tampering.

**Value:** Protects the trucks and the data; supports repossession when needed.

**Users:** System (automatic), Operations

**Capabilities:**

- Power-loss and tamper signals from the device
- Classify silence: power cut, no signal, ignition off
- High-priority alert on suspected tampering

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [DEV-05](#dev-05), [MON-09](telemetry.md#mon-09)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-J3 · Data IN-22 · Data OUT-56 · deferred.md 51  
**Old codes:** F-J3

**Open questions:**

- The device contract has no power or tamper signal yet; needs the manufacturer.

<a id="dev-07"></a>

### DEV-07 Remote device configuration

*Cấu hình thiết bị từ xa* · Should · P1.1 · Internal only

Push configuration changes (send interval, local alert thresholds) to one device or a whole fleet, confirm they were applied, and roll back.

**Value:** Tune devices without sending a technician to the truck.

**Users:** Operations

**Capabilities:**

- Push to one device or every device of a fleet, with a result per device
- Confirmation that the device applied the change
- Rollback and history of every command

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [DEV-02](#dev-02)  
**Also touches:** `fleet`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-J2 · deferred.md 52 · deferred.md 53 · deferred.md 56 · deferred.md 59  
**Old codes:** F-J2

<a id="dev-08"></a>

### DEV-08 Device identity certificates

*Chứng chỉ định danh thiết bị* · Must · P1.0 · Internal only

Give each device a unique certificate or token at provisioning, check it on every connection, and revoke it when needed.

**Value:** Nobody can send fake data in a truck's name.

**Users:** Operations, System (automatic)

**Capabilities:**

- Issue a per-device certificate or token at provisioning
- Mutual TLS or token check on the data broker
- Revoke a device; per-device topic permissions

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [DEV-01](#dev-01)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-23 · NF-06 · deferred.md 36 · deferred.md 57
