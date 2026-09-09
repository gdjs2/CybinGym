from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import extract_agent_pocs as extractor


def _write_trace(
    root: Path,
    sample_id: str,
    command: str,
    *,
    poc: str = "true",
    exploit: str = "false",
) -> Path:
    sample_dir = root / "crash_codex_gpt-5.6" / "codex" / "openai_gpt-5.6" / sample_id
    sample_dir.mkdir(parents=True)
    (sample_dir / "summary_row.json").write_text(
        json.dumps(
            {
                "id": sample_id,
                "project_name": "libpng",
                "target_binary": "png_read_fuzzer",
                "selected_agent_type": "codex",
                "selected_model": "openai/gpt-5.6",
                "poc": poc,
                "exploit": exploit,
            }
        ),
        encoding="utf-8",
    )
    (sample_dir / "sample_trace.json").write_text(
        json.dumps(
            {
                "id": sample_id,
                "messages": [
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "function": "default",
                                "arguments": {"command": command},
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return sample_dir / "sample_trace.json"


class ExtractAgentPocsTests(unittest.TestCase):
    def test_materializes_base64_pipeline_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_trace(
                root,
                "4451",
                "printf '%s' 'Y3Jhc2g=' | base64 -d > /CybinGym_workdir/poc_crash\n"
                "sha256sum /CybinGym_workdir/poc_crash",
            )
            output_dir = root / "out"

            exit_code = extractor.main(
                [
                    "--trace-root",
                    str(root),
                    "--output-dir",
                    str(output_dir),
                    "--only-successful",
                ]
            )

            self.assertEqual(exit_code, 0)
            artifact = output_dir / "crash_codex_gpt-5.6" / "codex" / "openai_gpt-5.6" / "4451" / "poc_crash"
            self.assertEqual(artifact.read_bytes(), b"crash")
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest[0]["status"], "ok")
            self.assertEqual(manifest[0]["extraction_kind"], "base64_pipeline")

    def test_materializes_cat_heredoc_exploit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_trace(
                root,
                "5625",
                "cat > /CybinGym_workdir/poc <<'PY'\n"
                "#!/usr/bin/env python3\n"
                "print('flag placeholder')\n"
                "PY\n"
                "chmod 0755 /CybinGym_workdir/poc",
                exploit="true",
            )
            output_dir = root / "out"

            exit_code = extractor.main(
                [
                    "--trace-root",
                    str(root),
                    "--output-dir",
                    str(output_dir),
                    "--artifact",
                    "poc",
                    "--only-successful",
                ]
            )

            self.assertEqual(exit_code, 0)
            artifact = output_dir / "crash_codex_gpt-5.6" / "codex" / "openai_gpt-5.6" / "5625" / "poc"
            self.assertEqual(artifact.read_text(encoding="utf-8"), "#!/usr/bin/env python3\nprint('flag placeholder')\n")

    def test_materializes_printf_literal_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_trace(
                root,
                "10628",
                "printf '\\x00\\x01\\n' > /CybinGym_workdir/poc_crash\n"
                "od -An -tx1 /CybinGym_workdir/poc_crash",
            )
            output_dir = root / "out"

            exit_code = extractor.main(
                [
                    "--trace-root",
                    str(root),
                    "--output-dir",
                    str(output_dir),
                    "--only-successful",
                ]
            )

            self.assertEqual(exit_code, 0)
            artifact = output_dir / "crash_codex_gpt-5.6" / "codex" / "openai_gpt-5.6" / "10628" / "poc_crash"
            self.assertEqual(artifact.read_bytes(), b"\x00\x01\n")

    def test_materializes_printf_xxd_pipeline_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_trace(
                root,
                "12096",
                "printf '%s\\n' '4142' '4300' | xxd -r -p > /CybinGym_workdir/poc_crash",
            )
            output_dir = root / "out"

            exit_code = extractor.main(
                [
                    "--trace-root",
                    str(root),
                    "--output-dir",
                    str(output_dir),
                    "--only-successful",
                ]
            )

            self.assertEqual(exit_code, 0)
            artifact = output_dir / "crash_codex_gpt-5.6" / "codex" / "openai_gpt-5.6" / "12096" / "poc_crash"
            self.assertEqual(artifact.read_bytes(), b"ABC\x00")

    def test_materializes_head_zero_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_trace(root, "16541", "head -c 4 /dev/zero > /CybinGym_workdir/poc_crash")
            output_dir = root / "out"

            exit_code = extractor.main(
                [
                    "--trace-root",
                    str(root),
                    "--output-dir",
                    str(output_dir),
                    "--only-successful",
                ]
            )

            self.assertEqual(exit_code, 0)
            artifact = output_dir / "crash_codex_gpt-5.6" / "codex" / "openai_gpt-5.6" / "16541" / "poc_crash"
            self.assertEqual(artifact.read_bytes(), b"\x00" * 4)

    def test_materializes_group_redirect_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_trace(
                root,
                "26345",
                "{ printf 'DA'; head -c 3 /dev/zero | tr '\\000' 'B'; printf 'A'; } > /CybinGym_workdir/poc_crash",
            )
            output_dir = root / "out"

            exit_code = extractor.main(
                [
                    "--trace-root",
                    str(root),
                    "--output-dir",
                    str(output_dir),
                    "--only-successful",
                ]
            )

            self.assertEqual(exit_code, 0)
            artifact = output_dir / "crash_codex_gpt-5.6" / "codex" / "openai_gpt-5.6" / "26345" / "poc_crash"
            self.assertEqual(artifact.read_bytes(), b"DABBBA")

    def test_saves_command_when_payload_is_not_literal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_trace(
                root,
                "19039",
                "python3 make_case.py\ncp /tmp/case.bin /CybinGym_workdir/poc_crash",
            )
            output_dir = root / "out"

            exit_code = extractor.main(
                [
                    "--trace-root",
                    str(root),
                    "--output-dir",
                    str(output_dir),
                    "--only-successful",
                ]
            )

            self.assertEqual(exit_code, 0)
            sample_dir = output_dir / "crash_codex_gpt-5.6" / "codex" / "openai_gpt-5.6" / "19039"
            self.assertFalse((sample_dir / "poc_crash").exists())
            self.assertTrue((sample_dir / "poc_crash.command.sh").exists())
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest[0]["status"], "command_only")

    def test_only_successful_filters_failed_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_trace(
                root,
                "12173",
                "printf '%s' 'ZmFpbA==' | base64 -d > /CybinGym_workdir/poc_crash",
                poc="false",
            )
            output_dir = root / "out"

            exit_code = extractor.main(
                [
                    "--trace-root",
                    str(root),
                    "--output-dir",
                    str(output_dir),
                    "--only-successful",
                ]
            )

            self.assertEqual(exit_code, 0)
            manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest, [])


if __name__ == "__main__":
    unittest.main()
