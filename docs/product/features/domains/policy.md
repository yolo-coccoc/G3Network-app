<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# POL — Charging policy & warranty

*Chính sách sạc & bảo hành* · [← Feature catalog](../README.md)

Warranty-linked charging rules, violation detection with evidence, and warranty risk for drivers, fleets and the warranty team. Backend domain: `policy`.

## Checklist

- [ ] **POL-01** [Charging policy configuration](#pol-01) — Backend ⬜ · Portal ⬜
- [ ] **POL-02** [Policy violation detection](#pol-02) — Backend ⬜
- [ ] **POL-03** [Warranty-risk alert](#pol-03) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **POL-04** [Charging compliance score](#pol-04) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **POL-05** [Warranty status dashboard](#pol-05) — Backend ⬜ · Portal ⬜
- [ ] **POL-06** [Violation case files](#pol-06) — Backend ⬜ · Portal ⬜

## Features

<a id="pol-01"></a>

### POL-01 Charging policy configuration

*Cấu hình chính sách sạc* · Must · P1.0 · Internal only

The warranty team sets charging rules: allowed time windows, battery min–max (e.g. 20–90%), duration, frequency and power, per truck, fleet or model; every version is kept.

**Value:** Protects battery life and gives warranty decisions a clear basis.

**Users:** Warranty

**Capabilities:**

- Create and version policies; effective immediately
- Apply to a truck, a fleet or a model

**Status:** Backend ⬜ · Portal ⬜

**Needed by:** [POL-02](#pol-02)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-B1 · Data IN-35  
**Old codes:** F-B1

**Open questions:**

- The actual policy values are a warranty decision still to be made.

<a id="pol-02"></a>

### POL-02 Policy violation detection

*Phát hiện vi phạm chính sách sạc* · Must · P1.0 · Internal only

Check every session against the active policy (charging outside the window, battery repeatedly above 90% or below 20%, too much fast charging) and flag violations with an unchangeable evidence snapshot.

**Value:** Evidence that holds in a warranty dispute.

**Users:** System (automatic)

**Capabilities:**

- Evaluate each session and the battery pattern over time
- Categorized violation with session and telematics evidence

**Status:** Backend ⬜

**Depends on:** [POL-01](#pol-01), [CHG-07](charging_sessions.md#chg-07), [MON-01](telemetry.md#mon-01)  
**Needed by:** [POL-03](#pol-03), [POL-04](#pol-04), [POL-06](#pol-06)  
**Also touches:** `charging_sessions`, `telemetry`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-B3 · NF-11  
**Old codes:** F-B3

**Open questions:**

- Is a violation only a warning, or does it carry a contractual penalty?

<a id="pol-03"></a>

### POL-03 Warranty-risk alert

*Cảnh báo nguy cơ mất bảo hành* · Must · P1.0 · To be priced

Tell the driver and the truck owner in real time when charging behaviour puts the warranty at risk, naming the behaviour and how to fix it, plus a periodic summary.

**Value:** Customers keep their warranty; fewer disputes.

**Users:** Driver, Fleet manager, System (automatic)

**Capabilities:**

- Real-time alert per violation
- Periodic summary with advice

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [POL-02](#pol-02), [NTF-01](notifications.md#ntf-01)  
**Also touches:** `notifications`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-B5 · Data OUT-13  
**Old codes:** F-B5

<a id="pol-04"></a>

### POL-04 Charging compliance score

*Điểm tuân thủ sạc* · Must · P1.0 · To be priced

A compliance score, violation count and warranty risk level per truck, shown to the driver and the fleet.

**Value:** One number that drivers understand and improve.

**Users:** Driver, Fleet manager, Warranty

**Capabilities:**

- Score, violations and risk level per truck
- Trend over time

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [POL-02](#pol-02)  
**Needed by:** [FLT-08](fleet.md#flt-08), [PLT-06](platform.md#plt-06), [POL-05](#pol-05)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-14 · Data OUT-30

**Open questions:**

- The scoring method waits for the policy (no policy yet).

<a id="pol-05"></a>

### POL-05 Warranty status dashboard

*Bảng trạng thái bảo hành* · Must · P1.0 · To be priced

For the warranty team and fleet managers: compliance, violations and risk per truck, filterable by risk, with an export for the warranty team.

**Value:** The warranty team focuses on the trucks at risk.

**Users:** Warranty, Fleet manager

**Capabilities:**

- Risk-sorted list of trucks
- Export for the warranty team

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [POL-04](#pol-04)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-B4 · Data OUT-30  
**Old codes:** F-B4

<a id="pol-06"></a>

### POL-06 Violation case files

*Hồ sơ vi phạm* · Should · P1.0 · Internal only

A case file per violation (type, session snapshot, matching telematics data), sent to the warranty team on a schedule or on demand, with its handling history.

**Value:** The warranty team decides on solid evidence.

**Users:** Warranty, Fleet manager, System (automatic)

**Capabilities:**

- Case file with evidence
- Scheduled and on-demand sending
- Case handling history kept

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [POL-02](#pol-02)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-B6 · Data OUT-31  
**Old codes:** F-B6
