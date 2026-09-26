#!/usr/bin/env bash
set -o errexit

pip install -r requirements.txt

python manage.py collectstatic --no-input

python manage.py migrate

# Ensure Celery Beat and Results tables exist (Django Admin monitoring)
python manage.py migrate django_celery_beat
python manage.py migrate django_celery_results