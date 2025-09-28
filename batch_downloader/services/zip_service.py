import os
import zipfile
from typing import Iterator, List
from pathlib import Path
from io import BytesIO

from django.conf import settings
from django.http import StreamingHttpResponse, HttpResponse
from django.core.files.storage import default_storage

from ..models import ProductBatch, ImageItem


class ZipService:
    """Service for creating and streaming ZIP files"""
    
    def stream_product_zip(self, product_batch: ProductBatch) -> HttpResponse:
        """Create a ZIP file for a single product"""
        # Create in-memory ZIP
        buffer = BytesIO()
        
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Get all completed images for this product
            images = ImageItem.objects.filter(
                product_batch=product_batch,
                status='DONE'
            ).exclude(file_path='')
            
            # Track used filenames to avoid duplicates
            used_names = set()
            
            for image in images:
                if self._file_exists(image.file_path):
                    # Generate unique archive name
                    archive_name = self._get_unique_archive_name(image.filename, used_names)
                    used_names.add(archive_name)
                    
                    if settings.MEDIA_BACKEND == 's3':
                        # For S3, read the file content
                        with default_storage.open(image.file_path, 'rb') as f:
                            zf.writestr(archive_name, f.read())
                    else:
                        # For local files, add directly
                        zf.write(image.file_path, archive_name)
        
        buffer.seek(0)
        
        # Create response with proper filename
        filename = f"{product_batch.product_number}.zip"
        response = HttpResponse(buffer.read(), content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    
    def stream_all_products_zip(self, job) -> HttpResponse:
        """Create a ZIP file containing all products"""
        # Create in-memory ZIP
        buffer = BytesIO()
        
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            # Get all product batches with completed images
            product_batches = ProductBatch.objects.filter(
                job=job
            ).prefetch_related('images')
            
            # Track used filenames globally to avoid duplicates across products
            used_names = set()
            
            for product_batch in product_batches:
                completed_images = product_batch.images.filter(status='DONE').exclude(file_path='')
                
                if completed_images.exists():
                    # Create a folder for each product
                    product_folder = f"{product_batch.product_number}/"
                    
                    # Add all images for this product
                    for image in completed_images:
                        if self._file_exists(image.file_path):
                            # Generate unique filename within the product folder
                            base_archive_name = f"{product_folder}{image.filename}"
                            archive_name = self._get_unique_archive_name(base_archive_name, used_names)
                            used_names.add(archive_name)
                            
                            if settings.MEDIA_BACKEND == 's3':
                                with default_storage.open(image.file_path, 'rb') as f:
                                    zf.writestr(archive_name, f.read())
                            else:
                                zf.write(image.file_path, archive_name)
        
        buffer.seek(0)
        
        filename = f"Job_{job.short_id}_All_Products.zip"
        response = HttpResponse(buffer.read(), content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    
    def _file_exists(self, file_path: str) -> bool:
        """Check if file exists (works for both local and S3)"""
        if settings.MEDIA_BACKEND == 's3':
            return default_storage.exists(file_path)
        else:
            return os.path.isfile(file_path)
    
    def _get_unique_archive_name(self, filename: str, used_names: set) -> str:
        """Generate a unique archive name by appending a counter if needed"""
        if filename not in used_names:
            return filename
        
        # Split filename into name and extension
        name, ext = os.path.splitext(filename)
        
        # Try adding a counter until we find a unique name
        counter = 1
        while True:
            unique_name = f"{name}_{counter}{ext}"
            if unique_name not in used_names:
                return unique_name
            counter += 1
    
    def update_product_zip_status(self, product_batch: ProductBatch):
        """Update the ZIP ready status for a product and create the ZIP file"""
        completed_images = ImageItem.objects.filter(
            product_batch=product_batch,
            status='DONE'
        ).exclude(file_path='')
        
        if completed_images.exists():
            # Create the actual ZIP file
            self._create_product_zip_file(product_batch, completed_images)
        else:
            product_batch.zip_ready = False
            product_batch.zip_size = 0
            product_batch.save(update_fields=['zip_ready', 'zip_size'])
    
    def _create_product_zip_file(self, product_batch: ProductBatch, completed_images):
        """Create the actual ZIP file for a product"""
        try:
            # Create ZIP file path using job ID structure
            zip_filename = f"{product_batch.product_number}.zip"
            zip_path = os.path.join(
                settings.MEDIA_ROOT,
                'zips',
                str(product_batch.job.id),
                zip_filename
            )
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(zip_path), exist_ok=True)
            
            # Track used filenames to avoid duplicates
            used_names = set()
            
            total_size = 0
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                for image in completed_images:
                    if os.path.exists(image.file_path):
                        # Generate unique archive name
                        arcname = self._get_unique_archive_name(image.filename, used_names)
                        used_names.add(arcname)
                        
                        zip_file.write(image.file_path, arcname)
                        total_size += os.path.getsize(image.file_path)
            
            # Update product batch
            product_batch.zip_path = zip_path
            product_batch.zip_size = total_size
            product_batch.zip_ready = True
            product_batch.save(update_fields=['zip_path', 'zip_size', 'zip_ready'])
            
        except Exception as e:
            print(f"Error creating ZIP for product {product_batch.product_number}: {e}")
            product_batch.zip_ready = False
            product_batch.save(update_fields=['zip_ready'])


# Global instance
zip_service = ZipService()
