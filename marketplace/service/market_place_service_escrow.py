"""
marketplace/service/marketplace_escrow_service.py
==================================================
Paystack escrow logic for the TradeLink NG marketplace.

Mirrors the pattern in jobs/service/escrow_service.py but adapted for
product orders instead of job milestones.

Flow
────
  1. initialize_order_payment()  →  Paystack payment link for buyer
  2. verify_order_payment()      →  webhook confirms charge.success
  3. buyer confirms receipt      →  confirm_order_receipt()
  4. release_order_to_seller()   →  Paystack Transfer API → seller bank
  5. 5% fee stays in platform balance

All money math uses Python Decimal — never float.
All Paystack API calls use 30-second timeouts and try/except.
"""

import logging
from decimal import Decimal

import requests
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

PAYSTACK_BASE = 'https://api.paystack.co'


def _headers():
    return {
        'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}',
        'Content-Type':  'application/json',
    }


# ──────────────────────────────────────────────────────────────────────────────
#  INITIALIZE PAYMENT
# ──────────────────────────────────────────────────────────────────────────────

def initialize_order_payment(order_id: str, buyer_email: str) -> dict:
    """
    Calls Paystack Initialize Transaction API to generate a payment link
    for the buyer to pay for the order.

    Args:
        order_id:    UUID string of the Order.
        buyer_email: Email address of the buyer for Paystack.

    Returns:
        {'authorization_url': ..., 'reference': ...} on success.
        {'error': ...} on failure.
    """
    from marketplace.models import Order

    try:
        order = Order.objects.get(pk=order_id)
    except Order.DoesNotExist:
        return {'error': f'Order {order_id} not found.'}

    amount_kobo = int(order.agreed_price * 100)

    payload = {
        'email':        buyer_email,
        'amount':       amount_kobo,
        'currency':     'NGN',
        'reference':    f'mktplace_{str(order.pk).replace("-", "")}',
        'callback_url': settings.PAYSTACK_CALLBACK_URL,
        'metadata': {
            'order_id':    str(order.pk),
            'product_id':  str(order.product_id),
            'type':        'marketplace_order',
        },
    }

    try:
        resp = requests.post(
            f'{PAYSTACK_BASE}/transaction/initialize',
            json=payload,
            headers=_headers(),
            timeout=30,
        )
        data = resp.json()
    except requests.RequestException as exc:
        logger.exception("initialize_order_payment: request failed for order %s", order_id)
        return {'error': str(exc)}

    if not data.get('status'):
        logger.error(
            "initialize_order_payment: Paystack error for order %s — %s",
            order_id, data.get('message'),
        )
        return {'error': data.get('message', 'Paystack error')}

    ref = data['data']['reference']
    Order.objects.filter(pk=order_id).update(paystack_payment_ref=ref)

    return {
        'authorization_url': data['data']['authorization_url'],
        'reference':         ref,
    }


# ──────────────────────────────────────────────────────────────────────────────
#  VERIFY PAYMENT (called by webhook charge.success)
# ──────────────────────────────────────────────────────────────────────────────

def verify_order_payment(reference: str) -> bool:
    """
    Verifies a Paystack transaction and marks the order as PAID.

    Called by the Paystack webhook handler on charge.success.

    Returns True if payment verified and order updated.
    """
    from marketplace.models import Order
    from jobs.models import Notification

    try:
        resp = requests.get(
            f'{PAYSTACK_BASE}/transaction/verify/{reference}',
            headers=_headers(),
            timeout=30,
        )
        data = resp.json()
    except requests.RequestException as exc:
        logger.exception("verify_order_payment: request failed for ref %s", reference)
        return False

    if not data.get('status') or data['data']['status'] != 'success':
        logger.warning(
            "verify_order_payment: transaction %s not successful — %s",
            reference, data.get('message'),
        )
        return False

    try:
        order = Order.objects.get(paystack_payment_ref=reference)
    except Order.DoesNotExist:
        logger.error(
            "verify_order_payment: no order found for reference %s", reference
        )
        return False

    if order.status != Order.Status.PENDING:
        logger.info(
            "verify_order_payment: order %s already processed (status=%s).",
            order.pk, order.status,
        )
        return True

    order.status  = Order.Status.PAID
    order.paid_at = timezone.now()
    order.compute_financials()
    order.save(update_fields=['status', 'paid_at', 'platform_fee_amount',
                              'seller_payout_amount'])

    # Mark product as reserved
    from marketplace.models import Product
    Product.objects.filter(pk=order.product_id).update(status=Product.Status.RESERVED)

    logger.info("verify_order_payment: order %s marked PAID.", order.pk)
    return True


# ──────────────────────────────────────────────────────────────────────────────
#  CONFIRM RECEIPT (buyer presses "I received this item")
# ──────────────────────────────────────────────────────────────────────────────

def confirm_order_receipt(order_id: str) -> bool:
    """
    Buyer confirms they received the item.
    Sets order to CONFIRMED and starts the 7-day auto-complete window.
    Triggers async payout to seller.

    Returns True on success.
    """
    from marketplace.models import Order
    from marketplace.tasks import process_order_payout_task
    from datetime import timedelta

    try:
        order = Order.objects.get(pk=order_id, status=Order.Status.PAID)
    except Order.DoesNotExist:
        logger.warning(
            "confirm_order_receipt: order %s not found or not PAID.", order_id
        )
        return False

    now = timezone.now()
    order.status           = Order.Status.CONFIRMED
    order.confirmed_at     = now
    order.auto_complete_at = now + timedelta(days=7)
    order.save(update_fields=['status', 'confirmed_at', 'auto_complete_at'])

    # Queue payout — seller gets money once buyer confirms
    process_order_payout_task.delay(order_id)

    logger.info("confirm_order_receipt: order %s confirmed by buyer.", order_id)
    return True


# ──────────────────────────────────────────────────────────────────────────────
#  RELEASE TO SELLER
# ──────────────────────────────────────────────────────────────────────────────

def release_order_to_seller(order_id: str) -> bool:
    """
    Transfers the seller's payout via Paystack Transfer API.
    Deducts 5% platform fee. Marks order as COMPLETED.

    Called by:
        - process_order_payout_task  (after buyer confirms)
        - auto_complete_orders_task  (7-day auto-complete)
        - Admin resolves dispute in seller's favour

    Returns True on success.
    """
    from marketplace.models import Order, OrderDispute
    from jobs.models import WorkerBankAccount

    try:
        order = Order.objects.select_related(
            'seller', 'seller__bank_account', 'product'
        ).get(pk=order_id)
    except Order.DoesNotExist:
        logger.error("release_order_to_seller: order %s not found.", order_id)
        return False

    if order.status == Order.Status.COMPLETED:
        logger.info("release_order_to_seller: order %s already completed.", order_id)
        return True

    # Ensure seller has a bank account with a recipient code
    try:
        bank = order.seller.bank_account
    except WorkerBankAccount.DoesNotExist:
        logger.error(
            "release_order_to_seller: seller %s has no bank account.", order.seller_id
        )
        return False

    if not bank.paystack_recipient_code:
        # Try to create recipient first
        from jobs.service.escrow_service import create_transfer_recipient
        try:
            create_transfer_recipient(str(bank.pk))
            bank.refresh_from_db()
        except Exception:
            logger.exception(
                "release_order_to_seller: could not create recipient for seller %s",
                order.seller_id,
            )
            return False

    if not order.seller_payout_amount:
        order.compute_financials()
        order.save(update_fields=['platform_fee_amount', 'seller_payout_amount'])

    payout_kobo = int(order.seller_payout_amount * 100)

    payload = {
        'source':    'balance',
        'amount':    payout_kobo,
        'recipient': bank.paystack_recipient_code,
        'reason':    f'TradeLink Marketplace: {order.product.title[:60]}',
    }

    try:
        resp = requests.post(
            f'{PAYSTACK_BASE}/transfer',
            json=payload,
            headers=_headers(),
            timeout=30,
        )
        data = resp.json()
    except requests.RequestException as exc:
        logger.exception(
            "release_order_to_seller: transfer request failed for order %s", order_id
        )
        return False

    if not data.get('status'):
        logger.error(
            "release_order_to_seller: Paystack transfer failed for order %s — %s",
            order_id, data.get('message'),
        )
        return False

    transfer_code = data['data'].get('transfer_code', '')
    now = timezone.now()

    order.paystack_transfer_ref = transfer_code
    order.status                = Order.Status.COMPLETED
    order.completed_at          = now
    order.save(update_fields=[
        'paystack_transfer_ref', 'status', 'completed_at',
    ])

    # Mark product as SOLD
    from marketplace.models import Product
    Product.objects.filter(pk=order.product_id).update(status=Product.Status.SOLD)

    logger.info(
        "release_order_to_seller: ₦%s transferred to seller %s for order %s.",
        order.seller_payout_amount, order.seller_id, order_id,
    )
    return True


# ──────────────────────────────────────────────────────────────────────────────
#  PRODUCT SEARCH SERVICE (semantic)
# ──────────────────────────────────────────────────────────────────────────────

def semantic_product_search(query: str, product_pks=None):
    """
    Encode `query` with the sentence-transformer and return a ranked list
    of (product_pk, score) tuples.

    Reuses the SAME text_encoder singleton as the jobs search —
    no extra memory, no second model load.

    Args:
        query:       The buyer's search string.
        product_pks: Optional list of PKs to restrict search to.

    Returns:
        List of (pk, score) tuples sorted by score descending, or None
        if semantic search is unavailable (fall back to icontains).
    """
    from marketplace.models import Product
    from jobs.service.text_encoder import text_encoder

    if not query or len(query.strip()) < 2:
        return None

    try:
        query_vec = text_encoder.encode(query.strip())

        qs = Product.objects.filter(
            status=Product.Status.ACTIVE,
            text_embedding__isnull=False,
        ).values('pk', 'text_embedding')

        if product_pks is not None:
            qs = qs.filter(pk__in=product_pks)

        rows = list(qs)
        if not rows:
            return None

        pks        = [r['pk'] for r in rows]
        embeddings = [r['text_embedding'] for r in rows]

        scores = text_encoder.batch_cosine_similarity(query_vec, embeddings)

        ranked = sorted(
            [(pk, score) for pk, score in zip(pks, scores) if score >= 0.15],
            key=lambda x: x[1],
            reverse=True,
        )
        return ranked

    except Exception as exc:
        logger.warning(
            "semantic_product_search failed for query %r — falling back. Error: %s",
            query, exc,
        )
        return None