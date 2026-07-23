-- Enable TimescaleDB extension (if available)
-- Note: TimescaleDB needs to be installed in the PostgreSQL container
-- For development, we'll create the extension if it exists, otherwise skip
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb') THEN
        CREATE EXTENSION IF NOT EXISTS timescaledb;
    END IF;
END $$;

-- Enable PostGIS extension for geospatial data
CREATE EXTENSION IF NOT EXISTS postgis;

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
