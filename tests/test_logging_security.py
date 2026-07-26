import logging

from app.core.logging import configure_logging


def test_dependency_request_logs_are_not_emitted_at_info_level() -> None:
    configure_logging("INFO", json_output=True)

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
