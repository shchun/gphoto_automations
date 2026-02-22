from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import date

import requests

# Ensure repo root is importable when executed as a script path (e.g. `python scripts/backup_favorites.py`)
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from gphoto_backup.auth import GoogleOAuthSecrets, build_credentials
from gphoto_backup.drive import DriveClient
from gphoto_backup.email_utils import SmtpConfig, send_email
from gphoto_backup.photos import PhotosClient
from gphoto_backup.utils import (
    RetryPolicy,
    iso_to_kst_date,
    json_dumps_compact,
    kst_today,
    month_range_to_dates,
    recent_month_dates,
)


PHOTOS_SCOPE = "https://www.googleapis.com/auth/photoslibrary.readonly"
DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


@dataclass
class Counts:
    total: int = 0
    uploaded: int = 0
    skipped: int = 0
    failed: int = 0


def _env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise RuntimeError(f"Missing env var: {name}")
    return v


def _append_actions_summary(md: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(md)
        if not md.endswith("\n"):
            f.write("\n")


def _get_access_token_scopes(access_token: str) -> list[str]:
    # Avoid printing token anywhere; only return scope list for diagnostics.
    r = requests.get(
        "https://oauth2.googleapis.com/tokeninfo",
        params={"access_token": access_token},
        timeout=30,
    )
    r.raise_for_status()
    info = r.json()
    scope_str = (info.get("scope") or "").strip()
    return [s for s in scope_str.split() if s]


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Backup Google Photos Favorites to Google Drive.")
    p.add_argument("--start-month", help="YYYY-MM (manual range start)")
    p.add_argument("--end-month", help="YYYY-MM (manual range end)")
    p.add_argument(
        "--recent-months",
        type=int,
        default=1,
        help="For schedule mode: how many recent months to include (default: 1).",
    )
    p.add_argument("--dry-run", action="store_true", help="List and count only; no download/upload.")
    return p


def _resolve_range(args: argparse.Namespace) -> tuple[date, date, str]:
    if args.start_month or args.end_month:
        if not (args.start_month and args.end_month):
            raise ValueError("Both --start-month and --end-month are required together.")
        start, end = month_range_to_dates(args.start_month, args.end_month)
        label = f"{args.start_month}..{args.end_month}"
        return start, end, label

    start, end = recent_month_dates(kst_today(), months=args.recent_months)
    label = f"recent_{args.recent_months}m({start.isoformat()}..{end.isoformat()})"
    return start, end, label


def main() -> int:
    args = _build_arg_parser().parse_args()
    start_date, end_date, range_label = _resolve_range(args)

    oauth = GoogleOAuthSecrets(
        client_id=_env("GOOGLE_CLIENT_ID"),
        client_secret=_env("GOOGLE_CLIENT_SECRET"),
        refresh_token=_env("GOOGLE_REFRESH_TOKEN"),
    )
    drive_root_folder_id = _env("DRIVE_FOLDER_ID")

    creds = build_credentials(oauth, scopes=[PHOTOS_SCOPE, DRIVE_SCOPE])
    token_scopes = []
    try:
        if creds.token:
            token_scopes = _get_access_token_scopes(creds.token)
    except Exception:
        token_scopes = []

    if PHOTOS_SCOPE not in token_scopes:
        raise RuntimeError(
            "Access token is missing required Photos scope. "
            f"required={PHOTOS_SCOPE} actual_scopes={token_scopes or '[unknown]'} "
            "(Re-issue refresh token with photoslibrary.readonly and update secrets.)"
        )

    photos = PhotosClient(credentials=creds)
    drive = DriveClient(credentials=creds)

    counts = Counts()
    failures: list[str] = []

    for item in photos.search_favorites_by_date_range(start_date=start_date, end_date=end_date):
        counts.total += 1

        media_item_id = item.get("id")
        filename = item.get("filename") or f"{media_item_id}"
        mime_type = item.get("mimeType") or "application/octet-stream"
        base_url = item.get("baseUrl")
        product_url = item.get("productUrl")
        creation_time = (item.get("mediaMetadata") or {}).get("creationTime")

        if not (media_item_id and base_url and creation_time):
            counts.failed += 1
            failures.append(json_dumps_compact({"reason": "missing_required_fields", "item": item}))
            continue

        kst_date = iso_to_kst_date(creation_time)
        folder_id = drive.ensure_date_folder(root_folder_id=drive_root_folder_id, date_folder_name=kst_date)

        try:
            if drive.already_uploaded(media_item_id=media_item_id):
                counts.skipped += 1
                continue

            if args.dry_run:
                counts.skipped += 1
                continue

            suffix = "=d" if mime_type.startswith("image/") else "=dv" if mime_type.startswith("video/") else "=d"
            download_url = f"{base_url}{suffix}"

            is_video = mime_type.startswith("video/")
            dl_policy = RetryPolicy(max_retries=6, base_sleep_s=1.0, max_sleep_s=60.0) if is_video else RetryPolicy()
            timeout_s = (10.0, 300.0) if is_video else (10.0, 60.0)

            fd, tmp_path = tempfile.mkstemp(prefix="gphoto_", suffix=f"_{filename}")
            os.close(fd)
            try:
                from gphoto_backup.utils import download_to_path

                download_to_path(url=download_url, path=tmp_path, timeout_s=timeout_s, policy=dl_policy)

                description_obj = {
                    "mediaItem": {
                        "id": media_item_id,
                        "filename": filename,
                        "productUrl": product_url,
                        "baseUrl": base_url,
                        "creationTime": creation_time,
                        "mimeType": mime_type,
                    }
                }
                file_id = drive.upload_file(
                    local_path=tmp_path,
                    filename=filename,
                    mime_type=mime_type,
                    parent_folder_id=folder_id,
                    app_properties={
                        "mediaItemId": media_item_id,
                        "creationTime": creation_time,
                        "mimeType": mime_type,
                    },
                    description_obj=description_obj,
                    policy=RetryPolicy(max_retries=8, base_sleep_s=1.0, max_sleep_s=90.0) if is_video else RetryPolicy(),
                    resumable=is_video,
                )
                _ = file_id
                counts.uploaded += 1
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        except Exception as e:  # keep going for the rest
            counts.failed += 1
            failures.append(json_dumps_compact({"id": media_item_id, "error": repr(e)}))

    today = kst_today().isoformat()

    summary_lines = [
        "## Google Photos Favorites Backup",
        "",
        f"- **Date(KST)**: {today}",
        f"- **Range**: {range_label} ({start_date.isoformat()}..{end_date.isoformat()})",
        "",
        "### Result",
        f"- **Queried favorites**: {counts.total}",
        f"- **Uploaded**: {counts.uploaded}",
        f"- **Skipped**: {counts.skipped}",
        f"- **Failed**: {counts.failed}",
        "",
    ]
    if counts.failed:
        summary_lines += ["### Failures (sample)", "```", "\n".join(failures[:20]), "```", ""]
    _append_actions_summary("\n".join(summary_lines))

    # Email notify
    smtp = SmtpConfig(
        host=_env("SMTP_HOST"),
        port=int(_env("SMTP_PORT")),
        user=_env("SMTP_USER"),
        password=_env("SMTP_PASSWORD"),
    )
    email_to = _env("EMAIL_TO")

    subject = f"[GooglePhotoBackup] {today} 결과"
    warning = "WARNING: 실패가 있습니다.\n\n" if counts.failed else ""
    body = (
        warning
        + "\n".join(
            [
                f"조회 Favorites 수: {counts.total}",
                f"신규 업로드 수: {counts.uploaded}",
                f"스킵 수: {counts.skipped}",
                f"실패 수: {counts.failed}",
                f"실행 범위: {range_label} ({start_date.isoformat()}..{end_date.isoformat()})",
            ]
        )
        + "\n"
    )
    send_email(smtp=smtp, to_addrs=email_to, subject=subject, body_text=body)

    return 1 if counts.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

