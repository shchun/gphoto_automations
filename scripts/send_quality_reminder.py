from __future__ import annotations

import os
import sys

# Ensure repo root is importable when executed as a script path
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from gphoto_backup.email_utils import SmtpConfig, send_email
from gphoto_backup.utils import kst_today


def _env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise RuntimeError(f"Missing env var: {name}")
    return v


def main() -> int:
    today = kst_today().isoformat()
    smtp = SmtpConfig(
        host=_env("SMTP_HOST"),
        port=int(_env("SMTP_PORT")),
        user=_env("SMTP_USER"),
        password=_env("SMTP_PASSWORD"),
    )
    email_to = _env("EMAIL_TO")

    subject = "[GooglePhotos] 화질 관리 작업 안내"
    body = "\n".join(
        [
            "Google Photos 저장공간 관리 작업 시점입니다.",
            "",
            "1) Google Photos → 저장공간 복구 실행",
            "2) 백업 화질을 다시 Original로 설정",
            "",
            "(이 작업은 수동 수행)",
            f"(기준일: {today} KST)",
            "",
        ]
    )
    send_email(smtp=smtp, to_addrs=email_to, subject=subject, body_text=body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

