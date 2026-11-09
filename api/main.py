"""ASGI entry point: ``uvicorn api.main:app``."""

from api.app import create_app
from infrastructure.observability import configure_logging

configure_logging()
app = create_app()
