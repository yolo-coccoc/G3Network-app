<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# NTF — Notifications

*Thông báo* · [← Feature catalog](../README.md)

Delivering alerts and messages to the right people on the right channels (app, portal, push, SMS, e-mail). Backend domain: `notifications`.

## Checklist

- [ ] **NTF-01** [Notification center](#ntf-01) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **NTF-02** [Push notifications](#ntf-02) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **NTF-03** [SMS messages](#ntf-03) — Backend ⬜
- [ ] **NTF-04** [E-mail messages](#ntf-04) — Backend ⬜
- [ ] **NTF-05** [Organization channel settings](#ntf-05) — Backend ⬜ · Portal ⬜
- [ ] **NTF-06** [Recipient routing](#ntf-06) — Backend ⬜
- [ ] **NTF-07** [Delivery status tracking](#ntf-07) — Backend ⬜ · Portal ⬜
- [ ] **NTF-08** [Alert escalation](#ntf-08) — Backend ⬜ · App ⬜ · Portal ⬜

## Features

<a id="ntf-01"></a>

### NTF-01 Notification center

*Trung tâm thông báo* · Must · P1.0 · Included in every plan

Every alert and message appears in the app and the portal, with unread count and mark-as-read; the portal checks for new ones about every 10 seconds.

**Value:** One place where nothing gets lost.

**Users:** Driver, Fleet manager, Operations, Customer care

**Capabilities:**

- List, filter by type, severity and truck
- Unread count, mark one or all as read
- History kept

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Needed by:** [DEV-05](telematics.md#dev-05), [FLT-05](fleet.md#flt-05), [FLT-06](fleet.md#flt-06), [MNT-01](maintenance.md#mnt-01), [MON-04](telemetry.md#mon-04), [MON-05](telemetry.md#mon-05), [MON-06](telemetry.md#mon-06), [NTF-02](#ntf-02), [NTF-08](#ntf-08), [POL-03](policy.md#pol-03), [SAF-07](safety.md#saf-07), [STN-05](charging_stations.md#stn-05), [SUP-02](support.md#sup-02)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-F3 · Decision NT-02  
**Old codes:** F-F3

<a id="ntf-02"></a>

### NTF-02 Push notifications

*Thông báo đẩy* · Must · P1.0 · Included in every plan

Send alerts as push notifications to the person's phones and browsers.

**Value:** Alerts reach drivers even when the app is closed.

**Users:** System (automatic), Driver, Fleet manager

**Capabilities:**

- Push via Firebase to registered devices
- Open the related screen from the notification

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [NTF-01](#ntf-01), [ACC-16](identity.md#acc-16), [NTF-06](#ntf-06)  
**Needed by:** [NTF-07](#ntf-07)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-F3 · Decision ID-19 · deferred.md 41  
**Old codes:** F-F3

<a id="ntf-03"></a>

### NTF-03 SMS messages

*Tin nhắn SMS* · Must · P1.1 · To be priced

Send SMS through a brandname provider: one-time codes, and critical alerts as a fallback (e.g. battery ≤10% with no data connection).

**Value:** Reaches people with no internet; cost kept under control.

**Users:** System (automatic), Driver

**Capabilities:**

- Brandname SMS provider integration
- OTP SMS as our operating cost; alert SMS counted against the customer's quota

**Status:** Backend ⬜

**Depends on:** [NTF-06](#ntf-06)  
**Needed by:** [ACC-05](identity.md#acc-05), [NTF-07](#ntf-07)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-F3 · Data IN-47 · Decision BL-05 · Decision NT-11 · deferred.md 96  
**Old codes:** F-F3

<a id="ntf-04"></a>

### NTF-04 E-mail messages

*Thư điện tử* · Should · P1.0 · Included in every plan

Send notifications and reports by e-mail to people with an address on file.

**Value:** Office staff get reports and invoices in their inbox.

**Users:** System (automatic), Fleet manager, Accountant

**Capabilities:**

- Transactional e-mail provider
- Only when the person has an e-mail address

**Status:** Backend ⬜

**Depends on:** [NTF-06](#ntf-06)  
**Related tables:** — (after the database review)  
**Sources:** Decision NT-03

<a id="ntf-05"></a>

### NTF-05 Organization channel settings

*Cấu hình kênh thông báo của tổ chức* · Must · P1.0 · Included in every plan

Each organization switches push, SMS and e-mail on or off per service; in-app and portal delivery is always on.

**Value:** Customers control noise and SMS spending.

**Users:** Organization administrator

**Capabilities:**

- Channel switches per service
- Sensible defaults for new organizations

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [NTF-06](#ntf-06)  
**Needed by:** [PAY-12](billing.md#pay-12)  
**Related tables:** — (after the database review)  
**Sources:** Decision NT-03

<a id="ntf-06"></a>

### NTF-06 Recipient routing

*Xác định người nhận thông báo* · Must · P1.0 · Included in every plan

Decide who receives each message: people whose role and plan include the service it belongs to, within their data scope.

**Value:** The right people are told; nobody sees another customer's alerts.

**Users:** System (automatic)

**Capabilities:**

- Each message type belongs to a service
- Recipients = role ∩ plan ∩ data scope

**Status:** Backend ⬜

**Depends on:** [ACC-14](identity.md#acc-14), [ACC-15](identity.md#acc-15)  
**Needed by:** [NTF-02](#ntf-02), [NTF-03](#ntf-03), [NTF-04](#ntf-04), [NTF-05](#ntf-05), [NTF-08](#ntf-08)  
**Also touches:** `identity`  
**Related tables:** — (after the database review)  
**Sources:** Decision NT-03 · deferred.md 40

<a id="ntf-07"></a>

### NTF-07 Delivery status tracking

*Theo dõi trạng thái gửi* · Should · P1.1 · Internal only

Record whether each push, SMS, OTP and e-mail was delivered, from the providers' reports.

**Value:** Support can prove a critical alert was sent and received.

**Users:** Customer care, System (automatic)

**Capabilities:**

- Status per message and channel
- Retry failed critical messages on another channel

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [NTF-02](#ntf-02), [NTF-03](#ntf-03)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-47 · Decision NT-12

<a id="ntf-08"></a>

### NTF-08 Alert escalation

*Leo thang cảnh báo* · Should · P1.1 · To be priced

Repeat or escalate a critical alert to the next person when nobody reacts in time.

**Value:** Safety alerts are never silently ignored.

**Users:** System (automatic), Fleet manager, Operations

**Capabilities:**

- Acknowledge an alert
- Escalation rules per severity

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [NTF-01](#ntf-01), [NTF-06](#ntf-06)  
**Needed by:** [SUP-03](support.md#sup-03)  
**Related tables:** — (after the database review)  
**Sources:** deferred.md 44 · Data OUT-57
