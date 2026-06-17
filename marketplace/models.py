"""
marketplace/models.py
======================
TradeLink NG — Tool & Equipment Marketplace models.

Only verified WorkerProfiles can list products.  This enforces the trust
angle: a plumber selling pipe fittings or a carpenter offloading a router
already knows the product intimately and their job reviews are on the same
account — bad product reviews hurt their worker reputation too.

Flow
────
  Seller lists product → Buyer browses / makes offer → Seller accepts offer
  → Buyer pays into Paystack escrow → Meetup / handover happens
  → Buyer confirms receipt → funds released to seller (minus 5% fee)
  → Both parties leave a review

Sentence-transformer integration
─────────────────────────────────
  Product.text_embedding is computed from:
      "{category} {condition}. {title}. {description}"
  This lets buyers search "used angle grinder Lagos" and find a listing
  titled "Bosch 9-inch grinder, barely used, Surulere" with zero keyword
  overlap — the same model already powering job search.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models

from jobs.models import WorkerProfile, TradeCategory, NIGERIAN_STATES


# ──────────────────────────────────────────────────────────────────────────────
#  1.  MARKETPLACE CATEGORY
# ──────────────────────────────────────────────────────────────────────────────

class MarketplaceCategory(models.Model):
    """
    Top-level category for marketplace listings.
    Separate from TradeCategory — these describe product types, not trades.

    Examples:
        Power Tools · Hand Tools · Electrical Equipment · Plumbing Supplies
        Safety Gear · Woodworking Tools · Measuring Instruments · Materials
    """
    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name          = models.CharField(max_length=120, unique=True)
    slug          = models.SlugField(max_length=120, unique=True)
    icon_class    = models.CharField(max_length=80, blank=True,
                                     help_text='FontAwesome class e.g. fas fa-tools')
    description   = models.TextField(blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    is_active     = models.BooleanField(default=True)
    created       = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = 'Marketplace Category'
        verbose_name_plural = 'Marketplace Categories'
        ordering            = ['display_order', 'name']

    def __str__(self):
        return self.name


# ──────────────────────────────────────────────────────────────────────────────
#  2.  PRODUCT
# ──────────────────────────────────────────────────────────────────────────────

class Product(models.Model):
    """
    A tool, piece of equipment, or material listed for sale by a worker.

    Only WorkerProfile users can list products.  Condition is mandatory so
    buyers know exactly what they're getting.  Price is the listed price;
    buyers can make an offer lower than this.

    Sentence-transformer embedding is stored in text_embedding and recomputed
    via Celery whenever title, description, or category changes.
    """

    class Condition(models.TextChoices):
        NEW        = 'new',        'Brand New'
        USED_GOOD  = 'used_good',  'Used — Good Condition'
        USED_FAIR  = 'used_fair',  'Used — Fair Condition'
        FOR_PARTS  = 'for_parts',  'For Parts / Not Working'

    class Status(models.TextChoices):
        DRAFT     = 'draft',     'Draft'
        ACTIVE    = 'active',    'Active'
        SOLD      = 'sold',      'Sold'
        RESERVED  = 'reserved',  'Reserved'
        INACTIVE  = 'inactive',  'Inactive'

    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # ── Seller ──────────────────────────────────────────────────────────────
    seller       = models.ForeignKey(
        WorkerProfile,
        on_delete=models.CASCADE,
        related_name='listings',
        help_text='Only verified WorkerProfiles can list products.',
    )

    # ── Category ─────────────────────────────────────────────────────────────
    category     = models.ForeignKey(
        MarketplaceCategory,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='products',
    )

    # Optional link to the trade this product belongs to
    # e.g. an electrician listing wire reels → Electrician trade
    trade        = models.ForeignKey(
        TradeCategory,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='marketplace_products',
        help_text='Which trade this product is relevant to.',
    )

    # ── Content ──────────────────────────────────────────────────────────────
    title        = models.CharField(max_length=200)
    description  = models.TextField(
        help_text='Describe the item, its condition, age, brand, and any defects.',
    )
    condition    = models.CharField(
        max_length=20,
        choices=Condition.choices,
        default=Condition.USED_GOOD,
    )
    brand        = models.CharField(max_length=100, blank=True,
                                    help_text='Brand or manufacturer e.g. Bosch, Dewalt')
    model_number = models.CharField(max_length=100, blank=True)

    # ── Pricing ──────────────────────────────────────────────────────────────
    price        = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        help_text='Listed price in NGN. Buyers can make lower offers.',
    )
    min_offer    = models.DecimalField(
        max_digits=12, decimal_places=2,
        null=True, blank=True,
        help_text='Minimum offer the seller will consider (optional, not shown to buyers).',
    )
    offers_allowed = models.BooleanField(
        default=True,
        help_text='Allow buyers to make price offers.',
    )

    # ── Location ─────────────────────────────────────────────────────────────
    state        = models.CharField(max_length=40, choices=NIGERIAN_STATES, blank=True)
    lga          = models.CharField(max_length=120, blank=True)

    # ── Pickup / Delivery ────────────────────────────────────────────────────
    # Phase 1: pickup only. Phase 2: add delivery options.
    pickup_only  = models.BooleanField(
        default=True,
        help_text='Phase 1: all transactions are pickup/meetup.',
    )
    pickup_notes = models.TextField(
        blank=True,
        help_text='Meeting point, availability for pickup, etc.',
    )

    # ── Status & Lifecycle ───────────────────────────────────────────────────
    status       = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT,
    )
    views_count  = models.PositiveIntegerField(default=0)
    slots        = models.PositiveSmallIntegerField(
        default=1,
        help_text='Quantity available. Set to 1 for single items.',
    )

    # ── Sentence-Transformer Embedding ───────────────────────────────────────
    # Computed from: "{category} {condition}. {title}. {description}"
    # Recomputed via Celery when title/description/category changes.
    text_embedding = models.JSONField(
        null=True, blank=True,
        help_text='Sentence-transformer embedding (768-dim) for semantic search.',
    )
    text_embedding_updated = models.DateTimeField(null=True, blank=True)

    # ── Platform Fee ─────────────────────────────────────────────────────────
    platform_fee_pct = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('5.00'),
        help_text='Platform fee percentage deducted from seller at payout.',
    )

    created      = models.DateTimeField(auto_now_add=True)
    updated      = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created']

    def __str__(self):
        return f'{self.title} — ₦{self.price} [{self.condition}]'

    def get_embedding_text(self) -> str:
        """
        Build the text input for the sentence-transformer.
        Combines category, condition, title, description, and brand.
        """
        parts = []
        if self.category:
            parts.append(self.category.name)
        parts.append(self.get_condition_display())
        if self.brand:
            parts.append(self.brand)
        parts.append(self.title)
        if self.description:
            parts.append(self.description)
        return '. '.join(parts)

    @property
    def seller_amount(self) -> Decimal:
        """Amount seller receives after platform fee deduction."""
        fee = self.price * (self.platform_fee_pct / Decimal('100'))
        return self.price - fee


# ──────────────────────────────────────────────────────────────────────────────
#  3.  PRODUCT IMAGE
# ──────────────────────────────────────────────────────────────────────────────

class ProductImage(models.Model):
    """
    Multiple images per product. First image (display_order=0) is the cover.
    """
    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product       = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='images',
    )
    image         = models.ImageField(upload_to='marketplace/products/%Y/%m/')
    caption       = models.CharField(max_length=200, blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)
    created       = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['product', 'display_order']

    def __str__(self):
        return f'{self.product.title} — image {self.display_order}'


# ──────────────────────────────────────────────────────────────────────────────
#  4.  OFFER
# ──────────────────────────────────────────────────────────────────────────────

class Offer(models.Model):
    """
    A buyer makes an offer on a product at a price lower than the listed price.

    Flow:
        Buyer submits offer → Seller accepts / counters / declines
        → If accepted: Order is created automatically
        → If countered: Buyer can accept the counter or walk away
        → If declined: Offer closes, buyer can try again
    """

    class Status(models.TextChoices):
        PENDING    = 'pending',    'Pending'
        ACCEPTED   = 'accepted',   'Accepted'
        DECLINED   = 'declined',   'Declined'
        COUNTERED  = 'countered',  'Countered'
        WITHDRAWN  = 'withdrawn',  'Withdrawn'
        EXPIRED    = 'expired',    'Expired'

    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product        = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='offers',
    )
    buyer          = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='marketplace_offers',
    )
    offered_price  = models.DecimalField(
        max_digits=12, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        help_text='Price the buyer is offering in NGN.',
    )
    counter_price  = models.DecimalField(
        max_digits=12, decimal_places=2,
        null=True, blank=True,
        help_text='Counter-offer price from seller.',
    )
    message = models.TextField(
        blank=True,
        help_text='Optional message from buyer to seller.',
    )
    counter_message = models.TextField(
        blank=True,
        help_text='Optional message from seller when countering.',
    )
    status         = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING,
    )
    expires_at     = models.DateTimeField(
        null=True, blank=True,
        help_text='Offer auto-expires after 48 hours if not responded to.',
    )
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Offer ₦{self.offered_price} on {self.product.title} by {self.buyer.username}'


# ──────────────────────────────────────────────────────────────────────────────
#  5.  ORDER
# ──────────────────────────────────────────────────────────────────────────────

class Order(models.Model):
    """
    Created when a buyer pays the listed price OR an offer is accepted.

    The agreed_price is either:
        - product.price  (Buy Now)
        - offer.offered_price  (offer accepted)
        - offer.counter_price  (buyer accepted counter-offer)

    Paystack escrow pattern mirrors the job milestone escrow:
        Buyer pays → funds held in Paystack balance → meetup/handover
        → Buyer confirms → seller_amount transferred to seller bank account
        → Platform keeps the 5% fee
    """

    class Status(models.TextChoices):
        PENDING           = 'pending',           'Pending Payment'
        PAID              = 'paid',              'Paid (In Escrow)'
        MEETUP_SCHEDULED  = 'meetup_scheduled',  'Meetup Scheduled'
        CONFIRMED         = 'confirmed',         'Buyer Confirmed Receipt'
        COMPLETED         = 'completed',         'Completed'
        DISPUTED          = 'disputed',          'Disputed'
        CANCELLED         = 'cancelled',         'Cancelled'
        REFUNDED          = 'refunded',          'Refunded'

    id                    = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product               = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name='orders',
    )
    buyer                 = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='marketplace_orders',
    )
    seller                = models.ForeignKey(
        WorkerProfile,
        on_delete=models.PROTECT,
        related_name='marketplace_sales',
    )
    offer                 = models.OneToOneField(
        Offer, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='order',
        help_text='Set when order originated from an accepted offer.',
    )

    # ── Financials ────────────────────────────────────────────────────────────
    agreed_price          = models.DecimalField(
        max_digits=12, decimal_places=2,
        help_text='Final agreed price in NGN (listed price or accepted offer).',
    )
    platform_fee_pct      = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal('5.00'),
    )
    platform_fee_amount   = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text='Computed on completion: agreed_price × platform_fee_pct / 100',
    )
    seller_payout_amount  = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text='agreed_price - platform_fee_amount. Transferred to seller on completion.',
    )

    # ── Paystack References ───────────────────────────────────────────────────
    paystack_payment_ref  = models.CharField(
        max_length=100, unique=True, null=True, blank=True,
        help_text='Paystack transaction reference for the buyer payment.',
    )
    paystack_transfer_ref = models.CharField(
        max_length=100, null=True, blank=True,
        help_text='Paystack transfer reference for the seller payout.',
    )

    # ── Status & Lifecycle ────────────────────────────────────────────────────
    status                = models.CharField(
        max_length=30, choices=Status.choices, default=Status.PENDING,
    )
    auto_complete_at      = models.DateTimeField(
        null=True, blank=True,
        help_text='Auto-completes 7 days after buyer confirms receipt if no dispute.',
    )
    meetup_notes          = models.TextField(
        blank=True,
        help_text='Location and time agreed for handover.',
    )
    paid_at               = models.DateTimeField(null=True, blank=True)
    confirmed_at          = models.DateTimeField(null=True, blank=True)
    completed_at          = models.DateTimeField(null=True, blank=True)
    created_at            = models.DateTimeField(auto_now_add=True)
    updated_at            = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Order #{str(self.pk)[:8]} — {self.product.title} ₦{self.agreed_price}'

    def compute_financials(self):
        """Compute and save fee/payout amounts."""
        fee = self.agreed_price * (self.platform_fee_pct / Decimal('100'))
        self.platform_fee_amount  = fee.quantize(Decimal('0.01'))
        self.seller_payout_amount = (self.agreed_price - fee).quantize(Decimal('0.01'))


# ──────────────────────────────────────────────────────────────────────────────
#  6.  ORDER DISPUTE
# ──────────────────────────────────────────────────────────────────────────────

class OrderDispute(models.Model):
    """
    Raised when buyer or seller has a problem with an order.
    Admin reviews and either releases funds to seller or refunds buyer.
    """

    class Resolution(models.TextChoices):
        PENDING              = 'pending',              'Pending'
        RELEASED_TO_SELLER   = 'released_to_seller',   'Released to Seller'
        REFUNDED_TO_BUYER    = 'refunded_to_buyer',    'Refunded to Buyer'
        SPLIT                = 'split',                'Split'

    id              = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order           = models.OneToOneField(
        Order, on_delete=models.PROTECT, related_name='dispute',
    )
    raised_by       = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='marketplace_disputes_raised',
    )
    reason          = models.TextField()
    evidence        = models.FileField(
        upload_to='marketplace/disputes/', null=True, blank=True,
    )
    resolution      = models.CharField(
        max_length=30, choices=Resolution.choices, default=Resolution.PENDING,
    )
    resolution_note = models.TextField(blank=True)
    resolved_at     = models.DateTimeField(null=True, blank=True)
    resolved_by     = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='marketplace_disputes_resolved',
    )
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Dispute: {self.order} — {self.resolution}'


# ──────────────────────────────────────────────────────────────────────────────
#  7.  PRODUCT REVIEW
# ──────────────────────────────────────────────────────────────────────────────

class ProductReview(models.Model):
    """
    Left by the buyer after an order is COMPLETED.
    Only one review per order — enforced by OneToOneField.
    """

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order      = models.OneToOneField(
        Order, on_delete=models.CASCADE, related_name='review',
    )
    reviewer   = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='marketplace_reviews_given',
    )
    seller     = models.ForeignKey(
        WorkerProfile,
        on_delete=models.CASCADE,
        related_name='marketplace_reviews_received',
    )
    product    = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='reviews',
    )
    rating     = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text='1–5 star rating.',
    )
    comment    = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.reviewer.username} → {self.seller} — {self.rating}★'


# ──────────────────────────────────────────────────────────────────────────────
#  8.  SAVED PRODUCT
# ──────────────────────────────────────────────────────────────────────────────

class SavedProduct(models.Model):
    """Buyers can bookmark products they're interested in."""

    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user       = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='saved_products',
    )
    product    = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name='saved_by',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'product')
        ordering        = ['-created_at']

    def __str__(self):
        return f'{self.user.username} saved {self.product.title}'