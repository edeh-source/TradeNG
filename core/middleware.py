"""
core/middleware.py
==================
Global rate-limit backstop middleware.

Applies a coarse per-IP cap across *all* endpoints as a last line of defence
against scrapers and credential-stuffing bots that hit arbitrary URLs not
individually protected by the ``@rate_limit`` decorator.

Configuration (via Django settings, all optional):

    GLOBAL_RATE_LIMIT          = 300   # requests per window (default 300)
    GLOBAL_RATE_LIMIT_WINDOW   = 60    # window in seconds   (default 60)

Exempt path prefixes (never rate-limited):

    /admin/   — Django admin (has its own auth)
    /static/  — served by WhiteNoise before reaching Python
    /media/   — served by WhiteNoise / Cloudinary

Register in settings.py MIDDLEWARE, *after* WhiteNoiseMiddleware and *before*
SessionMiddleware so static assets are never counted:

    MIDDLEWARE = [
        'django.middleware.security.SecurityMiddleware',
        'whitenoise.middleware.WhiteNoiseMiddleware',
        'core.middleware.GlobalRateLimitMiddleware',   # ← here
        'django.contrib.sessions.middleware.SessionMiddleware',
        ...
    ]
"""

import asyncio
import hashlib
import logging
import traceback
from datetime import datetime, timezone

from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpResponse

logger = logging.getLogger(__name__)
terminal_logger = logging.getLogger('production.errors')


# Paths that are always exempt from the global cap.
_EXEMPT_PREFIXES = ('/admin/', '/static/', '/media/')

# Defaults — override in settings if needed.
_DEFAULT_LIMIT  = 300
_DEFAULT_WINDOW = 60   # seconds


class GlobalRateLimitMiddleware:
    """
    Coarse sliding-window (or fixed-window fallback) per-IP rate limiter
    applied globally before any view logic runs.

    Fully async-compatible for use with Daphne / ASGI.
    """

    async_capable = True
    sync_capable = False

    def __init__(self, get_response):
        self.get_response = get_response
        self.limit  = getattr(settings, 'GLOBAL_RATE_LIMIT',        _DEFAULT_LIMIT)
        self.window = getattr(settings, 'GLOBAL_RATE_LIMIT_WINDOW',  _DEFAULT_WINDOW)

    async def __call__(self, request):
        # Skip exempt paths (admin, static, media).
        path = request.path_info
        if any(path.startswith(prefix) for prefix in _EXEMPT_PREFIXES):
            return await self.get_response(request)

        # Derive IP from X-Forwarded-For (set by reverse proxies / Render).
        x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        ip = x_forwarded.split(',')[0].strip() if x_forwarded else request.META.get('REMOTE_ADDR', 'unknown')

        # Hash the IP so the cache key is always a safe fixed-length string.
        ip_hash   = hashlib.sha256(ip.encode()).hexdigest()[:24]
        cache_key = f'rl:global:{ip_hash}'

        # Run the blocking cache call in a thread pool so we don't block the event loop.
        blocked = await sync_to_async(self._is_blocked)(cache_key, ip)
        if blocked:
            return HttpResponse(
                'Rate limit exceeded. Please slow down.',
                status=429,
                content_type='text/plain',
            )

        return await self.get_response(request)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _is_blocked(self, cache_key: str, ip: str) -> bool:
        """Return True if the IP has exceeded the global limit."""
        from core.ratelimit import count_requests

        try:
            count = count_requests(cache_key, self.window)
            if count > self.limit:
                logger.warning(
                    'global_ratelimit: BLOCKED ip=%s count=%d limit=%d window=%ds',
                    ip, count, self.limit, self.window,
                )
                return True
            return False
        except Exception as exc:
            # Fail open — never block users due to cache downtime.
            logger.warning('global_ratelimit: cache error (failing open): %s', exc)
            return False


class TerminalErrorLoggingMiddleware:
    """
    High-visibility production error logging middleware.

    Intercepts all unhandled exceptions (500 errors) and HTTP >= 500 status
    codes, printing a clean, impossible-to-miss diagnostic box to the terminal
    stderr/stdout with full request context (URL, Method, IP, User, Sanitized
    Parameters) and complete Python traceback.

    Fully async-compatible for use with Daphne / ASGI.
    """

    async_capable = True
    sync_capable = False

    # Keys whose values should be redacted to prevent sensitive leaks in logs.
    SENSITIVE_KEYS = {
        'password', 'password1', 'password2', 'secret', 'token', 'access_token',
        'refresh_token', 'api_key', 'key', 'authorization', 'card', 'cvv', 'pin',
        'paystack_secret_key', 'paystack_public_key', 'secret_key',
    }

    def __init__(self, get_response):
        self.get_response = get_response

    async def __call__(self, request):
        request._terminal_error_logged = False
        try:
            response = await self.get_response(request)
        except asyncio.CancelledError:
            # Client disconnected or Render killed the request — not a server
            # error. Re-raise silently without logging.
            raise
        except Exception:
            # process_exception handles logging; re-raise so Django continues
            # its normal exception-handling chain (custom 500 view, etc.).
            raise

        # Log 5xx responses returned directly without raising an exception.
        if response.status_code >= 500 and not getattr(request, '_terminal_error_logged', False):
            self._log_http_5xx(request, response)

        return response

    async def process_exception(self, request, exception):
        """
        Called by Django's ASGI handler for unhandled view exceptions.

        Must be async to avoid the async_to_sync wrapping warning produced
        when Django wraps a sync process_exception on an async middleware.
        """
        # CancelledError is not a server error — client disconnected or Render
        # timed out the request. Never log these as production errors.
        if isinstance(exception, asyncio.CancelledError):
            return None

        request._terminal_error_logged = True
        try:
            now_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
            method  = request.method
            path    = request.get_full_path()
            try:
                full_url = request.build_absolute_uri()
            except Exception:
                full_url = path

            ip               = self._get_client_ip(request)
            user_info        = self._get_user_info(request)
            sanitized_params = self._get_sanitized_params(request)
            tb               = traceback.format_exc()

            banner = [
                "",
                "╔" + "═" * 78 + "╗",
                f"║ 🚨 [PRODUCTION 500 ERROR] {exception.__class__.__name__}".ljust(79) + "║",
                "╠" + "═" * 78 + "╣",
                f"║ Time:        {now_str}".ljust(79) + "║",
                f"║ Method:      {method}".ljust(79) + "║",
                f"║ Path:        {path}".ljust(79) + "║",
                f"║ Full URL:    {full_url}".ljust(79) + "║",
                f"║ Client IP:   {ip}".ljust(79) + "║",
                f"║ User:        {user_info}".ljust(79) + "║",
                f"║ Params:      {sanitized_params}".ljust(79) + "║",
                f"║ Error:       {str(exception)}".ljust(79) + "║",
                "╟" + "─" * 78 + "╢",
                "║ TRACEBACK:".ljust(79) + "║",
            ]

            for line in tb.splitlines():
                banner.append(f"║   {line}".ljust(79) + "║")

            banner.append("╚" + "═" * 78 + "╝")
            banner.append("")

            terminal_logger.error("\n".join(banner))

        except Exception as logging_error:
            # Emergency fallback: ensure something is printed even if
            # the banner formatting itself fails.
            terminal_logger.error(
                "TerminalErrorLoggingMiddleware failed to format error: %s",
                logging_error,
                exc_info=True,
            )

        # Return None so Django continues with its standard 500 response / view.
        return None

    # ── Private helpers ───────────────────────────────────────────────────────

    def _log_http_5xx(self, request, response):
        """Log 5xx responses that were generated without an unhandled exception."""
        try:
            ip = self._get_client_ip(request)
            user_info = self._get_user_info(request)
            terminal_logger.error(
                "🚨 [PRODUCTION HTTP %d] Method=%s Path=%s User=%s IP=%s",
                response.status_code,
                request.method,
                request.get_full_path(),
                user_info,
                ip,
            )
        except Exception:
            pass

    def _get_client_ip(self, request) -> str:
        """Extract client IP, taking proxy headers into account."""
        x_forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if x_forwarded:
            return x_forwarded.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', 'unknown')

    def _get_user_info(self, request) -> str:
        """Extract user identification safely."""
        user = getattr(request, 'user', None)
        if user and getattr(user, 'is_authenticated', False):
            username = getattr(user, 'email', '') or getattr(user, 'username', str(user))
            user_id = getattr(user, 'pk', 'unknown')
            return f"{username} (ID: {user_id})"
        elif user:
            return "AnonymousUser"
        return "Unauthenticated / Pre-Auth"

    def _get_sanitized_params(self, request) -> str:
        """Collect and sanitize query parameters and POST fields."""
        params = {}
        # GET query params
        for k, v in request.GET.items():
            params[k] = '[REDACTED]' if self._is_sensitive(k) else v

        # POST form data (only for standard form payloads, avoid raw files)
        if request.method == 'POST' and request.content_type in (
            'application/x-www-form-urlencoded',
            'multipart/form-data',
        ):
            for k, v in request.POST.items():
                params[k] = '[REDACTED]' if self._is_sensitive(k) else (v if len(str(v)) < 120 else f"{str(v)[:120]}...")

        return str(params) if params else "{}"

    def _is_sensitive(self, key_name: str) -> bool:
        """Check if parameter name matches sensitive keywords."""
        key_lower = key_name.lower().replace('-', '_')
        return any(sensitive in key_lower for sensitive in self.SENSITIVE_KEYS)