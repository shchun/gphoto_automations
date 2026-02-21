# gphoto_automations

Google Photos의 **Favorites(즐겨찾기)** 미디어(사진+비디오)를 Google Drive로 백업하는 GitHub Actions 자동화입니다.

## 동작 요약

- **데이터 소스**: Google Photos Library API `mediaItems:search`
  - `featureFilter.includedFeatures = FAVORITES`
  - `dateFilter.ranges`로 기간 제한
  - 폴더 분기는 반드시 `mediaMetadata.creationTime`(촬영일) 기준이며, **KST(Asia/Seoul)** 로 변환한 날짜를 사용합니다.
- **백업 목적지(Drive)**:
  - `GooglePhotoFavorite/YYYY-MM-DD/` (YYYY-MM-DD는 KST 촬영일)
  - 중복 방지(idempotent): 동일 `mediaItem.id`는 업로드 스킵
  - Drive `appProperties`에 `mediaItemId`, `creationTime`, `mimeType` 저장
  - Drive `description`에 원본 메타데이터(JSON) 저장
- **실행 모드**
  - `workflow_dispatch`: `start_month`, `end_month` (YYYY-MM) 범위 처리
  - `schedule`: 매일 KST 기준 **최근 1개월** 범위 처리 (cron은 UTC로 설정됨)
- **알림**
  - 백업 완료 시 이메일 통보(실패가 있으면 상단 WARNING)
  - 3개월마다 “화질 관리 작업” 안내 이메일만 발송(백업 스크립트는 실행하지 않음)

## Repo 구조

```
.github/workflows/
  backup.yml
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
  get_refresh_token.py
requirements.txt
```

## Google OAuth 준비 (Refresh Token 발급)

1) Google Cloud Console에서 프로젝트 생성 후 API 활성화
- **Google Photos Library API**
- **Google Drive API**

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

## Google Drive 폴더 준비

Drive에 폴더를 하나 만들고(권장 이름: `GooglePhotoFavorite`) 그 **폴더 ID**를 `DRIVE_FOLDER_ID`로 설정합니다.

## GitHub Secrets 설정

Repository → Settings → Secrets and variables → Actions → **Secrets** 에 아래를 추가합니다.

- **GOOGLE_CLIENT_ID**
- **GOOGLE_CLIENT_SECRET**
- **GOOGLE_REFRESH_TOKEN**
- **DRIVE_FOLDER_ID**
- **EMAIL_TO** (콤마로 여러 수신자 가능)
- **SMTP_HOST**
- **SMTP_PORT**
- **SMTP_USER**
- **SMTP_PASSWORD**

## GitHub Actions 스케줄

- **백업**: `.github/workflows/backup.yml`
  - schedule: `15 0 * * *` (UTC) = **09:15 KST**
  - workflow_dispatch: `start_month`, `end_month` 입력 (YYYY-MM)
- **화질 관리 알림**: `.github/workflows/remind_quality.yml`
  - schedule: `30 0 1 1,4,7,10 *` (UTC) = **09:30 KST**, 분기(1/4/7/10월) 1일

원하는 KST 시간으로 바꾸려면 cron을 UTC로 환산해서 수정하세요.

## 로컬 테스트 방법

환경변수 설정 후 실행합니다.

```bash
export GOOGLE_CLIENT_ID="..."
export GOOGLE_CLIENT_SECRET="..."
export GOOGLE_REFRESH_TOKEN="..."
export DRIVE_FOLDER_ID="..."
export EMAIL_TO="me@example.com"
export SMTP_HOST="smtp.example.com"
export SMTP_PORT="587"
export SMTP_USER="smtp-user"
export SMTP_PASSWORD="smtp-pass"

# 최근 1개월
python scripts/backup_favorites.py --recent-months 1

# 월 범위(포함)
python scripts/backup_favorites.py --start-month 2026-01 --end-month 2026-02

# 업로드 없이 조회만
python scripts/backup_favorites.py --recent-months 1 --dry-run
```

## 참고/제약

- Photos API는 “삭제/품질변환” 자동화가 불가하므로, **화질 관리 작업은 이메일 알림만** 수행합니다.
- Photos 날짜 필터는 “날짜” 단위(`dateFilter`)이며, 폴더 분기는 `mediaMetadata.creationTime`을 KST로 변환한 날짜 기준입니다.
- 비디오는 Drive **resumable 업로드**(chunk 업로드)로 처리하며 재시도(backoff)와 타임아웃을 적용합니다.

