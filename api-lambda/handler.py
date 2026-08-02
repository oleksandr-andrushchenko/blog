"""API Lambda entry point."""

from app import app
from lambda_adapter import make_handler

handler = make_handler(app)
