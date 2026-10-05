<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# SUP — Support & rescue

*CSKH & cứu hộ* · [← Feature catalog](../README.md)

Support tickets, SOS, the support queue and SLA, and dispatching repair and rescue partners. Backend domain: `support`.

## Checklist

- [ ] **SUP-01** [Support tickets](#sup-01) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **SUP-02** [SOS](#sup-02) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **SUP-03** [Support queue and SLA monitor](#sup-03) — Backend ⬜ · Portal ⬜
- [ ] **SUP-04** [Repair and rescue partner directory](#sup-04) — Backend ⬜ · Portal ⬜
- [ ] **SUP-05** [Partner dispatch](#sup-05) — Backend ⬜ · Portal ⬜
- [ ] **SUP-06** [Partner response intake](#sup-06) — Backend ⬜ · App ⬜
- [ ] **SUP-07** [Rescue status tracking](#sup-07) — Backend ⬜ · App ⬜

## Features

<a id="sup-01"></a>

### SUP-01 Support tickets

*Yêu cầu hỗ trợ* · Must · P1.0 · Included in every plan

Customers raise a request in the app with the truck's VIN, position and error code attached automatically; Zalo and hotline contacts are logged as tickets too; each ticket has a category, status and SLA timer.

**Value:** Faster, better-informed support.

**Users:** Driver, Fleet manager, Customer care

**Capabilities:**

- Create with automatic vehicle context and photos
- Lifecycle open → acknowledged → resolved → closed
- SLA deadline fixed at creation
- Customer claims and bug reports as categories
- Data request as a category: a request for a truck owner's driving data is released only with the owner's written approval or an authority's order, and logged

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [VEH-01](vehicles.md#veh-01), [DRV-01](drivers.md#drv-01)  
**Needed by:** [SUP-02](#sup-02), [SUP-03](#sup-03)  
**Also touches:** `vehicles`, `drivers`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-I1 · Data IN-51 · Data OUT-61 · Decision DR-11  
**Old codes:** F-I1

**Open questions:**

- Who staffs 24/7 customer care (in-house or outsourced), and which SLA can we commit to?

<a id="sup-02"></a>

### SUP-02 SOS

*SOS khẩn cấp* · Must · P1.0 · Included in every plan

An always-visible SOS button sends the position and active error code to customer care, which calls back within 5 minutes; a hotline SOS is recorded the same way.

**Value:** Drivers in trouble get help fast.

**Users:** Driver, Customer care

**Capabilities:**

- One-tap SOS, works in the background
- Critical alert to the support console immediately
- Hotline fallback recorded as SOS

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [SUP-01](#sup-01), [NTF-01](notifications.md#ntf-01)  
**Needed by:** [SUP-05](#sup-05)  
**Also touches:** `notifications`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-I2 · Data IN-51 · Decision SP-05  
**Old codes:** F-I2

<a id="sup-03"></a>

### SUP-03 Support queue and SLA monitor

*Hàng đợi CSKH & giám sát SLA* · Must · P1.0 · Internal only

The support console lists open tickets with their context, category and status, and warns before and when an SLA is breached.

**Value:** No customer waits beyond the promise.

**Users:** Customer care

**Capabilities:**

- Queue with filters (awaiting response, breached, channel)
- Warning before breach and escalation after

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [SUP-01](#sup-01), [NTF-08](notifications.md#ntf-08)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-57 · deferred.md 70

<a id="sup-04"></a>

### SUP-04 Repair and rescue partner directory

*Danh bạ đối tác sửa chữa & cứu hộ* · Must · P1.0 · Internal only

Keep the partner network: type, service area, capabilities, opening hours, committed SLA, rating and revenue share.

**Value:** Changing regional partners needs no software change.

**Users:** Customer care, Operations

**Capabilities:**

- Partner organizations with a partner profile
- Service areas and capabilities
- Rating after each case

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [ACC-01](identity.md#acc-01)  
**Needed by:** [MNT-02](maintenance.md#mnt-02), [SUP-05](#sup-05)  
**Also touches:** `identity`  
**Related tables:** — (after the database review)  
**Sources:** Data IN-38 · NF-21 · deferred.md 68

**Open questions:**

- Who runs the repair and rescue network: a G3 workshop, Tri-Ring dealers or regional third parties?

<a id="sup-05"></a>

### SUP-05 Partner dispatch

*Điều phối đối tác* · Must · P1.1 · To be priced

Send an incident (position, VIN, error code, photos, required capability) to the nearest available partner; the partner must accept within 15 minutes with an ETA.

**Value:** Trucks are back on the road sooner.

**Users:** Customer care, System (automatic), Partner technician

**Capabilities:**

- Suggest or auto-route to the nearest capable partner
- Acceptance SLA ≤15 minutes; re-route on timeout
- Kept in sync with the ticket; incident history per truck

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [SUP-02](#sup-02), [SUP-04](#sup-04)  
**Needed by:** [SUP-06](#sup-06)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-I4 · Data OUT-58 · NF-21  
**Old codes:** F-I4

<a id="sup-06"></a>

### SUP-06 Partner response intake

*Tiếp nhận phản hồi của đối tác* · Must · P1.1 · Internal only

Partners confirm a case, give their ETA, report the result and the cost, in their own app account or through an API.

**Value:** Live status for drivers and costs for invoicing.

**Users:** Partner technician, External system

**Capabilities:**

- Accept or decline, ETA, result, cost, photos
- Standard intake API for partners' software

**Status:** Backend ⬜ · App ⬜

**Depends on:** [SUP-05](#sup-05), [ACC-21](identity.md#acc-21)  
**Needed by:** [SUP-07](#sup-07)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-52 · NF-21

**Open questions:**

- Content of partner responses to be clarified (Hùng Võ).

<a id="sup-07"></a>

### SUP-07 Rescue status tracking

*Theo dõi trạng thái cứu hộ* · Must · P1.1 · Included in every plan

The driver follows the SOS or rescue case: progress, the partner handling it and the estimated arrival time.

**Value:** Less anxiety and fewer calls to support.

**Users:** Driver

**Capabilities:**

- Case timeline in the app
- Partner name and ETA

**Status:** Backend ⬜ · App ⬜

**Depends on:** [SUP-06](#sup-06)  
**Related tables:** — (after the database review)  
**Sources:** Data OUT-16 · PRD F-I4
