# data-layer/__init__.py
#
# Marks the data-layer directory as a Python package.
# The data-layer is intentionally a standalone package — it does NOT import
# from the backend (app.*) so it can run on edge devices or CI runners
# without a full backend installation.
