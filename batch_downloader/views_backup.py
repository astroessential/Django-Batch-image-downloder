import json
import os
from collections import defaultdict
from typing import Dict, List

from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, Http404, HttpResponse
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.db import transaction
from django.urls import reverse
from django_ratelimit.decorators import ratelimit

from .models import DownloadJob, ProductBatch, ImageItem
from .forms import CSVUploadForm, BatchDataForm
from .validators import deduplicate_images_per_product
from .tasks import start_download_job
from .services.zip_service import zip_service


def landing_page(request):
    """Landing page with Excel-like table input"""
    csv_form = CSVUploadForm()
    batch_form = BatchDataForm()
    
    context = {
        'csv_form': csv_form,
        'batch_form': batch_form,
    }
    
    return render(request, 'batch_downloader/landing.html', context)


@require_POST
@ratelimit(key='ip', rate='5/m', method='POST')
def upload_csv(request):
    """Handle CSV file upload"""
    form = CSVUploadForm(request.POST, request.FILES)
    
    if form.is_valid():
        validation_result = form.validation_result
        
        # Create job from validated data
        job = create_download_job(validation_result.valid_rows, request.user)
        
        # Add any warnings as messages
        for warning in validation_result.warnings:
            messages.warning(request, warning)
        
        messages.success(request, f"Job created successfully! {validation_result.valid_rows_count} images queued for download.")
        
        return redirect('batch_downloader:job_detail', job_id=job.id)
    
    else:
        # Return to landing page with errors
        context = {
            'csv_form': form,
            'batch_form': BatchDataForm(),
        }
        return render(request, 'batch_downloader/landing.html', context)


@require_POST
@ratelimit(key='ip', rate='10/m', method='POST')
def create_job(request):
    """Create job from frontend table data"""
    form = BatchDataForm(request.POST)
    
    if form.is_valid():
        validation_result = form.validation_result
        
        # Create job from validated data
        job = create_download_job(validation_result.valid_rows, request.user)
        
        # Return JSON response for AJAX
        return JsonResponse({
            'success': True,
            'job_id': str(job.id),
            'job_url': reverse('batch_downloader:job_detail', kwargs={'job_id': job.id}),
            'message': f"Job created successfully! {validation_result.valid_rows_count} images queued for download.",
            'warnings': validation_result.warnings
        })
    
    else:
        return JsonResponse({
            'success': False,
            'errors': form.errors.get('batch_data', ['Invalid data'])
        }, status=400)


def job_detail(request, job_id):
    """Job detail page with progress tracking"""
    job = get_object_or_404(DownloadJob, id=job_id)
    
    # Get sorting parameters from URL
    sort_by = request.GET.get('sort_by', 'natural')  # natural, alphabetical, created, status
    sort_order = request.GET.get('sort_order', 'asc')  # asc, desc
    
    # Get product batches with sorting
    product_batches = get_sorted_products(job, sort_by, sort_order)
    
    # Calculate overall statistics
    total_size_mb = sum(p.bytes_downloaded for p in product_batches) / (1024 * 1024)
    
    # Handle AJAX requests for polling
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        products_data = []
        for product in product_batches:
            products_data.append({
                'product_number': product.product_number,
                'status': product.status,
                'progress_percentage': product.progress_percentage,
                'downloaded_count': product.downloaded_count,
                'image_count': product.image_count,
                'bytes_downloaded_mb': product.bytes_downloaded_mb,
                'zip_ready': product.zip_ready,
            })
        
        return JsonResponse({
            'job': {
                'id': str(job.id),
                'status': job.status,
                'progress_percentage': job.progress_percentage,
                'completed_images': job.completed_images,
                'total_images': job.total_images,
                'total_size_mb': round(total_size_mb, 2),
            },
            'products': products_data
        })
    
    context = {
        'job': job,
        'product_batches': product_batches,
        'total_size_mb': round(total_size_mb, 2),
        'sort_by': sort_by,
        'sort_order': sort_order,
    }
    
    return render(request, 'batch_downloader/job_detail.html', context)


def download_product_zip(request, job_id, product_number):
    """Download ZIP file for a single product"""
    job = get_object_or_404(DownloadJob, id=job_id)
    product_batch = get_object_or_404(
        ProductBatch, 
        job=job, 
        product_number=product_number
    )
    
    if not product_batch.zip_ready:
        # Try to create ZIP if not ready
        from .services.zip_service import zip_service
        zip_service.update_product_zip_status(product_batch)
        product_batch.refresh_from_db()
    
    # If we have a zip_path, serve the file directly
    if product_batch.zip_ready and product_batch.zip_path and os.path.exists(product_batch.zip_path):
        with open(product_batch.zip_path, 'rb') as f:
            response = HttpResponse(f.read(), content_type='application/zip')
            response['Content-Disposition'] = f'attachment; filename="{product_batch.product_number}.zip"'
            return response
    
    # Fallback to streaming
    return zip_service.stream_product_zip(product_batch)


def download_job_zip(request, job_id):
    """Stream ZIP file containing all products"""
    job = get_object_or_404(DownloadJob, id=job_id)
    
    # Check if any products are ready
    ready_products = ProductBatch.objects.filter(job=job, zip_ready=True)
    
    if not ready_products.exists():
        raise Http404("No products ready for download")
    
    return zip_service.stream_all_products_zip(job)


@require_http_methods(["GET"])
def job_list(request):
    """List all jobs (admin view)"""
    jobs = DownloadJob.objects.all().order_by('-created_at')[:50]  # Latest 50 jobs
    
    context = {
        'jobs': jobs,
    }
    
    return render(request, 'batch_downloader/job_list.html', context)


@require_http_methods(["GET"])
def job_progress_stream(request, job_id):
    """Server-sent events endpoint for real-time job progress updates"""
    import time
    from django.http import StreamingHttpResponse
    
    job = get_object_or_404(DownloadJob, id=job_id)
    
    def event_stream():
        # Check if job is already completed
        if job.status in ['COMPLETED', 'FAILED', 'CANCELLED']:
            # For completed jobs, send one final update and close
            # Refresh job data
            job.refresh_from_db()
            
            # Get updated product data
            product_batches = ProductBatch.objects.filter(job=job).prefetch_related('images').order_by('product_number')
            
            products_data = []
            total_size_mb = 0
            for product in product_batches:
                products_data.append({
                    'product_number': product.product_number,
                    'status': product.status,
                    'progress_percentage': product.progress_percentage,
                    'downloaded_count': product.downloaded_count,
                    'image_count': product.image_count,
                    'bytes_downloaded_mb': product.bytes_downloaded_mb,
                    'zip_ready': product.zip_ready,
                })
                total_size_mb += product.bytes_downloaded / (1024 * 1024) if product.bytes_downloaded else 0
            
            # Prepare SSE data
            progress_data = {
                'job': {
                    'id': str(job.id),
                    'status': job.status,
                    'progress_percentage': job.progress_percentage,
                    'completed_images': job.completed_images,
                    'total_images': job.total_images,
                    'total_size_mb': round(total_size_mb, 2),
                },
                'products': products_data,
                'final_update': True  # Signal this is the final update
            }
            
            # Send final update and close
            yield f"data: {json.dumps(progress_data)}\n\n"
            return
        
        # For active jobs, stream updates
        max_updates = 300  # Max 5 minutes for active jobs (1 update per second)
        update_count = 0
        
        while update_count < max_updates:
            # Refresh job data
            job.refresh_from_db()
            
            # Get updated product data
            product_batches = ProductBatch.objects.filter(job=job).prefetch_related('images').order_by('product_number')
            
            products_data = []
            total_size_mb = 0
            for product in product_batches:
                products_data.append({
                    'product_number': product.product_number,
                    'status': product.status,
                    'progress_percentage': product.progress_percentage,
                    'downloaded_count': product.downloaded_count,
                    'image_count': product.image_count,
                    'bytes_downloaded_mb': product.bytes_downloaded_mb,
                    'zip_ready': product.zip_ready,
                })
                total_size_mb += product.bytes_downloaded / (1024 * 1024) if product.bytes_downloaded else 0
            
            # Prepare SSE data
            progress_data = {
                'job': {
                    'id': str(job.id),
                    'status': job.status,
                    'progress_percentage': job.progress_percentage,
                    'completed_images': job.completed_images,
                    'total_images': job.total_images,
                    'total_size_mb': round(total_size_mb, 2),
                },
                'products': products_data,
                'final_update': job.status in ['COMPLETED', 'FAILED', 'CANCELLED']
            }
            
            # Send SSE formatted data
            yield f"data: {json.dumps(progress_data)}\n\n"
            update_count += 1
            
            # Break if job is completed after sending the update
            if job.status in ['COMPLETED', 'FAILED', 'CANCELLED']:
                return
            
            # Wait 1 second before next update
            time.sleep(1)
    
    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


@require_http_methods(["GET"])
def system_check(request):
    """System health check view"""
    from django.conf import settings
    import redis
    from celery import current_app
    import os
    
    checks = {
        'database': True,  # If we got here, database is working
        'redis': False,
        'celery': False,
        'media_write': False,
    }
    
    # Check Redis
    try:
        r = redis.from_url(settings.CELERY_BROKER_URL)
        r.ping()
        checks['redis'] = True
    except:
        pass
    
    # Check Celery
    try:
        inspect = current_app.control.inspect()
        stats = inspect.stats()
        if stats:
            checks['celery'] = True
    except:
        pass
    
    # Check media directory write permissions
    try:
        test_file = os.path.join(settings.MEDIA_ROOT, 'test_write.txt')
        os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
        with open(test_file, 'w') as f:
            f.write('test')
        os.remove(test_file)
        checks['media_write'] = True
    except:
        pass
    
    all_ok = all(checks.values())
    
    return JsonResponse({
        'status': 'ok' if all_ok else 'error',
        'checks': checks
    })


@require_http_methods(["GET"])
def sample_csv(request):
    """Download sample CSV file"""
    from django.http import HttpResponse
    
    csv_content = """Product Number,Image Src
PROD001,https://images.unsplash.com/photo-1541963463532-d68292c34d19?w=800
PROD001,https://images.unsplash.com/photo-1599305445671-ac291c95aaa9?w=800
PROD002,https://images.unsplash.com/photo-1517336714731-489689fd1ca8?w=800
PROD002,https://images.unsplash.com/photo-1487058792275-0ad4aaf24ca7?w=800
PROD003,https://images.unsplash.com/photo-1522199755839-a2bacb67c546?w=800
PROD003,https://images.unsplash.com/photo-1531297484001-80022131f5a1?w=800
PROD004,https://images.unsplash.com/photo-1525547719571-a2d4ac8945e2?w=800
PROD005,https://images.unsplash.com/photo-1580927752452-89d86da3fa0a?w=800"""
    
    response = HttpResponse(csv_content, content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="sample_batch_download.csv"'
    return response


def create_download_job(image_rows, user=None) -> DownloadJob:
    """Create a download job from validated image rows"""
    
    # Deduplicate images per product
    deduplicated_rows, warnings = deduplicate_images_per_product(image_rows)
    
    # Group by product
    products_data = defaultdict(list)
    for row in deduplicated_rows:
        products_data[row.product_number].append(row)
    
    with transaction.atomic():
        # Create the job
        job = DownloadJob.objects.create(
            created_by=user if user and user.is_authenticated else None,
            total_products=len(products_data),
            total_images=len(deduplicated_rows),
            status='PENDING'
        )
        
        # Create product batches and image items
        for product_number, rows in products_data.items():
            product_batch = ProductBatch.objects.create(
                job=job,
                product_number=product_number,
                image_count=len(rows)
            )
            
            # Create image items
            image_items = []
            for row in rows:
                image_items.append(ImageItem(
                    product_batch=product_batch,
                    url=str(row.image_src),
                    status='PENDING'
                ))
            
            ImageItem.objects.bulk_create(image_items)
        
        # Start the download job using simple downloader
        import json
import threading
from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse, HttpResponse, StreamingHttpResponse
from django.views.decorators.http import require_http_methods, require_POST
from django.contrib import messages
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.urls import reverse
import re

from .models import DownloadJob, ProductBatch, ImageItem
from .services.simple_downloader import simple_download_service


def natural_sort_key(text):
    """
    Convert a string into a list of string and number chunks.
    "z23a" -> ["z", 23, "a"]
    """
    def convert(match):
        return int(match.group()) if match.group().isdigit() else match.group().lower()
    
    return [convert(match) for match in re.finditer(r'\d+|\D+', text)]


def get_sorted_products(job, sort_by='natural', sort_order='asc'):
    """Get products sorted by different methods"""
    products = ProductBatch.objects.filter(job=job).prefetch_related('images')
    
    if sort_by == 'natural':
        # Natural sorting (handles PROD1, PROD2, PROD10 correctly)
        products_list = list(products)
        products_list.sort(key=lambda x: natural_sort_key(x.product_number), reverse=(sort_order == 'desc'))
        return products_list
    elif sort_by == 'alphabetical':
        # Pure alphabetical sorting
        order_field = 'product_number' if sort_order == 'asc' else '-product_number'
        return products.order_by(order_field)
    elif sort_by == 'created':
        # Sort by creation time
        order_field = 'created_at' if sort_order == 'asc' else '-created_at'
        return products.order_by(order_field)
    elif sort_by == 'status':
        # Sort by status
        order_field = 'status' if sort_order == 'asc' else '-status'
        return products.order_by(order_field, 'product_number')
    else:
        # Default natural sorting
        products_list = list(products)
        products_list.sort(key=lambda x: natural_sort_key(x.product_number))
        return products_list
        import threading
        
        # Set the job ID in the downloader service for proper file organization
        simple_download_service.set_job_id(job.id)
        
        # Process in background thread to avoid blocking the response
        def process_job():
            try:
                # Update job status
                job.status = 'RUNNING'
                job.save()
                
                # Get all pending images
                images = ImageItem.objects.filter(
                    product_batch__job=job,
                    status='PENDING'
                ).select_related('product_batch')
                
                # Process each image
                for image_item in images:
                    # Update product batch status to running
                    product_batch = image_item.product_batch
                    if product_batch.status == 'PENDING':
                        product_batch.status = 'RUNNING'
                        product_batch.save(update_fields=['status'])
                    
                    success, message = simple_download_service.download_image(image_item)
                    
                    # Update counters atomically
                    with transaction.atomic():
                        job.refresh_from_db()
                        product_batch.refresh_from_db()
                        
                        if success:
                            job.completed_images += 1
                            product_batch.downloaded_count += 1
                            if hasattr(image_item, 'size_bytes') and image_item.size_bytes:
                                product_batch.bytes_downloaded += image_item.size_bytes
                        else:
                            job.failed_images += 1
                            product_batch.failed_count += 1
                        
                        job.save(update_fields=['completed_images', 'failed_images'])
                        product_batch.save(update_fields=['downloaded_count', 'failed_count', 'bytes_downloaded'])
                
                # Update product batch statuses and ZIP readiness
                from .services.zip_service import zip_service
                for product_batch in ProductBatch.objects.filter(job=job):
                    if product_batch.failed_count == 0:
                        product_batch.status = 'DONE'
                    elif product_batch.downloaded_count == 0:
                        product_batch.status = 'FAILED'
                    else:
                        product_batch.status = 'PARTIAL'
                    product_batch.save(update_fields=['status'])
                    
                    # Update ZIP readiness
                    zip_service.update_product_zip_status(product_batch)
                
                # Final job status update
                if job.failed_images == 0:
                    job.status = 'COMPLETED'
                else:
                    job.status = 'COMPLETED'  # Completed with some failures
                    
                job.save()
                
            except Exception as e:
                job.status = 'FAILED'
                job.save()
        
        thread = threading.Thread(target=process_job)
        thread.daemon = True
        thread.start()
        
        return job


# Job Management Views
@require_POST
def delete_job(request, job_id):
    """Delete a job and all its data"""
    import shutil
    import os
    from django.conf import settings
    
    job = get_object_or_404(DownloadJob, id=job_id)
    
    try:
        # Cancel any running tasks
        if job.status in ['PENDING', 'RUNNING']:
            job.status = 'CANCELLED'
            job.save()
        
        # Delete physical files
        job_media_path = os.path.join(settings.MEDIA_ROOT, 'products', str(job.id))
        if os.path.exists(job_media_path):
            shutil.rmtree(job_media_path)
            print(f"Deleted job files at: {job_media_path}")
        
        # Delete job (cascades to products and images)
        job_short_id = job.short_id
        job.delete()
        
        messages.success(request, f"Job {job_short_id} and all associated files deleted successfully")
        return JsonResponse({'success': True, 'redirect': '/jobs/'})
        
    except Exception as e:
        messages.error(request, f"Error deleting job: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
def restart_job(request, job_id):
    """Restart a failed or cancelled job"""
    job = get_object_or_404(DownloadJob, id=job_id)
    
    try:
        if job.status in ['FAILED', 'CANCELLED']:
            # Reset job status
            job.status = 'PENDING'
            job.completed_images = 0
            job.failed_images = 0
            job.save()
            
            # Reset all products
            job.products.update(
                status='PENDING',
                downloaded_count=0,
                bytes_downloaded=0,
                zip_ready=False
            )
            
            # Reset all images for this job
            from .models import ImageItem
            ImageItem.objects.filter(product_batch__job=job).update(
                status='PENDING',
                error_message='',
                file_path='',
                size_bytes=0
            )
            
            # Start the job using our simple downloader
            from .services.simple_downloader import SimpleDownloadService
            downloader = SimpleDownloadService()
            
            import threading
            thread = threading.Thread(target=downloader.process_job, args=(job,))
            thread.daemon = True
            thread.start()
            
            messages.success(request, f"Job {job.short_id} restarted successfully")
            return JsonResponse({'success': True})
        else:
            return JsonResponse({'success': False, 'error': 'Only failed or cancelled jobs can be restarted'}, status=400)
            
    except Exception as e:
        messages.error(request, f"Error restarting job: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
def cancel_job(request, job_id):
    """Cancel a running job"""
    job = get_object_or_404(DownloadJob, id=job_id)
    
    try:
        if job.status in ['PENDING', 'RUNNING', 'PAUSED']:
            job.status = 'CANCELLED'
            job.save()
            
            messages.success(request, f"Job {job.short_id} cancelled successfully")
            return JsonResponse({'success': True})
        else:
            return JsonResponse({'success': False, 'error': 'Job cannot be cancelled'}, status=400)
            
    except Exception as e:
        messages.error(request, f"Error cancelling job: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
def pause_job(request, job_id):
    """Pause a running job"""
    job = get_object_or_404(DownloadJob, id=job_id)
    
    try:
        if job.status == 'RUNNING':
            job.status = 'PAUSED'
            job.save()
            
            messages.success(request, f"Job {job.short_id} paused successfully")
            return JsonResponse({'success': True})
        else:
            return JsonResponse({'success': False, 'error': 'Only running jobs can be paused'}, status=400)
            
    except Exception as e:
        messages.error(request, f"Error pausing job: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@require_POST
def resume_job(request, job_id):
    """Resume a paused job"""
    job = get_object_or_404(DownloadJob, id=job_id)
    
    try:
        if job.status == 'PAUSED':
            job.status = 'RUNNING'
            job.save()
            
            # Resume download processing in background
            from .services.simple_downloader import SimpleDownloader
            downloader = SimpleDownloader()
            
            import threading
            thread = threading.Thread(target=downloader.process_job, args=(job,))
            thread.daemon = True
            thread.start()
            
            messages.success(request, f"Job {job.short_id} resumed successfully")
            return JsonResponse({'success': True})
        else:
            return JsonResponse({'success': False, 'error': 'Only paused jobs can be resumed'}, status=400)
            
    except Exception as e:
        messages.error(request, f"Error resuming job: {str(e)}")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def job_list(request):
    """List all jobs with management options"""
    jobs = DownloadJob.objects.all().order_by('-created_at')
    
    context = {
        'jobs': jobs,
    }
    
    return render(request, 'batch_downloader/job_list.html', context)


def natural_sort_key(text):
    """
    Convert a string into a list of string and number chunks.
    "z23a" -> ["z", 23, "a"]
    """
    import re
    def convert(match):
        return int(match.group()) if match.group().isdigit() else match.group().lower()
    
    return [convert(match) for match in re.finditer(r'\d+|\D+', text)]


def get_sorted_products(job, sort_by='natural', sort_order='asc'):
    """Get products sorted by different methods"""
    products = ProductBatch.objects.filter(job=job).prefetch_related('images')
    
    if sort_by == 'natural':
        # Natural sorting (handles PROD1, PROD2, PROD10 correctly)
        products_list = list(products)
        products_list.sort(key=lambda x: natural_sort_key(x.product_number), reverse=(sort_order == 'desc'))
        return products_list
    elif sort_by == 'alphabetical':
        # Simple alphabetical sorting
        order_by = '-product_number' if sort_order == 'desc' else 'product_number'
        return products.order_by(order_by)
    elif sort_by == 'created':
        # Sort by creation time
        order_by = '-created_at' if sort_order == 'desc' else 'created_at'
        return products.order_by(order_by)
    elif sort_by == 'status':
        # Sort by status
        order_by = '-status' if sort_order == 'desc' else 'status'
        return products.order_by(order_by)
    else:
        # Default to natural sorting
        products_list = list(products)
        products_list.sort(key=lambda x: natural_sort_key(x.product_number))
        return products_list


def deduplicate_images_per_product(image_rows):
    """
    Remove duplicate image URLs per product, keeping only the first occurrence.
    Returns (deduplicated_rows, warnings_list)
    """
    seen_combinations = set()
    deduplicated_rows = []
    warnings = []
    
    for row in image_rows:
        combination = (row.product_number, row.image_src)
        if combination not in seen_combinations:
            seen_combinations.add(combination)
            deduplicated_rows.append(row)
        else:
            warnings.append(f"Duplicate image removed: {row.image_src} for product {row.product_number}")
    
    removed_count = len(image_rows) - len(deduplicated_rows)
    if removed_count > 0:
        warnings.insert(0, f"Removed {removed_count} duplicate image(s)")
    
    return deduplicated_rows, warnings
