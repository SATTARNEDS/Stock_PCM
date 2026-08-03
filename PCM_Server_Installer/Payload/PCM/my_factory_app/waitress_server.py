import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from waitress import serve

from app import app


def configure_file_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_log_path = str(log_path.resolve())
    root_logger = logging.getLogger()
    if any(
        isinstance(handler, RotatingFileHandler)
        and handler.baseFilename == resolved_log_path
        for handler in root_logger.handlers
    ):
        return

    file_handler = RotatingFileHandler(
        resolved_log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root_logger.addHandler(file_handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="PCM production server for Windows LAN.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--threads", type=int, default=12)
    arguments = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    configure_file_logging(project_root / "logs" / "web_server.log")
    app.logger.info(
        "Starting Waitress on %s:%s with %s threads",
        arguments.host,
        arguments.port,
        arguments.threads,
    )
    serve(
        app,
        host=arguments.host,
        port=arguments.port,
        threads=arguments.threads,
        channel_timeout=120,
        cleanup_interval=30,
    )


if __name__ == "__main__":
    main()
