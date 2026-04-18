"""
marketplace/views.py
====================
All views for the TradeLink NG marketplace app.

URL namespace: 'marketplace'

View map
────────
Public
  TradeCategoryListView        GET  /trades/
  TradeCategoryDetailView      GET  /trades/<slug>/
  JobListView                  GET  /jobs/
  JobDetailView                GET  /jobs/<pk>/
  WorkerProfilePublicView      GET  /workers/<pk>/
  EmployerProfilePublicView    GET  /employers/<pk>/

Worker (login required, must have WorkerProfile)
  WorkerDashboardView          GET  /dashboard/worker/
  WorkerProfileEditView        GET/POST /profile/worker/edit/
  PortfolioItemCreateView      GET/POST /profile/worker/portfolio/add/
  PortfolioItemDeleteView      POST /profile/worker/portfolio/<pk>/delete/
  JobApplyView                 GET/POST /jobs/<pk>/apply/
  WithdrawApplicationView      POST /applications/<pk>/withdraw/
  WorkerApplicationsView       GET  /applications/
  WorkerMatchesView            GET  /matches/           ← CLIP recommendations
  ToggleSaveJobView            POST /jobs/<pk>/save/    ← AJAX

Employer (login required, must have EmployerProfile)
  EmployerDashboardView        GET  /dashboard/employer/
  EmployerProfileEditView      GET/POST /profile/employer/edit/
  JobCreateView                GET/POST /jobs/post/
  JobUpdateView                GET/POST /jobs/<pk>/edit/
  JobDeleteView                POST /jobs/<pk>/delete/
  JobToggleStatusView          POST /jobs/<pk>/toggle-status/  ← AJAX
  JobApplicationsView          GET  /employer/jobs/<pk>/applications/
  UpdateApplicationStatusView  POST /employer/applications/<pk>/update/
  JobMatchesView               GET  /employer/jobs/<pk>/matches/ ← CLIP workers

Shared
  DashboardRedirectView        GET  /dashboard/
  NotificationListView         GET  /notifications/
  MarkNotificationReadView     POST /notifications/<pk>/read/   ← AJAX
  MarkAllNotificationsReadView POST /notifications/read-all/
  SubmitReviewView             GET/POST /reviews/submit/<pk>/   (job pk)
"""

import json
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import Q, Avg, Count
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import (
    ListView, DetailView, CreateView, UpdateView, DeleteView, TemplateView,
)

from .forms import (
    WorkerProfileForm,
    PortfolioItemForm,
    EmployerProfileForm,
    JobForm,
    JobApplicationForm,
    ReviewForm,
    JobFilterForm,
)
from .models import (
    TradeCategory,
    Skill,
    WorkerProfile,
    PortfolioItem,
    EmployerProfile,
    Job,
    CLIPMatch,
    JobApplication,
    SavedJob,
    Review,
    Notification,
)


# ──────────────────────────────────────────────────────────────────────────────
#  MIXINS
# ──────────────────────────────────────────────────────────────────────────────

class WorkerRequiredMixin(LoginRequiredMixin):
    """
    Ensures the logged-in user has a WorkerProfile.
    Redirects to the profile creation page if not.
    """

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not hasattr(request.user, 'worker_profile'):
            messages.info(request, 'Please complete your worker profile first.')
            return redirect('marketplace:worker_profile_edit')
        return super().dispatch(request, *args, **kwargs)

    @property
    def worker_profile(self):
        return self.request.user.worker_profile


class EmployerRequiredMixin(LoginRequiredMixin):
    """
    Ensures the logged-in user has an EmployerProfile.
    Redirects to employer profile setup if not.
    """

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not hasattr(request.user, 'employer_profile'):
            messages.info(request, 'Please set up your employer profile first.')
            return redirect('marketplace:employer_profile_edit')
        return super().dispatch(request, *args, **kwargs)

    @property
    def employer_profile(self):
        return self.request.user.employer_profile


# ──────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def _unread_notification_count(user):
    """Returns the count of unread notifications for a user."""
    if user.is_authenticated:
        return Notification.objects.filter(user=user, is_read=False).count()
    return 0


def _get_worker_profile_or_none(user):
    return getattr(user, 'worker_profile', None)


def _get_employer_profile_or_none(user):
    return getattr(user, 'employer_profile', None)


# ──────────────────────────────────────────────────────────────────────────────
#  PUBLIC — TRADE CATEGORIES
# ──────────────────────────────────────────────────────────────────────────────

class TradeCategoryListView(ListView):
    """
    /trades/  — Browse all active trade disciplines.
    Shows the count of active workers and jobs per category.
    """
    model               = TradeCategory
    template_name       = 'marketplace/trades/list.html'
    context_object_name = 'categories'

    def get_queryset(self):
        return (
            TradeCategory.objects.filter(is_active=True)
            .annotate(
                worker_count=Count('workers', distinct=True),
                job_count=Count(
                    'jobs',
                    filter=Q(jobs__status=Job.Status.ACTIVE),
                    distinct=True,
                ),
            )
            .order_by('display_order', 'name')
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['unread_count'] = _unread_notification_count(self.request.user)
        return ctx


class TradeCategoryDetailView(DetailView):
    """
    /trades/<slug>/  — All active jobs in a specific trade.
    """
    model               = TradeCategory
    template_name       = 'marketplace/trades/detail.html'
    context_object_name = 'category'
    slug_field          = 'slug'
    slug_url_kwarg      = 'slug'

    def get_queryset(self):
        return TradeCategory.objects.filter(is_active=True)

    def get_context_data(self, **kwargs):
        ctx  = super().get_context_data(**kwargs)
        jobs = (
            Job.objects.filter(
                trade_category=self.object,
                status=Job.Status.ACTIVE,
            )
            .select_related('employer')
            .order_by('-created')
        )
        paginator = Paginator(jobs, 12)
        ctx['jobs']          = paginator.get_page(self.request.GET.get('page'))
        ctx['workers']       = (
            WorkerProfile.objects.filter(
                trade_category=self.object,
                availability=WorkerProfile.Availability.AVAILABLE,
            )
            .select_related('user')
            .order_by('-is_featured', '-profile_completion')[:6]
        )
        ctx['unread_count']  = _unread_notification_count(self.request.user)
        return ctx


# ──────────────────────────────────────────────────────────────────────────────
#  PUBLIC — JOBS
# ──────────────────────────────────────────────────────────────────────────────

class JobListView(View):
    """
    /jobs/  — Browse and filter all active job listings.
    Supports keyword search, trade category, state, job type, pay type, remote.
    """
    template_name = 'marketplace/jobs/list.html'
    per_page      = 15

    def get(self, request):
        form = JobFilterForm(request.GET or None)
        qs   = (
            Job.objects.filter(status=Job.Status.ACTIVE)
            .select_related('employer', 'trade_category')
            .prefetch_related('required_skills')
            .order_by('-created')
        )

        if form.is_valid():
            q         = form.cleaned_data.get('q')
            trade     = form.cleaned_data.get('trade')
            state     = form.cleaned_data.get('state')
            job_type  = form.cleaned_data.get('job_type')
            pay_type  = form.cleaned_data.get('pay_type')
            is_remote = form.cleaned_data.get('is_remote')

            if q:
                qs = qs.filter(
                    Q(title__icontains=q) |
                    Q(description__icontains=q) |
                    Q(trade_category__name__icontains=q)
                )
            if trade:
                qs = qs.filter(trade_category=trade)
            if state:
                qs = qs.filter(state=state)
            if job_type:
                qs = qs.filter(job_type=job_type)
            if pay_type:
                qs = qs.filter(pay_type=pay_type)
            if is_remote:
                qs = qs.filter(is_remote=True)

        paginator = Paginator(qs, self.per_page)
        jobs      = paginator.get_page(request.GET.get('page'))

        # Saved job IDs for the current worker (to highlight saved buttons)
        saved_ids = set()
        worker    = _get_worker_profile_or_none(request.user)
        if worker:
            saved_ids = set(
                SavedJob.objects.filter(worker=worker)
                .values_list('job_id', flat=True)
            )

        return render(request, self.template_name, {
            'form':        form,
            'jobs':        jobs,
            'saved_ids':   saved_ids,
            'categories':  TradeCategory.objects.filter(is_active=True),
            'total_count': qs.count(),
            'unread_count': _unread_notification_count(request.user),
        })


class JobDetailView(View):
    """
    /jobs/<pk>/  — Full job details, application form, and similar jobs.
    Increments the view counter on each unique visit (session-gated).
    """
    template_name = 'marketplace/jobs/detail.html'

    def get(self, request, pk):
        job = get_object_or_404(
            Job.objects.select_related('employer', 'trade_category')
            .prefetch_related('required_skills', 'reviews'),
            pk=pk,
            status=Job.Status.ACTIVE,
        )

        # Increment view counter once per session
        session_key = f'viewed_job_{pk}'
        if not request.session.get(session_key):
            Job.objects.filter(pk=pk).update(views_count=job.views_count + 1)
            request.session[session_key] = True

        # Worker-specific context
        worker         = _get_worker_profile_or_none(request.user)
        has_applied    = False
        is_saved       = False
        application    = None
        clip_score     = None

        if worker:
            application = JobApplication.objects.filter(
                job=job, worker=worker
            ).first()
            has_applied = application is not None
            is_saved    = SavedJob.objects.filter(job=job, worker=worker).exists()
            match       = CLIPMatch.objects.filter(job=job, worker=worker).first()
            clip_score  = round(match.score * 100) if match else None

        # Similar jobs (same trade category, excluding this one)
        similar_jobs = (
            Job.objects.filter(
                trade_category=job.trade_category,
                status=Job.Status.ACTIVE,
            )
            .exclude(pk=pk)
            .select_related('employer')
            .order_by('-created')[:4]
        )

        return render(request, self.template_name, {
            'job':          job,
            'form':         JobApplicationForm(),
            'has_applied':  has_applied,
            'application':  application,
            'is_saved':     is_saved,
            'clip_score':   clip_score,
            'similar_jobs': similar_jobs,
            'unread_count': _unread_notification_count(request.user),
        })


# ──────────────────────────────────────────────────────────────────────────────
#  PUBLIC — WORKER & EMPLOYER PROFILE PAGES
# ──────────────────────────────────────────────────────────────────────────────

class WorkerProfilePublicView(View):
    """
    /workers/<pk>/  — Public-facing worker profile page.
    Shows skills, portfolio, reviews, and average rating.
    """
    template_name = 'marketplace/workers/profile.html'

    def get(self, request, pk):
        worker = get_object_or_404(
            WorkerProfile.objects.select_related('user', 'trade_category')
            .prefetch_related('skills', 'portfolio'),
            pk=pk,
        )
        reviews = (
            Review.objects.filter(
                reviewee=worker.user,
                review_type=Review.ReviewType.EMPLOYER_TO_WORKER,
                is_visible=True,
            )
            .select_related('reviewer')
            .order_by('-created_at')
        )
        avg_rating = reviews.aggregate(avg=Avg('rating'))['avg']

        return render(request, self.template_name, {
            'worker':       worker,
            'reviews':      reviews,
            'avg_rating':   round(avg_rating, 1) if avg_rating else None,
            'unread_count': _unread_notification_count(request.user),
        })


class EmployerProfilePublicView(View):
    """
    /employers/<pk>/  — Public-facing employer profile page.
    Shows company info, active jobs, and worker reviews.
    """
    template_name = 'marketplace/employers/profile.html'

    def get(self, request, pk):
        employer = get_object_or_404(
            EmployerProfile.objects.select_related('user'),
            pk=pk,
        )
        active_jobs = (
            Job.objects.filter(employer=employer, status=Job.Status.ACTIVE)
            .order_by('-created')
        )
        reviews = (
            Review.objects.filter(
                reviewee=employer.user,
                review_type=Review.ReviewType.WORKER_TO_EMPLOYER,
                is_visible=True,
            )
            .select_related('reviewer')
            .order_by('-created_at')
        )
        avg_rating = reviews.aggregate(avg=Avg('rating'))['avg']

        return render(request, self.template_name, {
            'employer':     employer,
            'active_jobs':  active_jobs,
            'reviews':      reviews,
            'avg_rating':   round(avg_rating, 1) if avg_rating else None,
            'unread_count': _unread_notification_count(request.user),
        })


# ──────────────────────────────────────────────────────────────────────────────
#  DASHBOARD — REDIRECT
# ──────────────────────────────────────────────────────────────────────────────

class DashboardRedirectView(LoginRequiredMixin, View):
    """
    /dashboard/  — Sends users to the correct dashboard based on their profile.
    Workers → /dashboard/worker/
    Employers → /dashboard/employer/
    New users → profile setup
    """

    def get(self, request):
        if hasattr(request.user, 'worker_profile'):
            return redirect('marketplace:worker_dashboard')
        if hasattr(request.user, 'employer_profile'):
            return redirect('marketplace:employer_dashboard')
        # Brand-new user — let them set up a profile
        return render(request, 'marketplace/dashboard/choose_role.html', {
            'unread_count': 0,
        })


# ──────────────────────────────────────────────────────────────────────────────
#  WORKER — DASHBOARD
# ──────────────────────────────────────────────────────────────────────────────

class WorkerDashboardView(WorkerRequiredMixin, View):
    """
    /dashboard/worker/
    Surfaces: top CLIP matches, recent applications, profile completion tips.
    """
    template_name = 'marketplace/dashboard/worker.html'

    def get(self, request):
        worker = self.worker_profile

        # Top 5 CLIP-recommended jobs
        top_matches = (
            CLIPMatch.objects.filter(
                worker=worker,
                job__status=Job.Status.ACTIVE,
                is_applied=False,
            )
            .select_related('job__employer', 'job__trade_category')
            .order_by('-score')[:5]
        )

        # Recent applications
        recent_apps = (
            JobApplication.objects.filter(worker=worker)
            .select_related('job__employer')
            .order_by('-applied_at')[:5]
        )

        # Saved jobs
        saved_jobs = (
            SavedJob.objects.filter(worker=worker)
            .select_related('job__employer', 'job__trade_category')
            .order_by('-saved_at')[:4]
        )

        return render(request, self.template_name, {
            'worker':       worker,
            'top_matches':  top_matches,
            'recent_apps':  recent_apps,
            'saved_jobs':   saved_jobs,
            'unread_count': _unread_notification_count(request.user),
        })


# ──────────────────────────────────────────────────────────────────────────────
#  WORKER — PROFILE EDIT
# ──────────────────────────────────────────────────────────────────────────────

class WorkerProfileEditView(LoginRequiredMixin, View):
    """
    /profile/worker/edit/
    Creates WorkerProfile if it doesn't exist yet (first-time setup).
    Triggers CLIP embedding recomputation via signal/task on save.
    """
    template_name = 'marketplace/workers/edit.html'

    def _get_or_create_profile(self, user):
        profile, _ = WorkerProfile.objects.get_or_create(user=user)
        return profile

    def get(self, request):
        profile = self._get_or_create_profile(request.user)
        form    = WorkerProfileForm(instance=profile)
        return render(request, self.template_name, {
            'form':         form,
            'profile':      profile,
            'unread_count': _unread_notification_count(request.user),
        })

    def post(self, request):
        profile = self._get_or_create_profile(request.user)
        form    = WorkerProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, 'Your profile has been updated.')
            return redirect('marketplace:worker_dashboard')
        return render(request, self.template_name, {
            'form':         form,
            'profile':      profile,
            'unread_count': _unread_notification_count(request.user),
        })


# ──────────────────────────────────────────────────────────────────────────────
#  WORKER — PORTFOLIO
# ──────────────────────────────────────────────────────────────────────────────

class PortfolioItemCreateView(WorkerRequiredMixin, View):
    """
    /profile/worker/portfolio/add/
    Adds a portfolio item. The image is later encoded by a background CLIP task.
    """
    template_name = 'marketplace/workers/portfolio_add.html'

    def get(self, request):
        return render(request, self.template_name, {
            'form':         PortfolioItemForm(),
            'unread_count': _unread_notification_count(request.user),
        })

    def post(self, request):
        form = PortfolioItemForm(request.POST, request.FILES)
        if form.is_valid():
            item = form.save(commit=False)
            item.worker = self.worker_profile
            item.save()
            messages.success(request, 'Portfolio item added.')
            return redirect('marketplace:worker_profile_edit')
        return render(request, self.template_name, {
            'form':         form,
            'unread_count': _unread_notification_count(request.user),
        })


class PortfolioItemDeleteView(WorkerRequiredMixin, View):
    """
    POST /profile/worker/portfolio/<pk>/delete/
    Deletes a portfolio item that belongs to the current worker.
    """

    def post(self, request, pk):
        item = get_object_or_404(PortfolioItem, pk=pk, worker=self.worker_profile)
        item.delete()
        messages.success(request, 'Portfolio item removed.')
        return redirect('marketplace:worker_profile_edit')


# ──────────────────────────────────────────────────────────────────────────────
#  WORKER — APPLY TO JOB
# ──────────────────────────────────────────────────────────────────────────────

class JobApplyView(WorkerRequiredMixin, View):
    """
    /jobs/<pk>/apply/
    Submits a job application. Captures the current CLIP score as a snapshot.
    """
    template_name = 'marketplace/jobs/apply.html'

    def _get_job(self, pk):
        return get_object_or_404(Job, pk=pk, status=Job.Status.ACTIVE)

    def get(self, request, pk):
        job = self._get_job(pk)
        # Redirect if already applied
        if JobApplication.objects.filter(job=job, worker=self.worker_profile).exists():
            messages.info(request, 'You have already applied for this job.')
            return redirect('marketplace:job_detail', pk=pk)

        return render(request, self.template_name, {
            'job':          job,
            'form':         JobApplicationForm(),
            'unread_count': _unread_notification_count(request.user),
        })

    def post(self, request, pk):
        job  = self._get_job(pk)
        worker = self.worker_profile
        form = JobApplicationForm(request.POST)

        if form.is_valid():
            # Snapshot the current CLIP score
            match = CLIPMatch.objects.filter(job=job, worker=worker).first()
            clip_score = match.score if match else None

            try:
                application = form.save(commit=False)
                application.job             = job
                application.worker          = worker
                application.clip_match_score = clip_score
                application.save()

                # Mark the CLIPMatch row as applied
                if match:
                    CLIPMatch.objects.filter(pk=match.pk).update(is_applied=True)

                # Increment cached counter on the job
                Job.objects.filter(pk=job.pk).update(
                    applications_count=job.applications_count + 1
                )

                # Notify the employer
                Notification.objects.create(
                    user=job.employer.user,
                    notif_type=Notification.NotifType.NEW_APPLICATION,
                    title=f'New application for "{job.title}"',
                    body=(
                        f'{worker.user.get_full_name() or worker.user.username} '
                        f'applied for your job.'
                    ),
                    data={'job_id': str(job.pk), 'application_id': str(application.pk)},
                )

                messages.success(request, 'Your application has been submitted!')
                return redirect('marketplace:worker_applications')

            except IntegrityError:
                messages.error(request, 'You have already applied for this job.')
                return redirect('marketplace:job_detail', pk=pk)

        return render(request, self.template_name, {
            'job':          job,
            'form':         form,
            'unread_count': _unread_notification_count(request.user),
        })


# ──────────────────────────────────────────────────────────────────────────────
#  WORKER — MY APPLICATIONS
# ──────────────────────────────────────────────────────────────────────────────

class WorkerApplicationsView(WorkerRequiredMixin, View):
    """
    /applications/
    Shows all job applications made by the current worker, grouped by status.
    """
    template_name = 'marketplace/workers/applications.html'

    def get(self, request):
        applications = (
            JobApplication.objects.filter(worker=self.worker_profile)
            .select_related('job__employer', 'job__trade_category')
            .order_by('-applied_at')
        )
        return render(request, self.template_name, {
            'applications': applications,
            'unread_count': _unread_notification_count(request.user),
        })


class WithdrawApplicationView(WorkerRequiredMixin, View):
    """
    POST /applications/<pk>/withdraw/
    Allows a worker to withdraw a pending application.
    """

    def post(self, request, pk):
        application = get_object_or_404(
            JobApplication,
            pk=pk,
            worker=self.worker_profile,
            status=JobApplication.Status.PENDING,
        )
        application.status = JobApplication.Status.WITHDRAWN
        application.save(update_fields=['status', 'updated_at'])

        # Un-mark the CLIPMatch so the job reappears in recommendations
        CLIPMatch.objects.filter(
            worker=self.worker_profile, job=application.job
        ).update(is_applied=False)

        messages.success(request, 'Application withdrawn.')
        return redirect('marketplace:worker_applications')


# ──────────────────────────────────────────────────────────────────────────────
#  WORKER — CLIP MATCHES  (personalised job recommendations)
# ──────────────────────────────────────────────────────────────────────────────

class WorkerMatchesView(WorkerRequiredMixin, View):
    """
    /matches/
    Shows CLIP-recommended jobs for the worker, ordered by similarity score.
    Only jobs with score >= 0.50 and status=active are shown.
    """
    template_name = 'marketplace/workers/matches.html'
    per_page      = 12

    def get(self, request):
        worker = self.worker_profile
        matches = (
            CLIPMatch.objects.filter(
                worker=worker,
                job__status=Job.Status.ACTIVE,
                score__gte=0.50,
                is_applied=False,
            )
            .select_related('job__employer', 'job__trade_category')
            .order_by('-score')
        )
        paginator = Paginator(matches, self.per_page)

        return render(request, self.template_name, {
            'matches':       paginator.get_page(request.GET.get('page')),
            'worker':        worker,
            'clip_ready': worker.text_embedding is not None,
            'unread_count':  _unread_notification_count(request.user),
        })


# ──────────────────────────────────────────────────────────────────────────────
#  WORKER — SAVE / UNSAVE JOB  (AJAX toggle)
# ──────────────────────────────────────────────────────────────────────────────

class ToggleSaveJobView(WorkerRequiredMixin, View):
    """
    POST /jobs/<pk>/save/
    Saves or un-saves a job for the current worker.
    Returns JSON: {"saved": true/false}
    """

    def post(self, request, pk):
        job    = get_object_or_404(Job, pk=pk)
        worker = self.worker_profile
        saved, created = SavedJob.objects.get_or_create(job=job, worker=worker)
        if not created:
            saved.delete()
            return JsonResponse({'saved': False})
        return JsonResponse({'saved': True})


# ──────────────────────────────────────────────────────────────────────────────
#  EMPLOYER — DASHBOARD
# ──────────────────────────────────────────────────────────────────────────────

class EmployerDashboardView(EmployerRequiredMixin, View):
    """
    /dashboard/employer/
    Surfaces: active jobs, recent applications, pending actions.
    """
    template_name = 'marketplace/dashboard/employer.html'

    def get(self, request):
        employer = self.employer_profile
        jobs     = (
            Job.objects.filter(employer=employer)
            .annotate(app_count=Count('applications'))
            .order_by('-created')
        )
        recent_apps = (
            JobApplication.objects.filter(
                job__employer=employer,
                status=JobApplication.Status.PENDING,
            )
            .select_related('worker__user', 'job')
            .order_by('-applied_at')[:8]
        )

        return render(request, self.template_name, {
            'employer':     employer,
            'jobs':         jobs,
            'recent_apps':  recent_apps,
            'unread_count': _unread_notification_count(request.user),
        })


# ──────────────────────────────────────────────────────────────────────────────
#  EMPLOYER — PROFILE EDIT
# ──────────────────────────────────────────────────────────────────────────────

class EmployerProfileEditView(LoginRequiredMixin, View):
    """
    /profile/employer/edit/
    Creates EmployerProfile if it doesn't exist yet.
    """
    template_name = 'marketplace/employers/edit.html'

    def _get_or_create_profile(self, user):
        profile, _ = EmployerProfile.objects.get_or_create(user=user)
        return profile

    def get(self, request):
        profile = self._get_or_create_profile(request.user)
        form    = EmployerProfileForm(instance=profile)
        return render(request, self.template_name, {
            'form':         form,
            'profile':      profile,
            'unread_count': _unread_notification_count(request.user),
        })

    def post(self, request):
        profile = self._get_or_create_profile(request.user)
        form    = EmployerProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, 'Company profile updated.')
            return redirect('marketplace:employer_dashboard')
        return render(request, self.template_name, {
            'form':         form,
            'profile':      profile,
            'unread_count': _unread_notification_count(request.user),
        })


# ──────────────────────────────────────────────────────────────────────────────
#  EMPLOYER — POST A JOB
# ──────────────────────────────────────────────────────────────────────────────

class JobCreateView(EmployerRequiredMixin, View):
    """
    /jobs/post/
    Employer posts a new job. Saved as Draft; employer manually activates it,
    OR it can be set directly to Active in the form.
    A post_save signal (in signals.py) will trigger CLIP embedding computation.
    """
    template_name = 'marketplace/jobs/create.html'

    def get(self, request):
        return render(request, self.template_name, {
            'form':         JobForm(),
            'unread_count': _unread_notification_count(request.user),
        })

    def post(self, request):
        form = JobForm(request.POST)
        if form.is_valid():
            job          = form.save(commit=False)
            job.employer = self.employer_profile
            job.status   = Job.Status.ACTIVE  # publish immediately
            job.save()
            form.save_m2m()  # save required_skills M2M
            messages.success(
                request,
                f'Job "{job.title}" has been posted and is now live.'
            )
            return redirect('marketplace:job_detail', pk=job.pk)
        return render(request, self.template_name, {
            'form':         form,
            'unread_count': _unread_notification_count(request.user),
        })


class JobUpdateView(EmployerRequiredMixin, View):
    """
    /jobs/<pk>/edit/
    Employer edits one of their own jobs.
    """
    template_name = 'marketplace/jobs/edit.html'

    def _get_job(self, pk):
        return get_object_or_404(Job, pk=pk, employer=self.employer_profile)

    def get(self, request, pk):
        job  = self._get_job(pk)
        form = JobForm(instance=job)
        return render(request, self.template_name, {
            'form':         form,
            'job':          job,
            'unread_count': _unread_notification_count(request.user),
        })

    def post(self, request, pk):
        job  = self._get_job(pk)
        form = JobForm(request.POST, instance=job)
        if form.is_valid():
            form.save()
            messages.success(request, 'Job listing updated.')
            return redirect('marketplace:job_detail', pk=job.pk)
        return render(request, self.template_name, {
            'form':         form,
            'job':          job,
            'unread_count': _unread_notification_count(request.user),
        })


class JobDeleteView(EmployerRequiredMixin, View):
    """
    POST /jobs/<pk>/delete/
    Employer deletes one of their own jobs.
    """

    def post(self, request, pk):
        job = get_object_or_404(Job, pk=pk, employer=self.employer_profile)
        title = job.title
        job.delete()
        messages.success(request, f'Job "{title}" has been deleted.')
        return redirect('marketplace:employer_dashboard')


class JobToggleStatusView(EmployerRequiredMixin, View):
    """
    POST /jobs/<pk>/toggle-status/   (AJAX)
    Toggles a job between Active and Paused.
    Returns JSON: {"status": "active"/"paused"}
    """

    def post(self, request, pk):
        job = get_object_or_404(Job, pk=pk, employer=self.employer_profile)
        if job.status == Job.Status.ACTIVE:
            job.status = Job.Status.PAUSED
        elif job.status == Job.Status.PAUSED:
            job.status = Job.Status.ACTIVE
        else:
            return JsonResponse({'error': 'Cannot toggle this job status.'}, status=400)
        job.save(update_fields=['status', 'updated'])
        return JsonResponse({'status': job.status})


# ──────────────────────────────────────────────────────────────────────────────
#  EMPLOYER — VIEW APPLICATIONS FOR A JOB
# ──────────────────────────────────────────────────────────────────────────────

class JobApplicationsView(EmployerRequiredMixin, View):
    """
    /employer/jobs/<pk>/applications/
    Shows all applications for one of the employer's jobs.
    Applications are sorted by CLIP match score (best first).
    """
    template_name = 'marketplace/employers/applications.html'

    def get(self, request, pk):
        job = get_object_or_404(Job, pk=pk, employer=self.employer_profile)
        applications = (
            JobApplication.objects.filter(job=job)
            .select_related('worker__user', 'worker__trade_category')
            .prefetch_related('worker__skills')
            .order_by('-clip_match_score', '-applied_at')
        )
        return render(request, self.template_name, {
            'job':           job,
            'applications':  applications,
            'status_choices': JobApplication.Status.choices,
            'unread_count':  _unread_notification_count(request.user),
        })


class UpdateApplicationStatusView(EmployerRequiredMixin, View):
    """
    POST /employer/applications/<pk>/update/
    Employer shortlists, accepts, or rejects an application.
    Sends a notification to the worker on status change.
    """

    def post(self, request, pk):
        application = get_object_or_404(
            JobApplication.objects.select_related('job__employer', 'worker__user'),
            pk=pk,
            job__employer=self.employer_profile,
        )
        new_status = request.POST.get('status')
        valid_statuses = [
            JobApplication.Status.SHORTLISTED,
            JobApplication.Status.ACCEPTED,
            JobApplication.Status.REJECTED,
        ]
        if new_status not in valid_statuses:
            messages.error(request, 'Invalid status.')
            return redirect('marketplace:job_applications', pk=application.job.pk)

        old_status = application.status
        application.status      = new_status
        application.employer_note = request.POST.get('employer_note', '')
        application.save(update_fields=['status', 'employer_note', 'updated_at'])

        # Notify the worker
        status_label = dict(JobApplication.Status.choices).get(new_status, new_status)
        Notification.objects.create(
            user=application.worker.user,
            notif_type=Notification.NotifType.APPLICATION_UPDATE,
            title=f'Application update: {application.job.title}',
            body=f'Your application status is now: {status_label}.',
            data={
                'job_id':         str(application.job.pk),
                'application_id': str(application.pk),
                'new_status':     new_status,
            },
        )

        messages.success(
            request,
            f'Application status updated to {status_label}.'
        )
        return redirect('marketplace:job_applications', pk=application.job.pk)


# ──────────────────────────────────────────────────────────────────────────────
#  EMPLOYER — CLIP WORKER MATCHES FOR A JOB
# ──────────────────────────────────────────────────────────────────────────────

class JobMatchesView(EmployerRequiredMixin, View):
    """
    /employer/jobs/<pk>/matches/
    Shows the CLIP-recommended workers for a specific job, ordered by score.
    This is the employer's view of the AI recommendation engine.
    """
    template_name = 'marketplace/employers/matches.html'
    per_page      = 12

    def get(self, request, pk):
        job = get_object_or_404(Job, pk=pk, employer=self.employer_profile)
        matches = (
            CLIPMatch.objects.filter(job=job, score__gte=0.50)
            .select_related(
                'worker__user',
                'worker__trade_category',
            )
            .prefetch_related('worker__skills')
            .order_by('-score')
        )
        paginator = Paginator(matches, self.per_page)

        return render(request, self.template_name, {
            'job':         job,
            'matches':     paginator.get_page(request.GET.get('page')),
            'clip_ready': job.text_embedding is not None,
            'unread_count': _unread_notification_count(request.user),
        })


# ──────────────────────────────────────────────────────────────────────────────
#  REVIEWS
# ──────────────────────────────────────────────────────────────────────────────

class SubmitReviewView(LoginRequiredMixin, View):
    """
    /reviews/submit/<pk>/   (pk = Job pk)
    Allows either party to leave a review after a job is completed/accepted.
    The correct review_type is inferred from the current user's profile type.
    """
    template_name = 'marketplace/reviews/submit.html'

    def _resolve_context(self, request, job):
        """
        Returns (review_type, reviewee) based on who is submitting the review.
        Raises Http404 if the user is not a participant in this job.
        """
        user = request.user
        # Employer reviewing the worker
        employer = _get_employer_profile_or_none(user)
        if employer and job.employer == employer:
            accepted_apps = JobApplication.objects.filter(
                job=job, status=JobApplication.Status.ACCEPTED
            ).select_related('worker__user')
            return Review.ReviewType.EMPLOYER_TO_WORKER, accepted_apps

        # Worker reviewing the employer
        worker = _get_worker_profile_or_none(user)
        if worker:
            application = JobApplication.objects.filter(
                job=job, worker=worker,
                status=JobApplication.Status.ACCEPTED,
            ).first()
            if application:
                return Review.ReviewType.WORKER_TO_EMPLOYER, [job.employer]

        raise Http404('You are not a participant in this job.')

    def get(self, request, pk):
        job            = get_object_or_404(Job, pk=pk)
        review_type, targets = self._resolve_context(request, job)
        return render(request, self.template_name, {
            'job':          job,
            'form':         ReviewForm(),
            'review_type':  review_type,
            'targets':      targets,
            'unread_count': _unread_notification_count(request.user),
        })

    def post(self, request, pk):
        job            = get_object_or_404(Job, pk=pk)
        review_type, _ = self._resolve_context(request, job)
        form           = ReviewForm(request.POST)

        if form.is_valid():
            reviewee_id = request.POST.get('reviewee_id')
            from django.contrib.auth import get_user_model
            User = get_user_model()
            reviewee = get_object_or_404(User, pk=reviewee_id)

            Review.objects.update_or_create(
                job=job,
                reviewer=request.user,
                review_type=review_type,
                defaults={
                    'reviewee': reviewee,
                    'rating':   form.cleaned_data['rating'],
                    'comment':  form.cleaned_data['comment'],
                },
            )
            # Notify reviewee
            Notification.objects.create(
                user=reviewee,
                notif_type=Notification.NotifType.NEW_REVIEW,
                title='You received a new review',
                body=f'{request.user.username} left you a {form.cleaned_data["rating"]}★ review.',
                data={'job_id': str(job.pk)},
            )
            messages.success(request, 'Review submitted. Thank you!')
            return redirect('marketplace:job_detail', pk=job.pk)

        return render(request, self.template_name, {
            'job':          job,
            'form':         form,
            'unread_count': _unread_notification_count(request.user),
        })


# ──────────────────────────────────────────────────────────────────────────────
#  NOTIFICATIONS
# ──────────────────────────────────────────────────────────────────────────────

class NotificationListView(LoginRequiredMixin, View):
    """
    /notifications/
    Lists all notifications for the current user, most recent first.
    """
    template_name = 'marketplace/notifications/list.html'
    per_page      = 20

    def get(self, request):
        notifications = Notification.objects.filter(
            user=request.user
        ).order_by('-created_at')
        paginator = Paginator(notifications, self.per_page)

        # Mark all as read when the user opens the page
        Notification.objects.filter(
            user=request.user, is_read=False
        ).update(is_read=True)

        return render(request, self.template_name, {
            'notifications': paginator.get_page(request.GET.get('page')),
            'unread_count':  0,   # just marked them all read
        })


class MarkNotificationReadView(LoginRequiredMixin, View):
    """
    POST /notifications/<pk>/read/  (AJAX)
    Marks a single notification as read.
    Returns JSON: {"unread_count": N}
    """

    def post(self, request, pk):
        Notification.objects.filter(
            pk=pk, user=request.user
        ).update(is_read=True)
        unread = Notification.objects.filter(
            user=request.user, is_read=False
        ).count()
        return JsonResponse({'unread_count': unread})


class MarkAllNotificationsReadView(LoginRequiredMixin, View):
    """
    POST /notifications/read-all/
    Marks every notification for the current user as read.
    """

    def post(self, request):
        Notification.objects.filter(
            user=request.user, is_read=False
        ).update(is_read=True)
        messages.success(request, 'All notifications marked as read.')
        return redirect('marketplace:notifications')