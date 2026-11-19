#!/usr/bin/env bash
# Build a C++ sketch three ways and run each; exit 0 only if all compile and exit 0.
# Usage: check_sketch.sh <sketch.cpp>
set -u
CXX="${CXX:-$HOME/utils/mamba/envs/gcc14/bin/x86_64-conda-linux-gnu-g++}"
TIMEOUT="${SKETCH_TIMEOUT:-120}"
src="${1:?usage: check_sketch.sh <sketch.cpp>}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
status=0

# name, extra flags... ; runs with the env given in RUN_ENV
build_run() {
    local name="$1"; shift
    if ! "$CXX" -std=c++17 -pthread "$@" "$src" -o "$tmp/$name" 2>"$tmp/$name.log"; then
        echo "FAIL $name (compile)"; cat "$tmp/$name.log"; status=1; return
    fi
    env $RUN_ENV timeout "$TIMEOUT" "$tmp/$name" >"$tmp/$name.out" 2>&1
    local rc=$?
    if [ $rc -eq 124 ]; then
        echo "FAIL $name (timeout after ${TIMEOUT}s)"; status=1; return
    elif [ $rc -ne 0 ]; then
        echo "FAIL $name (run)"; cat "$tmp/$name.out"; status=1; return
    fi
    echo "OK $name"
}

RUN_ENV="" build_run werror -O2 -Wall -Wextra -Werror
RUN_ENV="TSAN_OPTIONS=halt_on_error=1" build_run tsan -O1 -g -fsanitize=thread
RUN_ENV="" build_run asan-ubsan -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=all
exit $status
