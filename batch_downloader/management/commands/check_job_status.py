from django.core.management.base import BaseCommand
from batch_downloader.models import DownloadJob, ImageItem


class Command(BaseCommand):
    help = 'Check the status of a download job'

    def add_arguments(self, parser):
        parser.add_argument('job_id', type=str, help='Job ID to check')

    def handle(self, *args, **options):
        job_id = options['job_id']
        
        try:
            job = DownloadJob.objects.get(id=job_id)
            
            self.stdout.write(f'Job ID: {job.id}')
            self.stdout.write(f'Status: {job.status}')
            self.stdout.write(f'Total Images: {job.total_images}')
            self.stdout.write(f'Completed Images: {job.completed_images}')
            self.stdout.write(f'Failed Images: {job.failed_images}')
            self.stdout.write(f'Progress: {job.progress_percentage}%')
            self.stdout.write(f'Created: {job.created_at}')
            
            # Check individual image statuses  
            images = ImageItem.objects.filter(product_batch__job=job)
            status_counts = {}
            for image in images:
                status = image.status
                status_counts[status] = status_counts.get(status, 0) + 1

            self.stdout.write(f'\nImage Status Breakdown:')
            for status, count in status_counts.items():
                self.stdout.write(f'  {status}: {count}')

            # Show some completed images
            completed_images = images.filter(status='DONE')
            if completed_images.exists():
                self.stdout.write(f'\nCompleted images:')
                for img in completed_images[:3]:  # Show first 3
                    self.stdout.write(f'  - {img.filename} ({img.size_bytes} bytes)')
                    
            # Show any failed images
            failed_images = images.filter(status='FAILED')
            if failed_images.exists():
                self.stdout.write(f'\nFailed images:')
                for img in failed_images[:3]:  # Show first 3
                    self.stdout.write(f'  - {img.url}: {img.error_message}')
                    
        except DownloadJob.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f'Job with ID {job_id} not found')
            )
