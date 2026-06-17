"""
marketplace/signals.py
=======================
Signals for the marketplace app.

Triggers
────────
  Product saved with text-relevant fields changed
      → compute_product_embedding_task.delay(product_pk)

  WorkerBankAccount saved (seller needs payout account)
      → create_transfer_recipient_task.delay(bank_account_pk)
      (reuses the same Paystack recipient as the jobs escrow)

  Offer accepted
      → Notification to buyer that offer was accepted

  Order status changes
      → Notifications to both buyer and seller
"""

import logging

from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)

_PRODUCT_EMBEDDING_FIELDS = frozenset({
    'title', 'description', 'category', 'category_id',
    'condition', 'brand', 'model_number',
})


def _fields_changed(update_fields, watched) -> bool:
    if update_fields is None:
        return True
    return bool(frozenset(update_fields) & watched)


# ──────────────────────────────────────────────────────────────────────────────
#  PRODUCT
# ──────────────────────────────────────────────────────────────────────────────

@receiver(post_save, sender='marketplace.Product')
def on_product_save(sender, instance, created, update_fields, **kwargs):
    """
    Re-compute sentence-transformer embedding when product text changes.
    Only fires for ACTIVE products so draft listings don't waste Celery cycles.
    """
    from marketplace.tasks import compute_product_embedding_task
    from marketplace.models import Product

    if instance.status != Product.Status.ACTIVE:
        return

    if created or _fields_changed(update_fields, _PRODUCT_EMBEDDING_FIELDS):
        logger.debug(
            "Product %s saved with embedding-relevant fields — queuing task.",
            instance.pk,
        )
        compute_product_embedding_task.delay(str(instance.pk))


# ──────────────────────────────────────────────────────────────────────────────
#  OFFER
# ──────────────────────────────────────────────────────────────────────────────

@receiver(post_save, sender='marketplace.Offer')
def on_offer_save(sender, instance, created, update_fields, **kwargs):
    """
    Notify relevant parties when an offer is created or its status changes.
    """
    from marketplace.models import Offer
    from jobs.models import Notification

    if created:
        # Notify seller of new offer
        Notification.objects.create(
            user=instance.product.seller.user,
            body=(
                f'You have a new offer of ₦{instance.offered_price:,.0f} '
                f'on your listing "{instance.product.title}".'
            ),
        )
        return

    if update_fields and 'status' in update_fields:
        if instance.status == Offer.Status.ACCEPTED:
            Notification.objects.create(
                user=instance.buyer,
                body=(
                    f'Your offer of ₦{instance.offered_price:,.0f} on '
                    f'"{instance.product.title}" was accepted! Proceed to payment.'
                ),
            )
        elif instance.status == Offer.Status.DECLINED:
            Notification.objects.create(
                user=instance.buyer,
                body=(
                    f'Your offer on "{instance.product.title}" was declined.'
                ),
            )
        elif instance.status == Offer.Status.COUNTERED:
            Notification.objects.create(
                user=instance.buyer,
                body=(
                    f'The seller countered your offer on '
                    f'"{instance.product.title}" with ₦{instance.counter_price:,.0f}.'
                ),
            )


# ──────────────────────────────────────────────────────────────────────────────
#  ORDER
# ──────────────────────────────────────────────────────────────────────────────

@receiver(post_save, sender='marketplace.Order')
def on_order_save(sender, instance, created, update_fields, **kwargs):
    """
    Notify buyer and seller on order status changes.
    """
    from marketplace.models import Order
    from jobs.models import Notification

    if created:
        Notification.objects.create(
            user=instance.seller.user,
            body=(
                f'New order placed for your listing "{instance.product.title}" '
                f'at ₦{instance.agreed_price:,.0f}. Awaiting buyer payment.'
            ),
        )
        return

    if not update_fields or 'status' not in update_fields:
        return

    if instance.status == Order.Status.PAID:
        Notification.objects.create(
            user=instance.seller.user,
            body=(
                f'Payment of ₦{instance.agreed_price:,.0f} for '
                f'"{instance.product.title}" is secured in escrow. '
                f'Arrange meetup with the buyer.'
            ),
        )
        Notification.objects.create(
            user=instance.buyer,
            body=(
                f'Your payment for "{instance.product.title}" is secured. '
                f'The seller will contact you to arrange handover.'
            ),
        )

    elif instance.status == Order.Status.CONFIRMED:
        Notification.objects.create(
            user=instance.seller.user,
            body=(
                f'Buyer confirmed receipt of "{instance.product.title}". '
                f'Your payout of ₦{instance.seller_payout_amount:,.0f} '
                f'is being processed.'
            ),
        )

    elif instance.status == Order.Status.COMPLETED:
        Notification.objects.create(
            user=instance.buyer,
            body=(
                f'Your order for "{instance.product.title}" is complete. '
                f'Please leave a review for the seller.'
            ),
        )

    elif instance.status == Order.Status.DISPUTED:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        for admin in User.objects.filter(is_staff=True):
            Notification.objects.create(
                user=admin,
                body=(
                    f'Dispute raised on order for "{instance.product.title}". '
                    f'Admin review required.'
                ),
            )