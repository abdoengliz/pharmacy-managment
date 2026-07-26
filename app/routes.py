"""Compatibility entry point for all application routes.

The original monolithic file was split by business area. Importing this module
continues to register exactly the same Flask endpoints.
"""
from __future__ import annotations

from .routes_modules import auth_dashboard  # noqa: F401
from .routes_modules import organization  # noqa: F401
from .routes_modules import finance  # noqa: F401
from .routes_modules import suppliers  # noqa: F401
from .routes_modules import hr  # noqa: F401
from .routes_modules import inventory  # noqa: F401
from .routes_modules import users_access  # noqa: F401
from .routes_modules import workflow  # noqa: F401
from .routes_modules import reports  # noqa: F401
from .routes_modules import system  # noqa: F401
from .routes_modules import sales  # noqa: F401
from .routes_modules import operations  # noqa: F401
