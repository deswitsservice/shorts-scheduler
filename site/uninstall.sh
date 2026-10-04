#!/bin/bash
# Remove the Shorts Everywhere helper:  curl -fsSL https://minefoundation.org/uninstall.sh | bash
# Keeps ~/.shorts-everywhere/chrome_profile (your platform sign-ins) and uploads unless you pass --all:
#   curl -fsSL https://minefoundation.org/uninstall.sh | bash -s -- --all
set -euo pipefail

main() {
  local APP="$HOME/.shorts-everywhere" LABEL="org.minefoundation.shorts-helper"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
  rm -rf "$APP/app" "$APP/app.new" "$APP/venv" "$APP/bin" "$APP/helper.log"
  if [ "${1:-}" = "--all" ]; then
    rm -rf "$APP"
    echo "Removed the helper and all its data, including saved platform sign-ins."
  else
    echo "Removed the helper. Your sign-ins and uploads are still in $APP (delete that folder to remove them too)."
  fi
}

main "$@"
