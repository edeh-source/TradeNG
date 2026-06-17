"""
marketplace/tasks.py
=====================
Celery tasks for the TradeLink NG marketplace app.

Tasks
──────
  compute_product_embedding_task
      Encodes product text with the SAME sentence-transformer used by the
      jobs app (all-mpnet-base-v2, 768-dim). Reuses text_encoder singleton —
      the model is already loaded in memory, so this costs only ~30 ms.

  process_order_payout_task
      Releases seller funds via Paystack Transfer API after buyer confirms.

  auto_complete_orders_task
      Periodic task — auto-completes orders 7 days after buyer confirmation
      if no dispute is raised.

  expire_offers_task
      Periodic task — expires pending offers after 48 hours.
"""

import logging
from celery import shared_task

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
#  EMBEDDING
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
def compute_product_embedding_task(self, product_id: str) -> None:
    """
    Encodes a Product's text with the sentence-transformer (768-dim)
    and saves it to Product.text_embedding.

    Uses the SAME text_encoder singleton as the jobs app —
    no second model load, no extra memory.

    Input text format:
        "{category} {condition}. {brand}. {title}. {description}"
    """
    from django.utils import timezone
    from marketplace.models import Product
    from jobs.service.text_encoder import text_encoder

    logger.info("Task: compute_product_embedding for %s", product_id)

    try:
        product = Product.objects.select_related('category').get(pk=product_id)
    except Product.DoesNotExist:
        logger.warning("compute_product_embedding: product %s not found.", product_id)
        return

    input_text = product.get_embedding_text()
    if not input_text.strip():
        logger.warning(
            "compute_product_embedding: product %s has no text to encode.", product_id
        )
        return

    logger.info(
        "Product %s — embedding input: %s…", product_id, input_text[:120]
    )

    embedding = text_encoder.encode(input_text)
    Product.objects.filter(pk=product_id).update(
        text_embedding=embedding,
        text_embedding_updated=timezone.now(),
    )
    logger.info("compute_product_embedding: saved embedding for %s.", product_id)


# ──────────────────────────────────────────────────────────────────────────────
#  PAYOUT
# ──────────────────────────────────────────────────────────────────────────────

@shared_task(
    bind=True,
    max_retries=3,
    acks_late=True,
    ignore_result=True,
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
)
def process_order_payout_task(self, order_id: str) -> None:
    """
    Async wrapper around release_order_to_seller().
    Called after buyer confirms receipt so payout doesn't block the HTTP request.
    """
    from marketplace.service.marketplace_escrow_service import release_order_to_seller

    logger.info("Task: process_order_payout for %s", order_id)
    try:
        release_order_to_seller(order_id)
    except Exception as exc:
        logger.exception("Task: process_order_payout failed for %s", order_id)
        raise self.retry(exc=exc)


# ──────────────────────────────────────────────────────────────────────────────
#  PERIODIC TASKS
# ──────────────────────────────────────────────────────────────────────────────

@shared_task(ignore_result=True)
def auto_complete_orders_task() -> None:
    """
    Runs every hour via Celery Beat.
    Auto-completes orders where buyer confirmed receipt 7+ days ago
    and no dispute has been raised.

    Add to settings.py CELERY_BEAT_SCHEDULE:
        'auto-complete-orders': {
            'task': 'marketplace.tasks.auto_complete_orders_task',
            'schedule': crontab(minute=0),
        },
    """
    from django.utils import timezone
    from marketplace.models import Order
    from marketplace.service.marketplace_escrow_service import release_order_to_seller

    now = timezone.now()
    orders = Order.objects.filter(
        status=Order.Status.CONFIRMED,
        auto_complete_at__lte=now,
    ).exclude(dispute__isnull=False)

    count = 0
    for order in orders:
        try:
            release_order_to_seller(str(order.pk))
            count += 1
        except Exception:
            logger.exception(
                "auto_complete_orders_task: failed for order %s", order.pk
            )

    logger.info("auto_complete_orders_task: auto-completed %d orders.", count)


@shared_task(ignore_result=True)
def expire_offers_task() -> None:
    """
    Runs every hour via Celery Beat.
    Expires pending offers older than 48 hours.

    Add to settings.py CELERY_BEAT_SCHEDULE:
        'expire-offers': {
            'task': 'marketplace.tasks.expire_offers_task',
            'schedule': crontab(minute=30),
        },
    """
    from django.utils import timezone
    from marketplace.models import Offer

    updated = Offer.objects.filter(
        status=Offer.Status.PENDING,
        expires_at__lt=timezone.now(),
    ).update(status=Offer.Status.EXPIRED)

    logger.info("expire_offers_task: expired %d offers.", updated)


@shared_task(ignore_result=True)
def recompute_all_product_embeddings_task() -> None:
    """
    Periodic maintenance task — re-encodes every active product.
    Run nightly via Celery Beat after a model upgrade.

    Add to settings.py CELERY_BEAT_SCHEDULE:
        'recompute-product-embeddings': {
            'task': 'marketplace.tasks.recompute_all_product_embeddings_task',
            'schedule': crontab(hour=3, minute=0),
        },
    """
    from marketplace.models import Product

    product_ids = list(
        Product.objects.filter(status=Product.Status.ACTIVE)
        .values_list('id', flat=True)
    )
    for pid in product_ids:
        compute_product_embedding_task.delay(str(pid))

    logger.info(
        "recompute_all_product_embeddings_task: queued %d tasks.", len(product_ids)
    )