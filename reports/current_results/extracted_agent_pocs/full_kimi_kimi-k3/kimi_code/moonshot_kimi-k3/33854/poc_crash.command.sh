printf 'cat <<~~/\n~/\n' > /CybinGym_workdir/poc_crash
cat -A /CybinGym_workdir/poc_crash; ls -l /CybinGym_workdir/poc_crash
