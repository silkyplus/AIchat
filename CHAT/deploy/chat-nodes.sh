#!/usr/bin/env bash
# Manage only this project's ChatServer processes; no sudo and no kill -9 fallback.
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
server_bin="$project_dir/bin/ChatServer"
run_dir=${CHAT_NODES_RUN_DIR:-"$project_dir/.runtime/chat-nodes"}
action=${1:-status}
selection=${2:-all}
if [[ $# -gt 2 || ! "$action" =~ ^(start|stop|restart|status)$ || ! "$selection" =~ ^(a|b|all)$ || "$run_dir" != /* ]]; then
    echo 'Usage: chat-nodes.sh start|stop|restart|status [a|b|all]; run directory must be absolute.' >&2; exit 1
fi
mkdir -p "$run_dir"
exec 9>"$run_dir/control.lock"
flock -x 9
node_a=${CHAT_NODE_A_ID:-course-group-node-a}
node_b=${CHAT_NODE_B_ID:-course-group-node-b}
port_a=${CHAT_NODE_A_PORT:-16014}
port_b=${CHAT_NODE_B_PORT:-16015}
for node in "$node_a" "$node_b"; do
    if [[ ! "$node" =~ ^[a-zA-Z0-9_-]{1,64}$ ]]; then echo 'Invalid node ID.' >&2; exit 1; fi
done
for port in "$port_a" "$port_b"; do
    if [[ ! "$port" =~ ^[1-9][0-9]{0,4}$ ]] || ((10#$port > 65535)); then echo 'Invalid node port.' >&2; exit 1; fi
done
if [[ "$node_a" == "$node_b" || "$port_a" == "$port_b" ]]; then echo 'Nodes require distinct IDs and ports.' >&2; exit 1; fi

port_open() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
# Globals set by select_node; read only PID/executable/args/node identity, never print environment.
select_node() {
    if [[ "$1" == a ]]; then node="$node_a"; port="$port_a"; else node="$node_b"; port="$port_b"; fi
    pid_file="$run_dir/$node.pid"; log_file="$run_dir/$node.log"
}
load_pid() {
    pid=''
    [[ -f "$pid_file" ]] && read -r pid < "$pid_file" || return 1
    [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null || return 1
    [[ "$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null)" != Z ]] || return 1
}
owned_pid() {
    load_pid || return 1
    [[ "$(readlink "/proc/$pid/exe" 2>/dev/null)" == "$server_bin" ]] || return 1
    local -a arguments
    mapfile -d '' -t arguments < "/proc/$pid/cmdline" || return 1
    # argv[0] may be ./bin/ChatServer for nodes launched manually from the project.
    [[ ${#arguments[@]} -eq 2 && "${arguments[1]}" == "$port" ]] || return 1
    tr '\0' '\n' < "/proc/$pid/environ" | grep -Fxq "CHAT_SERVER_ID=$node"
}
start_node() {
    if owned_pid; then
        if port_open "$port"; then echo "$node already running, PID $pid, port $port"; return 0; fi
        echo "$node process exists but port is not ready; inspect $log_file" >&2; return 1
    fi
    if load_pid; then echo "$node PID file refers to an unrelated live process; refusing to start." >&2; return 1; fi
    if port_open "$port"; then echo "Port $port is occupied by an unmanaged process; refusing to start." >&2; return 1; fi
    if [[ ! -x "$server_bin" ]]; then echo 'Build bin/ChatServer first.' >&2; return 1; fi
    if [[ ! ${CHAT_DB_PASSWORD+x} ]]; then echo 'Set CHAT_DB_PASSWORD before starting a node.' >&2; return 1; fi
    CHAT_SERVER_ID="$node" CHAT_REDIS_PREFIX="${CHAT_REDIS_PREFIX:-course133:user:}" \
        nohup "$server_bin" "$port" >> "$log_file" 2>&1 < /dev/null 9>&- &
    pid=$!
    printf '%s\n' "$pid" > "$pid_file.tmp" && mv "$pid_file.tmp" "$pid_file" || return 1
    for ((attempt=0; attempt<100; ++attempt)); do
        if ! owned_pid; then
            # nohup may briefly still be exec'ing; check liveness before declaring startup failure.
            if ! load_pid; then echo "$node failed to start; inspect $log_file" >&2; return 1; fi
        elif port_open "$port"; then echo "$node started, PID $pid, port $port"; return 0; fi
        sleep 0.1
    done
    echo "$node startup timed out; process left for inspection, log: $log_file" >&2; return 1
}
stop_node() {
    if ! owned_pid; then
        if load_pid; then echo "$node PID belongs to an unrelated process; no signal sent." >&2; return 1; fi
        if port_open "$port"; then echo "$node port is occupied by an unmanaged process; no signal sent." >&2; return 1; fi
        echo "$node is not running."; return 0
    fi
    kill -TERM "$pid" || return 1
    for ((attempt=0; attempt<100; ++attempt)); do
        if ! owned_pid; then
            # Do not unlink a PID written by a concurrent manager (the flock also serializes managers).
            rm -f "$pid_file"; echo "$node stopped with SIGTERM."; return 0
        fi
        sleep 0.1
    done
    echo "$node has not exited; no SIGKILL sent. Inspect $log_file" >&2; return 1
}
status_node() {
    if owned_pid; then
        if port_open "$port"; then echo "$node RUNNING PID=$pid PORT=$port"; return 0; fi
        echo "$node NOT_READY PID=$pid PORT=$port"; return 1
    fi
    if load_pid; then echo "$node UNRELATED_PID PORT=$port"; return 1; fi
    if port_open "$port"; then echo "$node UNMANAGED_LISTENER PORT=$port"; return 1; fi
    echo "$node STOPPED PORT=$port"; return 1
}
result=0
if [[ "$selection" == all ]]; then targets=(a b); else targets=("$selection"); fi
for target in "${targets[@]}"; do
    select_node "$target"
    case "$action" in
        start) if ! start_node; then result=1; fi ;;
        stop) if ! stop_node; then result=1; fi ;;
        restart) if stop_node; then if ! start_node; then result=1; fi; else result=1; fi ;;
        status) if ! status_node; then result=1; fi ;;
    esac
done
exit "$result"
