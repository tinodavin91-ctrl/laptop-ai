"""Only one Laptop AI at a time, like a normal app.

Two copies would both answer your Telegram messages and both listen to the microphone. The first copy
holds a port on this PC only (127.0.0.1, never reachable from the network). Starting a second copy
tells the first to show its window, then the second one quietly exits.
"""
from __future__ import annotations

import os
import socket
import threading
from typing import Callable, Optional

PORT = 47653


class Instance:
    def __init__(self, port: int = PORT):
        self.port = port
        self._server: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def acquire(self) -> bool:
        """True if we are the only copy. False means another copy is running (and was asked to show itself)."""
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name != "nt":                      # on Windows this option would let a second copy steal the port
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", self.port))
            s.listen(4)
        except OSError:
            s.close()
            self._poke_running_copy()
            return False
        self._server = s
        return True

    def _poke_running_copy(self) -> None:
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=2) as c:
                c.sendall(b"show")
        except OSError:
            pass

    def listen(self, on_show: Callable[[], None]) -> None:
        """Run in the background: when another copy knocks, call on_show()."""
        if self._server is None:
            return

        def loop():
            self._server.settimeout(0.5)
            while not self._stop.is_set():
                try:
                    conn, _ = self._server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    return
                with conn:
                    try:
                        if conn.recv(8) == b"show":
                            on_show()
                    except OSError:
                        pass
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def release(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)         # let the listener leave accept() before the port is closed
            self._thread = None
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
