#!/usr/bin/env bash
# SaveSync 를 macOS 로그인 시 자동 시작(launchd LaunchAgent)으로 등록/해제한다.
#
# 사용법 (저장소 어디에서든):
#   scripts/macos/install-autostart.sh              # 등록(이미 있으면 갱신) + 즉시 시작
#   scripts/macos/install-autostart.sh --uninstall  # 실행 중지 + 등록 해제
#
# 전제: 저장소 루트에 .venv 가 준비되어 있어야 한다.
#   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
set -euo pipefail

LABEL="com.hyperize.savesync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON="$REPO_DIR/.venv/bin/python"
DATA_DIR="$HOME/.savesync"

if [[ "${1:-}" == "--uninstall" ]]; then
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "해제 완료: $LABEL"
    exit 0
fi

if [[ ! -x "$PYTHON" ]]; then
    echo "오류: $PYTHON 이 없습니다. 먼저 저장소 루트에서 venv 를 만드세요:" >&2
    echo "  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$DATA_DIR"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>-m</string>
        <string>savesync</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$REPO_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$DATA_DIR/launchd-out.log</string>
    <key>StandardErrorPath</key>
    <string>$DATA_DIR/launchd-err.log</string>
</dict>
</plist>
PLIST

# 이미 등록돼 있으면 내리고 새로 올린다 (plist 갱신 반영).
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "등록 완료: $LABEL — 로그인 시 자동 시작되며, 지금 바로 실행되었습니다."
echo "로그: $DATA_DIR/savesync.log (앱), $DATA_DIR/launchd-err.log (구동 오류)"
