#!/usr/bin/env python3
"""安装或卸载当前用户的应用入口，不复制源码或处理密钥。"""

import argparse
import os
from pathlib import Path


APP_ID = "io.github.selectionassistant"
PROJECT = Path(__file__).resolve().parent
USER_DIR = Path.home()
LAUNCHER = USER_DIR / ".local/bin/selection-assistant"
DESKTOP = USER_DIR / f".local/share/applications/{APP_ID}.desktop"
AUTOSTART = USER_DIR / f".config/autostart/{APP_ID}.desktop"
ICON = USER_DIR / f".local/share/icons/hicolor/scalable/apps/{APP_ID}.svg"
SVG = '''<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
  <!-- io.github.selectionassistant -->
  <rect x="6" y="6" width="116" height="116" rx="28" fill="#226b66"/>
  <path d="M28 28h72v54H65L44 101V82H28z" fill="#fff9eb"/>
  <path d="M42 46h44M42 58h32M42 70h22" stroke="#226b66" stroke-width="6" stroke-linecap="round"/>
</svg>
'''


def desktop_value(value: str) -> str:
    """转义 Desktop Entry 字符串值。"""
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


def exec_argument(value: str) -> str:
    """先引用 Exec 参数，再转义 Desktop Entry 字符串层。"""
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return desktop_value('"' + value.replace("%", "%%") + '"')


def project_marker(project=None) -> str:
    return f"X-SelectionAssistant-Project={desktop_value(str(PROJECT if project is None else project))}"


def desktop_entry(project=None, autostart=False) -> str:
    project = PROJECT if project is None else Path(project)
    command = exec_argument(str(project / 'run.sh'))
    if autostart:
        command += ' --background'
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.0",
        "Name=划词助手",
        "Comment=划词翻译、VLA 与编程解释及连续追问",
        f"Exec={command}",
        f"Path={desktop_value(str(project))}",
        f"Icon={APP_ID}",
        "Terminal=false",
        "Categories=Utility;Education;",
        "StartupNotify=false",
        *(["X-GNOME-Autostart-enabled=true"] if autostart else []),
        project_marker(project),
        "",
    ])


def owned_desktop(path: Path, project=None) -> bool:
    return (not path.is_symlink() and path.is_file()
            and project_marker(project) in path.read_text(encoding="utf-8").splitlines())


def owned_launcher() -> bool:
    return LAUNCHER.is_symlink() and Path(os.readlink(str(LAUNCHER))) == PROJECT / "run.sh"


def install() -> None:
    for required in (PROJECT / "run.sh", PROJECT / "app.py", PROJECT / ".venv/bin/python"):
        if not required.is_file():
            raise SystemExit(f"缺少运行文件：{required}，请先完成本机环境准备。")
    if (DESKTOP.exists() or DESKTOP.is_symlink()) and not owned_desktop(DESKTOP):
        raise SystemExit(f"未覆盖其它来源的应用入口：{DESKTOP}")
    if (LAUNCHER.exists() or LAUNCHER.is_symlink()) and not owned_launcher():
        raise SystemExit(f"未覆盖其它来源的命令入口：{LAUNCHER}")
    if ICON.is_symlink() or (ICON.exists() and (not ICON.is_file() or ICON.read_text(encoding="utf-8") != SVG)):
        raise SystemExit(f"未覆盖其它来源的图标：{ICON}")

    for directory in (DESKTOP.parent, LAUNCHER.parent, ICON.parent):
        directory.mkdir(parents=True, exist_ok=True)
    (PROJECT / "run.sh").chmod(0o755)
    DESKTOP.write_text(desktop_entry(), encoding="utf-8")
    ICON.write_text(SVG, encoding="utf-8")
    if not LAUNCHER.is_symlink():
        LAUNCHER.symlink_to(PROJECT / "run.sh")
    print(f"已安装当前用户的应用入口：{DESKTOP}")
    print(f"可在应用列表搜索“划词助手”，或运行：{LAUNCHER}")
    print("默认未启用登录自启；请在应用设置中按需开启。")


def uninstall() -> None:
    for desktop in (DESKTOP, AUTOSTART):
        if owned_desktop(desktop):
            desktop.unlink()
            print(f"已移除：{desktop}")
    if owned_launcher():
        LAUNCHER.unlink()
        print(f"已移除：{LAUNCHER}")
    if not ICON.is_symlink() and ICON.is_file() and ICON.read_text(encoding="utf-8") == SVG:
        ICON.unlink()
        print(f"已移除：{ICON}")
    print("卸载入口完成。源码、普通配置和密钥环中的 API Key 已保留。")
    print("正在运行的程序不会被终止，请从托盘菜单退出。")


def main() -> None:
    parser = argparse.ArgumentParser(description="划词助手：当前用户的应用入口安装工具")
    parser.add_argument("--uninstall", action="store_true", help="移除本项目的应用入口、图标和登录自启入口")
    args = parser.parse_args()
    if args.uninstall:
        uninstall()
    else:
        install()


if __name__ == "__main__":
    main()
