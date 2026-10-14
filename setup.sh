#!/usr/bin/env bash
set -euo pipefail

project_dir="$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"
skip_system_packages=false
install_desktop=true

usage() {
    cat <<'EOF'
用法：./setup.sh [--skip-system-packages] [--no-desktop]

为 Ubuntu 20.04、22.04、24.04 的 X11 桌面准备运行环境。
默认安装 apt 依赖、创建系统 Python 虚拟环境，并安装当前用户的应用入口。
仅 apt 步骤使用 sudo；安装结束后不会启动应用或开启登录自启。

  --skip-system-packages  跳过 apt；系统依赖须已安装
  --no-desktop            仅准备虚拟环境，不写应用入口（支持 root 容器/CI）
  --help                 显示本说明
EOF
}

for argument in "$@"; do
    case "$argument" in
        --skip-system-packages) skip_system_packages=true ;;
        --no-desktop) install_desktop=false ;;
        --help|-h) usage; exit 0 ;;
        *) printf '未知参数：%s\n' "$argument" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ "$EUID" -eq 0 && "$install_desktop" == true ]]; then
    printf '请用普通桌面用户运行 setup.sh，系统依赖步骤会单独调用 sudo。容器/CI 请加 --no-desktop。\n' >&2
    exit 1
fi

if [[ ! -r /etc/os-release ]]; then
    printf '无法确认系统版本；本安装脚本仅支持 Ubuntu 20.04、22.04、24.04。\n' >&2
    exit 1
fi
target_system="$(. /etc/os-release; printf '%s %s' "$ID" "$VERSION_ID")"
case "$target_system" in
    'ubuntu 20.04'|'ubuntu 22.04'|'ubuntu 24.04') ;;
    *) printf '不支持的系统：%s；仅支持 Ubuntu 20.04、22.04、24.04。\n' "$target_system" >&2; exit 1 ;;
esac

printf '准备 %s 环境；划词功能要求登录 X11 会话。\n' "$target_system"
if [[ "$skip_system_packages" == false ]]; then
    apt_command=(apt-get)
    if [[ "$EUID" -ne 0 ]]; then
        if ! command -v sudo >/dev/null; then
            printf '未找到 sudo。请管理员安装系统依赖后，使用 --skip-system-packages 重试。\n' >&2
            exit 1
        fi
        apt_command=(sudo apt-get)
    fi
    "${apt_command[@]}" update
    if ! "${apt_command[@]}" install -y --no-install-recommends \
        python3 python3-venv python3-gi python3-gi-cairo python3-requests \
        gir1.2-gtk-3.0 gir1.2-secret-1 gir1.2-ayatanaappindicator3-0.1 \
        gnome-keyring gnome-shell-extension-appindicator; then
        printf '系统依赖安装未完成；请检查 apt 错误及 Ubuntu universe 软件源后重试。\n' >&2
        exit 1
    fi
fi

if [[ ! -x /usr/bin/python3 ]]; then
    printf '缺少 Ubuntu 系统 Python：/usr/bin/python3。\n' >&2
    exit 1
fi

venv_dir="$project_dir/.venv"
if [[ -e "$venv_dir" || -L "$venv_dir" ]]; then
    if [[ ! -x "$venv_dir/bin/python" ]]; then
        printf '现有 .venv 不完整，未覆盖。请自行移走该目录后重试：%s\n' "$venv_dir" >&2
        exit 1
    fi
    system_python_info="$(/usr/bin/python3 -c 'import sys; print(sys.prefix, *sys.version_info[:2])')"
    venv_python_info="$("$venv_dir/bin/python" -c 'import sys; print(sys.base_prefix, *sys.version_info[:2])')"
    if [[ "$system_python_info" != "$venv_python_info" ]]; then
        printf '现有 .venv 不属于当前 Ubuntu 系统 Python，未覆盖。请自行移走后重新运行 setup.sh。\n' >&2
        exit 1
    fi
else
    /usr/bin/python3 -m venv --system-site-packages "$venv_dir"
fi

if ! "$venv_dir/bin/python" - <<'PY'
import sys
try:
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Secret', '1')
    gi.require_version('AyatanaAppIndicator3', '0.1')
    from gi.repository import Gtk, Secret, AyatanaAppIndicator3
    import cairo
    import requests
except (ImportError, ValueError) as error:
    print('系统依赖不可用：{}。请安装所需 apt 包，并确认 .venv 使用 --system-site-packages 创建。'.format(error), file=sys.stderr)
    sys.exit(1)
PY
then
    exit 1
fi

"$venv_dir/bin/python" -m pip install --disable-pip-version-check -r "$project_dir/requirements.txt"
if [[ "$install_desktop" == true ]]; then
    "$venv_dir/bin/python" "$project_dir/install.py"
fi
printf '环境准备完成。使用普通桌面用户在 X11 会话运行：%s/run.sh\n' "$project_dir"
printf '首次安装托盘扩展后若图标未出现，请重新登录桌面。登录自启默认关闭。\n'
