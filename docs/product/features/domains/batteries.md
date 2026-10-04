<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# BAT — Batteries

*Pin* · [← Feature catalog](../README.md)

Each truck battery managed as an asset of its own: its model, owner and the trucks it has been fitted to. Backend domain: `batteries`.

## Checklist

- [ ] **BAT-01** [Battery registry](#bat-01) — Backend ⬜ · Portal ⬜

## Features

<a id="bat-01"></a>

### BAT-01 Battery registry

*Hồ sơ pin* · Must · P1.0 · Internal only

Manage each truck battery as an asset: its model, owner and the trucks it has been fitted to.

**Value:** The battery is up to two thirds of a truck's price; its health, warranty and value follow it from truck to truck.

**Users:** Operations, Maintenance, Warranty

**Capabilities:**

- Battery model catalog: chemistry (LFP/CATL), design capacity, voltage, layout
- Each battery: serial number, model, owner (may differ from the truck's owner), status with a reason
- One battery per truck; history of which truck each battery was installed in
- A battery owned by the seller moves to the buyer when its truck is sold

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [VEH-01](vehicles.md#veh-01)  
**Needed by:** [MNT-06](maintenance.md#mnt-06), [MON-08](telemetry.md#mon-08), [WAR-01](warranties.md#war-01)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-33 · Decision VH-08 · Decision VH-13 · Decision VH-16 · deferred.md 87 · deferred.md 88

**Open questions:**

- Does the truck's BMS report the pack serial number through the T-Box? If so, an unrecorded battery swap can raise an alert.
