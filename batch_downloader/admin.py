from django.contrib import admin
from .models import DownloadJob, ProductBatch, ImageItem


@admin.register(DownloadJob)
class DownloadJobAdmin(admin.ModelAdmin):
    list_display = ['short_id', 'status', 'total_products', 'total_images', 
                   'completed_images', 'failed_images', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['id', 'created_by__username']
    readonly_fields = ['id', 'short_id', 'created_at']
    ordering = ['-created_at']


@admin.register(ProductBatch)  
class ProductBatchAdmin(admin.ModelAdmin):
    list_display = ['product_number', 'job', 'status', 'image_count', 
                   'downloaded_count', 'failed_count', 'bytes_downloaded_mb', 'zip_ready']
    list_filter = ['status', 'zip_ready', 'created_at']
    search_fields = ['product_number', 'job__id']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at']


@admin.register(ImageItem)
class ImageItemAdmin(admin.ModelAdmin):
    list_display = ['filename', 'product_batch', 'status', 'url', 'size_mb', 'content_type']
    list_filter = ['status', 'content_type', 'created_at']
    search_fields = ['filename', 'url', 'product_batch__product_number']
    readonly_fields = ['created_at', 'updated_at', 'checksum_sha256']
    ordering = ['-created_at']
