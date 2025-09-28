# Batch Image Downloader

A production-ready Django web application for downloading images in batches with Excel-style input, per-product ZIP files, and massive-scale reliability.

## Features

- **Excel-style Input**: Paste data directly from Excel/Google Sheets or upload CSV files
- **Concurrent Downloads**: Efficient image downloading with retry logic and rate limiting
- **Per-Product Organization**: Images grouped by product number with individual ZIP downloads
- **Real-time Progress**: WebSocket-based progress tracking for jobs and individual products
- **Massive Scale**: Handles 1000+ images with Celery background processing
- **Duplicate Detection**: SHA-256 checksums prevent duplicate downloads
- **Error Handling**: Comprehensive error reporting and retry mechanisms
- **Multiple Storage**: Local filesystem or S3-compatible storage

## Quick Start

### Prerequisites

- Python 3.11+
- Redis server
- Virtual environment (recommended)

### Installation

1. **Clone and setup environment**:
```bash
git clone <repository-url>
cd Image-Batch-Downloder
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

2. **Install dependencies**:
```bash
pip install -r requirements.txt
```

3. **Setup environment variables**:
```bash
cp .env.example .env
# Edit .env with your settings
```

4. **Setup database**:
```bash
python manage.py migrate
python manage.py createsuperuser
```

5. **Start Redis** (required for Celery):
```bash
# Option 1: Docker
docker run -d -p 6379:6379 redis:7-alpine

# Option 2: Local installation
redis-server
```

6. **Start the application**:
```bash
# Terminal 1: Django development server
python manage.py runserver

# Terminal 2: Celery worker
celery -A myproject worker --loglevel=info

# Terminal 3: Celery beat (optional, for scheduled tasks)
celery -A myproject beat --loglevel=info
```

7. **Access the application**:
- Main app: http://localhost:8000/
- Admin interface: http://localhost:8000/admin/
- System check: http://localhost:8000/system-check/

## Usage

### Basic Workflow

1. **Input Data**: 
   - Visit the homepage and paste 2-column data (Product Number, Image Src)
   - Or upload a CSV file with the same columns

2. **Create Job**: 
   - Click "Analyze & Queue Downloads" to validate and start processing
   - Jobs run in the background via Celery workers

3. **Monitor Progress**: 
   - Real-time progress updates show overall job status
   - Individual product cards display download progress
   - Error messages are shown for failed downloads

4. **Download Results**:
   - Download individual product ZIP files
   - Download a master ZIP containing all products
   - Each ZIP includes a manifest with metadata

### Data Format

**CSV Headers** (case-insensitive):
- `Product Number` / `Product` / `ProductID`
- `Image Src` / `Image URL` / `ImageURL`

**Example CSV**:
```csv
Product Number,Image Src
PROD001,https://example.com/image1.jpg
PROD001,https://example.com/image2.jpg
PROD002,https://example.com/image3.jpg
```

### Validation Rules

- Product numbers: alphanumeric, dash, underscore only (max 64 chars)
- Image URLs: must be absolute HTTP/HTTPS URLs (max 2048 chars)
- File types: standard image formats (JPEG, PNG, WebP, AVIF, GIF)
- File size: configurable limit (default 50MB per image)
- Duplicates: automatically removed per product

## Configuration

### Environment Variables

```bash
# Django Settings
SECRET_KEY=your-secret-key
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Database
DATABASE_URL=sqlite:///db.sqlite3

# Celery & Redis
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/0

# Download Configuration
MAX_GLOBAL_CONCURRENCY=32          # Global download limit
MAX_PER_HOST_CONCURRENCY=4         # Per-host download limit
DOWNLOAD_QPS=20                    # Downloads per second
HTTP_CONNECT_TIMEOUT=5             # Connection timeout (seconds)
HTTP_READ_TIMEOUT=45               # Read timeout (seconds)
MAX_IMAGE_SIZE_MB=50               # Max file size (MB)

# Storage (local or S3)
MEDIA_BACKEND=local                # or 's3'
AWS_ACCESS_KEY_ID=                 # For S3
AWS_SECRET_ACCESS_KEY=             # For S3
AWS_STORAGE_BUCKET_NAME=           # For S3
AWS_S3_REGION_NAME=us-east-1       # For S3

# Celery Worker
CELERY_WORKER_CONCURRENCY=8        # Worker processes
```

### Storage Options

**Local Storage** (default):
- Files stored in `media/products/<product_number>/originals/`
- ZIP files generated on-demand

**S3 Storage**:
- Set `MEDIA_BACKEND=s3`
- Configure AWS credentials
- Files stored in bucket with same structure

### Performance Tuning

**For High Volume**:
```bash
# Increase worker concurrency
CELERY_WORKER_CONCURRENCY=16

# Increase global limits
MAX_GLOBAL_CONCURRENCY=64
MAX_PER_HOST_CONCURRENCY=8
DOWNLOAD_QPS=50

# Use multiple worker processes
celery -A myproject worker --concurrency=8 --prefork
```

**For Rate Limiting**:
```bash
# Conservative settings for shared hosting
MAX_GLOBAL_CONCURRENCY=8
MAX_PER_HOST_CONCURRENCY=2
DOWNLOAD_QPS=5
```

## Docker Deployment

### Development with Docker Compose

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### Production Deployment

1. **Update environment variables** for production
2. **Use PostgreSQL** instead of SQLite:
```bash
DATABASE_URL=postgresql://user:pass@host:5432/dbname
```

3. **Configure reverse proxy** (nginx/Apache)
4. **Set up SSL/TLS certificates**
5. **Configure monitoring** and logging

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Landing page with data input |
| `/jobs/` | GET | List recent jobs |
| `/jobs/<uuid>/` | GET | Job detail with progress |
| `/create-job/` | POST | Create new job from JSON data |
| `/upload-csv/` | POST | Create job from CSV upload |
| `/jobs/<uuid>/zip/` | GET | Download all products ZIP |
| `/jobs/<uuid>/product/<product>/zip/` | GET | Download product ZIP |
| `/events/job-<uuid>/` | GET | Server-sent events for job |
| `/sample-csv/` | GET | Download sample CSV file |
| `/system-check/` | GET | System health check |

## Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Frontend      │    │   Django App    │    │   Celery Worker │
│   (Browser)     │◄──►│   (Web Server)  │◄──►│   (Background)  │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         │              ┌─────────────────┐              │
         │              │     Redis       │              │
         └──────────────►│  (Message Bus)  │◄─────────────┘
                        └─────────────────┘
                                 │
                        ┌─────────────────┐
                        │   PostgreSQL    │
                        │   (Database)    │
                        └─────────────────┘
```

### Components

- **Django**: Web framework handling HTTP requests and responses
- **Celery**: Distributed task queue for background image downloads
- **Redis**: Message broker and result backend for Celery
- **PostgreSQL**: Primary database (SQLite for development)
- **EventStream**: Server-sent events for real-time updates
- **Storage**: Local filesystem or S3-compatible object storage

## Monitoring

### System Health

Visit `/system-check/` to verify:
- ✅ Database connectivity
- ✅ Redis connectivity  
- ✅ Celery worker status
- ✅ Media directory permissions

### Celery Monitoring

```bash
# Monitor workers
celery -A myproject inspect active

# Monitor queues
celery -A myproject inspect reserved

# Purge failed tasks
celery -A myproject purge
```

### Django Admin

Access `/admin/` to:
- View and filter download jobs
- Inspect product batches and image items
- Monitor system usage and errors

## Troubleshooting

### Common Issues

**Redis Connection Error**:
```bash
# Check Redis is running
redis-cli ping
# Should return "PONG"
```

**Celery Worker Not Starting**:
```bash
# Check for import errors
python -c "import myproject.celery"

# Start with verbose logging
celery -A myproject worker --loglevel=debug
```

**Download Failures**:
- Check URL accessibility and format
- Verify file size limits
- Review error messages in job detail page
- Check network connectivity and DNS resolution

**Memory Issues**:
- Reduce `MAX_GLOBAL_CONCURRENCY`
- Increase `worker_max_tasks_per_child` in Celery config
- Monitor memory usage during large jobs

### Performance Optimization

**Database**:
```sql
-- Add indexes for large datasets
CREATE INDEX ON batch_downloader_imageitem (created_at);
CREATE INDEX ON batch_downloader_productbatch (created_at);
```

**File System**:
- Use SSD storage for media files
- Consider object storage (S3) for large scale
- Monitor disk space usage

## Testing

### Sample Data

Download sample CSV: http://localhost:8000/sample-csv/

Test with varying scenarios:
- Small jobs (10-50 images)
- Medium jobs (100-500 images)  
- Large jobs (1000+ images)
- Mixed file sizes and formats
- Invalid URLs and error conditions

### Load Testing

```bash
# Create test data
python manage.py shell
>>> from batch_downloader.models import *
>>> # Create test jobs programmatically
```

## Security Considerations

- Rate limiting on job creation endpoints
- URL validation and allowlist patterns
- File type restrictions and content validation
- Secure filename generation
- CSRF protection on all forms
- Input sanitization and validation

## License

This project is licensed under the MIT License.

## Support

For issues and questions:
1. Check this README and configuration
2. Review Django and Celery logs
3. Use the `/system-check/` endpoint
4. Check the GitHub issues page
