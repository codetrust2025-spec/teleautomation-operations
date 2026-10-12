"""Dashboard responses are compressed and its hashed assets cached (12 Oct).

Every dashboard open used to download 686 KB of JavaScript and 576 KB of CSS
uncompressed and uncached, and every list came back as raw JSON: nginx does not
compress proxied responses and the app did not either.
"""
from __future__ import annotations

import inspect

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from core.response_compression import ASSET_CACHE_CONTROL, CompressionMiddleware

ROWS = [{"id": f"row-{i}", "name": f"Invented Person {i}", "notes": "x" * 40} for i in range(200)]


@pytest.fixture
def client(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app-abc123.js").write_bytes(b"console.log('dashboard');\n" * 400)
    (assets / "tiny-1.css").write_bytes(b"a{}")
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4\n" + b"0" * 5000)

    app = FastAPI()

    @app.get("/candidates")
    def rows():
        return {"status": "ok", "candidates": ROWS}

    @app.get("/small")
    def small():
        return {"status": "ok"}

    @app.get("/resume")
    def resume():
        return FileResponse(str(pdf), media_type="application/pdf")

    @app.get("/csv")
    def csv():
        return Response("name\n" * 2000, media_type="text/csv")

    @app.get("/already")
    def already():
        return PlainTextResponse("y" * 5000, headers={"Content-Encoding": "br"})

    @app.get("/partial")
    def partial():
        return PlainTextResponse("z" * 5000, status_code=206, headers={"Content-Range": "bytes 0-4999/9000"})

    @app.get("/progress")
    def progress():
        # The booking form's live AI progress: must stream, never be held back.
        return StreamingResponse(iter([b"data: " + b"p" * 2000 + b"\n\n"] * 3), media_type="text/event-stream")

    @app.get("/error")
    def error():
        return JSONResponse({"detail": "e" * 5000}, status_code=500)

    app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")
    app.add_middleware(CompressionMiddleware)
    return TestClient(app)


GZIP = {"Accept-Encoding": "gzip, deflate"}


def test_a_list_is_gzipped_and_reads_back_identically(client):
    response = client.get("/candidates", headers=GZIP)
    assert response.headers["content-encoding"] == "gzip"
    assert "accept-encoding" in response.headers["vary"].lower()
    assert response.json()["candidates"] == ROWS
    raw = client.get("/candidates", headers={"Accept-Encoding": "identity"})
    assert int(response.headers["content-length"]) < len(raw.content) / 4


def test_nothing_is_compressed_for_a_client_that_did_not_ask(client):
    response = client.get("/candidates", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in response.headers
    assert response.json()["candidates"] == ROWS


def test_tiny_bodies_are_left_alone(client):
    response = client.get("/small", headers=GZIP)
    assert "content-encoding" not in response.headers
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/resume", "/csv", "/already", "/partial", "/progress"])
def test_downloads_partials_and_encoded_bodies_pass_through(client, path):
    response = client.get(path, headers=GZIP)
    assert response.headers.get("content-encoding") in (None, "br")


def test_an_error_body_is_not_reshaped(client):
    response = client.get("/error", headers=GZIP)
    assert response.status_code == 500
    assert "content-encoding" not in response.headers


def test_hashed_assets_are_compressed_and_cached_for_a_year(client):
    first = client.get("/assets/app-abc123.js", headers=GZIP)
    second = client.get("/assets/app-abc123.js", headers=GZIP)
    assert first.headers["content-encoding"] == "gzip"
    assert first.headers["cache-control"] == ASSET_CACHE_CONTROL.decode()
    assert first.text == "console.log('dashboard');\n" * 400
    assert second.content == first.content


def test_a_small_asset_is_still_cached(client):
    response = client.get("/assets/tiny-1.css", headers=GZIP)
    assert response.headers["cache-control"] == ASSET_CACHE_CONTROL.decode()
    assert "content-encoding" not in response.headers


def test_a_missing_asset_is_not_cached(client):
    response = client.get("/assets/nope.js", headers=GZIP)
    assert response.status_code == 404
    assert "cache-control" not in response.headers


def test_websockets_pass_straight_through():
    app = FastAPI()

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        await socket.send_json({"event": "connected"})
        await socket.close()

    app.add_middleware(CompressionMiddleware)
    with TestClient(app).websocket_connect("/ws") as socket:
        assert socket.receive_json() == {"event": "connected"}


def test_the_operations_app_installs_it_outermost():
    import main

    source = inspect.getsource(main)
    assert "app.add_middleware(CompressionMiddleware)" in source
    assert source.index("app.add_middleware(CompressionMiddleware)") > source.index("install_dashboard_auth(app)")
    # index.html keeps telling browsers to come back, so a deploy is picked up.
    assert '"Cache-Control": "no-cache, no-store, must-revalidate"' in source
