"""FastAPI web layer — the multi-user frontend/backend seam.

Mirrors the old ``app.py`` (Streamlit) role: UI-facing orchestration only. All
domain logic still lives in ``backend/``; this package adds HTTP, sessions, auth,
and per-user data scoping on top.
"""
