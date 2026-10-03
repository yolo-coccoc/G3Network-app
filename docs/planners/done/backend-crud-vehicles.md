# Planner: Backend CRUD Vehicles (F-F2)

> Feature code: F-F2 (Device provisioning)
> Status: ✅ Done (MVP scope; closed 2026-10-04 when moved to `done/`).
> Everything left over is deferred in `docs/decisions/deferred.md`; unticked
> acceptance items below were never re-verified. Status before closing:
>
> 🚧 Source implemented; integration acceptance and automated regression
> tests are still tracked in [`backend-automated-tests.md`](./backend-automated-tests.md)
> Created: 2026-07-23

---

## Overview

The status above reflects that the source exists, not that the entire
environment/database/manual test checklist below has been completed. Items
that could not yet be run on the current environment are kept as `[ ]` so no
result is falsely recorded.

Build a backend API for vehicle management with basic CRUD operations:
- Create: Create a new vehicle
- Read: View the vehicle list, view details of one vehicle
- Update: Update vehicle information
- Delete: Delete a vehicle (soft delete)

**Scope:**
- Backend only (FastAPI)
- Test via Swagger UI
- Not included: frontend, telematics device assignment, integration with other domains

---

## Architecture per CLAUDE.md

```
backend/
├── app/                        # Main source code (uv package layout)
│   ├── domains/
│   │   └── vehicles/
│   │       ├── router.py      # FastAPI endpoints
│   │       ├── service.py     # Business logic
│   │       ├── repository.py  # Database queries
│   │       ├── schemas.py     # Pydantic models
│   │       └── models.py      # SQLAlchemy models
│   ├── api/
│   │   └── main.py           # Mount router
│   └── libs/
│       └── db/
│           └── base.py        # SQLAlchemy base
├── pyproject.toml
└── uv.lock
```

---

## Implementation step list

### Step 0: Initialize database infrastructure

**Goal:** Create a PostgreSQL container with TimescaleDB + PostGIS extensions

**Prompt:**
```
Create the database infrastructure in the infra/ directory:
1. Create docker-compose.yml with a db service (PostgreSQL 16)
2. Create a db/init/ directory with a script that enables extensions (TimescaleDB, PostGIS, uuid-ossp)
3. Create .env.example with POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
4. Update backend/.env.example with DATABASE_URL matching infra/.env.example
```

**Important notes:**
- The `postgres:16` image does **not** come with TimescaleDB and PostGIS by default
- For basic development (vehicles CRUD), this image is sufficient
- When TimescaleDB/PostGIS is needed (telemetry, charging_stations,
  charging_sessions domains), switch to:
  - `timescale/timescaledb-ha:pg16` (has TimescaleDB)
  - `postgis/postgis:16-3.4` (has PostGIS)
  - Or build a custom image with both extensions

**Commands to run:**
```bash
# Copy .env.example to .env
cp infra/.env.example infra/.env

# Start the container
docker compose -f infra/docker-compose.yml up -d

# Check that the container is running
docker ps

# View logs (if needed)
docker compose -f infra/docker-compose.yml logs -f db

# Stop and remove the container (keep the volume)
docker compose -f infra/docker-compose.yml down

# Stop and remove the container along with the volume (reset the database)
docker compose -f infra/docker-compose.yml down -v
```

**Checks:**
- [ ] The `g3network-db` container is running
- [ ] Port 5432 is accessible
- [ ] Extensions are enabled in the database

---

### Step 1: Set up the backend environment

**Goal:** Initialize the backend project with FastAPI + uv

**Prompt:**
```
Initialize the backend project in the backend/ directory with:
- Python 3.12
- FastAPI
- uv for dependency management (pyproject.toml + uv.lock)
- Directory structure per CLAUDE.md:
  - backend/app/domains/vehicles/
  - backend/app/api/main.py
  - backend/app/libs/db/
- .env.example file with DATABASE_URL
- .gitignore file for Python
```

**Checks:**
- [ ] `uv sync` runs successfully
- [ ] `uv run uvicorn api.main:app --reload` starts the server
- [ ] Accessing `http://localhost:8000/docs` shows the Swagger UI

---

### Step 2: Set up the database connection

**Goal:** Connect to PostgreSQL, create the base model

**Prompt:**
```
Set up the database connection in backend/app/libs/db/:
1. Create base.py with a SQLAlchemy declarative_base
2. Create session management (async session)
3. Configure the database URL from an environment variable
4. Create a get_db dependency function to inject into the router
```

**Checks:**
- [ ] The server starts without errors when DATABASE_URL is valid
- [ ] The connection pool works

---

### Step 3: Create models.py (SQLAlchemy)

**Goal:** Define the `vehicles` table in the database

**Prompt:**
```
Create backend/app/domains/vehicles/models.py with a SQLAlchemy Vehicle model:

vehicles table:
- id: UUID primary key
- plate_number: string, unique, not null (license plate)
- make: string, not null (manufacturer: VinFast, Hyundai...)
- model: string, not null (vehicle model)
- year: integer (manufacture year)
- vin: string, unique (VIN / chassis number)
- battery_capacity_kwh: decimal (battery capacity)
- max_range_km: integer (maximum range on a full battery)
- status: enum (active, inactive, maintenance)
- team_id: UUID foreign key (nullable, for later fleet integration)
- created_at: timestamp
- updated_at: timestamp
- deleted_at: timestamp (nullable, soft delete)

Notes:
- Use async SQLAlchemy
- Import base from libs.db.base
- Add a docstring describing the table
```

**Checks:**
- [ ] The model has no syntax errors
- [ ] Fields have the correct data types
- [ ] Has a complete docstring

---

### Step 4: Create schemas.py (Pydantic)

**Goal:** Define request/response schemas

**Prompt:**
```
Create backend/app/domains/vehicles/schemas.py with Pydantic models:

1. VehicleBase:
   - plate_number: str
   - make: str
   - model: str
   - year: int | None
   - vin: str | None
   - battery_capacity_kwh: Decimal | None
   - max_range_km: int | None
   - status: VehicleStatus (enum)

2. VehicleCreate (extends VehicleBase):
   - team_id: UUID | None

3. VehicleUpdate:
   - All fields optional
   - Do not allow updating plate_number (or allow it if the business requires it)

4. VehicleResponse (extends VehicleBase):
   - id: UUID
   - team_id: UUID | None
   - created_at: datetime
   - updated_at: datetime

5. VehicleListResponse:
   - items: list[VehicleResponse]
   - total: int
   - page: int
   - page_size: int

Notes:
- Use Pydantic v2
- Add examples for each schema
- Add a docstring
```

**Checks:**
- [ ] The schemas have no syntax errors
- [ ] Examples display correctly in Swagger
- [ ] Complete docstrings

---

### Step 5: Create repository.py

**Goal:** Handle database queries

**Prompt:**
```
Create backend/app/domains/vehicles/repository.py with async functions:

1. create_vehicle(db, vehicle_data) -> Vehicle
2. get_vehicle_by_id(db, vehicle_id) -> Vehicle | None
3. get_vehicle_by_plate(db, plate_number) -> Vehicle | None
4. get_vehicles(db, skip, limit, status_filter) -> list[Vehicle]
5. count_vehicles(db, status_filter) -> int
6. update_vehicle(db, vehicle_id, update_data) -> Vehicle | None
7. soft_delete_vehicle(db, vehicle_id) -> Vehicle | None

Notes:
- Use an async session
- Soft delete: set deleted_at instead of actually deleting
- Filter: only fetch records where deleted_at is None
- Add a docstring for each function
```

**Checks:**
- [ ] The functions have no syntax errors
- [ ] Complete type hints
- [ ] Complete docstrings

---

### Step 6: Create service.py

**Goal:** Business logic layer

**Prompt:**
```
Create backend/app/domains/vehicles/service.py with the following functions:

1. create_vehicle(db, vehicle_data) -> VehicleResponse
   - Check whether plate_number already exists
   - If duplicate, raise HTTPException 400

2. get_vehicle(db, vehicle_id) -> VehicleResponse
   - If not found, raise HTTPException 404

3. list_vehicles(db, page, page_size, status) -> VehicleListResponse
   - Validate page, page_size
   - Call the repository to fetch data

4. update_vehicle(db, vehicle_id, update_data) -> VehicleResponse
   - Check that the vehicle exists
   - If updating plate_number, check for duplicates

5. delete_vehicle(db, vehicle_id) -> dict
   - Soft delete
   - Return {"message": "Vehicle deleted successfully"}

Notes:
- All I/O goes through the repository, do NOT query directly in the service
- Handle business exceptions
- Add a docstring
```

**Checks:**
- [ ] The functions have no syntax errors
- [ ] Exception handling is correct
- [ ] Complete docstrings

---

### Step 7: Create router.py

**Goal:** Define API endpoints

**Prompt:**
```
Create backend/app/domains/vehicles/router.py with a FastAPI APIRouter:

Endpoints:
1. POST /vehicles
   - Create a new vehicle
   - Request: VehicleCreate
   - Response: VehicleResponse (201)

2. GET /vehicles
   - Vehicle list (paginated)
   - Query params: page, page_size, status
   - Response: VehicleListResponse (200)

3. GET /vehicles/{vehicle_id}
   - Details of one vehicle
   - Response: VehicleResponse (200)

4. PUT /vehicles/{vehicle_id}
   - Update vehicle information
   - Request: VehicleUpdate
   - Response: VehicleResponse (200)

5. DELETE /vehicles/{vehicle_id}
   - Soft delete a vehicle
   - Response: {"message": "..."} (200)

Notes:
- Use async def
- Inject the db session via Depends(get_db)
- Add tags=["vehicles"] for Swagger grouping
- Add response_model for each endpoint
- Add a docstring for each endpoint
```

**Checks:**
- [ ] The router has no syntax errors
- [ ] Swagger displays the endpoints correctly
- [ ] Docstrings display in Swagger

---

### Step 8: Mount the router into main.py

**Goal:** Connect the router to the main app

**Prompt:**
```
Update backend/app/api/main.py:
1. Import the router from domains.vehicles.router
2. Create the FastAPI app with title, description
3. Include the router with prefix="/api/v1"
4. Add a health check endpoint GET /health
5. Add CORS middleware (allow all origins in dev)
```

**Checks:**
- [ ] The server starts without errors
- [ ] Accessing `/docs` shows all endpoints
- [ ] Health check returns 200

---

### Step 9: Create the Alembic migration

**Goal:** Create a migration to create the vehicles table

**Prompt:**
```
Set up Alembic and create the first migration:
1. Init Alembic in backend/
2. Configure alembic.ini with the database URL from env
3. Configure env.py to support async
4. Import the Vehicle model in env.py
5. Create the migration: alembic revision --autogenerate -m "create vehicles table"
6. Run the migration: alembic upgrade head
```

**Checks:**
- [ ] The migration runs successfully
- [ ] The vehicles table is created in the database
- [ ] Columns have the correct data types

---

### Step 10: Test CRUD via Swagger

**Goal:** Verify that all endpoints work

**Prompt:**
```
No coding needed, just test manually via Swagger UI:

Test case 1: Create a new vehicle
- POST /api/v1/vehicles
- Body: {"plate_number": "51A-12345", "make": "VinFast", "model": "e34", "year": 2024, "status": "active"}
- Check: 201, returns the vehicle with an id

Test case 2: Get the vehicle list
- GET /api/v1/vehicles
- Check: 200, returns a list including the vehicle just created

Test case 3: Get vehicle details
- GET /api/v1/vehicles/{id}
- Check: 200, returns the correct vehicle

Test case 4: Update a vehicle
- PATCH /api/v1/vehicles/{id}
- Body: {"year": 2025}
- Check: 200, year has changed

Test case 5: Delete a vehicle
- DELETE /api/v1/vehicles/{id}
- Check: 200
- Fetch the list again: the vehicle is no longer in the list

Test case 6: Create a vehicle with a duplicate plate number
- POST with a plate_number that already exists
- Check: 400, clear error message
```

**Checks:**
- [ ] All test cases pass
- [ ] Error responses have the correct format
- [ ] Soft delete works

---

### Step 11: Create the accompanying .md file

**Goal:** Documentation describing the vehicles domain

**Prompt:**
```
Create the file backend/app/domains/vehicles/vehicles.md describing:
- The purpose of the vehicles domain
- The endpoints and how to use them
- The fields in the Vehicle model
- Notes on usage (soft delete, validation...)
- A link to feature code F-F2 in the feature list
```

**Checks:**
- [ ] The .md file has complete content
- [ ] Placed in the correct location

---

## Recommended execution order

```
Step 1 → Step 2 → Step 3 → Step 4 → Step 5 → Step 6 → Step 7 → Step 8 → Step 9 → Step 10 → Step 11
```

**Notes:**
- Each step should be done separately, with thorough testing before moving to the next
- If an error occurs, stop and fix it right away
- Commit code after completing each step

---

## Summary checklist

After completing all steps:

- [ ] Backend runs stably on `localhost:8000`
- [ ] Swagger UI shows all endpoints at `/docs`
- [ ] The database has a `vehicles` table with the correct schema
- [ ] CRUD operations work via Swagger
- [ ] Soft delete works correctly
- [ ] Validation (plate_number unique) works
- [ ] Error responses have the correct format
- [ ] Code has complete docstrings
- [ ] The accompanying .md file has been created

---

## Notes

- **Not yet included:** Telematics device assignment, integration with the fleet/teams domain, authentication/authorization
- **Future extensions:** Device assignment API, API to get a vehicle's telemetry, filtering by team_id
