"""Entry point for running labgrid_runner as a module."""
import asyncio
import logging
import os

# Configure standard logging before importing other modules
log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

from .service import main

if __name__ == "__main__":
    asyncio.run(main())
