#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import re
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRACE_ROOT = REPO_ROOT / "reports" / "current_results" / "reported_execution_traces"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "reports" / "current_results" / "extracted_agent_pocs"
SUMMARY_ROW_NAME = "summary_row.json"
SAMPLE_TRACE_NAME = "sample_trace.json"
MANIFEST_CSV_NAME = "manifest.csv"
MANIFEST_JSON_NAME = "manifest.json"
ARTIFACT_NAMES = ("poc_crash", "poc")
SHELL_PUNCTUATION = "|><;{}"


@dataclass(frozen=True)
class ToolCommand:
    message_index: int
    call_index: int
    tool_name: str
    command: str


@dataclass(frozen=True)
class MaterializedArtifact:
    kind: str
    data: bytes


@dataclass(frozen=True)
class ExtractionResult:
    trace_group: str
    sample_id: str
    agent_type: str
    model: str
    project_name: str
    target_binary: str
    artifact: str
    summary_poc: str
    summary_exploit: str
    trace_path: str
    output_path: str
    command_path: str
    tool_name: str
    message_index: int
    call_index: int
    extraction_kind: str
    size_bytes: int
    sha256: str
    status: str
    message: str = ""


def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def safe_name(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in value)
    return safe.strip("._-") or "unknown"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def artifact_path(artifact: str) -> str:
    return f"/CybinGym_workdir/{artifact}"


def artifact_path_re(artifact: str) -> re.Pattern[str]:
    return re.compile(re.escape(artifact_path(artifact)) + r"(?![A-Za-z0-9_.-])")


def path_argument_re(artifact: str) -> str:
    return r"""['"]?""" + re.escape(artifact_path(artifact)) + r"""['"]?(?![A-Za-z0-9_.-])"""


def command_writes_artifact(command: str, artifact: str) -> bool:
    if not artifact_path_re(artifact).search(command):
        return False

    path_pattern = path_argument_re(artifact)
    checks = [
        rf">\s*{path_pattern}",
        rf"\btee\s+{path_pattern}",
        rf"\b(?:cp|mv|install)\b[^\n;]*{path_pattern}",
        rf"\bof={path_pattern}",
        rf"\bopen\(\s*['\"]{re.escape(artifact_path(artifact))}['\"]\s*,\s*['\"][^'\"]*w",
        rf"\bPath\(\s*['\"]{re.escape(artifact_path(artifact))}['\"]\s*\)\.write_(?:bytes|text)",
    ]
    if any(re.search(pattern, command) for pattern in checks):
        return True

    assignment = re.search(
        rf"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)=(['\"]?){re.escape(artifact_path(artifact))}\2\s*$",
        command,
    )
    if assignment:
        variable = re.escape(assignment.group(1))
        if re.search(rf">\s*['\"]?\${variable}['\"]?", command):
            return True

    return False


def iter_trace_paths(roots: Iterable[Path]) -> list[Path]:
    trace_paths: dict[Path, Path] = {}
    for root in roots:
        root = root.expanduser()
        if root.is_file() and root.name == SAMPLE_TRACE_NAME:
            trace_paths[root.resolve()] = root
        elif root.is_dir():
            for trace_path in root.rglob(SAMPLE_TRACE_NAME):
                trace_paths[trace_path.resolve()] = trace_path
    return [trace_paths[key] for key in sorted(trace_paths, key=lambda item: str(item))]


def read_summary(trace_path: Path) -> dict[str, Any]:
    summary_path = trace_path.parent / SUMMARY_ROW_NAME
    if not summary_path.exists():
        return {}
    try:
        summary = read_json(summary_path)
    except (OSError, json.JSONDecodeError):
        return {}
    return summary if isinstance(summary, dict) else {}


def trace_group_for_path(trace_root: Path, trace_path: Path) -> str:
    try:
        parts = trace_path.relative_to(trace_root).parts
    except ValueError:
        parts = trace_path.parts
    if parts and parts[0] in {"basic", "claude_code", "codex", "kimi_code", "opensage"}:
        return safe_name(trace_root.name)
    if len(parts) >= 4:
        return safe_name(parts[0])
    return safe_name(trace_root.name)


def output_dir_for_trace(output_root: Path, trace_root: Path, trace_path: Path, summary: dict[str, Any]) -> Path:
    trace_group = trace_group_for_path(trace_root, trace_path)
    agent_type = safe_name(clean(summary.get("selected_agent_type") or summary.get("agent_type")) or "unknown_agent")
    model = safe_name(clean(summary.get("selected_model") or summary.get("model")) or "unknown_model")
    sample_id = safe_name(clean(summary.get("id") or summary.get("sample_id")) or trace_path.parent.name)
    return output_root / trace_group / agent_type / model / sample_id


def parse_arguments(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def iter_tool_commands(sample: dict[str, Any]) -> Iterable[ToolCommand]:
    messages = sample.get("messages")
    if not isinstance(messages, list):
        return
    for message_index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue
        tool_calls = message.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            continue
        for call_index, tool_call in enumerate(tool_calls):
            if not isinstance(tool_call, dict):
                continue
            arguments = parse_arguments(tool_call.get("arguments"))
            command = clean(arguments.get("command"))
            if command:
                yield ToolCommand(
                    message_index=message_index,
                    call_index=call_index,
                    tool_name=clean(tool_call.get("function") or tool_call.get("name")),
                    command=command,
                )


def find_artifact_command(sample: dict[str, Any], artifact: str) -> ToolCommand | None:
    selected: ToolCommand | None = None
    for tool_command in iter_tool_commands(sample):
        if command_writes_artifact(tool_command.command, artifact):
            selected = tool_command
    return selected


def parse_heredoc_delimiter(line: str) -> str | None:
    match = re.search(r"<<-?\s*['\"]?([A-Za-z0-9_./-]+)['\"]?", line)
    if not match:
        return None
    return match.group(1)


def heredoc_body(command: str, start_index: int, delimiter: str) -> str | None:
    lines = command.splitlines(keepends=True)
    body: list[str] = []
    for line in lines[start_index + 1 :]:
        if line.rstrip("\r\n") == delimiter:
            return "".join(body)
        body.append(line)
    return None


def redirected_path(line: str) -> str:
    match = re.search(r">\s*['\"]?([^'\"\s;]+)", line)
    return match.group(1) if match else ""


def path_copied_to_artifact(command: str, source_path: str, artifact: str) -> bool:
    if not source_path:
        return False
    pattern = (
        r"\b(?:cp|mv|install)\b[^\n;]*"
        + re.escape(source_path)
        + r"[^\n;]*"
        + re.escape(artifact_path(artifact))
        + r"(?![A-Za-z0-9_.-])"
    )
    return re.search(pattern, command) is not None


def base64_decode_text(text: str) -> bytes:
    compact = "".join(text.split())
    return base64.b64decode(compact, validate=True)


def extract_base64_heredoc(command: str, artifact: str) -> MaterializedArtifact | None:
    lines = command.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if "base64" not in line or "<<" not in line:
            continue
        if "-d" not in line and "--decode" not in line:
            continue
        delimiter = parse_heredoc_delimiter(line)
        if not delimiter:
            continue
        body = heredoc_body(command, index, delimiter)
        if body is None:
            continue
        destination = redirected_path(line)
        if not artifact_path_re(artifact).search(line) and not path_copied_to_artifact(command, destination, artifact):
            continue
        try:
            return MaterializedArtifact(kind="base64_heredoc", data=base64_decode_text(body))
        except (ValueError, base64.binascii.Error):
            continue
    return None


def extract_base64_pipeline(command: str, artifact: str) -> MaterializedArtifact | None:
    quote = r"""(?P<quote>['"])"""
    quoted_data = quote + r"(?P<data>[A-Za-z0-9+/=\s]+)(?P=quote)"
    prefix = r"(?:printf\s+['\"]%s['\"]\s+|printf\s+|echo\s+-n\s+|echo\s+)"
    destination = r">\s*['\"]?(?P<destination>[^'\"\s;]+)"
    pattern = re.compile(
        prefix + quoted_data + r"\s*\|\s*base64\s+(?:-d|--decode)\s*" + destination,
        re.DOTALL,
    )
    for match in pattern.finditer(command):
        decoded_destination = match.group("destination")
        if (
            decoded_destination != artifact_path(artifact)
            and not artifact_path_re(artifact).search(decoded_destination)
            and not path_copied_to_artifact(command, decoded_destination, artifact)
        ):
            continue
        try:
            return MaterializedArtifact(kind="base64_pipeline", data=base64_decode_text(match.group("data")))
        except (ValueError, base64.binascii.Error):
            continue
    return None


def extract_cat_heredoc(command: str, artifact: str) -> MaterializedArtifact | None:
    lines = command.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if "cat" not in line or "<<" not in line or ">" not in line:
            continue
        if not artifact_path_re(artifact).search(line):
            continue
        delimiter = parse_heredoc_delimiter(line)
        if not delimiter:
            continue
        body = heredoc_body(command, index, delimiter)
        if body is not None:
            return MaterializedArtifact(kind="cat_heredoc", data=body.encode("utf-8"))
    return None


def extract_xxd_heredoc(command: str, artifact: str) -> MaterializedArtifact | None:
    lines = command.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if "xxd" not in line or "-r" not in line or "<<" not in line:
            continue
        if not artifact_path_re(artifact).search(line):
            continue
        delimiter = parse_heredoc_delimiter(line)
        if not delimiter:
            continue
        body = heredoc_body(command, index, delimiter)
        if body is None:
            continue
        compact = re.sub(r"[^0-9A-Fa-f]", "", body)
        if not compact or len(compact) % 2:
            continue
        try:
            return MaterializedArtifact(kind="xxd_heredoc", data=bytes.fromhex(compact))
        except ValueError:
            continue
    return None


def shell_tokens(command: str) -> list[str]:
    normalized = command.replace("\\\n", " ").replace("\n", ";")
    lexer = shlex.shlex(normalized, posix=True, punctuation_chars=SHELL_PUNCTUATION)
    lexer.whitespace_split = True
    return list(lexer)


def parse_artifact_variables(tokens: list[str], artifact: str) -> dict[str, str]:
    variables: dict[str, str] = {}
    for token in tokens:
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", token)
        if match and match.group(2) == artifact_path(artifact):
            variables[match.group(1)] = match.group(2)
    return variables


def resolves_to_artifact(token: str, artifact: str, variables: dict[str, str]) -> bool:
    if token == artifact_path(artifact):
        return True
    if token.startswith("$"):
        variable = token[2:-1] if token.startswith("${") and token.endswith("}") else token[1:]
        return variables.get(variable) == artifact_path(artifact)
    return False


def decode_ansi_c_token(token: str) -> str:
    if not token.startswith("$") or "\\" not in token:
        return token
    try:
        return token[1:].encode("utf-8").decode("unicode_escape")
    except UnicodeDecodeError:
        return token


def run_printf(tokens: list[str]) -> bytes | None:
    if not tokens or tokens[0] != "printf" or len(tokens) < 2:
        return None
    args = [decode_ansi_c_token(token) for token in tokens[1:]]
    try:
        completed = subprocess.run(
            ["/usr/bin/printf", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    return completed.stdout


def integer_token(value: str) -> int | None:
    try:
        return int(value, 0)
    except ValueError:
        return None


def run_head_zero(tokens: list[str]) -> bytes | None:
    if len(tokens) < 4 or tokens[0] != "head" or tokens[-1] != "/dev/zero":
        return None
    try:
        count_index = tokens.index("-c") + 1
    except ValueError:
        return None
    if count_index >= len(tokens):
        return None
    count = integer_token(tokens[count_index])
    if count is None or count < 0 or count > 64 * 1024 * 1024:
        return None
    return b"\x00" * count


def run_tr_zero_to_byte(tokens: list[str], data: bytes) -> bytes | None:
    if len(tokens) < 3 or tokens[0] != "tr":
        return None
    source = run_printf(["printf", tokens[1]])
    target = run_printf(["printf", tokens[2]])
    if source != b"\x00" or target is None or len(target) != 1:
        return None
    return data.replace(b"\x00", target)


def materialize_pipeline(tokens: list[str]) -> bytes | None:
    if "|" not in tokens:
        return materialize_simple_shell(tokens)
    pipe_index = tokens.index("|")
    left = tokens[:pipe_index]
    right = tokens[pipe_index + 1 :]
    if not left or not right:
        return None
    data = run_printf(left)
    if data is not None and right[:3] == ["xxd", "-r", "-p"]:
        compact = re.sub(rb"[^0-9A-Fa-f]", b"", data)
        if not compact or len(compact) % 2:
            return None
        try:
            return bytes.fromhex(compact.decode("ascii"))
        except ValueError:
            return None
    data = run_head_zero(left)
    if data is not None:
        return run_tr_zero_to_byte(right, data)
    return None


def materialize_simple_shell(tokens: list[str]) -> bytes | None:
    if not tokens:
        return b""
    if tokens[0] == ":":
        return b""
    if tokens[0] == "printf":
        return run_printf(tokens)
    if tokens[0] == "head":
        return run_head_zero(tokens)
    return None


def append_or_replace(current: bytes | None, data: bytes, operator: str) -> bytes | None:
    if operator == ">":
        return data
    if operator == ">>" and current is not None:
        return current + data
    return None


def split_shell_segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token == ";":
            if current:
                segments.append(current)
                current = []
        else:
            current.append(token)
    if current:
        segments.append(current)
    return segments


def extract_group_redirect(tokens: list[str], artifact: str, variables: dict[str, str]) -> MaterializedArtifact | None:
    if "{" not in tokens or "}" not in tokens:
        return None
    start = tokens.index("{")
    end = tokens.index("}", start + 1)
    if end + 2 >= len(tokens) or tokens[end + 1] not in {">", ">>"}:
        return None
    if not resolves_to_artifact(tokens[end + 2], artifact, variables):
        return None

    current = b""
    for segment in split_shell_segments(tokens[start + 1 : end]):
        data = materialize_pipeline(segment)
        if data is None:
            return None
        current += data
    return MaterializedArtifact(kind="shell_group", data=current)


def extract_shell_literals(command: str, artifact: str) -> MaterializedArtifact | None:
    if "<<" in command:
        return None
    try:
        tokens = shell_tokens(command)
    except ValueError:
        return None
    variables = parse_artifact_variables(tokens, artifact)
    group = extract_group_redirect(tokens, artifact, variables)
    if group is not None:
        return group

    current: bytes | None = None
    saw_supported_write = False
    for segment in split_shell_segments(tokens):
        operator_index = next((index for index, token in enumerate(segment) if token in {">", ">>"}), None)
        if operator_index is None or operator_index + 1 >= len(segment):
            continue
        destination = segment[operator_index + 1]
        if not resolves_to_artifact(destination, artifact, variables):
            continue
        data = materialize_pipeline(segment[:operator_index])
        if data is None:
            return None
        current = append_or_replace(current, data, segment[operator_index])
        if current is None:
            return None
        saw_supported_write = True

    if not saw_supported_write or current is None:
        return None
    return MaterializedArtifact(kind="shell_literal", data=current)


def materialize_artifact(command: str, artifact: str) -> MaterializedArtifact | None:
    for extractor in (
        extract_base64_heredoc,
        extract_base64_pipeline,
        extract_cat_heredoc,
        extract_xxd_heredoc,
        extract_shell_literals,
    ):
        materialized = extractor(command, artifact)
        if materialized is not None:
            return materialized
    return None


def artifact_succeeded(summary: dict[str, Any], artifact: str) -> bool:
    if artifact == "poc_crash":
        return clean(summary.get("poc")).lower() == "true" or clean(summary.get("raw_poc")).lower() == "true"
    if artifact == "poc":
        return clean(summary.get("exploit")).lower() == "true" or clean(summary.get("raw_exploit")).lower() == "true"
    return False


def extract_trace(
    trace_root: Path,
    trace_path: Path,
    output_root: Path,
    artifacts: Iterable[str],
    only_successful: bool,
) -> list[ExtractionResult]:
    try:
        sample = read_json(trace_path)
    except (OSError, json.JSONDecodeError) as error:
        return [
            ExtractionResult(
                trace_group=trace_group_for_path(trace_root, trace_path),
                sample_id=trace_path.parent.name,
                agent_type="",
                model="",
                project_name="",
                target_binary="",
                artifact="",
                summary_poc="",
                summary_exploit="",
                trace_path=str(trace_path),
                output_path="",
                command_path="",
                tool_name="",
                message_index=-1,
                call_index=-1,
                extraction_kind="",
                size_bytes=0,
                sha256="",
                status="error",
                message=str(error),
            )
        ]

    summary = read_summary(trace_path)
    sample_id = clean(summary.get("id") or summary.get("sample_id") or sample.get("id") or trace_path.parent.name)
    agent_type = clean(summary.get("selected_agent_type") or summary.get("agent_type"))
    model = clean(summary.get("selected_model") or summary.get("model"))
    project_name = clean(summary.get("project_name"))
    target_binary = clean(summary.get("target_binary"))
    summary_poc = clean(summary.get("poc") or summary.get("raw_poc"))
    summary_exploit = clean(summary.get("exploit") or summary.get("raw_exploit"))
    trace_group = trace_group_for_path(trace_root, trace_path)
    sample_output_dir = output_dir_for_trace(output_root, trace_root, trace_path, summary)

    results: list[ExtractionResult] = []
    for artifact in artifacts:
        if only_successful and not artifact_succeeded(summary, artifact):
            continue
        tool_command = find_artifact_command(sample, artifact)
        if tool_command is None:
            results.append(
                ExtractionResult(
                    trace_group=trace_group,
                    sample_id=sample_id,
                    agent_type=agent_type,
                    model=model,
                    project_name=project_name,
                    target_binary=target_binary,
                    artifact=artifact,
                    summary_poc=summary_poc,
                    summary_exploit=summary_exploit,
                    trace_path=str(trace_path),
                    output_path="",
                    command_path="",
                    tool_name="",
                    message_index=-1,
                    call_index=-1,
                    extraction_kind="",
                    size_bytes=0,
                    sha256="",
                    status="missing",
                    message="no artifact-writing command found",
                )
            )
            continue

        sample_output_dir.mkdir(parents=True, exist_ok=True)
        command_path = sample_output_dir / f"{artifact}.command.sh"
        command_path.write_text(tool_command.command.rstrip() + "\n", encoding="utf-8")

        materialized = materialize_artifact(tool_command.command, artifact)
        output_path = sample_output_dir / artifact
        if materialized is None:
            results.append(
                ExtractionResult(
                    trace_group=trace_group,
                    sample_id=sample_id,
                    agent_type=agent_type,
                    model=model,
                    project_name=project_name,
                    target_binary=target_binary,
                    artifact=artifact,
                    summary_poc=summary_poc,
                    summary_exploit=summary_exploit,
                    trace_path=str(trace_path),
                    output_path="",
                    command_path=str(command_path),
                    tool_name=tool_command.tool_name,
                    message_index=tool_command.message_index,
                    call_index=tool_command.call_index,
                    extraction_kind="command_only",
                    size_bytes=0,
                    sha256="",
                    status="command_only",
                    message="artifact bytes not embedded in a supported literal format",
                )
            )
            continue

        output_path.write_bytes(materialized.data)
        results.append(
            ExtractionResult(
                trace_group=trace_group,
                sample_id=sample_id,
                agent_type=agent_type,
                model=model,
                project_name=project_name,
                target_binary=target_binary,
                artifact=artifact,
                summary_poc=summary_poc,
                summary_exploit=summary_exploit,
                trace_path=str(trace_path),
                output_path=str(output_path),
                command_path=str(command_path),
                tool_name=tool_command.tool_name,
                message_index=tool_command.message_index,
                call_index=tool_command.call_index,
                extraction_kind=materialized.kind,
                size_bytes=len(materialized.data),
                sha256=sha256_bytes(materialized.data),
                status="ok",
            )
        )

    return results


def write_manifest(output_root: Path, results: list[ExtractionResult]) -> None:
    rows = [asdict(result) for result in results]
    write_json(output_root / MANIFEST_JSON_NAME, rows)
    fieldnames = list(ExtractionResult.__dataclass_fields__.keys())
    output_root.mkdir(parents=True, exist_ok=True)
    with (output_root / MANIFEST_CSV_NAME).open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract agent-generated poc_crash and poc artifacts from exported sample traces."
    )
    parser.add_argument(
        "--trace-root",
        nargs="+",
        default=[str(DEFAULT_TRACE_ROOT)],
        help="Trace root directories or sample_trace.json files. Defaults to reports/current_results/reported_execution_traces.",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument(
        "--artifact",
        action="append",
        choices=ARTIFACT_NAMES,
        help="Artifact to extract. May be provided more than once. Defaults to poc_crash and poc.",
    )
    parser.add_argument(
        "--only-successful",
        action="store_true",
        help="Only extract poc_crash when the summary marks poc=true, and poc when exploit=true.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    trace_roots = [Path(path) for path in args.trace_root]
    trace_paths = iter_trace_paths(trace_roots)
    if not trace_paths:
        raise SystemExit("error: no sample_trace.json files found")

    output_root = Path(args.output_dir).expanduser()
    artifacts = args.artifact or list(ARTIFACT_NAMES)
    results: list[ExtractionResult] = []
    for trace_root in trace_roots:
        if trace_root.is_file():
            selected_trace_paths = [trace_root]
        else:
            selected_trace_paths = [path for path in trace_paths if path.is_relative_to(trace_root)]
        for trace_path in selected_trace_paths:
            results.extend(
                extract_trace(
                    trace_root=trace_root,
                    trace_path=trace_path,
                    output_root=output_root,
                    artifacts=artifacts,
                    only_successful=args.only_successful,
                )
            )

    write_manifest(output_root, results)
    ok_count = sum(result.status == "ok" for result in results)
    command_only_count = sum(result.status == "command_only" for result in results)
    missing_count = sum(result.status == "missing" for result in results)
    error_count = sum(result.status == "error" for result in results)
    print(f"trace_files: {len(trace_paths)}")
    print(f"artifact_rows: {len(results)}")
    print(f"materialized: {ok_count}")
    print(f"command_only: {command_only_count}")
    print(f"missing: {missing_count}")
    print(f"errors: {error_count}")
    print(f"output_dir: {output_root}")
    if error_count:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
