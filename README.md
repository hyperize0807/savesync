# SaveSync

PC 고전게임의 세이브파일을 **Google Drive**와 자동 동기화하는 Windows 트레이 앱.

게임 루트 폴더처럼 실행파일과 세이브가 섞여 있어도 **확장자/패턴 규칙**으로
동기화 대상만 골라낼 수 있고, 덮어쓰기 전에는 **항상 백업**합니다.

## 주요 기능

- 게임별 **프로필**: 로컬 세이브 폴더 ↔ Google Drive 폴더 매핑 (여러 게임 등록 가능)
- **대상 파일 규칙**: 확장자(`.sav, .dat`), 포함 패턴(`save*.*`), 제외 패턴(`*.tmp`), 하위 폴더 포함 여부
- **1시간(설정 가능) 주기 자동 동기화** — 트레이에 상주하는 내부 타이머
- **양방향 동기화**: 로컬에만 있으면 업로드, 드라이브에만 있으면 다운로드
- **충돌 처리**: 양쪽 모두 있고 내용이 다르면 *수정한 날짜*를 비교
  - `수정날짜 최신 우선`(기본) / `항상 로컬 우선` / `항상 드라이브 우선` / `매번 물어보기`
- **덮어쓰기 전 자동 백업**: 사라질 버전을 타임스탬프 폴더에 보관
- 삭제는 전파하지 않음 (세이브 데이터 보호 — 한쪽에서 지워도 복원됨)

## 설치

```powershell
cd savesync
python -m pip install -r requirements.txt
```

요구사항: Python 3.10+ (3.13에서 테스트)

## Google Drive 연결 (최초 1회)

API를 직접 사용하므로 본인의 OAuth 클라이언트가 필요합니다.

1. [Google Cloud Console](https://console.cloud.google.com/) 에서 프로젝트 생성
2. **API 및 서비스 → 라이브러리** 에서 **Google Drive API** 사용 설정
3. **API 및 서비스 → OAuth 동의 화면** 구성 (외부, 테스트 사용자에 본인 계정 추가)
4. **사용자 인증 정보 → 사용자 인증 정보 만들기 → OAuth 클라이언트 ID → 데스크톱 앱**
5. 만들어진 클라이언트의 JSON을 내려받아 아래 위치에 **`credentials.json`** 이름으로 저장:

   ```
   %APPDATA%\SaveSync\credentials.json
   ```

6. 앱 실행 후 **트레이 아이콘 → 설정 → 계정 탭 → "Google Drive에 연결"** 클릭
   → 브라우저 인증을 마치면 `token.json` 이 저장되고 이후 자동 로그인됩니다.

## 실행

```powershell
# 트레이 상주 모드 (기본)
python -m savesync

# 콘솔 없이 실행 (탐색기에서 더블클릭 가능)
run_savesync.pyw

# 1회만 동기화하고 종료 (인증 완료 후)
python -m savesync --sync-once

# 설정 창만 열기
python -m savesync --settings
```

### 부팅 시 자동 시작
`run_savesync.pyw` 의 바로가기를 만들어
`shell:startup` (Win+R → 입력) 폴더에 넣으면 부팅 시 자동 상주합니다.

### 단일 실행파일(.exe)로 빌드
Python 설치 없이 배포하려면 PyInstaller 로 묶을 수 있습니다.

```powershell
python -m pip install -r requirements-dev.txt
.\build_exe.ps1
# 결과물: dist\SaveSync.exe  (콘솔 없이 트레이 상주)
```

빌드된 exe 도 최초 1회 Google 인증·`credentials.json` 이 필요하며,
설정/토큰/백업은 `%APPDATA%\SaveSync` 에 저장되어 exe 와 무관하게 유지됩니다.

## 사용 흐름

1. 트레이 아이콘 더블클릭 → 설정 창
2. **계정** 탭에서 Google Drive 연결
3. **프로필** 탭에서 **추가** → 이름 입력
   - **로컬 세이브 폴더**: `찾기…` 로 선택
   - **드라이브 폴더**: `탐색…` 으로 Drive 폴더를 트리에서 직접 선택하거나,
     URL/ID 를 붙여넣고 `확인`
   - **규칙**: 확장자/패턴 입력 (모두 비우면 폴더 내 모든 파일)
   - `현재 입력을 프로필에 적용`
4. **일반** 탭에서 충돌 정책 / 주기(분) / 백업 설정
5. **저장** → 이후 주기마다 자동 동기화. `지금 동기화` 로 즉시 실행 가능.
6. **로그** 탭에서 최근 실행 로그와 마지막 동기화 결과를 확인할 수 있습니다.
   (`자동(2초)` 으로 실시간 갱신, 오류 발생 시 트레이 알림으로도 통지)

## 데이터 위치 (`%APPDATA%\SaveSync\`)

| 파일/폴더 | 내용 |
|---|---|
| `config.json` | 모든 설정 |
| `credentials.json` | (사용자 제공) OAuth 클라이언트 |
| `token.json` | 인증 토큰 (자동 생성) |
| `backups\<프로필>\<타임스탬프>\` | 덮어쓰기 전 백업 |
| `savesync.log` | 실행 로그 |

## 동기화 동작 상세

| 상태 | 처리 |
|---|---|
| 로컬에만 존재 | 드라이브로 업로드 (백업 불필요) |
| 드라이브에만 존재 | 로컬로 다운로드 (백업 불필요) |
| 양쪽 존재, mtime 거의 동일 | 변경 없음 |
| 양쪽 존재, mtime 다름 | 충돌 정책에 따라 방향 결정 → **대상 백업 후** 덮어쓰기 |

- 업로드 시 Drive 파일의 `modifiedTime` 을 로컬 mtime 으로 맞추고, 다운로드 시
  로컬 mtime 을 Drive 기준으로 맞춥니다. 덕분에 양쪽 "수정한 날짜"를 동일 기준으로
  비교하며, 재실행해도 무한 재동기화가 일어나지 않습니다.
- `매번 물어보기` 정책은 수동(`지금 동기화`)에서만 대화상자로 묻습니다.
  자동(백그라운드) 실행 시에는 안전하게 `수정날짜 최신 우선`으로 처리합니다.

## 테스트

```powershell
python tests/test_logic.py
```

Google 라이브러리 없이도 매칭/충돌/백업 로직을 검증합니다 (가짜 Drive 사용).

## 구조

```
savesync/
  paths.py        # %APPDATA% 경로
  config.py       # 설정(프로필/규칙) 로드·저장
  matcher.py      # 동기화 대상 파일 규칙
  drive.py        # Google Drive API 래퍼
  backup.py       # 덮어쓰기 전 백업
  syncengine.py   # 동기화 결정/실행 (핵심 로직)
  scheduler.py    # 주기 타이머
  gui.py          # Tkinter 설정 창
  tray.py         # 시스템 트레이 앱
  __main__.py     # 진입점
```
