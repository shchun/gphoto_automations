# gphoto_automations

Google Photos의 미디어를 Google Drive로 백업하는 GitHub Actions 자동화입니다.

## 동작 요약

- **데이터 소스(현재 권장)**: **Google Takeout(수동 생성)** + GitHub Actions 자동 처리
  - 매달 안내 메일로 Takeout 생성 절차를 안내
  - Takeout 완료 메일이 Gmail로 오면(또는 수동 실행), Actions가 Drive의 Takeout zip을 찾아 처리
- **백업 목적지(Drive)**:
  - `GooglePhotoFavorite/YYYY-MM-DD/` (YYYY-MM-DD는 KST 촬영일)
  - 중복 방지(idempotent): Takeout 기반은 `mediaItem.id`를 얻을 수 없어 **sha256 해시 기반**으로 스킵
  - Drive `appProperties`에 `sha256`, `takenKstDate`, `source=google_takeout` 저장
  - Drive `description`에 원본 메타데이터(JSON) 저장
- **실행 모드**
  - `schedule`: 매일 KST 기준 Takeout 완료 여부/Drive zip 확인 후 처리
  - `workflow_dispatch`: 수동 강제 처리/드라이런 지원
- **알림**
  - 백업 완료 시 이메일 통보(실패가 있으면 상단 WARNING)
  - 3개월마다 “화질 관리 작업” 안내 이메일만 발송(백업 스크립트는 실행하지 않음)
  - 매달 “Takeout 생성 안내” 이메일 발송(절차 포함)

## Repo 구조

```
.github/workflows/
  backup.yml
  check_takeout.yml
  remind_takeout.yml
  remind_quality.yml
gphoto_backup/
  auth.py
  photos.py
  drive.py
  email_utils.py
  utils.py
scripts/
  backup_favorites.py
  send_quality_reminder.py
  send_takeout_reminder.py
  get_refresh_token.py
  check_takeout_and_process.py
requirements.txt
```

## Google OAuth 준비 (Refresh Token 발급)

1) Google Cloud Console에서 프로젝트 생성 후 API 활성화
- **Google Drive API** (필수)
- (참고) Google Photos Library API 기반 Favorites 조회는 정책 변경으로 동작하지 않을 수 있어, Takeout 플로우를 권장합니다.

2) OAuth 동의화면(Consent screen) 구성

3) OAuth Client 생성
- **Desktop app** 타입 권장(로컬에서 refresh token 발급 용도)
- 다운로드한 JSON을 예: `client_secret.json` 으로 저장

4) Refresh Token 발급(로컬)

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

python scripts/get_refresh_token.py --client-secrets client_secret.json
```

출력된 JSON에서 다음 값을 사용합니다.
- `refresh_token` → `GOOGLE_REFRESH_TOKEN`
- `client_id` → `GOOGLE_CLIENT_ID`
- `client_secret` → `GOOGLE_CLIENT_SECRET`

## Google Drive 폴더 준비(2개)

- **백업 루트 폴더**: (권장 이름: `GooglePhotoFavorite`) → **폴더 ID**를 `DRIVE_FOLDER_ID`로 설정
- **Takeout 소스 폴더**: Takeout zip을 넣어둘 폴더 → **폴더 ID**를 `TAKEOUT_FOLDER_ID`로 설정

## GitHub Secrets 설정

Repository → Settings → Secrets and variables → Actions → **Secrets** 에 아래를 추가합니다.

- **GOOGLE_CLIENT_ID**
- **GOOGLE_CLIENT_SECRET**
- **GOOGLE_REFRESH_TOKEN**
- **DRIVE_FOLDER_ID**
- **TAKEOUT_FOLDER_ID**
- **EMAIL_TO** (콤마로 여러 수신자 가능)
- **SMTP_HOST**
- **SMTP_PORT**
- **SMTP_USER**
- **SMTP_PASSWORD**

선택(권장): Gmail 완료 메일 감지(IMAP)
- **IMAP_HOST** (기본: `imap.gmail.com`)
- **IMAP_USER** (기본: `SMTP_USER`)
- **IMAP_PASSWORD** (기본: `SMTP_PASSWORD`)
- **IMAP_MAILBOX** (기본: `INBOX`)

## GitHub Actions 스케줄

- **Takeout 처리(매일)**: `.github/workflows/check_takeout.yml`
  - schedule: `40 0 * * *` (UTC) = **09:40 KST**
  - workflow_dispatch: `force`, `dry_run`
- **Takeout 안내(매월)**: `.github/workflows/remind_takeout.yml`
  - schedule: `10 0 1 * *` (UTC) = **09:10 KST**, 매월 1일
- (참고) `.github/workflows/backup.yml`은 과거 Library API 방식이며 현재는 Deprecated 입니다.
- **화질 관리 알림**: `.github/workflows/remind_quality.yml`
  - schedule: `30 0 1 1,4,7,10 *` (UTC) = **09:30 KST**, 분기(1/4/7/10월) 1일

원하는 KST 시간으로 바꾸려면 cron을 UTC로 환산해서 수정하세요.

## Takeout 운영 절차(권장)

1) 매월 안내 메일을 따라 Takeout을 생성합니다.
- Takeout에서 Google Photos만 선택
- 전송 방법은 **Drive에 추가** 권장

2) Drive에 생성된 Takeout zip을 `TAKEOUT_FOLDER_ID` 폴더로 이동합니다.

3) 완료 메일이 Gmail로 도착하면, Actions가 매일 확인 후 자동 처리합니다.

## 로컬 테스트 방법(파서/업로드 테스트)

환경변수 설정 후 실행합니다.

```bash
export GOOGLE_CLIENT_ID="..."
export GOOGLE_CLIENT_SECRET="..."
export GOOGLE_REFRESH_TOKEN="..."
export DRIVE_FOLDER_ID="..."
export TAKEOUT_FOLDER_ID="..."
export EMAIL_TO="me@example.com"
export SMTP_HOST="smtp.example.com"
export SMTP_PORT="587"
export SMTP_USER="smtp-user"
export SMTP_PASSWORD="smtp-pass"

# Gmail 완료 메일 없이 강제 처리(드라이런)
python scripts/check_takeout_and_process.py --force --dry-run
```

## 참고/제약

- Photos API는 “삭제/품질변환” 자동화가 불가하므로, **화질 관리 작업은 이메일 알림만** 수행합니다.
- Photos 날짜 필터는 “날짜” 단위(`dateFilter`)이며, 폴더 분기는 `mediaMetadata.creationTime`을 KST로 변환한 날짜 기준입니다.
- 비디오는 Drive **resumable 업로드**(chunk 업로드)로 처리하며 재시도(backoff)와 타임아웃을 적용합니다.

