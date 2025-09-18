import json
from typing import Dict, Any

# Temporarily disable EventStream until we fix the configuration
# from django_eventstream import send_event
from django.db.models import F, Sum

from ..models import DownloadJob, ProductBatch, ImageItem


class ProgressService:
    """Service for emitting real-time progress updates via Server-Sent Events"""
    
    def emit_job_progress(self, job_id: str, event_type: str, data: Dict[str, Any] = None):
        """Emit progress event for a job"""
        # TODO: Re-enable when EventStream is properly configured
        # event_data = {
        #     'type': event_type,
        #     'job_id': job_id,
        #     'timestamp': self._get_timestamp(),
        #     **(data or {})
        # }
        # send_event(f'job-{job_id}', 'job_update', event_data)
        pass
    
    def emit_product_progress(self, product_batch_id: int, event_type: str, data: Dict[str, Any] = None):
        """Emit progress event for a product batch"""
        # TODO: Re-enable when EventStream is properly configured
        pass
    
    def emit_image_started(self, image_item: ImageItem):
        """Emit event when image download starts"""
        self.emit_product_progress(
            image_item.product_batch.id,
            'image.started',
            {
                'image_id': image_item.id,
                'url': image_item.url,
                'filename': image_item.filename
            }
        )
    
    def emit_image_completed(self, image_item: ImageItem):
        """Emit event when image download completes successfully"""
        # Refresh from database to get latest counts
        product_batch = ProductBatch.objects.get(id=image_item.product_batch.id)
        
        self.emit_product_progress(
            product_batch.id,
            'image.completed',
            {
                'image_id': image_item.id,
                'url': image_item.url,
                'filename': image_item.filename,
                'size_bytes': image_item.size_bytes,
                'downloaded_count': product_batch.downloaded_count,
                'total_count': product_batch.image_count,
                'progress_percentage': product_batch.progress_percentage,
                'bytes_downloaded': product_batch.bytes_downloaded
            }
        )
        
        # Update job progress
        self._emit_job_totals_update(product_batch.job.id)
    
    def emit_image_failed(self, image_item: ImageItem):
        """Emit event when image download fails"""
        product_batch = ProductBatch.objects.get(id=image_item.product_batch.id)
        
        self.emit_product_progress(
            product_batch.id,
            'image.failed',
            {
                'image_id': image_item.id,
                'url': image_item.url,
                'error_message': image_item.error_message,
                'failed_count': product_batch.failed_count,
                'total_count': product_batch.image_count
            }
        )
        
        # Update job progress
        self._emit_job_totals_update(product_batch.job.id)
    
    def emit_image_skipped(self, image_item: ImageItem):
        """Emit event when image is skipped (duplicate)"""
        product_batch = ProductBatch.objects.get(id=image_item.product_batch.id)
        
        self.emit_product_progress(
            product_batch.id,
            'image.skipped',
            {
                'image_id': image_item.id,
                'url': image_item.url,
                'reason': 'Duplicate file (same checksum)',
                'downloaded_count': product_batch.downloaded_count,
                'total_count': product_batch.image_count,
                'progress_percentage': product_batch.progress_percentage
            }
        )
        
        # Update job progress
        self._emit_job_totals_update(product_batch.job.id)
    
    def emit_product_completed(self, product_batch: ProductBatch):
        """Emit event when all downloads for a product are complete"""
        self.emit_product_progress(
            product_batch.id,
            'product.completed',
            {
                'status': product_batch.status,
                'downloaded_count': product_batch.downloaded_count,
                'failed_count': product_batch.failed_count,
                'total_count': product_batch.image_count,
                'bytes_downloaded': product_batch.bytes_downloaded,
                'zip_ready': product_batch.zip_ready,
                'zip_size': product_batch.zip_size
            }
        )
    
    def emit_product_zip_ready(self, product_batch: ProductBatch):
        """Emit event when product ZIP is ready for download"""
        self.emit_product_progress(
            product_batch.id,
            'product.zip_ready',
            {
                'zip_ready': True,
                'zip_size': product_batch.zip_size,
                'zip_size_mb': round(product_batch.zip_size / (1024 * 1024), 2)
            }
        )
    
    def emit_job_status_change(self, job: DownloadJob):
        """Emit event when job status changes"""
        self.emit_job_progress(
            str(job.id),
            'job.status_changed',
            {
                'status': job.status,
                'total_products': job.total_products,
                'total_images': job.total_images,
                'completed_images': job.completed_images,
                'failed_images': job.failed_images,
                'progress_percentage': job.progress_percentage
            }
        )
    
    def _emit_job_totals_update(self, job_id: str):
        """Emit updated job totals"""
        try:
            job = DownloadJob.objects.get(id=job_id)
            
            # Recalculate totals from product batches
            product_stats = ProductBatch.objects.filter(job=job).aggregate(
                total_downloaded=Sum('downloaded_count'),
                total_failed=Sum('failed_count'),
                total_bytes=Sum('bytes_downloaded')
            )
            
            job.completed_images = product_stats['total_downloaded'] or 0
            job.failed_images = product_stats['total_failed'] or 0
            job.save(update_fields=['completed_images', 'failed_images'])
            
            self.emit_job_progress(
                str(job.id),
                'job.totals_updated',
                {
                    'completed_images': job.completed_images,
                    'failed_images': job.failed_images,
                    'total_images': job.total_images,
                    'progress_percentage': job.progress_percentage,
                    'total_bytes_downloaded': product_stats['total_bytes'] or 0
                }
            )
            
        except DownloadJob.DoesNotExist:
            pass
    
    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO format"""
        from django.utils import timezone
        return timezone.now().isoformat()


# Global instance
progress_service = ProgressService()
