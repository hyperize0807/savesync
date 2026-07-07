"""Tkinter 기반 설정 창.

탭 구성:
  - 프로필: 게임별 로컬/드라이브 폴더, 파일 규칙
  - 일반: 충돌 정책, 동기화 주기, 백업
  - 계정: Google Drive 인증 상태/연결
"""
from __future__ import annotations

import json
import threading
import tkinter as tk
import webbrowser
from contextlib import contextmanager
from tkinter import filedialog, messagebox, ttk

from . import config as config_mod
from . import updater
from .config import (
    Config, Profile, Rules,
    CONFLICT_NEWER, CONFLICT_LOCAL, CONFLICT_DRIVE, CONFLICT_ASK,
)
from .drive import DriveClient
from . import paths

POLICY_LABELS = {
    CONFLICT_NEWER: "수정날짜가 최신인 파일 (권장)",
    CONFLICT_LOCAL: "항상 로컬 우선 (로컬→드라이브)",
    CONFLICT_DRIVE: "항상 드라이브 우선 (드라이브→로컬)",
    CONFLICT_ASK: "매번 물어보기 (수동 동기화 시)",
}
LABEL_TO_POLICY = {v: k for k, v in POLICY_LABELS.items()}


class SettingsWindow:
    def __init__(self, cfg: Config, drive: DriveClient,
                 on_save=None, on_sync_now=None, master=None,
                 last_result_summary: str = "", on_quit=None):
        self.cfg = cfg
        self.drive = drive
        self.on_save = on_save
        self.on_sync_now = on_sync_now
        self.on_quit = on_quit
        self.last_result_summary = last_result_summary
        self._owns_root = master is None

        if master is None:
            self.root = tk.Tk()
        else:
            self.root = tk.Toplevel(master)
        self.root.title("SaveSync 설정")
        self.root.geometry("680x620")
        try:
            from . import appicon
            self.root.iconbitmap(default=str(appicon.runtime_ico_path()))
        except Exception:
            pass

        # 미저장 변경 추적 상태. 탭/프로필/창을 만들기 전에 초기화해야
        # 초기 자동 선택이 발생시키는 이벤트 핸들러가 안전하게 no-op 된다.
        self._ready = False
        self._suppress_select = False
        self._suppress_tab = False
        self._loaded_index = None
        self._clean_snapshot = None

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=8, pady=8)

        self.tab_profiles = ttk.Frame(self.nb)
        self.tab_general = ttk.Frame(self.nb)
        self.tab_account = ttk.Frame(self.nb)
        self.tab_log = ttk.Frame(self.nb)
        self.nb.add(self.tab_profiles, text="프로필")
        self.nb.add(self.tab_general, text="일반")
        self.nb.add(self.tab_account, text="계정")
        self.nb.add(self.tab_log, text="로그")

        self._build_profiles_tab()
        self._build_general_tab()
        self._build_account_tab()
        self._build_log_tab()

        bar = ttk.Frame(self.root)
        bar.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(bar, text="지금 동기화", command=self._sync_now).pack(side="left")
        ttk.Button(bar, text="저장", command=self._save).pack(side="right")
        ttk.Button(bar, text="닫기", command=self._on_close).pack(side="right", padx=6)

        # 미저장 변경 확인: 프로필 전환 / 탭 이동 / 창 닫기 를 가로챈다.
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self._on_tab_changed())
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._current_tab = self.nb.index("current")
        if self.cfg.profiles:
            self._loaded_index = self._selected_index()
        self._ready = True
        self._mark_clean()

    # ---------------- 프로필 탭 ----------------
    def _build_profiles_tab(self):
        f = self.tab_profiles
        left = ttk.Frame(f)
        left.pack(side="left", fill="y", padx=(4, 8), pady=4)

        ttk.Label(left, text="프로필 목록").pack(anchor="w")
        self.profile_list = tk.Listbox(left, width=24, height=20, exportselection=False)
        self.profile_list.pack(fill="y", expand=True)
        self.profile_list.bind("<<ListboxSelect>>", lambda e: self._on_profile_select())

        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="추가", command=self._add_profile).pack(side="left")
        ttk.Button(btns, text="삭제", command=self._del_profile).pack(side="left", padx=4)

        ttk.Separator(left, orient="horizontal").pack(fill="x", pady=(6, 4))
        ttk.Label(left, text="다른 기기와 목록 공유", foreground="#666").pack(anchor="w")
        cloud = ttk.Frame(left)
        cloud.pack(fill="x", pady=4)
        ttk.Button(cloud, text="드라이브로 내보내기",
                   command=self._export_profiles).pack(fill="x")
        ttk.Button(cloud, text="드라이브에서 가져오기",
                   command=self._import_profiles).pack(fill="x", pady=(4, 0))

        right = ttk.Frame(f)
        right.pack(side="left", fill="both", expand=True, pady=4)

        self.v_name = tk.StringVar()
        self.v_enabled = tk.BooleanVar(value=True)
        self.v_local = tk.StringVar()
        self.v_drive_name = tk.StringVar()
        self.v_exts = tk.StringVar()
        self.v_incl = tk.StringVar()
        self.v_excl = tk.StringVar()
        self.v_recursive = tk.BooleanVar(value=True)

        row = 0
        ttk.Label(right, text="프로필 이름").grid(row=row, column=0, sticky="w")
        ttk.Entry(right, textvariable=self.v_name, width=44).grid(row=row, column=1, columnspan=2, sticky="we", pady=2)
        row += 1
        ttk.Checkbutton(right, text="이 프로필 동기화 활성화", variable=self.v_enabled).grid(row=row, column=1, sticky="w")
        row += 1

        ttk.Label(right, text="로컬 세이브 폴더").grid(row=row, column=0, sticky="w")
        ttk.Entry(right, textvariable=self.v_local, width=36).grid(row=row, column=1, sticky="we", pady=2)
        ttk.Button(right, text="찾기…", command=self._browse_local).grid(row=row, column=2, padx=4)
        row += 1

        ttk.Label(right, text="드라이브 폴더 이름").grid(row=row, column=0, sticky="w")
        ttk.Entry(right, textvariable=self.v_drive_name, width=36).grid(row=row, column=1, columnspan=2, sticky="we", pady=2)
        row += 1
        ttk.Label(right, text="※ Google Drive의 'SaveSync' 폴더 아래에 이 이름으로 폴더가 만들어져 동기화됩니다. (비우면 프로필 이름 사용)",
                  foreground="#888", wraplength=380, justify="left").grid(row=row, column=0, columnspan=3, sticky="w")
        row += 1

        ttk.Separator(right, orient="horizontal").grid(row=row, column=0, columnspan=3, sticky="we", pady=8)
        row += 1
        ttk.Label(right, text="동기화 대상 파일 규칙", font=("", 9, "bold")).grid(row=row, column=0, columnspan=3, sticky="w")
        row += 1

        ttk.Label(right, text="확장자 (쉼표)").grid(row=row, column=0, sticky="w")
        ttk.Entry(right, textvariable=self.v_exts, width=36).grid(row=row, column=1, columnspan=2, sticky="we", pady=2)
        row += 1
        ttk.Label(right, text="예: .sav, .dat, .sgm", foreground="#888").grid(row=row, column=1, sticky="w")
        row += 1

        ttk.Label(right, text="포함 패턴 (쉼표)").grid(row=row, column=0, sticky="w")
        ttk.Entry(right, textvariable=self.v_incl, width=36).grid(row=row, column=1, columnspan=2, sticky="we", pady=2)
        row += 1
        ttk.Label(right, text="예: save*.*, *slot*", foreground="#888").grid(row=row, column=1, sticky="w")
        row += 1

        ttk.Label(right, text="제외 패턴 (쉼표)").grid(row=row, column=0, sticky="w")
        ttk.Entry(right, textvariable=self.v_excl, width=36).grid(row=row, column=1, columnspan=2, sticky="we", pady=2)
        row += 1
        ttk.Label(right, text="예: *.tmp, *.log", foreground="#888").grid(row=row, column=1, sticky="w")
        row += 1

        ttk.Checkbutton(right, text="하위 폴더 포함", variable=self.v_recursive).grid(row=row, column=1, sticky="w", pady=4)
        row += 1
        ttk.Label(right, text="※ 확장자/패턴을 모두 비우면 폴더 내 모든 파일이 대상이 됩니다.",
                  foreground="#888", wraplength=380, justify="left").grid(row=row, column=0, columnspan=3, sticky="w")
        row += 1

        right.columnconfigure(1, weight=1)

        self._refresh_profile_list()
        if self.cfg.profiles:
            self.profile_list.selection_set(0)
            self._load_selected_profile()

    def _refresh_profile_list(self):
        self.profile_list.delete(0, "end")
        for p in self.cfg.profiles:
            mark = "" if p.enabled else " (꺼짐)"
            # 활성 프로필인데 로컬 세이브 폴더가 비어 있으면 경고 표시.
            # (이 상태로는 동기화에서 건너뛰어지므로 사용자가 미리 알 수 있게 한다.)
            missing = p.enabled and not p.has_local_folder()
            if missing:
                mark += " ⚠ 폴더 미지정"
            self.profile_list.insert("end", p.name + mark)
            if missing:
                # 방금 삽입된 항목(마지막 인덱스)을 경고색으로 강조
                self.profile_list.itemconfig(self.profile_list.size() - 1,
                                             foreground="#b00020")

    def _selected_index(self):
        sel = self.profile_list.curselection()
        return sel[0] if sel else None

    def _add_profile(self):
        # 새 프로필로 전환되므로 현재 편집 중인 미저장 변경을 먼저 확인
        if not self._guard_unsaved():
            return
        self.cfg.profiles.append(Profile(name=f"프로필 {len(self.cfg.profiles)+1}"))
        new_i = len(self.cfg.profiles) - 1
        with self._suppressed_select():
            self._refresh_profile_list()
            self._set_profile_selection(new_i)
        self._loaded_index = new_i
        self._load_selected_profile()
        self._mark_clean()

    def _del_profile(self):
        i = self._selected_index()
        if i is None:
            return
        if messagebox.askyesno("삭제", f"'{self.cfg.profiles[i].name}' 프로필을 삭제할까요?"):
            del self.cfg.profiles[i]
            new_i = min(i, len(self.cfg.profiles) - 1) if self.cfg.profiles else None
            with self._suppressed_select():
                self._refresh_profile_list()
                self._set_profile_selection(new_i)
            self._loaded_index = new_i
            if new_i is not None:
                self._load_selected_profile()
            self._mark_clean()

    def _load_selected_profile(self):
        i = self._selected_index()
        if i is None:
            return
        p = self.cfg.profiles[i]
        self.v_name.set(p.name)
        self.v_enabled.set(p.enabled)
        self.v_local.set(p.local_folder)
        self.v_drive_name.set(p.drive_folder_name or p.name)
        self.v_exts.set(", ".join(p.rules.include_extensions))
        self.v_incl.set(", ".join(p.rules.include_globs))
        self.v_excl.set(", ".join(p.rules.exclude_globs))
        self.v_recursive.set(p.rules.recursive)

    # ---------------- 미저장 변경 추적 ----------------
    @contextmanager
    def _suppressed_select(self):
        old = self._suppress_select
        self._suppress_select = True
        try:
            yield
        finally:
            self._suppress_select = old

    @contextmanager
    def _suppressed_tab(self):
        old = self._suppress_tab
        self._suppress_tab = True
        try:
            yield
        finally:
            self._suppress_tab = old

    def _set_profile_selection(self, i):
        self.profile_list.selection_clear(0, "end")
        if i is not None:
            self.profile_list.selection_set(i)
            self.profile_list.activate(i)

    def _capture_form(self):
        """폼(프로필 편집 필드 + 일반 탭)의 현재 값을 스냅샷 튜플로 만든다."""
        def g(var):
            try:
                return var.get()
            except Exception:
                return None
        return (
            g(self.v_name), g(self.v_enabled), g(self.v_local), g(self.v_drive_name),
            g(self.v_exts), g(self.v_incl), g(self.v_excl), g(self.v_recursive),
            g(self.v_policy), g(self.v_auto_sync), g(self.v_interval),
            g(self.v_backup), g(self.v_backup_dir),
        )

    def _mark_clean(self):
        self._clean_snapshot = self._capture_form()

    def _is_dirty(self) -> bool:
        return self._ready and self._capture_form() != self._clean_snapshot

    def _prompt_unsaved(self) -> str:
        """저장/아니오/취소 → 'save' | 'discard' | 'cancel'."""
        ans = messagebox.askyesnocancel(
            "저장되지 않은 변경",
            "변경 사항이 아직 저장되지 않았습니다. 저장하시겠습니까?")
        if ans is None:
            return "cancel"
        return "save" if ans else "discard"

    def _reset_general_from_cfg(self):
        self.v_policy.set(POLICY_LABELS.get(self.cfg.conflict_policy,
                                            POLICY_LABELS[CONFLICT_NEWER]))
        self.v_auto_sync.set(self.cfg.auto_sync_enabled)
        self.v_interval.set(self.cfg.interval_minutes)
        self.v_backup.set(self.cfg.backup_enabled)
        self.v_backup_dir.set(self.cfg.backup_dir)
        self._on_auto_sync_toggle()

    def _reload_form_from_cfg(self):
        """저장된 설정 기준으로 폼 전체를 되돌린다(미저장 변경 폐기)."""
        with self._suppressed_select():
            self._reset_general_from_cfg()
            if self._loaded_index is not None:
                self._set_profile_selection(self._loaded_index)
                self._load_selected_profile()

    def _guard_unsaved(self) -> bool:
        """미저장 변경이 있으면 묻는다. 진행 가능하면 True, '취소'면 False.

        '저장'이면 저장하고, '아니오'면 일반 탭 값을 되돌린다(프로필 편집 필드는
        호출부에서 곧 다시 로드되므로 여기서 건드리지 않는다).
        """
        if not self._is_dirty():
            return True
        ans = self._prompt_unsaved()
        if ans == "cancel":
            return False
        if ans == "save":
            with self._suppressed_select():
                self._save_quiet()
        else:
            with self._suppressed_select():
                self._reset_general_from_cfg()
        return True

    def _on_profile_select(self):
        if not self._ready or self._suppress_select:
            return
        target = self._selected_index()
        if target is None or target == self._loaded_index:
            return
        # 저장 판단은 '이전 프로필' 기준이어야 하므로 선택을 잠시 되돌린다.
        with self._suppressed_select():
            self._set_profile_selection(self._loaded_index)
        if not self._guard_unsaved():
            return  # 취소 → 이전 프로필 유지
        with self._suppressed_select():
            self._set_profile_selection(target)
        self._loaded_index = target
        self._load_selected_profile()
        self._mark_clean()

    def _on_tab_changed(self):
        if not self._ready or self._suppress_tab:
            return
        new_tab = self.nb.index("current")
        if new_tab == self._current_tab:
            return
        if self._is_dirty():
            ans = self._prompt_unsaved()
            if ans == "cancel":
                with self._suppressed_tab():
                    self.nb.select(self._current_tab)
                return
            if ans == "save":
                with self._suppressed_select():
                    self._save_quiet()
            else:
                self._reload_form_from_cfg()
        self._current_tab = new_tab
        self._mark_clean()

    def _on_close(self):
        if self._is_dirty():
            ans = self._prompt_unsaved()
            if ans == "cancel":
                return
            if ans == "save":
                self._save_quiet()
        self.root.destroy()

    def _browse_local(self):
        d = filedialog.askdirectory(title="로컬 세이브 폴더 선택")
        if d:
            self.v_local.set(d)

    # ---------------- 일반 탭 ----------------
    def _build_general_tab(self):
        f = self.tab_general
        pad = {"padx": 10, "pady": 6}

        ttk.Label(f, text="덮어쓰기 충돌 처리 방식", font=("", 9, "bold")).grid(row=0, column=0, sticky="w", **pad)
        self.v_policy = tk.StringVar(value=POLICY_LABELS.get(self.cfg.conflict_policy, POLICY_LABELS[CONFLICT_NEWER]))
        ttk.Combobox(f, textvariable=self.v_policy, state="readonly",
                     values=list(POLICY_LABELS.values()), width=40).grid(row=0, column=1, sticky="w", **pad)

        self.v_auto_sync = tk.BooleanVar(value=self.cfg.auto_sync_enabled)
        ttk.Checkbutton(f, text="자동 동기화 사용 (끄면 '지금 동기화'로 수동 실행만)",
                        variable=self.v_auto_sync, command=self._on_auto_sync_toggle).grid(
            row=1, column=0, columnspan=2, sticky="w", **pad)

        ttk.Label(f, text="자동 동기화 주기 (분)").grid(row=2, column=0, sticky="w", **pad)
        self.v_interval = tk.IntVar(value=self.cfg.interval_minutes)
        self._interval_spin = ttk.Spinbox(f, from_=1, to=1440, textvariable=self.v_interval, width=8)
        self._interval_spin.grid(row=2, column=1, sticky="w", **pad)

        self.v_backup = tk.BooleanVar(value=self.cfg.backup_enabled)
        ttk.Checkbutton(f, text="덮어쓰기 전 항상 백업", variable=self.v_backup).grid(row=3, column=0, columnspan=2, sticky="w", **pad)

        ttk.Label(f, text="백업 폴더").grid(row=4, column=0, sticky="w", **pad)
        self.v_backup_dir = tk.StringVar(value=self.cfg.backup_dir)
        ttk.Entry(f, textvariable=self.v_backup_dir, width=44).grid(row=4, column=1, sticky="we", **pad)
        ttk.Button(f, text="찾기…", command=self._browse_backup).grid(row=4, column=2, **pad)

        ttk.Label(f, text=f"설정 파일: {paths.config_path()}", foreground="#888").grid(
            row=5, column=0, columnspan=3, sticky="w", **pad)

        ttk.Separator(f, orient="horizontal").grid(
            row=6, column=0, columnspan=3, sticky="we", padx=10, pady=(10, 4))
        ttk.Label(f, text=f"현재 버전: v{updater.__version__}").grid(
            row=7, column=0, sticky="w", **pad)
        self._update_btn = ttk.Button(f, text="업데이트 확인", command=self._check_update)
        self._update_btn.grid(row=7, column=1, sticky="w", **pad)

        self._on_auto_sync_toggle()  # 초기 주기 입력란 활성/비활성 반영

    def _on_auto_sync_toggle(self):
        self._interval_spin.configure(state="normal" if self.v_auto_sync.get() else "disabled")

    def _browse_backup(self):
        d = filedialog.askdirectory(title="백업 폴더 선택")
        if d:
            self.v_backup_dir.set(d)

    # ---------------- 계정 탭 ----------------
    def _build_account_tab(self):
        f = self.tab_account
        pad = {"padx": 10, "pady": 8}
        self.v_account_status = tk.StringVar()
        self._refresh_account_status()
        ttk.Label(f, textvariable=self.v_account_status, wraplength=560, justify="left").grid(
            row=0, column=0, columnspan=2, sticky="w", **pad)
        ttk.Button(f, text="Google Drive에 연결 / 재인증", command=self._connect_account).grid(
            row=1, column=0, sticky="w", **pad)
        ttk.Label(
            f,
            text=("'연결'을 누르면 브라우저가 열립니다. Google 로그인 후 동의하면 끝나며,\n"
                  "이후에는 자동으로 로그인됩니다. (동기화 파일은 내 드라이브의 'SaveSync'\n"
                  "폴더 아래에 저장됩니다.)"),
            foreground="#666", justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", **pad)

    def _refresh_account_status(self):
        if self.drive.is_authorized():
            self.v_account_status.set("상태: 인증됨 ✓")
        elif self.drive.has_credentials():
            self.v_account_status.set("상태: 아직 연결 안 됨 — 아래 '연결'을 누르세요")
        else:
            self.v_account_status.set(
                "상태: 앱에 OAuth 클라이언트가 설정되지 않았습니다 (개발자 설정 필요)")

    def _connect_account(self):
        def work():
            try:
                self.drive.connect(run_auth_flow=True)
                self.root.after(0, lambda: messagebox.showinfo("성공", "Google Drive 인증 완료!"))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("오류", f"인증 실패:\n{e}"))
            self.root.after(0, self._refresh_account_status)
        threading.Thread(target=work, daemon=True).start()

    # ---------------- 로그 탭 ----------------
    def _build_log_tab(self):
        f = self.tab_log
        top = ttk.Frame(f)
        top.pack(fill="x", padx=6, pady=6)
        self.v_last_result = tk.StringVar(value="마지막 동기화 결과: (아직 없음)")
        ttk.Label(top, textvariable=self.v_last_result, foreground="#06c").pack(side="left")
        ttk.Button(top, text="새로고침", command=self._refresh_log).pack(side="right")
        ttk.Button(top, text="자동(2초)", command=self._toggle_log_auto).pack(side="right", padx=4)

        wrap = ttk.Frame(f)
        wrap.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.log_text = tk.Text(wrap, wrap="none", height=20, state="disabled",
                                font=("Consolas", 9))
        ysb = ttk.Scrollbar(wrap, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=ysb.set)
        ysb.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

        self._log_auto = False
        self._refresh_log()

    def _refresh_log(self):
        from . import logsetup
        lines = logsetup.get_recent()
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.insert("end", "\n".join(lines[-400:]))
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        if self.last_result_summary:
            self.v_last_result.set(f"마지막 동기화 결과: {self.last_result_summary}")

    def _toggle_log_auto(self):
        self._log_auto = not self._log_auto
        if self._log_auto:
            self._auto_log_tick()

    def _auto_log_tick(self):
        if not self._log_auto:
            return
        try:
            self._refresh_log()
        finally:
            self.root.after(2000, self._auto_log_tick)

    # ---------------- 저장 / 동기화 ----------------
    def _collect_general(self):
        self.cfg.conflict_policy = LABEL_TO_POLICY.get(self.v_policy.get(), CONFLICT_NEWER)
        self.cfg.auto_sync_enabled = self.v_auto_sync.get()
        self.cfg.interval_minutes = max(1, int(self.v_interval.get()))
        self.cfg.backup_enabled = self.v_backup.get()
        self.cfg.backup_dir = self.v_backup_dir.get().strip() or str(paths.default_backup_dir())

    def _save(self):
        # 현재 편집 중인 프로필 필드를 반영하고 목록(이름 등)을 즉시 갱신
        with self._suppressed_select():
            self._apply_and_refresh()
            self._collect_general()
        config_mod.save(self.cfg)
        if self.on_save:
            self.on_save(self.cfg)
        self._mark_clean()
        messagebox.showinfo("저장됨", "설정을 저장했습니다.")

    def _apply_and_refresh(self):
        """편집 중인 프로필 입력을 반영하고 목록(이름 등)을 즉시 갱신한다."""
        i = self._selected_index()
        if i is None:
            return
        self._apply_profile_fields_silent()
        self._refresh_profile_list()
        self.profile_list.selection_set(i)

    def _apply_profile_fields_silent(self):
        i = self._selected_index()
        if i is None:
            return
        p = self.cfg.profiles[i]
        p.name = self.v_name.get().strip() or p.name
        p.enabled = self.v_enabled.get()
        p.local_folder = self.v_local.get().strip()
        p.drive_folder_name = self.v_drive_name.get().strip()
        p.drive_folder_id = ""  # 이름으로 매 동기화 시 해석/생성하므로 캐시를 비운다
        p.rules = Rules(
            include_extensions=_split_exts(self.v_exts.get()),
            include_globs=_split_csv(self.v_incl.get()),
            exclude_globs=_split_csv(self.v_excl.get()),
            recursive=self.v_recursive.get(),
        )

    def _sync_now(self):
        self._save_quiet()
        if self.on_sync_now:
            # on_sync_now 가 실제로 동기화를 시작했을 때만 안내한다.
            # (로컬 폴더 미지정 등으로 막히면 콜백 쪽에서 이미 경고를 띄운다.)
            started = self.on_sync_now()
            if started:
                messagebox.showinfo("동기화", "동기화를 시작했습니다. (트레이 로그/알림 참고)")

    def _save_quiet(self):
        with self._suppressed_select():
            self._apply_and_refresh()
            self._collect_general()
        config_mod.save(self.cfg)
        if self.on_save:
            self.on_save(self.cfg)
        self._mark_clean()

    # ---------------- 프로필 목록 클라우드 공유 ----------------
    def _require_auth(self) -> bool:
        if not self.drive.is_authorized():
            messagebox.showinfo(
                "연결 필요", "먼저 '계정' 탭에서 Google Drive에 연결하세요.")
            return False
        return True

    def _export_profiles(self):
        """프로필 목록을 Google Drive의 SaveSync 루트에 내보낸다."""
        if not self._require_auth():
            return
        with self._suppressed_select():
            self._apply_and_refresh()
        if not self.cfg.profiles:
            messagebox.showinfo("알림", "내보낼 프로필이 없습니다.")
            return

        def work():
            try:
                if self.drive.service is None:
                    self.drive.connect(run_auth_flow=False)
                # 각 프로필의 드라이브 폴더를 보장하고 ID를 수집(이름→ID)
                name_to_id: dict[str, str] = {}
                for p in self.cfg.profiles:
                    folder_name = p.drive_folder_name or p.name
                    name_to_id[p.name] = self.drive.ensure_profile_folder(folder_name)
                self.root.after(0, lambda: self._finish_export(name_to_id))
            except Exception as e:
                self.root.after(
                    0, lambda: messagebox.showerror("오류", f"내보내기 실패:\n{e}"))
        threading.Thread(target=work, daemon=True).start()

    def _finish_export(self, name_to_id: dict[str, str]):
        try:
            for p in self.cfg.profiles:
                if p.name in name_to_id:
                    p.drive_folder_id = name_to_id[p.name]
            payload = config_mod.export_profiles_payload(self.cfg)
            text = json.dumps(payload, ensure_ascii=False, indent=2)
            self.drive.upload_root_text(config_mod.PROFILES_BLOB_NAME, text)
            config_mod.save(self.cfg)
            if self.on_save:
                self.on_save(self.cfg)
            self._mark_clean()
            messagebox.showinfo(
                "내보내기 완료",
                f"{len(self.cfg.profiles)}개 프로필을 드라이브에 저장했습니다.\n"
                "다른 기기에서 '드라이브에서 가져오기'로 불러올 수 있습니다.")
        except Exception as e:
            messagebox.showerror("오류", f"내보내기 실패:\n{e}")

    def _import_profiles(self):
        """Google Drive의 SaveSync 루트에서 프로필 목록을 가져온다."""
        if not self._require_auth():
            return

        def work():
            try:
                if self.drive.service is None:
                    self.drive.connect(run_auth_flow=False)
                text = self.drive.download_root_text(config_mod.PROFILES_BLOB_NAME)
                self.root.after(0, lambda: self._finish_import(text))
            except Exception as e:
                self.root.after(
                    0, lambda: messagebox.showerror("오류", f"가져오기 실패:\n{e}"))
        threading.Thread(target=work, daemon=True).start()

    def _finish_import(self, text: str | None):
        if text is None:
            messagebox.showinfo(
                "알림", "드라이브에 내보낸 프로필 목록이 없습니다.\n"
                "먼저 다른 기기에서 '드라이브로 내보내기'를 실행하세요.")
            return
        try:
            payload = json.loads(text)
            added, updated = config_mod.merge_imported_profiles(self.cfg, payload)
        except Exception as e:
            messagebox.showerror("오류", f"프로필 목록을 읽을 수 없습니다:\n{e}")
            return

        with self._suppressed_select():
            self._refresh_profile_list()
            if self.cfg.profiles:
                self._set_profile_selection(0)
                self._loaded_index = 0
                self._load_selected_profile()
        config_mod.save(self.cfg)
        if self.on_save:
            self.on_save(self.cfg)
        self._mark_clean()

        need_local = [p.name for p in self.cfg.profiles if not p.has_local_folder()]
        msg = f"가져오기 완료 — 추가 {added}개 / 갱신 {updated}개"
        if need_local:
            msg += ("\n\n가져온 프로필은 로컬 세이브 폴더를 각각 지정해야 동기화됩니다.\n"
                    "지정 전까지 아래 프로필은 동기화에서 건너뜁니다:\n - "
                    + "\n - ".join(need_local))
        messagebox.showinfo("가져오기 완료", msg)

    # ---------------- 업데이트 ----------------
    def _set_update_busy(self, busy: bool, label: str = "업데이트 확인"):
        self._update_btn.config(text=label, state=("disabled" if busy else "normal"))

    def _check_update(self):
        """GitHub 최신 릴리스를 확인하고, 새 버전이 있으면 업데이트한다."""
        self._set_update_busy(True, "확인 중…")

        def work():
            try:
                rel = updater.get_latest_release()
                self.root.after(0, lambda: self._on_latest_release(rel))
            except Exception as e:
                self.root.after(0, lambda: self._update_failed("업데이트 확인 실패", e))
        threading.Thread(target=work, daemon=True).start()

    def _update_failed(self, title: str, err: Exception):
        self._set_update_busy(False)
        messagebox.showerror("오류", f"{title}:\n{err}")

    def _on_latest_release(self, rel: dict):
        self._set_update_busy(False)
        if not updater.is_newer(rel.get("tag", ""), updater.__version__):
            messagebox.showinfo(
                "업데이트", f"최신 버전입니다. (v{updater.__version__})")
            return

        tag = rel.get("tag", "")
        # 소스 실행 등 비프로즌: exe 교체 불가 → 릴리스 페이지 안내
        if not updater.is_frozen():
            if messagebox.askyesno(
                    "업데이트 있음",
                    f"새 버전 {tag} 이 있습니다.\n"
                    f"(현재 v{updater.__version__})\n\n"
                    "릴리스 페이지를 열까요?"):
                webbrowser.open(rel.get("html_url", updater.RELEASES_PAGE))
            return

        if not rel.get("exe_url"):
            messagebox.showerror(
                "오류", "릴리스에 SaveSync.exe 가 없어 업데이트할 수 없습니다.")
            return
        if not messagebox.askyesno(
                "업데이트 있음",
                f"새 버전 {tag} 이 있습니다. (현재 v{updater.__version__})\n\n"
                "지금 다운로드하고 업데이트할까요?\n"
                "업데이트하면 앱이 종료된 뒤 새 버전으로 다시 시작됩니다."):
            return

        self._set_update_busy(True, "다운로드 중…")

        def work():
            try:
                new_exe = updater.download_asset(
                    rel["exe_url"], expected_size=rel.get("size") or None)
                self.root.after(0, lambda: self._apply_update(new_exe, rel))
            except Exception as e:
                self.root.after(0, lambda: self._update_failed("다운로드 실패", e))
        threading.Thread(target=work, daemon=True).start()

    def _apply_update(self, new_exe, rel: dict):
        # 먼저 모달로 사용자에게 알린다. (헬퍼를 먼저 띄우면, 이 모달이 떠 있는 동안
        #  헬퍼가 아직 살아있는 앱을 기다리며 헛돌 수 있으므로 spawn 은 종료 직전에 한다.)
        messagebox.showinfo(
            "업데이트", "업데이트를 적용합니다. 앱이 종료된 뒤 자동으로 다시 시작됩니다.")
        try:
            updater.spawn_updater(new_exe)
        except updater.UpdateNotWritableError:
            self._set_update_busy(False)
            if messagebox.askyesno(
                    "권한 필요",
                    "설치 폴더에 쓸 수 없어 자동 교체할 수 없습니다.\n"
                    "(예: Program Files 에 설치된 경우)\n\n"
                    "릴리스 페이지를 열어 직접 받을까요?"):
                webbrowser.open(rel.get("html_url", updater.RELEASES_PAGE))
            return
        except Exception as e:
            self._update_failed("업데이트 적용 실패", e)
            return
        # 헬퍼 .cmd 가 종료를 기다렸다가 교체+재시작하므로 앱을 깔끔히 종료한다.
        if self.on_quit:
            self.on_quit()

    def show(self):
        if self._owns_root:
            self.root.mainloop()
        else:
            self.root.lift()
            self.root.focus_force()


def _split_csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _split_exts(s: str) -> list[str]:
    out = []
    for x in s.split(","):
        x = x.strip().lower()
        if not x:
            continue
        if not x.startswith("."):
            x = "." + x
        out.append(x)
    return out
