"""Loopback-only MCP App preview, synthetic host/data, no DB or provider calls.

Run python -m benchmarks.explorer_preview, then open http://127.0.0.1:8033/.
This is not a ChatGPT capability test or a production server.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from app.explorer import explorer_html, reporter_html


class Preview(BaseHTTPRequestHandler):
    def do_GET(self):
        route = urlsplit(self.path).path
        if route == "/":
            content = (Path(__file__).parent.parent / "tests/ui/preview.html").read_text("utf-8")
        elif route == "/explorer":
            content = explorer_html()
        elif route == "/reporter":
            content = reporter_html()
        elif route == "/reporter-preview":
            content = (Path(__file__).parent.parent / "tests/ui/reporter-preview.html").read_text(
                "utf-8"
            )
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content.encode())

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    print("Synthetic preview only: http://127.0.0.1:8033/", flush=True)
    ThreadingHTTPServer(("127.0.0.1", 8033), Preview).serve_forever()
