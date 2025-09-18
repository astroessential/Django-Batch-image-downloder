"""
Debug test to investigate job processing failure
"""
import os
import json
from django.test import TestCase
from unittest.mock import patch, Mock
from batch_downloader.models import DownloadJob, ProductBatch, ImageItem
from batch_downloader.services.simple_downloader import SimpleDownloadService


class JobFailureDebugTest(TestCase):
    """Specific test to debug the job failure issue"""
    
    def test_job_processing_debug(self):
        """Debug the specific job failure case"""
        # Create job with proper model structure
        job = DownloadJob.objects.create(
            total_products=1,
            total_images=1,
            status='PENDING'
        )
        
        # Create product batch
        product_batch = ProductBatch.objects.create(
            job=job,
            product_number="PROD1",
            image_count=1
        )
        
        # Create image item
        image_item = ImageItem.objects.create(
            product_batch=product_batch,
            url="http://example.com/test-image.jpg",
            status='PENDING'
        )
        
        print(f"Created job: {job.id}")
        print(f"Created product batch: {product_batch.product_number}")
        print(f"Created image item: {image_item.url}")
        
        # Create service instance
        service = SimpleDownloadService()
        print(f"Service created")
        
        # Test file path generation
        file_path = service._get_file_path("PROD1", "test-image.jpg")
        print(f"Generated file path: {file_path}")
        
        # Check if path contains 'products' and 'originals'
        self.assertIn("products", file_path)
        self.assertIn("originals", file_path)
        
        # Mock download_image to simulate success
        with patch.object(service, 'download_image', return_value=(True, "Success")) as mock_download:
            # Process the job
            print("Starting job processing...")
            service.process_job(job)
            
            # Check if download_image was called
            self.assertTrue(mock_download.called)
            print(f"download_image called {mock_download.call_count} times")
            
            # Check job status
            job.refresh_from_db()
            print(f"Final job status: {job.status}")
            print(f"Completed images: {job.completed_images}")
            print(f"Total images: {job.total_images}")
            
            # Assertions
            self.assertEqual(job.status, 'COMPLETED')
            self.assertEqual(job.completed_images, 1)

    def test_real_download_scenario(self):
        """Test with a real download scenario but mocked network call"""
        # Create job with proper model structure
        job = DownloadJob.objects.create(
            total_products=1,
            total_images=1,
            status='PENDING'
        )
        
        product_batch = ProductBatch.objects.create(
            job=job,
            product_number="PROD1",
            image_count=1
        )
        
        image_item = ImageItem.objects.create(
            product_batch=product_batch,
            url="https://httpbin.org/image/jpeg",
            status='PENDING'
        )
        
        print(f"Initial state - Job: {job.status}, Product: {product_batch.status}, Image: {image_item.status}")
        
        service = SimpleDownloadService()
        
        # Mock requests.get for successful download
        with patch('requests.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.headers = {'content-type': 'image/jpeg'}
            # Make the response iterable for streaming download
            mock_response.__iter__ = Mock(return_value=iter([b'fake jpeg content']))
            mock_response.iter_content = Mock(return_value=[b'fake jpeg content'])
            mock_get.return_value = mock_response
            
            # Test actual download_image method first
            result, message = service.download_image(image_item)
            print(f"Download result: {result}, message: {message}")
            
            # Check image item after download
            image_item.refresh_from_db()
            print(f"After download - Image status: {image_item.status}, size: {image_item.size_bytes}")
            
            # Reset for process_job test
            job.status = 'PENDING'
            job.completed_images = 0
            job.save()
            
            product_batch.downloaded_count = 0
            product_batch.status = 'PENDING' 
            product_batch.save()
            
            image_item.status = 'PENDING'
            image_item.save()
            
            # Test process_job
            print(f"Starting process_job...")
            service.process_job(job)
            
            # Refresh all objects
            job.refresh_from_db()
            product_batch.refresh_from_db()
            image_item.refresh_from_db()
            
            print(f"Final state:")
            print(f"  Job: {job.status}, completed: {job.completed_images}, total: {job.total_images}")
            print(f"  Product: {product_batch.status}, downloaded: {product_batch.downloaded_count}, total: {product_batch.image_count}")
            print(f"  Image: {image_item.status}, size: {image_item.size_bytes}")

    def test_file_path_generation(self):
        """Test file path generation specifically"""
        service = SimpleDownloadService()
        
        # Test file path with job context
        file_path = service._get_file_path("PROD1", "test.jpg")
        print(f"File path: {file_path}")
        
        # Check expected structure (note: slugify converts to lowercase)
        expected_parts = ["products", "prod1", "originals", "test.jpg"]  # slugified PROD1 becomes prod1
        for part in expected_parts:
            self.assertIn(part, file_path)
            print(f"✓ Found '{part}' in path")

    def test_model_relationships(self):
        """Test model relationships are working correctly"""
        # Create job
        job = DownloadJob.objects.create(
            total_products=2,
            total_images=3,
            status='PENDING'
        )
        
        # Create first product batch
        product1 = ProductBatch.objects.create(
            job=job,
            product_number="PROD1",
            image_count=2
        )
        
        # Create second product batch
        product2 = ProductBatch.objects.create(
            job=job,
            product_number="PROD2", 
            image_count=1
        )
        
        # Create image items
        ImageItem.objects.create(
            product_batch=product1,
            url="http://example.com/image1.jpg",
            status='PENDING'
        )
        
        ImageItem.objects.create(
            product_batch=product1,
            url="http://example.com/image2.jpg",
            status='PENDING'
        )
        
        ImageItem.objects.create(
            product_batch=product2,
            url="http://example.com/image3.jpg",
            status='PENDING'
        )
        
        # Test relationships
        self.assertEqual(job.products.count(), 2)
        self.assertEqual(product1.images.count(), 2)
        self.assertEqual(product2.images.count(), 1)
        
        # Test querying
        all_images = ImageItem.objects.filter(product_batch__job=job)
        self.assertEqual(all_images.count(), 3)
        
        print(f"Job has {job.products.count()} products")
        print(f"Total images in job: {all_images.count()}")

    def test_service_initialization(self):
        """Test service initialization"""
        service = SimpleDownloadService()
        
        print(f"Service timeout: {service.timeout}")
        print(f"Service initialized successfully")
        
        # Test file path generation without specific job context
        file_path = service._get_file_path("TEST", "image.jpg")
        print(f"Generated file path: {file_path}")
        
        self.assertIsInstance(file_path, str)
        self.assertIn("test", file_path)  # slugified TEST becomes test
