from __future__ import annotations

import json

import httpx
import pytest

from immich_ftp_pixel_backup.immich import ImmichClient, ImmichError


def _handler(request: httpx.Request) -> httpx.Response:
    if request.method == "GET" and request.url.path == "/api/assets/a1":
        if request.headers.get("x-api-key") != "good-key":
            return httpx.Response(401, json={"message": "unauthorized"})
        return httpx.Response(
            200,
            json={
                "id": "a1",
                "checksum": "Y2hlY2tzdW0=",
                "originalFileName": "pic.jpg",
                "createdAt": "2026-01-02T00:00:00.000Z",
                "fileCreatedAt": "2026-01-01T12:00:00.000Z",
                "ownerId": "user-1",
                "exifInfo": {"fileSizeInByte": 1234},
            },
        )

    if request.method == "GET" and request.url.path == "/api/assets/missing":
        return httpx.Response(404, json={"message": "not found"})

    if request.method == "GET" and request.url.path == "/api/assets/a1/original":
        return httpx.Response(200, content=b"JPEGDATA", headers={"content-type": "image/jpeg"})

    if request.method == "POST" and request.url.path == "/api/search/metadata":
        body = json.loads(request.content.decode())
        page = int(body.get("page") or 1)
        if page == 1:
            return httpx.Response(
                200,
                json={
                    "assets": {
                        "items": [
                            {
                                "id": "a1",
                                "checksum": "c1",
                                "originalFileName": "one.jpg",
                                "createdAt": "2026-01-02T00:00:00.000Z",
                            },
                            {
                                "id": "a2",
                                "checksum": "c2",
                                "originalFileName": "two.jpg",
                                "createdAt": "2026-01-03T00:00:00.000Z",
                            },
                        ],
                        "nextPage": "2",
                    }
                },
            )
        if page == 2:
            return httpx.Response(
                200,
                json={
                    "assets": {
                        "items": [
                            {
                                "id": "a3",
                                "checksum": "c3",
                                "originalFileName": "three.jpg",
                                "createdAt": "2026-01-04T00:00:00.000Z",
                            }
                        ],
                        "nextPage": None,
                    }
                },
            )
        return httpx.Response(200, json={"assets": {"items": [], "nextPage": None}})

    return httpx.Response(500, json={"message": f"unhandled {request.method} {request.url.path}"})


@pytest.fixture
def client() -> ImmichClient:
    transport = httpx.MockTransport(_handler)
    http = httpx.Client(transport=transport, base_url="http://immich.test")
    return ImmichClient("http://immich.test", "good-key", client=http)


def test_get_asset(client: ImmichClient) -> None:
    asset = client.get_asset("a1")
    assert asset.id == "a1"
    assert asset.checksum == "Y2hlY2tzdW0="
    assert asset.original_file_name == "pic.jpg"
    assert asset.file_size == 1234
    assert asset.owner_id == "user-1"


def test_get_asset_404(client: ImmichClient) -> None:
    with pytest.raises(ImmichError) as exc:
        client.get_asset("missing")
    assert exc.value.status_code == 404


def test_get_asset_401() -> None:
    transport = httpx.MockTransport(_handler)
    http = httpx.Client(transport=transport)
    bad = ImmichClient("http://immich.test", "bad-key", client=http)
    with pytest.raises(ImmichError) as exc:
        bad.get_asset("a1")
    assert exc.value.status_code == 401


def test_search_created_after_paginates(client: ImmichClient) -> None:
    assets = list(client.search_assets_created_after("2026-01-01T00:00:00.000Z", page_size=2))
    assert [a.id for a in assets] == ["a1", "a2", "a3"]
    assert assets[0].original_file_name == "one.jpg"


def test_download_original(client: ImmichClient) -> None:
    assert client.download_original("a1") == b"JPEGDATA"
