"""Standalone sandbox screenshot runner with a DNS-pinned public HTTPS proxy.

Uploaded to the sandbox and run there; never starts a browser on the API host.
"""

import ipaddress
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


def public_addresses(host: str) -> list[str]:
    resolved = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(str(entry[4][0]) for entry in resolved))
    if not addresses:
        raise ValueError("Browser destination has no public address")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        if not ip.is_global:
            raise ValueError("Browser destination resolves to a blocked address")
    return addresses


class PublicHTTPSProxy(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_CONNECT(self) -> None:
        try:
            parsed = urlsplit("https://" + self.path)
            if not parsed.hostname or parsed.port != 443 or parsed.username or parsed.password:
                raise ValueError("Browser proxy permits HTTPS on port 443 only")
            addresses = public_addresses(parsed.hostname)
            with socket.create_connection((addresses[0], 443), timeout=15) as upstream:
                self.send_response(200)
                self.end_headers()
                peers = [self.connection, upstream]
                while True:
                    readable, _, _ = select.select(peers, [], [], 20)
                    if not readable:
                        return
                    for peer in readable:
                        data = peer.recv(65536)
                        if not data:
                            return
                        target = upstream if peer is self.connection else self.connection
                        target.sendall(data)
        except ValueError, OSError:
            self.close_connection = True

    def do_GET(self) -> None:
        self.send_error(403, "Browser proxy permits HTTPS only")


if __name__ == "__main__":
    url, destination = sys.argv[1:]
    browser = next(
        (
            path
            for name in ("chromium", "chromium-browser", "google-chrome")
            if (path := shutil.which(name))
        ),
        None,
    )
    if browser is None:
        raise RuntimeError("Install Chromium in the docs workspace sandbox image")
    with (
        ThreadingHTTPServer(("127.0.0.1", 0), PublicHTTPSProxy) as proxy,
        tempfile.TemporaryDirectory(prefix="docs-browser-") as profile,
    ):
        proxy.daemon_threads = True
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        try:
            subprocess.run(
                [
                    browser,
                    "--headless",
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--disable-quic",
                    "--disable-background-networking",
                    "--hide-scrollbars",
                    "--window-size=1440,1000",
                    "--proxy-bypass-list=<-loopback>",
                    f"--proxy-server=http://127.0.0.1:{proxy.server_port}",
                    "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost",
                    f"--user-data-dir={profile}",
                    f"--screenshot={destination}",
                    url,
                ],
                check=True,
                timeout=50,
                capture_output=True,
            )
        finally:
            proxy.shutdown()
