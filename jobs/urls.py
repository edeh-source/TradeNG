"""
marketplace/urls.py
===================
URL configuration for the TradeLink NG marketplace app.

Include in your project's root urls.py with a namespace:

    from django.urls import path, include

    urlpatterns = [
        ...
        path('', include('marketplace.urls', namespace='marketplace')),
    ]

Then reference URLs as: {% url 'marketplace:job_list' %}
                         reverse('marketplace:job_detail', kwargs={'pk': job.pk})
"""

from django.urls import path

from .views import (
    # Public — Trades
    TradeCategoryListView,
    TradeCategoryDetailView,

    # Public — Jobs
    JobListView,
    JobDetailView,

    # Public — Profiles
    WorkerProfilePublicView,
    EmployerProfilePublicView,

    # Dashboard
    DashboardRedirectView,
    WorkerDashboardView,
    EmployerDashboardView,

    # Worker — Profile & Portfolio
    WorkerProfileEditView,
    PortfolioItemCreateView,
    PortfolioItemDeleteView,

    # Worker — Jobs & Applications
    JobApplyView,
    WorkerApplicationsView,
    WithdrawApplicationView,
    WorkerMatchesView,
    ToggleSaveJobView,

    # Employer — Profile
    EmployerProfileEditView,

    # Employer — Jobs
    JobCreateView,
    JobUpdateView,
    JobDeleteView,
    JobToggleStatusView,

    # Employer — Applications
    JobApplicationsView,
    UpdateApplicationStatusView,
    JobMatchesView,

    # Reviews
    SubmitReviewView,

    # Notifications
    NotificationListView,
    MarkNotificationReadView,
    MarkAllNotificationsReadView,
)

app_name = 'marketplace'

urlpatterns = [

    # ── Trade Categories ────────────────────────────────────────────────────
    path(
        'trades/',
        TradeCategoryListView.as_view(),
        name='trade_list',
    ),
    path(
        'trades/<slug:slug>/',
        TradeCategoryDetailView.as_view(),
        name='trade_detail',
    ),

    # ── Job Listings (public) ───────────────────────────────────────────────
    path(
        'jobs/',
        JobListView.as_view(),
        name='job_list',
    ),
    path(
        'jobs/<uuid:pk>/',
        JobDetailView.as_view(),
        name='job_detail',
    ),

    # ── Public Profiles ─────────────────────────────────────────────────────
    path(
        'workers/<uuid:pk>/',
        WorkerProfilePublicView.as_view(),
        name='worker_profile_public',
    ),
    path(
        'employers/<uuid:pk>/',
        EmployerProfilePublicView.as_view(),
        name='employer_profile_public',
    ),

    # ── Dashboard ───────────────────────────────────────────────────────────
    path(
        'dashboard/',
        DashboardRedirectView.as_view(),
        name='dashboard',
    ),
    path(
        'dashboard/worker/',
        WorkerDashboardView.as_view(),
        name='worker_dashboard',
    ),
    path(
        'dashboard/employer/',
        EmployerDashboardView.as_view(),
        name='employer_dashboard',
    ),

    # ── Worker — Profile & Portfolio ────────────────────────────────────────
    path(
        'profile/worker/edit/',
        WorkerProfileEditView.as_view(),
        name='worker_profile_edit',
    ),
    path(
        'profile/worker/portfolio/add/',
        PortfolioItemCreateView.as_view(),
        name='portfolio_add',
    ),
    path(
        'profile/worker/portfolio/<uuid:pk>/delete/',
        PortfolioItemDeleteView.as_view(),
        name='portfolio_delete',
    ),

    # ── Worker — Job Interactions ────────────────────────────────────────────
    path(
        'jobs/<uuid:pk>/apply/',
        JobApplyView.as_view(),
        name='job_apply',
    ),
    path(
        'jobs/<uuid:pk>/save/',
        ToggleSaveJobView.as_view(),
        name='job_save_toggle',
    ),
    path(
        'applications/',
        WorkerApplicationsView.as_view(),
        name='worker_applications',
    ),
    path(
        'applications/<uuid:pk>/withdraw/',
        WithdrawApplicationView.as_view(),
        name='application_withdraw',
    ),
    path(
        'matches/',
        WorkerMatchesView.as_view(),
        name='worker_matches',
    ),

    # ── Employer — Profile ───────────────────────────────────────────────────
    path(
        'profile/employer/edit/',
        EmployerProfileEditView.as_view(),
        name='employer_profile_edit',
    ),

    # ── Employer — Job Management ────────────────────────────────────────────
    path(
        'jobs/post/',
        JobCreateView.as_view(),
        name='job_create',
    ),
    path(
        'jobs/<uuid:pk>/edit/',
        JobUpdateView.as_view(),
        name='job_edit',
    ),
    path(
        'jobs/<uuid:pk>/delete/',
        JobDeleteView.as_view(),
        name='job_delete',
    ),
    path(
        'jobs/<uuid:pk>/toggle-status/',
        JobToggleStatusView.as_view(),
        name='job_toggle_status',
    ),

    # ── Employer — Applications ──────────────────────────────────────────────
    path(
        'employer/jobs/<uuid:pk>/applications/',
        JobApplicationsView.as_view(),
        name='job_applications',
    ),
    path(
        'employer/applications/<uuid:pk>/update/',
        UpdateApplicationStatusView.as_view(),
        name='application_update',
    ),
    path(
        'employer/jobs/<uuid:pk>/matches/',
        JobMatchesView.as_view(),
        name='job_matches',
    ),

    # ── Reviews ──────────────────────────────────────────────────────────────
    path(
        'reviews/submit/<uuid:pk>/',
        SubmitReviewView.as_view(),
        name='review_submit',
    ),

    # ── Notifications ────────────────────────────────────────────────────────
    path(
        'notifications/',
        NotificationListView.as_view(),
        name='notifications',
    ),
    path(
        'notifications/<uuid:pk>/read/',
        MarkNotificationReadView.as_view(),
        name='notification_read',
    ),
    path(
        'notifications/read-all/',
        MarkAllNotificationsReadView.as_view(),
        name='notifications_read_all',
    ),
]