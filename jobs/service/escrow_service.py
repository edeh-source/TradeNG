"""
marketplace/service/escrow_service.py
=====================================
Business-logic layer for the milestone-based escrow payment system.

All Paystack API interactions are encapsulated here so that views and
tasks only call high-level functions.  Each function handles its own
exceptions and logs errors via Python's logging module.

Required in settings.py:
    PAYSTACK_SECRET_KEY = env('PAYSTACK_SECRET_KEY')
    PAYSTACK_PUBLIC_KEY = env('PAYSTACK_PUBLIC_KEY')
    PAYSTACK_CALLBACK_URL = env('PAYSTACK_CALLBACK_URL')  # e.g. https://tradelink.ng/escrow/paystack/callback/
    PAYSTACK_WEBHOOK_SECRET = env('PAYSTACK_SECRET_KEY')  # same key used for webhook HMAC
"""

import logging
import uuid
from datetime import timedelta
from decimal import Decimal

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from jobs.models import (
    Contract,
    Dispute,
    Milestone,
    Notification,
    WorkerBankAccount,
)

logger = logging.getLogger(__name__)

User = get_user_model()

PAYSTACK_BASE = "https://api.paystack.co"
PAYSTACK_TIMEOUT = 30  # seconds


# ──────────────────────────────────────────────────────────────────────────────
#  INTERNAL HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _paystack_headers() -> dict:
    """Return the standard Paystack authorization headers."""
    return {
        "Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def _notify(user, notif_type, title, body, data=None):
    """Shortcut to create a Notification."""
    Notification.objects.create(
        user=user,
        notif_type=notif_type,
        title=title,
        body=body,
        data=data or {},
    )


# ──────────────────────────────────────────────────────────────────────────────
#  1. INITIALIZE MILESTONE PAYMENT
# ──────────────────────────────────────────────────────────────────────────────

def initialize_milestone_payment(milestone_id: str, employer_email: str) -> dict:
    """
    Calls Paystack Initialize Transaction API to generate a payment link
    for the employer to fund a milestone.

    - Amount is milestone.amount * 100 (Paystack uses kobo)
    - Metadata: milestone_id, contract_id, type='milestone_funding'
    - callback_url: settings.PAYSTACK_CALLBACK_URL
    - Saves the Paystack reference to milestone.paystack_payment_ref
    - Returns {'authorization_url': ..., 'reference': ...}
    """
    try:
        milestone = Milestone.objects.select_related("contract").get(pk=milestone_id)
    except Milestone.DoesNotExist:
        logger.error("initialize_milestone_payment: Milestone %s not found.", milestone_id)
        return {}

    amount_kobo = int(milestone.amount * 100)
    reference = f"tl_ms_{uuid.uuid4().hex[:24]}"

    payload = {
        "email": employer_email,
        "amount": amount_kobo,
        "reference": reference,
        "callback_url": settings.PAYSTACK_CALLBACK_URL,
        "metadata": {
            "milestone_id": str(milestone.pk),
            "contract_id": str(milestone.contract.pk),
            "type": "milestone_funding",
        },
    }

    try:
        resp = requests.post(
            f"{PAYSTACK_BASE}/transaction/initialize",
            json=payload,
            headers=_paystack_headers(),
            timeout=PAYSTACK_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status"):
            authorization_url = data["data"]["authorization_url"]
            ref = data["data"]["reference"]

            milestone.paystack_payment_ref = ref
            milestone.save(update_fields=["paystack_payment_ref", "updated_at"])

            logger.info(
                "initialize_milestone_payment: ref=%s for milestone %s",
                ref, milestone_id,
            )
            return {"authorization_url": authorization_url, "reference": ref}

        logger.error(
            "initialize_milestone_payment: Paystack returned status=false: %s",
            data.get("message"),
        )
        return {}

    except requests.RequestException:
        logger.exception("initialize_milestone_payment: request failed for %s", milestone_id)
        return {}


# ──────────────────────────────────────────────────────────────────────────────
#  2. VERIFY MILESTONE PAYMENT
# ──────────────────────────────────────────────────────────────────────────────

def verify_milestone_payment(reference: str) -> bool:
    """
    Calls Paystack Verify Transaction API with the reference.
    If status == 'success' and amount matches milestone.amount:
      - Sets milestone.status = FUNDED
      - Sets milestone.funded_at = now()
      - Creates notifications for the worker and employer.
    Returns True if verified successfully.
    """
    try:
        milestone = Milestone.objects.select_related(
            "contract__employer__user",
            "contract__worker__user",
        ).get(paystack_payment_ref=reference)
    except Milestone.DoesNotExist:
        logger.error("verify_milestone_payment: no milestone with ref %s", reference)
        return False

    try:
        resp = requests.get(
            f"{PAYSTACK_BASE}/transaction/verify/{reference}",
            headers=_paystack_headers(),
            timeout=PAYSTACK_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("status"):
            logger.error("verify_milestone_payment: Paystack status=false for %s", reference)
            return False

        txn = data["data"]
        if txn["status"] != "success":
            logger.warning("verify_milestone_payment: txn status=%s for %s", txn["status"], reference)
            return False

        # Verify amount matches (Paystack returns amount in kobo)
        expected_kobo = int(milestone.amount * 100)
        if txn["amount"] != expected_kobo:
            logger.error(
                "verify_milestone_payment: amount mismatch for %s — expected %d, got %d",
                reference, expected_kobo, txn["amount"],
            )
            return False

        # Update milestone
        now = timezone.now()
        milestone.status = Milestone.Status.FUNDED
        milestone.funded_at = now
        milestone.save(update_fields=["status", "funded_at", "updated_at"])

        # Activate contract if still pending
        contract = milestone.contract
        if contract.status == Contract.Status.PENDING:
            contract.status = Contract.Status.ACTIVE
            contract.save(update_fields=["status", "updated_at"])

        # Notify worker
        _notify(
            user=contract.worker.user,
            notif_type=Notification.NotifType.ESCROW_FUNDED,
            title=f'Milestone "{milestone.title}" has been funded',
            body=f'Milestone "{milestone.title}" has been funded. You can now begin work.',
            data={"milestone_id": str(milestone.pk), "contract_id": str(contract.pk)},
        )
        # Notify employer
        _notify(
            user=contract.employer.user,
            notif_type=Notification.NotifType.ESCROW_FUNDED,
            title=f'Payment secured in escrow',
            body=f'Your payment of \u20a6{milestone.amount:,.2f} for "{milestone.title}" is secured in escrow.',
            data={"milestone_id": str(milestone.pk), "contract_id": str(contract.pk)},
        )

        logger.info("verify_milestone_payment: milestone %s funded successfully.", milestone.pk)
        return True

    except requests.RequestException:
        logger.exception("verify_milestone_payment: request failed for ref %s", reference)
        return False


# ──────────────────────────────────────────────────────────────────────────────
#  3. SUBMIT MILESTONE WORK
# ──────────────────────────────────────────────────────────────────────────────

def submit_milestone_work(milestone_id: str, submission_note: str) -> bool:
    """
    Called when worker clicks 'Submit Work'.
    - Validates milestone.status == FUNDED
    - Sets milestone.status = IN_REVIEW
    - Sets milestone.submitted_at = now()
    - Sets milestone.auto_release_at = now() + timedelta(days=7)
    - Creates a Notification for the employer.
    Returns True on success.
    """
    try:
        milestone = Milestone.objects.select_related(
            "contract__employer__user",
        ).get(pk=milestone_id)
    except Milestone.DoesNotExist:
        logger.error("submit_milestone_work: Milestone %s not found.", milestone_id)
        return False

    if milestone.status != Milestone.Status.FUNDED:
        logger.warning(
            "submit_milestone_work: milestone %s has status %s, expected FUNDED.",
            milestone_id, milestone.status,
        )
        return False

    now = timezone.now()
    milestone.status = Milestone.Status.IN_REVIEW
    milestone.submitted_at = now
    milestone.auto_release_at = now + timedelta(days=7)
    milestone.submission_note = submission_note
    milestone.save(update_fields=[
        "status", "submitted_at", "auto_release_at", "submission_note", "updated_at",
    ])

    _notify(
        user=milestone.contract.employer.user,
        notif_type=Notification.NotifType.ESCROW_SUBMITTED,
        title=f'Work submitted: "{milestone.title}"',
        body=(
            f'Work has been submitted for "{milestone.title}". '
            f'Review and approve within 7 days or payment will be released automatically.'
        ),
        data={"milestone_id": str(milestone.pk), "contract_id": str(milestone.contract.pk)},
    )

    logger.info("submit_milestone_work: milestone %s submitted for review.", milestone_id)
    return True


# ──────────────────────────────────────────────────────────────────────────────
#  4. APPROVE MILESTONE
# ──────────────────────────────────────────────────────────────────────────────

def approve_milestone(milestone_id: str) -> bool:
    """
    Called when employer clicks 'Approve Work'.
    - Validates milestone.status == IN_REVIEW or DISPUTED
    - Sets milestone.status = APPROVED
    - Sets milestone.approved_at = now()
    - Calls release_milestone_to_worker(milestone_id)
    Returns True on success.
    """
    try:
        milestone = Milestone.objects.get(pk=milestone_id)
    except Milestone.DoesNotExist:
        logger.error("approve_milestone: Milestone %s not found.", milestone_id)
        return False

    if milestone.status not in (Milestone.Status.IN_REVIEW, Milestone.Status.DISPUTED):
        logger.warning(
            "approve_milestone: milestone %s has status %s, expected IN_REVIEW or DISPUTED.",
            milestone_id, milestone.status,
        )
        return False

    milestone.status = Milestone.Status.APPROVED
    milestone.approved_at = timezone.now()
    milestone.save(update_fields=["status", "approved_at", "updated_at"])

    logger.info("approve_milestone: milestone %s approved, triggering payout.", milestone_id)
    return release_milestone_to_worker(milestone_id)


# ──────────────────────────────────────────────────────────────────────────────
#  5. RELEASE MILESTONE TO WORKER  (core payout)
# ──────────────────────────────────────────────────────────────────────────────

def release_milestone_to_worker(milestone_id: str) -> bool:
    """
    The core payout function. Called by approve_milestone() and auto_release
    Celery task.

    - Validates worker has a WorkerBankAccount with paystack_recipient_code
    - Computes worker_amount = milestone.amount * (1 - contract.platform_fee_pct / 100)
    - If worker has no recipient_code, calls create_transfer_recipient() first
    - Calls Paystack Initiate Transfer API
    - Saves transfer_code to milestone.paystack_transfer_ref
    - Sets milestone.status = RELEASED, milestone.worker_amount
    - Creates Notifications for worker and employer
    Returns True on success.
    """
    try:
        milestone = Milestone.objects.select_related(
            "contract__employer__user",
            "contract__worker__user",
            "contract__worker",
        ).get(pk=milestone_id)
    except Milestone.DoesNotExist:
        logger.error("release_milestone_to_worker: Milestone %s not found.", milestone_id)
        return False

    contract = milestone.contract
    worker_profile = contract.worker

    # Get or validate bank account
    try:
        bank_account = WorkerBankAccount.objects.get(worker=worker_profile)
    except WorkerBankAccount.DoesNotExist:
        logger.error(
            "release_milestone_to_worker: worker %s has no bank account.",
            worker_profile.pk,
        )
        return False

    # Create transfer recipient if missing
    if not bank_account.paystack_recipient_code:
        recipient_code = create_transfer_recipient(str(bank_account.pk))
        if not recipient_code:
            logger.error(
                "release_milestone_to_worker: failed to create recipient for %s.",
                bank_account.pk,
            )
            return False
        bank_account.refresh_from_db()

    # Compute worker amount
    fee_pct = contract.platform_fee_pct
    worker_amount = milestone.amount * (Decimal("1") - fee_pct / Decimal("100"))
    worker_amount = worker_amount.quantize(Decimal("0.01"))
    worker_amount_kobo = int(worker_amount * 100)

    # Initiate transfer
    payload = {
        "source": "balance",
        "amount": worker_amount_kobo,
        "recipient": bank_account.paystack_recipient_code,
        "reason": f"TradeLink payout: {milestone.title}",
    }

    try:
        resp = requests.post(
            f"{PAYSTACK_BASE}/transfer",
            json=payload,
            headers=_paystack_headers(),
            timeout=PAYSTACK_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("status"):
            logger.error(
                "release_milestone_to_worker: Paystack transfer failed: %s",
                data.get("message"),
            )
            return False

        transfer_code = data["data"].get("transfer_code", "")

        milestone.paystack_transfer_ref = transfer_code
        milestone.status = Milestone.Status.RELEASED
        milestone.worker_amount = worker_amount
        milestone.save(update_fields=[
            "paystack_transfer_ref", "status", "worker_amount", "updated_at",
        ])

        # Check if all milestones are released → mark contract completed
        all_released = not contract.milestones.exclude(
            status=Milestone.Status.RELEASED,
        ).exists()
        if all_released:
            contract.status = Contract.Status.COMPLETED
            contract.save(update_fields=["status", "updated_at"])

        # Notify worker
        _notify(
            user=contract.worker.user,
            notif_type=Notification.NotifType.ESCROW_RELEASED,
            title=f'Payment sent: \u20a6{worker_amount:,.2f}',
            body=(
                f'Payment of \u20a6{worker_amount:,.2f} for "{milestone.title}" '
                f'has been sent to your bank account.'
            ),
            data={"milestone_id": str(milestone.pk), "contract_id": str(contract.pk)},
        )
        # Notify employer
        _notify(
            user=contract.employer.user,
            notif_type=Notification.NotifType.ESCROW_RELEASED,
            title=f'Milestone complete: "{milestone.title}"',
            body=(
                f'Milestone "{milestone.title}" is complete. '
                f'\u20a6{worker_amount:,.2f} paid to worker.'
            ),
            data={"milestone_id": str(milestone.pk), "contract_id": str(contract.pk)},
        )

        logger.info(
            "release_milestone_to_worker: milestone %s released — \u20a6%s to worker.",
            milestone_id, worker_amount,
        )
        return True

    except requests.RequestException:
        logger.exception(
            "release_milestone_to_worker: transfer request failed for milestone %s",
            milestone_id,
        )
        return False


# ──────────────────────────────────────────────────────────────────────────────
#  6. CREATE TRANSFER RECIPIENT
# ──────────────────────────────────────────────────────────────────────────────

def create_transfer_recipient(worker_bank_account_id: str) -> str:
    """
    Calls Paystack Create Transfer Recipient API.
    - POST https://api.paystack.co/transferrecipient
    - Saves recipient_code to WorkerBankAccount.paystack_recipient_code
    Returns recipient_code, or empty string on failure.
    """
    try:
        bank_account = WorkerBankAccount.objects.get(pk=worker_bank_account_id)
    except WorkerBankAccount.DoesNotExist:
        logger.error(
            "create_transfer_recipient: WorkerBankAccount %s not found.",
            worker_bank_account_id,
        )
        return ""

    payload = {
        "type": "nuban",
        "name": bank_account.account_name,
        "account_number": bank_account.account_number,
        "bank_code": bank_account.bank_code,
        "currency": "NGN",
    }

    try:
        resp = requests.post(
            f"{PAYSTACK_BASE}/transferrecipient",
            json=payload,
            headers=_paystack_headers(),
            timeout=PAYSTACK_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("status"):
            logger.error(
                "create_transfer_recipient: Paystack returned status=false: %s",
                data.get("message"),
            )
            return ""

        recipient_code = data["data"]["recipient_code"]
        bank_account.paystack_recipient_code = recipient_code
        bank_account.is_verified = True
        bank_account.save(update_fields=[
            "paystack_recipient_code", "is_verified", "updated_at",
        ])

        logger.info(
            "create_transfer_recipient: recipient %s created for bank account %s.",
            recipient_code, worker_bank_account_id,
        )
        return recipient_code

    except requests.RequestException:
        logger.exception(
            "create_transfer_recipient: request failed for %s",
            worker_bank_account_id,
        )
        return ""


# ──────────────────────────────────────────────────────────────────────────────
#  7. RAISE DISPUTE
# ──────────────────────────────────────────────────────────────────────────────

def raise_dispute(
    milestone_id: str,
    raised_by_user_id: int,
    reason: str,
    evidence=None,
) -> bool:
    """
    - Validates milestone.status in [IN_REVIEW, FUNDED]
    - Creates Dispute object
    - Sets milestone.status = DISPUTED
    - Notifies both parties and admin (users with is_staff=True)
    Returns True on success.
    """
    try:
        milestone = Milestone.objects.select_related(
            "contract__employer__user",
            "contract__worker__user",
        ).get(pk=milestone_id)
    except Milestone.DoesNotExist:
        logger.error("raise_dispute: Milestone %s not found.", milestone_id)
        return False

    valid_statuses = (Milestone.Status.IN_REVIEW, Milestone.Status.FUNDED)
    if milestone.status not in valid_statuses:
        logger.warning(
            "raise_dispute: milestone %s has status %s, expected IN_REVIEW or FUNDED.",
            milestone_id, milestone.status,
        )
        return False

    try:
        raised_by = User.objects.get(pk=raised_by_user_id)
    except User.DoesNotExist:
        logger.error("raise_dispute: User %s not found.", raised_by_user_id)
        return False

    # Create the dispute
    dispute = Dispute.objects.create(
        milestone=milestone,
        raised_by=raised_by,
        reason=reason,
        evidence=evidence,
    )

    milestone.status = Milestone.Status.DISPUTED
    milestone.save(update_fields=["status", "updated_at"])

    # Update contract status
    contract = milestone.contract
    contract.status = Contract.Status.DISPUTED
    contract.save(update_fields=["status", "updated_at"])

    # Notify both parties
    contract = milestone.contract
    for user in [contract.employer.user, contract.worker.user]:
        _notify(
            user=user,
            notif_type=Notification.NotifType.ESCROW_DISPUTE,
            title=f'Dispute raised: "{milestone.title}"',
            body=(
                f'A dispute has been raised on milestone "{milestone.title}" '
                f'by {raised_by.username}. Funds are locked until resolution.'
            ),
            data={
                "milestone_id": str(milestone.pk),
                "dispute_id": str(dispute.pk),
                "contract_id": str(contract.pk),
            },
        )

    # Notify admins
    admin_users = User.objects.filter(is_staff=True)
    for admin_user in admin_users:
        _notify(
            user=admin_user,
            notif_type=Notification.NotifType.ESCROW_DISPUTE,
            title=f'[ADMIN] Dispute requires attention',
            body=(
                f'Dispute raised on "{milestone.title}" '
                f'(Contract: {contract.title}) by {raised_by.username}. '
                f'Reason: {reason[:200]}'
            ),
            data={
                "milestone_id": str(milestone.pk),
                "dispute_id": str(dispute.pk),
                "contract_id": str(contract.pk),
            },
        )

    logger.info("raise_dispute: dispute created for milestone %s by user %s.", milestone_id, raised_by_user_id)
    return True


# ──────────────────────────────────────────────────────────────────────────────
#  8. RESOLVE DISPUTE
# ──────────────────────────────────────────────────────────────────────────────

def resolve_dispute(
    dispute_id: str,
    resolution: str,
    resolved_by_user_id: int,
    resolution_note: str = "",
) -> bool:
    """
    Admin-only. resolution is one of: RELEASED_TO_WORKER, REFUNDED_TO_EMPLOYER.
    - If RELEASED_TO_WORKER: calls release_milestone_to_worker()
    - If REFUNDED_TO_EMPLOYER: calls Paystack refund API, sets milestone.status = REFUNDED
    - Sets dispute.resolution, dispute.resolved_at, dispute.resolved_by
    - Notifies both parties of outcome
    Returns True on success.
    """
    try:
        dispute = Dispute.objects.select_related(
            "milestone__contract__employer__user",
            "milestone__contract__worker__user",
        ).get(pk=dispute_id)
    except Dispute.DoesNotExist:
        logger.error("resolve_dispute: Dispute %s not found.", dispute_id)
        return False

    try:
        resolved_by = User.objects.get(pk=resolved_by_user_id)
    except User.DoesNotExist:
        logger.error("resolve_dispute: User %s not found.", resolved_by_user_id)
        return False

    milestone = dispute.milestone
    contract = milestone.contract

    if resolution == Dispute.Resolution.RELEASED_TO_WORKER:
        success = release_milestone_to_worker(str(milestone.pk))
        if not success:
            logger.error(
                "resolve_dispute: failed to release milestone %s to worker.",
                milestone.pk,
            )
            return False

    elif resolution == Dispute.Resolution.REFUNDED_TO_EMPLOYER:
        # Call Paystack refund API
        if milestone.paystack_payment_ref:
            payload = {
                "transaction": milestone.paystack_payment_ref,
            }
            try:
                resp = requests.post(
                    f"{PAYSTACK_BASE}/refund",
                    json=payload,
                    headers=_paystack_headers(),
                    timeout=PAYSTACK_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()

                if not data.get("status"):
                    logger.error(
                        "resolve_dispute: Paystack refund failed: %s",
                        data.get("message"),
                    )
                    return False

            except requests.RequestException:
                logger.exception(
                    "resolve_dispute: refund request failed for milestone %s",
                    milestone.pk,
                )
                return False

        milestone.status = Milestone.Status.REFUNDED
        milestone.save(update_fields=["status", "updated_at"])

    else:
        logger.error("resolve_dispute: unsupported resolution '%s'.", resolution)
        return False

    # Update dispute
    dispute.resolution = resolution
    dispute.resolution_note = resolution_note
    dispute.resolved_at = timezone.now()
    dispute.resolved_by = resolved_by
    dispute.save(update_fields=[
        "resolution", "resolution_note", "resolved_at", "resolved_by",
    ])

    # Revert contract status if no more disputed milestones
    has_disputed = contract.milestones.filter(status=Milestone.Status.DISPUTED).exists()
    if not has_disputed and contract.status == Contract.Status.DISPUTED:
        contract.status = Contract.Status.ACTIVE
        contract.save(update_fields=["status", "updated_at"])

    # Notify both parties
    resolution_label = dict(Dispute.Resolution.choices).get(resolution, resolution)
    for user in [contract.employer.user, contract.worker.user]:
        _notify(
            user=user,
            notif_type=Notification.NotifType.ESCROW_DISPUTE,
            title=f'Dispute resolved: "{milestone.title}"',
            body=(
                f'The dispute on "{milestone.title}" has been resolved: '
                f'{resolution_label}. {resolution_note}'.strip()
            ),
            data={
                "milestone_id": str(milestone.pk),
                "dispute_id": str(dispute.pk),
                "contract_id": str(contract.pk),
            },
        )

    logger.info(
        "resolve_dispute: dispute %s resolved as %s by user %s.",
        dispute_id, resolution, resolved_by_user_id,
    )
    return True
