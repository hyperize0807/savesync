# 릴리스 노트

버전별 릴리스 노트를 `v<버전>.md` 파일로 둡니다. (예: `v1.0.5.md`)

## 동작 방식
태그(`v*`)를 push 하면 `.github/workflows/release.yml` 이 실행되어,
`release-notes/<태그>.md` 의 내용을 GitHub Release 본문으로 사용하고
그 아래에 자동 생성된 변경 로그(Full Changelog)를 덧붙입니다.

해당 파일이 없으면 본문 없이(자동 변경 로그만) 릴리스됩니다.

## 릴리스 절차
1. `savesync/__init__.py` 의 `__version__` 을 올린다. (예: `1.0.6`)
2. `release-notes/v1.0.6.md` 에 사람이 읽을 변경 내용을 작성한다.
   - 자동으로 붙는 "Full Changelog" 링크는 적지 않는다(중복 방지).
3. 커밋 후 같은 버전으로 태그를 만들고 push 한다.
   ```
   git tag -a v1.0.6 -m "SaveSync v1.0.6"
   git push origin main v1.0.6
   ```
4. CI(`release.yml`)가 Windows 러너에서 exe 를 빌드해 릴리스에 첨부한다.
   - 태그와 `__version__` 이 다르면 빌드가 실패하니 1번을 먼저 맞춘다.
