import asyncio
from typing import List
from django.db import transaction
from django.utils import timezone

from ..models import DownloadJob, ProductBatch, ImageItem
from .downloader import download_service


class SimpleDownloadProcessor:
    """Simple download processor without Celery dependency"""
    
    def process_job(self, job_id: str):
        """Process a download job synchronously"""
        try:
            job = DownloadJob.objects.get(id=job_id)
            
            if job.status != 'PENDING':
                return f"Job {job_id} is not in PENDING status"
            
            # Update job status to running
            job.status = 'RUNNING'
            job.save()
            
            # Get all image items for this job
            image_items = ImageItem.objects.filter(
                product_batch__job=job,
                status='PENDING'
            )
            
            if not image_items.exists():
                job.status = 'COMPLETED'
                job.save()
                return f"No pending images found for job {job_id}"
            
            # Process images synchronously
            self._process_images_sync(list(image_items))
            
            # Update job status
            self._update_job_status(job)
            
            return f"Job {job_id} processed successfully"
            
        except DownloadJob.DoesNotExist:
            return f"Job {job_id} not found"
        except Exception as e:
            # Mark job as failed
            try:
                job = DownloadJob.objects.get(id=job_id)
                job.status = 'FAILED'
                job.save()
            except:
                pass
            return f"Error processing job {job_id}: {str(e)}"
    
    def _process_images_sync(self, image_items: List[ImageItem]):
        """Process images synchronously using asyncio"""
        async def process_all():
            tasks = []
            for item in image_items:
                tasks.append(self._download_single_image(item))
            
            # Process in batches to avoid overwhelming the system
            from django.conf import settings
            batch_size = getattr(settings, 'MAX_GLOBAL_CONCURRENCY', 48)
            for i in range(0, len(tasks), batch_size):
                batch = tasks[i:i + batch_size]
                await asyncio.gather(*batch, return_exceptions=True)
        
        # Run the async processing
        asyncio.run(process_all())
    
    async def _download_single_image(self, image_item: ImageItem):
        """Download a single image item"""
        try:
            success, message = await download_service.download_image(image_item)
            if success:
                # Update counters
                self._increment_completed(image_item.product_batch.job)
                self._increment_product_completed(image_item.product_batch)
            else:
                # Update failed counters
                self._increment_failed(image_item.product_batch.job)
                self._increment_product_failed(image_item.product_batch)
        except Exception as e:
            # Mark as failed
            image_item.status = 'FAILED'
            image_item.error_message = str(e)
            image_item.save()
            self._increment_failed(image_item.product_batch.job)
            self._increment_product_failed(image_item.product_batch)
    
    def _increment_completed(self, job: DownloadJob):
        """Increment completed counter"""
        with transaction.atomic():
            job.refresh_from_db()
            job.completed_images += 1
            job.save(update_fields=['completed_images'])
    
    def _increment_failed(self, job: DownloadJob):
        """Increment failed counter"""
        with transaction.atomic():
            job.refresh_from_db()
            job.failed_images += 1
            job.save(update_fields=['failed_images'])
    
    def _increment_product_completed(self, product_batch):
        """Increment product batch completed counter"""
        with transaction.atomic():
            product_batch.refresh_from_db()
            product_batch.downloaded_count += 1
            product_batch.save(update_fields=['downloaded_count'])
    
    def _increment_product_failed(self, product_batch):
        """Increment product batch failed counter"""
        with transaction.atomic():
            product_batch.refresh_from_db()
            product_batch.failed_count += 1
            product_batch.save(update_fields=['failed_count'])
    
    def _update_job_status(self, job: DownloadJob):
        """Update final job status"""
        job.refresh_from_db()
        
        total_processed = job.completed_images + job.failed_images
        
        if total_processed >= job.total_images:
            if job.failed_images == 0:
                job.status = 'COMPLETED'
            elif job.completed_images == 0:
                job.status = 'FAILED'
            else:
                job.status = 'COMPLETED'  # Partial success
        
        job.save()


# Global instance
download_processor = SimpleDownloadProcessor()
