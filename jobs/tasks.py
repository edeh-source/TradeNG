"""
marketplace/tasks.py
=====================
Celery tasks for async embedding computation and match scoring.

Why async / why Celery?
────────────────────────
  Both sentence-transformer and CLIP inference take 50–300 ms on CPU.
  Running them synchronously inside a web request would block every profile
  save and job post.  Instead:

  1. User saves profile / posts job  →  view returns immediately.
  2. post_save signal fires           →  task(s) queued in Redis.
  3. Celery worker picks them up      →  ML inference runs in background.
  4. Embeddings + CLIPMatch rows written → next page load shows fresh matches.

Embedding task chain (per job)
───────────────────────────────
  compute_job_embedding_task
      ├─ compute_job_text_embedding()     sentence-transformer 768-dim → text_embedding
      ├─ compute_job_clip_embedding()     CLIP text encoder 512-dim    → clip_embedding
      └─ compute_matches_for_job_task.delay()

  compute_worker_embedding_task
      ├─ compute_worker_text_embedding()  sentence-transformer 768-dim → text_embedding
      ├─ calculate_profile_completion()
      └─ compute_matches_for_worker_task.delay()

  compute_portfolio_image_task
      └─ compute_portfolio_image_embedding()  CLIP visual encoder 512-dim → clip_image_embedding

Task design principles
───────────────────────
  bind=True          — access to self.retry()
  max_retries=3      — retries on transient failures (OOM, DB blip)
  autoretry_for      — auto-retries on any Exception
  retry_backoff      — exponential back-off (30 s, 60 s, 120 s)
  acks_late=True     — re-queued if worker crashes mid-flight
  ignore_result=True — return values not needed in result backend

Routing (add to settings.py)
──────────────────────────────
  CELERY_TASK_ROUTES = {
      'jobs.tasks.compute_worker_embedding_task':  {'queue': 'embeddings'},
      'jobs.tasks.compute_job_embedding_task':     {'queue': 'embeddings'},
      'jobs.tasks.compute_portfolio_image_task':   {'queue': 'embeddings'},
      'jobs.tasks.compute_matches_for_job_task':   {'queue': 'matching'},
      'jobs.tasks.compute_matches_for_worker_task':{'queue': 'matching'},
  }
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
#  EMBEDDING TASKS
# ──────────────────────────────────────────────────────────────────────────────

@shared_task(
    bind=True,
    max_retries=3,
    acks_late=True,
    ignore_result=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
)
def compute_worker_embedding_task(self, worker_profile_id: str) -> None:
    """
    Encodes a WorkerProfile's text with sentence-transformers (768-dim)
    and saves it to WorkerProfile.text_embedding.

    On success:
      - Recalculates profile_completion.
      - Chains compute_matches_for_worker_task.
    """
    from jobs.service.matching_service import (
        compute_worker_embedding,
        calculate_profile_completion,
    )

    logger.info("Task: compute_worker_embedding for %s", worker_profile_id)

    success = compute_worker_embedding(worker_profile_id)
    if success:
        calculate_profile_completion(worker_profile_id)
        compute_matches_for_worker_task.delay(worker_profile_id)


@shared_task(
    bind=True,
    max_retries=3,
    acks_late=True,
    ignore_result=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
)
def compute_job_embedding_task(self, job_id: str) -> None:
    """
    Runs BOTH embedding encoders for a job, then triggers match computation.

    Step 1 — sentence-transformers (768-dim → Job.text_embedding)
        Used for text ↔ text similarity (50 % of the hybrid score).

    Step 2 — CLIP text encoder (512-dim → Job.clip_embedding)
        Used for cross-modal comparison: job description text vs worker
        portfolio IMAGES.  Storing it here avoids recomputing it on every
        match run.

    Step 3 — compute_matches_for_job_task
        Only fires if at least the sentence-transformer embedding succeeded,
        since that is the dominant signal.  CLIP failure is non-fatal.
    """
    from jobs.service.matching_service import (
        compute_job_embedding,
        compute_job_clip_embedding,
    )

    logger.info("Task: compute_job_embedding for %s", job_id)

    # Step 1: sentence-transformer (primary signal, must succeed)
    st_success = compute_job_embedding(job_id)

    if st_success:
        # Step 2: CLIP text encoder (secondary signal — failure is logged but
        # does not block match computation; image_score defaults to 0.5 neutral)
        clip_success = compute_job_clip_embedding(job_id)
        if not clip_success:
            logger.warning(
                "Task: CLIP text embedding failed for job %s — "
                "image scores will use neutral default (0.5) until it succeeds.",
                job_id,
            )

        # Step 3: trigger match computation regardless of CLIP result
        compute_matches_for_job_task.delay(job_id)


@shared_task(
    bind=True,
    max_retries=3,
    acks_late=True,
    ignore_result=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
)
def compute_portfolio_image_task(self, portfolio_item_id: str) -> None:
    """
    Encodes a PortfolioItem image with CLIP's visual encoder (512-dim)
    and saves it to PortfolioItem.clip_image_embedding.
    """
    from jobs.service.matching_service import compute_portfolio_image_embedding

    logger.info("Task: compute_portfolio_image for %s", portfolio_item_id)
    compute_portfolio_image_embedding(portfolio_item_id)


# ──────────────────────────────────────────────────────────────────────────────
#  MATCHING TASKS
# ──────────────────────────────────────────────────────────────────────────────

@shared_task(
    bind=True,
    max_retries=3,
    acks_late=True,
    ignore_result=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def compute_matches_for_job_task(self, job_id: str) -> None:
    """
    Computes CLIPMatch scores between a job and all workers in the same trade.
    Safe to re-run (idempotent upsert).
    """
    from jobs.service.matching_service import compute_matches_for_job

    logger.info("Task: compute_matches_for_job for %s", job_id)
    count = compute_matches_for_job(job_id)
    logger.info(
        "Task: compute_matches_for_job — %d rows written for %s", count, job_id
    )


@shared_task(
    bind=True,
    max_retries=3,
    acks_late=True,
    ignore_result=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
)
def compute_matches_for_worker_task(self, worker_profile_id: str) -> None:
    """
    Computes CLIPMatch scores between a worker and all active jobs in their trade.
    Safe to re-run (idempotent upsert).
    """
    from jobs.service.matching_service import compute_matches_for_worker

    logger.info("Task: compute_matches_for_worker for %s", worker_profile_id)
    count = compute_matches_for_worker(worker_profile_id)
    logger.info(
        "Task: compute_matches_for_worker — %d rows written for %s",
        count, worker_profile_id,
    )


# ──────────────────────────────────────────────────────────────────────────────
#  PERIODIC / MAINTENANCE TASKS
# ──────────────────────────────────────────────────────────────────────────────

@shared_task(ignore_result=True)
def recompute_all_embeddings_task() -> None:
    """
    Periodic maintenance task — re-encodes every worker and job.

    Run nightly via Celery Beat to keep embeddings fresh when a model is
    upgraded or the input text templates change.

    Add to settings.py:
        from celery.schedules import crontab
        CELERY_BEAT_SCHEDULE = {
            'recompute-all-embeddings': {
                'task': 'jobs.tasks.recompute_all_embeddings_task',
                'schedule': crontab(hour=2, minute=0),   # 2 AM daily
            },
        }
    """
    from jobs.models import WorkerProfile, Job

    logger.info("Periodic task: recomputing all embeddings.")

    worker_ids = list(WorkerProfile.objects.values_list('id', flat=True))
    for wid in worker_ids:
        compute_worker_embedding_task.delay(str(wid))

    job_ids = list(
        Job.objects.filter(status=Job.Status.ACTIVE).values_list('id', flat=True)
    )
    for jid in job_ids:
        compute_job_embedding_task.delay(str(jid))

    logger.info(
        "Periodic task: queued %d worker + %d job embedding tasks.",
        len(worker_ids), len(job_ids),
    )


@shared_task(ignore_result=True)
def expire_old_jobs_task() -> None:
    """
    Periodic task — marks jobs past their deadline as EXPIRED.
    Add to Celery Beat schedule to run hourly.
    """
    from django.utils import timezone
    from jobs.models import Job

    updated = Job.objects.filter(
        status=Job.Status.ACTIVE,
        deadline__lt=timezone.now().date(),
    ).update(status=Job.Status.EXPIRED)

    logger.info("expire_old_jobs_task: %d jobs expired.", updated)