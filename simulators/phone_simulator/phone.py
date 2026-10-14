"""The driver app's logic: what each screen asks the backend.

One phone, one logged-in driver at a time. Every screen is a real call to
the backend's API, the one the mobile app will use (CHG-01 to CHG-04,
PAY-05/06 wallet):

* log in (driver accounts only: the scan needs the ``DRIVER`` role);
* home: wallet balance, recent charges, a charge still running;
* scan: ``POST /charging-sessions/scan`` with the charger code read from its
  QR code and the gun; the answer freezes the price;
* charge: the session (``PENDING`` -> ``ACTIVE`` -> ``COMPLETED`` /
  ``ABANDONED``) with live energy, power and SoC from its measurements;
* stop: ``POST /charging-sessions/{id}/stop``;
* receipt: ``/charging-sessions/{id}/receipt`` (energy, time, price, VAT);
* wallet: balance and statement; top-up: a VietQR code to pay from a bank
  app.

**The simulated bank.** A real top-up is paid in a bank app, and the bank
notifies the backend. Here the "bank" confirms the transfer through the
backend's development endpoint ``POST /payments/vietqr/simulate``, which only
an internal admin may call; its login is the ``PHONE_BANK_ADMIN_*`` settings,
kept apart from the driver's session.
"""

from __future__ import annotations

import threading
from typing import Any

from api_client import ApiError, ApiSession
from settings import PhoneSettings

# The measurands the charging screen shows, latest value each.
LIVE_MEASURANDS = ("Energy.Active.Import.Register", "Power.Active.Import", "SoC")

# How many charges the home screen lists, and statement lines the wallet shows.
RECENT_CHARGES = 5
STATEMENT_LINES = 20


class NotDriverError(Exception):
    """The account that logged in cannot charge (it has no DRIVER role)."""


class PhoneApp:
    """The app on one phone: the driver's session and the screens' calls.

    Attributes:
        settings: The phone's settings.
    """

    def __init__(self, settings: PhoneSettings) -> None:
        """Start logged out.

        Args:
            settings: The phone's settings.
        """
        self.settings = settings
        self._driver = ApiSession(settings.api_url, "ANDROID")
        self._bank = ApiSession(settings.api_url, "WEB")
        self._profile: dict[str, Any] | None = None
        # The price frozen by the last scan, per session (the receipt has it
        # once billed; the running cost needs it before).
        self._quotes: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    # --- account ---------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        """Return what the page needs to start: logged in or not, and who.

        Returns:
            Login state, the profile, the login to prefill and whether the
            simulated bank is available.
        """
        with self._lock:
            profile = self._profile
        return {
            "logged_in": self._driver.is_logged_in and profile is not None,
            "profile": profile,
            "prefill_phone": self.settings.login_phone,
            "prefill_password": self.settings.login_password,
            "bank_enabled": self.settings.bank_enabled,
            "api_url": self.settings.api_url,
        }

    def login(self, phone_number: str, password: str) -> dict[str, Any]:
        """Log in as a driver.

        Args:
            phone_number: The login phone number.
            password: The password.

        Returns:
            The profile: name, phone and roles.

        Raises:
            ApiError: The backend refused the login.
            NotDriverError: The account has no DRIVER role.
        """
        self._driver.login(phone_number, password)
        me = self._driver.get("/api/v1/auth/me")
        roles = me.get("roles", [])
        if "DRIVER" not in roles:
            self._driver.logout()
            raise NotDriverError("this account cannot charge: it has no DRIVER role")
        profile = {
            "full_name": me["user"].get("full_name"),
            "phone_number": me["user"].get("phone_number"),
            "roles": roles,
        }
        with self._lock:
            self._profile = profile
        return profile

    def logout(self) -> dict[str, str]:
        """Log out and forget the driver.

        Returns:
            A message for the page.
        """
        self._driver.logout()
        with self._lock:
            self._profile = None
            self._quotes.clear()
        return {"message": "logged out"}

    # --- home and charging ---------------------------------------------------------

    def home(self) -> dict[str, Any]:
        """Return the home screen: wallet, recent charges, a running charge.

        Returns:
            ``wallet``, ``recent`` (newest first) and ``open_charge`` (a
            ``PENDING`` or ``ACTIVE`` session, or ``None``).
        """
        wallet = self._driver.get("/api/v1/wallets/me")
        page = self._driver.get(
            "/api/v1/charging-sessions/mine", {"page": 1, "page_size": RECENT_CHARGES}
        )
        recent = page.get("items", [])
        open_charge = next(
            (item for item in recent if item["status"] in ("PENDING", "ACTIVE")), None
        )
        return {"wallet": wallet, "recent": recent, "open_charge": open_charge}

    def scan(self, charger_code: str, gun: int | None) -> dict[str, Any]:
        """Scan a charger's QR code: the backend asks the charger to start.

        Args:
            charger_code: What the QR code holds (the charger's code).
            gun: The gun chosen on the phone; ``None`` = any gun.

        Returns:
            The session (``PENDING``), when the scan expires, and the frozen
            price.

        Raises:
            ApiError: The backend refused the scan (offline charger, busy gun,
                low balance, no tariff...).
        """
        answer = self._driver.post(
            "/api/v1/charging-sessions/scan",
            {"charger_code": charger_code, "connector_number": gun},
        )
        quote = {
            "price_per_kwh": answer.get("price_per_kwh"),
            "vat_rate_percent": answer.get("vat_rate_percent"),
            "currency": answer.get("currency"),
            "charger_code": charger_code,
            "gun": gun,
        }
        with self._lock:
            self._quotes[answer["session_id"]] = quote
        return {
            "session_id": answer["session_id"],
            "status": answer["status"],
            "expires_at": answer.get("expires_at"),
            "quote": quote,
        }

    def charge(self, session_id: str) -> dict[str, Any]:
        """Return a charge as the charging screen shows it.

        Reads the session and, while it runs, the latest energy, power and
        SoC readings (two calls per measurand: the total, then the last
        page, since readings are listed oldest first).

        Args:
            session_id: The session.

        Returns:
            Status, times, stop reason, the live readings, energy so far and
            the running cost (from the price frozen at the scan).
        """
        session = self._driver.get(f"/api/v1/charging-sessions/{session_id}")
        live: dict[str, float] = {}
        reading_at: str | None = None
        if session["status"] in ("ACTIVE", "COMPLETED"):
            for measurand in LIVE_MEASURANDS:
                reading = self._latest_reading(session_id, measurand)
                if reading is not None:
                    live[measurand] = float(reading["value"])
                    reading_at = max(reading_at or "", str(reading["sampled_at"]))
        energy_wh = None
        register = live.get("Energy.Active.Import.Register")
        if session.get("energy_delivered_wh") is not None:
            energy_wh = float(session["energy_delivered_wh"])
        elif register is not None and session.get("meter_start_wh") is not None:
            energy_wh = register - float(session["meter_start_wh"])
        with self._lock:
            quote = self._quotes.get(session_id)
        cost = None
        if quote and quote.get("price_per_kwh") is not None and energy_wh is not None:
            vat = float(quote.get("vat_rate_percent") or 0)
            cost = round(energy_wh / 1000 * quote["price_per_kwh"] * (1 + vat / 100))
        return {
            "session_id": session_id,
            "status": session["status"],
            "started_at": session.get("started_at"),
            "ended_at": session.get("ended_at"),
            "stop_reason": session.get("stop_reason"),
            "energy_kwh": None if energy_wh is None else round(energy_wh / 1000, 3),
            "power_kw": None
            if "Power.Active.Import" not in live
            else round(live["Power.Active.Import"] / 1000, 1),
            "soc": live.get("SoC"),
            "estimated_cost": cost,
            "reading_at": reading_at,
            "quote": quote,
        }

    def _latest_reading(self, session_id: str, measurand: str) -> dict[str, Any] | None:
        """Return the newest reading of one measurand of a session.

        Args:
            session_id: The session.
            measurand: The OCPP measurand.

        Returns:
            The reading (``value``, ``sampled_at``...), or ``None`` if there
            is none yet.
        """
        path = f"/api/v1/charging-sessions/{session_id}/measurements"
        first = self._driver.get(
            path, {"measurand": measurand, "page": 1, "page_size": 1}
        )
        total = int(first.get("total", 0))
        if total == 0:
            return None
        last = (
            first
            if total == 1
            else self._driver.get(
                path, {"measurand": measurand, "page": total, "page_size": 1}
            )
        )
        items: list[dict[str, Any]] = last.get("items", [])
        return items[-1] if items else None

    def stop(self, session_id: str) -> dict[str, Any]:
        """Stop a charge from the app.

        Args:
            session_id: The session.

        Returns:
            The backend's answer (the session stays ``ACTIVE`` until the
            charger confirms the stop).
        """
        answer: dict[str, Any] = self._driver.post(
            f"/api/v1/charging-sessions/{session_id}/stop", {}
        )
        return answer

    def receipt(self, session_id: str) -> dict[str, Any]:
        """Return a finished charge's receipt.

        Args:
            session_id: The session.

        Returns:
            The backend's ``ChargingSessionReceiptResponse``.
        """
        answer: dict[str, Any] = self._driver.get(
            f"/api/v1/charging-sessions/{session_id}/receipt"
        )
        return answer

    # --- wallet ----------------------------------------------------------------------

    def wallet(self) -> dict[str, Any]:
        """Return the wallet and its latest statement lines.

        Returns:
            ``wallet`` and ``statement`` (newest first).
        """
        wallet = self._driver.get("/api/v1/wallets/me")
        statement = self._driver.get(
            "/api/v1/wallets/me/transactions", {"page": 1, "page_size": STATEMENT_LINES}
        )
        return {"wallet": wallet, "statement": statement.get("items", [])}

    def top_up(self, amount: int) -> dict[str, Any]:
        """Ask for a VietQR code to top up the wallet.

        Args:
            amount: Amount in whole dong.

        Returns:
            The payment: transfer code, VietQR payload, bank account, amount,
            expiry.
        """
        answer: dict[str, Any] = self._driver.post(
            "/api/v1/wallets/me/top-ups", {"amount": amount}
        )
        return answer

    def bank_pay(self, transfer_code: str) -> dict[str, Any]:
        """Pay a top-up from the simulated bank app.

        Logs the bank in on first use, as the configured internal admin, and
        confirms the transfer through the backend's development endpoint.

        Args:
            transfer_code: The top-up's transfer code.

        Returns:
            The backend's answer to the simulated bank notification.

        Raises:
            ApiError: No bank login configured, the admin login failed, or
                the backend refused the transfer (unknown code, simulation
                switched off).
        """
        if not self.settings.bank_enabled:
            raise ApiError(400, "the simulated bank is not set up (PHONE_BANK_ADMIN_*)")
        if not self._bank.is_logged_in:
            self._bank.login(
                self.settings.bank_admin_phone, self.settings.bank_admin_password
            )
        answer: dict[str, Any] = self._bank.post(
            "/api/v1/payments/vietqr/simulate", {"transfer_code": transfer_code}
        )
        return answer
