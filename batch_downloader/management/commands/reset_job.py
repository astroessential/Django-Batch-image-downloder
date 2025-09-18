from django.core.management.base import BaseCommand
from batch_downloader.models import DownloadJob, ImageItem


class Command(BaseCommand):
    help = 'Reset a job back to PENDING status for retesting'

    def add_arguments(self, parser):
        parser.add_argument('job_id', type=str, help='Job ID to reset')

    def handle(self, *args, **options):
        job_id = options['job_id']
        
        try:
            job = DownloadJob.objects.get(id=job_id)
            
            # Reset job status
            job.status = 'PENDING'
            job.completed_images = 0
            job.failed_images = 0
            job.save()
            
            # Reset all image items
            ImageItem.objects.filter(product_batch__job=job).update(
                status='PENDING',
                error_message='',
                filename='',
                file_path='',
                content_type='',
                size_bytes=0,
                checksum_sha256=''
            )
            
            self.stdout.write(
                self.style.SUCCESS(f'Job {job_id} reset to PENDING status')
            )
            
        except DownloadJob.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f'Job {job_id} not found')
            )
