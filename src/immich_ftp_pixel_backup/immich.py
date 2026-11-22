from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterator

import httpx


class ImmichError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ImmichAsset:
    id: str
    checksum: str | None
    original_file_name: str | None
    file_created_at: str | None
    created_at: str | None
    file_size: int | None
    owner_id: str | None
    raw: dict[str, Any]


def _parse_asset(data: dict[str, Any]) -> ImmichAsset:
    exif = data.get("exifInfo") or {}
    file_size = exif.get("fileSizeInByte")
    if file_size is None:
        file_size = data.get("size")
    return ImmichAsset(
        id=str(data["id"]),
        checksum=data.get("checksum"),
        original_file_name=data.get("originalFileName"),
        file_created_at=data.get("fileCreatedAt"),
        created_at=data.get("createdAt"),
        file_size=int(file_size) if file_size is not None else None,
        owner_id=data.get("ownerId"),
        raw=data,
    )


class ImmichClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> ImmichClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._api_key, "Accept": "application/json"}

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{self._base_url}{path}"
        headers = dict(kwargs.pop("headers", {}))
        headers.update(self._headers())
        try:
            response = self._client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise ImmichError(f"Immich request failed: {exc}") from exc
        if response.status_code >= 400:
            raise ImmichError(
                f"Immich {method} {path} failed: HTTP {response.status_code}",
                status_code=response.status_code,
            )
        return response

    def get_asset(self, asset_id: str) -> ImmichAsset:
        response = self._request("GET", f"/api/assets/{asset_id}")
        return _parse_asset(response.json())

    def search_assets_created_after(
        self,
        created_after: str,
        *,
        page_size: int = 100,
    ) -> Iterator[ImmichAsset]:
        """Yield assets with Immich createdAt after the given ISO timestamp."""
        page = 1
        while True:
            response = self._request(
                "POST",
                "/api/search/metadata",
                json={
                    "createdAfter": created_after,
                    "page": page,
                    "size": page_size,
                },
            )
            payload = response.json()
            # Immich returns { assets: { items, total, ... } } or a flat list in some versions.
            if isinstance(payload, list):
                items = payload
                next_page = None
            else:
                assets = payload.get("assets") or payload
                if isinstance(assets, list):
                    items = assets
                    next_page = None
                else:
                    items = assets.get("items") or []
                    next_page = assets.get("nextPage")

            if not items:
                break

            for item in items:
                yield _parse_asset(item)

            if next_page not in (None, "", False):
                page = int(next_page) if str(next_page).isdigit() else page + 1
                continue
            if len(items) < page_size:
                break
            page += 1

    def download_original(self, asset_id: str) -> bytes:
        response = self._request(
            "GET",
            f"/api/assets/{asset_id}/original",
            headers={"Accept": "*/*"},
        )
        return response.content
