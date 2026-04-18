web: gunicorn technicians.wsgi --log-file -
worker: celery -A technicians worker --loglevel=info --concurrency=1