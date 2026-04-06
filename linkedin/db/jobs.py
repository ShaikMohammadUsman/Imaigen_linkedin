import logging
from typing import Optional
from sqlalchemy import desc
from sqlalchemy.orm import Session
from datetime import datetime
from linkedin.db.models import JobStatus

logger = logging.getLogger(__name__)

def create_job(session: Session, handle: str, job_type: str, expected_limit: int = 0) -> int:
    """Create a new job and return its ID."""
    job = JobStatus(
        handle=handle,
        job_type=job_type,
        status="running",
        expected_limit=expected_limit
    )
    session.add(job)
    session.commit()
    logger.info(f"Started new {job_type} job (ID: {job.id}) for {handle}")
    return job.id

def update_job_progress(session: Session, job_id: int, profiles_processed: int):
    """Update the number of profiles processed."""
    job = session.query(JobStatus).filter_by(id=job_id).first()
    if job:
        job.profiles_processed = profiles_processed
        session.commit()

def end_job(session: Session, job_id: int, status: str, error_message: str = None):
    """Mark a job as completed, stopped, or failed."""
    job = session.query(JobStatus).filter_by(id=job_id).first()
    if job:
        job.status = status
        job.end_time = datetime.utcnow()
        if error_message:
            job.error_message = error_message
        session.commit()
        logger.info(f"Job {job.id} ended with status: {status}")

def get_recent_jobs(session: Session, handle: str, limit: int = 10):
    """Get the most recent jobs for an account."""
    return session.query(JobStatus).filter_by(handle=handle).order_by(desc(JobStatus.start_time)).limit(limit).all()
