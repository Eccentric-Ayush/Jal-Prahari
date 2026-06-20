# backend/app/api/__init__.py
#
# Marks the `api` directory as a Python package.
# Routers are registered in app/main.py — not imported here — to avoid
# circular imports between the router and the app factory.
