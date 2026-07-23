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
