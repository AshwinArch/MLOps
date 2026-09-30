"""Standalone worker entrypoint:  python -m app.workers.run

Same image as the API. Deploy API replicas with WORKER_ENABLED=false and this process as its own Deployment
so workers scale independently of request traffic.
"""
import logging
import signal

from app.core.config import get_settings, validate_production
from app.core.logging import configure_logging
from app.db.session import init_db, make_engine, make_session_factory
from app.workers.factory import build_runtime
from app.workers.worker import DeploymentWorker


def _serve_metrics(port: int) -> None:
    """Workers have no HTTP API, so counters (deployments_*_total, failure classes) need their own scrape port."""
    if not port:
        return
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread

    from app.core import metrics

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            ok = self.path == "/metrics"
            body = (metrics.render() if ok else "ok\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    Thread(target=HTTPServer(("0.0.0.0", port), Handler).serve_forever, daemon=True).start()


def main() -> None:
    settings = get_settings()
    validate_production(settings)
    configure_logging(settings.log_level)
    engine = make_engine(settings.database_url)
    if settings.auto_create_schema:
        init_db(engine)
    _serve_metrics(settings.worker_metrics_port)
    worker = DeploymentWorker(make_session_factory(engine), build_runtime(settings), settings)
    signal.signal(signal.SIGTERM, lambda *_: worker._stop.set())
    signal.signal(signal.SIGINT, lambda *_: worker._stop.set())
    logging.getLogger("worker").info("worker started")
    worker._loop()  # blocks until SIGTERM


if __name__ == "__main__":
    main()
