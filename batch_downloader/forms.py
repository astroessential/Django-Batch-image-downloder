from django import forms
from django.core.files.uploadedfile import UploadedFile
import pandas as pd
import json

from .validators import validate_csv_data, validate_pandas_dataframe


class CSVUploadForm(forms.Form):
    """Form for uploading CSV files"""
    csv_file = forms.FileField(
        label="CSV File",
        help_text="Upload a CSV file with Product Number and Image Src columns",
        widget=forms.FileInput(attrs={
            'class': 'form-control',
            'accept': '.csv'
        })
    )
    
    def clean_csv_file(self):
        csv_file = self.cleaned_data['csv_file']
        
        if not csv_file.name.endswith('.csv'):
            raise forms.ValidationError("Please upload a CSV file")
        
        if csv_file.size > 10 * 1024 * 1024:  # 10MB limit
            raise forms.ValidationError("File size must be less than 10MB")
        
        try:
            # Try to read the CSV
            df = pd.read_csv(csv_file)
            
            # Validate the data
            validation_result = validate_pandas_dataframe(df)
            
            if not validation_result.is_valid:
                error_msg = "CSV validation failed:\n" + "\n".join(validation_result.errors)
                raise forms.ValidationError(error_msg)
            
            # Store validation result for later use
            self.validation_result = validation_result
            
        except pd.errors.EmptyDataError:
            raise forms.ValidationError("The CSV file is empty")
        except pd.errors.ParserError as e:
            raise forms.ValidationError(f"CSV parsing error: {str(e)}")
        except Exception as e:
            raise forms.ValidationError(f"Error processing CSV: {str(e)}")
        
        # Reset file pointer
        csv_file.seek(0)
        return csv_file


class BatchDataForm(forms.Form):
    """Form for handling JSON data from frontend table"""
    batch_data = forms.CharField(
        widget=forms.HiddenInput(),
        required=True
    )
    
    def clean_batch_data(self):
        data_str = self.cleaned_data['batch_data']
        
        try:
            # Parse JSON data
            data = json.loads(data_str)
            
            if not isinstance(data, list):
                raise forms.ValidationError("Invalid data format")
            
            # Validate the data
            validation_result = validate_csv_data(data)
            
            if not validation_result.is_valid:
                error_msg = "Data validation failed:\n" + "\n".join(validation_result.errors)
                raise forms.ValidationError(error_msg)
            
            # Store validation result for later use
            self.validation_result = validation_result
            
            return data
            
        except json.JSONDecodeError:
            raise forms.ValidationError("Invalid JSON data")
        except Exception as e:
            raise forms.ValidationError(f"Error processing data: {str(e)}")


class JobFilterForm(forms.Form):
    """Form for filtering jobs in admin/list views"""
    status = forms.ChoiceField(
        choices=[('', 'All')] + [
            ('PENDING', 'Pending'),
            ('RUNNING', 'Running'),
            ('COMPLETED', 'Completed'),
            ('FAILED', 'Failed'),
            ('CANCELLED', 'Cancelled'),
        ],
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
