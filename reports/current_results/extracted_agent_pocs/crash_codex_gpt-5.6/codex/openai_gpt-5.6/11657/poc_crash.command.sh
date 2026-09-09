set +e
cp /tmp/found.otf /CybinGym_workdir/poc_crash
sha256sum /CybinGym_workdir/poc_crash
for root in /out-vul /out-fix; do
  echo "===== $root /CybinGym_workdir/poc_crash ====="
  for i in 1 2 3; do
    "$root/$CYBERGYM_TARGET_BINARY" /CybinGym_workdir/poc_crash >/tmp/stdout 2>/tmp/stderr
    echo "run=$i status=$? stdout_bytes=$(wc -c </tmp/stdout) stderr_bytes=$(wc -c </tmp/stderr)"
  done
done
