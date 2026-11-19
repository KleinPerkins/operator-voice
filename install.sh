#!/usr/bin/env bash
# install.sh — Operator Voice Toolkit (OVT) Standalone System Package installer.
#
# OVT is a Standalone System Package (SSP, build plan v3 §4/§13): a self-contained product that
# installs itself from its own checkout, with no sibling-repo dependency. This script:
#   1. creates/updates a private venv holding OVT's two runtime dependencies (cryptography, mcp)
#      — never the operator_voice package itself, which is installed as an immutable, versioned,
#      content-digested RUNTIME SLOT via `operator_voice.runtime.install` (atomic install/repair,
#      §13 M1 acceptance: same-digest no-op, same-version/different-digest refuses for distributor
#      payloads (home checkout and --force upgrade), interruption preserves the prior runtime,
#      repair changes runtime only);
#   2. writes a thin `operator-voice` wrapper script that points PYTHONPATH at the currently
#      active runtime slot (`.../operator-voice/runtime/current`), decoupling execution from this
#      checkout so the checkout can move/disappear after install without breaking the install;
#   3. installs the packaged SKILL.md into every present agent's skills directory (idempotent).
#
#   install.sh              install (or no-op if already current; default)
#   install.sh --force       install even when a distributor payload differs at the same version
#   install.sh --repair      verify/replace the runtime payload only (never config/state/keys)
#   install.sh --doctor      read-only health check
#   install.sh --check       CI-safe static preflight (no writes, no network)
#   install.sh --uninstall   remove the venv/runtime/wrapper/skill; profile data+keys untouched
#
# All paths are HOME / XDG-relative; nothing here ever touches ~/dev or any other repo.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
NAME="operator-voice"
log()     { printf '[install:%s] %s\n' "$NAME" "$*"; }
degrade() { printf '[install:%s] DEGRADED: %s\n' "$NAME" "$*" >&2; }

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
APP_DIR="$DATA_HOME/$NAME"
VENV="$APP_DIR/venv"
RUNTIME_ROOT="$APP_DIR/runtime"
RECEIPT="$STATE_HOME/$NAME/install-receipt.json"
BIN_DIR="$HOME/.local/bin"
WRAPPER="$BIN_DIR/$NAME"
SOURCE_ROOT="$SRC/src/operator_voice"
INSTALLER_PYTHONPATH="$SRC/src"

need_python312() {
  for cand in python3.12 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
      if "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' 2>/dev/null; then
        echo "$cand"; return 0
      fi
    fi
  done
  return 1
}

runtime_requirements() {
  # Single source of truth: src/operator_voice/runtime/requirements.json (operator_voice.runtime.
  # requirements), never a second hardcoded pin list here — that drifted from pyproject.toml before.
  "$1" -c '
import sys
sys.path.insert(0, "'"$SRC"'/src")
from operator_voice.runtime.requirements import load_install_requires
from pathlib import Path
print("\n".join(load_install_requires(Path("'"$SOURCE_ROOT"'"))))
'
}

ensure_venv() {
  local py; py="$(need_python312)" || { degrade "Python 3.12 required; no supported interpreter found on PATH"; return 2; }
  if [ ! -x "$VENV/bin/python" ]; then
    log "creating venv -> $VENV"
    if command -v uv >/dev/null 2>&1; then
      uv venv --python "$py" "$VENV" >/dev/null
    else
      "$py" -m venv "$VENV"
    fi
  fi
  if ! "$VENV/bin/python" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' 2>/dev/null; then
    degrade "existing venv requires Python 3.12; recreate it with the supported interpreter"
    return 2
  fi
  local reqs; reqs="$(runtime_requirements "$py")"
  log "installing runtime dependencies ($(printf '%s' "$reqs" | tr '\n' ' ')) into the venv"
  if command -v uv >/dev/null 2>&1; then
    uv pip install --python "$VENV/bin/python" --quiet $reqs
  else
    "$VENV/bin/pip" install --quiet $reqs
  fi
}

run_install_cli() {
  PYTHONPATH="$INSTALLER_PYTHONPATH" "$VENV/bin/python" -m operator_voice.runtime.install_cli "$@"
}

write_wrapper() {
  mkdir -p "$BIN_DIR"
  local tmp; tmp="$(mktemp "$BIN_DIR/.${NAME}.XXXXXX")"
  cat > "$tmp" <<WRAP
#!/usr/bin/env bash
export PYTHONPATH="$RUNTIME_ROOT/current\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$VENV/bin/python" -m operator_voice.interfaces.cli "\$@"
WRAP
  chmod +x "$tmp"
  mv -f "$tmp" "$WRAPPER"
  log "wrapper installed -> $WRAPPER (ensure $BIN_DIR is on PATH)"
}

install_skill_files() {
  PYTHONPATH="$RUNTIME_ROOT/current" "$VENV/bin/python" -c '
from operator_voice.interfaces.skill import install_skill
import json
print(json.dumps(install_skill()))
' 2>/dev/null || degrade "skill install step failed (non-fatal; CLI/MCP remain usable)"
}

refresh_curation_schedule() {
  if [ ! -x "$VENV/bin/python" ] || [ ! -d "$RUNTIME_ROOT/current" ]; then
    return 0
  fi
  log "refreshing on-demand curation registration (retires the independent timer)"
  PYTHONPATH="$RUNTIME_ROOT/current" "$VENV/bin/python" -c '
from operator_voice.runtime.local_jobs_enroll import ensure_curation_schedule
import json
print(json.dumps(ensure_curation_schedule()))
' 2>/dev/null || degrade "on-demand curation registration refresh failed (non-fatal; run operator-voice setup or schedule-plan)"
}

do_install() {
  local force="${1:-}"
  ensure_venv || return $?
  local args=(install --source-root "$SOURCE_ROOT" --runtime-root "$RUNTIME_ROOT" --receipt "$RECEIPT" --source home)
  [ "$force" = "1" ] && args+=(--force)
  run_install_cli "${args[@]}"
  write_wrapper
  install_skill_files
  refresh_curation_schedule
}

do_repair() {
  ensure_venv || return $?
  run_install_cli repair --source-root "$SOURCE_ROOT" --runtime-root "$RUNTIME_ROOT" --receipt "$RECEIPT"
  write_wrapper
  install_skill_files
  refresh_curation_schedule
}

do_check() {
  local py; py="$(need_python312)" || { degrade "Python 3.12 required; no supported interpreter found on PATH"; return 2; }
  "$py" -B -c '
import sys
sys.path.insert(0, "'"$SRC"'/src")
from operator_voice.runtime.install_cli import cmd_check, build_parser
ns = build_parser().parse_args(["check", "--source-root", "'"$SOURCE_ROOT"'"])
raise SystemExit(cmd_check(ns))
'
}

do_doctor() {
  [ -x "$VENV/bin/python" ] || { degrade "no venv yet — run install.sh first"; return 3; }
  run_install_cli doctor --runtime-root "$RUNTIME_ROOT" --receipt "$RECEIPT"
  local rc=$?
  if [ -x "$WRAPPER" ]; then log "wrapper present -> $WRAPPER"; else degrade "wrapper missing"; rc=1; fi
  return $rc
}

do_uninstall() {
  log "removing venv, runtime, wrapper, and skill files (profile data/config/keys under \$XDG_DATA_HOME/$NAME and \$XDG_STATE_HOME/$NAME/profile-pack-clone are left untouched by this step; run 'operator-voice erase' first if you want those gone too)"
  if [ -x "$VENV/bin/python" ]; then
    PYTHONPATH="$RUNTIME_ROOT/current" "$VENV/bin/python" -c '
from operator_voice.interfaces.skill import uninstall_skill
uninstall_skill()
' 2>/dev/null || true
  fi
  rm -rf "$VENV" "$RUNTIME_ROOT"
  rm -f "$WRAPPER"
  log "uninstalled runtime + wrapper + skill files. Receipt left at $RECEIPT for audit; delete manually if desired."
}

case "${1:-install}" in
  install|"")   do_install 0 ;;
  --force)      do_install 1 ;;
  --repair)     do_repair ;;
  --doctor)     do_doctor ;;
  --check)      do_check ;;
  --uninstall)  do_uninstall ;;
  -h|--help)    sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//' ;;
  *) log "WARN unknown arg '$1' (ignored); running default install"; do_install 0 ;;
esac
