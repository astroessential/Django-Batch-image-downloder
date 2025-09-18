import os
import hashlib
import mimetypes
from pathlib import Path
from urllib.parse import urlparse, unquote
from typing import Optional, Tuple
import asyncio

import httpx
from django.conf import settings
from django.core.files.storage import default_storage
from slugify import slugify

from ..models import ImageItem


class DownloadService:
    """Service for downloading images with retry logic and checksums"""
    
    def __init__(self):
        self.max_size = settings.MAX_IMAGE_SIZE_MB * 1024 * 1024  # Convert to bytes
        self.connect_timeout = settings.HTTP_CONNECT_TIMEOUT
        self.read_timeout = settings.HTTP_READ_TIMEOUT
        self.write_timeout = settings.HTTP_WRITE_TIMEOUT
        
        # Valid image MIME types
        self.valid_image_types = {
            'image/jpeg', 'image/jpg', 'image/png', 'image/webp', 
            'image/avif', 'image/gif', 'image/bmp', 'image/tiff'
        }
    
    async def download_image(self, image_item: ImageItem) -> Tuple[bool, str]:
        """
        Download a single image item
        Returns: (success: bool, message: str)
        """
        try:
            # Update status to downloading
            image_item.status = 'DOWNLOADING'
            image_item.save(update_fields=['status'])
            
            # Check if file already exists by checksum
            existing_file = await self._check_existing_file(image_item)
            if existing_file:
                return True, f"File already exists: {existing_file}"
            
            # Download the image
            success, message = await self._perform_download(image_item)
            
            if success:
                image_item.status = 'DONE'
            else:
                image_item.status = 'FAILED'
                image_item.error_message = message
            
            image_item.save()
            return success, message
            
        except Exception as e:
            image_item.status = 'FAILED'
            image_item.error_message = str(e)
            image_item.save()
            return False, str(e)
    
    async def _check_existing_file(self, image_item: ImageItem) -> Optional[str]:
        """Check if file already exists by checksum"""
        if not image_item.checksum_sha256:
            return None
        
        # Check for existing files with same checksum in the same product
        existing_items = ImageItem.objects.filter(
            product_batch=image_item.product_batch,
            checksum_sha256=image_item.checksum_sha256,
            status='DONE'
        ).exclude(id=image_item.id)
        
        if existing_items.exists():
            existing_item = existing_items.first()
            # Copy file info from existing item
            image_item.filename = existing_item.filename
            image_item.file_path = existing_item.file_path
            image_item.content_type = existing_item.content_type
            image_item.size_bytes = existing_item.size_bytes
            image_item.status = 'SKIPPED'
            image_item.save()
            return existing_item.file_path
        
        return None
    
    async def _perform_download(self, image_item: ImageItem) -> Tuple[bool, str]:
        """Perform the actual download with retries"""
        url = image_item.url
        max_retries = 3
        
        timeout = httpx.Timeout(
            connect=self.connect_timeout,
            read=self.read_timeout,
            write=self.write_timeout
        )
        
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(
                    timeout=timeout,
                    follow_redirects=True,
                    limits=httpx.Limits(max_redirects=5)
                ) as client:
                    
                    async with client.stream('GET', url) as response:
                        if response.status_code != 200:
                            if attempt == max_retries - 1:
                                return False, f"HTTP {response.status_code}: {response.reason_phrase}"
                            await asyncio.sleep(2 ** attempt)  # Exponential backoff
                            continue
                        
                        # Validate content type
                        content_type = response.headers.get('content-type', '').split(';')[0].lower()
                        if content_type and content_type not in self.valid_image_types:
                            return False, f"Invalid content type: {content_type}"
                        
                        # Check content length
                        content_length = response.headers.get('content-length')
                        if content_length and int(content_length) > self.max_size:
                            return False, f"File too large: {content_length} bytes"
                        
                        # Generate filename and path
                        filename = self._generate_filename(url, content_type)
                        file_path = self._get_file_path(image_item.product_batch.product_number, filename)
                        
                        # Ensure directory exists
                        os.makedirs(os.path.dirname(file_path), exist_ok=True)
                        
                        # Download and save file
                        total_size = 0
                        sha256_hash = hashlib.sha256()
                        
                        with open(file_path, 'wb') as f:
                            async for chunk in response.aiter_bytes(chunk_size=8192):
                                total_size += len(chunk)
                                
                                # Check size limit
                                if total_size > self.max_size:
                                    f.close()
                                    os.remove(file_path)
                                    return False, f"File exceeds size limit: {total_size} bytes"
                                
                                f.write(chunk)
                                sha256_hash.update(chunk)
                        
                        # Update image item
                        image_item.filename = filename
                        image_item.file_path = file_path
                        image_item.content_type = content_type
                        image_item.size_bytes = total_size
                        image_item.checksum_sha256 = sha256_hash.hexdigest()
                        
                        return True, f"Downloaded {filename} ({total_size} bytes)"
                        
            except httpx.TimeoutException:
                if attempt == max_retries - 1:
                    return False, "Download timeout"
                await asyncio.sleep(2 ** attempt)
                continue
                
            except httpx.RequestError as e:
                if attempt == max_retries - 1:
                    return False, f"Request error: {str(e)}"
                await asyncio.sleep(2 ** attempt)
                continue
                
            except Exception as e:
                return False, f"Unexpected error: {str(e)}"
        
        return False, "Max retries exceeded"
    
    def _generate_filename(self, url: str, content_type: str) -> str:
        """Generate a safe filename from URL and content type"""
        parsed = urlparse(url)
        path = unquote(parsed.path)
        
        # Try to get filename from URL
        if path and '/' in path:
            filename = path.split('/')[-1]
            if '.' in filename:
                name, ext = os.path.splitext(filename)
                name = slugify(name, max_length=50)
                if name and ext:
                    return f"{name}{ext.lower()}"
        
        # Fallback: generate from content type
        ext = mimetypes.guess_extension(content_type) or '.jpg'
        return f"image_{abs(hash(url)) % 10000}{ext}"
    
    def _get_file_path(self, product_number: str, filename: str) -> str:
        """Get full file path for storing image"""
        # Clean product number for filesystem
        clean_product = slugify(product_number)
        
        if settings.MEDIA_BACKEND == 's3':
            # For S3, return the key path
            return f"products/{clean_product}/originals/{filename}"
        else:
            # For local storage
            return os.path.join(
                settings.MEDIA_ROOT,
                'products',
                clean_product,
                'originals',
                filename
            )
    
    def get_product_directory(self, product_number: str) -> str:
        """Get the directory path for a product"""
        clean_product = slugify(product_number)
        
        if settings.MEDIA_BACKEND == 's3':
            return f"products/{clean_product}/"
        else:
            return os.path.join(settings.MEDIA_ROOT, 'products', clean_product)


# Global instance
download_service = DownloadService()
