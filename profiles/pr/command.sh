#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

copilot_args=()
if [ "${1:-}" = "--allow-all" ]; then
  copilot_args+=("$1")
  shift
fi

usage() {
  cat >&2 <<'USAGE'
usage:
  copilot-container --profile pr create "[extra instruction]"
  copilot-container --profile pr describe "<PR link>" "[extra instruction]"
  copilot-container --profile pr review "<PR link>" "[extra instruction]"
USAGE
}

extra_text() {
  if [ "$#" -gt 0 ]; then
    printf '\n\nAdditional user instruction:\n%s' "$*"
  fi
}

require_pr_link() {
  if [ "$#" -lt 1 ] || [ -z "$1" ]; then
    usage
    exit 2
  fi
}

render_prompt() {
  local template_file="$1"
  local pr_link="$2"
  shift 2

  local prompt
  prompt="$(< "$template_file")"
  prompt="${prompt//\{\{PR_LINK\}\}/${pr_link}}"
  prompt="${prompt//\{\{EXTRA_INSTRUCTIONS\}\}/$(extra_text "$@")}"
  printf '%s' "$prompt"
}

if [ "$#" -lt 1 ]; then
  usage
  exit 2
fi

command="$1"
shift

case "$command" in
  create)
    prompt="$(render_prompt "$SCRIPT_DIR/prompts/create.md" "" "$@")"
    exec copilot "${copilot_args[@]}" --model gpt-6.1-sol -p "$prompt"
    ;;
  describe|description)
    require_pr_link "$@"
    pr_link="$1"
    shift
    prompt="$(render_prompt "$SCRIPT_DIR/prompts/describe.md" "$pr_link" "$@")"
    exec copilot "${copilot_args[@]}" --model gpt-6.1-sol -p "$prompt"
    ;;
  review)
    require_pr_link "$@"
    pr_link="$1"
    shift
    prompt="$(render_prompt "$SCRIPT_DIR/prompts/review.md" "$pr_link" "$@")"
    exec copilot "${copilot_args[@]}" --model gemini-3.8-flash -p "$prompt"
    ;;
  *)
    usage
    exit 2
    ;;
esac
