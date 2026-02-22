from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterator, Optional

import requests
from google.auth.credentials import Credentials as BaseCredentials
from google.auth.transport.requests import AuthorizedSession

from .utils import RetryPolicy, with_retries


PHOTOS_API = "https://photoslibrary.googleapis.com/v1"


def _date_to_api(d: date) -> dict:
    return {"year": d.year, "month": d.month, "day": d.day}


@dataclass(frozen=True)
class PhotosSearchResult:
    media_items: list[dict]
    next_page_token: Optional[str]


class PhotosClient:
    def __init__(self, *, credentials: BaseCredentials, timeout_s: float = 30.0) -> None:
        self._timeout_s = timeout_s
        self._session = AuthorizedSession(credentials)

    def search_favorites_by_date_range(
        self,
        *,
        start_date: date,
        end_date: date,
        page_size: int = 100,
        policy: RetryPolicy = RetryPolicy(),
    ) -> Iterator[dict]:
        page_token: Optional[str] = None
        while True:
            result = self._search_once(
                start_date=start_date,
                end_date=end_date,
                page_size=page_size,
                page_token=page_token,
                policy=policy,
            )
            for item in result.media_items:
                yield item
            if not result.next_page_token:
                break
            page_token = result.next_page_token

    def _search_once(
        self,
        *,
        start_date: date,
        end_date: date,
        page_size: int,
        page_token: Optional[str],
        policy: RetryPolicy,
    ) -> PhotosSearchResult:
        url = f"{PHOTOS_API}/mediaItems:search"
        body: dict = {
            "pageSize": page_size,
            "filters": {
                "featureFilter": {"includedFeatures": ["FAVORITES"]},
                "dateFilter": {"ranges": [{"startDate": _date_to_api(start_date), "endDate": _date_to_api(end_date)}]},
            },
        }
        if page_token:
            body["pageToken"] = page_token

        def _once() -> PhotosSearchResult:
            r = self._session.post(url, json=body, timeout=self._timeout_s)
            try:
                r.raise_for_status()
            except requests.HTTPError as e:
                detail = ""
                try:
                    detail = r.text
                except Exception:
                    detail = ""
                if detail:
                    detail = detail.strip()
                    if len(detail) > 2000:
                        detail = detail[:2000] + "...(truncated)"
                    raise requests.HTTPError(f"{e} | body={detail}", response=r) from e
                raise
            data = r.json()
            return PhotosSearchResult(
                media_items=data.get("mediaItems", []) or [],
                next_page_token=data.get("nextPageToken"),
            )

        return with_retries(_once, retry_on=(requests.RequestException,), policy=policy)

