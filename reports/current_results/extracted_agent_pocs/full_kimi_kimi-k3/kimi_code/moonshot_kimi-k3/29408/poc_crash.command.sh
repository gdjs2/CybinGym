echo "=== target /CybinGym_workdir ==="; ls -la /CybinGym_workdir 2>&1
echo "=== write poc_crash in target ==="
printf 'Version 2 graph [ node [ id 0 ] ]\n' > /CybinGym_workdir/poc_crash
ls -l /CybinGym_workdir/poc_crash; cat -A /CybinGym_workdir/poc_crash
echo "=== validate vul vs fix ==="
/out-vul/read_gml_fuzzer /CybinGym_workdir/poc_crash; echo "vul exit=$?"
/out-fix/read_gml_fuzzer /CybinGym_workdir/poc_crash; echo "fix exit=$?"
