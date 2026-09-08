"""FastAPI application exposing the platform over HTTP and a browser UI."""

from dataforge.web.app import create_app

__all__ = ["create_app"]
