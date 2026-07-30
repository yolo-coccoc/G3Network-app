# G3Network Backend

Backend for G3Network - Electric truck driver support system.

## Development

```bash
# Install dependencies
uv sync

# Run development server
uv run uvicorn app.api.main:app --reload

# Run with specific host/port
uv run uvicorn app.api.main:app --host 0.0.0.0 --port 8000 --reload
```

## Configuration

Copy `.env.example` to `.env` before running the backend. The shared schema,
types, validation and safe defaults live in
`app/libs/common/config.py`; `.env` only supplies values that vary by runtime
environment, credentials and operational tuning. `DATABASE_URL` is required
and must not be placed in source code.

## API Documentation

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Project Structure

```
backend/
├── app/               # Source code (uv package)
│   ├── domains/      # Business domains (bounded contexts)
│   │   └── vehicles/ # Vehicle management (AD-05)
│   ├── api/          # FastAPI application
│   │   └── main.py   # Entry point
│   └── libs/         # Shared utilities
│       └── db/       # Database configuration
├── pyproject.toml
└── uv.lock
```
