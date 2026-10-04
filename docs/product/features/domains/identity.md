<!-- GENERATED from features.yaml by the feature-catalog skill. Edit the source, then regenerate; never edit this file by hand. -->

# ACC — Accounts & access

*Tài khoản & phân quyền* · [← Feature catalog](../README.md)

Organizations, people, logins, roles and what each person may see and do; consent and audit of personal data. Backend domain: `identity`.

## Checklist

- [ ] **ACC-01** [Organization registry](#acc-01) — Backend ⬜ · Portal ⬜
- [ ] **ACC-02** [Account manager per customer](#acc-02) — Backend ⬜ · Portal ⬜
- [ ] **ACC-03** [User accounts](#acc-03) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **ACC-04** [Login and sessions](#acc-04) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **ACC-05** [One-time codes (OTP)](#acc-05) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **ACC-06** [Password reset and phone number change](#acc-06) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **ACC-07** [Self-registration for individual customers](#acc-07) — Backend ⬜ · App ⬜
- [ ] **ACC-08** [Company onboarding by sales](#acc-08) — Backend ⬜ · Portal ⬜
- [ ] **ACC-09** [Invite users to an organization](#acc-09) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **ACC-10** [Driver account claim](#acc-10) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **ACC-11** [Bulk import of users and drivers](#acc-11) — Backend ⬜ · Portal ⬜
- [ ] **ACC-12** [Memberships](#acc-12) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **ACC-13** [Role assignment](#acc-13) — Backend ⬜ · Portal ⬜
- [ ] **ACC-14** [Feature access control](#acc-14) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **ACC-15** [Data scope enforcement](#acc-15) — Backend ⬜
- [ ] **ACC-16** [Push device registration](#acc-16) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **ACC-17** [Legal acceptance and consent](#acc-17) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **ACC-18** [Personal-data access audit log](#acc-18) — Backend ⬜ · Portal ⬜
- [ ] **ACC-19** [Data-subject requests](#acc-19) — Backend ⬜ · App ⬜ · Portal ⬜
- [ ] **ACC-20** [Emergency administrator access](#acc-20) — Backend ⬜
- [ ] **ACC-21** [API access for partners and systems](#acc-21) — Backend ⬜ · Portal ⬜
- [ ] **ACC-22** [Change history viewer](#acc-22) — Backend ⬜ · Portal ⬜

## Features

<a id="acc-01"></a>

### ACC-01 Organization registry

*Danh mục tổ chức* · Must · P1.0 · Internal only

Record every party that uses the system: companies, owner-drivers, individual customers, our own organization and repair partners, stored the same way.

**Value:** One model for all customers and partners: data ownership, contracts and invoices always point to one organization.

**Users:** Sales, Co-administrator (internal), Head administrator (internal)

**Capabilities:**

- Create and edit organizations: names, address, legal form (company or individual)
- Check the tax code against the legal form (company 10/13 digits, individual 12-digit citizen ID)
- Status Active / Suspended / Closed, with the reason
- Mark internal organizations, whose users see every organization's data
- Keep the full change history of each organization

**Status:** Backend ⬜ · Portal ⬜

**Needed by:** [ACC-02](#acc-02), [ACC-07](#acc-07), [ACC-08](#acc-08), [ACC-12](#acc-12), [FLT-01](fleet.md#flt-01), [PAY-03](billing.md#pay-03), [PAY-11](billing.md#pay-11), [SUP-04](support.md#sup-04), [VEH-02](vehicles.md#veh-02)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-F1 · Data IN-36 · Decision ID-02 · Decision ID-03 · Decision ID-05 · Decision ID-06  
**Old codes:** F-F1

<a id="acc-02"></a>

### ACC-02 Account manager per customer

*Nhân viên phụ trách khách hàng* · Should · P1.0 · Internal only

Assign one of our sales staff to each customer organization and keep a record of every reassignment.

**Value:** Every customer has a clear owner on our side for contracts, renewals and escalations.

**Users:** Sales, Co-administrator (internal)

**Capabilities:**

- Assign or change the account manager of an organization
- List the customers each sales person manages

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [ACC-01](#acc-01)  
**Related tables:** — (after the database review)  
**Sources:** Decision ID-07

<a id="acc-03"></a>

### ACC-03 User accounts

*Tài khoản người dùng* · Must · P1.0 · Included in every plan

One account per person, identified by phone number, used across every organization the person works for.

**Value:** No duplicate accounts when a driver changes or adds employers; one place to lock a person out.

**Users:** Head administrator (internal), Co-administrator (internal)

**Capabilities:**

- Profile: full name, phone number (login ID), optional e-mail
- Account status Invited / Active / Locked, with the reason; only our administrators lock a whole account
- Record who created each account and keep its change history

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Needed by:** [ACC-04](#acc-04), [ACC-05](#acc-05), [ACC-12](#acc-12)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-F1 · Data IN-36 · Data IN-49 · Decision ID-08  
**Old codes:** F-F1

<a id="acc-04"></a>

### ACC-04 Login and sessions

*Đăng nhập & phiên làm việc* · Must · P1.0 · Included in every plan

Log in with phone number and password on the app and the portal; a person in several organizations picks one after login.

**Value:** Secure, simple access; every action afterwards is tied to a known person and organization.

**Users:** Driver, Fleet manager, Organization administrator, Customer care, Operations

**Capabilities:**

- Phone + password login, logout, session expiry
- See my logged-in devices and log out one or all of them; every session ends when the password changes or the account is locked
- Organization picker after login, remembering the last one
- Temporary lockout after repeated failed logins, plus a per-device/IP rate limit; password reset by OTP still works while locked
- Passwords stored only as one-way hashes; old ones cannot be reused
- Record last login and last activity
- Login history (time, IP, device) visible to the user and our admins; alert the user on a login from a new device

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [ACC-03](#acc-03)  
**Needed by:** [ACC-16](#acc-16), [CHG-01](charging_sessions.md#chg-01)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-49 · Decision ID-08 · Decision ID-17 · Decision ID-18

<a id="acc-05"></a>

### ACC-05 One-time codes (OTP)

*Mã xác thực một lần (OTP)* · Must · P1.0 · Included in every plan

Send a one-time code by SMS to confirm a phone number when signing up, accepting an invitation, resetting a password or changing phone number.

**Value:** Proves the person owns the phone without ever sending a password by SMS.

**Users:** System (automatic), Driver, Organization administrator

**Capabilities:**

- Codes are hashed, expire, and work once; 5 wrong attempts kill a code; only the newest code counts
- Rate limit per phone number against abuse and SMS cost
- One mechanism for sign-up, invitation, password reset and phone change

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [ACC-03](#acc-03), [NTF-03](notifications.md#ntf-03)  
**Needed by:** [ACC-06](#acc-06), [ACC-07](#acc-07), [ACC-09](#acc-09)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-49 · Decision ID-16

<a id="acc-06"></a>

### ACC-06 Password reset and phone number change

*Đặt lại mật khẩu & đổi số điện thoại* · Must · P1.0 · Included in every plan

A user resets a forgotten password or moves the account to a new phone number, confirmed by a one-time code.

**Value:** Users recover access by themselves, without calling support.

**Users:** Driver, Fleet manager, Organization administrator

**Capabilities:**

- Forgotten password: OTP to the registered phone, then a new password
- Change phone number: OTP to the new number; the change is kept in history
- Change password while logged in
- Extra check by customer care or the organization admin before resetting the password of a long-inactive account (phone numbers get recycled)

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [ACC-05](#acc-05)  
**Related tables:** — (after the database review)  
**Sources:** Decision ID-16 · Design review 2026-10-03

<a id="acc-07"></a>

### ACC-07 Self-registration for individual customers

*Khách lẻ tự đăng ký* · Must · P1.0 · Included in every plan

An individual driver signs up in the app with phone number and OTP and gets a personal organization on the free default plan.

**Value:** Any electric-truck driver can start charging with us without a contract.

**Users:** Driver

**Capabilities:**

- Sign up with phone number, OTP and password
- Create a personal (individual) organization with the user as its administrator and driver
- Accept terms and privacy policy at sign-up
- Start on the free default plan (charging, wallet, receipts, history)

**Status:** Backend ⬜ · App ⬜

**Depends on:** [ACC-01](#acc-01), [ACC-05](#acc-05), [ACC-17](#acc-17), [PAY-02](billing.md#pay-02)  
**Related tables:** — (after the database review)  
**Sources:** Decision ID-15 · Decision BL-03

<a id="acc-08"></a>

### ACC-08 Company onboarding by sales

*Kinh doanh khởi tạo khách hàng doanh nghiệp* · Must · P1.0 · Internal only

Our sales team creates a company's organization, its subscription and its first administrator, who then receives an invitation.

**Value:** Contract-led customers are set up correctly from day one.

**Users:** Sales

**Capabilities:**

- Create the organization and its subscription in one flow
- Invite the first organization administrator by phone
- Assign the account manager
- The first administrator accepts the data processing agreement for the company at first login

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [ACC-01](#acc-01), [ACC-09](#acc-09), [PAY-03](billing.md#pay-03)  
**Related tables:** — (after the database review)  
**Sources:** Decision ID-15 · Decision BL-04

<a id="acc-09"></a>

### ACC-09 Invite users to an organization

*Mời người dùng vào tổ chức* · Must · P1.0 · Included in every plan

An organization administrator invites office staff by phone number with their roles; the person accepts with an OTP.

**Value:** Customers manage their own staff; we are not a bottleneck.

**Users:** Organization administrator, Co-administrator (internal)

**Capabilities:**

- Invite by phone number with one or more roles
- The invitee joins with OTP; an existing user just gains the new organization
- Resend or cancel a pending invitation

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [ACC-05](#acc-05), [ACC-12](#acc-12), [ACC-13](#acc-13)  
**Needed by:** [ACC-08](#acc-08), [ACC-10](#acc-10), [ACC-11](#acc-11)  
**Related tables:** — (after the database review)  
**Sources:** Decision ID-15

<a id="acc-10"></a>

### ACC-10 Driver account claim

*Tài xế kích hoạt tài khoản* · Must · P1.0 · Included in every plan

A fleet manager registers a driver by phone number; the driver installs the app and claims the account with an OTP.

**Value:** Drivers start using the app with no paperwork or passwords handed out.

**Users:** Fleet manager, Driver

**Capabilities:**

- Register a driver by phone with the driver role and a driver profile
- Driver claims the account with OTP and sets a password

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [ACC-09](#acc-09), [DRV-01](drivers.md#drv-01)  
**Needed by:** [ACC-11](#acc-11)  
**Also touches:** `drivers`  
**Related tables:** — (after the database review)  
**Sources:** Decision ID-15

<a id="acc-11"></a>

### ACC-11 Bulk import of users and drivers

*Nhập hàng loạt người dùng & tài xế* · Could · P1.1 · Internal only

Our operations team imports a large customer's staff and drivers from a spreadsheet.

**Value:** Onboards a big fleet in hours instead of days.

**Users:** Operations

**Capabilities:**

- Upload a spreadsheet, validate every row, report errors
- Create invitations and driver profiles for valid rows

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [ACC-09](#acc-09), [ACC-10](#acc-10)  
**Related tables:** — (after the database review)  
**Sources:** Decision ID-15

<a id="acc-12"></a>

### ACC-12 Memberships

*Thành viên tổ chức* · Must · P1.0 · Included in every plan

Record which organizations each person belongs to, when they joined and left, and whether their membership is locked.

**Value:** A driver can work for two companies, or move between them, without mixing their data.

**Users:** Organization administrator, Co-administrator (internal)

**Capabilities:**

- List members of an organization with their roles
- Lock, unlock or remove a member with a reason (removal keeps history); see when each member was invited and when they joined
- A person sees and switches between their organizations

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [ACC-01](#acc-01), [ACC-03](#acc-03)  
**Needed by:** [ACC-09](#acc-09), [ACC-13](#acc-13), [ACC-15](#acc-15), [DRV-01](drivers.md#drv-01)  
**Related tables:** — (after the database review)  
**Sources:** Decision ID-09

<a id="acc-13"></a>

### ACC-13 Role assignment

*Gán vai trò* · Must · P1.0 · Included in every plan

Give each member one or more job-title roles (driver, fleet manager, accountant...), each a fixed bundle of features.

**Value:** The same job gets the same access in every company; special cases are covered by combining roles.

**Users:** Organization administrator, Co-administrator (internal), Head administrator (internal)

**Capabilities:**

- Grant and revoke roles per membership, with history: who granted or revoked each role, and when
- Exactly one organization administrator: hand the role over in one step; our co-administrator appoints a new one if the admin is gone
- Head and co-administrator roles exist only in internal organizations; co-administrators cannot manage administrators

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [ACC-12](#acc-12)  
**Needed by:** [ACC-09](#acc-09), [ACC-14](#acc-14), [FLT-03](fleet.md#flt-03)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-F1 · Decision ID-10 · Decision ID-12  
**Old codes:** F-F1

<a id="acc-14"></a>

### ACC-14 Feature access control

*Kiểm soát quyền dùng tính năng* · Must · P1.0 · Included in every plan

Allow a feature only when it is in the person's roles and, for customers, also in their organization's plan.

**Value:** What a customer pays for is exactly what its staff can use.

**Users:** System (automatic)

**Capabilities:**

- Features = role ∩ plan for customers; the whole role for internal users
- Internal-only features are never in any plan
- The app and portal hide what the user cannot use
- Lock features of an overdue subscription

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [ACC-13](#acc-13), [PAY-02](billing.md#pay-02)  
**Needed by:** [ACC-21](#acc-21), [NTF-06](notifications.md#ntf-06)  
**Also touches:** `billing`  
**Related tables:** — (after the database review)  
**Sources:** PRD F-F1 · Decision ID-11  
**Old codes:** F-F1

<a id="acc-15"></a>

### ACC-15 Data scope enforcement

*Giới hạn phạm vi dữ liệu* · Must · P1.0 · Included in every plan

Every query returns only the current organization's data; internal users see every organization; fleet-level roles see only their assigned fleets.

**Value:** Customers never see each other's trucks, drivers or invoices.

**Users:** System (automatic)

**Capabilities:**

- Filter every organization-owned record by the current organization
- Internal organizations see all organizations, still limited by role
- Fleet-level roles limited to their fleets and every fleet below

**Status:** Backend ⬜

**Depends on:** [ACC-12](#acc-12), [FLT-03](fleet.md#flt-03)  
**Needed by:** [NTF-06](notifications.md#ntf-06)  
**Also touches:** `fleet`  
**Related tables:** — (after the database review)  
**Sources:** Decision ID-11 · Decision DM-18

<a id="acc-16"></a>

### ACC-16 Push device registration

*Đăng ký thiết bị nhận thông báo đẩy* · Must · P1.0 · Included in every plan

Register each phone or browser that should receive push notifications, and clean up stale ones.

**Value:** Push alerts reach the person's current devices and stop after logout.

**Users:** System (automatic), Driver, Fleet manager

**Capabilities:**

- Store the Firebase token on the device's login session, refreshed at login; optional when the user refuses notifications
- The token disappears with the session: logout, expiry, password change, account lock, or the app reported uninstalled
- Notifications from all of the person's organizations reach every device; opening one switches to its organization

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [ACC-04](#acc-04)  
**Needed by:** [NTF-02](notifications.md#ntf-02)  
**Related tables:** — (after the database review)  
**Sources:** Decision ID-19

<a id="acc-17"></a>

### ACC-17 Legal acceptance and consent

*Chấp thuận văn bản pháp lý & đồng ý xử lý dữ liệu* · Must · P1.0 · Included in every plan

Record who accepted which version of which legal text: a company accepts the data processing agreement on its own behalf, an employed driver acknowledges the privacy notice, an individual customer accepts the terms, privacy policy and location tracking.

**Value:** Proves lawful processing of personal data (Decree 13/2023, Law 91/2025/QH15) with the exact text accepted.

**Users:** Driver, Organization administrator, Co-administrator (internal), System (automatic)

**Capabilities:**

- Publish versions of each legal text; a new version asks everyone concerned to accept again before continuing
- The organization administrator accepts the data processing agreement for the company at first login; members cannot use the system before
- Employed drivers acknowledge the privacy notice once per version; individual customers consent per purpose
- Proof kept with each acceptance: exact text version, time, IP and device; never changed
- Withdrawing a required acceptance means leaving the service (account closure)

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Needed by:** [ACC-07](#acc-07), [ACC-19](#acc-19), [PAY-12](billing.md#pay-12)  
**Related tables:** — (after the database review)  
**Sources:** Data IN-37 · NF-08 · Decision ID-20 · Decision ID-38

**Open questions:**

- Legal adviser to confirm the company-agreement model for Vietnam (open question 6).

<a id="acc-18"></a>

### ACC-18 Personal-data access audit log

*Nhật ký truy cập dữ liệu cá nhân* · Must · P1.0 · Internal only

Log who viewed, downloaded or exported which personal, location or camera data, when, and for which ticket or reason.

**Value:** Required by law; protects drivers and lets us answer any complaint or inspection.

**Users:** System (automatic), Head administrator (internal), Co-administrator (internal)

**Capabilities:**

- Append-only log of every access to personal, location and camera data
- Ask for a reason or ticket before exporting sensitive data
- Search the log by person, organization, time and data type
- One entry per screen opened (not per refresh); kept forever, older months compressed
- Account security events in the same log: logins, failed logins, lockouts, logouts, password and phone changes, with IP and device

**Status:** Backend ⬜ · Portal ⬜

**Needed by:** [MON-10](telemetry.md#mon-10), [SAF-03](safety.md#saf-03), [SAF-06](safety.md#saf-06)  
**Related tables:** — (after the database review)  
**Sources:** PRD F-F1 · Data OUT-59 · NF-08 · Decision ID-21  
**Old codes:** F-F1

<a id="acc-19"></a>

### ACC-19 Data-subject requests

*Yêu cầu của chủ thể dữ liệu* · Should · P1.1 · Included in every plan

Handle a person's requests to see, export, correct or delete their personal data.

**Value:** Meets the data-subject rights of Decree 13/2023 within the legal deadline.

**Users:** Driver, Customer care, Co-administrator (internal)

**Capabilities:**

- Submit a request from the app
- Export a person's data in a readable file
- Delete or anonymize data not required to be kept by law
- Track each request to completion against its deadline

**Status:** Backend ⬜ · App ⬜ · Portal ⬜

**Depends on:** [ACC-17](#acc-17)  
**Related tables:** — (after the database review)  
**Sources:** NF-08 · NF-22

**Open questions:**

- Which data must be kept despite a deletion request (invoices, sessions, camera records), and for how long?

<a id="acc-20"></a>

### ACC-20 Emergency administrator access

*Tài khoản quản trị khẩn cấp* · Should · P1.0 · Internal only

A sealed head-administrator account kept offline and used only when the owner cannot be reached; every use is logged.

**Value:** An administrator account compromise can be fixed even when the owner is away.

**Users:** Head administrator (internal)

**Capabilities:**

- Create and seal the emergency account before go-live
- Alert the owners whenever it logs in

**Status:** Backend ⬜

**Related tables:** — (after the database review)  
**Sources:** Decision IS-11

<a id="acc-21"></a>

### ACC-21 API access for partners and systems

*Truy cập API cho đối tác & hệ thống* · Should · P1.1 · To be priced

Issue API credentials to a partner's or customer's software (rescue partners, TMS, regulators) with limited permissions.

**Value:** Partners and customers connect their systems without sharing a person's login.

**Users:** Co-administrator (internal), External system

**Capabilities:**

- Create, rotate and revoke API keys per organization
- Limit each key to named features
- Log every API call

**Status:** Backend ⬜ · Portal ⬜

**Depends on:** [ACC-14](#acc-14)  
**Needed by:** [SUP-06](support.md#sup-06), [TMS-02](tms.md#tms-02)  
**Related tables:** — (after the database review)  
**Sources:** NF-21 · Decision SP-06

<a id="acc-22"></a>

### ACC-22 Change history viewer

*Xem lịch sử thay đổi* · Should · P1.1 · Internal only

Show who changed an organization, a user, a role or another audited record, when, and what it looked like before.

**Value:** Disputes and mistakes are traced in minutes, without database access.

**Users:** Co-administrator (internal), Head administrator (internal), Customer care

**Capabilities:**

- Timeline of changes per record, with the acting user
- Compare any old version with the current one

**Status:** Backend ⬜ · Portal ⬜

**Related tables:** — (after the database review)  
**Sources:** Decision DM-15
