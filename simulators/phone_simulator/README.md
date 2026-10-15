# Phone simulator

How the three simulators fit together, and the test-day checklist: [../README.md](../README.md).

The driver's mobile app, on a page shaped like a phone. It plays one driver's
phone against the backend's HTTP API, the same calls the real app will make:

- **Log in** with a driver account (phone + password).
- **Home**: wallet balance, a charge still running, recent charges.
- **Scan QR to charge**: type or paste what the charger's QR code holds (its
  charger code) and choose the gun; the backend asks the charger to start.
- **Waiting**: plug the gun in (on the charger simulator) before the request
  expires.
- **Charging**: battery %, energy, power, time and cost so far, updated with
  every reading the charger sends; **Stop charging**.
- **Receipt**: place, gun, times, energy, how it ended, price, VAT, total.
- **Wallet**: balance, statement, top-up with a VietQR code, and **Pay with
  bank app (simulated)**: the simulated bank confirms the transfer through the
  backend's development endpoint, logged in as an internal admin.

Sign-up is not on the phone: use an existing driver account. The login tokens
stay in the simulator's own process, never in the browser.

The folder is self-contained: copy it anywhere. It needs Python 3.12 and no
third-party library, and reads only its own `.env`.

## Set up (once)

```bash
cp .env.example .env        # Windows: copy .env.example .env
```

In `.env`, set `PHONE_API_URL` (the backend API), and optionally the driver
login to prefill and the bank's admin login (`PHONE_BANK_ADMIN_*`).

## Run

On a laptop the API is not public: open an SSH tunnel first and keep it open:

```bash
ssh -p 10022 -N -L 8000:127.0.0.1:8000 <user>@<server>
```

Then, in another terminal:

```bash
python3 phone_simulator.py      # Windows: python phone_simulator.py
```

Open `http://127.0.0.1:8093/`. A full charge: plug a gun in on the charger
simulator's page, scan its charger code on the phone, watch the charge on
both (and in the charger monitor), stop it on the phone, read the receipt.
