import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from ...db import SessionLocal
from ...dependencies import get_current_user, get_db
from ...enums import CrawlStatus, TranscriptRecipientType
from ...models import Project, ProjectTranscriptRecipient
from ...schemas import (
    ProjectCreate,
    ProjectCrawlStatus,
    ProjectLearningUpdate,
    ProjectRead,
    TranscriptRecipient,
)
from ...services.crawler import CrawlConfig, crawl_project

router = APIRouter()


def _normalize_domain(value: str) -> str:
    value = value.strip()
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    return value


def _ensure_project_owner(db: Session, owner_id: int, project_id: int) -> Project:
    project = (
        db.query(Project)
        .filter(Project.owner_id == owner_id, Project.id == project_id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/", response_model=list[ProjectRead])
def list_projects(db: Session = Depends(get_db), current_user=Depends(get_current_user)):
    projects = (
        db.query(Project)
        .filter(Project.owner_id == current_user.id)
        .order_by(Project.created_at.desc())
        .all()
    )
    return projects


@router.post("/", response_model=ProjectRead)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = Project(
        owner_id=current_user.id,
        name=payload.name,
        primary_domain=_normalize_domain(payload.primary_domain),
        public_token=secrets.token_urlsafe(16),
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.post("/{project_id}/crawl")
def start_crawl(
    project_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = (
        db.query(Project)
        .filter(Project.owner_id == current_user.id, Project.id == project_id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404)
    project.crawl_status = CrawlStatus.RUNNING
    db.commit()

    def run_task(project_id: int):
        with SessionLocal() as task_db:
            record = task_db.get(Project, project_id)
            if not record:
                return
            crawl_project(task_db, record, record.primary_domain, CrawlConfig())

    background_tasks.add_task(run_task, project.id)
    return {"status": "started"}


@router.get("/{project_id}/crawl-status", response_model=ProjectCrawlStatus)
def get_crawl_status(
    project_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = _ensure_project_owner(db, current_user.id, project_id)
    return ProjectCrawlStatus(
        status=project.crawl_status,
        last_crawled_at=project.last_crawled_at,
    )


@router.get("/{project_id}/learning", response_model=ProjectLearningUpdate)
def get_learning_settings(
    project_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = _ensure_project_owner(db, current_user.id, project_id)
    return ProjectLearningUpdate(
        learning_enabled=bool(project.learning_enabled),
        learning_sample_rate=project.learning_sample_rate,
    )


@router.post("/{project_id}/learning", response_model=ProjectLearningUpdate)
def update_learning_settings(
    project_id: int,
    payload: ProjectLearningUpdate,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = _ensure_project_owner(db, current_user.id, project_id)
    project.learning_enabled = 1 if payload.learning_enabled else 0
    project.learning_sample_rate = payload.learning_sample_rate
    db.commit()
    return payload


@router.get("/{project_id}/transcript-recipients", response_model=list[TranscriptRecipient])
def list_transcript_recipients(
    project_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = _ensure_project_owner(db, current_user.id, project_id)
    return project.transcript_recipients


@router.post("/{project_id}/transcript-recipients", response_model=TranscriptRecipient)
def add_transcript_recipient(
    project_id: int,
    recipient: TranscriptRecipient,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = _ensure_project_owner(db, current_user.id, project_id)
    entry = ProjectTranscriptRecipient(
        project_id=project.id,
        email=recipient.email,
        type=recipient.type,
        is_active=1 if recipient.is_active else 0,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/{project_id}/transcript-recipients/{recipient_id}")
def delete_transcript_recipient(
    project_id: int,
    recipient_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ = _ensure_project_owner(db, current_user.id, project_id)
    entry = (
        db.query(ProjectTranscriptRecipient)
        .filter(
            ProjectTranscriptRecipient.id == recipient_id,
            ProjectTranscriptRecipient.project_id == project_id,
        )
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Recipient not found")
    db.delete(entry)
    db.commit()
    return {"status": "deleted"}

