## 구현 요약 (2026-02-22)

이 문서는 `main`에 올라간 현재 구현(자동 백업 파이프라인)을 요약하고, 왜 Google Photos Favorites 조회 단계에서 중단되었는지(원인/근거/다음 선택지)를 기록합니다.

---

## 목표(원래 요구사항)

- Google Photos Library API로 **Favorites(즐겨찾기)** 만 조회
- 촬영일(`mediaMetadata.creationTime`)을 **KST(Asia/Seoul)** 로 변환해
  - Drive 폴더: `GooglePhotoFavorite/YYYY-MM-DD/` 로 분기
- 사진/비디오 모두 다운로드 후 Google Drive 업로드
- `mediaItem.id` 기준 **중복 업로드 방지(idempotent)**
- 작업 결과를 GitHub Actions Summary + 이메일로 통보
- 3개월마다 “화질 관리 작업” 이메일 알림만 별도 스케줄로 발송

---

## Repo 구성(현재 `main`)

- **워크플로**
  - `.github/workflows/backup.yml`
    - `workflow_dispatch`: `start_month`, `end_month` (YYYY-MM)
    - `schedule`: 매일 09:15 KST(UTC cron으로 설정) “최근 1개월”
  - `.github/workflows/remind_quality.yml`
    - 분기(1/4/7/10월 1일 09:30 KST) 알림 메일만 발송

- **백업 스크립트**
  - `scripts/backup_favorites.py`
    - 범위 계산: 수동(월 범위) / 자동(최근 N개월)
    - Photos 검색: `mediaItems:search` + `featureFilter: FAVORITES` + `dateFilter.ranges`
    - KST 촬영일 기준 폴더 생성/분기
    - Drive `appProperties`로 `mediaItemId` 저장 후 중복 스킵
    - 이미지: `baseUrl + "=d"`, 비디오: `baseUrl + "=dv"`
    - 비디오 업로드는 Drive resumable 업로드 + 재시도(backoff) 적용
    - 결과 요약: 조회/업로드/스킵/실패 → Actions Summary 출력
    - 이메일 통보: `[GooglePhotoBackup] YYYY-MM-DD 결과`

- **리마인더 스크립트**
  - `scripts/send_quality_reminder.py`
    - 백업 실행 없이 이메일만 전송
    - 제목: `[GooglePhotos] 화질 관리 작업 안내`

- **Google 연동**
  - `gphoto_backup/auth.py`: refresh token 기반 credentials 생성/갱신
  - `gphoto_backup/photos.py`: Photos Library API 호출 + (403 원인 확인을 위한) 응답 본문 포함
  - `gphoto_backup/drive.py`: 날짜 폴더 생성, `appProperties` 기반 중복 체크 및 업로드
  - `gphoto_backup/email_utils.py`: SMTP 발송

---

## 중단된 지점(현재 실패)

Photos Favorites 조회 단계에서 아래 오류로 중단됨:

- `POST https://photoslibrary.googleapis.com/v1/mediaItems:search`
- 오류: `403 PERMISSION_DENIED`
- 메시지: `Request had insufficient authentication scopes.`

이 오류는 단순히 “refresh token에 scope를 안 넣어서”가 아니라,
Google Photos API 정책 변경(2025-03-31 이후 Library API의 읽기 접근 제한) 영향으로
**사용자 전체 라이브러리(즐겨찾기 포함) 검색/조회 방식이 더 이상 기존 Library API scope로 동작하지 않는 상황**과 맞물려 발생함.

특히 Google Photos Library API 문서에서 `mediaItems.search`의 권한 스코프가
`photoslibrary.readonly.appcreateddata`로 제한되는 방향으로 안내되고 있어,
기존 목표였던 “사용자 전체 Favorites를 자동 조회”는 **공식 Library API만으로는 구현이 어려움**.

---

## 현재 구현이 만족하는 부분 / 만족 못하는 부분

- **만족**
  - Drive 폴더 구조/메타데이터(idempotent용 `appProperties`, description JSON)
  - 사진/비디오 다운로드 규칙(`=d`, `=dv`) 및 비디오 resumable 업로드/재시도
  - GitHub Actions Summary / 이메일 통보 / 분기 알림 워크플로

- **미만족(중요)**
  - “Favorites를 Library API로 자동 조회” 요구사항이 현재 API 정책/권한 모델과 충돌

---

## 다음 선택지(재개를 위한 방향)

1) **Picker API로 전환(공식 권장)**
   - 장점: 사용자 라이브러리 접근 가능
   - 단점: 사용자 상호작용(선택)이 필요해 “완전 자동(매일 스케줄)”과 충돌 가능

2) **Library API를 appcreateddata 범위로 축소**
   - 장점: API 호출 자체는 가능해짐
   - 단점: 앱이 만든 미디어만 대상으로 제한되어 “전체 Favorites 백업” 목표를 충족하지 못함

3) **공식 API 외 대안(예: Takeout 기반 수동/반자동 파이프라인)**
   - 장점: 자동 백업 흐름을 유지할 수 있는 여지가 있음
   - 단점: 구현/운영 복잡도 및 제약(정확도/지연/규정/유지보수) 증가

---

## 비고(운영시 주의)

- `DRIVE_FOLDER_ID`는 폴더 “이름”이 아니라 Drive URL의 `folders/<ID>` 값이어야 함.
- `.env`, `secrets/*`는 로컬 전용이며 커밋되지 않도록 `.gitignore`에 반영됨.

