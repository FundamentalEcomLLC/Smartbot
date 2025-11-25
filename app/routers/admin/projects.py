import json
import logging
import secrets
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from email_validator import EmailNotValidError, validate_email
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ...db import SessionLocal
from ...dependencies import get_current_user, get_db
from ...enums import DocumentSourceType, IntegrationType, TranscriptRecipientType
from ...models import (
    BotConfig,
    CustomQA,
    Document,
    IntegrationConfig,
    Project,
    ProjectTranscriptRecipient,
)
from ...services.crawler import CrawlConfig, crawl_project
from ...services.knowledge import index_document_chunks, reembed_project_documents

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[2] / "templates"))


def _normalize_domain(value: str) -> str:
    value = value.strip()
    if not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    return value


def _ensure_project(db: Session, user_id: int, project_id: int) -> Project:
    project = (
        db.query(Project)
        .filter(Project.owner_id == user_id, Project.id == project_id)
        .first()
    )
    if not project:
        raise HTTPException(status_code=404)
    return project


@router.get("/projects")
def list_projects(
    request: Request, current_user=Depends(get_current_user), db: Session = Depends(get_db)
):
    projects = (
        db.query(Project)
        .filter(Project.owner_id == current_user.id)
        .order_by(Project.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "admin/projects.html", {"request": request, "projects": projects}
    )


@router.get("/projects/new")
def new_project(request: Request):
    return templates.TemplateResponse("admin/project_new.html", {"request": request})


@router.post("/projects")
def create_project(
    name: str = Form(...),
    primary_domain: str = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = Project(
        owner_id=current_user.id,
        name=name,
        primary_domain=_normalize_domain(primary_domain),
        public_token=secrets.token_urlsafe(16),
    )
    db.add(project)
    db.flush()
    bot_config = BotConfig(project_id=project.id)
    db.add(bot_config)
    db.commit()
    return RedirectResponse(url=f"/admin/projects/{project.id}", status_code=303)


@router.get("/projects/{project_id}")
def project_dashboard(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
    doc_q: str | None = Query(None, alias="doc_q"),
    page: int = Query(1, ge=1),
):
    project = _ensure_project(db, current_user.id, project_id)
    documents_query = db.query(Document).filter(Document.project_id == project.id)
    if doc_q:
        like = f"%{doc_q}%"
        documents_query = documents_query.filter(
            or_(Document.url_or_name.ilike(like), Document.raw_content.ilike(like))
        )
    page_size = 20
    total_docs = documents_query.count()
    total_pages = max((total_docs + page_size - 1) // page_size, 1)
    documents = (
        documents_query.order_by(Document.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    custom_qas = (
        db.query(CustomQA)
        .filter(CustomQA.project_id == project.id)
        .order_by(CustomQA.created_at.desc())
        .all()
    )
    integrations = (
        db.query(IntegrationConfig)
        .filter(IntegrationConfig.project_id == project.id)
        .order_by(IntegrationConfig.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "admin/project_detail.html",
        {
            "request": request,
            "project": project,
            "documents": documents,
            "custom_qas": custom_qas,
            "doc_q": doc_q or "",
            "page": page,
            "total_pages": total_pages,
            "total_docs": total_docs,
            "integrations": integrations,
            "integration_types": list(IntegrationType),
        },
    )


@router.post("/projects/{project_id}/crawl")
def trigger_crawl(
    project_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = _ensure_project(db, current_user.id, project_id)
    config = CrawlConfig()
    logger.info("Received crawl request for project %s (%s)", project.id, project.primary_domain)
    print(f"[crawl] enqueue project {project.id} ({project.primary_domain})", flush=True)

    def run_crawl_background(target_project_id: int, crawl_config: CrawlConfig) -> None:
        logger.info("Background crawl kickoff for project %s", target_project_id)
        print(f"[crawl] starting background worker for project {target_project_id}", flush=True)
        with SessionLocal() as task_db:
            try:
                task_project = task_db.get(Project, target_project_id)
                if task_project is None:
                    logger.warning("Project %s disappeared before crawl", target_project_id)
                    print(f"[crawl] project {target_project_id} missing", flush=True)
                    return
                crawl_project(task_db, task_project, task_project.primary_domain, crawl_config)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Background crawl failed for project %s: %s", target_project_id, exc)
                print(f"[crawl] background worker crashed for project {target_project_id}: {exc}", flush=True)

    background_tasks.add_task(run_crawl_background, project.id, config)
    return RedirectResponse(url=f"/admin/projects/{project.id}", status_code=303)


@router.post("/projects/{project_id}/manual-note")
def add_manual_note(
    project_id: int,
    content: str = Form(...),
    title: str = Form("Manual Note"),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = _ensure_project(db, current_user.id, project_id)
    document = Document(
        project_id=project.id,
        source_type=DocumentSourceType.MANUAL_ENTRY,
        url_or_name=title,
        raw_content=content,
        metadata_json={"title": title},
    )
    db.add(document)
    index_document_chunks(db, project.id, document)
    db.commit()
    return RedirectResponse(url=f"/admin/projects/{project.id}", status_code=303)


@router.post("/projects/{project_id}/custom-qa")
def add_custom_qa(
    project_id: int,
    question: str = Form(...),
    answer: str = Form(...),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = _ensure_project(db, current_user.id, project_id)
    entry = CustomQA(project_id=project.id, question=question, answer=answer)
    db.add(entry)
    db.commit()
    return RedirectResponse(url=f"/admin/projects/{project.id}", status_code=303)


@router.post("/projects/{project_id}/reembed")
def reembed_project(
    project_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = _ensure_project(db, current_user.id, project_id)

    def run_reembed(pid: int) -> None:
        with SessionLocal() as task_db:
            reembed_project_documents(task_db, pid)

    background_tasks.add_task(run_reembed, project.id)
    return RedirectResponse(url=f"/admin/projects/{project.id}", status_code=303)


@router.post("/projects/{project_id}/learning")
def update_learning_settings(
    project_id: int,
    learning_enabled: int = Form(0),
    learning_sample_rate: int = Form(100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = _ensure_project(db, current_user.id, project_id)
    project.learning_enabled = 1 if learning_enabled else 0
    project.learning_sample_rate = max(1, min(100, learning_sample_rate))
    db.commit()
    return RedirectResponse(url=f"/admin/projects/{project.id}", status_code=303)


def _validate_email(email_value: str) -> str:
    try:
        return validate_email(email_value).email
    except EmailNotValidError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid email: {exc}") from exc


@router.post("/projects/{project_id}/transcript-recipient")
def add_transcript_recipient(
    project_id: int,
    recipient_email: str = Form(...),
    recipient_type: TranscriptRecipientType = Form(TranscriptRecipientType.TO),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = _ensure_project(db, current_user.id, project_id)
    email_value = _validate_email(recipient_email.strip())
    entry = ProjectTranscriptRecipient(
        project_id=project.id,
        email=email_value,
        type=recipient_type,
        is_active=1,
    )
    db.add(entry)
    db.commit()
    return RedirectResponse(url=f"/admin/projects/{project.id}", status_code=303)


@router.post("/projects/{project_id}/transcript-recipient/{recipient_id}/toggle")
def toggle_transcript_recipient(
    project_id: int,
    recipient_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ = _ensure_project(db, current_user.id, project_id)
    recipient = (
        db.query(ProjectTranscriptRecipient)
        .filter(
            ProjectTranscriptRecipient.id == recipient_id,
            ProjectTranscriptRecipient.project_id == project_id,
        )
        .first()
    )
    if not recipient:
        raise HTTPException(status_code=404)
    recipient.is_active = 0 if recipient.is_active else 1
    db.commit()
    return RedirectResponse(url=f"/admin/projects/{project.id}", status_code=303)


@router.post("/projects/{project_id}/transcript-recipient/{recipient_id}/delete")
def delete_transcript_recipient(
    project_id: int,
    recipient_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ = _ensure_project(db, current_user.id, project_id)
    recipient = (
        db.query(ProjectTranscriptRecipient)
        .filter(
            ProjectTranscriptRecipient.id == recipient_id,
            ProjectTranscriptRecipient.project_id == project_id,
        )
        .first()
    )
    if not recipient:
        raise HTTPException(status_code=404)
    db.delete(recipient)
    db.commit()
    return RedirectResponse(url=f"/admin/projects/{project.id}", status_code=303)


@router.post("/projects/{project_id}/bot-config")
def update_bot_config(
    project_id: int,
    system_prompt: str = Form(...),
    additional_instructions: str = Form(""),
    temperature: float = Form(0.2),
    max_tokens: int = Form(700),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = _ensure_project(db, current_user.id, project_id)
    config = project.bot_config or BotConfig(project_id=project.id)
    config.system_prompt = system_prompt
    config.additional_instructions = additional_instructions or None
    config.temperature = temperature
    config.max_tokens = max_tokens
    db.add(config)
    db.commit()
    return RedirectResponse(url=f"/admin/projects/{project.id}", status_code=303)


@router.post("/projects/{project_id}/integrations")
def create_integration(
    project_id: int,
    type: IntegrationType = Form(...),
    config_body: str = Form("{}"),
    is_active: bool = Form(True),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    project = _ensure_project(db, current_user.id, project_id)
    try:
        config_json = json.loads(config_body or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc
    integration = IntegrationConfig(
        project_id=project.id,
        type=type,
        config_json=config_json,
        is_active=is_active,
    )
    db.add(integration)
    db.commit()
    return RedirectResponse(url=f"/admin/projects/{project.id}", status_code=303)


@router.post("/projects/{project_id}/integrations/{integration_id}/toggle")
def toggle_integration(
    project_id: int,
    integration_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_project(db, current_user.id, project_id)
    integration = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.id == integration_id,
            IntegrationConfig.project_id == project_id,
        )
        .first()
    )
    if not integration:
        raise HTTPException(status_code=404)
    integration.is_active = not integration.is_active
    db.commit()
    return RedirectResponse(url=f"/admin/projects/{project.id}", status_code=303)


@router.post("/projects/{project_id}/integrations/{integration_id}/delete")
def delete_integration(
    project_id: int,
    integration_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    _ensure_project(db, current_user.id, project_id)
    integration = (
        db.query(IntegrationConfig)
        .filter(
            IntegrationConfig.id == integration_id,
            IntegrationConfig.project_id == project_id,
        )
        .first()
    )
    if not integration:
        raise HTTPException(status_code=404)
    db.delete(integration)
    db.commit()
    return RedirectResponse(url=f"/admin/projects/{project.id}", status_code=303)
