import os
from celery import Celery
from django.conf import settings

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')

app = Celery('myproject')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

# Configure Celery settings
app.conf.update(
    # Worker settings
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    worker_max_tasks_per_child=1000,
    
    # Task routing
    task_routes={
        'batch_downloader.tasks.download_image_task': {'queue': 'download'},
        'batch_downloader.tasks.start_download_job': {'queue': 'jobs'},
        'batch_downloader.tasks.finalize_product_batch': {'queue': 'jobs'},
        'batch_downloader.tasks.finalize_download_job': {'queue': 'jobs'},
    },
    
    # Task time limits
    task_soft_time_limit=300,  # 5 minutes
    task_time_limit=600,       # 10 minutes (hard limit)
    
    # Retry settings
    task_retry_jitter=True,
    task_retry_jitter_max=30,
)

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
