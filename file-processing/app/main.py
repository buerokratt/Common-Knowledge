from fastapi import FastAPI
from contextlib import asynccontextmanager
import asyncio
import logging
import sys
from app.api import api_router
from app.services import upload_service

logger = logging.getLogger(__name__)


async def cleanup_background_task():
    """Background task to periodically clean up old tasks."""
    while True:
        try:
            # Clean up tasks older than 24 hours every hour
            await asyncio.sleep(3600)  # 1 hour
            upload_service.cleanup_old_tasks(max_age_hours=24)
        except Exception as e:
            logger.error(f"Error in cleanup task: {str(e)}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s - %(asctime)s - %(name)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler("app.log")],
    )

    # Startup
    logger.info("Starting File Processing API")
    
    # Start cleanup task (optional)
    cleanup_task = asyncio.create_task(cleanup_background_task())
    
    yield
    
    # Shutdown
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    
    logger.info("File Processing API shutdown complete")


app = FastAPI(
    title="File Processing API", 
    version="1.0.0",
    lifespan=lifespan
)

app.include_router(api_router, prefix="/api/v1")