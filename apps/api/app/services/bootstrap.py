from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Project, ResearchRun, ResearchTask, Source
from .events import add_event
from .search import brave_search
from .crawler import add_crawl_targets, ensure_crawl_job


async def bootstrap_queued_runs(db: Session) -> int:
    runs = db.scalars(
        select(ResearchRun).where(ResearchRun.status.in_(["queued", "running", "reviewing"])).limit(5)
    ).all()
    processed = 0
    for run in runs:
        task = db.scalar(
            select(ResearchTask).where(ResearchTask.run_id == run.id, ResearchTask.role == "planning")
        )
        if not task:
            continue
        context = json.loads(task.context_json or "{}")
        if context.get("discovery_complete"):
            continue
        project = db.get(Project, run.project_id)
        crawl_job = ensure_crawl_job(db, run)
        discovery_query = context.get("gap_query") or project.topic
        try:
            results = await brave_search(discovery_query, count=min(20, run.source_budget))
            for item in results:
                existing = db.scalar(
                    select(Source).where(Source.project_id == project.id, Source.url == item["url"])
                )
                if not existing:
                    db.add(
                        Source(
                            project_id=project.id,
                            run_id=run.id,
                            url=item["url"],
                            title=item["title"],
                            status="discovered",
                        )
                    )
            add_crawl_targets(
                db,
                crawl_job,
                [
                    {
                        **item,
                        "query": discovery_query,
                        "relevance_reason": item.get("description", "Brave Search result"),
                    }
                    for item in results
                ],
            )
            context["search_results"] = results
            context["discovery_complete"] = True
            context["brave_enabled"] = bool(results)
            task.context_json = json.dumps(context, ensure_ascii=False)
            add_event(
                db,
                run.id,
                "discovery_complete",
                f"Initial discovery found {len(results)} sources." if results else "Agent-led discovery enabled; Brave is not configured.",
            )
            processed += 1
        except Exception as exc:
            context["discovery_complete"] = True
            context["discovery_error"] = str(exc)[:500]
            task.context_json = json.dumps(context, ensure_ascii=False)
            add_event(db, run.id, "discovery_failed", f"Brave discovery failed; agents may still research: {exc}")
            processed += 1
    if processed:
        db.commit()
    return processed
