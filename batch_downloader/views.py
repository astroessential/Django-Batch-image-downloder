import json
import os
import re
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
from .services.zip_service import zip_service


def natural_sort_key(text):
    """Convert a string into a list of string and number chunks."""
    def convert(match):
        return int(match.group()) if match.group().isdigit() else match.group().lower()
    return [convert(match) for match in re.finditer(r'\d+|\D+', text)]


def get_sorted_products(job, sort_by='natural', sort_order='asc'):
    """Get products sorted by different methods"""
    products = ProductBatch.objects.filter(job=job).prefetch_related('images')
    
    if sort_by == 'natural':
        products_list = list(products)
        products_list.sort(key=lambda x: natural_sort_key(x.product_number), reverse=(sort_order == 'desc'))
        return products_list
    elif sort_by == 'alphabetical':
        order_by = '-product_number' if sort_order == 'desc' else 'product_number'
        return products.order_by(order_by)
    elif sort_by == 'created':
        order_by = '-created_at' if sort_order == 'desc' else 'created_at'
        return products.order_by(order_by)
    elif sort_by == 'status':
        order_by = '-status' if sort_order == 'desc' else 'status'
        return products.order_by(order_by)
    else:
        products_list = list(products)
        products_list.sort(key=lambda x: natural_sort_key(x.product_number))
        return products_list


def deduplicate_images_per_product(image_rows):
    """Remove duplicate image URLs per product."""
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


def create_download_job(image_rows, user=None):
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
        from .services.simple_downloader import SimpleDownloadService
        downloader = SimpleDownloadService()
        
        def process_job():
            try:
                downloader.process_job(job)
            except Exception as e:
                print(f"Job processing error: {e}")
                job.status = 'FAILED'
                job.save()
        
        import threading
        thread = threading.Thread(target=process_job)
        thread.daemon = True
        thread.start()
        
        return job


def landing_page(request):
    """Main landing page"""
    if request.method == 'POST':
        # Redirect to prevent duplicate submissions
        job = create_download_job(request.POST.get('batch_data', ''))
        return redirect('batch_downloader:job_detail', job_id=job.id)
    
    # Create form instance for the template
    batch_form = BatchDataForm()
    
    return render(request, 'batch_downloader/landing.html', {
        'batch_form': batch_form
    })


@require_POST
def create_job(request):
    """Create a new download job via AJAX"""
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
    sort_by = request.GET.get('sort_by', 'natural')
    sort_order = request.GET.get('sort_order', 'asc')
    
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
    """Download ZIP file for a specific product"""
    job = get_object_or_404(DownloadJob, id=job_id)
    product = get_object_or_404(ProductBatch, job=job, product_number=product_number)
    
    if not product.zip_ready:
        raise Http404("ZIP file not ready")
    
    return zip_service.stream_product_zip(product)


def download_job_zip(request, job_id):
    """Download ZIP file containing all products for a job"""
    job = get_object_or_404(DownloadJob, id=job_id)
    return zip_service.stream_all_products_zip(job)


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
                'final_update': True
            }
            
            # Send final update and close
            yield f"data: {json.dumps(progress_data)}\n\n"
            return
        
        # For active jobs, stream updates
        max_updates = 300
        update_count = 0
        
        while update_count < max_updates:
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


@require_POST
def delete_job(request, job_id):
    """Delete a job and all its data"""
    import shutil
    from django.conf import settings
    
    job = get_object_or_404(DownloadJob, id=job_id)
    
    try:
        # Cancel any running tasks
        if job.status in ['PENDING', 'RUNNING', 'PAUSED']:
            job.status = 'CANCELLED'
            job.save()
        
        # Delete physical files
        job_media_path = os.path.join(settings.MEDIA_ROOT, 'products', str(job.id))
        if os.path.exists(job_media_path):
            shutil.rmtree(job_media_path)
        
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
            from .services.simple_downloader import SimpleDownloadService
            downloader = SimpleDownloadService()
            
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


# Additional utility views
@csrf_exempt
def upload_csv(request):
    """Handle CSV file upload"""
    if request.method == 'POST':
        form = CSVUploadForm(request.POST, request.FILES)
        if form.is_valid():
            csv_data = form.process_csv()
            return JsonResponse({'success': True, 'data': csv_data})
        else:
            return JsonResponse({'success': False, 'errors': form.errors})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})


def sample_csv(request):
    """Download sample CSV file"""
    import csv
    from django.http import HttpResponse
    
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="sample_data.csv"'
    
    writer = csv.writer(response)
    writer.writerow(['Product Number', 'Image Src'])
    writer.writerow(['PROD001', 'https://picsum.photos/800/600?random=1'])
    writer.writerow(['PROD001', 'https://picsum.photos/800/600?random=2'])
    writer.writerow(['PROD002', 'https://picsum.photos/800/600?random=3'])
    
    return response


def system_check(request):
    """System health check view"""
    from django.conf import settings
    
    checks = {
        'database': True,  # If we got here, database is working
        'media_write': False,
    }
    
    # Check media directory write permissions
    try:
        test_file = os.path.join(settings.MEDIA_ROOT, 'test_write.txt')
        with open(test_file, 'w') as f:
            f.write('test')
        os.remove(test_file)
        checks['media_write'] = True
    except:
        pass
    
    return JsonResponse({
        'status': 'ok' if all(checks.values()) else 'error',
        'checks': checks
    })
