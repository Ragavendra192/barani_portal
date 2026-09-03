# routes/__init__.py

# This file makes the "routes" folder a Python package.
# Do NOT put route logic here.

from .auth import auth_bp
from .admin import admin_bp
from .dashboard import dashboard_bp

__all__ = [
    "auth_bp",
    "admin_bp",
    "dashboard_bp"
]
