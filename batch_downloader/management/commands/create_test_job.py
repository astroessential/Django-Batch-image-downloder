from django.core.management.base import BaseCommand
from batch_downloader.views import create_download_job
from batch_downloader.validators import ImageRow


class Command(BaseCommand):
    help = 'Create a test download job with sample data'

    def handle(self, *args, **options):
        # Create test data
        test_rows = [
            ImageRow(
                product_number="TEST001",
                image_src="https://picsum.photos/300/300"
            ),
            ImageRow(
                product_number="TEST001", 
                image_src="https://picsum.photos/400/400"
            ),
            ImageRow(
                product_number="TEST002",
                image_src="https://picsum.photos/350/350"
            ),
            ImageRow(
                product_number="TEST002",
                image_src="https://picsum.photos/500/500"
            ),
            ImageRow(
                product_number="TEST003",
                image_src="https://picsum.photos/600/400"
            ),
        ]

        # Create the job
        job = create_download_job(test_rows)
        
        self.stdout.write(
            self.style.SUCCESS(
                f'Successfully created test job with ID: {job.id}\n'
                f'Total products: {job.total_products}\n'
                f'Total images: {job.total_images}\n'
                f'Status: {job.status}\n'
                f'View at: http://127.0.0.1:8000/jobs/{job.id}/'
            )
        )
