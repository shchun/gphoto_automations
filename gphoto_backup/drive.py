from __future__ import annotations

import socket
from typing import Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from google.oauth2.credentials import Credentials

from .utils import RetryPolicy, json_dumps_compact, sleep_backoff


class DriveClient:
    def __init__(self, *, credentials: Credentials) -> None:
        self._svc = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self._date_folder_cache: dict[tuple[str, str], str] = {}
        self._id_exists_cache: dict[str, bool] = {}

    def ensure_date_folder(self, *, root_folder_id: str, date_folder_name: str) -> str:
        key = (root_folder_id, date_folder_name)
        if key in self._date_folder_cache:
            return self._date_folder_cache[key]

        q = (
            f"mimeType='application/vnd.google-apps.folder' and "
            f"'{root_folder_id}' in parents and "
            f"name='{date_folder_name}' and trashed=false"
        )
        resp = (
            self._svc.files()
            .list(q=q, spaces="drive", fields="files(id,name)", pageSize=1)
            .execute()
        )
        files = resp.get("files", []) or []
        if files:
            folder_id = files[0]["id"]
        else:
            meta = {
                "name": date_folder_name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [root_folder_id],
            }
            folder = self._svc.files().create(body=meta, fields="id").execute()
            folder_id = folder["id"]

        self._date_folder_cache[key] = folder_id
        return folder_id

    def already_uploaded(self, *, media_item_id: str) -> bool:
        if media_item_id in self._id_exists_cache:
            return self._id_exists_cache[media_item_id]

        q = (
            "trashed=false and "
            f"appProperties has {{ key='mediaItemId' and value='{media_item_id}' }}"
        )
        resp = (
            self._svc.files()
            .list(q=q, spaces="drive", fields="files(id)", pageSize=1)
            .execute()
        )
        exists = bool(resp.get("files", []) or [])
        self._id_exists_cache[media_item_id] = exists
        return exists

    def already_uploaded_by_sha256(self, *, sha256_hex: str) -> bool:
        key = f"sha256:{sha256_hex}"
        if key in self._id_exists_cache:
            return self._id_exists_cache[key]

        q = (
            "trashed=false and "
            f"appProperties has {{ key='sha256' and value='{sha256_hex}' }}"
        )
        resp = (
            self._svc.files()
            .list(q=q, spaces="drive", fields="files(id)", pageSize=1)
            .execute()
        )
        exists = bool(resp.get("files", []) or [])
        self._id_exists_cache[key] = exists
        return exists

    def list_children(
        self,
        *,
        folder_id: str,
        page_size: int = 200,
        fields: str = "nextPageToken,files(id,name,mimeType,modifiedTime,size,appProperties)",
    ) -> list[dict]:
        out: list[dict] = []
        page_token: Optional[str] = None
        while True:
            resp = (
                self._svc.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed=false",
                    spaces="drive",
                    fields=fields,
                    pageSize=page_size,
                    pageToken=page_token,
                )
                .execute()
            )
            out.extend(resp.get("files", []) or [])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return out

    def download_file(self, *, file_id: str, dest_path: str, policy: RetryPolicy = RetryPolicy()) -> None:
        request = self._svc.files().get_media(fileId=file_id)

        def _once() -> None:
            with open(dest_path, "wb") as f:
                downloader = MediaIoBaseDownload(f, request, chunksize=10 * 1024 * 1024)
                done = False
                while not done:
                    _status, done = downloader.next_chunk()

        return self._execute_with_retries(_once, policy=policy)

    def update_app_properties(self, *, file_id: str, app_properties: dict[str, str]) -> None:
        body = {"appProperties": app_properties}
        self._svc.files().update(fileId=file_id, body=body, fields="id").execute()

    def upload_file(
        self,
        *,
        local_path: str,
        filename: str,
        mime_type: str,
        parent_folder_id: str,
        app_properties: dict[str, str],
        description_obj: dict,
        policy: RetryPolicy = RetryPolicy(max_retries=5),
        resumable: bool = False,
        chunksize: int = 10 * 1024 * 1024,
    ) -> str:
        body = {
            "name": filename,
            "parents": [parent_folder_id],
            "appProperties": app_properties,
            "description": json_dumps_compact(description_obj),
        }
        if resumable:
            media = MediaFileUpload(
                local_path,
                mimetype=mime_type,
                resumable=True,
                chunksize=chunksize,
            )
        else:
            media = MediaFileUpload(
                local_path,
                mimetype=mime_type,
                resumable=False,
            )

        request = self._svc.files().create(body=body, media_body=media, fields="id")
        if not resumable:
            # googleapiclient has internal retries, but keep ours consistent
            return self._execute_with_retries(lambda: request.execute(), policy=policy)

        # Resumable upload loop with explicit retries/backoff
        response = None
        last_err: Optional[BaseException] = None
        for attempt in range(policy.max_retries + 1):
            try:
                while response is None:
                    status, response = request.next_chunk()
                    _ = status  # progress ignored
                return response["id"]
            except (HttpError, socket.timeout, OSError) as e:
                last_err = e
                if attempt >= policy.max_retries:
                    break
                sleep_backoff(attempt, policy)
                # next_chunk() resumes automatically if the request is resumable

        assert last_err is not None
        raise last_err

    def _execute_with_retries(self, fn, *, policy: RetryPolicy):
        last_err: Optional[BaseException] = None
        for attempt in range(policy.max_retries + 1):
            try:
                return fn()
            except (HttpError, socket.timeout, OSError) as e:
                last_err = e
                if attempt >= policy.max_retries:
                    break
                sleep_backoff(attempt, policy)
        assert last_err is not None
        raise last_err

