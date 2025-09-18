from django.core.management.base import BaseCommand
from batch_downloader.models import DownloadJob, ImageItem
from batch_downloader.services.simple_downloader import simple_download_service


class Command(BaseCommand):
    help = 'Manually trigger download job processing'

    def add_arguments(self, parser):
        parser.add_argument('job_id', type=str, help='Job ID to process')

    def handle(self, *args, **options):
        job_id = options['job_id']
        
        try:
            job = DownloadJob.objects.get(id=job_id)
            
            if job.status not in ['PENDING', 'FAILED']:
                self.stdout.write(
                    self.style.WARNING(f'Job {job_id} is not in PENDING/FAILED status (current: {job.status})')
                )
                return
                
            self.stdout.write(f'Processing job {job_id}...')
            
            # Update job status
            job.status = 'RUNNING'
            job.save()
            
            # Get all pending images
            images = ImageItem.objects.filter(
                product_batch__job=job,
                status='PENDING'
            ).select_related('product_batch')
            
            self.stdout.write(f'Found {images.count()} pending images')
            
            # Process each image
            for i, image_item in enumerate(images, 1):
                self.stdout.write(f'[{i}/{images.count()}] Processing: {image_item.url}')
                
                success, message = simple_download_service.download_image(image_item)
                
                if success:
                    self.stdout.write(f'  ✓ SUCCESS: {message}')
                    job.completed_images += 1
                else:
                    self.stdout.write(f'  ✗ FAILED: {message}')
                    job.failed_images += 1
                    
                # Update progress
                job.save(update_fields=['completed_images', 'failed_images'])
            
            # Final status update
            if job.failed_images == 0:
                job.status = 'COMPLETED'
            else:
                job.status = 'COMPLETED'  # Completed with some failures
                
            job.save()
            
            self.stdout.write(
                self.style.SUCCESS(f'Job processing completed: {job.completed_images} successful, {job.failed_images} failed')
            )
                    
        except DownloadJob.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f'Job with ID {job_id} not found')
            )
