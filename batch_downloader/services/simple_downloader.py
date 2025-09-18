import os
import hashlib
import mimetypes
from urllib.parse import urlparse, unquote
from typing import Tuple
import time

import requests
from django.conf import settings
from slugify import slugify

from ..models import ImageItem


class SimpleDownloadService:
    """Very simple download service using requests library"""
    
    def __init__(self):
        self.max_size = 50 * 1024 * 1024  # 50MB limit
        self.timeout = 30  # 30 seconds timeout
        self._current_job_id = None
        
    def set_job_id(self, job_id: str):
        """Set the current job ID for file organization"""
        self._current_job_id = job_id
        
    def download_image(self, image_item: ImageItem) -> Tuple[bool, str]:
        """Download a single image item"""
        try:
            # Update status
            image_item.status = 'DOWNLOADING'
            image_item.save(update_fields=['status'])
            
            url = image_item.url
            
            # Make request with timeout
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(url, headers=headers, timeout=self.timeout, stream=True)
            response.raise_for_status()
            
            # Check content type
            content_type = response.headers.get('content-type', '').split(';')[0].lower()
            if content_type and not content_type.startswith('image/'):
                return False, f"Invalid content type: {content_type}"
            
            # Generate filename
            filename = self._generate_filename(url, content_type)
            file_path = self._get_file_path(image_item.product_batch.product_number, filename)
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            # Download file
            total_size = 0
            sha256_hash = hashlib.sha256()
            
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        total_size += len(chunk)
                        
                        # Check size limit
                        if total_size > self.max_size:
                            f.close()
                            os.remove(file_path)
                            return False, f"File too large: {total_size} bytes"
                        
                        f.write(chunk)
                        sha256_hash.update(chunk)
            
            # Update image item
            image_item.filename = filename
            image_item.file_path = file_path
            image_item.content_type = content_type
            image_item.size_bytes = total_size
            image_item.checksum_sha256 = sha256_hash.hexdigest()
            image_item.status = 'DONE'
            image_item.save()
            
            return True, f"Downloaded {filename} ({total_size} bytes)"
            
        except requests.exceptions.Timeout:
            image_item.status = 'FAILED'
            image_item.error_message = "Download timeout"
            image_item.save()
            return False, "Download timeout"
            
        except requests.exceptions.RequestException as e:
            image_item.status = 'FAILED'
            image_item.error_message = str(e)
            image_item.save()
            return False, f"Request error: {str(e)}"
            
        except Exception as e:
            image_item.status = 'FAILED'
            image_item.error_message = str(e)
            image_item.save()
            return False, f"Unexpected error: {str(e)}"
    
    def _generate_filename(self, url: str, content_type: str) -> str:
        """Generate a safe filename from URL and content type"""
        parsed = urlparse(url)
        path = unquote(parsed.path)
        
        # Try to get filename from URL
        if path and '/' in path:
            filename = path.split('/')[-1]
            if '.' in filename and len(filename) < 100:
                return filename
        
        # Fallback: generate from content type
        ext = mimetypes.guess_extension(content_type) or '.jpg'
        return f"image_{abs(hash(url)) % 10000}{ext}"
    
    def _get_file_path(self, product_number: str, filename: str) -> str:
        """Get full file path for storing image"""
        # Clean product number for filesystem
        clean_product = slugify(product_number)
        
        # Use job ID for better organization and tracking
        job_id = getattr(self, '_current_job_id', None)
        
        if job_id:
            return os.path.join(
                settings.MEDIA_ROOT,
                'products',
                str(job_id),
                clean_product,
                'originals',
                filename
            )
        else:
            # Fallback when no job ID available
            return os.path.join(
                settings.MEDIA_ROOT,
                'products',
                clean_product,
                'originals',
                filename
            )
    
    def process_job(self, job):
        """Process a complete job, handling pause/resume functionality"""
        from ..models import ProductBatch
        
        try:
            # Check initial job status - don't override PAUSED or CANCELLED
            if job.status not in ['PAUSED', 'CANCELLED']:
                job.status = 'RUNNING'
                job.save()
            
            self.set_job_id(str(job.id))
            
            # Get all products for this job, ordered by product_number
            products = ProductBatch.objects.filter(job=job).order_by('product_number')
            
            for product in products:
                # Check if job was paused or cancelled
                job.refresh_from_db()
                if job.status in ['PAUSED', 'CANCELLED']:
                    print(f"Job {job.id} was {job.status.lower()}, stopping processing")
                    return  # Exit early instead of break to avoid status update
                
                # Process images for this product
                images = product.images.filter(status__in=['PENDING', 'FAILED'])
                
                for image_item in images:
                    # Check pause/cancel status again
                    job.refresh_from_db()
                    if job.status in ['PAUSED', 'CANCELLED']:
                        print(f"Job {job.id} was {job.status.lower()}, stopping processing")
                        return
                    
                    # Download the image
                    success, message = self.download_image(image_item)
                    if success:
                        product.downloaded_count += 1
                        product.bytes_downloaded += image_item.size_bytes
                        job.completed_images += 1
                    else:
                        job.failed_images += 1
                    
                    # Save progress (don't try to set progress_percentage as it's a property)
                    product.save()
                    job.save()
                    
                    # Small delay to allow pause checks
                    time.sleep(0.1)
                
                # Update product status
                if product.downloaded_count >= product.image_count:
                    product.status = 'COMPLETED'
                elif product.downloaded_count > 0:
                    product.status = 'PARTIAL'
                else:
                    product.status = 'FAILED'
                product.save()
                
                # Update ZIP status for completed/partial products
                if product.status in ['COMPLETED', 'PARTIAL']:
                    from .zip_service import zip_service
                    zip_service.update_product_zip_status(product)
            
            # Update final job status
            job.refresh_from_db()
            if job.status == 'RUNNING':  # Only update if still running (not paused)
                if job.completed_images >= job.total_images:
                    job.status = 'COMPLETED'
                elif job.completed_images > 0:
                    job.status = 'COMPLETED'  # Consider partial downloads as completed
                else:
                    job.status = 'FAILED'
                job.save()
                
        except Exception as e:
            print(f"Error processing job {job.id}: {e}")
            job.status = 'FAILED'
            job.save()


# Global instance
simple_download_service = SimpleDownloadService()

# Alias for backward compatibility
SimpleDownloader = SimpleDownloadService
