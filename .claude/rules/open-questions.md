# Open questions / decisions still needing confirmation

> Read this file when touching one of the undecided areas below, to see what
> the current interim proposal is.

1. **CI/CD**: which platform to use (GitHub Actions, GitLab CI, Jenkins...)? Not yet decided. Interim decision (2026-10-01): local pre-commit hook (`make check`) only; remote is GitHub, so GitHub Actions is the natural candidate when CI is set up.
2. **Secrets management for production** (Vault, AWS/GCP Secrets Manager, or just `.env` + CI secrets)? Not yet decided.
3. **Local connection protocol** between the Telematics device and the vehicle screen (BLE/Wi-Fi Direct/CAN...) — out of current scope since `vehicle-app/` has no source yet; must be finalized before starting to code the `core/` module of the vehicle app.
4. **Willdigits charger — information required from the vendor** (OCPP 1.6J integration). Not yet answered; interim proposal: build against the OCPP 1.6J standard and the real logs (raw message log + post-boot `GetConfiguration`), assuming no vendor-specific behaviour. Blocking requests: (a) OCPP Implementation Guide / Interface Document; (b) mapping of the 80 internal error codes to `vendorErrorCode` (CSV/JSON); (c) DC meter brand/model/accuracy class/tamper seal; (d) confirmation of `wss://` and the authentication mechanism (Basic Auth or client certificate). Other: written list of supported OCPP profiles; multi-level HMI permissions; whether VIN Autocharge runs over OCPP; offline buffer capacity; whether `DataTransfer` is used. Details: `docs/03-specifications/charging-station-specification-summary.md` §6.2.
5. **Non-software blockers for the charger project**: (a) truck inlet standard — CCS2 or GB/T for Tri-Ring EVT-262/400/825; (b) DC meter verification in Vietnam (decides whether kWh invoicing is legal, or a service-fee model is needed); (c) warranty and technical-support provider in Vietnam. Interim proposal: nothing in code; the backend stores meter readings as reported and does not claim they are legally verified.

For anything not yet decided, keep the interim proposal above and implement accordingly — changing it later won't significantly affect the directory structure.
