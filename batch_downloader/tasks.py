import asyncio
from typing import List
from celery import shared_task, group, chord
from django.db import transaction
from django.db.models import F

from .models import DownloadJob, ProductBatch, ImageItem
from .services.downloader import download_service
from .services.progress import progress_service
from .services.zip_service import zip_service


@shared_task(bind=True, max_retries=3)
def download_image_task(self, image_item_id: int):
    """
    Download a single image
    This task is idempotent and can be retried
    """
    try:
        image_item = ImageItem.objects.get(id=image_item_id)
        
        # Emit progress event that download started
        progress_service.emit_image_started(image_item)
        
        # Download the image (convert async to sync)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            success, message = loop.run_until_complete(
                download_service.download_image(image_item)
            )
        finally:
            loop.close()
        
        if success:
            # Update product batch counters
            with transaction.atomic():
                product_batch = ProductBatch.objects.select_for_update().get(
                    id=image_item.product_batch.id
                )
                
                if image_item.status == 'DONE':
                    product_batch.downloaded_count = F('downloaded_count') + 1
                    product_batch.bytes_downloaded = F('bytes_downloaded') + image_item.size_bytes
                elif image_item.status == 'SKIPPED':
                    product_batch.downloaded_count = F('downloaded_count') + 1
                    # Don't add to bytes_downloaded for skipped files
                
                product_batch.save(update_fields=['downloaded_count', 'bytes_downloaded'])
                product_batch.refresh_from_db()
            
            # Emit progress events
            if image_item.status == 'DONE':
                progress_service.emit_image_completed(image_item)
            elif image_item.status == 'SKIPPED':
                progress_service.emit_image_skipped(image_item)
            
        else:
            # Update failed count
            with transaction.atomic():
                product_batch = ProductBatch.objects.select_for_update().get(
                    id=image_item.product_batch.id
                )
                product_batch.failed_count = F('failed_count') + 1
                product_batch.save(update_fields=['failed_count'])
                product_batch.refresh_from_db()
            
            progress_service.emit_image_failed(image_item)
        
        return {
            'success': success,
            'message': message,
            'image_id': image_item_id,
            'product_batch_id': image_item.product_batch.id,
            'size_bytes': image_item.size_bytes if success else 0
        }
        
    except ImageItem.DoesNotExist:
        return {'success': False, 'message': 'Image item not found', 'image_id': image_item_id}
    
    except Exception as exc:
        # Retry on unexpected errors
        if self.request.retries < self.max_retries:
            raise self.retry(countdown=60 * (2 ** self.request.retries), exc=exc)
        
        # Final failure - update database
        try:
            image_item = ImageItem.objects.get(id=image_item_id)
            image_item.status = 'FAILED'
            image_item.error_message = f"Task failed after retries: {str(exc)}"
            image_item.save()
            
            # Update failed count
            with transaction.atomic():
                product_batch = ProductBatch.objects.select_for_update().get(
                    id=image_item.product_batch.id
                )
                product_batch.failed_count = F('failed_count') + 1
                product_batch.save(update_fields=['failed_count'])
            
            progress_service.emit_image_failed(image_item)
            
        except Exception:
            pass  # Ignore errors in error handling
        
        return {'success': False, 'message': str(exc), 'image_id': image_item_id}


@shared_task
def finalize_product_batch(product_batch_id: int, download_results: List[dict]):
    """
    Finalize a product batch after all downloads complete
    """
    try:
        product_batch = ProductBatch.objects.get(id=product_batch_id)
        
        # Determine final status
        if product_batch.failed_count == 0:
            status = 'DONE'
        elif product_batch.downloaded_count == 0:
            status = 'FAILED'
        else:
            status = 'PARTIAL'
        
        product_batch.status = status
        product_batch.save(update_fields=['status'])
        
        # Update ZIP status
        zip_service.update_product_zip_status(product_batch)
        
        # Emit progress events
        progress_service.emit_product_completed(product_batch)
        
        if product_batch.zip_ready:
            progress_service.emit_product_zip_ready(product_batch)
        
        return {
            'product_batch_id': product_batch_id,
            'status': status,
            'downloaded_count': product_batch.downloaded_count,
            'failed_count': product_batch.failed_count
        }
        
    except ProductBatch.DoesNotExist:
        return {'error': 'Product batch not found', 'product_batch_id': product_batch_id}


@shared_task
def finalize_download_job(job_id: str, product_results: List[dict]):
    """
    Finalize the entire download job after all products complete
    """
    try:
        job = DownloadJob.objects.get(id=job_id)
        
        # Recalculate totals
        product_batches = ProductBatch.objects.filter(job=job)
        total_downloaded = sum(p.downloaded_count for p in product_batches)
        total_failed = sum(p.failed_count for p in product_batches)
        
        job.completed_images = total_downloaded
        job.failed_images = total_failed
        
        # Determine final job status
        if total_failed == 0:
            job.status = 'COMPLETED'
        elif total_downloaded == 0:
            job.status = 'FAILED'
        else:
            job.status = 'COMPLETED'  # Partial success is still completed
        
        job.save(update_fields=['completed_images', 'failed_images', 'status'])
        
        # Emit final job progress
        progress_service.emit_job_status_change(job)
        
        return {
            'job_id': job_id,
            'status': job.status,
            'completed_images': job.completed_images,
            'failed_images': job.failed_images
        }
        
    except DownloadJob.DoesNotExist:
        return {'error': 'Job not found', 'job_id': job_id}


@shared_task
def start_download_job(job_id: str):
    """
    Start downloading all images for a job
    Creates a chord of product groups
    """
    try:
        job = DownloadJob.objects.get(id=job_id)
        job.status = 'RUNNING'
        job.save(update_fields=['status'])
        
        progress_service.emit_job_status_change(job)
        
        # Get all product batches
        product_batches = ProductBatch.objects.filter(job=job).prefetch_related('images')
        
        # Create download tasks for each product
        product_chords = []
        
        for product_batch in product_batches:
            # Update product status
            product_batch.status = 'RUNNING'
            product_batch.save(update_fields=['status'])
            
            # Get all pending images for this product
            image_items = product_batch.images.filter(status='PENDING')
            
            if image_items.exists():
                # Create download tasks for all images in this product
                download_tasks = group(
                    download_image_task.s(image.id) for image in image_items
                )
                
                # Create chord: downloads followed by product finalization
                product_chord = chord(download_tasks)(
                    finalize_product_batch.s(product_batch.id)
                )
                product_chords.append(product_chord)
            else:
                # No images to download, finalize immediately
                finalize_product_batch.delay(product_batch.id, [])
        
        # Create final chord: all products followed by job finalization
        if product_chords:
            # Wait for all product chords to complete, then finalize job
            job_chord = chord(product_chords)(
                finalize_download_job.s(job_id)
            )
        else:
            # No products to process, finalize immediately
            finalize_download_job.delay(job_id, [])
        
        return {'job_id': job_id, 'status': 'RUNNING', 'product_count': len(product_batches)}
        
    except DownloadJob.DoesNotExist:
        return {'error': 'Job not found', 'job_id': job_id}
    except Exception as exc:
        # Update job status to failed
        try:
            job = DownloadJob.objects.get(id=job_id)
            job.status = 'FAILED'
            job.save(update_fields=['status'])
            progress_service.emit_job_status_change(job)
        except:
            pass
        
        raise exc
