#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p build
cc -std=c11 -D_DEFAULT_SOURCE -Wall -Wextra -Werror scripts/pull_edge.c -o build/pull_edge $(pkg-config --cflags --libs libmtp)
