"""Tkinter 기반 설정 창.

탭 구성:
  - 프로필: 게임별 로컬/드라이브 폴더, 파일 규칙
  - 일반: 충돌 정책, 동기화 주기, 백업
  - 계정: Google Drive 인증 상태/연결
"""
from __future__ import annotations

import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import config as config_mod
from .config import (
    Config, Profile, Rules,
    CONFLICT_NEWER, CONFLICT_LOCAL, CONFLICT_DRIVE, CONFLICT_ASK,
)
from .drive import DriveClient, DriveError
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
                 last_result_summary: str = ""):
        self.cfg = cfg
        self.drive = drive
        self.on_save = on_save
        self.on_sync_now = on_sync_now
        self.last_result_summary = last_result_summary
        self._owns_root = master is None

        if master is None:
            self.root = tk.Tk()
        else:
            self.root = tk.Toplevel(master)
        self.root.title("SaveSync 설정")
        self.root.geometry("680x620")

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        self.tab_profiles = ttk.Frame(nb)
        self.tab_general = ttk.Frame(nb)
        self.tab_account = ttk.Frame(nb)
        self.tab_log = ttk.Frame(nb)
        nb.add(self.tab_profiles, text="프로필")
        nb.add(self.tab_general, text="일반")
        nb.add(self.tab_account, text="계정")
        nb.add(self.tab_log, text="로그")

        self._build_profiles_tab()
        self._build_general_tab()
        self._build_account_tab()
        self._build_log_tab()

        bar = ttk.Frame(self.root)
        bar.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(bar, text="지금 동기화", command=self._sync_now).pack(side="left")
        ttk.Button(bar, text="저장", command=self._save).pack(side="right")
        ttk.Button(bar, text="닫기", command=self.root.destroy).pack(side="right", padx=6)

    # ---------------- 프로필 탭 ----------------
    def _build_profiles_tab(self):
        f = self.tab_profiles
        left = ttk.Frame(f)
        left.pack(side="left", fill="y", padx=(4, 8), pady=4)

        ttk.Label(left, text="프로필 목록").pack(anchor="w")
        self.profile_list = tk.Listbox(left, width=24, height=20, exportselection=False)
        self.profile_list.pack(fill="y", expand=True)
        self.profile_list.bind("<<ListboxSelect>>", lambda e: self._load_selected_profile())

        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=4)
        ttk.Button(btns, text="추가", command=self._add_profile).pack(side="left")
        ttk.Button(btns, text="삭제", command=self._del_profile).pack(side="left", padx=4)

        right = ttk.Frame(f)
        right.pack(side="left", fill="both", expand=True, pady=4)

        self.v_name = tk.StringVar()
        self.v_enabled = tk.BooleanVar(value=True)
        self.v_local = tk.StringVar()
        self.v_drive_id = tk.StringVar()
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

        ttk.Label(right, text="드라이브 폴더 (URL/ID)").grid(row=row, column=0, sticky="w")
        ttk.Entry(right, textvariable=self.v_drive_id, width=36).grid(row=row, column=1, sticky="we", pady=2)
        dbtns = ttk.Frame(right)
        dbtns.grid(row=row, column=2, padx=4)
        ttk.Button(dbtns, text="확인", width=5, command=self._verify_drive).pack(side="left")
        ttk.Button(dbtns, text="탐색…", width=6, command=self._browse_drive).pack(side="left", padx=(4, 0))
        row += 1
        ttk.Label(right, textvariable=self.v_drive_name, foreground="#0a7").grid(row=row, column=1, sticky="w")
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
        ttk.Button(right, text="현재 입력을 프로필에 적용", command=self._apply_profile_fields).grid(
            row=row, column=1, sticky="w", pady=8)

        right.columnconfigure(1, weight=1)

        self._refresh_profile_list()
        if self.cfg.profiles:
            self.profile_list.selection_set(0)
            self._load_selected_profile()

    def _refresh_profile_list(self):
        self.profile_list.delete(0, "end")
        for p in self.cfg.profiles:
            mark = "" if p.enabled else " (꺼짐)"
            self.profile_list.insert("end", p.name + mark)

    def _selected_index(self):
        sel = self.profile_list.curselection()
        return sel[0] if sel else None

    def _add_profile(self):
        self.cfg.profiles.append(Profile(name=f"프로필 {len(self.cfg.profiles)+1}"))
        self._refresh_profile_list()
        self.profile_list.selection_clear(0, "end")
        self.profile_list.selection_set("end")
        self._load_selected_profile()

    def _del_profile(self):
        i = self._selected_index()
        if i is None:
            return
        if messagebox.askyesno("삭제", f"'{self.cfg.profiles[i].name}' 프로필을 삭제할까요?"):
            del self.cfg.profiles[i]
            self._refresh_profile_list()

    def _load_selected_profile(self):
        i = self._selected_index()
        if i is None:
            return
        p = self.cfg.profiles[i]
        self.v_name.set(p.name)
        self.v_enabled.set(p.enabled)
        self.v_local.set(p.local_folder)
        self.v_drive_id.set(p.drive_folder_id)
        self.v_drive_name.set(f"→ {p.drive_folder_name}" if p.drive_folder_name else "")
        self.v_exts.set(", ".join(p.rules.include_extensions))
        self.v_incl.set(", ".join(p.rules.include_globs))
        self.v_excl.set(", ".join(p.rules.exclude_globs))
        self.v_recursive.set(p.rules.recursive)

    def _apply_profile_fields(self):
        i = self._selected_index()
        if i is None:
            messagebox.showinfo("알림", "먼저 프로필을 선택하세요.")
            return
        p = self.cfg.profiles[i]
        p.name = self.v_name.get().strip() or p.name
        p.enabled = self.v_enabled.get()
        p.local_folder = self.v_local.get().strip()
        p.drive_folder_id = self.v_drive_id.get().strip()
        p.drive_folder_name = self.v_drive_name.get().replace("→ ", "").strip()
        p.rules = Rules(
            include_extensions=_split_exts(self.v_exts.get()),
            include_globs=_split_csv(self.v_incl.get()),
            exclude_globs=_split_csv(self.v_excl.get()),
            recursive=self.v_recursive.get(),
        )
        self._refresh_profile_list()
        self.profile_list.selection_set(i)
        messagebox.showinfo("적용됨", "프로필에 반영되었습니다. (저장 버튼으로 디스크에 기록)")

    def _browse_local(self):
        d = filedialog.askdirectory(title="로컬 세이브 폴더 선택")
        if d:
            self.v_local.set(d)

    def _verify_drive(self):
        val = self.v_drive_id.get().strip()
        if not val:
            return
        if not self.drive.is_authorized():
            messagebox.showwarning("인증 필요", "먼저 '계정' 탭에서 Google Drive에 연결하세요.")
            return
        try:
            if self.drive.service is None:
                self.drive.connect(run_auth_flow=False)
            fid, name = self.drive.resolve_folder(val)
            self.v_drive_id.set(fid)
            self.v_drive_name.set(f"→ {name}")
        except Exception as e:
            messagebox.showerror("오류", f"드라이브 폴더 확인 실패:\n{e}")

    def _browse_drive(self):
        if not self.drive.is_authorized():
            messagebox.showwarning("인증 필요", "먼저 '계정' 탭에서 Google Drive에 연결하세요.")
            return
        try:
            if self.drive.service is None:
                self.drive.connect(run_auth_flow=False)
        except Exception as e:
            messagebox.showerror("오류", f"드라이브 연결 실패:\n{e}")
            return
        picked = DriveFolderBrowser(self.root, self.drive).pick()
        if picked:
            fid, name = picked
            self.v_drive_id.set(fid)
            self.v_drive_name.set(f"→ {name}")

    # ---------------- 일반 탭 ----------------
    def _build_general_tab(self):
        f = self.tab_general
        pad = {"padx": 10, "pady": 6}

        ttk.Label(f, text="덮어쓰기 충돌 처리 방식", font=("", 9, "bold")).grid(row=0, column=0, sticky="w", **pad)
        self.v_policy = tk.StringVar(value=POLICY_LABELS.get(self.cfg.conflict_policy, POLICY_LABELS[CONFLICT_NEWER]))
        ttk.Combobox(f, textvariable=self.v_policy, state="readonly",
                     values=list(POLICY_LABELS.values()), width=40).grid(row=0, column=1, sticky="w", **pad)

        ttk.Label(f, text="자동 동기화 주기 (분)").grid(row=1, column=0, sticky="w", **pad)
        self.v_interval = tk.IntVar(value=self.cfg.interval_minutes)
        ttk.Spinbox(f, from_=1, to=1440, textvariable=self.v_interval, width=8).grid(row=1, column=1, sticky="w", **pad)

        self.v_backup = tk.BooleanVar(value=self.cfg.backup_enabled)
        ttk.Checkbutton(f, text="덮어쓰기 전 항상 백업", variable=self.v_backup).grid(row=2, column=0, columnspan=2, sticky="w", **pad)

        ttk.Label(f, text="백업 폴더").grid(row=3, column=0, sticky="w", **pad)
        self.v_backup_dir = tk.StringVar(value=self.cfg.backup_dir)
        ttk.Entry(f, textvariable=self.v_backup_dir, width=44).grid(row=3, column=1, sticky="we", **pad)
        ttk.Button(f, text="찾기…", command=self._browse_backup).grid(row=3, column=2, **pad)

        ttk.Label(f, text=f"설정 파일: {paths.config_path()}", foreground="#888").grid(
            row=4, column=0, columnspan=3, sticky="w", **pad)

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
            text=("연결하려면 Google Cloud Console에서 '데스크톱 앱' OAuth 클라이언트를 만들고\n"
                  f"credentials.json 파일을 아래 위치에 두세요:\n{paths.credentials_path()}"),
            foreground="#666", justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", **pad)

    def _refresh_account_status(self):
        if self.drive.is_authorized():
            self.v_account_status.set("상태: 인증됨 ✓")
        elif self.drive.has_credentials():
            self.v_account_status.set("상태: credentials.json 있음 — 아직 인증 전")
        else:
            self.v_account_status.set("상태: credentials.json 없음 — 아래 안내를 따르세요")

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
        self.cfg.interval_minutes = max(1, int(self.v_interval.get()))
        self.cfg.backup_enabled = self.v_backup.get()
        self.cfg.backup_dir = self.v_backup_dir.get().strip() or str(paths.default_backup_dir())

    def _save(self):
        # 현재 편집 중인 프로필 필드도 반영
        if self._selected_index() is not None:
            self._apply_profile_fields_silent()
        self._collect_general()
        config_mod.save(self.cfg)
        if self.on_save:
            self.on_save(self.cfg)
        messagebox.showinfo("저장됨", "설정을 저장했습니다.")

    def _apply_profile_fields_silent(self):
        i = self._selected_index()
        if i is None:
            return
        p = self.cfg.profiles[i]
        p.name = self.v_name.get().strip() or p.name
        p.enabled = self.v_enabled.get()
        p.local_folder = self.v_local.get().strip()
        p.drive_folder_id = self.v_drive_id.get().strip()
        p.drive_folder_name = self.v_drive_name.get().replace("→ ", "").strip()
        p.rules = Rules(
            include_extensions=_split_exts(self.v_exts.get()),
            include_globs=_split_csv(self.v_incl.get()),
            exclude_globs=_split_csv(self.v_excl.get()),
            recursive=self.v_recursive.get(),
        )

    def _sync_now(self):
        self._save_quiet()
        if self.on_sync_now:
            self.on_sync_now()
            messagebox.showinfo("동기화", "동기화를 시작했습니다. (트레이 로그/알림 참고)")

    def _save_quiet(self):
        if self._selected_index() is not None:
            self._apply_profile_fields_silent()
        self._collect_general()
        config_mod.save(self.cfg)
        if self.on_save:
            self.on_save(self.cfg)

    def show(self):
        if self._owns_root:
            self.root.mainloop()
        else:
            self.root.lift()
            self.root.focus_force()


class DriveFolderBrowser:
    """Google Drive 폴더를 트리로 지연 로딩하며 선택하는 다이얼로그."""

    def __init__(self, master, drive: DriveClient):
        self.drive = drive
        self.result: tuple[str, str] | None = None

        self.win = tk.Toplevel(master)
        self.win.title("드라이브 폴더 선택")
        self.win.geometry("440x480")
        self.win.transient(master)
        self.win.grab_set()

        ttk.Label(self.win, text="동기화할 폴더를 선택하세요. ▶ 를 눌러 펼칩니다.").pack(
            anchor="w", padx=8, pady=(8, 4))

        wrap = ttk.Frame(self.win)
        wrap.pack(fill="both", expand=True, padx=8)
        self.tree = ttk.Treeview(wrap, show="tree", selectmode="browse")
        ysb = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ysb.set)
        ysb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        # id -> (folder_id, name, loaded)
        self._nodes: dict[str, list] = {}
        root_node = self.tree.insert("", "end", text="내 드라이브", open=False)
        self._nodes[root_node] = ["root", "내 드라이브", False]
        self.tree.insert(root_node, "end", text="(로딩…)")  # 더미

        self.tree.bind("<<TreeviewOpen>>", self._on_open)

        bar = ttk.Frame(self.win)
        bar.pack(fill="x", padx=8, pady=8)
        ttk.Button(bar, text="이 폴더 선택", command=self._select).pack(side="right")
        ttk.Button(bar, text="취소", command=self.win.destroy).pack(side="right", padx=6)

    def _on_open(self, _event):
        node = self.tree.focus()
        info = self._nodes.get(node)
        if not info or info[2]:  # 이미 로드됨
            return
        folder_id = info[0]
        # 더미 제거
        for child in self.tree.get_children(node):
            self.tree.delete(child)
        try:
            folders = self.drive.list_folders(folder_id)
        except Exception as e:
            messagebox.showerror("오류", f"폴더 목록 로드 실패:\n{e}")
            return
        for fo in folders:
            child = self.tree.insert(node, "end", text=fo["name"])
            self._nodes[child] = [fo["id"], fo["name"], False]
            self.tree.insert(child, "end", text="(로딩…)")  # 더미(펼침 표시용)
        info[2] = True

    def _select(self):
        node = self.tree.focus()
        info = self._nodes.get(node)
        if not info:
            messagebox.showinfo("알림", "폴더를 선택하세요.")
            return
        self.result = (info[0], info[1])
        self.win.destroy()

    def pick(self) -> tuple[str, str] | None:
        self.win.wait_window()
        return self.result


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
