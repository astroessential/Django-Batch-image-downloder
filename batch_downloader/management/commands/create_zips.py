from django.core.management.base import BaseCommand
from batch_downloader.models import DownloadJob, ProductBatch
from batch_downloader.services.zip_service import zip_service


class Command(BaseCommand):
    help = 'Create ZIP files for completed jobs that don\'t have them yet'

    def add_arguments(self, parser):
        parser.add_argument(
            '--job-id',
            type=str,
            help='Specific job ID to process (optional)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without actually doing it',
        )

    def handle(self, *args, **options):
        job_id = options.get('job_id')
        dry_run = options.get('dry_run', False)
        
        if job_id:
            # Process specific job
            try:
                job = DownloadJob.objects.get(id=job_id)
                jobs = [job]
                self.stdout.write(f"Processing specific job: {job.id}")
            except DownloadJob.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f'Job {job_id} not found')
                )
                return
        else:
            # Process all completed jobs
            jobs = DownloadJob.objects.filter(status='COMPLETED')
            self.stdout.write(f"Processing {jobs.count()} completed jobs")

        total_processed = 0
        total_zips_created = 0

        for job in jobs:
            self.stdout.write(f"\n--- Processing Job {job.short_id} ---")
            
            # Get product batches that have completed images but no ZIP
            product_batches = ProductBatch.objects.filter(
                job=job,
                zip_ready=False  # Only process those without ZIP
            ).prefetch_related('images')
            
            if not product_batches.exists():
                self.stdout.write("  No product batches need ZIP creation")
                continue
            
            total_processed += 1
            job_zips_created = 0
            
            for product_batch in product_batches:
                # Check if product has any completed images
                completed_images = product_batch.images.filter(status='DONE')
                
                if completed_images.exists():
                    self.stdout.write(
                        f"  Product {product_batch.product_number}: "
                        f"{completed_images.count()} completed images"
                    )
                    
                    if not dry_run:
                        # Create ZIP file
                        try:
                            zip_service.update_product_zip_status(product_batch)
                            product_batch.refresh_from_db()
                            
                            if product_batch.zip_ready:
                                self.stdout.write(
                                    self.style.SUCCESS(
                                        f"    ✓ ZIP created: {product_batch.zip_path}"
                                    )
                                )
                                job_zips_created += 1
                                total_zips_created += 1
                            else:
                                self.stdout.write(
                                    self.style.WARNING(
                                        f"    ! ZIP creation may have failed"
                                    )
                                )
                        except Exception as e:
                            self.stdout.write(
                                self.style.ERROR(
                                    f"    ✗ Error creating ZIP: {e}"
                                )
                            )
                    else:
                        self.stdout.write("    (dry-run: would create ZIP)")
                        job_zips_created += 1
                else:
                    self.stdout.write(
                        f"  Product {product_batch.product_number}: "
                        "No completed images, skipping"
                    )
            
            if job_zips_created > 0:
                self.stdout.write(
                    f"  Created {job_zips_created} ZIP file(s) for this job"
                )

        # Summary
        self.stdout.write(f"\n--- Summary ---")
        self.stdout.write(f"Jobs processed: {total_processed}")
        self.stdout.write(f"ZIP files created: {total_zips_created}")
        
        if dry_run:
            self.stdout.write(
                self.style.WARNING("This was a dry run - no actual changes made")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("ZIP creation completed!")
            )
