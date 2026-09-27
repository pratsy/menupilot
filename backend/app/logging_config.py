import logging
import os


def configure_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Chroma's telemetry client is noisy and occasionally errors on this version's
    # posthog signature mismatch; neither is actionable for us.
    logging.getLogger("chromadb.telemetry").setLevel(logging.ERROR)
    logging.getLogger("httpx").setLevel(logging.WARNING)
