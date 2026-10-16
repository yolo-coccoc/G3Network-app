# Driver app

The G3 Network app for drivers' phones (Android and iOS): log in, check in to
a truck, scan a charger's QR code to charge, follow the charge, pay from the
wallet, SOS. The screens and the API call behind each one are described in
[docs/scenario/mobile-app.md](../docs/scenario/mobile-app.md); the features
are in the [feature catalog](../docs/product/features/README.md) (`app`
surface).

**No source yet.** This folder is reserved for the app (TT-31 in the
[decision log](../docs/decisions/decision-log.md)); its stack is still open
question 12 there (React Native + Expo proposed, Flutter the alternative).
Don't add code or conventions here before that question is answered and a
planner for the app's first task is confirmed.

When it starts, the folder is **self-contained**, like `web-portal/`: its own
toolchain, dependencies and lockfile inside `driver-app/`, and its own typed
API client generated from the backend's OpenAPI document (exported with
`cd backend && uv run python -m app.api.openapi_export <file>`). It imports
nothing from `web-portal/` or any other folder.
