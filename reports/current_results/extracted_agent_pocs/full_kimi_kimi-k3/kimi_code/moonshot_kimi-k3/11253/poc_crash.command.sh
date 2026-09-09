printf '\x02actions=drop\x00' > /CybinGym_workdir/poc_crash
echo "=== poc_crash ==="; xxd /CybinGym_workdir/poc_crash; ls -l /CybinGym_workdir/
