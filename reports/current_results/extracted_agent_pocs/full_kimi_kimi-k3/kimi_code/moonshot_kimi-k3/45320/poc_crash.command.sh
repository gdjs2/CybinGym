cp /tmp/v2.pdf /CybinGym_workdir/poc_crash
VUL="/out-vul/$CYBERGYM_TARGET_BINARY"; FIX="/out-fix/$CYBERGYM_TARGET_BINARY"
echo "final poc_crash:"; for i in 1 2 3; do $VUL /CybinGym_workdir/poc_crash >/dev/null 2>&1; echo -n "v=$? "; done; echo; $FIX /CybinGym_workdir/poc_crash >/dev/null 2>&1; echo "fix=$?"
