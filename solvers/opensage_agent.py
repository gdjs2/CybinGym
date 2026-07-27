from __future__ import annotations

import asyncio
import datetime
import json
import os
import re
import socket
import sys
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


async def _reserve_port_block(base_port: int, port_stride: int) -> dict[str, int]:
    if base_port <= 0:
        raise ValueError("opensage_base_port must be positive")
    if port_stride < 3:
        raise ValueError("opensage_port_stride must be at least 3")

    async with _PORT_LOCK:
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
_OPENSAGE_CONTAINER_PREFIXES = (
    "opensage_main_",
    "opensage_pwn_tools_",
    "opensage_gdb_mcp_",
    "opensage_placeholder_",
)


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


def _cleanup_opensage_artifacts(output_dir: Path) -> dict[str, Any]:
    session_ids = _extract_opensage_session_ids(output_dir)
    status: dict[str, Any] = {
        "session_ids": session_ids,
        "containers_removed": [],
        "volumes_removed": [],
        "errors": [],
    }
    if not session_ids:
        return status

    try:
        import docker

        client = docker.from_env(timeout=10)
        for container in client.containers.list(all=True):
            name = getattr(container, "name", "")
            if not name.startswith(_OPENSAGE_CONTAINER_PREFIXES):
                continue
            if not any(session_id in name for session_id in session_ids):
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
    except Exception as exc:
        status["errors"].append(f"docker cleanup: {type(exc).__name__}: {exc}")

    return status


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
    ports: dict[str, int],
    max_llm_calls: int,
    max_workers: int,
    timeout: int,
    cleanup_grace: int,
    llm_retry_timeout: int,
    llm_retry_count: int,
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
            "--max_workers",
            str(max_workers),
            "--llm_retry_timeout",
            str(llm_retry_timeout),
            "--llm_retry_count",
            str(llm_retry_count),
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
            llm_retry_timeout=llm_retry_timeout,
            llm_retry_count=llm_retry_count,
        )
        process_status: dict[str, Any] = {
            "bridge_timeout_seconds": int(timeout),
            "bridge_cleanup_grace_seconds": int(cleanup_grace),
            "bridge_wait_timeout_seconds": int(timeout) + max(0, int(cleanup_grace)),
            "llm_retry_timeout": int(llm_retry_timeout),
            "llm_retry_count": int(llm_retry_count),
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
    max_llm_calls: int = 400,
    max_workers: int = 10,
    timeout: int = 7200,
    cleanup_grace: int = 900,
    llm_retry_timeout: int = 600,
    llm_retry_count: int = 5,
    opensage_python: str = str(DEFAULT_OPENSAGE_PYTHON),
    opensage_source_dir: str = str(DEFAULT_OPENSAGE_SOURCE_DIR),
    opensage_model: str = "",
    opensage_provider: str = "",
    base_port: int = 20000,
    port_stride: int = 10,
) -> Solver:
    """Run an OpenSAGE agent and import its generated CyBinGym PoC."""

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
        bridge_status_path = sample_output_dir / "opensage_bridge_status.json"
        bridge_status: dict[str, Any] = {
            "sample_id": sample_id,
            "status": "queued",
            "queued_at": _now_iso(),
            "model": model_name,
            "provider": provider_name,
            "bridge_max_workers": worker_limit,
            "runner_max_workers": int(max_workers),
            "bridge_timeout_seconds": int(timeout),
            "bridge_cleanup_grace_seconds": int(cleanup_grace),
            "llm_retry_timeout": int(llm_retry_timeout),
            "llm_retry_count": int(llm_retry_count),
        }
        docker_warning = _stale_inspect_network_warning()
        if docker_warning:
            bridge_status["docker_network_warning"] = docker_warning
        _write_bridge_status(bridge_status_path, bridge_status)

        ports: dict[str, int] = {}
        returncode = -1
        stdout_path = sample_output_dir / "opensage_stdout.log"
        stderr_path = sample_output_dir / "opensage_stderr.log"
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
                    ports=ports,
                    max_llm_calls=max_llm_calls,
                    max_workers=max_workers,
                    timeout=timeout,
                    cleanup_grace=cleanup_grace,
                    llm_retry_timeout=llm_retry_timeout,
                    llm_retry_count=llm_retry_count,
                )
                bridge_status.update(runner_status)
                bridge_status["returncode"] = returncode
                bridge_status["status"] = (
                    "outer_timeout" if runner_status.get("outer_timeout") else "finished"
                )
            except asyncio.CancelledError:
                bridge_status["status"] = "cancelled"
                bridge_status["cancelled_at"] = _now_iso()
                raise
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

        poc_path = sample_output_dir / "poc"
        poc_exists = poc_path.exists()
        if poc_exists:
            await sandbox().write_file("poc", poc_path.read_bytes())
            bridge_status["poc_exists"] = True
            bridge_status["poc_copied_to_inspect_sandbox"] = True
            _write_bridge_status(bridge_status_path, bridge_status)
            message = (
                "OpenSAGE generated poc and it was copied into the Inspect sandbox.\n"
                f"Return code: {returncode}\n"
                f"OpenSAGE output: {sample_output_dir}\n"
                f"MCP ports: {ports}"
            )
        else:
            bridge_status["poc_exists"] = False
            bridge_status["poc_copied_to_inspect_sandbox"] = False
            _write_bridge_status(bridge_status_path, bridge_status)
            message = (
                "OpenSAGE did not produce a poc for this sample.\n"
                f"Return code: {returncode}\n"
                f"OpenSAGE output: {sample_output_dir}\n"
                f"MCP ports: {ports}\n"
                f"stdout: {stdout_path}\n"
                f"stderr: {stderr_path}"
            )

        state.messages.append(
            ChatMessageAssistant(content=message, model="opensage-bridge")
        )
        state.completed = True
        return state

    return solve
