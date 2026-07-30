import json

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Claim, Evidence, ResearchTask, ReviewItem, Source, Submission
from app.services.evidence import normalize_text, verify_quote


def create_started_project(client):
    response = client.post(
        "/api/v1/projects",
        json={
            "title": "QLoRA Evaluation",
            "topic": "How to evaluate QLoRA fine-tunes",
            "start_immediately": True,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_project_creates_durable_planning_task(client):
    payload = create_started_project(client)
    project_id = payload["project"]["id"]
    detail = client.get(f"/api/v1/projects/{project_id}").json()
    assert detail["run"]["status"] == "queued"
    assert detail["run"]["task_budget"] == 20
    assert len(detail["tasks"]) == 1
    assert detail["tasks"][0]["role"] == "planning"


def test_runner_claims_and_submits_planning_task(client, auth_headers):
    payload = create_started_project(client)
    register = client.post(
        "/api/v1/runner/register",
        headers=auth_headers,
        json={
            "runner_id": "test-runner",
            "hostname": "test-host",
            "providers": [
                {"provider": "claude", "status": "available", "version": "test", "mode": "headless", "capabilities": {}},
                {"provider": "codex", "status": "available", "version": "test", "mode": "headless", "capabilities": {}},
            ],
        },
    )
    assert register.status_code == 200
    claimed = client.post(
        "/api/v1/runner/tasks/claim",
        headers=auth_headers,
        json={"runner_id": "test-runner", "provider": "claude", "cli_version": "test"},
    )
    assert claimed.status_code == 200
    task = claimed.json()["task"]
    assert task["role"] == "planning"
    result = {
        "summary": "A bounded curriculum plan",
        "subtopics": ["Dataset quality", "Quantization", "Task evaluation"],
        "claims": [],
        "concepts": [],
        "relationships": [],
        "note_section_markdown": "",
        "gaps": [],
        "proposed_followups": [],
    }
    submitted = client.post(
        f"/api/v1/runner/tasks/{task['id']}/submit",
        headers=auth_headers,
        json={
            "runner_id": "test-runner",
            "provider": "claude",
            "cli_version": "test",
            "result": result,
        },
    )
    assert submitted.status_code == 200, submitted.text
    detail = client.get(f"/api/v1/projects/{payload['project']['id']}").json()
    assert len([item for item in detail["tasks"] if item["role"] == "research"]) == 3


def test_runner_routes_are_token_protected(client):
    response = client.post(
        "/api/v1/runner/register",
        json={"runner_id": "no-token", "hostname": "host", "providers": []},
    )
    assert response.status_code == 401


def test_mcp_initialization_and_tools(client, auth_headers):
    initialized = client.post(
        "/mcp",
        headers=auth_headers,
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert initialized.status_code == 200
    assert initialized.json()["result"]["serverInfo"]["name"] == "atlas-research"
    tools = client.post(
        "/mcp",
        headers=auth_headers,
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ).json()["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert {"create_research_topic", "claim_research_task", "submit_research_review", "get_knowledge_graph"} <= names


def test_mcp_can_create_and_start_topic(client, auth_headers):
    response = client.post(
        "/mcp",
        headers=auth_headers,
        json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "create_research_topic",
                "arguments": {"title": "Transformers", "topic": "Transformer architecture", "start_immediately": True},
            },
        },
    )
    assert response.status_code == 200
    result = response.json()["result"]["structuredContent"]
    assert result["project_id"]
    assert result["run_id"]


def test_kiro_button_reserves_a_task_and_runner_claims_launch(client, auth_headers):
    payload = create_started_project(client)
    project_id = payload["project"]["id"]
    registered = client.post(
        "/api/v1/runner/register",
        headers=auth_headers,
        json={"runner_id": "kiro-host", "hostname": "test-host", "providers": []},
    )
    assert registered.status_code == 200
    requested = client.post(f"/api/v1/projects/{project_id}/research-with-kiro")
    assert requested.status_code == 202
    assert requested.json()["task"]["provider"] == "kiro"
    launch = client.post(
        "/api/v1/runner/kiro-launches/claim",
        headers=auth_headers,
        json={"runner_id": "kiro-host"},
    )
    assert launch.status_code == 200
    assert launch.json()["task"]["id"] == requested.json()["task"]["id"]
    duplicate = client.post(
        "/api/v1/runner/kiro-launches/claim",
        headers=auth_headers,
        json={"runner_id": "kiro-host"},
    )
    assert duplicate.json()["task"] is None


def test_codex_button_can_take_over_a_queued_kiro_task(client):
    payload = create_started_project(client)
    project_id = payload["project"]["id"]
    kiro = client.post(f"/api/v1/projects/{project_id}/research-with-kiro")
    assert kiro.status_code == 202
    requested = client.post(f"/api/v1/projects/{project_id}/research-with-codex")
    assert requested.status_code == 202
    assert requested.json()["task"]["provider"] == "codex"


def test_quote_verification_normalizes_whitespace_and_case():
    content = "LoRA freezes the pretrained model weights.\nIt injects trainable rank decomposition matrices."
    assert verify_quote(content, "lora FREEZES   the pretrained model weights")
    assert not verify_quote(content, "This quotation was invented by an agent")
    assert normalize_text("  A\n B  ") == "a b"


def test_research_settings_are_configurable(client):
    current = client.get("/api/v1/settings/research")
    assert current.status_code == 200
    assert current.json()["codex_concurrency"] == 3
    updated = client.patch(
        "/api/v1/settings/research",
        json={"codex_concurrency": 5, "codex_web_research": False, "default_source_budget": 200},
    )
    assert updated.status_code == 200
    assert updated.json()["codex_concurrency"] == 5
    assert updated.json()["codex_web_research"] is False


def test_three_codex_tasks_can_be_leased_in_parallel_and_stream_logs(client, auth_headers):
    payload = create_started_project(client)
    project_id = payload["project"]["id"]
    client.post(
        "/api/v1/runner/register",
        headers=auth_headers,
        json={
            "runner_id": "parallel-host",
            "hostname": "test-host",
            "providers": [{"provider": "codex", "status": "available", "version": "test", "mode": "headless"}],
        },
    )
    planning = client.post(
        "/api/v1/runner/tasks/claim",
        headers=auth_headers,
        json={"runner_id": "parallel-host", "provider": "codex", "cli_version": "test"},
    ).json()
    client.post(
        f"/api/v1/runner/tasks/{planning['task']['id']}/submit",
        headers=auth_headers,
        json={
            "runner_id": "parallel-host",
            "provider": "codex",
            "result": {
                "summary": "plan",
                "subtopics": ["one", "two", "three"],
                "claims": [],
                "concepts": [],
                "relationships": [],
                "source_candidates": [],
                "note_section_markdown": "",
                "gaps": [],
                "proposed_followups": [],
            },
        },
    )
    claims = [
        client.post(
            "/api/v1/runner/tasks/claim",
            headers=auth_headers,
            json={"runner_id": "parallel-host", "provider": "codex", "cli_version": "test"},
        ).json()
        for _ in range(3)
    ]
    assert len({item["task"]["id"] for item in claims}) == 3
    execution_id = claims[0]["execution_id"]
    logged = client.post(
        f"/api/v1/runner/executions/{execution_id}/events",
        headers=auth_headers,
        json={
            "runner_id": "parallel-host",
            "events": [
                {"stream": "stdout", "event_type": "tool", "content": "token=super-secret"},
                {"stream": "stdout", "event_type": "reasoning", "content": "hidden chain"},
                {"stream": "stderr", "event_type": "diagnostic", "content": r"C:\Users\person\private\task.txt failed"},
            ],
        },
    )
    assert logged.status_code == 200
    detail = client.get(f"/api/v1/executions/{execution_id}").json()
    assert "super-secret" not in detail["events"][0]["content"]
    assert detail["events"][1]["content"] == "[private reasoning event omitted]"
    assert "C:\\Users" not in detail["events"][2]["content"]
    listed = client.get(f"/api/v1/research-runs/{planning['task']['run_id']}/executions").json()
    assert len(listed["executions"]) == 4


def test_execution_cancel_then_task_retry(client, auth_headers):
    payload = create_started_project(client)
    client.post(
        "/api/v1/runner/register",
        headers=auth_headers,
        json={"runner_id": "cancel-host", "hostname": "test-host", "providers": []},
    )
    claim = client.post(
        "/api/v1/runner/tasks/claim",
        headers=auth_headers,
        json={"runner_id": "cancel-host", "provider": "codex"},
    ).json()
    cancelled = client.post(f"/api/v1/executions/{claim['execution_id']}/cancel")
    assert cancelled.json()["status"] == "cancel_requested"
    heartbeat = client.post(
        f"/api/v1/runner/tasks/{claim['task']['id']}/heartbeat",
        headers=auth_headers,
        json={"runner_id": "cancel-host", "provider": "codex"},
    )
    assert heartbeat.json()["cancel_requested"] is True
    failed = client.post(
        f"/api/v1/runner/tasks/{claim['task']['id']}/fail",
        headers=auth_headers,
        json={"runner_id": "cancel-host", "provider": "codex", "diagnostic": "agent execution cancelled"},
    )
    assert failed.json()["status"] == "cancelled"
    retried = client.post(f"/api/v1/tasks/{claim['task']['id']}/retry")
    assert retried.json()["status"] == "queued"


def test_provisional_draft_is_visible_before_final_course(client, auth_headers):
    payload = create_started_project(client)
    client.post(
        "/api/v1/runner/register",
        headers=auth_headers,
        json={"runner_id": "draft-host", "hostname": "test-host", "providers": []},
    )
    planning = client.post(
        "/api/v1/runner/tasks/claim",
        headers=auth_headers,
        json={"runner_id": "draft-host", "provider": "codex"},
    ).json()
    base = {"claims": [], "concepts": [], "relationships": [], "source_candidates": [], "gaps": [], "proposed_followups": []}
    client.post(
        f"/api/v1/runner/tasks/{planning['task']['id']}/submit",
        headers=auth_headers,
        json={"runner_id": "draft-host", "provider": "codex", "result": {**base, "summary": "plan", "subtopics": ["draft section"], "note_section_markdown": ""}},
    )
    research = client.post(
        "/api/v1/runner/tasks/claim",
        headers=auth_headers,
        json={"runner_id": "draft-host", "provider": "codex"},
    ).json()
    client.post(
        f"/api/v1/runner/tasks/{research['task']['id']}/submit",
        headers=auth_headers,
        json={"runner_id": "draft-host", "provider": "codex", "result": {**base, "summary": "Visible draft", "subtopics": [], "note_section_markdown": "## Draft body\nProvisional content."}},
    )
    draft = client.get(f"/api/v1/projects/{payload['project']['id']}/draft").json()
    assert "Visible draft" in draft["markdown"]
    assert "Provisional content" in draft["markdown"]


def test_review_workbench_returns_claim_quote_source_and_draft(client):
    payload = create_started_project(client)
    project_id = payload["project"]["id"]
    run_id = payload["run"]["id"]
    with SessionLocal() as db:
        task = db.scalar(select(ResearchTask).where(ResearchTask.run_id == run_id))
        submission = Submission(
            task_id=task.id,
            provider="codex",
            kind="result",
            payload_json=json.dumps({"summary": "Draft section", "note_section_markdown": "Evidence draft"}),
            validation_status="mixed",
        )
        db.add(submission)
        db.flush()
        claim = Claim(project_id=project_id, task_id=task.id, submission_id=submission.id, text="A reviewable claim")
        source = Source(project_id=project_id, run_id=run_id, url="https://example.com/source", title="Example")
        db.add_all([claim, source])
        db.flush()
        db.add(Evidence(claim_id=claim.id, source_id=source.id, quote="Exact submitted quotation", verified=False, error="quotation not found"))
        first = ReviewItem(project_id=project_id, run_id=run_id, submission_id=submission.id, claim_id=claim.id, category="unsupported_claim", message="Needs evidence")
        second = ReviewItem(project_id=project_id, run_id=run_id, submission_id=submission.id, claim_id=claim.id, category="single_source", message="Needs another source")
        db.add_all([first, second])
        db.commit()
        review_id = first.id
    response = client.get(f"/api/v1/projects/{project_id}/review-items?page_size=100")
    assert response.status_code == 200
    item = next(value for value in response.json()["items"] if value["id"] == review_id)
    assert item["claim"]["text"] == "A reviewable claim"
    assert item["evidence"][0]["quote"] == "Exact submitted quotation"
    assert item["evidence"][0]["source"]["title"] == "Example"
    assert item["submission"]["note_section_markdown"] == "Evidence draft"
    decided = client.patch(f"/api/v1/review-items/{review_id}", json={"decision": "reject"})
    assert decided.status_code == 200
    grouped = client.get(f"/api/v1/projects/{project_id}/review-items?page_size=100").json()["items"]
    assert all(value["status"] == "resolved" for value in grouped if (value.get("claim") or {}).get("id") == item["claim"]["id"])
