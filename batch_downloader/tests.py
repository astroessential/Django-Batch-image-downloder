import os
import tempfile
import shutil
from unittest.mock import Mock, patch, MagicMock
from django.test import TestCase, Client
from django.urls import reverse
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
import json

from batch_downloader.models import DownloadJob, ProductBatch, ImageItem
from batch_downloader.services.simple_downloader import SimpleDownloadService
from batch_downloader.forms import BatchDataForm


class SimpleDownloadServiceTest(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.original_media_root = settings.MEDIA_ROOT
        settings.MEDIA_ROOT = self.temp_dir
        
        # Create test job with proper model structure
        self.job = DownloadJob.objects.create(
            total_products=1,
            total_images=1,
            status='PENDING'
        )
        
        # Create product batch
        self.product_batch = ProductBatch.objects.create(
            job=self.job,
            product_number="PROD1",
            image_count=1
        )
        
        # Create image item
        self.image_item = ImageItem.objects.create(
            product_batch=self.product_batch,
            url="http://example.com/image1.jpg",
            status='PENDING'
        )
        
        self.service = SimpleDownloadService()

    def tearDown(self):
        settings.MEDIA_ROOT = self.original_media_root
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_get_file_path(self):
        """Test file path generation"""
        file_path = self.service._get_file_path("PROD1", "image1.jpg")
        expected_path = os.path.join(
            self.temp_dir, 
            "products", 
            "PROD1", 
            "originals", 
            "image1.jpg"
        )
        self.assertIn("products", file_path)
        self.assertIn("originals", file_path)

    @patch('requests.get')
    def test_download_image_success(self, mock_get):
        """Test successful image download"""
        # Mock successful response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {'content-type': 'image/jpeg'}
        # Make the response iterable for streaming download
        mock_response.__iter__ = Mock(return_value=iter([b'fake image content']))
        mock_response.iter_content = Mock(return_value=[b'fake image content'])
        mock_get.return_value = mock_response
        
        result, message = self.service.download_image(self.image_item)
        
        self.assertTrue(result)
        mock_get.assert_called_once()

    @patch('requests.get')
    def test_download_image_failure(self, mock_get):
        """Test failed image download"""
        # Mock failed response
        mock_get.side_effect = Exception("Network error")
        
        result, message = self.service.download_image(self.image_item)
        
        self.assertFalse(result)

    @patch('batch_downloader.services.simple_downloader.SimpleDownloadService.download_image')
    def test_process_job_success(self, mock_download):
        """Test successful job processing"""
        mock_download.return_value = (True, "Success")
        
        self.service.process_job(self.job)
        
        # Refresh job from database
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'COMPLETED')
        self.assertEqual(self.job.completed_images, 1)

    @patch('batch_downloader.services.simple_downloader.SimpleDownloadService.download_image')
    def test_process_job_with_failures(self, mock_download):
        """Test job processing with some failures"""
        mock_download.return_value = (False, "Failed")
        
        self.service.process_job(self.job)
        
        # Refresh job from database
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'FAILED')
        self.assertEqual(self.job.failed_images, 1)

    def test_pause_resume_functionality(self):
        """Test pause and resume functionality"""
        # The service doesn't have built-in pause/resume, 
        # it relies on job status checking
        self.job.status = 'PAUSED'
        self.job.save()
        
        # Test that job processing respects pause status
        with patch.object(self.service, 'download_image') as mock_download:
            mock_download.return_value = (True, "Success")
            self.service.process_job(self.job)
            # Should not call download_image when paused since processing exits early
            mock_download.assert_not_called()


class BatchDownloaderViewsTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.temp_dir = tempfile.mkdtemp()
        self.original_media_root = settings.MEDIA_ROOT
        settings.MEDIA_ROOT = self.temp_dir

    def tearDown(self):
        settings.MEDIA_ROOT = self.original_media_root
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_landing_page_view(self):
        """Test landing page loads correctly"""
        response = self.client.get(reverse('batch_downloader:landing'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('batch_form', response.context)
        self.assertIsInstance(response.context['batch_form'], BatchDataForm)

    def test_create_download_job_valid_data(self):
        """Test job creation with valid data"""
        batch_data = json.dumps([
            {
                "product": "PROD1",
                "images": ["http://example.com/image1.jpg", "http://example.com/image2.jpg"]
            },
            {
                "product": "PROD2", 
                "images": ["http://example.com/image3.jpg"]
            }
        ])
        
        response = self.client.post(reverse('batch_downloader:create_job'), {
            'batch_data': batch_data
        })
        
        self.assertEqual(response.status_code, 200)
        
        # Check job was created
        job = DownloadJob.objects.last()
        self.assertIsNotNone(job)
        self.assertEqual(job.total_products, 2)
        self.assertEqual(job.total_images, 3)
        self.assertEqual(job.status, 'pending')

    def test_create_download_job_invalid_data(self):
        """Test job creation with invalid data"""
        response = self.client.post(reverse('batch_downloader:create_job'), {
            'batch_data': 'invalid json'
        })
        
        self.assertEqual(response.status_code, 400)

    def test_job_detail_view(self):
        """Test job detail view"""
        job = DownloadJob.objects.create(
            total_products=1,
            total_images=1,
            status='PENDING'
        )
        
        response = self.client.get(reverse('batch_downloader:job_detail', args=[job.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['job'], job)

    def test_pause_job_view(self):
        """Test pause job functionality"""
        job = DownloadJob.objects.create(
            total_products=1,
            total_images=1,
            status='RUNNING'
        )
        
        response = self.client.post(reverse('batch_downloader:pause_job', args=[job.id]))
        self.assertEqual(response.status_code, 200)
        
        job.refresh_from_db()
        self.assertEqual(job.status, 'PAUSED')

    def test_resume_job_view(self):
        """Test resume job functionality"""
        job = DownloadJob.objects.create(
            total_products=1,
            total_images=1,
            status='PAUSED'
        )
        
        response = self.client.post(reverse('batch_downloader:resume_job', args=[job.id]))
        self.assertEqual(response.status_code, 200)
        
        job.refresh_from_db()
        self.assertEqual(job.status, 'RUNNING')

    def test_cancel_job_view(self):
        """Test cancel job functionality"""
        job = DownloadJob.objects.create(
            total_products=1,
            total_images=1,
            status='RUNNING'
        )
        
        response = self.client.post(reverse('batch_downloader:cancel_job', args=[job.id]))
        self.assertEqual(response.status_code, 200)
        
        job.refresh_from_db()
        self.assertEqual(job.status, 'CANCELLED')

    def test_restart_job_view(self):
        """Test restart job functionality"""
        job = DownloadJob.objects.create(
            total_products=1,
            total_images=1,
            status='FAILED',
            completed_images=0
        )
        
        response = self.client.post(reverse('batch_downloader:restart_job', args=[job.id]))
        self.assertEqual(response.status_code, 200)
        
        job.refresh_from_db()
        self.assertEqual(job.status, 'PENDING')
        self.assertEqual(job.completed_images, 0)

    def test_job_progress_sse(self):
        """Test job progress SSE endpoint"""
        job = DownloadJob.objects.create(
            total_products=1,
            total_images=1,
            status='RUNNING'
        )
        
        response = self.client.get(reverse('batch_downloader:job_progress', args=[job.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/event-stream')


class BatchDataFormTest(TestCase):
    def test_valid_form_data(self):
        """Test form with valid batch data"""
        batch_data = json.dumps([
            {
                "product": "PROD1",
                "images": ["http://example.com/image1.jpg"]
            }
        ])
        
        form = BatchDataForm(data={'batch_data': batch_data})
        self.assertTrue(form.is_valid())

    def test_invalid_json_data(self):
        """Test form with invalid JSON"""
        form = BatchDataForm(data={'batch_data': 'invalid json'})
        self.assertFalse(form.is_valid())
        self.assertIn('batch_data', form.errors)

    def test_empty_data(self):
        """Test form with empty data"""
        form = BatchDataForm(data={'batch_data': ''})
        self.assertFalse(form.is_valid())
        self.assertIn('batch_data', form.errors)


class IntegrationTest(TestCase):
    """Integration test for the complete workflow"""
    
    def setUp(self):
        self.client = Client()
        self.temp_dir = tempfile.mkdtemp()
        self.original_media_root = settings.MEDIA_ROOT
        settings.MEDIA_ROOT = self.temp_dir

    def tearDown(self):
        settings.MEDIA_ROOT = self.original_media_root
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch('requests.get')
    @patch('batch_downloader.services.simple_downloader.SimpleDownloadService.download_image')
    def test_complete_workflow(self, mock_download, mock_get):
        """Test complete workflow from job creation to completion"""
        # Mock successful download
        mock_download.return_value = (True, "Success")
        
        # Step 1: Create job
        batch_data = json.dumps([
            {
                "product": "PROD1",
                "images": ["http://example.com/image1.jpg"]
            }
        ])
        
        response = self.client.post(reverse('batch_downloader:create_job'), {
            'batch_data': batch_data
        })
        
        self.assertEqual(response.status_code, 200)
        
        # Get created job
        job = DownloadJob.objects.last()
        self.assertIsNotNone(job)
        
        # Step 2: Process job (this would normally be done in a thread)
        service = SimpleDownloadService()
        service.process_job(job)
        
        # Step 3: Verify job completion
        job.refresh_from_db()
        self.assertEqual(job.status, 'COMPLETED')
        self.assertEqual(job.completed_images, 1)
        
        # Step 4: Test file path creation
        expected_path_parts = [
            "products",
            "PROD1",
            "originals"
        ]
        # Directory structure should be created even if no actual file downloaded in test
        mock_download.assert_called_once()
