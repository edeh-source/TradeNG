from django.apps import AppConfig

class JobsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name               = 'jobs'
    verbose_name       = 'TradeLink NG Marketplace'

    def ready(self):
        import jobs.signals  # noqa: F401

        # Load the sentence-transformer eagerly at startup so the first search
        # request is fast and no online network call can sneak in.
        # We intentionally do NOT guard on RUN_MAIN here — that env var is only
        # set by Django's dev-server reloader and is never present under Gunicorn,
        # Uvicorn, or Celery, which meant the model was never pre-loaded in
        # production and fell back to a lazy (online) load on the first request.
        self._load_model_sync()

    @staticmethod
    def _load_model_sync():
        try:
            from jobs.service.text_encoder import text_encoder
            print("[TradeLink] Loading sentence-transformer (sync)...")
            text_encoder._ensure_loaded()
            print("[TradeLink] ✓ Model ready.")
        except Exception as e:
            print(f"[TradeLink] ✗ Failed: {e}")