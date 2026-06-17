"""
marketplace/views_escrow.py
============================
Class-based views for the milestone-based escrow payment system.

View map
────────
  ContractListView           GET   /escrow/contracts/
  ContractDetailView         GET   /escrow/contracts/<pk>/
  MilestoneCreateView        POST  /escrow/milestones/create/<contract_pk>/
  MilestoneFundView          POST  /escrow/milestones/<pk>/fund/
  PaystackCallbackView       GET   /escrow/paystack/callback/
  MilestoneSubmitWorkView    POST  /escrow/milestones/<pk>/submit/
  MilestoneApproveView       POST  /escrow/milestones/<pk>/approve/
  MilestoneDisputeView       POST  /escrow/milestones/<pk>/dispute/
  WorkerBankAccountView      GET/POST /escrow/bank-account/
  DisputeAdminResolveView    POST  /escrow/disputes/<pk>/resolve/
"""

import json
import logging
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from .models import (
    Contract,
    Milestone,
    WorkerBankAccount,
    Dispute,
    Notification,
)
from .views import (
    WorkerRequiredMixin,
    EmployerRequiredMixin,
    _unread_notification_count,
)
from .service.escrow_service import (
    initialize_milestone_payment,
    verify_milestone_payment,
    submit_milestone_work,
    approve_milestone,
    raise_dispute,
    resolve_dispute,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _is_ajax(request):
    """Check whether the request is an AJAX / fetch request."""
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def _get_user_contract_qs(user):
    """
    Return a Contract queryset filtered to contracts the user participates in
    (as either employer or worker).
    """
    qs = Contract.objects.select_related(
        'employer__user', 'worker__user', 'job',
    ).prefetch_related('milestones')

    if hasattr(user, 'employer_profile'):
        employer_qs = qs.filter(employer=user.employer_profile)
    else:
        employer_qs = Contract.objects.none()

    if hasattr(user, 'worker_profile'):
        worker_qs = qs.filter(worker=user.worker_profile)
    else:
        worker_qs = Contract.objects.none()

    return (employer_qs | worker_qs).distinct()


def _user_owns_contract(user, contract):
    """Return True if the user is either the employer or worker on a contract."""
    if hasattr(user, 'employer_profile') and contract.employer_id == user.employer_profile.pk:
        return True
    if hasattr(user, 'worker_profile') and contract.worker_id == user.worker_profile.pk:
        return True
    return False


# ──────────────────────────────────────────────────────────────────────────────
#  CONTRACT LIST
# ──────────────────────────────────────────────────────────────────────────────

class ContractListView(LoginRequiredMixin, View):
    """
    GET /escrow/contracts/
    Lists all contracts where the current user is either employer or worker.
    """
    template_name = 'marketplace/escrow/contract_list.html'

    def get(self, request):
        contracts = _get_user_contract_qs(request.user).order_by('-created_at')

        return render(request, self.template_name, {
            'contracts':    contracts,
            'unread_count': _unread_notification_count(request.user),
        })


# ──────────────────────────────────────────────────────────────────────────────
#  CONTRACT DETAIL
# ──────────────────────────────────────────────────────────────────────────────

class ContractDetailView(LoginRequiredMixin, View):
    """
    GET /escrow/contracts/<pk>/
    Shows a single contract with all its milestones.
    Only accessible by the employer or worker on the contract.
    """
    template_name = 'marketplace/escrow/contract_detail.html'

    def get(self, request, pk):
        contract = get_object_or_404(
            Contract.objects.select_related(
                'employer__user', 'worker__user', 'job',
            ).prefetch_related('milestones__dispute'),
            pk=pk,
        )

        if not _user_owns_contract(request.user, contract):
            raise Http404

        milestones = contract.milestones.order_by('display_order', 'created_at')

        # Determine user role for template
        is_employer = (
            hasattr(request.user, 'employer_profile')
            and contract.employer_id == request.user.employer_profile.pk
        )

        # Bank account status for workers
        has_bank_account = False
        if hasattr(request.user, 'worker_profile'):
            has_bank_account = WorkerBankAccount.objects.filter(
                worker=request.user.worker_profile,
            ).exists()

        return render(request, self.template_name, {
            'contract':         contract,
            'milestones':       milestones,
            'is_employer':      is_employer,
            'has_bank_account': has_bank_account,
            'unread_count':     _unread_notification_count(request.user),
        })


# ──────────────────────────────────────────────────────────────────────────────
#  MILESTONE CREATE
# ──────────────────────────────────────────────────────────────────────────────

class MilestoneCreateView(EmployerRequiredMixin, View):
    """
    POST /escrow/milestones/create/<contract_pk>/
    Employer creates one or more milestones for a contract.
    """

    def post(self, request, contract_pk):
        contract = get_object_or_404(
            Contract.objects.select_related('worker__user'),
            pk=contract_pk,
            employer=self.employer_profile,
        )

        title = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        amount_str = request.POST.get('amount', '').strip()
        due_date = request.POST.get('due_date') or None

        # Validation
        errors = []
        if not title:
            errors.append('Title is required.')
        if not description:
            errors.append('Description is required.')

        amount = None
        try:
            amount = Decimal(amount_str)
            if amount <= 0:
                errors.append('Amount must be greater than zero.')
        except (InvalidOperation, ValueError):
            errors.append('Enter a valid amount.')

        if errors:
            if _is_ajax(request):
                return JsonResponse({'errors': errors}, status=400)
            for e in errors:
                messages.error(request, e)
            return redirect('marketplace:contract_detail', pk=contract_pk)

        # Determine display order
        max_order = contract.milestones.count()

        milestone = Milestone.objects.create(
            contract=contract,
            title=title,
            description=description,
            amount=amount,
            due_date=due_date,
            display_order=max_order,
        )

        # Notify worker
        Notification.objects.create(
            user=contract.worker.user,
            notif_type=Notification.NotifType.SYSTEM,
            title=f'New milestone: "{milestone.title}"',
            body=(
                f'A new milestone "{milestone.title}" worth ₦{amount:,.2f} '
                f'has been created on your contract "{contract.title}".'
            ),
            data={
                'milestone_id': str(milestone.pk),
                'contract_id': str(contract.pk),
            },
        )

        if _is_ajax(request):
            return JsonResponse({
                'id': str(milestone.pk),
                'title': milestone.title,
                'amount': str(milestone.amount),
                'status': milestone.status,
            }, status=201)

        messages.success(request, f'Milestone "{title}" created.')
        return redirect('marketplace:contract_detail', pk=contract_pk)


# ──────────────────────────────────────────────────────────────────────────────
#  MILESTONE FUND
# ──────────────────────────────────────────────────────────────────────────────

class MilestoneFundView(EmployerRequiredMixin, View):
    """
    POST /escrow/milestones/<pk>/fund/
    Calls initialize_milestone_payment() and redirects to Paystack.
    """

    def post(self, request, pk):
        milestone = get_object_or_404(
            Milestone.objects.select_related('contract__employer'),
            pk=pk,
            contract__employer=self.employer_profile,
        )

        if milestone.status != Milestone.Status.UNFUNDED:
            messages.error(request, 'This milestone has already been funded or is not in a fundable state.')
            return redirect('marketplace:contract_detail', pk=milestone.contract.pk)

        result = initialize_milestone_payment(
            milestone_id=str(milestone.pk),
            employer_email=request.user.email,
        )

        if result and result.get('authorization_url'):
            return redirect(result['authorization_url'])

        messages.error(request, 'Could not initialize payment. Please try again.')
        return redirect('marketplace:contract_detail', pk=milestone.contract.pk)


# ──────────────────────────────────────────────────────────────────────────────
#  PAYSTACK CALLBACK
# ──────────────────────────────────────────────────────────────────────────────

class PaystackCallbackView(View):
    """
    GET /escrow/paystack/callback/
    Handles the redirect from Paystack after the employer completes payment.
    Public — no login required (Paystack redirects the browser here).
    """

    def get(self, request):
        reference = request.GET.get('reference', '')
        trxref = request.GET.get('trxref', reference)
        ref = reference or trxref

        if not ref:
            messages.error(request, 'No payment reference found.')
            return redirect('marketplace:dashboard')

        success = verify_milestone_payment(ref)

        if success:
            # Try to find the milestone to redirect to contract detail
            try:
                milestone = Milestone.objects.select_related('contract').get(
                    paystack_payment_ref=ref,
                )
                messages.success(request, f'Payment verified! Milestone "{milestone.title}" is now funded.')
                return redirect('marketplace:contract_detail', pk=milestone.contract.pk)
            except Milestone.DoesNotExist:
                messages.success(request, 'Payment verified successfully.')
                return redirect('marketplace:contract_list')

        messages.error(request, 'Payment verification failed. Please contact support if funds were deducted.')
        return redirect('marketplace:contract_list')


# ──────────────────────────────────────────────────────────────────────────────
#  MILESTONE SUBMIT WORK
# ──────────────────────────────────────────────────────────────────────────────

class MilestoneSubmitWorkView(WorkerRequiredMixin, View):
    """
    POST /escrow/milestones/<pk>/submit/
    Worker submits completed work for a milestone.
    """

    def post(self, request, pk):
        milestone = get_object_or_404(
            Milestone.objects.select_related('contract__worker'),
            pk=pk,
            contract__worker=self.worker_profile,
        )

        submission_note = request.POST.get('submission_note', '').strip()

        success = submit_milestone_work(
            milestone_id=str(milestone.pk),
            submission_note=submission_note,
        )

        if success:
            if _is_ajax(request):
                return JsonResponse({'status': 'submitted', 'milestone_id': str(milestone.pk)})
            messages.success(request, f'Work submitted for "{milestone.title}". The employer will review it.')
            return redirect('marketplace:contract_detail', pk=milestone.contract.pk)

        if _is_ajax(request):
            return JsonResponse({'error': 'Could not submit work. Check milestone status.'}, status=400)
        messages.error(request, 'Could not submit work. The milestone may not be in a fundable state.')
        return redirect('marketplace:contract_detail', pk=milestone.contract.pk)


# ──────────────────────────────────────────────────────────────────────────────
#  MILESTONE APPROVE
# ──────────────────────────────────────────────────────────────────────────────

class MilestoneApproveView(EmployerRequiredMixin, View):
    """
    POST /escrow/milestones/<pk>/approve/
    Employer approves submitted work and triggers payout.
    """

    def post(self, request, pk):
        milestone = get_object_or_404(
            Milestone.objects.select_related('contract__employer'),
            pk=pk,
            contract__employer=self.employer_profile,
        )

        success = approve_milestone(milestone_id=str(milestone.pk))

        if success:
            if _is_ajax(request):
                return JsonResponse({'status': 'approved', 'milestone_id': str(milestone.pk)})
            messages.success(request, f'Work approved! Payment for "{milestone.title}" is being processed.')
            return redirect('marketplace:contract_detail', pk=milestone.contract.pk)

        if _is_ajax(request):
            return JsonResponse({'error': 'Could not approve milestone.'}, status=400)
        messages.error(request, 'Could not approve this milestone. It may not be in a reviewable state.')
        return redirect('marketplace:contract_detail', pk=milestone.contract.pk)


# ──────────────────────────────────────────────────────────────────────────────
#  MILESTONE DISPUTE
# ──────────────────────────────────────────────────────────────────────────────

class MilestoneDisputeView(LoginRequiredMixin, View):
    """
    POST /escrow/milestones/<pk>/dispute/
    Either employer or worker can raise a dispute on a milestone.
    """

    def post(self, request, pk):
        milestone = get_object_or_404(
            Milestone.objects.select_related(
                'contract__employer__user',
                'contract__worker__user',
            ),
            pk=pk,
        )

        # Check that requesting user is a participant
        if not _user_owns_contract(request.user, milestone.contract):
            raise Http404

        reason = request.POST.get('reason', '').strip()
        if not reason:
            if _is_ajax(request):
                return JsonResponse({'error': 'A reason is required.'}, status=400)
            messages.error(request, 'Please provide a reason for the dispute.')
            return redirect('marketplace:contract_detail', pk=milestone.contract.pk)

        evidence = request.FILES.get('evidence')

        success = raise_dispute(
            milestone_id=str(milestone.pk),
            raised_by_user_id=request.user.pk,
            reason=reason,
            evidence=evidence,
        )

        if success:
            if _is_ajax(request):
                return JsonResponse({'status': 'disputed', 'milestone_id': str(milestone.pk)})
            messages.success(request, 'Dispute raised. An admin will review and resolve it.')
            return redirect('marketplace:contract_detail', pk=milestone.contract.pk)

        if _is_ajax(request):
            return JsonResponse({'error': 'Could not raise dispute.'}, status=400)
        messages.error(request, 'Could not raise a dispute on this milestone.')
        return redirect('marketplace:contract_detail', pk=milestone.contract.pk)


# ──────────────────────────────────────────────────────────────────────────────
#  WORKER BANK ACCOUNT
# ──────────────────────────────────────────────────────────────────────────────

class WorkerBankAccountView(WorkerRequiredMixin, View):
    """
    GET/POST /escrow/bank-account/
    Worker creates or updates their bank account for receiving payouts.
    """
    template_name = 'marketplace/escrow/bank_account.html'

    def get(self, request):
        bank_account = WorkerBankAccount.objects.filter(
            worker=self.worker_profile,
        ).first()

        return render(request, self.template_name, {
            'bank_account': bank_account,
            'unread_count': _unread_notification_count(request.user),
        })

    def post(self, request):
        account_name   = request.POST.get('account_name', '').strip()
        account_number = request.POST.get('account_number', '').strip()
        bank_code      = request.POST.get('bank_code', '').strip()
        bank_name      = request.POST.get('bank_name', '').strip()

        # Validation
        errors = []
        if not account_name:
            errors.append('Account name is required.')
        if not account_number or len(account_number) != 10 or not account_number.isdigit():
            errors.append('Enter a valid 10-digit account number.')
        if not bank_code:
            errors.append('Bank code is required.')
        if not bank_name:
            errors.append('Bank name is required.')

        if errors:
            if _is_ajax(request):
                return JsonResponse({'errors': errors}, status=400)
            for e in errors:
                messages.error(request, e)
            return redirect('marketplace:bank_account')

        bank_account, created = WorkerBankAccount.objects.update_or_create(
            worker=self.worker_profile,
            defaults={
                'account_name': account_name,
                'account_number': account_number,
                'bank_code': bank_code,
                'bank_name': bank_name,
                'paystack_recipient_code': '',  # Reset — will be re-created
                'is_verified': False,
            },
        )

        # Trigger async recipient creation
        from jobs.tasks import create_transfer_recipient_task
        create_transfer_recipient_task.delay(str(bank_account.pk))

        if _is_ajax(request):
            return JsonResponse({
                'id': str(bank_account.pk),
                'account_name': bank_account.account_name,
                'bank_name': bank_account.bank_name,
                'created': created,
            })

        action = 'added' if created else 'updated'
        messages.success(request, f'Bank account {action} successfully. Verification in progress.')
        return redirect('marketplace:bank_account')


# ──────────────────────────────────────────────────────────────────────────────
#  DISPUTE ADMIN RESOLVE
# ──────────────────────────────────────────────────────────────────────────────

class DisputeAdminResolveView(LoginRequiredMixin, View):
    """
    POST /escrow/disputes/<pk>/resolve/
    Staff-only view to resolve a dispute.
    """

    def post(self, request, pk):
        if not request.user.is_staff:
            raise Http404

        dispute = get_object_or_404(
            Dispute.objects.select_related(
                'milestone__contract',
            ),
            pk=pk,
        )

        resolution = request.POST.get('resolution', '').strip()
        resolution_note = request.POST.get('resolution_note', '').strip()

        valid_resolutions = [
            Dispute.Resolution.RELEASED_TO_WORKER,
            Dispute.Resolution.REFUNDED_TO_EMPLOYER,
        ]
        if resolution not in valid_resolutions:
            if _is_ajax(request):
                return JsonResponse({'error': 'Invalid resolution.'}, status=400)
            messages.error(request, 'Invalid resolution choice.')
            return redirect('admin:jobs_dispute_change', dispute.pk)

        success = resolve_dispute(
            dispute_id=str(dispute.pk),
            resolution=resolution,
            resolved_by_user_id=request.user.pk,
            resolution_note=resolution_note,
        )

        if success:
            if _is_ajax(request):
                return JsonResponse({'status': 'resolved', 'resolution': resolution})
            messages.success(request, f'Dispute resolved: {resolution}.')
        else:
            if _is_ajax(request):
                return JsonResponse({'error': 'Failed to resolve dispute.'}, status=500)
            messages.error(request, 'Failed to resolve dispute. Check logs for details.')

        return redirect('admin:jobs_dispute_change', dispute.pk)
