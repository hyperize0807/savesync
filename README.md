# SaveSync

PC 고전게임의 세이브파일을 **Google Drive**와 자동 동기화하는 트레이 앱 (Windows / macOS).

게임 루트 폴더처럼 실행파일과 세이브가 섞여 있어도 **확장자/패턴 규칙**으로
동기화 대상만 골라낼 수 있고, 덮어쓰기 전에는 **항상 백업**합니다.

## 주요 기능

- 게임별 **프로필**: 로컬 세이브 폴더 ↔ Google Drive 폴더 매핑 (여러 게임 등록 가능)
- **대상 파일 규칙**: 확장자(`.sav, .dat`), 포함 패턴(`save*.*`), 제외 패턴(`*.tmp`), 하위 폴더 포함 여부
- **1시간(설정 가능) 주기 자동 동기화** — 트레이에 상주하는 내부 타이머
  (끄고 `지금 동기화`로 수동 실행만 할 수도 있음)
- **양방향 동기화**: 로컬에만 있으면 업로드, 드라이브에만 있으면 다운로드
- **충돌 처리**: 양쪽 모두 있고 내용이 다르면 *수정한 날짜*를 비교
  - `수정날짜 최신 우선`(기본) / `항상 로컬 우선` / `항상 드라이브 우선` / `매번 물어보기`
- **덮어쓰기 전 자동 백업**: 사라질 버전을 타임스탬프 폴더에 보관
- 삭제는 전파하지 않음 (세이브 데이터 보호 — 한쪽에서 지워도 복원됨)

---

## 빠른 시작 (최종 사용자)

Python 설치가 필요 없습니다. 릴리스 exe 하나면 됩니다.

1. [Releases](../../releases) 에서 최신 **`SaveSync.exe`** 를 내려받습니다.
2. 더블클릭해 실행합니다.

   > **SmartScreen 경고가 뜰 때** — 처음 실행하면 *"Windows의 PC를 보호했습니다"*
   > 파란 창이 나올 수 있습니다. 악성코드가 아니라 **게시자 서명이 없어서** 뜨는
   > 경고이니, **`추가 정보` → `실행`** 을 누르면 됩니다. (백신이 막으면 예외/허용
   > 처리하세요. PyInstaller 로 묶은 exe 특성상 드물게 오탐이 있습니다.)

3. 콘솔 창 없이 **작업표시줄 트레이**에 SaveSync 아이콘이 상주합니다.
4. 트레이 아이콘을 더블클릭해 설정 창을 열고 **① Google Drive 연결 → ② 프로필 등록**
   순서로 진행합니다(아래 두 절 참고).

> **부팅 시 자동 시작**: `SaveSync.exe` 의 바로가기를 만들어 `shell:startup`
> (Win+R 에 입력) 폴더에 넣으면 부팅 시 자동으로 트레이에 상주합니다.

## macOS에서 사용 (소스 실행)

macOS 는 릴리스 실행파일 없이 소스로 실행합니다. 동기화 로직·설정 화면은
Windows 와 동일하며, Windows PC 와 같은 Google 계정으로 연결하면 동일한
Drive 폴더를 통해 이어서 플레이할 수 있습니다.

요구사항: Python 3.10+ 과 Tk (Homebrew 기준 `brew install python-tk`)

```bash
git clone https://github.com/hyperize0807/savesync
cd savesync
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m savesync        # 메뉴 막대(상태바)에 아이콘 상주
```

- **로그인 시 자동 시작**: `scripts/macos/install-autostart.sh` 를 한 번 실행
  (해제는 `--uninstall` 인자 추가)
- **업데이트**: `git pull` 후 앱 재시작. 의존성이 바뀐 경우
  `.venv/bin/pip install -r requirements.txt` 재실행.
- **데이터 위치**: `~/.savesync/` (Windows 의 `%APPDATA%\SaveSync` 에 해당,
  구성 파일 이름은 동일)
- **Windows 와 다른 점**
  - 메뉴 막대 아이콘은 더블클릭 대신 **클릭 → "설정 열기"** 로 엽니다.
  - 인앱 자동 교체 업데이트는 Windows exe 전용입니다. macOS 에서는 새 버전이
    있으면 릴리스 페이지를 열어 주며, 실제 갱신은 `git pull` 로 합니다.

## ① Google Drive 연결 (최초 1회)

설정 창 **계정** 탭 → **"Google Drive에 연결"** 을 누르면 브라우저가 열립니다.
Google 로그인·동의를 마치면 이후 자동 로그인됩니다. (따로 준비할 파일은 없습니다.)

- 동기화 파일은 내 드라이브의 **`SaveSync`** 폴더 아래에 프로필별 폴더로 저장됩니다.
- 앱은 `drive.file` 권한만 사용해 **자기가 만든 이 폴더 외의 파일에는 접근하지
  않습니다** (다른 파일·개인정보 안전).

> 동의 화면에 **"Google에서 확인하지 않은 앱"** 경고가 보이면 `고급 → 계속` 을
> 누르세요. (검수 전 앱이라 그렇습니다. 배포자가 등록한 테스트 계정으로 로그인해야
> 진행됩니다 — 안 된다면 배포자에게 계정 등록을 요청하세요.)

## ② 프로필 등록 / 사용 흐름

1. **프로필** 탭 → **추가** → 이름 입력
   - **로컬 세이브 폴더**: `찾기…` 로 선택
   - **드라이브 폴더 이름**: 내 드라이브 `SaveSync/` 아래에 만들 폴더 이름
     (비우면 프로필 이름 사용)
   - **규칙**: 확장자/패턴 입력 (모두 비우면 폴더 내 모든 파일)
   - `현재 입력을 프로필에 적용`
2. **일반** 탭에서 충돌 정책 / **자동 동기화 사용 여부·주기(분)** / 백업 설정
   - `자동 동기화 사용`을 끄면 주기 타이머가 멈추고, **`지금 동기화`로 수동 실행만**
     하게 됩니다(끈 상태에서도 수동 실행은 동작).
3. **저장** → (자동 동기화가 켜져 있으면) 주기마다 자동 동기화. `지금 동기화` 로
   언제든 즉시 실행 가능.
4. **로그** 탭에서 최근 실행 로그와 **마지막 동기화 시각·결과**를 확인할 수 있습니다.
   (`자동(2초)` 으로 실시간 갱신. **변경이 있었던 프로필**만 이름과 함께 트레이
   알림으로 통지하며, 오류 발생 시에도 알립니다.)

### 다른 기기에서 같은 설정 쓰기 (프로필 목록 공유)

게임마다 폴더 이름을 다시 입력하지 않고 프로필 목록을 그대로 옮길 수 있습니다.

1. 한 기기에서 **프로필** 탭 → **드라이브로 내보내기** — 프로필 목록이 내 드라이브
   `SaveSync/savesync-profiles.json` 에 저장됩니다. (드라이브 폴더 이름/규칙 포함,
   기기별 경로인 로컬 폴더는 제외)
2. 다른 기기에서 같은 Google 계정으로 연결한 뒤 **드라이브에서 가져오기** — 프로필이
   복원됩니다. 같은 이름의 프로필은 갱신되고(로컬 폴더 설정은 보존), 없던 프로필은
   추가됩니다.
3. 가져온 프로필의 **로컬 세이브 폴더만** 이 기기에 맞게 지정하면 바로 동기화됩니다.
   (설정이 필요한 프로필은 가져오기 완료 안내에 표시됩니다.)

## 데이터 위치 (`%APPDATA%\SaveSync\`)

| 파일/폴더 | 내용 |
|---|---|
| `config.json` | 모든 설정 |
| `token.json` | 인증 토큰 (자동 생성) |
| `oauth_client.json` | (선택·개발자용) OAuth 클라이언트 주입 파일 |
| `backups\<프로필>\<타임스탬프>\` | 덮어쓰기 전 백업 |
| `savesync.log` | 실행 로그 |

> 설정/토큰/백업은 모두 여기에 저장되므로, exe 를 새 버전으로 교체해도 그대로
> 유지됩니다.

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

## 업데이트

**앱 안에서 바로 업데이트할 수 있습니다.** 설정 창의 **일반** 탭에서 현재 버전을
확인하고 **`업데이트 확인`** 을 누르면:

- 최신 버전이면 "최신 버전입니다" 안내가 표시됩니다.
- 새 버전이 있으면 확인 후 자동으로 내려받아 교체하고, **앱이 종료된 뒤 새 버전으로
  다시 시작**됩니다.

설정·인증 토큰·백업은 `%APPDATA%\SaveSync` 에 보관되므로 업데이트 후에도 그대로
유지됩니다. 자동 교체가 불가능한 경우(예: `Program Files` 처럼 쓰기 권한이 없는
위치에 설치)에는 [Releases](../../releases) 페이지로 안내하니 직접 받아 덮어쓰면 됩니다.

## 개인정보 처리

- **수집하지 않습니다.** 인증은 사용자의 브라우저와 Google 사이에서 직접
  이루어지며, 개발자나 제3자의 서버를 거치지 않습니다.
- 세이브 파일은 **사용자의 로컬 PC ↔ 사용자 본인의 Google Drive** 사이에서만
  이동합니다. 개발자는 어떤 파일·데이터에도 접근할 수 없습니다.
- 인증 토큰(`token.json`)과 설정은 사용자 PC 의 `%APPDATA%\SaveSync` 에만
  저장됩니다. PC 가 탈취되면 이 토큰이 노출될 수 있으나, 앱은 `drive.file`
  권한만 쓰므로 접근 범위가 **앱이 만든 `SaveSync` 폴더로 한정**됩니다.
- 연결을 해제하려면 [Google 계정 권한 페이지](https://myaccount.google.com/permissions)
  에서 SaveSync 접근 권한을 제거하세요.

## 면책 (No Warranty)

본 소프트웨어는 **"있는 그대로(AS IS)"** 제공되며 어떠한 명시적·묵시적 보증도
하지 않습니다. 동기화 과정에서 발생할 수 있는 데이터 손실·손상에 대해 개발자는
책임지지 않습니다. 앱은 덮어쓰기 전 자동 백업을 수행하지만, **중요한 세이브
데이터는 반드시 별도로 백업**하시고, 모든 사용 책임은 사용자 본인에게 있습니다.

---

# 개발자 / 메인테이너

## 소스에서 실행 (Python)

요구사항: Python 3.10+ (3.13에서 테스트). **exe 사용자는 이 절을 건너뛰세요.**

```powershell
python -m pip install -r requirements.txt

python -m savesync            # 트레이 상주 모드 (기본)
run_savesync.pyw              # 콘솔 없이 실행 (탐색기에서 더블클릭)
python -m savesync --sync-once  # 1회만 동기화하고 종료 (인증 완료 후)
python -m savesync --settings   # 설정 창만 열기
```

## 공유 OAuth 클라이언트 만들기 (배포 전 1회)

릴리스 exe 가 사용자에게 "클릭만으로 연결"되려면 **배포자의 OAuth 클라이언트가
exe 에 내장**되어 있어야 합니다.

1. [Google Cloud Console](https://console.cloud.google.com/) 프로젝트 생성
2. **API 및 서비스 → 라이브러리 → Google Drive API** 사용 설정
3. **OAuth 동의 화면** 구성(외부). 검수 없이 쓰려면 **테스트 사용자**에 사용할
   계정들을 추가(최대 100명). *주의: 테스트 모드에서는 refresh 토큰이 7일마다
   만료되어 재로그인이 필요합니다.*
4. **사용자 인증 정보 → OAuth 클라이언트 ID → 데스크톱 앱** 생성
5. 발급된 `client_id` / `client_secret` 을 주입(상황별 택1):
   - **릴리스 빌드 (권장)**: GitHub 저장소 **Settings → Secrets and variables →
     Actions** 에 `SAVESYNC_OAUTH_CLIENT_ID`, `SAVESYNC_OAUTH_CLIENT_SECRET` 등록.
     CI 가 빌드 직전 `oauth_client.py` 에 값을 주입하므로 소스/공개 저장소에는
     비밀이 남지 않습니다.
   - **로컬 테스트**: 같은 이름의 환경변수, 또는
     `%APPDATA%\SaveSync\oauth_client.json`
     (`{"installed": {"client_id": "...", "client_secret": "..."}}`)
   - 또는 `savesync/oauth_client.py` 상수에 직접 입력(공개 저장소엔 비권장)

## 릴리스 빌드

버전 태그(`vX.Y.Z`)를 push 하면 GitHub Actions 가 Windows 러너에서 exe 를 빌드하고,
위 Secrets 로 OAuth 클라이언트를 주입한 뒤 Release 에 `SaveSync.exe` 를 첨부합니다.

```powershell
git tag v1.0.0
git push origin v1.0.0
# → .github/workflows/release.yml 가 exe 를 빌드해 Release 에 첨부
```

로컬에서 직접 묶으려면(위 환경변수/`oauth_client.json` 으로 클라이언트를 주입한 상태):

```powershell
python -m pip install -r requirements-dev.txt
.\build_exe.ps1
# 결과물: dist\SaveSync.exe  (콘솔 없이 트레이 상주)
```

## 테스트

```powershell
python tests/test_logic.py
```

Google 라이브러리 없이도 매칭/충돌/백업 로직을 검증합니다 (가짜 Drive 사용).

## 구조

```
savesync/
  paths.py        # %APPDATA% 경로
  config.py       # 설정(프로필/규칙) 로드·저장, 프로필 목록 내보내기/병합
  oauth_client.py # 내장 공유 OAuth 클라이언트 (사용자 credentials.json 불필요)
  appicon.py      # 앱 아이콘 생성 (트레이/창/exe 공용 .ico)
  matcher.py      # 동기화 대상 파일 규칙
  drive.py        # Google Drive API 래퍼 (drive.file 스코프)
  backup.py       # 덮어쓰기 전 백업
  syncengine.py   # 동기화 결정/실행 (핵심 로직)
  scheduler.py    # 주기 타이머
  updater.py      # 인앱 자동 업데이트(GitHub 릴리스 확인·교체·재시작)
  gui.py          # Tkinter 설정 창
  tray.py         # 시스템 트레이 앱
  __main__.py     # 진입점
```

> 프로필 목록 공유 파일은 내 드라이브 `SaveSync/savesync-profiles.json` 에 저장됩니다.

## 라이선스

[MIT License](LICENSE) © 2026 hyperize0807

이 앱은 다음 오픈소스에 의존합니다: Google API Client·google-auth(Apache-2.0),
Pillow(HPND), **pystray(LGPL-3.0)**. 재배포 시 각 라이선스 고지를 준수하세요.
