from __future__ import annotations

import asyncio
import datetime
import io
import json
import os
import re
import socket
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from inspect_ai.model import ChatMessageAssistant
from inspect_ai.solver import Generate, Solver, TaskState, solver
from inspect_ai.util import sandbox


CYBINGYM_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OPENSAGE_AGENT_DIR = CYBINGYM_DIR / "solvers" / "ctf_agent"
DEFAULT_OPENSAGE_SOURCE_DIR = Path(
    os.environ.get("OPENSAGE_SOURCE_DIR", str(CYBINGYM_DIR.parent / "opensage-adk-dev"))
)


def _default_opensage_python() -> Path:
    configured = os.environ.get("OPENSAGE_PYTHON")
    if configured:
        return Path(configured)
    virtual_env = os.environ.get("VIRTUAL_ENV")
    if virtual_env:
        candidate = Path(virtual_env) / "bin" / "python"
        if candidate.exists():
            return candidate
    return Path(sys.executable)


DEFAULT_OPENSAGE_PYTHON = _default_opensage_python()
_PORT_LOCK = asyncio.Lock()
_RESERVED_PORTS: set[int] = set()
_MCP_PORT_ARGS = {"--gdb_port", "--ida_pro_mcp_port", "--pyghidra_mcp_port"}
RUNNER_TERMINATE_GRACE_SECONDS = 30
STALE_INSPECT_NETWORK_WARNING_THRESHOLD = 10
_FALSE_ENV_VALUES = {"0", "false", "no", "off"}


def _env_flag_enabled(name: str, *, default: bool = True) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in _FALSE_ENV_VALUES


def _should_route_openai_model_to_responses(model_name: str) -> bool:
    if not _env_flag_enabled("OPENSAGE_OPENAI_RESPONSES_API", default=True):
        return False
    if not model_name.startswith("openai/"):
        return False
    model_id = model_name[len("openai/") :]
    if model_id.startswith("responses/"):
        model_id = model_id[len("responses/") :]
    return model_id.startswith("gpt-5") and not model_id.startswith("gpt-5-chat")


def _target_text(state: TaskState) -> str:
    target = state.target
    text = getattr(target, "text", None)
    if isinstance(text, str):
        return text
    return str(target)


def _build_env(
    *,
    cybingym_dir: Path,
    opensage_agent_dir: Path,
    opensage_source_dir: Path,
    opensage_model: str,
    opensage_provider: str,
    opensage_reasoning_effort: str = "",
    llm_retry_timeout: int | None = None,
    llm_retry_count: int | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    pythonpath_entries = [
        str(opensage_source_dir / "src"),
        str(cybingym_dir),
        str(opensage_agent_dir.parent),
    ]
    if env.get("PYTHONPATH"):
        pythonpath_entries.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
    if opensage_model:
        env["OPENSAGE_MODEL"] = opensage_model
        if _should_route_openai_model_to_responses(opensage_model):
            env["LITELLM_ROUTE_ALL_CHAT_OPENAI_TO_RESPONSES"] = "true"
    if opensage_provider:
        env["OPENSAGE_PROVIDER"] = opensage_provider
    if opensage_reasoning_effort:
        env["OPENSAGE_REASONING_EFFORT"] = opensage_reasoning_effort
    if llm_retry_timeout is not None:
        env["OPENSAGE_LITELLM_TIMEOUT"] = str(int(llm_retry_timeout))
    if llm_retry_count is not None:
        env["OPENSAGE_LITELLM_NUM_RETRIES"] = str(int(llm_retry_count))
    return env


def _infer_opensage_provider(model_name: str) -> str:
    if model_name.startswith("openai/"):
        return "openai"
    if model_name.startswith("ollama") or model_name.startswith("ollama_chat/"):
        return "ollama"
    return ""


def _port_is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


def _listening_tcp_ports() -> set[int]:
    ports: set[int] = set()
    for proc_net_path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            lines = proc_net_path.read_text(encoding="ascii", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines[1:]:
            fields = line.split()
            if len(fields) < 4 or fields[3] != "0A":
                continue
            try:
                ports.add(int(fields[1].split(":", 1)[1], 16))
            except (IndexError, ValueError):
                continue
    return ports


def _active_opensage_runner_ports() -> set[int]:
    ports: set[int] = set()
    proc_root = Path("/proc")
    try:
        pid_dirs = list(proc_root.iterdir())
    except OSError:
        return ports

    for pid_dir in pid_dirs:
        if not pid_dir.name.isdigit():
            continue
        try:
            raw_cmdline = (pid_dir / "cmdline").read_bytes()
        except OSError:
            continue
        if b"solvers.opensage_cybingym_runner" not in raw_cmdline:
            continue

        argv = [part.decode(errors="ignore") for part in raw_cmdline.split(b"\0") if part]
        for index, arg in enumerate(argv):
            if arg in _MCP_PORT_ARGS and index + 1 < len(argv):
                try:
                    ports.add(int(argv[index + 1]))
                except ValueError:
                    pass
            else:
                name, sep, value = arg.partition("=")
                if sep and name in _MCP_PORT_ARGS:
                    try:
                        ports.add(int(value))
                    except ValueError:
                        pass
    return ports


async def _reserve_port_block(base_port: int, port_stride: int) -> dict[str, int]:
    if base_port <= 0:
        raise ValueError("opensage_base_port must be positive")
    if port_stride < 3:
        raise ValueError("opensage_port_stride must be at least 3")

    async with _PORT_LOCK:
        externally_used_ports = _listening_tcp_ports() | _active_opensage_runner_ports()
        for slot in range(1000):
            start = base_port + slot * port_stride
            ports = {
                "gdb_port": start,
                "ida_pro_mcp_port": start + 1,
                "pyghidra_mcp_port": start + 2,
            }
            values = set(ports.values())
            if values & _RESERVED_PORTS:
                continue
            if values & externally_used_ports:
                continue
            if not all(_port_is_available(port) for port in values):
                continue
            _RESERVED_PORTS.update(values)
            return ports

    raise RuntimeError(
        f"Could not reserve an OpenSAGE MCP port block starting at {base_port} "
        f"with stride {port_stride}."
    )


async def _release_port_block(ports: dict[str, int]) -> None:
    async with _PORT_LOCK:
        for port in ports.values():
            _RESERVED_PORTS.discard(port)


def _write_bridge_status(path: Path, status: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=2), encoding="utf-8")


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


_SESSION_ID_RE = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)
_SESSION_ID_HINT_RE = re.compile(
    r"(?:Created OpenSageSession for session:|for session:?)\s*"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
)
_PLACEHOLDER_CONTAINER_ID_RE = re.compile(
    r"Connected to existing container ([0-9a-f]{12,64}) \(image: alpine:latest\)"
)
_OPENSAGE_CONTAINER_PREFIXES = (
    "opensage_main_",
    "opensage_pwn_tools_",
    "opensage_gdb_mcp_",
    "opensage_victim_",
    "opensage_placeholder_",
    "opensage__placeholder_",
)
_FLAG_RE = re.compile(r"flag\{([^{}]+)\}")
_PLACEHOLDER_FLAG_VALUES = {"...", "TEST_SECRET"}
_USER_EVENT_AUTHORS = {"user", "__user__"}


def _extract_submitted_flag(text: str) -> str:
    for match in _FLAG_RE.finditer(text or ""):
        inner = match.group(1).strip()
        if inner and inner not in _PLACEHOLDER_FLAG_VALUES:
            return match.group(0)
    return ""


def _iter_string_values(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_string_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_string_values(item)


def _iter_event_content_strings(content: Any):
    if isinstance(content, dict) and isinstance(content.get("parts"), list):
        for part in content["parts"]:
            if not isinstance(part, dict):
                yield from _iter_string_values(part)
            elif "text" in part:
                yield from _iter_string_values(part["text"])
            elif "function_response" in part:
                yield from _iter_string_values(part["function_response"])
        return
    yield from _iter_string_values(content)


def _extract_submitted_flag_from_event(event: Any) -> str:
    if not isinstance(event, dict):
        return ""
    author = str(
        event.get("author")
        or event.get("from_agent_name")
        or event.get("from_sid")
        or ""
    ).strip()
    if author in _USER_EVENT_AUTHORS:
        return ""
    if "content" not in event:
        return ""
    for text in _iter_event_content_strings(event.get("content")):
        submitted_flag = _extract_submitted_flag(text)
        if submitted_flag:
            return submitted_flag
    return ""


def _extract_submitted_flag_from_json_payload(payload: Any) -> str:
    if isinstance(payload, dict):
        events = payload.get("events")
        if isinstance(events, list):
            for event in events:
                submitted_flag = _extract_submitted_flag_from_event(event)
                if submitted_flag:
                    return submitted_flag
            return ""
        return _extract_submitted_flag_from_event(payload)
    if isinstance(payload, list):
        for item in payload:
            submitted_flag = _extract_submitted_flag_from_json_payload(item)
            if submitted_flag:
                return submitted_flag
    return ""


def _extract_submitted_flag_from_json_file(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return _extract_submitted_flag_from_json_payload(payload)


def _extract_submitted_flag_from_jsonl_file(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return ""
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        submitted_flag = _extract_submitted_flag_from_json_payload(payload)
        if submitted_flag:
            return submitted_flag
    return ""


def _read_opensage_summary(output_dir: Path) -> dict[str, Any]:
    summary_path = output_dir / "cybingym_result.json"
    if not summary_path.exists():
        return {}
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _extract_flag_from_opensage_outputs(output_dir: Path) -> str:
    summary = _read_opensage_summary(output_dir)
    submitted_flag = _extract_submitted_flag(str(summary.get("submitted_flag") or ""))
    if submitted_flag:
        return submitted_flag

    json_paths = [output_dir / "session_trace.json"]
    json_paths.extend(output_dir.glob("*/session_trace.json"))
    jsonl_paths = [output_dir / "live_events.jsonl"]
    jsonl_paths.extend(output_dir.glob("*/live_events.jsonl"))

    for path in json_paths:
        if not path.exists():
            continue
        submitted_flag = _extract_submitted_flag_from_json_file(path)
        if submitted_flag:
            return submitted_flag
    for path in jsonl_paths:
        if not path.exists():
            continue
        submitted_flag = _extract_submitted_flag_from_jsonl_file(path)
        if submitted_flag:
            return submitted_flag

    instances_dir = output_dir / "instances"
    if instances_dir.exists():
        for path in instances_dir.rglob("*.json"):
            submitted_flag = _extract_submitted_flag_from_json_file(path)
            if submitted_flag:
                return submitted_flag
        for path in instances_dir.rglob("*.jsonl"):
            submitted_flag = _extract_submitted_flag_from_jsonl_file(path)
            if submitted_flag:
                return submitted_flag
    return ""


def _extract_opensage_session_ids(output_dir: Path) -> list[str]:
    hinted_session_ids: list[str] = []
    fallback_session_ids: list[str] = []
    for log_name in (
        "evaluation_master.log",
        "opensage_stdout.log",
        "opensage_stderr.log",
    ):
        log_path = output_dir / log_name
        if not log_path.exists():
            continue
        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in _SESSION_ID_HINT_RE.finditer(text):
            session_id = match.group(1)
            if session_id not in hinted_session_ids:
                hinted_session_ids.append(session_id)
        for match in _SESSION_ID_RE.finditer(text):
            session_id = match.group(0)
            if session_id not in fallback_session_ids:
                fallback_session_ids.append(session_id)
    return hinted_session_ids or fallback_session_ids


def _extract_opensage_placeholder_container_ids(output_dir: Path) -> list[str]:
    container_ids: list[str] = []
    for log_name in (
        "evaluation_master.log",
        "opensage_stdout.log",
        "opensage_stderr.log",
    ):
        log_path = output_dir / log_name
        if not log_path.exists():
            continue
        try:
            text = log_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in _PLACEHOLDER_CONTAINER_ID_RE.finditer(text):
            container_id = match.group(1)
            if container_id not in container_ids:
                container_ids.append(container_id)
    return container_ids


def _salvage_artifact_from_shared_volume(
    *,
    client: Any,
    volume_name: str,
    output_dir: Path,
    artifact_name: str,
) -> dict[str, Any]:
    artifact_path = output_dir / artifact_name
    status: dict[str, Any] = {
        "volume": volume_name,
        "artifact": artifact_name,
        "ok": False,
        "path": str(artifact_path),
    }
    if artifact_path.exists():
        status["ok"] = True
        status["already_exists"] = True
        return status

    container = None
    try:
        helper_image = "alpine:latest"
        helper_name = f"cybingym_{artifact_name}_extract_{volume_name}"
        try:
            stale = client.containers.get(helper_name)
            stale.remove(force=True)
        except Exception:
            pass

        container = client.containers.create(
            helper_image,
            name=helper_name,
            command=["sh", "-c", "sleep 30"],
            volumes={volume_name: {"bind": "/data", "mode": "ro"}},
        )
        container.start()
        container_artifact_path = f"/data/{artifact_name}"
        test_result = container.exec_run(["test", "-f", container_artifact_path])
        if test_result.exit_code != 0:
            status["error"] = f"{artifact_name} not present in shared volume"
            return status

        stream, _ = container.get_archive(container_artifact_path)
        tar_bytes = b"".join(stream)
        with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
            member = tar.getmembers()[0]
            fileobj = tar.extractfile(member)
            if fileobj is None:
                status["error"] = f"failed to extract {artifact_name} from archive"
                return status
            artifact_path.write_bytes(fileobj.read())

        status["ok"] = True
        status["bytes"] = artifact_path.stat().st_size
        return status
    except Exception as exc:
        status["error"] = f"{type(exc).__name__}: {exc}"
        return status
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass


def _cleanup_opensage_artifacts(output_dir: Path) -> dict[str, Any]:
    session_ids = _extract_opensage_session_ids(output_dir)
    placeholder_container_ids = _extract_opensage_placeholder_container_ids(output_dir)
    status: dict[str, Any] = {
        "session_ids": session_ids,
        "placeholder_container_ids": placeholder_container_ids,
        "poc_crash_salvage": [],
        "poc_salvage": [],
        "containers_removed": [],
        "volumes_removed": [],
        "networks_removed": [],
        "errors": [],
    }
    if not session_ids and not placeholder_container_ids:
        return status

    try:
        import docker

        cleanup_timeout = int(os.environ.get("OPENSAGE_DOCKER_CLEANUP_TIMEOUT", "60"))
        client = docker.from_env(timeout=cleanup_timeout)
        artifact_salvage_keys = (
            ("poc_crash", "poc_crash_salvage"),
            ("poc", "poc_salvage"),
        )
        for session_id in session_ids:
            volume_name = f"{session_id}_shared"
            missing_artifacts: list[tuple[str, str]] = []
            for artifact_name, salvage_key in artifact_salvage_keys:
                artifact_path = output_dir / artifact_name
                if artifact_path.exists():
                    status[salvage_key].append(
                        {
                            "volume": volume_name,
                            "artifact": artifact_name,
                            "ok": True,
                            "path": str(artifact_path),
                            "already_exists": True,
                        }
                    )
                else:
                    missing_artifacts.append((artifact_name, salvage_key))

            if not missing_artifacts:
                continue

            try:
                client.volumes.get(volume_name)
            except Exception as exc:
                for artifact_name, salvage_key in missing_artifacts:
                    status[salvage_key].append(
                        {
                            "volume": volume_name,
                            "artifact": artifact_name,
                            "ok": False,
                            "path": str(output_dir / artifact_name),
                            "error": f"volume lookup: {type(exc).__name__}: {exc}",
                        }
                    )
                continue

            for artifact_name, salvage_key in missing_artifacts:
                status[salvage_key].append(
                    _salvage_artifact_from_shared_volume(
                        client=client,
                        volume_name=volume_name,
                        output_dir=output_dir,
                        artifact_name=artifact_name,
                    )
                )

        for container in client.containers.list(all=True):
            name = getattr(container, "name", "")
            container_id = getattr(container, "id", "")
            name_matches = name.startswith(_OPENSAGE_CONTAINER_PREFIXES) and any(
                session_id in name for session_id in session_ids
            )
            placeholder_matches = any(
                container_id.startswith(placeholder_id)
                or placeholder_id.startswith(container_id[:12])
                for placeholder_id in placeholder_container_ids
            )
            if not name_matches and not placeholder_matches:
                continue
            try:
                container.remove(force=True)
                status["containers_removed"].append(name)
            except Exception as exc:
                status["errors"].append(
                    f"container {name}: {type(exc).__name__}: {exc}"
                )

        for volume in client.volumes.list():
            name = getattr(volume, "name", "")
            if not any(session_id in name for session_id in session_ids):
                continue
            try:
                volume.remove(force=True)
                status["volumes_removed"].append(name)
            except Exception as exc:
                status["errors"].append(f"volume {name}: {type(exc).__name__}: {exc}")

        for network in client.networks.list():
            name = getattr(network, "name", "")
            if not name.startswith("cybingym_"):
                continue
            if not any(session_id in name for session_id in session_ids):
                continue
            try:
                network.remove()
            except Exception as exc:
                status["errors"].append(f"network {name}: {type(exc).__name__}: {exc}")
            else:
                status["networks_removed"].append(name)

    except Exception as exc:
        status["errors"].append(f"docker cleanup: {type(exc).__name__}: {exc}")

    return status

def _has_recoverable_poc_crash(output_dir: Path) -> bool:
    return (output_dir / "poc_crash").exists()


def _uncancel_current_task() -> None:
    current_task = asyncio.current_task()
    if current_task is None or not hasattr(current_task, "uncancel"):
        return
    while current_task.cancelling():
        current_task.uncancel()


async def _import_opensage_outputs_into_state(
    *,
    state: TaskState,
    sample_output_dir: Path,
    bridge_status: dict[str, Any],
    bridge_status_path: Path,
    returncode: int,
    ports: dict[str, int],
    stdout_path: Path,
    stderr_path: Path,
    interrupted: bool = False,
) -> TaskState:
    poc_crash_path = sample_output_dir / "poc_crash"
    poc_path = sample_output_dir / "poc"
    poc_crash_exists = poc_crash_path.is_file()
    poc_exists = poc_path.is_file()
    submitted_flag = _extract_flag_from_opensage_outputs(sample_output_dir)
    bridge_status["submitted_flag"] = submitted_flag

    if poc_crash_exists:
        await sandbox().write_file("poc_crash", poc_crash_path.read_bytes())
    if poc_exists:
        await sandbox().write_file("poc", poc_path.read_bytes())

    bridge_status["poc_crash_exists"] = poc_crash_exists
    bridge_status["poc_crash_copied_to_inspect_sandbox"] = poc_crash_exists
    bridge_status["poc_exists"] = poc_exists
    bridge_status["poc_copied_to_inspect_sandbox"] = poc_exists

    if poc_crash_exists and interrupted:
        bridge_status["cancelled_artifacts_imported"] = True
        bridge_status["status"] = "cancelled_artifacts_imported"

    _write_bridge_status(bridge_status_path, bridge_status)

    if poc_crash_exists:
        copied = ["poc_crash"]
        if poc_exists:
            copied.append("poc")
        copied_text = ", ".join(copied)
        message_lines = [
            f"OpenSAGE generated {copied_text} and copied available artifacts into the Inspect sandbox.",
            f"Return code: {returncode}",
            f"OpenSAGE output: {sample_output_dir}",
            f"MCP ports: {ports}",
        ]
        if interrupted:
            message_lines[0] = (
                f"OpenSAGE was interrupted after generating {copied_text}; the bridge "
                "recovered available artifacts and copied them into the Inspect sandbox."
            )
    else:
        poc_note = " OpenSAGE did produce poc." if poc_exists else ""
        message_lines = [
            f"OpenSAGE did not produce poc_crash for this sample.{poc_note}",
            f"Return code: {returncode}",
            f"OpenSAGE output: {sample_output_dir}",
            f"MCP ports: {ports}",
            f"stdout: {stdout_path}",
            f"stderr: {stderr_path}",
        ]
    if submitted_flag:
        message_lines.append(f"Submitted flag: {submitted_flag}")

    state.messages.append(
        ChatMessageAssistant(content="\n".join(message_lines), model="opensage-bridge")
    )
    state.completed = True
    return state

def _stale_inspect_network_warning() -> str | None:
    try:
        import docker

        client = docker.from_env(timeout=5)
        stale_networks: list[str] = []
        for network in client.networks.list():
            name = getattr(network, "name", "")
            if not name.startswith("inspect-cybingym-"):
                continue
            try:
                network.reload()
            except Exception:
                continue
            if not (network.attrs.get("Containers") or {}):
                stale_networks.append(name)

        if len(stale_networks) < STALE_INSPECT_NETWORK_WARNING_THRESHOLD:
            return None

        examples = ", ".join(stale_networks[:5])
        return (
            f"Detected {len(stale_networks)} empty inspect-cybingym Docker networks. "
            "If Docker reports address-pool exhaustion, remove stale networks manually. "
            f"Examples: {examples}"
        )
    except Exception:
        return None


async def _terminate_runner(process: asyncio.subprocess.Process) -> dict[str, Any]:
    status: dict[str, Any] = {
        "sent_terminate": False,
        "sent_kill": False,
        "terminate_grace_seconds": RUNNER_TERMINATE_GRACE_SECONDS,
    }

    if process.returncode is not None:
        status["returncode_after_termination"] = process.returncode
        return status

    try:
        process.terminate()
        status["sent_terminate"] = True
    except ProcessLookupError:
        status["returncode_after_termination"] = process.returncode
        return status

    try:
        await asyncio.wait_for(process.wait(), timeout=RUNNER_TERMINATE_GRACE_SECONDS)
    except asyncio.TimeoutError:
        if process.returncode is None:
            process.kill()
            status["sent_kill"] = True
        await process.wait()

    status["returncode_after_termination"] = process.returncode
    return status


async def _run_opensage(
    *,
    sample: dict[str, Any],
    output_dir: Path,
    cybingym_dir: Path,
    opensage_agent_dir: Path,
    opensage_source_dir: Path,
    opensage_python: Path,
    opensage_model: str,
    opensage_provider: str,
    opensage_reasoning_effort: str,
    ports: dict[str, int],
    max_llm_calls: int,
    max_workers: int,
    timeout: int,
    cleanup_grace: int,
    llm_retry_timeout: int,
    llm_retry_count: int,
    artifact_collection_mode: str,
) -> tuple[int, Path, Path, dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = output_dir / "opensage_stdout.log"
    stderr_path = output_dir / "opensage_stderr.log"

    config_path = opensage_agent_dir / "config.toml"
    pwn_tools_dockerfile = opensage_agent_dir / "main_sandbox" / "Dockerfile"

    with tempfile.TemporaryDirectory(prefix="cybingym-opensage-sample-") as tmpdir:
        sample_path = Path(tmpdir) / "sample.json"
        sample_path.write_text(json.dumps(sample, indent=2), encoding="utf-8")

        cmd = [
            str(opensage_python),
            "-m",
            "solvers.opensage_cybingym_runner",
            "--sample_json",
            str(sample_path),
            "--cybingym_dir",
            str(cybingym_dir),
            "--agent_dir",
            str(opensage_agent_dir),
            "--config_template_path",
            str(config_path),
            "--pwn_tools_dockerfile",
            str(pwn_tools_dockerfile),
            "--output_dir",
            str(output_dir),
            "--max_llm_calls",
            str(max_llm_calls),
            "--agent_timeout",
            str(timeout),
            "--max_workers",
            str(max_workers),
            "--llm_retry_timeout",
            str(llm_retry_timeout),
            "--llm_retry_count",
            str(llm_retry_count),
            "--reasoning_effort",
            str(opensage_reasoning_effort),
            "--artifact_collection_mode",
            str(artifact_collection_mode),
            "--non_interactive",
            "True",
            "--gdb_port",
            str(ports["gdb_port"]),
            "--ida_pro_mcp_port",
            str(ports["ida_pro_mcp_port"]),
            "--pyghidra_mcp_port",
            str(ports["pyghidra_mcp_port"]),
            "run",
        ]

        env = _build_env(
            cybingym_dir=cybingym_dir,
            opensage_agent_dir=opensage_agent_dir,
            opensage_source_dir=opensage_source_dir,
            opensage_model=opensage_model,
            opensage_provider=opensage_provider,
            opensage_reasoning_effort=opensage_reasoning_effort,
            llm_retry_timeout=llm_retry_timeout,
            llm_retry_count=llm_retry_count,
        )
        process_status: dict[str, Any] = {
            "agent_timeout_seconds": int(timeout),
            "bridge_timeout_seconds": int(timeout),
            "bridge_cleanup_grace_seconds": int(cleanup_grace),
            "bridge_wait_timeout_seconds": (
                None
                if int(timeout) <= 0
                else int(timeout) + max(0, int(cleanup_grace))
            ),
            "llm_retry_timeout": int(llm_retry_timeout),
            "llm_retry_count": int(llm_retry_count),
            "reasoning_effort": opensage_reasoning_effort,
            "artifact_collection_mode": artifact_collection_mode,
            "outer_timeout": False,
        }
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(cybingym_dir),
                env=env,
                stdout=stdout,
                stderr=stderr,
            )
            process_status["pid"] = process.pid
            wait_timeout = process_status["bridge_wait_timeout_seconds"]
            try:
                returncode = await asyncio.wait_for(process.wait(), timeout=wait_timeout)
            except asyncio.TimeoutError:
                process_status["outer_timeout"] = True
                process_status["outer_timed_out_at"] = _now_iso()
                process_status["termination"] = await _terminate_runner(process)
                returncode = process.returncode if process.returncode is not None else -1
            except asyncio.CancelledError:
                await _terminate_runner(process)
                raise

    return returncode, stdout_path, stderr_path, process_status


@solver
def opensage_solver(
    *,
    opensage_agent_dir: str = str(DEFAULT_OPENSAGE_AGENT_DIR),
    binary_analysis_agent_dir: str | None = None,
    output_dir: str = "",
    max_llm_calls: int = 600,
    max_workers: int = 10,
    timeout: int = 7200,
    cleanup_grace: int = 900,
    llm_retry_timeout: int = 600,
    llm_retry_count: int = 5,
    opensage_python: str = str(DEFAULT_OPENSAGE_PYTHON),
    opensage_source_dir: str = str(DEFAULT_OPENSAGE_SOURCE_DIR),
    opensage_model: str = "",
    opensage_provider: str = "",
    opensage_reasoning_effort: str = "",
    artifact_collection_mode: str = "minimal",
    base_port: int = 20000,
    port_stride: int = 10,
) -> Solver:
    """Run an OpenSAGE agent and import its generated CyBinGym artifacts."""

    selected_agent_dir = binary_analysis_agent_dir or opensage_agent_dir
    worker_limit = max(1, int(max_workers))
    run_semaphore = asyncio.Semaphore(worker_limit)

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        del generate

        sample_id = str(state.sample_id)
        run_stamp = datetime.datetime.now().strftime("%y%m%d_%H%M%S_%f")
        root_output_dir = (
            Path(output_dir).expanduser()
            if output_dir
            else CYBINGYM_DIR / "evals" / "opensage_inspect"
        )
        sample_output_dir = root_output_dir / sample_id / run_stamp

        sample = {
            "id": state.sample_id,
            "input": state.input_text,
            "target": _target_text(state),
            "metadata": state.metadata,
        }
        model_name = opensage_model or str(state.model)
        provider_name = opensage_provider or _infer_opensage_provider(model_name)
        reasoning_effort = str(
            opensage_reasoning_effort or os.environ.get("OPENSAGE_REASONING_EFFORT", "")
        ).strip()
        bridge_status_path = sample_output_dir / "opensage_bridge_status.json"
        bridge_status: dict[str, Any] = {
            "sample_id": sample_id,
            "status": "queued",
            "queued_at": _now_iso(),
            "model": model_name,
            "provider": provider_name,
            "reasoning_effort": reasoning_effort,
            "bridge_max_workers": worker_limit,
            "runner_max_workers": int(max_workers),
            "agent_timeout_seconds": int(timeout),
            "bridge_timeout_seconds": int(timeout),
            "bridge_cleanup_grace_seconds": int(cleanup_grace),
            "max_llm_calls": int(max_llm_calls),
            "llm_retry_timeout": int(llm_retry_timeout),
            "llm_retry_count": int(llm_retry_count),
            "artifact_collection_mode": artifact_collection_mode,
        }
        docker_warning = _stale_inspect_network_warning()
        if docker_warning:
            bridge_status["docker_network_warning"] = docker_warning
        _write_bridge_status(bridge_status_path, bridge_status)

        ports: dict[str, int] = {}
        returncode = -1
        stdout_path = sample_output_dir / "opensage_stdout.log"
        stderr_path = sample_output_dir / "opensage_stderr.log"
        was_cancelled = False
        async with run_semaphore:
            bridge_status.update(
                {
                    "status": "running",
                    "started_at": _now_iso(),
                }
            )
            _write_bridge_status(bridge_status_path, bridge_status)

            ports = await _reserve_port_block(int(base_port), int(port_stride))
            bridge_status["ports"] = ports
            _write_bridge_status(bridge_status_path, bridge_status)

            try:
                returncode, stdout_path, stderr_path, runner_status = await _run_opensage(
                    sample=sample,
                    output_dir=sample_output_dir,
                    cybingym_dir=CYBINGYM_DIR,
                    opensage_agent_dir=Path(selected_agent_dir).expanduser().resolve(),
                    opensage_source_dir=Path(opensage_source_dir).expanduser().resolve(),
                    opensage_python=Path(opensage_python).expanduser(),
                    opensage_model=model_name,
                    opensage_provider=provider_name,
                    opensage_reasoning_effort=reasoning_effort,
                    ports=ports,
                    max_llm_calls=max_llm_calls,
                    max_workers=max_workers,
                    timeout=timeout,
                    cleanup_grace=cleanup_grace,
                    llm_retry_timeout=llm_retry_timeout,
                    llm_retry_count=llm_retry_count,
                    artifact_collection_mode=artifact_collection_mode,
                )
                bridge_status.update(runner_status)
                bridge_status["returncode"] = returncode
                bridge_status["status"] = (
                    "outer_timeout" if runner_status.get("outer_timeout") else "finished"
                )
            except asyncio.CancelledError:
                was_cancelled = True
                bridge_status["status"] = "cancelled"
                bridge_status["cancelled_at"] = _now_iso()
            except Exception as exc:
                bridge_status["status"] = "error"
                bridge_status["error"] = f"{type(exc).__name__}: {exc}"
                raise
            finally:
                if ports:
                    await _release_port_block(ports)
                cleanup_status = _cleanup_opensage_artifacts(sample_output_dir)
                if cleanup_status["session_ids"] or cleanup_status["errors"]:
                    bridge_status["opensage_artifact_cleanup"] = cleanup_status
                bridge_status["finished_at"] = _now_iso()
                _write_bridge_status(bridge_status_path, bridge_status)

        if was_cancelled:
            if not _has_recoverable_poc_crash(sample_output_dir):
                raise asyncio.CancelledError()
            _uncancel_current_task()

        return await _import_opensage_outputs_into_state(
            state=state,
            sample_output_dir=sample_output_dir,
            bridge_status=bridge_status,
            bridge_status_path=bridge_status_path,
            returncode=returncode,
            ports=ports,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            interrupted=was_cancelled,
        )

    return solve
