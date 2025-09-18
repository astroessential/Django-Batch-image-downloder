import re
from typing import List, Tuple
from pydantic import BaseModel, Field, validator, HttpUrl
from urllib.parse import urlparse, unquote
import pandas as pd


class ImageRow(BaseModel):
    product_number: str = Field(..., min_length=1, max_length=64)
    image_src: HttpUrl = Field(..., description="Absolute HTTP/HTTPS URL")
    
    @validator('product_number')
    def validate_product_number(cls, v):
        # Trim whitespace
        v = v.strip()
        # Allow alphanumerics, dash, underscore
        if not re.match(r'^[a-zA-Z0-9_-]+$', v):
            raise ValueError('Product number can only contain alphanumerics, dash, and underscore')
        return v
    
    @validator('image_src')
    def validate_image_url(cls, v):
        # Convert to string for validation
        url_str = str(v)
        
        # Check length
        if len(url_str) > 2048:
            raise ValueError('URL length cannot exceed 2048 characters')
        
        # Must be HTTP/HTTPS
        if not url_str.startswith(('http://', 'https://')):
            raise ValueError('Only HTTP/HTTPS URLs are allowed')
        
        # Reject data URIs
        if url_str.startswith('data:'):
            raise ValueError('Data URIs are not allowed')
        
        return v


class BatchValidationResult(BaseModel):
    valid_rows: List[ImageRow] = []
    errors: List[str] = []
    warnings: List[str] = []
    total_rows: int = 0
    valid_rows_count: int = 0
    
    @property
    def is_valid(self) -> bool:
        return len(self.valid_rows) > 0 and len(self.errors) == 0


def normalize_header(header: str) -> str:
    """Normalize column headers to match expected format"""
    header = header.strip().lower()
    
    # Product number variations
    if header in ['product number', 'product', 'productid', 'product_id', 'product_number']:
        return 'product_number'
    
    # Image URL variations
    if header in ['image src', 'image url', 'image_src', 'image_url', 'imageurl', 'imagesrc']:
        return 'image_src'
    
    return header


def validate_csv_data(data: List[List[str]]) -> BatchValidationResult:
    """Validate CSV data from frontend"""
    result = BatchValidationResult()
    
    if not data or len(data) < 2:  # Need header + at least one row
        result.errors.append("At least one data row is required")
        return result
    
    # Get headers
    headers = [normalize_header(h) for h in data[0]]
    
    # Check required columns
    if 'product_number' not in headers:
        result.errors.append("Missing required column: Product Number")
    if 'image_src' not in headers:
        result.errors.append("Missing required column: Image Src")
    
    if result.errors:
        return result
    
    # Get column indices
    product_idx = headers.index('product_number')
    image_idx = headers.index('image_src')
    
    # Validate data rows
    result.total_rows = len(data) - 1  # Exclude header
    
    for row_num, row in enumerate(data[1:], 2):  # Start from row 2 (after header)
        if len(row) <= max(product_idx, image_idx):
            result.errors.append(f"Row {row_num}: Insufficient columns")
            continue
        
        product_number = row[product_idx].strip() if len(row) > product_idx else ""
        image_src = row[image_idx].strip() if len(row) > image_idx else ""
        
        if not product_number:
            result.errors.append(f"Row {row_num}: Product Number is required")
            continue
        
        if not image_src:
            result.errors.append(f"Row {row_num}: Image Src is required")
            continue
        
        try:
            image_row = ImageRow(product_number=product_number, image_src=image_src)
            result.valid_rows.append(image_row)
        except Exception as e:
            result.errors.append(f"Row {row_num}: {str(e)}")
    
    result.valid_rows_count = len(result.valid_rows)
    
    # Add warnings for large datasets
    if result.valid_rows_count > 1000:
        result.warnings.append(f"Large dataset ({result.valid_rows_count} rows) - processing may take time")
    
    return result


def validate_pandas_dataframe(df: pd.DataFrame) -> BatchValidationResult:
    """Validate pandas DataFrame from CSV upload"""
    result = BatchValidationResult()
    
    if df.empty:
        result.errors.append("CSV file is empty")
        return result
    
    # Normalize column names
    df.columns = [normalize_header(col) for col in df.columns]
    
    # Check required columns
    if 'product_number' not in df.columns:
        result.errors.append("Missing required column: Product Number")
    if 'image_src' not in df.columns:
        result.errors.append("Missing required column: Image Src")
    
    if result.errors:
        return result
    
    # Drop rows with missing required values
    original_count = len(df)
    df = df.dropna(subset=['product_number', 'image_src'])
    dropped_count = original_count - len(df)
    
    if dropped_count > 0:
        result.warnings.append(f"Dropped {dropped_count} rows with missing required values")
    
    result.total_rows = len(df)
    
    # Validate each row
    for idx, row in df.iterrows():
        try:
            image_row = ImageRow(
                product_number=str(row['product_number']).strip(),
                image_src=str(row['image_src']).strip()
            )
            result.valid_rows.append(image_row)
        except Exception as e:
            result.errors.append(f"Row {idx + 2}: {str(e)}")  # +2 for 1-based + header
    
    result.valid_rows_count = len(result.valid_rows)
    
    if result.valid_rows_count > 1000:
        result.warnings.append(f"Large dataset ({result.valid_rows_count} rows) - processing may take time")
    
    return result


def deduplicate_images_per_product(rows: List[ImageRow]) -> Tuple[List[ImageRow], List[str]]:
    """Remove duplicate URLs per product, return cleaned list and warnings"""
    product_urls = {}
    deduplicated = []
    warnings = []
    
    for row in rows:
        product = row.product_number
        url = str(row.image_src)
        
        if product not in product_urls:
            product_urls[product] = set()
        
        if url not in product_urls[product]:
            product_urls[product].add(url)
            deduplicated.append(row)
        else:
            warnings.append(f"Duplicate URL for product {product}: {url}")
    
    return deduplicated, warnings
