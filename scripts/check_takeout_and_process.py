from __future__ import annotations

import argparse
import email
import imaplib
import os
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Iterable, Optional

# Ensure repo root is importable when executed as a script path
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from gphoto_backup.auth import GoogleOAuthSecrets, build_credentials
from gphoto_backup.drive import DriveClient
from gphoto_backup.email_utils import SmtpConfig, send_email
from gphoto_backup.utils import KST, RetryPolicy, iso_to_kst_date, json_dumps_compact, kst_today


DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"


@dataclass
class Counts:
    takeout_files_seen: int = 0
    takeout_files_processed: int = 0
    favorites_found: int = 0
    uploaded: int = 0
    skipped: int = 0
    failed: int = 0


def _env(name: str, default: str | None = None) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        if default is not None:
            return default
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


def _imap_find_takeout_ready(
    *,
    host: str,
    user: str,
    password: str,
    mailbox: str = "INBOX",
    from_contains: str = "google",
    subject_keywords: Iterable[str] = (),
) -> bool:
    # Looks for UNSEEN messages that likely indicate Takeout export completion.
    # If found, marks them as Seen to avoid re-triggering.
    kw = [k for k in subject_keywords if k]
    with imaplib.IMAP4_SSL(host) as imap:
        imap.login(user, password)
        imap.select(mailbox)

        # IMAP SEARCH is limited; keep it simple and filter in Python.
        typ, data = imap.search(None, "UNSEEN")
        if typ != "OK":
            return False
        ids = (data[0] or b"").split()
        if not ids:
            return False

        matched = []
        for msg_id in ids[-50:]:  # cap scan
            typ, parts = imap.fetch(msg_id, "(BODY.PEEK[HEADER])")
            if typ != "OK" or not parts or not isinstance(parts[0], tuple):
                continue
            raw = parts[0][1]
            msg = email.message_from_bytes(raw)
            subj = (msg.get("Subject") or "").strip()
            frm = (msg.get("From") or "").strip().lower()
            if from_contains and from_contains.lower() not in frm:
                continue
            if kw and not any(k.lower() in subj.lower() for k in kw):
                continue
            matched.append(msg_id)

        if not matched:
            return False

        # Mark matched as Seen (best-effort)
        for msg_id in matched:
            imap.store(msg_id, "+FLAGS", "\\Seen")
        return True


def _parse_favorite_flag(meta: dict) -> bool:
    # Takeout JSON varies; handle common shapes.
    for key in ("isFavorite", "favorite", "favorited", "is_favorite"):
        if key in meta:
            v = meta.get(key)
            if isinstance(v, bool):
                return v
            if isinstance(v, str):
                return v.strip().lower() in ("true", "1", "yes", "y")
            if isinstance(v, (int, float)):
                return bool(v)

    # Some exports include "starred" or similar flags
    for key in ("starred", "isStarred"):
        if key in meta:
            v = meta.get(key)
            if isinstance(v, bool):
                return v
            if isinstance(v, str):
                return v.strip().lower() in ("true", "1", "yes", "y")
    return False


def _extract_taken_time_iso(meta: dict) -> Optional[str]:
    # Prefer taken time fields commonly found in Takeout.
    candidates = [
        ("photoTakenTime", "timestamp"),
        ("creationTime", "timestamp"),
        ("takenTime", "timestamp"),
    ]
    for obj_key, ts_key in candidates:
        obj = meta.get(obj_key)
        if isinstance(obj, dict) and ts_key in obj:
            ts = obj.get(ts_key)
            try:
                ts_int = int(ts)
                return datetime.fromtimestamp(ts_int, tz=KST).astimezone(KST).isoformat()
            except Exception:
                pass

    # Some files include RFC3339-like "creationTime"
    ct = meta.get("mediaMetadata", {}).get("creationTime") if isinstance(meta.get("mediaMetadata"), dict) else None
    if isinstance(ct, str) and ct:
        return ct
    return None


def _safe_drive_filename(name: str) -> str:
    # Drive allows many chars, but avoid path separators.
    name = name.replace("\\", "_").replace("/", "_").strip()
    return name or "file"


def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Check Gmail for Takeout completion and process Drive Takeout exports.")
    p.add_argument("--dry-run", action="store_true", help="Do not upload; only parse and count.")
    p.add_argument("--force", action="store_true", help="Process Drive Takeout exports even without Gmail trigger.")
    p.add_argument("--max-zips", type=int, default=5, help="Max number of Takeout zip files to process per run.")
    return p


def main() -> int:
    args = _build_arg_parser().parse_args()

    smtp = SmtpConfig(
        host=_env("SMTP_HOST"),
        port=int(_env("SMTP_PORT", "587")),
        user=_env("SMTP_USER"),
        password=_env("SMTP_PASSWORD"),
    )
    email_to = _env("EMAIL_TO")

    # IMAP (Gmail) trigger: optional but recommended
    imap_host = os.environ.get("IMAP_HOST", "").strip() or "imap.gmail.com"
    imap_user = os.environ.get("IMAP_USER", "").strip() or smtp.user
    imap_password = os.environ.get("IMAP_PASSWORD", "").strip() or smtp.password
    imap_mailbox = os.environ.get("IMAP_MAILBOX", "").strip() or "INBOX"

    subject_keywords = [
        "Takeout",
        "데이터가 준비",
        "data is ready",
        "Google data is ready",
    ]

    triggered = False
    if args.force:
        triggered = True
    else:
        try:
            triggered = _imap_find_takeout_ready(
                host=imap_host,
                user=imap_user,
                password=imap_password,
                mailbox=imap_mailbox,
                from_contains="google",
                subject_keywords=subject_keywords,
            )
        except Exception as e:
            # If IMAP fails, do not process (avoid unexpected large runs)
            _append_actions_summary(
                "\n".join(
                    [
                        "## Takeout check",
                        "",
                        f"- **Triggered**: false (IMAP error: {repr(e)})",
                        "",
                    ]
                )
            )
            return 1

    if not triggered:
        _append_actions_summary(
            "\n".join(
                [
                    "## Takeout check",
                    "",
                    "- **Triggered**: false (no new Takeout-ready email found)",
                    "",
                ]
            )
        )
        return 0

    oauth = GoogleOAuthSecrets(
        client_id=_env("GOOGLE_CLIENT_ID"),
        client_secret=_env("GOOGLE_CLIENT_SECRET"),
        refresh_token=_env("GOOGLE_REFRESH_TOKEN"),
    )
    backup_root_folder_id = _env("DRIVE_FOLDER_ID")
    takeout_source_folder_id = _env("TAKEOUT_FOLDER_ID")

    creds = build_credentials(oauth, scopes=[DRIVE_SCOPE])
    drive = DriveClient(credentials=creds)

    counts = Counts()
    failures: list[str] = []

    # Find candidate zip files in Takeout source folder that are not processed yet
    children = drive.list_children(folder_id=takeout_source_folder_id)
    zips = [
        f
        for f in children
        if (f.get("mimeType") == "application/zip" or (f.get("name", "").lower().endswith(".zip")))
    ]
    counts.takeout_files_seen = len(zips)

    def is_processed(f: dict) -> bool:
        ap = f.get("appProperties") or {}
        return isinstance(ap, dict) and ap.get("takeoutProcessed") == "true"

    pending = [f for f in zips if not is_processed(f)]
    pending.sort(key=lambda x: (x.get("modifiedTime") or "", x.get("name") or ""))
    pending = pending[: max(0, args.max_zips)]

    with tempfile.TemporaryDirectory(prefix="takeout_") as td:
        td_path = Path(td)
        for f in pending:
            file_id = f.get("id")
            name = f.get("name") or "takeout.zip"
            if not file_id:
                continue
            counts.takeout_files_processed += 1

            zip_path = td_path / _safe_drive_filename(name)
            try:
                drive.download_file(file_id=file_id, dest_path=str(zip_path), policy=RetryPolicy(max_retries=6))
            except Exception as e:
                counts.failed += 1
                failures.append(json_dumps_compact({"takeoutZip": name, "error": repr(e)}))
                continue

            try:
                _process_zip(
                    zip_path=zip_path,
                    drive=drive,
                    backup_root_folder_id=backup_root_folder_id,
                    counts=counts,
                    failures=failures,
                    dry_run=args.dry_run,
                )

                # Mark zip as processed to make daily polling idempotent.
                try:
                    ap = f.get("appProperties") or {}
                    if not isinstance(ap, dict):
                        ap = {}
                    ap["takeoutProcessed"] = "true"
                    ap["takeoutProcessedAt"] = datetime.now(tz=KST).isoformat()
                    drive.update_app_properties(file_id=file_id, app_properties=ap)  # best-effort
                except Exception:
                    pass
            except Exception as e:
                counts.failed += 1
                failures.append(json_dumps_compact({"takeoutZip": name, "error": repr(e)}))

    today = kst_today().isoformat()
    subject = f"[GooglePhotoBackup] {today} Takeout 처리 결과"
    warning = "WARNING: 실패가 있습니다.\n\n" if counts.failed else ""
    body = warning + "\n".join(
        [
            f"Takeout zip 발견: {counts.takeout_files_seen}",
            f"Takeout zip 처리: {counts.takeout_files_processed}",
            f"Favorites 메타 발견: {counts.favorites_found}",
            f"신규 업로드: {counts.uploaded}",
            f"스킵(중복/기타): {counts.skipped}",
            f"실패: {counts.failed}",
            f"모드: {'dry-run' if args.dry_run else 'upload'}",
        ]
    )
    send_email(smtp=smtp, to_addrs=email_to, subject=subject, body_text=body + "\n")

    summary_lines = [
        "## Takeout processing",
        "",
        f"- **Triggered**: true",
        f"- **Takeout zip found**: {counts.takeout_files_seen}",
        f"- **Takeout zip processed**: {counts.takeout_files_processed}",
        f"- **Favorites found**: {counts.favorites_found}",
        f"- **Uploaded**: {counts.uploaded}",
        f"- **Skipped**: {counts.skipped}",
        f"- **Failed**: {counts.failed}",
        "",
    ]
    if failures:
        summary_lines += ["### Failures (sample)", "```", "\n".join(failures[:20]), "```", ""]
    _append_actions_summary("\n".join(summary_lines))

    return 1 if counts.failed else 0


def _process_zip(
    *,
    zip_path: Path,
    drive: DriveClient,
    backup_root_folder_id: str,
    counts: Counts,
    failures: list[str],
    dry_run: bool,
) -> None:
    # Heuristic: Takeout photos are under a folder containing "Google Photos"
    with zipfile.ZipFile(zip_path, "r") as z:
        names = z.namelist()
        json_names = [n for n in names if n.lower().endswith(".json")]

        # Map media path (without .json) -> json path
        json_by_base: dict[str, str] = {}
        for jn in json_names:
            base = re.sub(r"\.json$", "", jn, flags=re.IGNORECASE)
            json_by_base[base] = jn

        for base, jn in json_by_base.items():
            try:
                with z.open(jn) as jf:
                    import json as _json

                    meta = _json.load(jf)
            except Exception:
                continue

            if not isinstance(meta, dict):
                continue
            if not _parse_favorite_flag(meta):
                continue
            counts.favorites_found += 1

            # Find the media file entry. Commonly the base path exists exactly.
            media_name = None
            if base in names:
                media_name = base
            else:
                # Some takeouts add suffixes; try matching by stem.
                base_stem = Path(base).name
                for n in names:
                    if Path(n).name == base_stem:
                        media_name = n
                        break
            if not media_name:
                counts.failed += 1
                failures.append(json_dumps_compact({"reason": "media_not_found", "json": jn}))
                continue

            taken_iso = _extract_taken_time_iso(meta)
            if not taken_iso:
                counts.failed += 1
                failures.append(json_dumps_compact({"reason": "taken_time_not_found", "json": jn}))
                continue

            # Use KST date folder from taken time
            try:
                # taken_iso might be tz-aware isoformat; convert to date string
                dt = datetime.fromisoformat(taken_iso)
                kst_date = dt.astimezone(KST).date().isoformat()
            except Exception:
                # fallback for RFC3339
                kst_date = iso_to_kst_date(taken_iso)

            folder_id = drive.ensure_date_folder(root_folder_id=backup_root_folder_id, date_folder_name=kst_date)

            filename = _safe_drive_filename(meta.get("title") or Path(media_name).name)
            mime_type = (meta.get("mimeType") or meta.get("mime_type") or "").strip() or "application/octet-stream"

            with tempfile.NamedTemporaryFile(prefix="takeout_item_", delete=False) as tf:
                tmp_path = tf.name
            try:
                # Extract media to temp file and hash it
                h = sha256()
                with z.open(media_name) as mf, open(tmp_path, "wb") as out:
                    for chunk in iter(lambda: mf.read(1024 * 1024), b""):
                        out.write(chunk)
                        h.update(chunk)
                sha_hex = h.hexdigest()

                if drive.already_uploaded_by_sha256(sha256_hex=sha_hex):
                    counts.skipped += 1
                    continue
                if dry_run:
                    counts.skipped += 1
                    continue

                description_obj = {
                    "source": "google_takeout",
                    "takeout": {"zip": zip_path.name, "path": media_name, "metaPath": jn},
                    "meta": meta,
                    "sha256": sha_hex,
                    "takenTimeKstDate": kst_date,
                }
                drive.upload_file(
                    local_path=tmp_path,
                    filename=filename,
                    mime_type=mime_type,
                    parent_folder_id=folder_id,
                    app_properties={
                        "source": "google_takeout",
                        "sha256": sha_hex,
                        "takenKstDate": kst_date,
                    },
                    description_obj=description_obj,
                    policy=RetryPolicy(max_retries=8, base_sleep_s=1.0, max_sleep_s=90.0)
                    if mime_type.startswith("video/")
                    else RetryPolicy(),
                    resumable=mime_type.startswith("video/"),
                )
                counts.uploaded += 1
            except Exception as e:
                counts.failed += 1
                failures.append(json_dumps_compact({"reason": "upload_failed", "file": media_name, "error": repr(e)}))
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())

