"""Logging configuration (Phase 10).

Configures the app's own ``gtm`` logger namespace directly — level + a dedicated
handler with ``propagate=False`` — rather than ``logging.basicConfig``, which is a
no-op once the root logger already has handlers (as under Streamlit). That keeps
``LOG_LEVEL`` effective regardless of the host, and avoids touching other
libraries' logging or double-emitting through the root handlers.

Library modules use ``logging.getLogger("gtm.<area>")`` and stay quiet until an
entrypoint calls ``configure_logging`` — so imports and the test suite emit no noise.
"""

import logging
from typing import Optional

from backend.config import get_settings

_ROOT_NAME = "gtm"
_FORMAT = "%(asctime)s %(levelname)s %(name)s | %(message)s"
_CONFIGURED = False


def configure_logging(level: Optional[str] = None) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    lvl = (level or get_settings().log_level or "INFO").upper()
    log = logging.getLogger(_ROOT_NAME)
    log.setLevel(getattr(logging, lvl, logging.INFO))
    if not log.handlers:  # add our handler once; don't duplicate on re-entry
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(_FORMAT))
        log.addHandler(handler)
    log.propagate = False  # don't re-emit through root/Streamlit handlers
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Fetch a logger, ensuring logging is configured (for entrypoints/CLIs)."""
    configure_logging()
    return logging.getLogger(name)
