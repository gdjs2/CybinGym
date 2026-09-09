cp /CybinGym_workdir/poc_crash /tmp/final.pgm
echo "=== VULNERABLE ==="; /out-vul/FuzzPGMLoader /tmp/final.pgm 2>&1; echo "exit=${PIPESTATUS[0]}"
echo "=== FIXED ==="; /out-fix/FuzzPGMLoader /tmp/final.pgm 2>&1; echo "exit=${PIPESTATUS[0]}"
