"""
marketplace/apps.py
====================
AppConfig for the marketplace app.

The ready() method is the Django-recommended place to connect signals.
Importing signals here (rather than in models.py) avoids import cycles and
ensures signals are registered exactly once per process.
"""

from django.apps import AppConfig


class JobsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name               = 'jobs'
    verbose_name       = 'TradeLink NG Marketplace'

    def ready(self):
        # Importing the signals module registers all @receiver decorators.
        # This import must stay inside ready() to avoid circular imports.
        import jobs.signals  # noqa: F401