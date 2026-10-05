#!/bin/bash
# Shorts Everywhere helper installer (macOS):  curl -fsSL https://minefoundation.org/install.sh | bash
#
# Installs everything under ~/.shorts-everywhere (no admin password, nothing system-wide):
#   app/      the helper code, from github.com/deswitsservice/shorts-scheduler
#   venv/     its own Python (fetched by uv), so the Mac's Python version doesn't matter
#   chrome_profile/, uploads/, shots/   your data; platform sign-ins stay on this computer
# and a LaunchAgent that starts the helper at login. Remove it with:
#   curl -fsSL https://minefoundation.org/uninstall.sh | bash
#
# For testing: SHORTS_REF=<branch> installs another branch; SHORTS_SOURCE_DIR=<path> copies a local checkout instead.
set -euo pipefail

main() {
  local APP="$HOME/.shorts-everywhere" REPO="deswitsservice/shorts-scheduler" REF="${SHORTS_REF:-master}"
  local LABEL="org.minefoundation.shorts-helper" PORT=8765
  local PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  say() { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
  die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

  [ "$(uname -s)" = "Darwin" ] || die "The helper currently runs on macOS only."
  [ -d "/Applications/Google Chrome.app" ] || die "Install Google Chrome first (https://www.google.com/chrome/), then run this again."
  mkdir -p "$APP" && chmod 700 "$APP"

  if [ ! -x "$APP/bin/uv" ]; then
    say "Downloading uv (Python manager)…"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$APP/bin" UV_NO_MODIFY_PATH=1 INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null
  fi
  local UV="$APP/bin/uv"

  say "Getting the helper…"
  rm -rf "$APP/app.new" && mkdir -p "$APP/app.new"
  if [ -n "${SHORTS_SOURCE_DIR:-}" ]; then
    (cd "$SHORTS_SOURCE_DIR" && tar --exclude=chrome_profile --exclude='private_data' --exclude=.git -cf - .) | tar -xf - -C "$APP/app.new"
  else
    curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$REF" | tar -xzf - -C "$APP/app.new" --strip-components=1
  fi
  [ -f "$APP/app.new/helper/local_app.py" ] || die "The download didn't contain the helper. Try again later."

  say "Setting up Python and packages (first time takes a minute)…"
  "$UV" venv --quiet --python 3.12 --allow-existing "$APP/venv"
  "$UV" pip install --quiet --python "$APP/venv/bin/python" -r "$APP/app.new/helper/requirements.txt"

  # Swap in the new code only once everything installed, so a failed update leaves the old helper working.
  # bootout returns before launchd has finished removing the service; bootstrapping again too early fails with
  # "Bootstrap failed: 5: Input/output error" (hit on the first re-install, 2026-10-04). Wait until it's gone.
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  for _ in $(seq 1 20); do
    launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || break
    sleep 0.5
  done
  rm -rf "$APP/app" && mv "$APP/app.new" "$APP/app"

  say "Starting the helper (and at every login)…"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$APP/venv/bin/python</string><string>-u</string><string>-m</string><string>helper.local_app</string></array>
  <key>WorkingDirectory</key><string>$APP/app</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$APP/helper.log</string>
  <key>StandardErrorPath</key><string>$APP/helper.log</string>
</dict></plist>
PLIST
  local tries=0
  until launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; do
    tries=$((tries + 1))
    [ "$tries" -lt 5 ] || die "macOS wouldn't start the helper (launchctl bootstrap failed). Try running this again."
    sleep 2
  done

  for _ in $(seq 1 60); do
    if curl -fsS "http://127.0.0.1:$PORT/api/helper/info" >/dev/null 2>&1; then
      say "Done. The helper is running. Opening Shorts Everywhere…"
      [ -n "${SHORTS_NO_OPEN:-}" ] || open "https://minefoundation.org/"
      return
    fi
    sleep 1
  done
  die "The helper didn't start. See $APP/helper.log"
}

main "$@"
