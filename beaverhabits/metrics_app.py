"""Internal metrics server - runs on 127.0.0.1:9090 only.

This module provides a minimal FastAPI app that only exposes /metrics and /debug/*
endpoints for internal monitoring. It should be run as a separate gunicorn worker
on the internal network interface.

Usage:
    gunicorn -w 1 -b 127.0.0.1:9090 beaverhabits.metrics_app:app
"""

import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from beaverhabits.app.db import create_db_and_tables
from beaverhabits.configs import settings
from beaverhabits.logger import logger
from beaverhabits.routes.metrics import init_metrics_routes


@asynccontextmanager
async def metrics_lifespan(_: FastAPI):
    # Create database tables (needed for some debug endpoints)
    await create_db_and_tables()
    yield


app = FastAPI(lifespan=metrics_lifespan, title="BeaverHabits Internal Metrics")

# Only expose metrics and debug endpoints
init_metrics_routes(app)


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting internal metrics server on 127.0.0.1:9090")
    uvicorn.run(app=app, host="127.0.0.1", port=9090, workers=1)