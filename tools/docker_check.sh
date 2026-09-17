#!/usr/bin/env bash
# Build the image and prove what the README claims about it.
#
#   bash tools/docker_check.sh
#
# Runs in CI (.github/workflows/ci.yml, job "docker") because the development
# machine cannot run Docker. Every claim below was stated in the README before
# anything had executed it:
#
#   1. personal files never reach the image       (checked in the layers)
#   2. the process is not root
#   3. `jsa init` and `jsa matches` work with the example profile, no API key
#   4. `jsa serve --host 0.0.0.0` refuses without JSA_ALLOW_PUBLIC_BIND=1
#   5. ...and starts with it, published on 127.0.0.1 only
#   6. the tracker on the /data volume survives a container restart
#
# The layer, non-root and bind-guard assertions were each pushed once against
# deliberately broken input, and failed, before this was trusted. The README's
# "Known limitations" section records the runs.

set -euo pipefail

IMAGE="${IMAGE:-jobsearch:ci}"
VOLUME="jsa-ci-data"
DASH="jsa-ci-dashboard"
PORT="${PORT:-18765}"
MARK="JSA-DECOY-$(date +%s)-$$"

ok()   { echo "ok    $*"; }
fail() { echo "FAIL  $*" >&2; exit 1; }

# --- 1a. plant decoys in the build context --------------------------------
# A checkout never contains personal files, so without these the exclusion
# check would pass against an ignore file that excludes nothing.
DECOYS=(
  ".env"
  "profile/master_profile.yaml"
  "jobsearch.db"
  "jobsearch.db-wal"
  "config/decoy.db"
  "jsa/decoy.db"
  "output/decoy/resume.docx"
  "documents/decoy.txt"
  "jsa/decoy.log"
)
for f in "${DECOYS[@]}"; do
  # Never overwrite a real file: run this in a clean checkout.
  [ -e "$f" ] && fail "$f already exists; refusing to overwrite it (use a clean checkout)"
done

WORK="$(mktemp -d)"
cleanup() {
  docker rm -f "$DASH" >/dev/null 2>&1 || true
  docker volume rm -f "$VOLUME" >/dev/null 2>&1 || true
  for f in "${DECOYS[@]}"; do
    if [ -f "$f" ] && grep -q "$MARK" "$f" 2>/dev/null; then rm -f "$f"; fi
  done
  rmdir output/decoy output documents 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT

for f in "${DECOYS[@]}"; do
  mkdir -p "$(dirname "$f")"
  printf 'NVIDIA_API_KEY=%s\nfull_name: %s\n' "$MARK" "$MARK" > "$f"
done
ok "planted ${#DECOYS[@]} decoy personal files in the build context"

# --- build -------------------------------------------------------------------
docker build -q -t "$IMAGE" . >/dev/null
ok "built $IMAGE"

# --- 1b. no decoy in any layer -------------------------------------------------
docker save "$IMAGE" -o "$WORK/image.tar"
mkdir -p "$WORK/image"
tar -xf "$WORK/image.tar" -C "$WORK/image"
: > "$WORK/files.txt"
while IFS= read -r -d '' blob; do
  # Layers are tars; config and manifest blobs are not, and are skipped.
  tar -tf "$blob" >> "$WORK/files.txt" 2>/dev/null || true
  # grep -c reads to the end. `tar | grep -q` under pipefail would report a
  # FOUND marker as a failed pipeline -- tar dies of SIGPIPE when grep exits
  # early -- and the leak check would pass exactly when it should fail.
  hits="$(tar -xOf "$blob" 2>/dev/null | grep -ac "$MARK" || true)"
  if [ "${hits:-0}" -gt 0 ]; then
    fail "a layer contains decoy content ($blob)"
  fi
done < <(find "$WORK/image" -type f -print0)
[ -s "$WORK/files.txt" ] || fail "no layer could be listed; the check would pass vacuously"
leaked="$(grep -E '(^|/)(\.env|master_profile\.yaml|[^/]*\.db(-wal|-shm)?|[^/]*\.docx|decoy[^/]*)$' "$WORK/files.txt" || true)"
[ -z "$leaked" ] || fail "personal-looking files in the image layers:
$leaked"
ok "no decoy in $(wc -l < "$WORK/files.txt") layer entries"

# And from inside the running image, as a second view of the same claim.
inside="$(docker run --rm --entrypoint sh "$IMAGE" -c \
  'find /app /data /home -name .env -o -name master_profile.yaml -o -name "*.db" -o -name "*.docx" 2>/dev/null')"
[ -z "$inside" ] || fail "personal-looking files inside the container: $inside"
ok "no .env, master_profile.yaml, *.db or *.docx under /app, /data or /home"

# --- 2. not root -----------------------------------------------------------------
uid="$(docker run --rm --entrypoint id "$IMAGE" -u)"
[ "$uid" != "0" ] || fail "the container runs as root (uid 0)"
ok "runs as uid $uid"

# --- 3. init and matches, example profile, no key -----------------------------------
mkdir -p "$WORK/profile"
cp profile/master_profile.example.yaml "$WORK/profile/master_profile.yaml"
chmod -R a+rX "$WORK/profile"
docker volume create "$VOLUME" >/dev/null
run() {
  docker run --rm -v "$VOLUME:/data" -v "$WORK/profile:/app/profile:ro" "$IMAGE" "$@"
}
[ -z "$(docker run --rm --entrypoint env "$IMAGE" | grep NVIDIA_API_KEY || true)" ] \
  || fail "the image carries an NVIDIA_API_KEY"
run init >/dev/null
out="$(run matches --limit 5)" || fail "jsa matches exited non-zero: $out"
echo "$out" | grep -q "no unreviewed matches" || fail "unexpected matches output: $out"
ok "jsa init and jsa matches exit 0 with the example profile and no API key"

# --- 4. the bind guard refuses ----------------------------------------------------------
set +e
out="$(timeout 30 docker run --rm -v "$VOLUME:/data" "$IMAGE" serve --host 0.0.0.0 2>&1)"
status=$?
set -e
[ "$status" -ne 124 ] || fail "serve --host 0.0.0.0 STARTED without the opt-in (killed by timeout)"
[ "$status" -ne 0 ] || fail "serve --host 0.0.0.0 exited 0 without the opt-in"
echo "$out" | grep -q "refusing to bind" \
  || fail "serve failed, but not because of the bind guard: $out"
ok "serve --host 0.0.0.0 refuses without JSA_ALLOW_PUBLIC_BIND (exit $status)"

# --- 5. ...and starts with it, on loopback only ------------------------------------------
docker run -d --name "$DASH" -e JSA_ALLOW_PUBLIC_BIND=1 \
  -v "$VOLUME:/data" -p "127.0.0.1:$PORT:8765" \
  "$IMAGE" serve --host 0.0.0.0 --port 8765 >/dev/null
wait_up() {
  for _ in $(seq 1 30); do
    code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/pipeline" || true)"
    [ "$code" = "200" ] && return 0
    sleep 1
  done
  docker logs "$DASH" >&2 || true
  return 1
}
wait_up || fail "the dashboard did not answer on 127.0.0.1:$PORT"
published="$(docker port "$DASH" 8765)"
case "$published" in
  127.0.0.1:*) ;;
  *) fail "port published beyond loopback: $published" ;;
esac
code="$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: evil.example' "http://127.0.0.1:$PORT/")"
[ "$code" = "400" ] || fail "a foreign Host header got $code, expected 400"
ok "serve starts with the opt-in, published on $published, refuses foreign hosts"

# --- 6. the tracker survives a restart ------------------------------------------------------
docker exec "$DASH" python -c "
import sqlite3
con = sqlite3.connect('/data/jobsearch.db')
con.execute(\"INSERT INTO companies (name, slug) VALUES ('Persistence Check', 'persistence-check')\")
con.commit()"
docker restart "$DASH" >/dev/null
wait_up || fail "the dashboard did not come back after a restart"
count="$(docker exec "$DASH" python -c "
import sqlite3
print(sqlite3.connect('/data/jobsearch.db').execute(
    \"SELECT COUNT(*) FROM companies WHERE slug = 'persistence-check'\").fetchone()[0])")"
[ "$count" = "1" ] || fail "the tracker lost its row across a restart (count=$count)"
docker rm -f "$DASH" >/dev/null
count="$(docker run --rm -v "$VOLUME:/data" --entrypoint python "$IMAGE" -c "
import sqlite3
print(sqlite3.connect('/data/jobsearch.db').execute(
    \"SELECT COUNT(*) FROM companies WHERE slug = 'persistence-check'\").fetchone()[0])")"
[ "$count" = "1" ] || fail "a new container did not see the tracker (count=$count)"
ok "the /data tracker survives a restart and a new container"

echo
echo "all container checks passed"
