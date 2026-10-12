#!/usr/bin/env bash
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
private_dir="$project_dir/.runtime/nginx"
run_dir=${CHAT_NGINX_RUN_DIR:-"$project_dir/.runtime/nginx-chat"}
if [[ "$run_dir" != /* ]]; then echo 'CHAT_NGINX_RUN_DIR must be absolute.' >&2; exit 1; fi
nginx_bin="$private_dir/usr/sbin/nginx"
stream_module="$private_dir/usr/lib/nginx/modules/ngx_stream_module.so"
command_name=${1:-status}
if [[ $# -gt 1 ]]; then echo 'Usage: chat-nginx.sh setup|test|start|status|reload|stop|quit' >&2; exit 1; fi

if [[ "$command_name" == setup ]]; then
    if [[ -x "$nginx_bin" && -f "$stream_module" ]]; then
        "$nginx_bin" -v
        echo "Project-private Nginx already installed: $private_dir"
        exit 0
    fi
    mkdir -p "$private_dir/packages"
    (
        cd "$private_dir/packages"
        # Download distribution packages without installing system services or requiring sudo.
        apt-get download nginx libnginx-mod-stream
        for package in nginx_*.deb libnginx-mod-stream_*.deb; do
            dpkg-deb --extract "$package" "$private_dir"
        done
    )
    "$nginx_bin" -v
    echo "Installed project-private Nginx: $private_dir"
    exit 0
fi
if [[ ! -x "$nginx_bin" || ! -f "$stream_module" ]]; then
    echo 'Run ./deploy/nginx/chat-nginx.sh setup first.' >&2; exit 1
fi
mkdir -p "$run_dir"
# Only signal the master started with this project's prefix; never act on a reused PID.
is_running() {
    [[ -f "$run_dir/nginx.pid" ]] || return 1
    local master_pid master_exe master_args
    read -r master_pid < "$run_dir/nginx.pid" || return 1
    [[ "$master_pid" =~ ^[0-9]+$ ]] || return 1
    kill -0 "$master_pid" 2>/dev/null || return 1
    master_exe=$(readlink "/proc/$master_pid/exe" 2>/dev/null) || return 1
    master_args=$(tr '\0' ' ' < "/proc/$master_pid/cmdline") || return 1
    [[ "$master_exe" == "$nginx_bin" && "$master_args" == *"$run_dir/"* ]]
}
render_and_test() {
    local listen_port=${CHAT_NGINX_PORT:-16016}
    local backend_a=${CHAT_NGINX_BACKEND_A_PORT:-16014}
    local backend_b=${CHAT_NGINX_BACKEND_B_PORT:-16015}
    local port
    for port in "$listen_port" "$backend_a" "$backend_b"; do
        if [[ ! "$port" =~ ^[1-9][0-9]{0,4}$ ]] || ((10#$port > 65535)); then
            echo "Invalid TCP port: $port" >&2; return 1
        fi
    done
    if [[ "$listen_port" == "$backend_a" || "$listen_port" == "$backend_b" || "$backend_a" == "$backend_b" ]]; then
        echo 'Listen and both backend ports must be distinct.' >&2; return 1
    fi
    local strategy=${CHAT_NGINX_STRATEGY:-round_robin}
    local balance_directive='# default round robin'
    case "$strategy" in
        round_robin) ;;
        least_conn) balance_directive='least_conn;' ;;
        *) echo 'CHAT_NGINX_STRATEGY must be round_robin or least_conn.' >&2; return 1 ;;
    esac
    local temporary
    temporary=$(mktemp "$run_dir/config.XXXXXX")
    # Escape sed replacement metacharacters in filesystem paths.
    local escaped_run escaped_module
    escaped_run=$(printf '%s' "$run_dir" | sed 's/[\\&|]/\\&/g')
    escaped_module=$(printf '%s' "$stream_module" | sed 's/[\\&|]/\\&/g')
    sed -e "s|@RUN_DIR@|$escaped_run|g" -e "s|@STREAM_MODULE@|$escaped_module|g" \
        -e "s|@LISTEN_PORT@|$listen_port|g" -e "s|@BACKEND_A@|$backend_a|g" \
        -e "s|@STRATEGY@|$strategy|g" -e "s|@BALANCE_DIRECTIVE@|$balance_directive|g" \
        -e "s|@BACKEND_B@|$backend_b|g" "$project_dir/deploy/nginx/nginx.conf.in" > "$temporary"
    if ! "$nginx_bin" -p "$run_dir/" -c "$temporary" -t; then rm -f "$temporary"; return 1; fi
    if [[ "$command_name" == test ]]; then rm -f "$temporary"; else mv "$temporary" "$run_dir/nginx.conf"; fi
}
case "$command_name" in
    test) render_and_test ;;
    start)
        if is_running; then echo 'Project Nginx already running.'; exit 0; fi
        if [[ -f "$run_dir/nginx.pid" ]]; then
            read -r previous_pid < "$run_dir/nginx.pid" || previous_pid=""
            if [[ "$previous_pid" =~ ^[0-9]+$ ]] && kill -0 "$previous_pid" 2>/dev/null; then
                echo 'PID file belongs to a different process; refusing to start.' >&2; exit 1
            fi
        fi
        render_and_test
        "$nginx_bin" -p "$run_dir/" -c "$run_dir/nginx.conf"
        echo "Started project Nginx; configuration: $run_dir/nginx.conf"
        ;;
    status)
        if is_running; then echo "Project Nginx running, PID $(cat "$run_dir/nginx.pid")"; else echo 'Project Nginx is not running.'; exit 1; fi
        ;;
    reload)
        if ! is_running; then echo 'Project Nginx is not running.' >&2; exit 1; fi
        render_and_test
        "$nginx_bin" -p "$run_dir/" -c "$run_dir/nginx.conf" -s reload
        ;;
    stop|quit)
        if ! is_running; then echo 'Project Nginx is not running; no process signalled.'; exit 0; fi
        "$nginx_bin" -p "$run_dir/" -c "$run_dir/nginx.conf" -s "$command_name"
        echo "Sent $command_name to project Nginx."
        ;;
    *) echo 'Usage: chat-nginx.sh setup|test|start|status|reload|stop|quit' >&2; exit 1 ;;
esac
