#!/usr/bin/env bash
set -euo pipefail

project_dir="$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"
if [[ ! -x "$project_dir/.venv/bin/python" ]]; then
    printf '未找到项目虚拟环境，请先运行：%s/setup.sh\n' "$project_dir" >&2
    exit 1
fi
cd -- "$project_dir"
exec "$project_dir/.venv/bin/python" "$project_dir/app.py" "$@"
