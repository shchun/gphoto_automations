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

    subject = f"[GoogleTakeout] Google Photos 백업 안내 ({today})"
    body = "\n".join(
        [
            "Google Photos 자동 Favorites 백업이 API 정책 변경으로 제한되어,",
            "월 1회 Google Takeout으로 내보내기(수동) 후 자동 처리(Drive→백업)를 진행합니다.",
            "",
            "## 1) Google Takeout 생성(수동)",
            "1. Google Takeout 접속: https://takeout.google.com/",
            "2. '선택 해제' 클릭 후 'Google Photos'만 선택",
            "3. '다음 단계' 클릭",
            "4. 전송 방법: 'Drive에 추가' 선택 (권장)",
            "5. 내보내기 빈도: '1회' 또는 '2개월마다'(가능한 경우) 선택",
            "6. 파일 형식: .zip",
            "7. 내보내기 생성",
            "",
            "## 2) 완료 메일 확인",
            "- Gmail로 '데이터가 준비되었습니다 / Takeout'류 메일이 도착하면 완료입니다.",
            "",
            "## 3) Drive에 생성된 Takeout 파일 이동(필수)",
            "아래 폴더(워크플로가 보는 Takeout 소스 폴더)에 Takeout zip을 넣어주세요.",
            "- (관리자 설정) GitHub Secrets의 TAKEOUT_FOLDER_ID",
            "",
            "## 4) 자동 처리(매일 확인)",
            "- GitHub Actions가 매일 Gmail(완료 메일) + Drive(Takeout zip)를 확인합니다.",
            "- 새 Takeout zip이 있으면 Favorites로 표시된 항목만 추출하여",
            "  백업 폴더 구조(촬영일 KST 기준)로 업로드합니다.",
            "",
            "## 결과",
            "- 처리 결과는 GitHub Actions Summary와 이메일로 통보됩니다.",
            "",
            f"(기준일: {today} KST)",
            "",
        ]
    )

    send_email(smtp=smtp, to_addrs=email_to, subject=subject, body_text=body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

