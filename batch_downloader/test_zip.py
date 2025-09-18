"""
Test ZIP file creation functionality
"""
import os
import tempfile
import zipfile
from django.test import TestCase
from unittest.mock import patch, Mock
from batch_downloader.models import DownloadJob, ProductBatch, ImageItem
from batch_downloader.services.simple_downloader import SimpleDownloadService
from batch_downloader.services.zip_service import zip_service
from django.conf import settings


class ZipCreationTest(TestCase):
    """Test ZIP file creation after job completion"""
    
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.original_media_root = settings.MEDIA_ROOT
        settings.MEDIA_ROOT = self.temp_dir
        
        # Create job with proper model structure
        self.job = DownloadJob.objects.create(
            total_products=2,
            total_images=3,
            status='PENDING'
        )
        
        # Create first product batch
        self.product1 = ProductBatch.objects.create(
            job=self.job,
            product_number="PROD1",
            image_count=2
        )
        
        # Create second product batch  
        self.product2 = ProductBatch.objects.create(
            job=self.job,
            product_number="PROD2",
            image_count=1
        )
        
        # Create image items
        self.image1 = ImageItem.objects.create(
            product_batch=self.product1,
            url="http://example.com/image1.jpg",
            status='PENDING'
        )
        
        self.image2 = ImageItem.objects.create(
            product_batch=self.product1,
            url="http://example.com/image2.jpg", 
            status='PENDING'
        )
        
        self.image3 = ImageItem.objects.create(
            product_batch=self.product2,
            url="http://example.com/image3.jpg",
            status='PENDING'
        )
        
        self.service = SimpleDownloadService()

    def tearDown(self):
        settings.MEDIA_ROOT = self.original_media_root
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def create_mock_image_file(self, file_path, content=b'fake image content'):
        """Create a mock image file at the specified path"""
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'wb') as f:
            f.write(content)

    @patch('batch_downloader.services.simple_downloader.SimpleDownloadService.download_image')
    def test_zip_creation_after_job_completion(self, mock_download):
        """Test that ZIP files are created after job completion"""
        
        def mock_download_side_effect(image_item):
            # Simulate successful download
            image_item.status = 'DONE'
            image_item.filename = os.path.basename(image_item.url)
            image_item.size_bytes = 1024
            
            # Create mock file path
            file_path = self.service._get_file_path(
                image_item.product_batch.product_number, 
                image_item.filename
            )
            image_item.file_path = file_path
            
            # Create actual mock file
            self.create_mock_image_file(file_path)
            
            image_item.save()
            return True, "Success"
        
        mock_download.side_effect = mock_download_side_effect
        
        # Process the job
        self.service.process_job(self.job)
        
        # Refresh objects from database
        self.job.refresh_from_db()
        self.product1.refresh_from_db()
        self.product2.refresh_from_db()
        
        # Check job completion
        self.assertEqual(self.job.status, 'COMPLETED')
        self.assertEqual(self.job.completed_images, 3)
        
        # Check that products are completed
        self.assertEqual(self.product1.status, 'COMPLETED')
        self.assertEqual(self.product2.status, 'COMPLETED')
        
        # Check that ZIP files are ready
        self.assertTrue(self.product1.zip_ready)
        self.assertTrue(self.product2.zip_ready)
        
        # Check that ZIP files exist
        self.assertTrue(os.path.exists(self.product1.zip_path))
        self.assertTrue(os.path.exists(self.product2.zip_path))
        
        # Check ZIP file contents
        with zipfile.ZipFile(self.product1.zip_path, 'r') as zf:
            files_in_zip = zf.namelist()
            self.assertEqual(len(files_in_zip), 2)  # 2 images in PROD1
            self.assertIn('image1.jpg', files_in_zip)
            self.assertIn('image2.jpg', files_in_zip)
        
        with zipfile.ZipFile(self.product2.zip_path, 'r') as zf:
            files_in_zip = zf.namelist()
            self.assertEqual(len(files_in_zip), 1)  # 1 image in PROD2
            self.assertIn('image3.jpg', files_in_zip)
        
        print(f"✓ PROD1 ZIP ready: {self.product1.zip_ready}, path: {self.product1.zip_path}")
        print(f"✓ PROD2 ZIP ready: {self.product2.zip_ready}, path: {self.product2.zip_path}")

    def test_job_has_ready_zips_property(self):
        """Test that job.has_ready_zips works correctly"""
        # Initially no ready zips
        self.assertFalse(self.job.has_ready_zips)
        
        # Mark one product as having ready zip
        self.product1.zip_ready = True
        self.product1.save()
        
        # Now should have ready zips
        self.assertTrue(self.job.has_ready_zips)

    def test_zip_service_stream_product_zip(self):
        """Test that ZIP streaming works"""
        # Setup completed images with actual files
        self.image1.status = 'DONE'
        self.image1.filename = 'image1.jpg'
        self.image1.file_path = os.path.join(self.temp_dir, 'test', 'image1.jpg')
        self.create_mock_image_file(self.image1.file_path, b'fake image 1 content')
        self.image1.save()
        
        self.image2.status = 'DONE' 
        self.image2.filename = 'image2.jpg'
        self.image2.file_path = os.path.join(self.temp_dir, 'test', 'image2.jpg')
        self.create_mock_image_file(self.image2.file_path, b'fake image 2 content')
        self.image2.save()
        
        # Test ZIP streaming
        response = zip_service.stream_product_zip(self.product1)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        self.assertIn('PROD1.zip', response['Content-Disposition'])
        
        # Verify ZIP content
        import io
        zip_data = io.BytesIO(response.content)
        with zipfile.ZipFile(zip_data, 'r') as zf:
            files_in_zip = zf.namelist()
            self.assertEqual(len(files_in_zip), 2)
            self.assertIn('image1.jpg', files_in_zip)
            self.assertIn('image2.jpg', files_in_zip)

    def test_zip_service_stream_all_products_zip(self):
        """Test that full job ZIP streaming works"""
        # Setup all images as completed
        for image_item in [self.image1, self.image2, self.image3]:
            image_item.status = 'DONE'
            image_item.filename = os.path.basename(image_item.url)
            image_item.file_path = os.path.join(
                self.temp_dir, 'test', image_item.filename
            )
            self.create_mock_image_file(
                image_item.file_path, 
                f'fake content for {image_item.filename}'.encode()
            )
            image_item.save()
        
        # Test full job ZIP streaming
        response = zip_service.stream_all_products_zip(self.job)
        
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/zip')
        self.assertIn(f'Job_{self.job.short_id}_All_Products.zip', response['Content-Disposition'])
        
        # Verify ZIP content includes product folders
        import io
        zip_data = io.BytesIO(response.content)
        with zipfile.ZipFile(zip_data, 'r') as zf:
            files_in_zip = zf.namelist()
            self.assertEqual(len(files_in_zip), 3)
            
            # Check that files are organized by product
            prod1_files = [f for f in files_in_zip if f.startswith('PROD1/')]
            prod2_files = [f for f in files_in_zip if f.startswith('PROD2/')]
            
            self.assertEqual(len(prod1_files), 2)
            self.assertEqual(len(prod2_files), 1)
            
            self.assertIn('PROD1/image1.jpg', files_in_zip)
            self.assertIn('PROD1/image2.jpg', files_in_zip)  
            self.assertIn('PROD2/image3.jpg', files_in_zip)
