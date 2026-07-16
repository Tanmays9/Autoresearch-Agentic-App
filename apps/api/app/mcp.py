from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import get_db
from .models import Concept, CourseVersion, Project, Relationship, ResearchRun, ResearchTask, Source
from .schemas import AgentReviewResult, AgentTaskResult
from .security import require_local_token
from .services.orchestration import (
    _assemble_course,
    accept_submission,
    build_task_context,
    claim_task,
    create_run,
    create_task,
)


router = APIRouter(dependencies=[Depends(require_local_token)])
PROTOCOL_VERSION = "2025-03-26"


TOOLS = [
    {
        "name": "create_research_topic",
        "description": "Create a durable research project and optionally start its first research run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "topic": {"type": "string"},
                "goal": {"type": "string"},
                "learner_level": {"type": "string"},
                "start_immediately": {"type": "boolean", "default": True},
            },
            "required": ["title", "topic"],
        },
    },
    {
        "name": "start_research",
        "description": "Start a bounded agentic research run for an existing project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "task_budget": {"type": "integer", "default": 8, "minimum": 3, "maximum": 24},
                "source_budget": {"type": "integer", "default": 200, "minimum": 1, "maximum": 200},
            },
            "required": ["project_id"],
        },
    },
    {
        "name": "get_research_status",
        "description": "Get the current run status, budget use, and task counts.",
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
    },
    {
        "name": "list_research_tasks",
        "description": "List research tasks for a run.",
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
    },
    {
        "name": "claim_research_task",
        "description": "Lease the next compatible task to an interactive agent such as Kiro.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_name": {"type": "string"},
                "provider": {"type": "string", "enum": ["kiro", "codex", "claude", "gemini"], "default": "kiro"},
            },
            "required": ["agent_name"],
        },
    },
    {
        "name": "get_task_context",
        "description": "Read the isolated context and response requirements for a leased task.",
        "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
    },
    {
        "name": "submit_research_result",
        "description": "Submit structured research. The app independently fetches and verifies every citation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "provider": {"type": "string"},
                "result": {"type": "object"},
            },
            "required": ["task_id", "provider", "result"],
        },
    },
    {
        "name": "submit_research_review",
        "description": "Submit a structured review containing citation problems, conflicts, and bounded follow-ups.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "provider": {"type": "string"},
                "result": {"type": "object"},
            },
            "required": ["task_id", "provider", "result"],
        },
    },
    {
        "name": "create_followup_task",
        "description": "Create one bounded follow-up task while the run budget and depth limit permit it.",
        "inputSchema": {
            "type": "object",
            "properties": {"parent_task_id": {"type": "string"}, "objective": {"type": "string"}},
            "required": ["parent_task_id", "objective"],
        },
    },
    {
        "name": "cancel_research",
        "description": "Cancel a run and all queued or leased tasks.",
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
    },
    {
        "name": "generate_course_notes",
        "description": "Assemble a new cited Markdown course version from validated research.",
        "inputSchema": {"type": "object", "properties": {"project_id": {"type": "string"}}, "required": ["project_id"]},
    },
    {
        "name": "get_course_notes",
        "description": "Get the latest generated Markdown course.",
        "inputSchema": {"type": "object", "properties": {"project_id": {"type": "string"}}, "required": ["project_id"]},
    },
    {
        "name": "get_knowledge_graph",
        "description": "Get the project's current supported concepts and relationships.",
        "inputSchema": {"type": "object", "properties": {"project_id": {"type": "string"}}, "required": ["project_id"]},
    },
]


def tool_result(value: Any, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, default=str)}],
        "structuredContent": value if isinstance(value, dict) else {"result": value},
        "isError": is_error,
    }


def serialize_task(task: ResearchTask, db: Session, with_context: bool = False) -> dict:
    result = {
        "id": task.id,
        "run_id": task.run_id,
        "role": task.role,
        "objective": task.objective,
        "status": task.status,
        "depth": task.depth,
        "provider": task.assigned_provider,
        "lease_expires_at": task.lease_expires_at,
        "attempts": task.attempts,
        "max_attempts": task.max_attempts,
        "available_after": task.available_after,
    }
    if with_context:
        result["context"] = build_task_context(db, task)
    return result


def graph_payload(db: Session, project_id: str) -> dict:
    concepts = db.scalars(select(Concept).where(Concept.project_id == project_id, Concept.status == "supported")).all()
    relationships = db.scalars(select(Relationship).where(Relationship.project_id == project_id, Relationship.status == "supported")).all()
    return {
        "nodes": [
            {
                "id": item.id,
                "name": item.name,
                "type": item.concept_type,
                "summary": item.summary,
                "provenance": item.provenance,
            }
            for item in concepts
        ],
        "edges": [
            {
                "id": item.id,
                "source": item.source_concept_id,
                "target": item.target_concept_id,
                "type": item.relation_type,
                "status": item.status,
            }
            for item in relationships
        ],
    }


async def call_tool(name: str, args: dict, db: Session) -> dict:
    if name == "create_research_topic":
        project = Project(
            title=args["title"],
            topic=args["topic"],
            goal=args.get("goal") or "Create a cited, prerequisite-ordered course",
            learner_level=args.get("learner_level") or "Python and basic ML",
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        run = create_run(db, project) if args.get("start_immediately", True) else None
        return tool_result({"project_id": project.id, "run_id": run.id if run else None})
    if name == "start_research":
        project = db.get(Project, args["project_id"])
        if not project:
            return tool_result({"error": "project not found"}, True)
        run = create_run(db, project, args.get("task_budget", 8), args.get("source_budget", 200))
        return tool_result({"run_id": run.id, "status": run.status})
    if name == "get_research_status":
        run = db.get(ResearchRun, args["run_id"])
        if not run:
            return tool_result({"error": "run not found"}, True)
        counts = {}
        for task in run.tasks:
            counts[task.status] = counts.get(task.status, 0) + 1
        return tool_result({"id": run.id, "status": run.status, "tasks": counts, "budget": run.task_budget})
    if name == "list_research_tasks":
        tasks = db.scalars(select(ResearchTask).where(ResearchTask.run_id == args["run_id"]).order_by(ResearchTask.created_at)).all()
        return tool_result({"tasks": [serialize_task(item, db) for item in tasks]})
    if name == "claim_research_task":
        claimed = claim_task(db, f"mcp:{args['agent_name']}", args.get("provider", "kiro"), "interactive-mcp")
        return tool_result({"task": serialize_task(claimed[0], db, True) if claimed else None})
    if name == "get_task_context":
        task = db.get(ResearchTask, args["task_id"])
        return tool_result({"task": serialize_task(task, db, True)} if task else {"error": "task not found"}, not bool(task))
    if name in {"submit_research_result", "submit_research_review"}:
        task = db.get(ResearchTask, args["task_id"])
        if not task:
            return tool_result({"error": "task not found"}, True)
        expected = AgentReviewResult if name == "submit_research_review" else AgentTaskResult
        parsed = expected.model_validate(args["result"])
        submission = await accept_submission(
            db,
            task,
            args["provider"],
            "interactive-mcp",
            "mcp-research-v1",
            parsed.model_dump(mode="json"),
        )
        return tool_result({"submission_id": submission.id, "validation_status": submission.validation_status})
    if name == "create_followup_task":
        parent = db.get(ResearchTask, args["parent_task_id"])
        if not parent:
            return tool_result({"error": "parent task not found"}, True)
        run = db.get(ResearchRun, parent.run_id)
        if parent.depth >= run.followup_depth_limit or run.tasks_created >= run.task_budget:
            return tool_result({"error": "follow-up limit reached"}, True)
        task = create_task(db, run, "followup", args["objective"], depth=parent.depth + 1, parent_task_id=parent.id)
        db.commit()
        return tool_result({"task_id": task.id if task else None})
    if name == "cancel_research":
        run = db.get(ResearchRun, args["run_id"])
        if not run:
            return tool_result({"error": "run not found"}, True)
        run.status = "cancelled"
        for task in run.tasks:
            if task.status in {"queued", "leased", "running"}:
                task.status = "cancelled"
        db.commit()
        return tool_result({"run_id": run.id, "status": run.status})
    if name == "generate_course_notes":
        run = db.scalar(select(ResearchRun).where(ResearchRun.project_id == args["project_id"]).order_by(ResearchRun.created_at.desc()))
        if not run:
            return tool_result({"error": "no research run found"}, True)
        course = _assemble_course(db, run)
        db.commit()
        return tool_result({"course_id": course.id, "version": course.version})
    if name == "get_course_notes":
        course = db.scalar(select(CourseVersion).where(CourseVersion.project_id == args["project_id"]).order_by(CourseVersion.version.desc()))
        return tool_result({"markdown": course.markdown, "version": course.version} if course else {"markdown": None})
    if name == "get_knowledge_graph":
        return tool_result(graph_payload(db, args["project_id"]))
    return tool_result({"error": f"unknown tool: {name}"}, True)


def list_resources(db: Session) -> list[dict]:
    resources = []
    for project in db.scalars(select(Project).order_by(Project.updated_at.desc())).all():
        resources.extend(
            [
                {"uri": f"research://projects/{project.id}", "name": project.title, "mimeType": "application/json"},
                {"uri": f"knowledge://projects/{project.id}/graph", "name": f"{project.title} graph", "mimeType": "application/json"},
                {"uri": f"notes://projects/{project.id}/course", "name": f"{project.title} course", "mimeType": "text/markdown"},
                {"uri": f"research://projects/{project.id}/sources", "name": f"{project.title} sources", "mimeType": "application/json"},
            ]
        )
        run = db.scalar(select(ResearchRun).where(ResearchRun.project_id == project.id).order_by(ResearchRun.created_at.desc()))
        if run:
            resources.append({"uri": f"research://runs/{run.id}", "name": f"{project.title} latest run", "mimeType": "application/json"})
            for task in run.tasks:
                resources.append(
                    {"uri": f"research://tasks/{task.id}/context", "name": f"Task context: {task.role}", "mimeType": "application/json"}
                )
    return resources


def read_resource(db: Session, uri: str) -> dict:
    parts = uri.split("/")
    if uri.startswith("research://tasks/") and uri.endswith("/context"):
        task = db.get(ResearchTask, parts[-2])
        value = build_task_context(db, task) if task else {"error": "not found"}
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(value, default=str)}]}
    if uri.startswith("research://runs/"):
        run = db.get(ResearchRun, parts[-1])
        value = (
            {"id": run.id, "status": run.status, "task_budget": run.task_budget, "tasks": [serialize_task(task, db) for task in run.tasks]}
            if run
            else {"error": "not found"}
        )
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(value, default=str)}]}
    if uri.startswith("research://projects/") and uri.endswith("/sources"):
        project_id = parts[-2]
        sources = db.scalars(select(Source).where(Source.project_id == project_id)).all()
        value = [{"id": item.id, "url": item.url, "title": item.title, "status": item.status, "trust": item.trust_level} for item in sources]
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(value)}]}
    if uri.startswith("research://projects/"):
        project = db.get(Project, parts[-1])
        value = {"id": project.id, "title": project.title, "topic": project.topic, "goal": project.goal} if project else {"error": "not found"}
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(value)}]}
    if uri.startswith("knowledge://projects/"):
        return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(graph_payload(db, parts[-2]))}]}
    if uri.startswith("notes://projects/"):
        course = db.scalar(select(CourseVersion).where(CourseVersion.project_id == parts[-2]).order_by(CourseVersion.version.desc()))
        return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": course.markdown if course else "No course has been generated."}]}
    return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": "Resource not found."}]}


@router.post("/mcp")
async def mcp_endpoint(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    request_id = body.get("id")
    method = body.get("method")
    params = body.get("params") or {}
    if method and method.startswith("notifications/"):
        return Response(status_code=202)
    try:
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}, "resources": {"subscribe": False, "listChanged": False}},
                "serverInfo": {"name": "atlas-research", "version": "0.1.0"},
                "instructions": "Create topics, run bounded research, submit cited results, and inspect validated notes and graph resources.",
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            result = await call_tool(params.get("name", ""), params.get("arguments") or {}, db)
        elif method == "resources/list":
            result = {"resources": list_resources(db)}
        elif method == "resources/templates/list":
            result = {"resourceTemplates": []}
        elif method == "resources/read":
            result = read_resource(db, params.get("uri", ""))
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}})
        return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})
    except Exception as exc:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)[:1000]}},
            status_code=200,
        )
