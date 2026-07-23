"""FastAPI application entry point."""

from fastapi import FastAPI

app = FastAPI(
    title="G3Network Backend",
    description="Backend for G3Network - Electric truck driver support system",
    version="0.1.0",
)


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint."""
    return {"status": "healthy", "version": "0.1.0"}
