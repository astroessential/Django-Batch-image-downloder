import json
import time
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from .models import DownloadJob, ProductBatch, ImageItem


@require_GET
def job_progress_stream(request, job_id):
    """Server-Sent Events stream for real-time job progress updates"""
    
    def event_stream():
        """Generator function for SSE stream"""
        job = get_object_or_404(DownloadJob, id=job_id)
        last_update = None
        
        while True:
            try:
                # Refresh job data
                job.refresh_from_db()
                
                # Get current progress data
                progress_data = {
                    'job': {
                        'id': str(job.id),
                        'status': job.status,
                        'total_images': job.total_images,
                        'completed_images': job.completed_images,
                        'failed_images': job.failed_images,
                        'progress_percentage': job.progress_percentage,
                        'total_size_mb': round(sum(p.bytes_downloaded for p in job.products.all()) / (1024 * 1024), 2)
                    },
                    'products': []
                }
                
                # Get product progress
                products = ProductBatch.objects.filter(job=job).order_by('product_number')
                for product in products:
                    progress_data['products'].append({
                        'product_number': product.product_number,
                        'status': product.status,
                        'downloaded_count': product.downloaded_count,
                        'failed_count': product.failed_count,
                        'image_count': product.image_count,
                        'progress_percentage': product.progress_percentage,
                        'size_mb': product.bytes_downloaded_mb,
                        'zip_ready': product.zip_ready
                    })
                
                # Only send if data changed or job completed
                current_update = json.dumps(progress_data, sort_keys=True)
                if current_update != last_update or job.status in ['COMPLETED', 'FAILED']:
                    yield f"data: {current_update}\n\n"
                    last_update = current_update
                
                # Stop streaming if job is complete
                if job.status in ['COMPLETED', 'FAILED', 'CANCELLED']:
                    yield f"event: complete\ndata: Job {job.status.lower()}\n\n"
                    break
                
                # Wait before next update
                time.sleep(1)  # Update every second
                
            except Exception as e:
                yield f"event: error\ndata: {str(e)}\n\n"
                break
    
    response = StreamingHttpResponse(
        event_stream(),
        content_type='text/event-stream'
    )
    response['Cache-Control'] = 'no-cache'
    response['Connection'] = 'keep-alive'
    response['Access-Control-Allow-Origin'] = '*'
    response['Access-Control-Allow-Headers'] = 'Cache-Control'
    
    return response
