cd /tmp && cp /CybinGym_workdir/poc_crash pc.bin
echo "=== VUL ==="; /out-vul/mode_padding pc.bin; echo "vul exit=$?"
echo "=== FIX ==="; /out-fix/mode_padding pc.bin; echo "fix exit=$?"
echo "=== FIX ref_oneandzero: does it read buf[-1]? (fix ref is inline too) ==="
gdb -q -batch \
 -ex 'break *0x403680' \
 -ex 'run pc.bin' \
 -ex 'printf "fix ref loop base rbx=%p rax=%ld\n",$rbx,$rax' \
 -ex 'delete 1' -ex 'break *0x403680 if $rax<=0' -ex 'continue' \
 -ex 'printf "fix reached rax=%ld (if stops, no underflow)\n",$rax' \
 /out-fix/mode_padding 2>&1 | grep -v '^\[\|warning\|Thread\|host lib' | head -15
