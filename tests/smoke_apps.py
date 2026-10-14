#!/usr/bin/env python3
"""显式交互验收：仅操作新建临时 Chrome、Evince 和 VS Code 测试窗口。

运行：.venv/bin/python tests/smoke_apps.py --interactive
本脚本会移动真实鼠标，不属于 unittest 自动发现；不会点击助手功能或发 API。
可用 --app chrome/chrome_pdf/evince/vscode 单独复跑，--origin APP:X,Y 校准文字首字符
在测试窗口内的物理像素坐标；截图与 JSON 结果保留在输出的临时目录。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def window_title(window, name_atom, utf8_atom):
    """窗口管理器的客户列表可能短暂包含刚刚关闭的窗口。"""
    from Xlib.error import XError

    try:
        prop = window.get_full_property(name_atom, utf8_atom)
        return prop.value.decode("utf-8", errors="replace") if prop else ""
    except XError:
        return ""


def terminate_created_process(process):
    if process.poll() is not None:
        return True
    try:
        os.killpg(process.pid, signal.SIGTERM)
        return True
    except (PermissionError, ProcessLookupError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interactive", action="store_true", help="明确允许临时窗口和真实鼠标交互")
    parser.add_argument("--app", choices=("all", "chrome", "chrome_pdf", "evince", "vscode"), default="all")
    parser.add_argument("--origin", action="append", default=[], metavar="APP:X,Y")
    parser.add_argument("--drag-length", type=int, default=270)
    args = parser.parse_args()
    if not args.interactive:
        parser.error("交互验收会移动鼠标；请显式添加 --interactive，普通单元测试不要调用此脚本。")

    import cairo
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GLib, Gtk
    from Xlib import X, display, protocol
    from Xlib.ext import xtest
    from selection import SelectionMonitor

    phrase = "vision language action models learn robot policies"
    run_id = uuid.uuid4().hex[:8]
    directory = Path(tempfile.mkdtemp(prefix="selection-assistant-smoke-"))
    origins = {"chrome": (94, 200), "chrome_pdf": (410, 273), "evince": (210, 155), "vscode": (32, 97)}
    for value in args.origin:
        app, coords = value.split(":", 1)
        origins[app] = tuple(int(part) for part in coords.split(","))

    x = display.Display()
    root = x.screen().root
    initial_clients = root.get_full_property(x.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType)
    initial_window_ids = set(initial_clients.value) if initial_clients else set()
    old_focus = x.get_input_focus().focus
    old_pointer = root.query_pointer()
    context = GLib.MainContext.default()
    captures: list[dict] = []
    results: list[dict] = []
    processes: list[subprocess.Popen] = []
    windows = []
    temporary_documents = []
    phase = ""

    def spin(seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            while context.pending():
                context.iteration(False)
            time.sleep(0.01)

    def selected(text, px, py):
        # 只记录本脚本的固定测试词，不记录用户其它窗口的选文。
        if text and text in phrase:
            captures.append({"phase": phase, "text": text, "point": [px, py]})

    monitor = SelectionMonitor(selected, lambda *_: None, lambda: None, lambda: False)

    def message(window, name, data):
        event = protocol.event.ClientMessage(
            window=window, client_type=x.intern_atom(name), data=(32, data)
        )
        root.send_event(event, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
        x.flush()

    def title(window):
        return window_title(window, x.intern_atom("_NET_WM_NAME"), x.intern_atom("UTF8_STRING"))

    def wait_window(marker):
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            prop = root.get_full_property(x.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType)
            for wid in prop.value if prop else []:
                window = x.create_resource_object("window", wid)
                if marker in title(window):
                    return window
            spin(0.1)
        raise RuntimeError(f"未找到新建测试窗口：{marker}")

    def emit(kind, detail=0, px=0, py=0):
        if kind == X.MotionNotify:
            xtest.fake_input(x, kind, x=px, y=py)
        else:
            xtest.fake_input(x, kind, detail=detail)
        x.sync()
        spin(0.045)

    def escape():
        keycode = x.keysym_to_keycode(0xFF1B)
        emit(X.KeyPress, keycode)
        emit(X.KeyRelease, keycode)

    def close(window):
        message(window, "_NET_CLOSE_WINDOW", [X.CurrentTime, 2, 0, 0, 0])
        spin(0.2)

    def write_pdf(path, marker):
        surface = cairo.PDFSurface(str(path), 1000, 650)
        surface.set_metadata(cairo.PDF_METADATA_TITLE, marker)
        canvas = cairo.Context(surface)
        canvas.set_source_rgb(1, 1, 1)
        canvas.paint()
        canvas.set_source_rgb(0, 0, 0)
        canvas.select_font_face("monospace")
        canvas.set_font_size(32)
        canvas.move_to(80, 120)
        canvas.show_text(phrase)
        canvas.show_page()
        surface.finish()

    def prepare(app):
        marker = f"Selection Smoke {app} {run_id}"
        if app == "chrome":
            path = directory / "chrome.html"
            path.write_text(
                f"<!doctype html><title>{marker}</title>"
                "<style>body{margin:70px;font:32px/1.5 monospace;color:#111;background:#fff}</style>"
                f"<p>{phrase}</p><p style='font-size:16px'>Temporary selection test.</p>",
                encoding="utf-8",
            )
            command = ["google-chrome-stable", f"--user-data-dir={directory / 'chrome-profile'}",
                       "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
                       "--disable-component-update", "--disable-sync", "--new-window", path.as_uri()]
        elif app == "chrome_pdf":
            path = directory / f"{marker}.pdf"
            write_pdf(path, marker)
            command = ["google-chrome-stable", f"--user-data-dir={directory / 'chrome-profile'}",
                       "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
                       "--disable-component-update", "--disable-sync", "--new-window", path.as_uri() + "#zoom=75"]
        elif app == "evince":
            document_dir = Path(__file__).resolve().parents[1] / ".runtime"
            document_dir.mkdir(exist_ok=True)
            path = document_dir / f"{marker}.pdf"
            temporary_documents.append(path)
            write_pdf(path, marker)
            command = ["evince", "--new-window", str(path)]
        else:
            profile = directory / "code-profile"
            (profile / "User").mkdir(parents=True)
            settings = {
                "security.workspace.trust.enabled": False,
                "workbench.startupEditor": "none", "workbench.colorTheme": "Default Light Modern",
                "workbench.activityBar.location": "hidden", "workbench.statusBar.visible": False,
                "editor.minimap.enabled": False, "breadcrumbs.enabled": False,
                "editor.stickyScroll.enabled": False, "editor.fontSize": 28,
                "editor.fontFamily": "monospace", "editor.lineNumbers": "off",
                "editor.glyphMargin": False, "editor.folding": False,
                "window.title": marker,
                "telemetry.telemetryLevel": "off", "update.mode": "none",
                "extensions.autoCheckUpdates": False, "extensions.autoUpdate": False,
            }
            (profile / "User" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
            path = directory / "vscode.txt"
            path.write_text(phrase + "\n\nTemporary selection test.\n", encoding="utf-8")
            command = ["code", "--new-window", "--disable-extensions", "--disable-workspace-trust",
                       "--skip-release-notes", "--skip-welcome", "--user-data-dir", str(profile),
                       "--extensions-dir", str(directory / "code-extensions"), str(path)]
        with (directory / f"{app}.log").open("w", encoding="utf-8") as app_log:
            process = subprocess.Popen(command, stdout=app_log, stderr=app_log, start_new_session=True)
        processes.append(process)
        window = wait_window(marker)
        windows.append(window)
        message(window, "_NET_WM_STATE", [0, x.intern_atom("_NET_WM_STATE_MAXIMIZED_VERT"),
                                         x.intern_atom("_NET_WM_STATE_MAXIMIZED_HORZ"), 2, 0])
        window.configure(x=250, y=200, width=1150, height=760)
        message(window, "_NET_ACTIVE_WINDOW", [2, X.CurrentTime, 0, 0, 0])
        spin(2)
        geometry = window.get_geometry()
        origin = root.translate_coords(window, 0, 0)
        scale = Gdk.get_default_root_window().get_scale_factor()
        screenshot = Gdk.pixbuf_get_from_window(
            Gdk.get_default_root_window(), origin.x // scale, origin.y // scale,
            geometry.width // scale, geometry.height // scale,
        )
        screenshot.savev(str(directory / f"{app}.png"), "png", [], [])
        return window, origin

    print(f"交互验收输出：{directory}", flush=True)
    try:
        monitor.start()
        for app in ("chrome", "chrome_pdf", "evince", "vscode") if args.app == "all" else (args.app,):
            try:
                window, origin = prepare(app)
                px, py = origin.x + origins[app][0], origin.y + origins[app][1]
                emit(X.MotionNotify, px=px + 500, py=py + 300)
                emit(X.ButtonPress, 1)
                emit(X.ButtonRelease, 1)
                phase = f"{app}:drag"
                begin = len(captures)
                emit(X.MotionNotify, px=px, py=py)
                emit(X.ButtonPress, 1)
                for step in range(1, 16):
                    emit(X.MotionNotify, px=px + args.drag_length * step // 15, py=py)
                emit(X.ButtonRelease, 1)
                spin(0.5)
                drag = captures[begin:]
                escape()
                spin(0.45)
                phase = f"{app}:double"
                begin = len(captures)
                emit(X.MotionNotify, px=px + 140, py=py)
                for _ in range(2):
                    emit(X.ButtonPress, 1)
                    emit(X.ButtonRelease, 1)
                spin(0.5)
                double = captures[begin:]
                result = {"app": app, "drag": drag, "double": double,
                          "passed": bool(drag and double), "screenshot": str(directory / f"{app}.png")}
                results.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
                escape()
                close(window)
                windows.remove(window)
            except Exception as error:
                result = {"app": app, "passed": False, "error": str(error)}
                results.append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        escape()
        monitor.stop()
        prop = root.get_full_property(x.intern_atom("_NET_CLIENT_LIST"), X.AnyPropertyType)
        for wid in prop.value if prop else []:
            window = x.create_resource_object("window", wid)
            try:
                pid = window.get_full_property(x.intern_atom("_NET_WM_PID"), X.AnyPropertyType)
                if wid not in initial_window_ids and (
                    run_id in title(window) or (pid and pid.value[0] in {p.pid for p in processes})
                ):
                    if window not in windows:
                        windows.append(window)
            except Exception:
                pass
        for window in windows:
            close(window)
        for process in processes:
            # 所有进程均以独立 session 创建；只终止本脚本创建的进程组。
            terminate_created_process(process)
        xtest.fake_input(x, X.MotionNotify, x=old_pointer.root_x, y=old_pointer.root_y)
        try:
            if hasattr(old_focus, "set_input_focus") and old_focus.get_attributes().map_state == X.IsViewable:
                old_focus.set_input_focus(X.RevertToParent, X.CurrentTime)
        except Exception:
            pass
        x.sync()
        x.close()
        for path in temporary_documents:
            path.unlink(missing_ok=True)
        (directory / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if results and all(item["passed"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
