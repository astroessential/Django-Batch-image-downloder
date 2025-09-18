import uuid
from django.db import models
from django.contrib.auth.models import User
from django.urls import reverse


class DownloadJob(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('RUNNING', 'Running'),
        ('PAUSED', 'Paused'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('CANCELLED', 'Cancelled'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    
    total_products = models.IntegerField(default=0)
    total_images = models.IntegerField(default=0)
    completed_images = models.IntegerField(default=0)
    failed_images = models.IntegerField(default=0)
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"Job {self.id.hex[:8]} - {self.status}"
    
    @property
    def short_id(self):
        return self.id.hex[:8]
    
    @property
    def progress_percentage(self):
        if self.total_images == 0:
            return 0
        return int((self.completed_images / self.total_images) * 100)
    
    @property
    def has_ready_zips(self):
        """Check if there are any product batches with ready ZIP files"""
        return self.products.filter(zip_ready=True).exists()
    
    def get_absolute_url(self):
        return reverse('batch_downloader:job_detail', kwargs={'job_id': self.id})


class ProductBatch(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('RUNNING', 'Running'),
        ('DONE', 'Done'),
        ('FAILED', 'Failed'),
        ('PARTIAL', 'Partial'),
    ]
    
    job = models.ForeignKey(DownloadJob, on_delete=models.CASCADE, related_name='products')
    product_number = models.CharField(max_length=64)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    
    image_count = models.IntegerField(default=0)
    downloaded_count = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)
    bytes_downloaded = models.BigIntegerField(default=0)
    
    zip_ready = models.BooleanField(default=False)
    zip_size = models.BigIntegerField(default=0)
    zip_path = models.CharField(max_length=500, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ['job', 'product_number']
        indexes = [
            models.Index(fields=['job', 'status']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        return f"Product {self.product_number} - {self.status}"
    
    @property
    def progress_percentage(self):
        if self.image_count == 0:
            return 0
        return int((self.downloaded_count / self.image_count) * 100)
    
    @property
    def bytes_downloaded_mb(self):
        return round(self.bytes_downloaded / (1024 * 1024), 2)
    
    @property
    def failed_images(self):
        """Get failed images for this product batch"""
        return self.images.filter(status='FAILED')


class ImageItem(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('DOWNLOADING', 'Downloading'),
        ('DONE', 'Done'),
        ('FAILED', 'Failed'),
        ('SKIPPED', 'Skipped'),
    ]
    
    product_batch = models.ForeignKey(ProductBatch, on_delete=models.CASCADE, related_name='images')
    url = models.URLField(max_length=2048)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    
    filename = models.CharField(max_length=255, blank=True)
    file_path = models.CharField(max_length=500, blank=True)
    content_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.BigIntegerField(default=0)
    
    error_message = models.TextField(blank=True)
    checksum_sha256 = models.CharField(max_length=64, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ['product_batch', 'url']
        indexes = [
            models.Index(fields=['product_batch', 'status']),
            models.Index(fields=['status']),
            models.Index(fields=['checksum_sha256']),
        ]
    
    def __str__(self):
        return f"Image {self.filename or self.url} - {self.status}"
    
    @property
    def size_mb(self):
        return round(self.size_bytes / (1024 * 1024), 2)
