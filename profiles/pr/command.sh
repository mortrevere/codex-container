#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

codex_args=(--profile container --dangerously-bypass-approvals-and-sandbox --dangerously-bypass-hook-trust -c 'cli_auth_credentials_store="file"' -c "${CODEX_CONTAINER_TRUST_CONFIG:-projects.\"/workspace\".trust_level=\"trusted\"}")
while [ "$#" -gt 0 ]; do
  case "$1" in
    --model|-m|--config|-c)
      if [ "$#" -lt 2 ]; then
        echo "error: $1 requires a value" >&2
        exit 2
      fi
      codex_args+=("$1" "$2")
      shift 2
      ;;
    --model=*|--config=*)
      codex_args+=("$1")
      shift
      ;;
    *)
      break
      ;;
  esac
done

usage() {
  cat >&2 <<'USAGE'
usage:
  codex-container --profile pr [--model MODEL] create "[extra instruction]"
  codex-container --profile pr [--model MODEL] describe "<PR link>" "[extra instruction]"
  codex-container --profile pr [--model MODEL] review "<PR link>" "[extra instruction]"
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

case "$1" in
  create)
    shift
    prompt="$(render_prompt "$SCRIPT_DIR/prompts/create.md" "" "$@")"
    exec codex exec "${codex_args[@]}" "$prompt"
    ;;
  describe|description)
    shift
    require_pr_link "$@"
    pr_link="$1"
    shift
    prompt="$(render_prompt "$SCRIPT_DIR/prompts/describe.md" "$pr_link" "$@")"
    exec codex exec "${codex_args[@]}" "$prompt"
    ;;
  review)
    shift
    require_pr_link "$@"
    pr_link="$1"
    shift
    prompt="$(render_prompt "$SCRIPT_DIR/prompts/review.md" "$pr_link" "$@")"
    exec codex exec "${codex_args[@]}" "$prompt"
    ;;
  *)
    exec codex "${codex_args[@]}" "$@"
    ;;
esac
