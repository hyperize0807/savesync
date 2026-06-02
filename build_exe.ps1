# SaveSync 를 단일 실행파일(.exe)로 빌드한다.
#
# 사용법 (PowerShell):
#   python -m pip install -r requirements-dev.txt
#   .\build_exe.ps1
#
# 결과물: dist\SaveSync.exe  (콘솔 창 없이 트레이 상주)
#
# 참고: 빌드된 exe 도 최초 1회 Google 인증과 credentials.json 이 필요하다.
#       파일은 %APPDATA%\SaveSync 에 저장되므로 exe 와 무관하게 유지된다.

$ErrorActionPreference = "Stop"

Write-Host "SaveSync 실행파일 빌드 시작..." -ForegroundColor Cyan

python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onefile `
    --name SaveSync `
    --collect-all googleapiclient `
    --collect-all google_auth_oauthlib `
    --collect-all google `
    --collect-all pystray `
    --collect-all PIL `
    run_savesync.pyw

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n완료: dist\SaveSync.exe" -ForegroundColor Green
} else {
    Write-Host "`n빌드 실패 (exit $LASTEXITCODE)" -ForegroundColor Red
}
