"""앱 아이콘 생성.

레트로 게임 컨트롤러(패미컴 타입) 위에, 동기화를 의미하는
녹색 순환 화살표 배지를 하단에 오버레이한 아이콘을 Pillow 로 그린다.

한 소스에서 세 곳에 동일한 아이콘을 공급한다.
- 트레이 아이콘     : render(64) 가 돌려주는 PIL 이미지
- 설정창/작업표시줄 : runtime_ico_path() 가 만들어 주는 .ico (tkinter iconbitmap)
- exe 빌드          : `python -m savesync.appicon` 로 assets/savesync.ico 생성 후 --icon

모듈을 직접 실행하면(`python -m savesync.appicon`) 저장소의 assets/savesync.ico 를
다시 만든다(빌드용).
"""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

# 디자인을 바꾸면 이 값을 올린다. 런타임 .ico 캐시 파일명에 들어가
# 사용자 PC 의 옛 캐시를 자동으로 무시하게 한다.
ICON_VERSION = 2

# 팔레트
_BODY = (43, 47, 54, 255)        # 컨트롤러 본체 (다크 슬레이트)
_BODY_EDGE = (24, 27, 32, 255)   # 본체 외곽선
_FACE = (54, 59, 68, 255)        # 페이스플레이트(살짝 밝은 인셋)
_DPAD = (20, 22, 26, 255)        # 십자 방향키
_BTN = (217, 68, 54, 255)        # A/B 버튼 (레트로 레드)
_BTN_EDGE = (150, 38, 30, 255)
_PILL = (150, 156, 164, 255)     # 셀렉트/스타트
_BADGE = (255, 255, 255, 255)    # 동기화 배지 바탕
_BADGE_EDGE = (39, 174, 96, 255) # 배지 테두리(녹색)
_ARROW = (39, 174, 96, 255)      # 순환 화살표 (녹색)

_SS = 4  # 슈퍼샘플링 배수 (안티에일리어싱)


def _rrect(d: ImageDraw.ImageDraw, box, r, **kw) -> None:
    d.rounded_rectangle(box, radius=r, **kw)


def _arc_arrowhead(d, cx, cy, R, angle_deg, spin, color, head) -> None:
    """반지름 R 의 원 위 angle_deg 위치에 접선 방향 화살촉을 그린다.

    spin: +1 시계방향 / -1 반시계방향 (화살촉이 가리키는 회전 방향).
    """
    a = math.radians(angle_deg)
    px = cx + R * math.cos(a)
    py = cy + R * math.sin(a)
    ta = a + spin * math.pi / 2          # 접선 방향
    tx, ty = math.cos(ta), math.sin(ta)
    rx, ry = math.cos(a), math.sin(a)    # 반지름 방향
    tip = (px + tx * head * 1.3, py + ty * head * 1.3)
    b1 = (px + rx * head, py + ry * head)
    b2 = (px - rx * head, py - ry * head)
    d.polygon([tip, b1, b2], fill=color)


def _draw(d: ImageDraw.ImageDraw, S: int) -> None:
    """0..S 좌표계에 아이콘을 그린다."""
    def p(v: float) -> float:
        return v * S

    # ── 레트로 컨트롤러 본체 ────────────────────────────────
    body = (p(0.07), p(0.20), p(0.93), p(0.64))
    _rrect(d, body, r=p(0.07), fill=_BODY,
           outline=_BODY_EDGE, width=int(p(0.012)))
    # 살짝 밝은 페이스플레이트 인셋
    face = (p(0.13), p(0.27), p(0.87), p(0.57))
    _rrect(d, face, r=p(0.04), fill=_FACE)

    # 십자 방향키 (왼쪽)
    dcx, dcy = p(0.27), p(0.42)
    arm, th = p(0.085), p(0.060)
    d.rectangle((dcx - th, dcy - arm, dcx + th, dcy + arm), fill=_DPAD)
    d.rectangle((dcx - arm, dcy - th, dcx + arm, dcy + th), fill=_DPAD)

    # 셀렉트 / 스타트 (가운데, 비스듬한 알약 두 개)
    for cxp in (0.45, 0.55):
        px = p(cxp)
        _rrect(d, (px - p(0.035), p(0.45), px + p(0.035), p(0.49)),
               r=p(0.02), fill=_PILL)

    # A / B 버튼 (오른쪽, 빨간 원 두 개)
    br = p(0.058)
    for bx, by in ((0.67, 0.46), (0.80, 0.39)):
        cx, cy = p(bx), p(by)
        d.ellipse((cx - br, cy - br, cx + br, cy + br),
                  fill=_BTN, outline=_BTN_EDGE, width=int(p(0.008)))

    # ── 동기화 배지 (하단 오버레이) ─────────────────────────
    bcx, bcy = p(0.50), p(0.74)
    bR = p(0.235)
    d.ellipse((bcx - bR, bcy - bR, bcx + bR, bcy + bR),
              fill=_BADGE, outline=_BADGE_EDGE, width=int(p(0.018)))

    # 순환 화살표: 안쪽에 두 개의 호 + 화살촉
    aR = p(0.135)              # 화살표 호 반지름
    w = int(p(0.045))          # 호 두께
    abox = (bcx - aR, bcy - aR, bcx + aR, bcy + aR)
    # 위쪽 호 / 아래쪽 호 (양끝에 화살촉 자리 비움)
    d.arc(abox, start=40, end=200, fill=_ARROW, width=w)
    d.arc(abox, start=220, end=380, fill=_ARROW, width=w)
    head = p(0.055)
    _arc_arrowhead(d, bcx, bcy, aR, 200, spin=+1, color=_ARROW, head=head)
    _arc_arrowhead(d, bcx, bcy, aR, 20, spin=+1, color=_ARROW, head=head)


def render(size: int = 256, margin: float = 0.04) -> Image.Image:
    """size×size RGBA 아이콘 이미지를 만든다.

    슈퍼샘플링으로 그린 뒤 실제 그림의 알파 경계로 잘라, 작은 여백(margin)만
    남기고 캔버스에 꽉 차게 맞춘다(트레이/작업표시줄에서 더 크게 보이도록).
    """
    big = size * _SS
    art = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    _draw(ImageDraw.Draw(art), big)

    bbox = art.getbbox()
    if bbox:
        art = art.crop(bbox)

    pad = max(0, int(round(size * margin)))
    inner = max(1, size - 2 * pad)
    w, h = art.size
    scale = inner / max(w, h)
    art = art.resize((max(1, round(w * scale)), max(1, round(h * scale))),
                     Image.LANCZOS)

    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(art, ((size - art.width) // 2, (size - art.height) // 2), art)
    return out


def build_ico(dest: Path,
              sizes=(16, 24, 32, 48, 64, 128, 256)) -> Path:
    """멀티 해상도 .ico 파일을 생성한다."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    base = render(256)
    base.save(dest, format="ICO", sizes=[(s, s) for s in sizes])
    return dest


def runtime_ico_path() -> Path:
    """런타임에 쓸 .ico 경로(없으면 생성). frozen exe 에서도 동작."""
    from . import paths
    p = paths.app_dir() / f"savesync-icon-v{ICON_VERSION}.ico"
    if not p.exists():
        build_ico(p)
    return p


def _repo_ico_path() -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "savesync.ico"


if __name__ == "__main__":
    out = build_ico(_repo_ico_path())
    print(f"아이콘 생성 완료: {out}")
