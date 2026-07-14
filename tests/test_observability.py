import logging

import backend.observability as obs


def test_get_logger_returns_named_logger():
    log = obs.get_logger("gtm.test")
    assert isinstance(log, logging.Logger)
    assert log.name == "gtm.test"


def test_configure_logging_adds_one_handler_and_sets_level(monkeypatch):
    log = logging.getLogger("gtm")
    log.handlers.clear()
    monkeypatch.setattr(obs, "_CONFIGURED", False)

    obs.configure_logging(level="DEBUG")
    obs.configure_logging()  # idempotent
    obs.configure_logging()

    assert len(log.handlers) == 1  # not duplicated on re-entry
    assert log.level == logging.DEBUG  # LOG_LEVEL respected
    assert log.propagate is False  # doesn't double-emit through root/Streamlit


def test_configure_logging_independent_of_root_handlers(monkeypatch):
    # Even if the root logger is already configured (Streamlit-like), our gtm
    # logger must still get its own handler/level (basicConfig would no-op here).
    logging.getLogger().addHandler(logging.NullHandler())
    log = logging.getLogger("gtm")
    log.handlers.clear()
    monkeypatch.setattr(obs, "_CONFIGURED", False)

    obs.configure_logging(level="INFO")
    assert log.handlers and log.level == logging.INFO
