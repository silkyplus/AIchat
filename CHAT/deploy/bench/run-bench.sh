#!/usr/bin/env bash
set -euo pipefail
project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
if [[ $# -ne 1 ]]; then echo 'Usage: run-bench.sh config.json' >&2; exit 1; fi
exec "$project_dir/bin/ChatBench" "$1"
