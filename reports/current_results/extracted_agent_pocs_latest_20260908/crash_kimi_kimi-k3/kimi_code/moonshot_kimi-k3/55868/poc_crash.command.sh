cd /tmp/pocwork
# create a first candidate: hybrid xref table + broken xref stream (test1 style)
cp big_1000.pdf /CybinGym_workdir/poc_crash
ls -la /CybinGym_workdir/poc_crash
echo "=== vul ==="; timeout 30 /out-vul/pdf_fuzzer /CybinGym_workdir/poc_crash 2>&1 | tail -2; echo "rc=${PIPESTATUS[0]}"
echo "=== fix ==="; timeout 30 /out-fix/pdf_fuzzer /CybinGym_workdir/poc_crash 2>&1 | tail -2; echo "rc=${PIPESTATUS[0]}"
