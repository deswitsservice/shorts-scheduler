#!/bin/bash
# Assemble the static minefoundation.org site into site/dist (no server: the page talks to the local helper).
#   bash site/build.sh            then upload site/dist/ to the host's web root
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/site/dist"
rm -rf "$OUT" && mkdir -p "$OUT/static"
cp "$ROOT/webapp/static/index.html" "$OUT/index.html"
cp "$ROOT/webapp/static/design.css" "$ROOT/webapp/static/connections.js" "$OUT/static/"
cp "$ROOT/site/install.sh" "$ROOT/site/uninstall.sh" "$OUT/"
cat > "$OUT/.htaccess" <<'HTACCESS'
# Serve the installer scripts as text so `curl | bash` gets them verbatim, and never cache them.
<FilesMatch "\.sh$">
  ForceType text/plain
  Header set Cache-Control "no-cache"
</FilesMatch>
<FilesMatch "^index\.html$">
  Header set Cache-Control "no-cache"
</FilesMatch>
HTACCESS
echo "Built $OUT:"; (cd "$OUT" && find . -type f | sort)
