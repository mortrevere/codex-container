#!/usr/bin/env bash
set -euo pipefail

codex plugin marketplace add DietrichGebert/ponytail \
  && codex plugin add ponytail@ponytail
