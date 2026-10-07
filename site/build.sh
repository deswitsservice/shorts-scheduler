#!/bin/bash
# Assemble the static minefoundation.org site into site/dist (no server: the page talks to the local helper).
#   bash site/build.sh            then upload site/dist/ to the host's web root
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/site/dist"
rm -rf "$OUT" && mkdir -p "$OUT/static"
cp "$ROOT/webapp/static/index.html" "$OUT/index.html"
cp "$ROOT/webapp/static/design.css" "$ROOT/webapp/static/connections.js" "$OUT/static/"
cp "$ROOT/site/install.sh" "$ROOT/site/uninstall.sh" "$ROOT/site/install.ps1" "$ROOT/site/uninstall.ps1" "$OUT/"
cat > "$OUT/.htaccess" <<'HTACCESS'
# Serve the installer scripts as text so `curl | bash` (macOS) and `irm | iex` (Windows) get them verbatim, and never
# cache them. Header lines are guarded: without mod_headers an unguarded `Header` makes Apache answer 500 for the site.
<FilesMatch "\.(sh|ps1)$">
  ForceType "text/plain; charset=utf-8"
</FilesMatch>
<IfModule mod_headers.c>
  <FilesMatch "(\.sh|\.ps1|^index\.html)$">
    Header set Cache-Control "no-cache"
  </FilesMatch>
</IfModule>
HTACCESS
echo "Built $OUT:"; (cd "$OUT" && find . -type f | sort)
