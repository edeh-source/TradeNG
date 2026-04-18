"""
marketplace/models.py
=====================
TradeLink NG — Core marketplace models.

Django app name: marketplace

This app is the engine of TradeLink NG. It owns every domain object that is
NOT auth: trade categories, skills, worker profiles, employer profiles, job
listings, CLIP match scores, applications, reviews, bookmarks, and
notifications.

CLIP integration overview
─────────────────────────
CLIP (Contrastive Language–Image Pre-Training) lets us embed both *text* and
*images* into the same vector space, so we can compute similarity across
modalities.

  Worker side
  ──────────
  • WorkerProfile.clip_text_embedding  — encoded from: bio + trade + skills
  • PortfolioItem.clip_image_embedding  — encoded from: portfolio photo

  Employer / Job side
  ───────────────────
  • Job.clip_embedding                  — encoded from: title + description + required skills

  Matching
  ────────
  • CLIPMatch stores the cosine similarity score between every (worker, job)
    pair whose trade categories overlap.
  • A background Celery task re-computes CLIPMatch rows whenever:
      – A new job is posted or updated
      – A worker updates their profile or portfolio
  • The recommendation endpoint simply queries:
      CLIPMatch.objects.filter(job=job).order_by('-score')[:20]

Embedding storage
─────────────────
  Embeddings are stored as JSON arrays (list[float]).  CLIP ViT-B/32 produces
  512-dimensional vectors.  For ~50K workers × 10K active jobs this is large
  but manageable as a cache table backed by pgvector in production.
  
  Swap JSONField → pgvector's VectorField in production for fast ANN queries.
"""

import uuid
from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator


# ──────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────────────────────────────────────

NIGERIAN_STATES = [
    ('abia',          'Abia'),
    ('adamawa',       'Adamawa'),
    ('akwa_ibom',     'Akwa Ibom'),
    ('anambra',       'Anambra'),
    ('bauchi',        'Bauchi'),
    ('bayelsa',       'Bayelsa'),
    ('benue',         'Benue'),
    ('borno',         'Borno'),
    ('cross_river',   'Cross River'),
    ('delta',         'Delta'),
    ('ebonyi',        'Ebonyi'),
    ('edo',           'Edo'),
    ('ekiti',         'Ekiti'),
    ('enugu',         'Enugu'),
    ('fct',           'FCT — Abuja'),
    ('gombe',         'Gombe'),
    ('imo',           'Imo'),
    ('jigawa',        'Jigawa'),
    ('kaduna',        'Kaduna'),
    ('kano',          'Kano'),
    ('katsina',       'Katsina'),
    ('kebbi',         'Kebbi'),
    ('kogi',          'Kogi'),
    ('kwara',         'Kwara'),
    ('lagos',         'Lagos'),
    ('nasarawa',      'Nasarawa'),
    ('niger',         'Niger'),
    ('ogun',          'Ogun'),
    ('ondo',          'Ondo'),
    ('osun',          'Osun'),
    ('oyo',           'Oyo'),
    ('plateau',       'Plateau'),
    ('rivers',        'Rivers'),
    ('sokoto',        'Sokoto'),
    ('taraba',        'Taraba'),
    ('yobe',          'Yobe'),
    ('zamfara',       'Zamfara'),
]


# ──────────────────────────────────────────────────────────────────────────────
#  1.  TRADE CATEGORY
#      The top-level taxonomy: Electrician, Plumber, Carpenter, etc.
# ──────────────────────────────────────────────────────────────────────────────

class TradeCategory(models.Model):
    """
    A broad skilled trade discipline.

    Examples from the homepage:
        Electrician · Carpenter · Plumber · Solar Installer
        Hair Stylist · Painter & Decorator · Welder · Mason
    """

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name        = models.CharField(max_length=120, unique=True)
    slug        = models.SlugField(max_length=120, unique=True)

    # FontAwesome class shown on the homepage trade cards, e.g. "fas fa-bolt"
    icon_class  = models.CharField(max_length=80, blank=True)

    description = models.TextField(blank=True)

    # Short text fed to CLIP when we encode this category as context for jobs
    # that don't have a rich description.  e.g. "skilled electrician in Nigeria"
    clip_context_text = models.TextField(
        blank=True,
        help_text="Short natural-language description used to anchor CLIP embeddings "
                  "for this trade (e.g. 'expert electrician wiring installation Lagos').",
    )

    is_active    = models.BooleanField(default=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    created      = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = 'Trade Category'
        verbose_name_plural = 'Trade Categories'
        ordering            = ['display_order', 'name']

    def __str__(self):
        return self.name


# ──────────────────────────────────────────────────────────────────────────────
#  2.  SKILL
#      Granular skills within a trade category.
# ──────────────────────────────────────────────────────────────────────────────

class Skill(models.Model):
    """
    A specific skill or specialisation within a TradeCategory.

    Examples:
        Electrician → { Solar Panel Wiring, CCTV Installation, Generator Repair }
        Hair Stylist → { Hair Braiding, Locs, Perms, Colouring }
        Carpenter    → { Cabinet Making, Roof Framing, Furniture Fitting }
    """

    id       = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.ForeignKey(
        TradeCategory,
        on_delete=models.CASCADE,
        related_name='skills',
    )
    name     = models.CharField(max_length=120)
    slug     = models.SlugField(max_length=120)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ('category', 'slug')
        ordering        = ['category', 'name']

    def __str__(self):
        return f'{self.category.name} → {self.name}'


# ──────────────────────────────────────────────────────────────────────────────
#  3.  WORKER PROFILE
#      The rich profile a tradesperson builds to get matched to jobs.
# ──────────────────────────────────────────────────────────────────────────────

class WorkerProfile(models.Model):

    class Availability(models.TextChoices):
        AVAILABLE      = 'available',    'Available Now'
        AVAILABLE_SOON = 'soon',         'Available Soon'
        BUSY           = 'busy',         'Currently Busy'
        NOT_AVAILABLE  = 'unavailable',  'Not Available'

    class ExperienceLevel(models.TextChoices):
        ENTRY       = 'entry',       'Entry (0–2 years)'
        INTERMEDIATE = 'mid',        'Intermediate (3–5 years)'
        EXPERIENCED  = 'experienced','Experienced (6–10 years)'
        EXPERT       = 'expert',     'Expert (10+ years)'

    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # One-to-one link to the custom User model in the accounts app
    user             = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='worker_profile',
    )

    # ── Trade & Skills ──────────────────────────────────────────────────────
    trade_category   = models.ForeignKey(
        TradeCategory,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='workers',
    )
    skills           = models.ManyToManyField(
        Skill,
        through='WorkerSkill',
        related_name='workers',
        blank=True,
    )
    experience_level = models.CharField(
        max_length=20,
        choices=ExperienceLevel.choices,
        default=ExperienceLevel.ENTRY,
    )
    years_experience = models.PositiveSmallIntegerField(default=0)

    # ── Bio — the primary text fed into CLIP ────────────────────────────────
    # Workers should describe what they do, specialisations, work style, etc.
    # This text is the main input for the CLIP text encoder.
    bio              = models.TextField(
        blank=True,
        help_text="Natural-language description of the worker's skills and experience. "
                  "This is the primary text encoded by CLIP for job matching.",
    )

    # ── Location ────────────────────────────────────────────────────────────
    state            = models.CharField(max_length=40, choices=NIGERIAN_STATES, blank=True)
    lga              = models.CharField(
        max_length=120, blank=True,
        verbose_name='Local Government Area',
    )
    is_willing_to_relocate = models.BooleanField(default=False)

    # ── Rates ───────────────────────────────────────────────────────────────
    hourly_rate      = models.DecimalField(
        max_digits=10, decimal_places=2,
        null=True, blank=True,
        help_text='Hourly rate in NGN',
    )
    daily_rate       = models.DecimalField(
        max_digits=10, decimal_places=2,
        null=True, blank=True,
        help_text='Day rate in NGN',
    )

    # ── Status ──────────────────────────────────────────────────────────────
    availability     = models.CharField(
        max_length=20,
        choices=Availability.choices,
        default=Availability.AVAILABLE,
    )
    is_verified      = models.BooleanField(
        default=False,
        help_text='Set by admin after document/ID verification.',
    )
    is_featured      = models.BooleanField(
        default=False,
        help_text='Featured workers appear at the top of recommendation lists.',
    )
    profile_completion = models.PositiveSmallIntegerField(
        default=0,
        validators=[MaxValueValidator(100)],
        help_text='Percentage (0–100) calculated from filled profile fields.',
    )

    # ── Sentence-Transformer Embedding (primary text signal) ────────────────
    # 768-dim vector from all-mpnet-base-v2.  This is the main embedding used
    # for text similarity in the hybrid scoring engine.
    text_embedding = models.JSONField(
        null=True, blank=True,
        help_text="Sentence-transformer embedding of bio + trade + skills. "
                  "768-dim float list. Recomputed when profile text changes.",
    )
    text_embedding_updated = models.DateTimeField(null=True, blank=True)

    # ── CLIP Text Embedding (kept for backward compat) ───────────────────────
    clip_text_embedding = models.JSONField(
        null=True, blank=True,
        help_text="Legacy CLIP text embedding. Use text_embedding instead.",
    )
    clip_embedding_updated = models.DateTimeField(null=True, blank=True)

    # ── Metadata ────────────────────────────────────────────────────────────
    created          = models.DateTimeField(auto_now_add=True)
    updated          = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_featured', '-profile_completion', '-created']

    def __str__(self):
        return f'Worker: {self.user.username} ({self.trade_category})'

    # ── Helpers ─────────────────────────────────────────────────────────────

    def get_clip_input_text(self) -> str:
        """
        Build the text that is sent to the CLIP text encoder.

        Combines: trade category name + skill names + bio.
        Example: "Electrician. Solar Panel Wiring, CCTV Installation.
                  Experienced electrician in Lagos with 6 years..."
        """
        parts = []
        if self.trade_category:
            parts.append(self.trade_category.name)
        skill_names = list(
            self.skills.filter(is_active=True).values_list('name', flat=True)
        )
        if skill_names:
            parts.append(', '.join(skill_names))
        if self.bio:
            parts.append(self.bio)
        return '. '.join(parts)


# ──────────────────────────────────────────────────────────────────────────────
#  4.  WORKER SKILL  (through model)
# ──────────────────────────────────────────────────────────────────────────────

class WorkerSkill(models.Model):
    """
    Associates a WorkerProfile with a Skill, recording proficiency detail.
    The through model lets us store extra data on the M2M relationship.
    """

    class ProficiencyLevel(models.TextChoices):
        BEGINNER     = 'beginner',     'Beginner'
        COMPETENT    = 'competent',    'Competent'
        PROFICIENT   = 'proficient',   'Proficient'
        EXPERT       = 'expert',       'Expert'

    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    worker           = models.ForeignKey(WorkerProfile, on_delete=models.CASCADE)
    skill            = models.ForeignKey(Skill, on_delete=models.CASCADE)
    years_experience = models.PositiveSmallIntegerField(default=0)
    proficiency      = models.CharField(
        max_length=20,
        choices=ProficiencyLevel.choices,
        default=ProficiencyLevel.COMPETENT,
    )

    class Meta:
        unique_together = ('worker', 'skill')

    def __str__(self):
        return f'{self.worker.user.username} — {self.skill.name} ({self.proficiency})'


# ──────────────────────────────────────────────────────────────────────────────
#  5.  PORTFOLIO ITEM
#      Images of a worker's past work — the visual side of CLIP matching.
# ──────────────────────────────────────────────────────────────────────────────

class PortfolioItem(models.Model):
    """
    A single past-work photo with a descriptive caption.

    CLIP use: the *image* is encoded by CLIP's visual encoder and the resulting
    embedding is stored in clip_image_embedding.  This lets employers who
    describe a project in text ("install solar panels on a rooftop Lagos")
    find workers whose *portfolio photos* are visually similar to that project.
    """

    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    worker        = models.ForeignKey(
        WorkerProfile,
        on_delete=models.CASCADE,
        related_name='portfolio',
    )
    image         = models.ImageField(upload_to='portfolio/%Y/%m/')
    caption       = models.CharField(max_length=280, blank=True)
    trade_context = models.ForeignKey(
        TradeCategory, null=True, blank=True, on_delete=models.SET_NULL,
        help_text='Which trade this item demonstrates.',
    )

    # CLIP image embedding — 512-dim float list from CLIP ViT-B/32
    clip_image_embedding = models.JSONField(
        null=True, blank=True,
        help_text="CLIP image embedding of this portfolio photo.",
    )

    display_order = models.PositiveSmallIntegerField(default=0)
    created       = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['worker', 'display_order', '-created']

    def __str__(self):
        return f'{self.worker.user.username} portfolio — {self.caption[:40]}'


# ──────────────────────────────────────────────────────────────────────────────
#  6.  EMPLOYER PROFILE
# ──────────────────────────────────────────────────────────────────────────────

class EmployerProfile(models.Model):

    class CompanyType(models.TextChoices):
        INDIVIDUAL  = 'individual',  'Individual / Homeowner'
        SME         = 'sme',         'Small / Medium Business'
        CORPORATION = 'corporation', 'Large Corporation'
        NGO         = 'ngo',         'NGO / Non-Profit'
        GOVERNMENT  = 'government',  'Government Agency'

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user         = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='employer_profile',
    )
    company_name = models.CharField(max_length=256, blank=True)
    company_type = models.CharField(
        max_length=20,
        choices=CompanyType.choices,
        default=CompanyType.SME,
    )
    industry     = models.CharField(max_length=120, blank=True)
    about        = models.TextField(blank=True)
    logo         = models.ImageField(upload_to='employer_logos/', blank=True, null=True)
    website      = models.URLField(blank=True)

    state        = models.CharField(max_length=40, choices=NIGERIAN_STATES, blank=True)
    lga          = models.CharField(max_length=120, blank=True)

    is_verified  = models.BooleanField(
        default=False,
        help_text='Admin-confirmed verified employer. Shown with a badge.',
    )
    created      = models.DateTimeField(auto_now_add=True)
    updated      = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_verified', '-created']

    def __str__(self):
        return self.company_name or self.user.username


# ──────────────────────────────────────────────────────────────────────────────
#  7.  JOB LISTING
#      The central object employers post and workers are matched to.
# ──────────────────────────────────────────────────────────────────────────────

class Job(models.Model):

    class JobType(models.TextChoices):
        FULL_TIME  = 'full_time',  'Full-Time'
        PART_TIME  = 'part_time',  'Part-Time'
        CONTRACT   = 'contract',   'Contract'
        ONCE_OFF   = 'once_off',   'One-Off / Gig'
        INTERNSHIP = 'internship', 'Apprenticeship / Internship'

    class PayType(models.TextChoices):
        HOURLY      = 'hourly',     'Per Hour'
        DAILY       = 'daily',      'Per Day'
        WEEKLY      = 'weekly',     'Per Week'
        MONTHLY     = 'monthly',    'Per Month'
        FIXED       = 'fixed',      'Fixed Price'
        NEGOTIABLE  = 'negotiable', 'Negotiable'

    class Status(models.TextChoices):
        DRAFT   = 'draft',   'Draft'
        ACTIVE  = 'active',  'Active'
        PAUSED  = 'paused',  'Paused'
        FILLED  = 'filled',  'Filled'
        CLOSED  = 'closed',  'Closed'
        EXPIRED = 'expired', 'Expired'

    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    employer         = models.ForeignKey(
        EmployerProfile,
        on_delete=models.CASCADE,
        related_name='jobs',
    )
    trade_category   = models.ForeignKey(
        TradeCategory,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='jobs',
    )
    required_skills  = models.ManyToManyField(
        Skill,
        blank=True,
        related_name='jobs',
    )

    # ── Content ─────────────────────────────────────────────────────────────
    title            = models.CharField(max_length=256)
    description      = models.TextField(
        help_text="Full job description. This is the primary text encoded by CLIP "
                  "for matching workers to this job.",
    )

    # ── Type / Pay ──────────────────────────────────────────────────────────
    job_type         = models.CharField(
        max_length=20, choices=JobType.choices, default=JobType.ONCE_OFF,
    )
    pay_type         = models.CharField(
        max_length=20, choices=PayType.choices, default=PayType.NEGOTIABLE,
    )
    pay_min          = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text='Minimum pay in NGN',
    )
    pay_max          = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text='Maximum pay in NGN',
    )

    # ── Location ────────────────────────────────────────────────────────────
    state            = models.CharField(max_length=40, choices=NIGERIAN_STATES, blank=True)
    lga              = models.CharField(max_length=120, blank=True)
    is_remote        = models.BooleanField(default=False)

    # ── Status & Lifecycle ──────────────────────────────────────────────────
    status           = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT,
    )
    deadline         = models.DateField(null=True, blank=True)
    slots            = models.PositiveSmallIntegerField(
        default=1,
        help_text='How many workers are needed for this job.',
    )

    # ── Sentence-Transformer Embedding (primary text signal) ────────────────
    text_embedding   = models.JSONField(
        null=True, blank=True,
        help_text="Sentence-transformer embedding of title + trade + skills + description. "
                  "768-dim float list. Recomputed when job text changes.",
    )
    text_embedding_updated = models.DateTimeField(null=True, blank=True)

    # ── CLIP Embedding (kept for backward compat) ────────────────────────────
    clip_embedding   = models.JSONField(
        null=True, blank=True,
        help_text="Legacy CLIP text embedding. Use text_embedding instead.",
    )
    clip_embedding_updated = models.DateTimeField(null=True, blank=True)

    # ── Cached counters (updated by signals) ────────────────────────────────
    applications_count = models.PositiveIntegerField(default=0)
    views_count        = models.PositiveIntegerField(default=0)

    created          = models.DateTimeField(auto_now_add=True)
    updated          = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created']
        indexes  = [
            models.Index(fields=['status', 'trade_category']),
            models.Index(fields=['state', 'status']),
        ]

    def __str__(self):
        return f'{self.title} — {self.employer}'

    # ── Helper ──────────────────────────────────────────────────────────────

    def get_clip_input_text(self) -> str:
        """
        Build the text sent to the CLIP text encoder for this job.

        Combines: title + trade category + required skills + description.
        """
        parts = [self.title]
        if self.trade_category:
            parts.append(self.trade_category.name)
        skill_names = list(
            self.required_skills.filter(is_active=True).values_list('name', flat=True)
        )
        if skill_names:
            parts.append(', '.join(skill_names))
        if self.description:
            parts.append(self.description)
        return '. '.join(parts)


# ──────────────────────────────────────────────────────────────────────────────
#  8.  CLIP MATCH  ← the recommendation engine's core table
# ──────────────────────────────────────────────────────────────────────────────

class CLIPMatch(models.Model):
    """
    Precomputed hybrid match score between a WorkerProfile and a Job.

    The final `score` is a weighted combination of six signals:
        text_score       × 0.50  — sentence-transformer semantic similarity
        location_score   × 0.15  — same state / willing to relocate
        experience_score × 0.15  — experience level fits job type
        image_score      × 0.10  — CLIP portfolio image similarity
        rating_score     × 0.05  — normalised worker avg rating
        verification     × 0.05  — admin-verified worker bonus

    Population strategy
    ───────────────────
    A Celery background task runs:
      1. Job posted/activated  → compute scores for all workers in same trade.
      2. Worker profile saved  → recompute scores for all active jobs in their trade.

    Query pattern for "recommend workers for a job":
        CLIPMatch.objects.filter(job=job, score__gte=0.60)
                         .select_related('worker__user')
                         .order_by('-score')[:20]

    Query pattern for "recommend jobs for a worker":
        CLIPMatch.objects.filter(worker=profile, job__status='active')
                         .select_related('job__employer')
                         .order_by('-score')[:20]
    """

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    worker      = models.ForeignKey(
        WorkerProfile,
        on_delete=models.CASCADE,
        related_name='clip_matches',
    )
    job         = models.ForeignKey(
        Job,
        on_delete=models.CASCADE,
        related_name='clip_matches',
    )

    # ── Hybrid final score ∈ [0.0, 1.0] ────────────────────────────────────
    # Weighted combination of all signals below.  Values above 0.65 are strong.
    score       = models.FloatField(
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text='Weighted hybrid score: 50% text + 15% location + 15% experience '
                  '+ 10% image + 5% rating + 5% verification.',
    )

    # ── Component scores (stored for explainability & re-weighting) ──────────
    text_score          = models.FloatField(null=True, blank=True,
        help_text='Sentence-transformer cosine similarity (0–1).')
    image_score         = models.FloatField(null=True, blank=True,
        help_text='CLIP image↔text similarity avg across portfolio items (0–1).')
    location_score      = models.FloatField(null=True, blank=True,
        help_text='1.0=same state, 0.5=willing to relocate/remote, 0.0=different state.')
    experience_score    = models.FloatField(null=True, blank=True,
        help_text='How well the worker experience level fits the job type (0–1).')
    rating_score        = models.FloatField(null=True, blank=True,
        help_text='Normalised average worker rating: (avg-1)/4. Default 0.5 if no ratings.')
    verification_bonus  = models.FloatField(null=True, blank=True,
        help_text='1.0 if worker is admin-verified, 0.0 otherwise.')

    # Flag set if the worker has already applied, so we skip re-recommending
    is_applied  = models.BooleanField(default=False)

    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('worker', 'job')
        indexes         = [
            models.Index(fields=['job', '-score']),
            models.Index(fields=['worker', '-score']),
        ]
        ordering        = ['-score']

    def __str__(self):
        return (
            f'{self.worker.user.username} ↔ {self.job.title} '
            f'[score={self.score:.3f}]'
        )


# ──────────────────────────────────────────────────────────────────────────────
#  9.  JOB APPLICATION
# ──────────────────────────────────────────────────────────────────────────────

class JobApplication(models.Model):

    class Status(models.TextChoices):
        PENDING      = 'pending',     'Pending Review'
        SHORTLISTED  = 'shortlisted', 'Shortlisted'
        ACCEPTED     = 'accepted',    'Accepted'
        REJECTED     = 'rejected',    'Rejected'
        WITHDRAWN    = 'withdrawn',   'Withdrawn by Worker'

    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job              = models.ForeignKey(
        Job, on_delete=models.CASCADE, related_name='applications',
    )
    worker           = models.ForeignKey(
        WorkerProfile, on_delete=models.CASCADE, related_name='applications',
    )
    cover_note       = models.TextField(
        blank=True,
        help_text="Brief message from the worker to the employer.",
    )

    # The CLIP score at the time of application — preserved even if profiles change.
    clip_match_score = models.FloatField(
        null=True, blank=True,
        validators=[MinValueValidator(0.0), MaxValueValidator(1.0)],
        help_text='Snapshot of the CLIP similarity score when the application was made.',
    )

    status           = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING,
    )
    applied_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    # Optional employer note (only visible to employer)
    employer_note    = models.TextField(blank=True)

    class Meta:
        unique_together = ('job', 'worker')
        ordering        = ['-applied_at']

    def __str__(self):
        return f'{self.worker.user.username} → {self.job.title} [{self.status}]'


# ──────────────────────────────────────────────────────────────────────────────
#  10.  SAVED JOB  (bookmarks)
# ──────────────────────────────────────────────────────────────────────────────

class SavedJob(models.Model):
    """
    Worker bookmarks a job listing.
    Matches the heart/save button shown on the job cards in the homepage.
    """

    id       = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    worker   = models.ForeignKey(
        WorkerProfile, on_delete=models.CASCADE, related_name='saved_jobs',
    )
    job      = models.ForeignKey(
        Job, on_delete=models.CASCADE, related_name='saved_by',
    )
    saved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('worker', 'job')
        ordering        = ['-saved_at']

    def __str__(self):
        return f'{self.worker.user.username} saved "{self.job.title}"'


# ──────────────────────────────────────────────────────────────────────────────
#  11.  REVIEW
#       Bidirectional ratings after a job is completed.
# ──────────────────────────────────────────────────────────────────────────────

class Review(models.Model):

    class ReviewType(models.TextChoices):
        EMPLOYER_TO_WORKER = 'employer_to_worker', 'Employer → Worker'
        WORKER_TO_EMPLOYER = 'worker_to_employer', 'Worker → Employer'

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job         = models.ForeignKey(
        Job, on_delete=models.CASCADE, related_name='reviews',
    )
    reviewer    = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reviews_given',
    )
    reviewee    = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reviews_received',
    )
    review_type = models.CharField(max_length=25, choices=ReviewType.choices)

    # 1–5 star rating
    rating      = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
    )
    comment     = models.TextField(blank=True)
    is_visible  = models.BooleanField(
        default=True,
        help_text='Admin can hide inappropriate reviews.',
    )
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        # One review per reviewer per job per direction
        unique_together = ('job', 'reviewer', 'review_type')
        ordering        = ['-created_at']

    def __str__(self):
        return (
            f'{self.reviewer.username} → {self.reviewee.username} '
            f'({self.rating}★) on "{self.job.title}"'
        )


# ──────────────────────────────────────────────────────────────────────────────
#  12.  NOTIFICATION
# ──────────────────────────────────────────────────────────────────────────────

class Notification(models.Model):

    class NotifType(models.TextChoices):
        NEW_MATCH          = 'new_match',          'New Job Match'
        APPLICATION_UPDATE = 'application_update', 'Application Status Update'
        NEW_APPLICATION    = 'new_application',    'New Application Received'
        NEW_REVIEW         = 'new_review',          'New Review'
        JOB_EXPIRING       = 'job_expiring',        'Job Expiring Soon'
        PROFILE_TIP        = 'profile_tip',         'Profile Improvement Tip'
        SYSTEM             = 'system',              'System Message'

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user       = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notifications',
    )
    notif_type = models.CharField(max_length=30, choices=NotifType.choices)
    title      = models.CharField(max_length=180)
    body       = models.TextField(blank=True)

    # Arbitrary JSON payload, e.g. {"job_id": "...", "score": 0.91}
    data       = models.JSONField(default=dict, blank=True)

    is_read    = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes  = [models.Index(fields=['user', 'is_read', '-created_at'])]

    def __str__(self):
        return f'[{self.notif_type}] → {self.user.username}: {self.title}'