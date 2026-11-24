from .core import app  # noqa: F401  (route modules register themselves on import)
from . import routes_account, routes_apps, routes_console, routes_jobs, routes_profile  # noqa: F401,E402
