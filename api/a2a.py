"""A2A v1.0: agent card at /.well-known/agent-card.json and a JSON-RPC endpoint at /a2a/v1.

Supported: SendMessage (data part `{"skill": ..., ...args}` or a text part, which is a search)
and GetTask (triage runs are tasks; awaiting human approval = TASK_STATE_INPUT_REQUIRED).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from agent.tools import ToolInputError, ToolService

A2A_VERSION = "1.0"
router = APIRouter(tags=["a2a"])

TASK_STATE = {
    "running": "TASK_STATE_WORKING",
    "submitted": "TASK_STATE_SUBMITTED",
    "awaiting_approval": "TASK_STATE_INPUT_REQUIRED",
    "needs_human": "TASK_STATE_INPUT_REQUIRED",
    "approved": "TASK_STATE_COMPLETED",
    "applied": "TASK_STATE_COMPLETED",
    "rejected": "TASK_STATE_REJECTED",
    "expired": "TASK_STATE_CANCELED",
    "failed": "TASK_STATE_FAILED",
}

SKILLS = [
    {
        "id": "search_issues",
        "name": "Search issues and docs",
        "description": "Hybrid (dense + BM25, reranked) search over past issues, PRs, discussions "
        "and docs of fastapi, pydantic and uv.",
        "tags": ["search", "github", "retrieval"],
        "examples": ['{"skill": "search_issues", "query": "yield dependency RuntimeError"}'],
        "inputModes": ["application/json", "text/plain"],
        "outputModes": ["application/json"],
    },
    {
        "id": "suggest_labels",
        "name": "Suggest labels",
        "description": "Classify an issue (type, area, severity) and propose labels from the "
        "maintainers' own vocabulary.",
        "tags": ["triage", "classification", "labels"],
        "examples": ['{"skill": "suggest_labels", "repo": "astral-sh/uv", "title": "..."}'],
        "inputModes": ["application/json"],
        "outputModes": ["application/json"],
    },
    {
        "id": "triage_issue",
        "name": "Triage an issue",
        "description": "Start the full triage workflow (classify, duplicates, grounded draft). "
        "The task stays INPUT_REQUIRED until a human approves it in OpsPilot.",
        "tags": ["triage", "workflow", "human-in-the-loop"],
        "examples": ['{"skill": "triage_issue", "repo": "fastapi/fastapi", "number": 123}'],
        "inputModes": ["application/json"],
        "outputModes": ["application/json"],
    },
]


def agent_card(base_url: str) -> dict[str, Any]:
    return {
        "name": "OpsPilot Triage Agent",
        "description": "Triage copilot for open-source issue trackers: grounded, cited drafts and "
        "label/duplicate suggestions with human approval. Runs on local models.",
        "supportedInterfaces": [
            {
                "url": f"{base_url}/a2a/v1",
                "protocolBinding": "JSONRPC",
                "protocolVersion": A2A_VERSION,
            }
        ],
        "provider": {"organization": "OpsPilot (portfolio project)", "url": base_url},
        "version": "0.1.0",
        "documentationUrl": f"{base_url}/docs",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        "defaultInputModes": ["application/json", "text/plain"],
        "defaultOutputModes": ["application/json"],
        "skills": SKILLS,
    }


def _rpc_error(rpc_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}
    )


def _rpc_result(rpc_id: Any, result: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": rpc_id, "result": result})


def _agent_message(data: dict[str, Any], context_id: str | None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "ROLE_AGENT",
        "parts": [{"data": data}],
        "messageId": str(uuid.uuid4()),
    }
    if context_id:
        message["contextId"] = context_id
    return {"message": message}


def _task(run: dict[str, Any] | None, run_id: str, status: str) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": run_id,
        "contextId": run_id,
        "status": {
            "state": TASK_STATE.get(status, "TASK_STATE_WORKING"),
            "timestamp": datetime.now(UTC).isoformat(),
        },
    }
    if run is not None:
        task["artifacts"] = [{"artifactId": f"{run_id}-suggestions", "parts": [{"data": run}]}]
    return {"task": task}


def get_tools(request: Request) -> ToolService:
    tools: ToolService | None = getattr(request.app.state, "tools", None)
    if tools is None:
        from api.mcp_server import default_tools

        tools = default_tools()
    return tools


@router.get("/.well-known/agent-card.json")
def well_known_card(request: Request, response: Response) -> dict[str, Any]:
    response.headers["Cache-Control"] = "public, max-age=3600"
    return agent_card(str(request.base_url).rstrip("/"))


async def _dispatch(tools: ToolService, method: str, params: dict[str, Any]) -> dict[str, Any]:
    if method == "GetTask":
        run_id = str(params.get("id", ""))
        run = await tools.get_run(run_id)
        if run is None:
            raise LookupError(run_id)
        return _task(run, run_id, str(run["status"]))
    message = params.get("message") or {}
    if message.get("role") != "ROLE_USER" or not isinstance(message.get("parts"), list):
        raise ToolInputError("params.message must be a ROLE_USER message with parts")
    data = next((p["data"] for p in message["parts"] if isinstance(p.get("data"), dict)), None)
    text = next((p["text"] for p in message["parts"] if isinstance(p.get("text"), str)), None)
    context_id = message.get("contextId")
    if data is None:
        if not text:
            raise ToolInputError("send a data part {skill, ...} or a text part")
        data = {"skill": "search_issues", "query": text}
    skill = data.get("skill")
    args = {k: v for k, v in data.items() if k != "skill"}
    calls: dict[str, Callable[..., Any]] = {
        "search_issues": tools.search_issues,
        "suggest_labels": tools.suggest_labels,
    }
    if skill in calls:
        try:
            return _agent_message(await calls[skill](**args), context_id)
        except TypeError as exc:
            raise ToolInputError(f"bad arguments for {skill}: {exc}") from exc
    if skill == "triage_issue":
        started = await tools.triage_issue(str(args.get("repo", "")), int(args.get("number", 0)))
        return _task(await tools.get_run(started["run_id"]), started["run_id"], started["status"])
    raise ToolInputError(f"unknown skill {skill!r}; see the agent card")


@router.post("/a2a/v1")
async def a2a_rpc(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        return _rpc_error(None, -32700, "Parse error")
    rpc_id = body.get("id") if isinstance(body, dict) else None
    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or "method" not in body:
        return _rpc_error(rpc_id, -32600, "Invalid Request")
    version = request.headers.get("A2A-Version", "") or "0.3"
    if version.split(".")[:2] != A2A_VERSION.split("."):
        return _rpc_error(rpc_id, -32009, f"A2A version {version} not supported; use {A2A_VERSION}")
    if body["method"] not in ("SendMessage", "GetTask"):
        return _rpc_error(rpc_id, -32601, "Method not found")
    try:
        result = await _dispatch(get_tools(request), body["method"], body.get("params") or {})
    except ToolInputError as exc:
        return _rpc_error(rpc_id, -32602, f"Invalid params: {exc}")
    except LookupError:
        return _rpc_error(rpc_id, -32001, "Task not found")
    return _rpc_result(rpc_id, result)
