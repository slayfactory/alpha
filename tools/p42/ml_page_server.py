#!/usr/bin/env python3
"""Сервер страницы П-42 (srv :8090) — как `python -m http.server`, плюс Cache-Control (TK-696):
страница, описи и всё без версии — no-cache (браузер сверяет каждый раз, 304 — если не менялось);
пакеты веера с ?v=<версия данных> — на год, адрес меняется вместе с данными.
Запуск: python3 ml_page_server.py <порт> <каталог>   (юнит ml-page-8090, ставит ml_page_update.sh)."""
import functools
import http.server
import sys
from urllib.parse import parse_qs, urlsplit


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        u = urlsplit(self.path)
        pack = u.path.startswith("/data/fan/") and u.path.endswith(".gz") and "v" in parse_qs(u.query)
        self.send_header("Cache-Control", "public, max-age=31536000, immutable" if pack else "no-cache")
        super().end_headers()


if __name__ == "__main__":
    port, root = int(sys.argv[1]), sys.argv[2]
    http.server.ThreadingHTTPServer(("0.0.0.0", port), functools.partial(Handler, directory=root)).serve_forever()
