"""콘솔 창 없이 트레이 앱을 실행하는 런처.

탐색기에서 더블클릭하거나, 바로가기를 만들어 Windows 시작프로그램에 등록하면
부팅 시 자동으로 트레이에 상주한다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from savesync.tray import main

if __name__ == "__main__":
    main()
