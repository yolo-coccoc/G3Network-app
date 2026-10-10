<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# WAR — Warranties

*Bảo hành* · [← Feature catalog](../README.md)

The warranties of each truck, battery and device: their periods and limits, and why a warranty was voided. Backend domain: `warranties`.

## Checklist

- [ ] **WAR-01** [Warranty register](#war-01) — Backend ✅ · Portal ⬜

## Features

<a id="war-01"></a>

### WAR-01 Warranty register

*Sổ bảo hành* · Must · P1.0 · Included in every plan

Keep every warranty of a truck, its battery, its devices and of a charger: period, limits such as distance, charge cycles, energy delivered or sessions, and its status, with the reason when a warranty is voided.

**Value:** Everyone knows what is still covered; a voided warranty is explained and provable.

**Users:** Warranty, Operations, Fleet manager

**Capabilities:**

- Warranties per truck, battery and device, set per truck, each with its own period and limits
- Expiry computed from the date and the truck's or battery's counters; voiding with a reason and history
- A battery's warranty follows the battery to another truck
- Charger warranties limited by energy delivered (kWh) and number of sessions, identified by the registered serial number

**Status:** Backend ✅ · Portal ⬜

**Depends on:** [VEH-01](vehicles.md#veh-01), [BAT-01](batteries.md#bat-01)  
**Related tables:** `warranties`  
**Sources:** Data IN-32 · Decision VH-09 · Decision VH-14 · Decision VH-18 · Decision VH-19 · Decision CS-14
