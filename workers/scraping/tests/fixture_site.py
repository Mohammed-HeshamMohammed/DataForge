"""A small local website used by expansion tests: sitemaps, feeds, llms.txt, structured data, articles,
crawlable listings, documents, and usage signals. Served on 127.0.0.1 only."""

from __future__ import annotations

import gzip
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlparse

PRODUCTS = [{"sku": f"SKU-{n}", "name": f"Widget {n}", "price": f"{n}9.99", "phone": f"(512) 555-01{n:02d}"} for n in range(1, 13)]


def product_page(product: dict, variant: str = "jsonld") -> str:
    jsonld = {"@context": "https://schema.org", "@graph": [
        {"@type": "Product", "name": product["name"], "sku": product["sku"], "offers": {"@type": "Offer", "price": product["price"], "priceCurrency": "USD"}},
        {"@type": "BreadcrumbList", "itemListElement": []},
    ]}
    microdata = ""
    if variant == "conflict":
        microdata = f"<div itemscope itemtype='https://schema.org/Product'><span itemprop='name'>{product['name']}</span><meta itemprop='sku' content='{product['sku']}'><div itemprop='offers' itemscope itemtype='https://schema.org/Offer'><span itemprop='price'>1.00</span></div></div>"
    return (f"<html><head><title>{product['name']}</title><script type='application/ld+json'>{json.dumps(jsonld)}</script></head>"
            f"<body><main class='product'><h1>{product['name']}</h1><span class='sku'>{product['sku']}</span><span class='price'>${product['price']}</span></main>{microdata}"
            f"<a href='/products?page=1'>back</a></body></html>")


ARTICLE = ("<html><head><title>Rivers of Texas</title><meta name='author' content='A. Writer'>"
           "<meta property='article:published_time' content='2026-03-04'></head><body><nav>Home | About</nav>"
           "<article><h1>Rivers of Texas</h1><p>" + "The Colorado River of Texas flows through Austin and on to Matagorda Bay. " * 12 + "</p>"
           "<p>" + "Its reservoirs, the Highland Lakes, supply water to much of central Texas. " * 8 + "</p></article>"
           "<footer>Copyright notice and unrelated links</footer></body></html>")


class SiteState:
    def __init__(self) -> None:
        self.requests: list[str] = []
        self.robots = "User-agent: *\nDisallow: /private\nCrawl-delay: 0\nSitemap: {base}/sitemap_index.xml\n"
        self.tdmrep: object | None = None
        self.content_usage_header: str | None = None
        self.etag_hits = 0
        self.pdf: bytes | None = None
        self.robots_status = 200


def make_handler(state: SiteState):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes | str, content_type: str = "text/html; charset=utf-8", headers: dict | None = None) -> None:
            data = body.encode() if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode()
            state.requests.append("POST " + self.path + " " + body)
            self._send(200, json.dumps({"elements": [{"id": 1, "tags": {"name": "Cafe", "amenity": "cafe"}, "body": body}]}), "application/json")

        def do_GET(self) -> None:
            url = urlparse(self.path)
            query = parse_qs(url.query)
            state.requests.append(self.path)
            base = f"http://{self.headers['Host']}"
            path = url.path
            if path == "/robots.txt":
                if state.robots_status != 200:
                    return self._send(state.robots_status, "error", "text/plain")
                return self._send(200, state.robots.format(base=base), "text/plain")
            if path == "/.well-known/tdmrep.json":
                return self._send(200, json.dumps(state.tdmrep), "application/json") if state.tdmrep is not None else self._send(404, "")
            if path == "/ai.txt":
                return self._send(404, "")
            if path == "/sitemap_index.xml":
                return self._send(200, f"<?xml version='1.0'?><sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'><sitemap><loc>{base}/sitemap-products.xml.gz</loc></sitemap><sitemap><loc>{base}/sitemap-pages.xml</loc><lastmod>2020-01-01</lastmod></sitemap></sitemapindex>", "application/xml")
            if path == "/sitemap-products.xml.gz":
                xml = "<?xml version='1.0'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>" + "".join(
                    f"<url><loc>{base}/product/{p['sku']}</loc><lastmod>2026-0{1 + i % 9}-01</lastmod></url>" for i, p in enumerate(PRODUCTS)) + f"<url><loc>{base}/private/secret</loc></url><url><loc>https://elsewhere.example/product/x</loc></url></urlset>"
                return self._send(200, gzip.compress(xml.encode()), "application/gzip")
            if path == "/sitemap-pages.xml":
                return self._send(200, f"<?xml version='1.0'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'><url><loc>{base}/article</loc></url></urlset>", "application/xml")
            if path == "/products":
                page = int(query.get("page", ["1"])[0])
                items = PRODUCTS[(page - 1) * 4: page * 4]
                links = "".join(f"<li class='item'><a href='/product/{p['sku']}'>{p['name']}</a></li>" for p in items)
                more = f"<a rel='next' href='/products?page={page + 1}'>next</a>" if page * 4 < len(PRODUCTS) else ""
                return self._send(200, f"<html><body><ul>{links}</ul>{more}<a href='/private/x'>p</a><a href='https://elsewhere.example/'>x</a></body></html>")
            if path.startswith("/product/"):
                sku = path.rsplit("/", 1)[1]
                product = next((p for p in PRODUCTS if p["sku"] == sku), None)
                if product is None:
                    return self._send(404, "missing")
                headers = {"Content-Usage": state.content_usage_header} if state.content_usage_header else {}
                return self._send(200, product_page(product, "conflict" if sku == "SKU-3" and query.get("conflict") else "jsonld"), headers=headers)
            if path == "/article":
                etag = '"article-v1"'
                if self.headers.get("If-None-Match") == etag:
                    state.etag_hits += 1
                    self.send_response(304)
                    self.send_header("ETag", etag)
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    return
                return self._send(200, ARTICLE, headers={"ETag": etag, "Cache-Control": "no-cache"})
            if path == "/feed.xml":
                items = "".join(f"<item><title>{p['name']}</title><link>{base}/product/{p['sku']}</link><guid>{p['sku']}</guid><pubDate>Mon, 0{1 + i % 9} Jun 2026 10:00:00 GMT</pubDate><description>&lt;b&gt;Buy&lt;/b&gt; {p['name']}</description></item>" for i, p in enumerate(PRODUCTS[:5]))
                return self._send(200, f"<?xml version='1.0'?><rss version='2.0'><channel><title>Products</title>{items}</channel></rss>", "application/rss+xml")
            if path == "/llms.txt":
                return self._send(200, f"# Fixture\n\n> Test site\n\n## Docs\n\n- [Guide]({base}/docs/guide.md): how to\n- [Article](/article)\n\n## Optional\n\n- [Extra](/docs/extra.md)\n", "text/plain")
            if path.startswith("/docs/"):
                return self._send(200, "# Guide\n\nUse the widgets.", "text/markdown")
            if path == "/reports":
                return self._send(200, "<html><body><a href='/files/table.pdf'>PDF</a><a href='/files/data.csv'>CSV</a><a href='/files/fake.pdf'>fake</a></body></html>")
            if path == "/files/table.pdf":
                return self._send(200, state.pdf or b"", "application/pdf")
            if path == "/files/fake.pdf":
                return self._send(200, "<html>not a pdf</html>", "application/pdf")
            if path == "/files/data.csv":
                return self._send(200, "name,phone\nAcme,512-555-0100\nBeta,512-555-0101\n", "text/csv")
            if path == "/challenge":
                return self._send(200, "<div class='g-recaptcha'></div>")
            if path == "/limited":
                return self._send(429, "slow down", headers={"Retry-After": "1"})
            if path == "/api/search":
                return self._send(200, json.dumps({"results": {"bindings": [{"item": {"type": "uri", "value": "http://x/Q1"}, "label": {"type": "literal", "value": "One"}}]}, "echo": url.query}), "application/json")
            return self._send(404, "not found")

        def log_message(self, *args: object) -> None:
            return

    return Handler


def serve() -> tuple[str, SiteState, ThreadingHTTPServer]:
    state = SiteState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state))
    Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_port}", state, server


def table_pdf(rows: list[list[str]]) -> bytes:
    """A minimal ruled-table PDF (no external libraries) so document extraction is tested offline."""
    x0, y0, widths, height = 50, 700, [150, 150, 100], 20
    stream = ["0.5 w"]
    total_width = sum(widths)
    for index in range(len(rows) + 1):
        y = y0 - index * height
        stream.append(f"{x0} {y} m {x0 + total_width} {y} l S")
    x = x0
    for width in [0, *widths]:
        x += width
        stream.append(f"{x} {y0} m {x} {y0 - len(rows) * height} l S")
    for r, row in enumerate(rows):
        x = x0
        for c, cell in enumerate(row):
            stream.append(f"BT /F1 10 Tf {x + 4} {y0 - (r + 1) * height + 6} Td ({cell}) Tj ET")
            x += widths[c]
    content = "\n".join(stream).encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
