#!/bin/bash
# AutoLunch — n8n wrapper scripts
# These are called by n8n's Execute Command nodes to avoid shell escaping issues.

set -e
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT"
source .venv/bin/activate

case "$1" in
  decide)
    python -m autolunch.cli decide 2>/dev/null
    ;;
  checkout)
    python -m autolunch.cli checkout --cart-id "$2" 2>/dev/null
    ;;
  reject)
    python -m autolunch.cli reject \
      --restaurant "$2" \
      --item "$3" \
      --cart-id "$4" \
      --net-total "$5" \
      --reason "$6" \
      2>/dev/null
    ;;
  *)
    echo '{"status":"error","message":"Unknown command"}'
    exit 1
    ;;
esac
