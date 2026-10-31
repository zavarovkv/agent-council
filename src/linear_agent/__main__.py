"""Run the bridge without an agent executor, shell access or private profile mounts."""

import logging
import os
import signal
import threading
from pathlib import Path

from .http import Server
from .service import Service
from .statistics import loop as statistics_loop


def main():
    os.umask(0o077)
    if os.geteuid() == 0 or os.environ.get("HERMES_HOME") != "/opt/data":
        raise SystemExit("Use the isolated unprivileged Linear adapter profile.")
    logging.basicConfig(level=logging.WARNING)
    service = Service(Path("/opt/data"))
    public = Server(("0.0.0.0", 9121), service)
    internal = Server(("0.0.0.0", 9122), service, internal=True)
    threads = [
        threading.Thread(target=target, daemon=True)
        for target in (
            public.serve_forever,
            internal.serve_forever,
            service.loop,
            lambda: statistics_loop(service),
        )
    ]
    for thread in threads:
        thread.start()

    def shutdown(_signal, _frame):
        service.halt.set()
        service.wake.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    service.halt.wait()
    public.shutdown()
    internal.shutdown()
    for thread in threads:
        thread.join(timeout=10)


if __name__ == "__main__":
    main()
