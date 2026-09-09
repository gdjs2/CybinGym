cp /CybinGym_workdir/poc_crash /tmp/poc_crash 2>/dev/null; ls -la /tmp/poc_crash
echo "=== VULNERABLE ==="
/out-vul/"$CYBERGYM_TARGET_BINARY" /tmp/poc_crash; echo "vuln exit=$?"
echo "=== FIXED ==="
/out-fix/"$CYBERGYM_TARGET_BINARY" /tmp/poc_crash; echo "fix exit=$?"
