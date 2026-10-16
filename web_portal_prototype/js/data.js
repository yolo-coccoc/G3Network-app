/* Mock data for the prototype - deterministic (seeded), shaped like the backend.
   Field names follow the DBML / backend schemas (vehicle_id, vin, license_plate,
   telematic_serial, ocpp_identity...), enum values follow app/domains/<d>/types.py.
   Everything here is illustrative: model specs, prices and names are invented.

   Shared contract for the page modules:
     G3.db.<collection>          arrays of records (see each block below)
     G3.db.find(collection, id)  one record by its <singular>_id
     G3.db.* helpers             series and reports generated on demand
   Pages may add their own private mock data in their own file; they never edit
   this file's arrays except through the mutation helpers at the end. */

(function () {
  /* ---------- Seeded random ---------- */
  let seed = 20261016;
  function rand() {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let x = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x;
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
  }
  const between = (a, b) => a + (b - a) * rand();
  const int = (a, b) => Math.floor(between(a, b + 1));
  const pick = (list) => list[Math.floor(rand() * list.length)];
  const NOW = Date.now();
  const MIN = 60 * 1000;
  const HOUR = 60 * MIN;
  const DAY = 24 * HOUR;
  const iso = (ms) => new Date(ms).toISOString();
  const ago = (ms) => iso(NOW - ms);
  let counter = 1000;
  const uid = (prefix) => `${prefix}-${(counter++).toString(36)}`;

  /* ---------- Places (southern Vietnam logistics corridor) ---------- */
  const PLACES = {
    hcm: { name: "TP. Hồ Chí Minh", lat: 10.776, lon: 106.700 },
    catlai: { name: "Cảng Cát Lái", lat: 10.758, lon: 106.790 },
    thuduc: { name: "Thủ Đức", lat: 10.850, lon: 106.770 },
    dian: { name: "Dĩ An", lat: 10.905, lon: 106.770 },
    vsip: { name: "VSIP Bình Dương", lat: 10.930, lon: 106.710 },
    tdm: { name: "Thủ Dầu Một", lat: 10.980, lon: 106.650 },
    tanuyen: { name: "Tân Uyên", lat: 11.050, lon: 106.800 },
    bienhoa: { name: "Biên Hòa", lat: 10.950, lon: 106.830 },
    longthanh: { name: "Long Thành", lat: 10.790, lon: 106.950 },
    nhontrach: { name: "Nhơn Trạch", lat: 10.705, lon: 106.895 },
    phumy: { name: "Phú Mỹ", lat: 10.590, lon: 107.050 },
    caimep: { name: "Cảng Cái Mép", lat: 10.530, lon: 107.020 },
    baria: { name: "Bà Rịa", lat: 10.500, lon: 107.170 },
    vungtau: { name: "Vũng Tàu", lat: 10.360, lon: 107.080 },
    daugiay: { name: "Dầu Giây", lat: 10.940, lon: 107.150 },
    benluc: { name: "Bến Lức", lat: 10.640, lon: 106.490 },
  };

  /* Main roads as lat/lon polylines; trucks move along them. */
  const ROADS = [
    { name: "CT HCM–LT–DG", width: 5, points: [[10.79, 106.75], [10.80, 106.85], [10.79, 106.95], [10.86, 107.05], [10.94, 107.15]] },
    { name: "QL51", width: 4, points: [[10.95, 106.84], [10.88, 106.90], [10.79, 106.95], [10.70, 107.00], [10.59, 107.05], [10.50, 107.17], [10.36, 107.08]] },
    { name: "QL1A", width: 4, points: [[10.64, 106.49], [10.73, 106.60], [10.84, 106.67], [10.87, 106.77], [10.95, 106.84], [10.95, 107.00], [10.94, 107.15], [10.95, 107.30]] },
    { name: "QL13", width: 3, points: [[10.80, 106.70], [10.88, 106.68], [10.98, 106.65], [11.13, 106.62]] },
    { name: "Mỹ Phước–Tân Vạn", width: 3, points: [[10.90, 106.80], [10.93, 106.71], [11.05, 106.68]] },
    { name: "Vành đai 2", width: 3, points: [[10.85, 106.77], [10.80, 106.78], [10.76, 106.79]] },
    { name: "CT BL–LT", width: 4, points: [[10.64, 106.49], [10.66, 106.62], [10.69, 106.78], [10.71, 106.88], [10.79, 106.95]] },
    { name: "ĐT25B", width: 2, points: [[10.71, 106.88], [10.70, 106.95], [10.72, 107.02]] },
    { name: "Tân Uyên", width: 2, points: [[10.95, 106.84], [11.05, 106.80]] },
    { name: "Cái Mép", width: 2, points: [[10.59, 107.05], [10.53, 107.02]] },
  ];

  /* ---------- Organizations (ACC-01) ---------- */
  const organizations = [
    { organization_id: "org-g3", name: "Công ty CP G3 Network", short_name: "G3 Network", legal_form: "COMPANY", tax_code: "0318123456", kind: "INTERNAL", is_internal: true, status: "ACTIVE", status_reason: null, address: "Tầng 12, 2 Hải Triều, Bến Nghé, Quận 1, TP. HCM", created_at: ago(400 * DAY) },
    { organization_id: "org-tcx", name: "Công ty CP Logistics Tân Cảng Xanh", short_name: "Tân Cảng Xanh", legal_form: "COMPANY", tax_code: "0312987654", kind: "CUSTOMER", is_internal: false, status: "ACTIVE", status_reason: null, address: "KCN Nhơn Trạch 3, Đồng Nai", created_at: ago(210 * DAY) },
    { organization_id: "org-mn", name: "Công ty TNHH Vận tải Miền Nam EV", short_name: "Miền Nam EV", legal_form: "COMPANY", tax_code: "3702345678", kind: "CUSTOMER", is_internal: false, status: "ACTIVE", status_reason: null, address: "KCN VSIP I, Thuận An, Bình Dương", created_at: ago(160 * DAY) },
    { organization_id: "org-dn", name: "HTX Vận tải Đồng Nai Xanh", short_name: "Đồng Nai Xanh", legal_form: "COMPANY", tax_code: "3603456789", kind: "CUSTOMER", is_internal: false, status: "SUSPENDED", status_reason: "Hóa đơn tháng 8 quá hạn 30 ngày", address: "Phường Long Bình, Biên Hòa, Đồng Nai", created_at: ago(120 * DAY) },
    { organization_id: "org-ind", name: "Nguyễn Văn Tâm", short_name: "Nguyễn Văn Tâm", legal_form: "INDIVIDUAL", tax_code: "079088001234", kind: "CUSTOMER", is_internal: false, status: "ACTIVE", status_reason: null, address: "Phú Mỹ, Bà Rịa - Vũng Tàu", created_at: ago(75 * DAY) },
    { organization_id: "org-p1", name: "Garage Cứu hộ 24/7 Biên Hòa", short_name: "Cứu hộ Biên Hòa", legal_form: "COMPANY", tax_code: "3604567890", kind: "PARTNER", is_internal: false, status: "ACTIVE", status_reason: null, address: "QL1A, Biên Hòa, Đồng Nai", created_at: ago(180 * DAY) },
    { organization_id: "org-p2", name: "Xưởng dịch vụ xe điện Cái Mép", short_name: "Xưởng Cái Mép", legal_form: "COMPANY", tax_code: "3501234567", kind: "PARTNER", is_internal: false, status: "ACTIVE", status_reason: null, address: "KCN Cái Mép, Phú Mỹ, BR-VT", created_at: ago(170 * DAY) },
    { organization_id: "org-p3", name: "Cứu hộ Giao thông Sài Gòn", short_name: "Cứu hộ Sài Gòn", legal_form: "COMPANY", tax_code: "0309876543", kind: "PARTNER", is_internal: false, status: "ACTIVE", status_reason: null, address: "Xa lộ Hà Nội, Thủ Đức, TP. HCM", created_at: ago(150 * DAY) },
  ];

  /* ---------- People: users, memberships, roles (ACC-03, ACC-12, ACC-13) ---------- */
  const SURNAMES = ["Nguyễn", "Trần", "Lê", "Phạm", "Hoàng", "Huỳnh", "Phan", "Vũ", "Võ", "Đặng", "Bùi", "Đỗ", "Hồ", "Ngô", "Dương"];
  const MIDDLE = ["Văn", "Minh", "Hữu", "Thanh", "Quốc", "Đức", "Thị", "Ngọc", "Hoàng", "Gia"];
  const GIVEN = ["Hùng", "Dũng", "Tuấn", "Phong", "Long", "Nam", "Sơn", "Khoa", "Tài", "Lộc", "Phúc", "Bình", "Hải", "Thắng", "Trung", "Quang", "Vinh", "Toàn", "Hiếu", "Đạt", "Lan", "Hương", "Mai", "Thảo"];
  const phone = () => "09" + int(10000000, 99999999);
  const users = [];
  const memberships = [];

  function addPerson(fullName, organizationId, roles, extra = {}) {
    const user = {
      user_id: uid("usr"),
      full_name: fullName,
      phone_number: extra.phone || phone(),
      email: extra.email === undefined ? null : extra.email,
      status: extra.status || "ACTIVE",
      status_reason: extra.status_reason || null,
      created_at: ago(int(30, 300) * DAY),
      last_login_at: extra.status === "INVITED" ? null : ago(int(5, 60 * 24 * 6) * MIN),
    };
    users.push(user);
    // Invited after the organization was created, joined a few days after the invitation.
    const org = organizations.find((o) => o.organization_id === organizationId);
    const orgAgeDays = Math.max(2, Math.round((NOW - new Date(org.created_at).getTime()) / DAY));
    const invitedAt = NOW - int(1, orgAgeDays - 1) * DAY;
    const membership = {
      membership_id: uid("mbr"),
      user_id: user.user_id,
      organization_id: organizationId,
      status: extra.membership_status || (user.status === "INVITED" ? "INVITED" : "ACTIVE"),
      status_reason: null,
      roles: roles.slice(),
      fleet_ids: extra.fleet_ids || [],
      invited_at: iso(invitedAt),
      joined_at: user.status === "INVITED" ? null : iso(Math.min(NOW - HOUR, invitedAt + int(0, 4) * DAY + int(1, 20) * HOUR)),
      left_at: null,
    };
    memberships.push(membership);
    return { user, membership };
  }

  const people = {
    head: addPerson("Trần Minh Quân", "org-g3", ["HEAD_ADMIN"], { email: "quan.tran@g3network.vn" }),
    coadmin: addPerson("Lê Thu Hà", "org-g3", ["CO_ADMIN"], { email: "ha.le@g3network.vn" }),
    sales: addPerson("Phạm Gia Bảo", "org-g3", ["SALES"], { email: "bao.pham@g3network.vn" }),
    sales2: addPerson("Võ Ngọc Anh", "org-g3", ["SALES"], { email: "anh.vo@g3network.vn" }),
    ops: addPerson("Đặng Quốc Việt", "org-g3", ["OPERATIONS"], { email: "viet.dang@g3network.vn" }),
    care: addPerson("Huỳnh Thảo My", "org-g3", ["CUSTOMER_CARE"], { email: "my.huynh@g3network.vn" }),
    warranty: addPerson("Bùi Đức Thịnh", "org-g3", ["WARRANTY"], { email: "thinh.bui@g3network.vn" }),
    accountant: addPerson("Ngô Thị Lan", "org-g3", ["ACCOUNTANT"], { email: "lan.ngo@g3network.vn" }),
    maintenance: addPerson("Hồ Văn Lực", "org-g3", ["MAINTENANCE"], { email: "luc.ho@g3network.vn" }),
    tcxAdmin: addPerson("Nguyễn Hoàng Phúc", "org-tcx", ["ORG_ADMIN"], { email: "phuc.nh@tancangxanh.vn" }),
    tcxFm: addPerson("Lê Minh Khoa", "org-tcx", ["FLEET_MANAGER"], { email: "khoa.lm@tancangxanh.vn" }),
    tcxFmLimited: addPerson("Phan Thanh Tùng", "org-tcx", ["FLEET_MANAGER", "DISPATCHER"], { email: "tung.pt@tancangxanh.vn" }),
    tcxDispatch: addPerson("Trần Ngọc Mai", "org-tcx", ["DISPATCHER"], { email: null }),
    tcxAcc: addPerson("Vũ Thị Hương", "org-tcx", ["ACCOUNTANT"], { email: "huong.vt@tancangxanh.vn" }),
    mnAdmin: addPerson("Đỗ Quang Huy", "org-mn", ["ORG_ADMIN", "FLEET_MANAGER"], { email: "huy.dq@miennam-ev.vn" }),
    dnAdmin: addPerson("Dương Văn Bình", "org-dn", ["ORG_ADMIN", "FLEET_MANAGER"], { email: null }),
    indOwner: addPerson("Nguyễn Văn Tâm", "org-ind", ["ORG_ADMIN", "DRIVER"], { email: null }),
    p1Tech: addPerson("Lý Văn Mạnh", "org-p1", ["TECHNICIAN", "ORG_ADMIN"], { email: null }),
    invited: addPerson("Hoàng Gia Huy", "org-tcx", ["FLEET_MANAGER"], { status: "INVITED", email: null }),
    lockedOne: addPerson("Trương Văn Hậu", "org-tcx", ["DISPATCHER"], { email: null, membership_status: "LOCKED" }),
  };
  memberships.find((m) => m.membership_id === people.lockedOne.membership.membership_id).status_reason = "Nghỉ việc, chờ bàn giao";
  // One person in two organizations: the TCX fleet manager also dispatches for Đồng Nai Xanh (ACC-12 picker).
  memberships.push({
    membership_id: uid("mbr"), user_id: people.tcxFm.user.user_id, organization_id: "org-dn", status: "ACTIVE", status_reason: null,
    roles: ["DISPATCHER"], fleet_ids: [], invited_at: ago(60 * DAY), joined_at: ago(58 * DAY), left_at: null,
  });

  // Account managers (ACC-02)
  organizations.find((o) => o.organization_id === "org-tcx").account_manager_user_id = people.sales.user.user_id;
  organizations.find((o) => o.organization_id === "org-mn").account_manager_user_id = people.sales.user.user_id;
  organizations.find((o) => o.organization_id === "org-dn").account_manager_user_id = people.sales2.user.user_id;
  organizations.find((o) => o.organization_id === "org-ind").account_manager_user_id = people.sales2.user.user_id;

  /* ---------- Fleets (FLT-01) ---------- */
  const fleets = [
    { fleet_id: "flt-tcx-south", organization_id: "org-tcx", name: "Miền Nam", code: "MN", parent_fleet_id: null, status: "ACTIVE" },
    { fleet_id: "flt-tcx-catlai", organization_id: "org-tcx", name: "Depot Cát Lái", code: "CL", parent_fleet_id: "flt-tcx-south", status: "ACTIVE" },
    { fleet_id: "flt-tcx-caimep", organization_id: "org-tcx", name: "Depot Cái Mép", code: "CM", parent_fleet_id: "flt-tcx-south", status: "ACTIVE" },
    { fleet_id: "flt-tcx-tractor", organization_id: "org-tcx", name: "Đội đầu kéo", code: "DK", parent_fleet_id: null, status: "ACTIVE" },
    { fleet_id: "flt-mn-bd", organization_id: "org-mn", name: "Đội Bình Dương", code: "BD", parent_fleet_id: null, status: "ACTIVE" },
    { fleet_id: "flt-dn-main", organization_id: "org-dn", name: "Đội chính", code: "DN1", parent_fleet_id: null, status: "ACTIVE" },
  ];
  // FLT-03: Phan Thanh Tùng is limited to Depot Cái Mép.
  memberships.find((m) => m.membership_id === people.tcxFmLimited.membership.membership_id).fleet_ids = ["flt-tcx-caimep"];

  /* ---------- Vehicle and battery models (VEH-03, BAT-01) ---------- */
  const vehicle_models = [
    { vehicle_model_id: "vm-262", code: "EVT-262", name: "Tri-Ring EVT-262", kind: "Xe tải 4x2", gross_weight_t: 18, battery_capacity_kwh: 211, consumption_empty_kwh_km: 0.78, consumption_loaded_kwh_km: 1.05, connector: "CCS2" },
    { vehicle_model_id: "vm-400", code: "EVT-400", name: "Tri-Ring EVT-400", kind: "Đầu kéo 4x2", gross_weight_t: 40, battery_capacity_kwh: 350, consumption_empty_kwh_km: 1.05, consumption_loaded_kwh_km: 1.45, connector: "CCS2" },
    { vehicle_model_id: "vm-825", code: "EVT-825", name: "Tri-Ring EVT-825", kind: "Đầu kéo 6x4", gross_weight_t: 49, battery_capacity_kwh: 423, consumption_empty_kwh_km: 1.20, consumption_loaded_kwh_km: 1.70, connector: "CCS2" },
  ];
  const battery_models = [
    { battery_model_id: "bm-211", name: "CATL LFP 211 kWh", chemistry: "LFP", capacity_kwh: 211, nominal_voltage_v: 576, layout: "1P180S", cells: 180 },
    { battery_model_id: "bm-350", name: "CATL LFP 350 kWh", chemistry: "LFP", capacity_kwh: 350, nominal_voltage_v: 614, layout: "2P192S", cells: 384 },
    { battery_model_id: "bm-423", name: "CATL LFP 423 kWh", chemistry: "LFP", capacity_kwh: 423, nominal_voltage_v: 614, layout: "2P192S", cells: 384 },
  ];
  const BATTERY_FOR_MODEL = { "vm-262": "bm-211", "vm-400": "bm-350", "vm-825": "bm-423" };

  /* ---------- Vehicles, devices, batteries (VEH-01, DEV-01, BAT-01) ---------- */
  const PLATE_PREFIX = { "org-tcx": ["51C", "60C"], "org-mn": ["61C"], "org-dn": ["60C"], "org-ind": ["72C"], "org-g3": ["51D"] };
  const vehicles = [];
  const telematics = [];
  const batteries = [];

  /** Point along a road at fraction f (0..1). */
  function pointOnRoad(road, f) {
    const pts = road.points;
    const segs = pts.length - 1;
    const x = Math.min(Math.max(f, 0), 0.9999) * segs;
    const i = Math.floor(x);
    const r = x - i;
    const a = pts[i];
    const b = pts[i + 1];
    const heading = (Math.atan2(b[1] - a[1], b[0] - a[0]) * 180) / Math.PI;
    return { lat: a[0] + (b[0] - a[0]) * r, lon: a[1] + (b[1] - a[1]) * r, heading: (heading + 360) % 360 };
  }

  const FLEET_PLAN = [
    // [organization, fleet, model, count]
    ["org-tcx", "flt-tcx-catlai", "vm-262", 5],
    ["org-tcx", "flt-tcx-caimep", "vm-400", 4],
    ["org-tcx", "flt-tcx-tractor", "vm-825", 4],
    ["org-tcx", null, "vm-262", 1],
    ["org-mn", "flt-mn-bd", "vm-262", 4],
    ["org-mn", "flt-mn-bd", "vm-400", 3],
    ["org-dn", "flt-dn-main", "vm-400", 4],
    ["org-ind", null, "vm-400", 1],
    ["org-g3", null, "vm-262", 1],
    ["org-g3", null, "vm-825", 1],
  ];
  const ORG_ROADS = { "org-tcx": [0, 1, 6, 9, 5], "org-mn": [3, 4, 2, 8], "org-dn": [1, 2, 0], "org-ind": [1, 9], "org-g3": [5] };

  let vehicleNo = 1;
  FLEET_PLAN.forEach(([orgId, fleetId, modelId, count]) => {
    for (let k = 0; k < count; k++) {
      const model = vehicle_models.find((m) => m.vehicle_model_id === modelId);
      const vehicleId = `veh-${String(vehicleNo).padStart(3, "0")}`;
      const inStock = orgId === "org-g3";
      const plate = `${pick(PLATE_PREFIX[orgId])}-${int(100, 999)}.${String(int(0, 99)).padStart(2, "0")}`;
      const vin = `LZT${model.code.replace("EVT-", "")}${String.fromCharCode(65 + int(0, 25))}${int(1, 9)}R${String(260000 + vehicleNo * 37).padStart(7, "0")}`.slice(0, 17).padEnd(17, "0");
      const handover = inStock ? null : NOW - int(20, 200) * DAY;
      const road = ROADS[pick(ORG_ROADS[orgId])];
      const roadF = rand(); // the live tick moves the truck on from this point of its road
      const pos = pointOnRoad(road, roadF);

      // operating state mix
      const roll = rand();
      let state = roll < 0.42 ? "DRIVING" : roll < 0.68 ? "PARKED" : roll < 0.84 ? "CHARGING" : "OFF";
      let online = true;
      if (vehicleNo === 7 || vehicleNo === 19) { online = false; state = "OFF"; }
      if (inStock) { state = "OFF"; online = false; }
      const soc = state === "CHARGING" ? int(25, 80) : vehicleNo === 3 ? 18 : vehicleNo === 15 ? 9 : int(24, 96);
      const status = vehicleNo === 11 ? "INACTIVE" : "ACTIVE";

      const vehicle = {
        vehicle_id: vehicleId,
        vin,
        license_plate: plate,
        vehicle_model_id: modelId,
        organization_id: orgId,
        fleet_id: fleetId,
        status,
        status_reason: status === "INACTIVE" ? "Đang bảo dưỡng tại xưởng Cái Mép" : null,
        handover_at: handover ? iso(handover) : null,
        acquired_at: handover ? iso(handover) : iso(NOW - 30 * DAY),
        odometer_km: inStock ? int(5, 40) : int(8000, 92000),
        created_at: iso((handover || NOW - 25 * DAY) - 10 * DAY),
        live: inStock && vehicleNo % 2 === 0 ? null : {
          soc_percent: soc,
          lat: pos.lat,
          lon: pos.lon,
          heading: pos.heading,
          speed_kmh: state === "DRIVING" ? int(28, 72) : 0,
          state,
          is_online: online,
          received_at: online ? ago(int(3, 25) * 1000) : ago(int(3, 30) * HOUR),
          battery_temperature_celsius: state === "CHARGING" ? int(34, 41) : int(27, 36),
          pack_voltage_v: Math.round(between(560, 640)),
          pack_current_a: state === "CHARGING" ? -int(180, 420) : state === "DRIVING" ? int(40, 260) : 0,
          road: road.name,
          road_index: ROADS.indexOf(road),
          road_f: roadF,
        },
        soh_percent: Math.round(between(93.5, 99.4) * 10) / 10,
        cycle_count: int(80, 640),
        driver_id: null,
      };
      if (vehicleNo === 22) { vehicle.soh_percent = 87.6; vehicle.cycle_count = 1210; }
      vehicles.push(vehicle);

      // T-Box (absent on one stock truck -> NO_DEVICE)
      if (!(inStock && vehicleNo % 2 === 0)) {
        const telematic = {
          telematic_id: `tbx-${String(vehicleNo).padStart(3, "0")}`,
          telematic_serial: `TBX-2410-${String(4100 + vehicleNo * 13).padStart(5, "0")}`,
          imei: "86" + String(int(1e12, 9e12)),
          iccid: "8984" + String(int(1e14, 9e14)) + int(10, 99),
          firmware_version: pick(["v2.3.1", "v2.3.1", "v2.3.1", "v2.2.8", "v2.4.0-rc1"]),
          vehicle_id: vehicleId,
          organization_id: inStock ? "org-g3" : pick([orgId, orgId, "org-g3"]),
          status: "ACTIVE",
          installed_at: iso((handover || NOW - 5 * DAY) - 2 * DAY),
          send_interval_seconds: orgId === "org-tcx" ? 10 : 15,
          reported_interval_seconds: orgId === "org-tcx" ? (vehicleNo === 5 ? 30 : 10) : 15,
          signal_dbm: online ? -int(62, 104) : null,
          power_v: online ? Math.round(between(23.4, 27.9) * 10) / 10 : Math.round(between(0, 11) * 10) / 10,
          storage_used_percent: int(8, online ? 72 : 96),
          gnss_fix: online ? pick(["3D", "3D", "3D", "2D"]) : "NONE",
          sim_status: online ? "OK" : pick(["OK", "NO_DATA"]),
          last_seen_at: vehicle.live ? vehicle.live.received_at : null,
        };
        telematic.health = !vehicle.live ? "NO_DATA" : !online ? "SILENT" : (telematic.signal_dbm < -100 || telematic.storage_used_percent > 85 || telematic.firmware_version === "v2.2.8") ? "ATTENTION" : "HEALTHY";
        telematics.push(telematic);
        vehicle.telematic_id = telematic.telematic_id;
      } else {
        vehicle.telematic_id = null;
      }
      vehicle.activation = !vehicle.telematic_id ? "NO_DEVICE" : !vehicle.live ? "AWAITING_DATA" : "ACTIVATED";

      // Battery
      const batteryModelId = BATTERY_FOR_MODEL[modelId];
      const battery = {
        battery_id: `bat-${String(vehicleNo).padStart(3, "0")}`,
        battery_serial: `CATL-${batteryModelId.slice(3)}-${String(2400000 + vehicleNo * 211)}`,
        battery_model_id: batteryModelId,
        organization_id: vehicleNo === 12 ? "org-g3" : orgId, // VaaS: one pack leased by G3
        vehicle_id: vehicleId,
        status: "ACTIVE",
        status_reason: null,
        manufactured_on: iso((handover || NOW) - int(60, 200) * DAY).slice(0, 10),
        installed_at: iso((handover || NOW - 25 * DAY) - 3 * DAY),
        soh_percent: vehicle.soh_percent,
        cycle_count: vehicle.cycle_count,
      };
      batteries.push(battery);
      vehicle.battery_id = battery.battery_id;
      vehicleNo++;
    }
  });
  // Pin a few trucks to the states the sample alerts below describe.
  [["veh-003", "DRIVING", 18], ["veh-009", "DRIVING", 64], ["veh-015", "DRIVING", 9], ["veh-013", "PARKED", 47], ["veh-018", "PARKED", 52], ["veh-002", "DRIVING", 71]].forEach(([id, state, soc]) => {
    const v = vehicles.find((x) => x.vehicle_id === id);
    Object.assign(v.live, { state, soc_percent: soc, speed_kmh: state === "DRIVING" ? int(35, 62) : 0, is_online: true, received_at: ago(int(3, 20) * 1000) });
  });
  vehicles.find((x) => x.vehicle_id === "veh-013").live.battery_temperature_celsius = 58;

  // A spare pack in G3's stock, not fitted (BAT-01).
  batteries.push({ battery_id: "bat-spare", battery_serial: "CATL-350-2409999", battery_model_id: "bm-350", organization_id: "org-g3", vehicle_id: null, status: "INACTIVE", status_reason: "Pin dự phòng trong kho", manufactured_on: iso(NOW - 150 * DAY).slice(0, 10), installed_at: null, soh_percent: 99.8, cycle_count: 3 });
  // A device in stock, not mounted (DEV-04 NOT_MOUNTED).
  telematics.push({ telematic_id: "tbx-stock", telematic_serial: "TBX-2410-09990", imei: "861234567890123", iccid: "89840100000000000012", firmware_version: "v2.3.1", vehicle_id: null, organization_id: "org-g3", status: "ACTIVE", installed_at: null, send_interval_seconds: 15, reported_interval_seconds: null, signal_dbm: null, power_v: null, storage_used_percent: 0, gnss_fix: "NONE", sim_status: "OK", last_seen_at: null, health: "NOT_MOUNTED" });

  /* ---------- Drivers and check-ins (DRV-01, DRV-02) ---------- */
  const drivers = [];
  const DRIVER_COUNT = { "org-tcx": 16, "org-mn": 8, "org-dn": 5 };
  Object.entries(DRIVER_COUNT).forEach(([orgId, count]) => {
    for (let k = 0; k < count; k++) {
      const name = `${pick(SURNAMES)} ${pick(MIDDLE.filter((m) => m !== "Thị"))} ${pick(GIVEN.slice(0, 20))}`;
      const { user, membership } = addPerson(name, orgId, ["DRIVER"], { email: null });
      const expires = NOW + int(-20, 1500) * DAY;
      drivers.push({
        driver_id: uid("drv"),
        membership_id: membership.membership_id,
        user_id: user.user_id,
        organization_id: orgId,
        full_name: name,
        phone_number: user.phone_number,
        license_number: String(int(790000000000, 799999999999)),
        license_class: pick(["C", "CE", "CE", "CE"]),
        license_expires_on: iso(expires).slice(0, 10),
        status: k === count - 1 ? "INACTIVE" : "ACTIVE",
        status_reason: k === count - 1 ? "Nghỉ phép dài hạn" : null,
        safety_score: int(62, 98),
        created_at: ago(int(20, 200) * DAY),
      });
    }
  });
  // The owner-driver of the individual organization.
  drivers.push({ driver_id: uid("drv"), membership_id: people.indOwner.membership.membership_id, user_id: people.indOwner.user.user_id, organization_id: "org-ind", full_name: "Nguyễn Văn Tâm", phone_number: people.indOwner.user.phone_number, license_number: "791234567890", license_class: "CE", license_expires_on: iso(NOW + 900 * DAY).slice(0, 10), status: "ACTIVE", status_reason: null, safety_score: 88, created_at: ago(70 * DAY) });

  // Current check-ins: most driving/parked trucks have a driver at the wheel.
  const driving_sessions = [];
  vehicles.forEach((v) => {
    if (!v.live || v.status !== "ACTIVE") return;
    const pool = drivers.filter((d) => d.organization_id === v.organization_id && d.status === "ACTIVE" && !driving_sessions.some((s) => s.driver_id === d.driver_id && !s.ended_at));
    const needsDriver = v.live.state === "DRIVING" || (v.live.state !== "OFF" && rand() < 0.6);
    if (!pool.length || !needsDriver) return;
    if (v.vehicle_id === "veh-009") return; // MON / DRV-02: drives with nobody checked in
    const d = pick(pool);
    v.driver_id = d.driver_id;
    driving_sessions.push({ driving_session_id: uid("ds"), driver_id: d.driver_id, vehicle_id: v.vehicle_id, organization_id: v.organization_id, check_in_method: pick(["QR", "QR", "APP", "PORTAL"]), checked_in_at: ago(int(20, 300) * MIN), ended_at: null, end_cause: null });
  });
  // History of ended sessions.
  drivers.forEach((d) => {
    const orgVehicles = vehicles.filter((v) => v.organization_id === d.organization_id && v.live);
    if (!orgVehicles.length) return;
    for (let k = 0; k < int(3, 7); k++) {
      const start = NOW - int(1, 20) * DAY - int(0, 10) * HOUR;
      driving_sessions.push({ driving_session_id: uid("ds"), driver_id: d.driver_id, vehicle_id: pick(orgVehicles).vehicle_id, organization_id: d.organization_id, check_in_method: pick(["QR", "QR", "APP", "PORTAL"]), checked_in_at: iso(start), ended_at: iso(start + int(3, 11) * HOUR), end_cause: pick(["CHECKED_OUT", "CHECKED_OUT", "CHECKED_OUT", "AUTO_ENDED", "TAKEN_OVER", "OTHER_TRUCK"]) });
    }
  });

  /* ---------- Trips (MON-11) ---------- */
  const ROUTES = [["catlai", "caimep"], ["catlai", "vsip"], ["caimep", "bienhoa"], ["vsip", "catlai"], ["nhontrach", "catlai"], ["tdm", "longthanh"], ["caimep", "nhontrach"], ["benluc", "catlai"], ["dian", "phumy"], ["tanuyen", "caimep"]];
  const trips = [];
  vehicles.filter((v) => v.live && v.organization_id !== "org-g3").forEach((v) => {
    const model = vehicle_models.find((m) => m.vehicle_model_id === v.vehicle_model_id);
    for (let k = 0; k < int(4, 9); k++) {
      const [a, b] = pick(ROUTES);
      const day = int(0, 13);
      const plannedDep = NOW - day * DAY - int(1, 10) * HOUR;
      const distance = between(28, 95);
      const loaded = rand() < 0.62;
      const kwh = distance * (loaded ? model.consumption_loaded_kwh_km : model.consumption_empty_kwh_km) * between(0.92, 1.12);
      const isCurrent = k === 0 && v.live.state === "DRIVING" && v.driver_id;
      const isPlanned = k === 1 && rand() < 0.4;
      const status = isCurrent ? "IN_PROGRESS" : isPlanned ? "PLANNED" : rand() < 0.06 ? "CANCELLED" : "COMPLETED";
      const dep = isPlanned ? NOW + int(1, 20) * HOUR : isCurrent ? NOW - int(20, 80) * MIN : plannedDep;
      const durationMin = (distance / between(32, 48)) * 60;
      const drv = v.driver_id && (isCurrent || rand() < 0.5) ? v.driver_id : (pick(drivers.filter((d) => d.organization_id === v.organization_id)) || {}).driver_id;
      trips.push({
        trip_id: `TRP-${String(trips.length + 10421)}`,
        organization_id: v.organization_id,
        vehicle_id: v.vehicle_id,
        driver_id: drv || null,
        origin: PLACES[a].name,
        destination: PLACES[b].name,
        origin_key: a,
        destination_key: b,
        planned_departure_at: iso(dep),
        planned_arrival_at: iso(dep + durationMin * MIN),
        started_at: status === "COMPLETED" || status === "IN_PROGRESS" ? iso(dep + int(-10, 25) * MIN) : null,
        ended_at: status === "COMPLETED" ? iso(dep + durationMin * MIN + int(-5, 30) * MIN) : null,
        status,
        distance_km: status === "COMPLETED" ? Math.round(distance * 10) / 10 : status === "IN_PROGRESS" ? Math.round(distance * 0.45 * 10) / 10 : null,
        energy_kwh: status === "COMPLETED" ? Math.round(kwh * 10) / 10 : status === "IN_PROGRESS" ? Math.round(kwh * 0.45 * 10) / 10 : null,
        declared_load: status === "PLANNED" ? null : loaded ? "LOADED" : "EMPTY",
        inferred_load: status === "COMPLETED" ? (rand() < 0.9 ? (loaded ? "LOADED" : "EMPTY") : (loaded ? "EMPTY" : "LOADED")) : null,
        stops: status === "COMPLETED" ? int(0, 4) : 0,
        stop_minutes: status === "COMPLETED" ? int(0, 75) : 0,
      });
    }
  });
  trips.forEach((trip) => { if (trip.energy_kwh) trip.cost_vnd = Math.round(trip.energy_kwh * 4150); });

  /* ---------- Charging network (STN-01, STN-02, STN-04, STN-12) ---------- */
  const charging_locations = [
    { location_id: "loc-catlai", name: "G3 Cát Lái", place: "catlai", address: "Đường Nguyễn Thị Định, Cát Lái, Thủ Đức", organization_id: "org-g3", is_public: true, chargers: 4, solar_kw: 300, storage_kwh: 500 },
    { location_id: "loc-caimep", name: "G3 Cái Mép", place: "caimep", address: "Cảng CMIT, Phú Mỹ, BR-VT", organization_id: "org-g3", is_public: true, chargers: 4, solar_kw: 500, storage_kwh: 1000 },
    { location_id: "loc-longthanh", name: "G3 Long Thành", place: "longthanh", address: "Nút giao CT HCM–LT–DG, Long Thành", organization_id: "org-g3", is_public: true, chargers: 3, solar_kw: 0, storage_kwh: 0 },
    { location_id: "loc-vsip", name: "G3 VSIP Bình Dương", place: "vsip", address: "Đại lộ Hữu Nghị, VSIP I, Thuận An", organization_id: "org-g3", is_public: true, chargers: 3, solar_kw: 200, storage_kwh: 0 },
    { location_id: "loc-bienhoa", name: "G3 Biên Hòa", place: "bienhoa", address: "QL1A, Long Bình, Biên Hòa", organization_id: "org-g3", is_public: true, chargers: 2, solar_kw: 0, storage_kwh: 0 },
    { location_id: "loc-phumy", name: "G3 Phú Mỹ", place: "phumy", address: "QL51, Phú Mỹ, BR-VT", organization_id: "org-g3", is_public: true, chargers: 2, solar_kw: 150, storage_kwh: 250 },
    { location_id: "loc-benluc", name: "G3 Bến Lức", place: "benluc", address: "Nút giao CT Bến Lức–Long Thành, Long An", organization_id: "org-g3", is_public: true, chargers: 2, solar_kw: 0, storage_kwh: 0, status: "INACTIVE", status_reason: "Chờ nghiệm thu điện lực" },
    { location_id: "loc-tcx-depot", name: "Depot Tân Cảng Xanh – Nhơn Trạch", place: "nhontrach", address: "KCN Nhơn Trạch 3, Đồng Nai", organization_id: "org-tcx", is_public: false, allowed_organization_ids: ["org-tcx"], chargers: 2, solar_kw: 400, storage_kwh: 0 },
  ];
  const charging_stations = []; // one OCPP charger each (CS-09: "station" = place, "charger" = OCPP device)
  charging_locations.forEach((loc) => {
    loc.lat = PLACES[loc.place].lat;
    loc.lon = PLACES[loc.place].lon;
    loc.status = loc.status || "ACTIVE";
    loc.status_reason = loc.status_reason || null;
    loc.incident_contact = "Trực vận hành G3 – 1900 6868";
    loc.region = loc.place === "vsip" || loc.place === "tdm" ? "Bình Dương" : ["caimep", "phumy"].includes(loc.place) ? "Bà Rịa – Vũng Tàu" : ["catlai"].includes(loc.place) ? "TP. HCM" : loc.place === "benluc" ? "Long An" : "Đồng Nai";
    for (let k = 1; k <= loc.chargers; k++) {
      const offline = loc.status === "INACTIVE" || (loc.location_id === "loc-vsip" && k === 3);
      const power = pick([240, 480, 480]);
      const station = {
        station_id: `chg-${loc.location_id.slice(4)}-${k}`,
        location_id: loc.location_id,
        ocpp_identity: `G3-${loc.location_id.slice(4, 7).toUpperCase()}-${String(k).padStart(2, "0")}`,
        display_name: [`Charger ${k}`, `Trụ ${k}`],
        vendor: "Willdigits",
        model: `DC ${power} kW`,
        max_power_kw: power,
        ocpp_version: k % 3 === 0 ? "2.0.1" : "1.6J",
        firmware_version: pick(["WD-3.4.2", "WD-3.4.2", "WD-3.5.0"]),
        serial_number: `WD${power}-${int(230000, 239999)}`,
        status: loc.status === "INACTIVE" ? "INACTIVE" : "ACTIVE",
        last_seen_at: offline ? ago(int(2, 30) * HOUR) : ago(int(5, 55) * 1000),
        is_online: !offline,
        connected_at: offline ? null : ago(int(1, 9) * DAY),
        connectors: [],
        energy_delivered_kwh_total: int(18000, 140000),
        sessions_total: int(400, 3100),
      };
      for (let g = 1; g <= 2; g++) {
        let s = pick(["Available", "Available", "Available", "Charging", "Charging", "Preparing", "SuspendedEV", "Finishing"]);
        if (loc.location_id === "loc-longthanh" && k === 2 && g === 1) s = "Faulted";
        if (offline) s = "Unknown";
        station.connectors.push({
          connector_id: `${station.station_id}-g${g}`,
          ocpp_evse_id: g,
          ocpp_connector_id: 1,
          standard: "CCS2",
          max_power_kw: power / 2,
          status: s,
          error_code: s === "Faulted" ? "GroundFailure" : null,
          vendor_error_code: s === "Faulted" ? "E-0x2F14" : null,
          status_at: ago(int(1, 90) * MIN),
          current_power_kw: s === "Charging" ? int(Math.round(power * 0.25), Math.round(power / 2)) : 0,
        });
      }
      charging_stations.push(station);
    }
  });

  /* ---------- Tariffs (PAY-09) ---------- */
  const tariffs = charging_locations.map((loc, i) => ({
    tariff_id: `trf-${loc.location_id.slice(4)}`,
    location_id: loc.location_id,
    organization_id: loc.organization_id,
    name: loc.is_public ? ["Public time-of-use", "Giá công cộng theo giờ"] : ["Private depot price", "Giá trạm riêng"],
    status: "ACTIVE",
    effective_from: ago((40 + i) * DAY),
    version: loc.is_public ? 3 : 1,
    slots: loc.is_public
      ? [
          { from: "00:00", to: "04:00", price_vnd_kwh: 3250, source: "GRID", label: "OFF_PEAK" },
          { from: "04:00", to: "09:30", price_vnd_kwh: 4450, source: "GRID", label: "NORMAL" },
          { from: "09:30", to: "11:30", price_vnd_kwh: loc.solar_kw ? 3890 : 6290, source: loc.solar_kw ? "SOLAR" : "GRID", label: loc.solar_kw ? "SOLAR" : "PEAK" },
          { from: "11:30", to: "17:00", price_vnd_kwh: loc.solar_kw ? 3890 : 4450, source: loc.solar_kw ? "SOLAR" : "GRID", label: loc.solar_kw ? "SOLAR" : "NORMAL" },
          { from: "17:00", to: "20:00", price_vnd_kwh: loc.storage_kwh ? 5490 : 6290, source: loc.storage_kwh ? "STORAGE" : "GRID", label: "PEAK" },
          { from: "20:00", to: "22:00", price_vnd_kwh: 4450, source: "GRID", label: "NORMAL" },
          { from: "22:00", to: "24:00", price_vnd_kwh: 3250, source: "GRID", label: "OFF_PEAK" },
        ]
      : [
          { from: "00:00", to: "06:00", price_vnd_kwh: 2900, source: "GRID", label: "OFF_PEAK" },
          { from: "06:00", to: "17:00", price_vnd_kwh: 3300, source: "SOLAR", label: "SOLAR" },
          { from: "17:00", to: "24:00", price_vnd_kwh: 4100, source: "GRID", label: "NORMAL" },
        ],
  }));
  function priceAt(locationId, ms) {
    const tariff = tariffs.find((x) => x.location_id === locationId);
    if (!tariff) return 4450; // a station created in the UI has no tariff yet: the normal grid price
    const d = new Date(ms);
    const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    const slot = tariff.slots.find((s) => hm >= s.from && hm < s.to) || tariff.slots[0];
    return slot.price_vnd_kwh;
  }

  /* ---------- Charging sessions (CHG-02, CHG-04, CHG-05, CHG-06, PAY-10) ---------- */
  const charging_sessions = [];
  const activeStations = charging_stations.filter((s) => s.status === "ACTIVE");
  let txId = 5100;
  vehicles.filter((v) => v.live && v.organization_id !== "org-g3").forEach((v) => {
    const model = vehicle_models.find((m) => m.vehicle_model_id === v.vehicle_model_id);
    for (let k = 0; k < int(5, 10); k++) {
      const station = pick(activeStations.filter((s) => {
        const loc = charging_locations.find((l) => l.location_id === s.location_id);
        return loc.is_public || (loc.allowed_organization_ids || []).includes(v.organization_id);
      }));
      const start = NOW - int(0, 29) * DAY - int(0, 23) * HOUR - int(0, 59) * MIN;
      const socStart = int(12, 45);
      const socEnd = Math.min(100, socStart + int(30, 75));
      const kwh = ((socEnd - socStart) / 100) * model.battery_capacity_kwh * between(1.02, 1.06);
      const minutes = (kwh / between(140, 220)) * 60;
      const abandoned = rand() < 0.04;
      const price = priceAt(station.location_id, start);
      const vehicleKwh = kwh * between(0.955, 0.985) * (rand() < 0.07 ? 0.93 : 1); // CHG-06: a few mismatches
      const driver = drivers.find((d) => d.organization_id === v.organization_id && d.status === "ACTIVE");
      charging_sessions.push({
        session_id: uid("ses"),
        ocpp_transaction_id: String(txId++),
        station_id: station.station_id,
        location_id: station.location_id,
        connector_id: pick(station.connectors).connector_id,
        vehicle_id: v.vehicle_id,
        organization_id: v.organization_id,
        driver_id: driver ? driver.driver_id : null,
        started_at: iso(start),
        ended_at: abandoned ? null : iso(start + minutes * MIN),
        status: abandoned ? "ABANDONED" : "COMPLETED",
        energy_kwh: abandoned ? 0 : Math.round(kwh * 10) / 10,
        vehicle_reported_kwh: abandoned ? 0 : Math.round(vehicleKwh * 10) / 10,
        soc_start_percent: socStart,
        soc_end_percent: abandoned ? socStart : socEnd,
        peak_power_kw: abandoned ? 0 : int(150, station.max_power_kw / 2 + 20),
        duration_minutes: abandoned ? 0 : Math.round(minutes),
        price_vnd_kwh: price,
        cost_vnd: abandoned ? 0 : Math.round(kwh * price),
        bill_status: abandoned ? "VOID" : rand() < 0.03 ? "ON_HOLD" : "BILLED",
        start_method: pick(["QR", "QR", "QR", "REMOTE_START"]),
      });
    }
  });
  // Sessions running right now on connectors that report Charging.
  charging_stations.forEach((station) => station.connectors.forEach((c) => {
    if (c.status !== "Charging") return;
    const v = vehicles.find((x) => x.live && x.live.state === "CHARGING" && !charging_sessions.some((s) => s.vehicle_id === x.vehicle_id && s.status === "ACTIVE"));
    const start = NOW - int(8, 55) * MIN;
    charging_sessions.push({
      session_id: uid("ses"), ocpp_transaction_id: String(txId++), station_id: station.station_id, location_id: station.location_id, connector_id: c.connector_id,
      vehicle_id: v ? v.vehicle_id : null, organization_id: v ? v.organization_id : "org-ind", driver_id: v ? v.driver_id : null,
      started_at: iso(start), ended_at: null, status: "ACTIVE", energy_kwh: Math.round(((NOW - start) / HOUR) * c.current_power_kw * 10) / 10,
      vehicle_reported_kwh: null, soc_start_percent: int(15, 35), soc_end_percent: v ? v.live.soc_percent : int(40, 70), peak_power_kw: c.current_power_kw,
      duration_minutes: Math.round((NOW - start) / MIN), price_vnd_kwh: priceAt(station.location_id, start), cost_vnd: null, bill_status: "QUOTED", start_method: "QR",
    });
  }));
  charging_sessions.sort((a, b) => (a.started_at < b.started_at ? 1 : -1));

  /* ---------- Station commands (STN-10) ---------- */
  const station_commands = [];
  charging_stations.slice(0, 10).forEach((s) => {
    for (let k = 0; k < int(1, 4); k++) {
      const type = pick(["REMOTE_START", "REMOTE_STOP", "RESET", "GET_CONFIGURATION", "UNLOCK_CONNECTOR", "CHANGE_CONFIGURATION", "TRIGGER_MESSAGE"]);
      station_commands.push({ command_id: uid("cmd"), station_id: s.station_id, command_type: type, outcome: pick(["ACCEPTED", "ACCEPTED", "ACCEPTED", "REJECTED", "TIMEOUT"]), requested_by: pick([people.ops.user.user_id, null]), requested_at: ago(int(10, 6000) * MIN), answered_at: null });
    }
  });
  station_commands.forEach((c) => { c.answered_at = c.outcome === "TIMEOUT" ? null : iso(new Date(c.requested_at).getTime() + int(1, 8) * 1000); });
  station_commands.sort((a, b) => (a.requested_at < b.requested_at ? 1 : -1));

  /* ---------- Geofences (FLT-05) ---------- */
  const box = (lat, lon, dLat, dLon) => [[lat - dLat, lon - dLon], [lat - dLat, lon + dLon], [lat + dLat, lon + dLon * 1.2], [lat + dLat * 1.1, lon - dLon * 0.8]];
  const geofences = [
    { geofence_id: "geo-1", organization_id: "org-tcx", fleet_id: "flt-tcx-south", name: "Cảng Cát Lái", kind: "DEPOT", boundary: box(10.758, 106.790, 0.012, 0.014), alert_on_enter: true, alert_on_exit: true, color: "#0e9f6e" },
    { geofence_id: "geo-2", organization_id: "org-tcx", fleet_id: "flt-tcx-caimep", name: "Cảng Cái Mép", kind: "DEPOT", boundary: box(10.530, 107.020, 0.016, 0.018), alert_on_enter: true, alert_on_exit: true, color: "#0e9f6e" },
    { geofence_id: "geo-3", organization_id: "org-tcx", fleet_id: "flt-tcx-south", name: "Depot Nhơn Trạch", kind: "DEPOT", boundary: box(10.705, 106.895, 0.01, 0.012), alert_on_enter: false, alert_on_exit: true, color: "#2563eb" },
    { geofence_id: "geo-4", organization_id: "org-tcx", fleet_id: "flt-tcx-tractor", name: "Cấm xe tải nội thành", kind: "RESTRICTED", boundary: [[10.74, 106.64], [10.74, 106.72], [10.80, 106.73], [10.82, 106.66]], alert_on_enter: true, alert_on_exit: false, color: "#dc2626" },
    { geofence_id: "geo-5", organization_id: "org-mn", fleet_id: "flt-mn-bd", name: "KCN VSIP I", kind: "DEPOT", boundary: box(10.930, 106.710, 0.014, 0.016), alert_on_enter: true, alert_on_exit: true, color: "#0e9f6e" },
  ];

  /* ---------- Notifications (NTF-01, MON-04/05/06, DEV-05, FLT-05) ---------- */
  const notifications = [];
  const plate = (id) => (vehicles.find((v) => v.vehicle_id === id) || {}).license_plate;
  function note(type, severity, vehicleId, minutesAgo, en, vi, extra = {}) {
    const v = vehicles.find((x) => x.vehicle_id === vehicleId);
    notifications.push({
      notification_id: 88000 + notifications.length,
      notification_type: type,
      severity,
      vehicle_id: vehicleId || null,
      organization_id: v ? v.organization_id : extra.organization_id || "org-g3",
      title: [en, vi],
      created_at: ago(minutesAgo * MIN),
      read_at: minutesAgo > 300 ? ago((minutesAgo - 20) * MIN) : null,
      payload: extra.payload || {},
      station_id: extra.station_id || null,
    });
  }
  note("BATTERY_ALERT", "CRITICAL", "veh-015", 4, `Battery 9% on ${plate("veh-015")} — nearest station G3 Phú Mỹ, 6.2 km`, `Pin 9% trên xe ${plate("veh-015")} — trạm gần nhất G3 Phú Mỹ, 6,2 km`, { payload: { level: "CRITICAL", soc: 9, station: "G3 Phú Mỹ", distance_km: 6.2 } });
  note("SOS_ALERT", "CRITICAL", "veh-018", 11, `SOS from driver on ${plate("veh-018")} near Dĩ An`, `SOS từ tài xế xe ${plate("veh-018")} gần Dĩ An`);
  note("ANOMALY_ALERT", "CRITICAL", "veh-013", 26, `High battery temperature 58 °C on ${plate("veh-013")}`, `Nhiệt độ pin cao 58 °C trên xe ${plate("veh-013")}`, { payload: { anomaly: "HIGH_BATTERY_TEMPERATURE", value: 58 } });
  note("BATTERY_ALERT", "WARNING", "veh-003", 38, `Battery 18% on ${plate("veh-003")} — G3 Cát Lái 4.1 km`, `Pin 18% trên xe ${plate("veh-003")} — G3 Cát Lái 4,1 km`, { payload: { level: "MAIN", soc: 18, station: "G3 Cát Lái", distance_km: 4.1 } });
  note("NO_DRIVER_CHECK_IN_ALERT", "WARNING", "veh-009", 52, `${plate("veh-009")} is driving with nobody checked in`, `Xe ${plate("veh-009")} đang chạy nhưng chưa có tài xế nhận xe`);
  note("GEOFENCE_ALERT", "INFO", "veh-006", 64, `${plate("veh-006")} entered Cảng Cái Mép`, `Xe ${plate("veh-006")} vào vùng Cảng Cái Mép`, { payload: { transition: "ENTER", geofence: "Cảng Cái Mép" } });
  note("DEVICE_OFFLINE_ALERT", "WARNING", "veh-007", 95, `T-Box on ${plate("veh-007")} silent for 3 h`, `T-Box xe ${plate("veh-007")} mất tín hiệu 3 giờ`);
  note("FAULT_ALERT", "WARNING", "veh-010", 130, `Tyre pressure low (rear left) on ${plate("veh-010")} — can keep driving`, `Áp suất lốp thấp (sau trái) xe ${plate("veh-010")} — vẫn chạy được`, { payload: { dtc: "C1A2F", drivable: true } });
  note("CHARGER_FAULT", "CRITICAL", null, 150, "Charger G3-LON-02 gun 1 faulted (GroundFailure)", "Trụ G3-LON-02 súng 1 báo lỗi (GroundFailure)", { station_id: "chg-longthanh-2" });
  note("GEOFENCE_ALERT", "CRITICAL", "veh-012", 170, `${plate("veh-012")} entered restricted zone (no trucks in the city)`, `Xe ${plate("veh-012")} vào vùng cấm xe tải nội thành`, { payload: { transition: "ENTER", geofence: "Cấm xe tải nội thành" } });
  note("NO_TRIP_STARTED", "INFO", "veh-002", 200, `${plate("veh-002")} is moving but no trip was started`, `Xe ${plate("veh-002")} đang di chuyển nhưng chưa bắt đầu chuyến`);
  note("SOH_ALERT", "WARNING", "veh-022", 60 * 9, `Battery health 87.6% on ${plate("veh-022")} — below 90%`, `Sức khỏe pin 87,6% trên xe ${plate("veh-022")} — dưới ngưỡng 90%`);
  note("LOW_WALLET_BALANCE", "WARNING", null, 60 * 11, "Fleet wallet below 5,000,000 ₫", "Ví đội xe dưới 5.000.000 ₫", { organization_id: "org-mn" });
  note("POLICY_VIOLATION", "WARNING", "veh-004", 60 * 14, `${plate("veh-004")} charged to 100% three times this week`, `Xe ${plate("veh-004")} sạc đầy 100% ba lần trong tuần`);
  note("OUTSIDE_DRIVER_CHECK_IN", "INFO", "veh-016", 60 * 20, `A driver from Đồng Nai Xanh checked in to ${plate("veh-016")}`, `Tài xế của Đồng Nai Xanh nhận xe ${plate("veh-016")}`);
  note("TOP_UP_RECEIVED", "INFO", null, 60 * 26, "Top-up 50,000,000 ₫ received (VietQR)", "Đã nhận nạp 50.000.000 ₫ (VietQR)", { organization_id: "org-tcx" });
  note("BATTERY_ALERT", "INFO", "veh-001", 60 * 30, `Battery 30% on ${plate("veh-001")}`, `Pin 30% trên xe ${plate("veh-001")}`, { payload: { level: "EARLY", soc: 30 } });
  note("ANOMALY_ALERT", "WARNING", "veh-020", 60 * 40, `Sudden voltage drop on ${plate("veh-020")}`, `Sụt áp đột ngột trên xe ${plate("veh-020")}`, { payload: { anomaly: "SUDDEN_VOLTAGE_DROP" } });
  note("DEVICE_OFFLINE_ALERT", "WARNING", "veh-019", 60 * 52, `T-Box on ${plate("veh-019")} silent for 24 h`, `T-Box xe ${plate("veh-019")} mất tín hiệu 24 giờ`);
  note("CHARGING_RECEIPT", "INFO", "veh-005", 60 * 60, `Charging receipt: 168.4 kWh, 699,860 ₫`, `Biên nhận sạc: 168,4 kWh, 699.860 ₫`);
  note("TRIP_ASSIGNED", "INFO", "veh-008", 60 * 70, "Trip TRP-10433 Cát Lái → Cái Mép assigned", "Đã giao chuyến TRP-10433 Cát Lái → Cái Mép");
  notifications.sort((a, b) => (a.created_at < b.created_at ? 1 : -1));

  /* ---------- Support (SUP-01, SUP-02, SUP-03, SUP-04) ---------- */
  const support_cases = [];
  function supportCase(type, category, channel, status, vehicleId, minutesAgo, en, vi, extra = {}) {
    const v = vehicles.find((x) => x.vehicle_id === vehicleId);
    const created = NOW - minutesAgo * MIN;
    const slaMinutes = type === "SOS" ? 5 : category === "BREAKDOWN" || category === "ACCIDENT" ? 30 : category === "BILLING" ? 24 * 60 : 4 * 60;
    const drv = v && drivers.find((d) => d.driver_id === v.driver_id);
    support_cases.push({
      support_case_id: `SC-${String(support_cases.length + 2301)}`,
      case_type: type,
      category,
      channel,
      status,
      vehicle_id: vehicleId,
      vin: v ? v.vin : null,
      organization_id: v ? v.organization_id : extra.organization_id,
      driver_id: drv ? drv.driver_id : null,
      reporter_name: drv ? drv.full_name : extra.reporter || "—",
      reporter_phone: drv ? drv.phone_number : phone(),
      subject: [en, vi],
      error_code: extra.error_code || null,
      location: v && v.live ? { lat: v.live.lat, lon: v.live.lon } : null,
      created_at: iso(created),
      sla_due_at: iso(created + slaMinutes * MIN),
      acknowledged_at: status !== "OPEN" ? iso(created + Math.min(slaMinutes * 0.6, int(2, 40)) * MIN) : null,
      resolved_at: ["RESOLVED", "CLOSED"].includes(status) ? iso(created + int(40, 300) * MIN) : null,
      assignee_user_id: status !== "OPEN" ? people.care.user.user_id : null,
      partner_organization_id: extra.partner || null,
      notes: extra.notes || [],
    });
  }
  supportCase("SOS", "BREAKDOWN", "IN_APP", "OPEN", "veh-018", 11, "SOS: truck stopped, motor fault", "SOS: xe dừng, lỗi động cơ", { error_code: "P0A3F" });
  supportCase("TICKET", "BATTERY", "IN_APP", "ACKNOWLEDGED", "veh-013", 30, "Battery temperature warning on dashboard", "Cảnh báo nhiệt độ pin trên táp-lô", { error_code: "B1C42" });
  supportCase("TICKET", "CHARGING", "HOTLINE", "OPEN", "veh-003", 75, "Charger at G3 Long Thành does not start", "Trụ G3 Long Thành không khởi động sạc");
  supportCase("TICKET", "TECHNICAL", "ZALO", "ACKNOWLEDGED", "veh-010", 190, "Tyre pressure warning keeps coming back", "Cảnh báo áp suất lốp lặp lại", { error_code: "C1A2F", partner: "org-p2" });
  supportCase("TICKET", "BILLING", "IN_APP", "OPEN", "veh-005", 60 * 7, "Charged twice for one session", "Bị trừ tiền hai lần cho một phiên sạc");
  supportCase("TICKET", "OTHER", "ZALO", "RESOLVED", "veh-014", 60 * 26, "Cannot check in by QR, camera blurry", "Không quét được QR nhận xe");
  supportCase("SOS", "ACCIDENT", "HOTLINE", "RESOLVED", "veh-020", 60 * 50, "Minor collision at Dầu Giây junction", "Va chạm nhẹ tại nút giao Dầu Giây", { partner: "org-p1" });
  supportCase("TICKET", "TECHNICAL", "IN_APP", "CLOSED", "veh-001", 60 * 80, "Air conditioning not cooling", "Điều hòa không mát");
  supportCase("TICKET", "CHARGING", "IN_APP", "CLOSED", "veh-021", 60 * 100, "Session stopped at 60%", "Phiên sạc dừng ở 60%");
  supportCase("TICKET", "BREAKDOWN", "HOTLINE", "ACKNOWLEDGED", "veh-024", 160, "Truck will not power on after charging", "Xe không lên nguồn sau khi sạc", { partner: "org-p1", error_code: "U0100" });
  support_cases.sort((a, b) => (a.created_at < b.created_at ? 1 : -1));

  const partner_profiles = [
    { organization_id: "org-p1", partner_type: "RESCUE", service_area: ["Đồng Nai", "Bình Dương", "TP. HCM"], capabilities: ["Kéo xe đầu kéo", "Cứu hộ pin", "Sửa lốp"], opening_hours: "24/7", sla_minutes: 45, rating: 4.6, cases: 38, revenue_share_percent: 12, hotline: "0251 3888 247", lat: 10.955, lon: 106.86 },
    { organization_id: "org-p2", partner_type: "WORKSHOP", service_area: ["Bà Rịa – Vũng Tàu", "Đồng Nai"], capabilities: ["Chẩn đoán HV", "Thay cell pin", "Bảo dưỡng định kỳ", "Sửa lốp"], opening_hours: "07:00–19:00", sla_minutes: 60, rating: 4.8, cases: 22, revenue_share_percent: 10, hotline: "0254 3777 999", lat: 10.55, lon: 107.03 },
    { organization_id: "org-p3", partner_type: "RESCUE", service_area: ["TP. HCM", "Long An"], capabilities: ["Kéo xe tải", "Sửa lốp"], opening_hours: "24/7", sla_minutes: 40, rating: 4.2, cases: 51, revenue_share_percent: 12, hotline: "028 3896 1234", lat: 10.84, lon: 106.78 },
  ];

  /* ---------- Warranties (WAR-01) ---------- */
  const warranties = [];
  vehicles.forEach((v) => {
    if (!v.handover_at) return;
    const start = new Date(v.handover_at).getTime();
    warranties.push({ warranty_id: uid("war"), object_kind: "VEHICLE", object_id: v.vehicle_id, organization_id: v.organization_id, warranty_type: "STANDARD", starts_on: iso(start).slice(0, 10), ends_on: iso(start + 5 * 365 * DAY).slice(0, 10), distance_limit_km: 300000, cycle_limit: null, energy_limit_kwh: null, session_limit: null, status: "ACTIVE", status_reason: null });
    warranties.push({ warranty_id: uid("war"), object_kind: "BATTERY", object_id: v.battery_id, organization_id: v.organization_id, warranty_type: "STANDARD", starts_on: iso(start).slice(0, 10), ends_on: iso(start + 8 * 365 * DAY).slice(0, 10), distance_limit_km: null, cycle_limit: 4000, energy_limit_kwh: null, session_limit: null, status: v.vehicle_id === "veh-022" ? "VOIDED" : "ACTIVE", status_reason: v.vehicle_id === "veh-022" ? "Sạc DC ngoài trạm được phép nhiều lần, vi phạm điều khoản 7.2" : null });
    if (v.telematic_id) warranties.push({ warranty_id: uid("war"), object_kind: "TELEMATIC", object_id: v.telematic_id, organization_id: v.organization_id, warranty_type: "STANDARD", starts_on: iso(start).slice(0, 10), ends_on: iso(start + 2 * 365 * DAY).slice(0, 10), distance_limit_km: null, cycle_limit: null, energy_limit_kwh: null, session_limit: null, status: "ACTIVE", status_reason: null });
  });
  charging_stations.slice(0, 8).forEach((s) => warranties.push({ warranty_id: uid("war"), object_kind: "STATION", object_id: s.station_id, organization_id: "org-g3", warranty_type: pick(["STANDARD", "EXTENDED"]), starts_on: iso(NOW - 200 * DAY).slice(0, 10), ends_on: iso(NOW + 900 * DAY).slice(0, 10), distance_limit_km: null, cycle_limit: null, energy_limit_kwh: 400000, session_limit: 12000, status: "ACTIVE", status_reason: null }));

  /* ---------- Charging policy and compliance (POL-01..06) ---------- */
  const charging_policies = [
    { policy_id: "pol-1", name: "Chuẩn bảo hành pin LFP", scope: "MODEL", scope_ids: ["vm-262", "vm-400", "vm-825"], soc_min: 15, soc_max: 95, allowed_windows: ["00:00–24:00"], max_dc_sessions_per_day: 3, max_power_kw: 240, version: 4, effective_from: ago(60 * DAY), status: "ACTIVE" },
    { policy_id: "pol-2", name: "Đầu kéo Cái Mép – ưu tiên giờ thấp điểm", scope: "FLEET", scope_ids: ["flt-tcx-caimep"], soc_min: 20, soc_max: 90, allowed_windows: ["22:00–04:00", "10:00–14:00"], max_dc_sessions_per_day: 2, max_power_kw: 240, version: 2, effective_from: ago(20 * DAY), status: "ACTIVE" },
  ];
  const compliance = vehicles.filter((v) => v.live && v.organization_id !== "org-g3").map((v) => {
    const violations = v.vehicle_id === "veh-022" ? 14 : v.vehicle_id === "veh-004" ? 6 : int(0, 4);
    const score = Math.max(42, 100 - violations * int(4, 7));
    return { vehicle_id: v.vehicle_id, organization_id: v.organization_id, score, violations_30d: violations, risk: score >= 85 ? "LOW" : score >= 70 ? "MEDIUM" : "HIGH", trend: Array.from({ length: 8 }, () => Math.min(100, score + int(-8, 8))) };
  });

  /* ---------- Billing (PAY-01..13) ---------- */
  const catalog_features = [
    { code: "MON", name: ["Vehicle & battery monitoring", "Giám sát xe & pin"], unit: "TRUCK_MONTH", list_price_vnd: 350000 },
    { code: "FLT", name: ["Fleet management & live map", "Quản lý đội xe & bản đồ"], unit: "TRUCK_MONTH", list_price_vnd: 150000 },
    { code: "POL", name: ["Charging policy & warranty", "Chính sách sạc & bảo hành"], unit: "TRUCK_MONTH", list_price_vnd: 100000 },
    { code: "SAF", name: ["Compliant driver camera", "Camera giám sát tài xế"], unit: "TRUCK_MONTH", list_price_vnd: 250000 },
    { code: "ADAS", name: ["In-cab ADAS package", "Gói ADAS trong cabin"], unit: "TRUCK_MONTH", list_price_vnd: 400000 },
    { code: "CRB", name: ["Carbon reporting", "Báo cáo carbon"], unit: "ORGANIZATION_MONTH", list_price_vnd: 2000000 },
    { code: "SMS", name: ["SMS alerts (1,000)", "SMS cảnh báo (1.000 tin)"], unit: "PACK", list_price_vnd: 800000 },
  ];
  const plans = [
    { plan_id: "plan-basic", name: "Basic", visibility: "PUBLIC", features: ["MON", "FLT", "POL", "SAF"], price_vnd_truck_month: 790000, description: ["Compliant camera + vehicle, battery and charging management", "Camera hợp chuẩn + quản lý xe, pin và sạc"] },
    { plan_id: "plan-advanced", name: "Advanced", visibility: "PUBLIC", features: ["MON", "FLT", "POL", "SAF", "ADAS", "CRB"], price_vnd_truck_month: 1190000, description: ["Basic plus in-cab ADAS and carbon reporting", "Basic kèm ADAS trong cabin và báo cáo carbon"] },
    { plan_id: "plan-free", name: "Cá nhân", visibility: "DEFAULT", features: ["MON"], price_vnd_truck_month: 0, description: ["Free default plan for individual owners", "Gói mặc định miễn phí cho chủ xe cá nhân"] },
    { plan_id: "plan-tcx", name: "Tân Cảng Xanh 2026", visibility: "PRIVATE", private_organization_id: "org-tcx", features: ["MON", "FLT", "POL", "SAF", "CRB"], price_vnd_truck_month: 920000, description: ["Private plan, 24-month contract", "Gói riêng, hợp đồng 24 tháng"] },
  ];
  const subscriptions = [
    { subscription_id: "sub-tcx", organization_id: "org-tcx", plan_id: "plan-tcx", status: "ACTIVE", agreed_price_vnd_truck_month: 920000, billing_cycle: "MONTHLY", starts_on: iso(NOW - 200 * DAY).slice(0, 10), ends_on: iso(NOW + 530 * DAY).slice(0, 10), add_ons: [{ code: "SMS", quantity: 2, used: 1340, quota: 2000 }] },
    { subscription_id: "sub-mn", organization_id: "org-mn", plan_id: "plan-advanced", status: "ACTIVE", agreed_price_vnd_truck_month: 1090000, billing_cycle: "MONTHLY", starts_on: iso(NOW - 150 * DAY).slice(0, 10), ends_on: iso(NOW + 20 * DAY).slice(0, 10), add_ons: [] },
    { subscription_id: "sub-dn", organization_id: "org-dn", plan_id: "plan-basic", status: "OVERDUE", agreed_price_vnd_truck_month: 790000, billing_cycle: "MONTHLY", starts_on: iso(NOW - 110 * DAY).slice(0, 10), ends_on: iso(NOW + 255 * DAY).slice(0, 10), add_ons: [] },
    { subscription_id: "sub-ind", organization_id: "org-ind", plan_id: "plan-free", status: "ACTIVE", agreed_price_vnd_truck_month: 0, billing_cycle: "MONTHLY", starts_on: iso(NOW - 70 * DAY).slice(0, 10), ends_on: null, add_ons: [] },
  ];
  const wallets = [
    { wallet_id: "wal-tcx", organization_id: "org-tcx", owner_kind: "ORGANIZATION", owner_name: "Tân Cảng Xanh", balance_vnd: 61850000, status: "ACTIVE" },
    { wallet_id: "wal-mn", organization_id: "org-mn", owner_kind: "ORGANIZATION", owner_name: "Miền Nam EV", balance_vnd: 3920000, status: "ACTIVE" },
    { wallet_id: "wal-dn", organization_id: "org-dn", owner_kind: "ORGANIZATION", owner_name: "Đồng Nai Xanh", balance_vnd: -1250000, status: "BLOCKED" },
    { wallet_id: "wal-ind", organization_id: "org-ind", owner_kind: "USER", owner_name: "Nguyễn Văn Tâm", balance_vnd: 2400000, status: "ACTIVE" },
  ];
  const wallet_transactions = [];
  // Each wallet's history runs forwards: charging bills are paid from the balance
  // and a VietQR top-up arrives when it runs low. The final balance becomes the
  // wallet's. Đồng Nai Xanh stopped topping up, so its wallet ends negative (BLOCKED).
  wallets.forEach((w, wi) => {
    const sessions = charging_sessions.filter((x) => x.organization_id === w.organization_id && x.bill_status === "BILLED").slice(0, 14).reverse();
    const rows = [];
    let running = [12000000, 6000000, 3000000, 2500000][wi];
    sessions.forEach((x, i) => {
      const lowMark = w.status === "BLOCKED" ? -Infinity : 1500000;
      if (running - x.cost_vnd < lowMark || (w.wallet_id === "wal-tcx" && i % 4 === 3)) {
        const amount = w.wallet_id === "wal-ind" ? 5000000 : w.wallet_id === "wal-tcx" ? 20000000 : 10000000;
        running += amount;
        rows.push({ wallet_transaction_id: uid("wtx"), wallet_id: w.wallet_id, transaction_type: "TOP_UP", amount_vnd: amount, reference: "G3NAP" + int(10000, 99999), created_at: iso(new Date(x.ended_at).getTime() - 2 * HOUR), balance_after_vnd: running });
      }
      running -= x.cost_vnd;
      rows.push({ wallet_transaction_id: uid("wtx"), wallet_id: w.wallet_id, transaction_type: "SESSION_BILL", amount_vnd: -x.cost_vnd, session_id: x.session_id, created_at: x.ended_at, balance_after_vnd: running });
    });
    w.balance_vnd = running;
    rows.reverse();
    wallet_transactions.push(...rows);
  });
  const invoices = [];
  ["org-tcx", "org-mn", "org-dn"].forEach((orgId) => {
    for (let m = 1; m <= 4; m++) {
      const d = new Date(NOW); d.setDate(1); d.setMonth(d.getMonth() - m);
      const trucks = vehicles.filter((v) => v.organization_id === orgId).length;
      const sub = subscriptions.find((s) => s.organization_id === orgId);
      const energy = int(9000, 28000) * (orgId === "org-tcx" ? 1.6 : 1);
      invoices.push({ invoice_id: uid("inv"), invoice_number: `G3N/${d.getFullYear()}/${String(int(1000, 9999))}`, organization_id: orgId, kind: "MONTHLY", period: `${String(d.getMonth() + 1).padStart(2, "0")}/${d.getFullYear()}`, energy_kwh: Math.round(energy), energy_vnd: Math.round(energy * 4180), subscription_vnd: trucks * sub.agreed_price_vnd_truck_month, status: orgId === "org-dn" && m === 2 ? "OVERDUE" : m === 1 ? "ISSUED" : "PAID", lookup_code: int(36 ** 7, 36 ** 8 - 1).toString(36).toUpperCase(), issued_at: iso(d.getTime() + 33 * DAY) });
    }
  });
  invoices.forEach((inv) => { inv.vat_vnd = Math.round((inv.energy_vnd + inv.subscription_vnd) * 0.08); inv.total_vnd = inv.energy_vnd + inv.subscription_vnd + inv.vat_vnd; });

  /* ---------- Audit log (ACC-18) ---------- */
  const access_audit_logs = [];
  const AUDIT_ACTORS = [people.head, people.coadmin, people.care, people.ops, people.tcxFm, people.tcxAdmin, people.mnAdmin];
  for (let k = 0; k < 60; k++) {
    const actor = pick(AUDIT_ACTORS);
    const action = pick(["VIEW", "VIEW", "VIEW", "VIEW", "EXPORT", "LOGIN_SUCCESS", "LOGIN_SUCCESS", "LOGIN_FAILED", "LOGOUT", "PASSWORD_CHANGED"]);
    const v = pick(vehicles);
    access_audit_logs.push({
      access_audit_log_id: 520000 + k,
      user_id: actor.user.user_id,
      organization_id: actor.membership.organization_id,
      action,
      data_type: ["VIEW", "EXPORT"].includes(action) ? pick(["LOCATION_HISTORY", "LIVE_POSITION", "DRIVER_PROFILE", "DRIVING_SESSIONS", "CAMERA_FOOTAGE"]) : "ACCOUNT",
      subject: ["VIEW", "EXPORT"].includes(action) ? v.license_plate : null,
      reason: action === "EXPORT" ? pick(["SC-2304 khiếu nại tai nạn", "Báo cáo tháng gửi khách hàng", "Yêu cầu của CSGT số 118/CV"]) : null,
      ip_address: `113.161.${int(1, 254)}.${int(1, 254)}`,
      device: pick(["Chrome 129 / Windows", "Edge 129 / Windows", "Safari 18 / macOS", "G3 Driver 1.4 / Android 14"]),
      occurred_at: ago(int(2, 60 * 24 * 10) * MIN),
    });
  }
  access_audit_logs.sort((a, b) => (a.occurred_at < b.occurred_at ? 1 : -1));

  /* ---------- Login sessions of the current user (ACC-04) ---------- */
  const login_sessions = [
    { session_id: "lgn-1", platform: "WEB", device: "Chrome 129 / Windows 11", ip_address: "113.161.72.18", created_at: ago(2 * HOUR), last_active_at: ago(1 * MIN), current: true },
    { session_id: "lgn-2", platform: "ANDROID", device: "Samsung Galaxy A55 – G3 Fleet 1.4", ip_address: "27.72.101.9", created_at: ago(3 * DAY), last_active_at: ago(40 * MIN), current: false },
    { session_id: "lgn-3", platform: "WEB", device: "Safari 18 / macOS", ip_address: "14.161.20.77", created_at: ago(9 * DAY), last_active_at: ago(2 * DAY), current: false },
  ];

  /* ---------- System health (PLT-03) ---------- */
  const system_processes = [
    { name: "api", label: "API (FastAPI)", make: "make backend-dev", status: "HEALTHY", uptime_hours: 312, metric: "p95 84 ms", detail: "1.240 req/phút" },
    { name: "telemetry-ingestion", label: "Telemetry ingestion (MQTT)", make: "make telemetry-dev", status: "HEALTHY", uptime_hours: 312, metric: "độ trễ p95 3,8 s", detail: "processed 2.410/phút · skipped 3 · errors 0" },
    { name: "telematics-status", label: "T-Box status ingestion", make: "make telematics-status-dev", status: "HEALTHY", uptime_hours: 140, metric: "48 báo cáo/phút", detail: "g3network/telematics/+/status" },
    { name: "ocpp-gateway", label: "OCPP gateway (1.6J + 2.0.1)", make: "make charging-ocpp-dev", status: "ATTENTION", uptime_hours: 18, metric: "19/21 trụ kết nối", detail: "G3-VSI-03 mất kết nối 4 giờ" },
    { name: "telematics-monitor", label: "Device silence monitor", make: "make telematics-monitor-dev", status: "HEALTHY", uptime_hours: 312, metric: "vòng 60 s", detail: "2 thiết bị im lặng" },
    { name: "driving-sessions-autoend", label: "Driving-session auto-end", make: "make driving-sessions-autoend-dev", status: "HEALTHY", uptime_hours: 312, metric: "vòng 300 s", detail: "kết thúc 6 phiên hôm nay" },
    { name: "db", label: "PostgreSQL + TimescaleDB", make: "make infra-up", status: "HEALTHY", uptime_hours: 1820, metric: "41 GB", detail: "chunk telemetry 1 ngày" },
    { name: "broker", label: "EMQX broker", make: "make infra-up", status: "HEALTHY", uptime_hours: 1820, metric: "29 client", detail: "QoS 0" },
  ];

  /* ---------- Generated series ---------- */
  function seededSeries(key, length, base, spread) {
    let h = 0;
    for (const ch of key) h = (h * 31 + ch.charCodeAt(0)) | 0;
    const out = [];
    let v = base;
    for (let i = 0; i < length; i++) {
      h = (h * 1103515245 + 12345) | 0;
      const r = ((h >>> 8) & 0xffff) / 0xffff;
      v = Math.max(0, base + (v - base) * 0.6 + (r - 0.5) * spread);
      out.push(v);
    }
    return out;
  }

  /** Daily operating rows for one truck (MON-14): km, kWh, kWh/km, cost. */
  function dailyReport(vehicleId, days = 30) {
    const v = vehicles.find((x) => x.vehicle_id === vehicleId);
    const model = vehicle_models.find((m) => m.vehicle_model_id === v.vehicle_model_id);
    const kmSeries = seededSeries(vehicleId + "km", days, 210, 160);
    return kmSeries.map((km, i) => {
      const date = new Date(NOW - (days - 1 - i) * DAY);
      const isIdle = v.status === "INACTIVE" || (date.getDay() === 0 && i % 2 === 0);
      const distance = isIdle ? 0 : Math.round(km);
      const rate = (model.consumption_empty_kwh_km + model.consumption_loaded_kwh_km) / 2 * (0.92 + ((i * 7) % 10) / 60);
      const kwh = Math.round(distance * rate);
      return { date: date.toISOString().slice(0, 10), distance_km: distance, energy_kwh: kwh, kwh_per_km: distance ? Math.round((kwh / distance) * 100) / 100 : null, cost_vnd: Math.round(kwh * 4180), driving_hours: Math.round(distance / 38 * 10) / 10 };
    });
  }

  /** Battery % over the last `hours` hours (MON-02 chart). */
  function socSeries(vehicleId, hours = 24) {
    const v = vehicles.find((x) => x.vehicle_id === vehicleId);
    const out = [];
    let soc = v && v.live ? v.live.soc_percent : 60;
    for (let i = 0; i < hours * 4; i++) {
      out.unshift({ at: iso(NOW - i * 15 * MIN), soc_percent: Math.round(soc) });
      const charging = (i > 20 && i < 30) || (i > 60 && i < 66);
      soc = charging ? soc - 6 : Math.min(100, soc + between(0.3, 1.6));
      soc = Math.max(5, Math.min(100, soc));
    }
    return out;
  }

  /** Daily SOH for the last `days` days (MON-07). */
  function sohSeries(vehicleId, days = 180) {
    const v = vehicles.find((x) => x.vehicle_id === vehicleId);
    const end = v.soh_percent;
    const start = Math.min(100, end + days / 180 * (100 - end) * 0.55 + 0.4);
    return Array.from({ length: days }, (_, i) => ({ date: iso(NOW - (days - 1 - i) * DAY).slice(0, 10), soh_percent: Math.round((start - (start - end) * (i / (days - 1)) + Math.sin(i / 7) * 0.12) * 100) / 100 }));
  }

  /** Per-cell voltage and SOH (MON-08). */
  function cellSnapshot(vehicleId) {
    const v = vehicles.find((x) => x.vehicle_id === vehicleId);
    const battery = batteries.find((b) => b.battery_id === v.battery_id);
    if (!battery) return []; // no pack fitted
    const model = battery_models.find((m) => m.battery_model_id === battery.battery_model_id);
    const cells = Math.min(model.cells, 192);
    const vals = seededSeries(vehicleId + "cells", cells, 3.29, 0.03);
    const weak = vehicleId === "veh-022" ? [17, 18, 143] : vehicleId === "veh-013" ? [88] : [];
    return vals.map((voltage, i) => ({ cell: i + 1, voltage_v: Math.round((weak.includes(i) ? voltage - 0.11 : voltage) * 1000) / 1000, soh_percent: Math.round((weak.includes(i) ? v.soh_percent - 9 : v.soh_percent + ((i * 13) % 7) / 10 - 0.3) * 10) / 10, temperature_celsius: Math.round((v.live ? v.live.battery_temperature_celsius : 28) + ((i * 7) % 5) - 2) }));
  }

  /** Route points of a truck over the last hours (MON-10 replay). */
  function routeHistory(vehicleId, hours = 8) {
    const v = vehicles.find((x) => x.vehicle_id === vehicleId);
    if (!v || !v.live) return [];
    const road = ROADS[v.live.road_index];
    const points = [];
    const steps = hours * 12;
    const endF = v.live.road_f;
    // Come from the far side when the truck sits near the start of its road, so the replay always covers distance.
    const startF = endF >= 0.35 ? Math.max(0, endF - 0.7) : Math.min(1, endF + 0.7);
    for (let i = 0; i <= steps; i++) {
      const f = startF + (endF - startF) * (i / steps);
      const p = pointOnRoad(road, f);
      points.push({ at: iso(NOW - (steps - i) * 5 * MIN), lat: p.lat, lon: p.lon, speed_kmh: i % 17 < 2 ? 0 : 30 + ((i * 13) % 35), soc_percent: Math.max(10, Math.round(v.live.soc_percent + (steps - i) * 0.35)) });
    }
    return points;
  }

  /** Hourly kWh delivered by a location over the last `days` days (STN-14). */
  function stationEnergy(locationId, days = 14) {
    const sessions = charging_sessions.filter((s) => s.location_id === locationId && s.status === "COMPLETED");
    const perDay = Array.from({ length: days }, (_, i) => ({ date: iso(NOW - (days - 1 - i) * DAY).slice(0, 10), energy_kwh: 0, sessions: 0 }));
    sessions.forEach((s) => {
      const day = perDay.find((d) => d.date === s.started_at.slice(0, 10));
      if (day) { day.energy_kwh += s.energy_kwh; day.sessions += 1; }
    });
    const hourly = Array.from({ length: 24 }, (_, h) => ({ hour: h, energy_kwh: 0 }));
    sessions.forEach((s) => { hourly[new Date(s.started_at).getHours()].energy_kwh += s.energy_kwh; });
    return { perDay, hourly };
  }

  /** CO2 figures per organization and month (CRB-07, CRB-08). Grid factor 0.6592 tCO2e/MWh (MONRE 2023, illustrative). */
  const GRID_FACTOR_T_PER_MWH = 0.6592;
  const DIESEL_KG_PER_KM = 0.92;
  function carbonMonthly(orgId, months = 6) {
    const trucks = vehicles.filter((v) => (!orgId || v.organization_id === orgId) && v.live && v.organization_id !== "org-g3").length;
    // Deterministic per organization and month: the page may call this many times.
    const noise = seededSeries("carbon" + (orgId || "all"), months * 3, 0.5, 1).map((v) => Math.min(1, Math.max(0, v)));
    return Array.from({ length: months }, (_, i) => {
      const d = new Date(NOW); d.setDate(1); d.setMonth(d.getMonth() - (months - 1 - i));
      const km = Math.round(trucks * (4800 + 1300 * noise[i * 3]));
      const kwh = Math.round(km * (1.12 + 0.12 * noise[i * 3 + 1]));
      const renewableShare = 0.14 + 0.17 * noise[i * 3 + 2];
      const gridT = (kwh * (1 - renewableShare) / 1000) * GRID_FACTOR_T_PER_MWH;
      const renewT = (kwh * renewableShare / 1000) * 0.041;
      const dieselT = (km * DIESEL_KG_PER_KM) / 1000;
      return { month: `${String(d.getMonth() + 1).padStart(2, "0")}/${d.getFullYear()}`, distance_km: km, energy_kwh: kwh, renewable_share: renewableShare, scope2_grid_t: Math.round(gridT * 10) / 10, scope2_renewable_t: Math.round(renewT * 10) / 10, diesel_baseline_t: Math.round(dieselT * 10) / 10, avoided_t: Math.round((dieselT - gridT - renewT) * 10) / 10 };
    });
  }

  /* ---------- Public contract ---------- */
  const db = {
    NOW, MIN, HOUR, DAY, PLACES, ROADS,
    organizations, users, memberships, people, fleets,
    vehicle_models, battery_models, vehicles, telematics, batteries,
    drivers, driving_sessions, trips,
    charging_locations, charging_stations, charging_sessions, station_commands, tariffs,
    geofences, notifications, support_cases, partner_profiles,
    warranties, charging_policies, compliance,
    catalog_features, plans, subscriptions, wallets, wallet_transactions, invoices,
    access_audit_logs, login_sessions, system_processes,
    GRID_FACTOR_T_PER_MWH, DIESEL_KG_PER_KM,
    dailyReport, socSeries, sohSeries, cellSnapshot, routeHistory, stationEnergy, carbonMonthly, pointOnRoad, priceAt,
  };

  const KEYS = {
    organizations: "organization_id", users: "user_id", memberships: "membership_id", fleets: "fleet_id",
    vehicle_models: "vehicle_model_id", battery_models: "battery_model_id", vehicles: "vehicle_id",
    telematics: "telematic_id", batteries: "battery_id", drivers: "driver_id", trips: "trip_id",
    charging_locations: "location_id", charging_stations: "station_id", charging_sessions: "session_id",
    geofences: "geofence_id", notifications: "notification_id", support_cases: "support_case_id",
    warranties: "warranty_id", plans: "plan_id", subscriptions: "subscription_id", wallets: "wallet_id", invoices: "invoice_id",
  };
  /** One record by its key, or undefined. */
  db.find = function (collection, id) {
    const key = KEYS[collection];
    return db[collection].find((r) => r[key] === id);
  };
  db.org = (id) => db.find("organizations", id);
  db.vehicle = (id) => db.find("vehicles", id);
  db.model = (id) => db.find("vehicle_models", id);
  db.driver = (id) => db.find("drivers", id);
  db.user = (id) => db.find("users", id);
  db.fleet = (id) => db.find("fleets", id);
  db.location = (id) => db.find("charging_locations", id);
  db.station = (id) => db.find("charging_stations", id);
  db.telematic = (id) => db.find("telematics", id);
  db.battery = (id) => db.find("batteries", id);
  /** The fleet and every fleet below it (FLT-01 roll-up, FLT-03 scope). */
  db.fleetAndDescendants = function (fleetId) {
    const out = [fleetId];
    for (let i = 0; i < out.length; i++) fleets.filter((f) => f.parent_fleet_id === out[i]).forEach((f) => out.push(f.fleet_id));
    return out;
  };
  db.uid = uid;

  G3.db = db;
})();
