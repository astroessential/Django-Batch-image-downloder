from django.urls import path, include
from . import views

app_name = 'batch_downloader'

urlpatterns = [
    # Main pages
    path('', views.landing_page, name='landing'),
    path('jobs/', views.job_list, name='job_list'),
    path('jobs/<uuid:job_id>/', views.job_detail, name='job_detail'),
    
    # Job creation
    path('upload-csv/', views.upload_csv, name='upload_csv'),
    path('create-job/', views.create_job, name='create_job'),
    
    # Job management
    path('jobs/<uuid:job_id>/delete/', views.delete_job, name='delete_job'),
    path('jobs/<uuid:job_id>/restart/', views.restart_job, name='restart_job'),
    path('jobs/<uuid:job_id>/cancel/', views.cancel_job, name='cancel_job'),
    path('jobs/<uuid:job_id>/pause/', views.pause_job, name='pause_job'),
    path('jobs/<uuid:job_id>/resume/', views.resume_job, name='resume_job'),
    
    # Real-time updates (SSE)
    path('jobs/<uuid:job_id>/stream/', views.job_progress_stream, name='job_progress_stream'),
    
    # Downloads
    path('jobs/<uuid:job_id>/product/<str:product_number>/zip/', 
         views.download_product_zip, name='download_product_zip'),
    path('jobs/<uuid:job_id>/zip/', 
         views.download_job_zip, name='download_job_zip'),
    
    # Utilities
    path('sample-csv/', views.sample_csv, name='sample_csv'),
    path('system-check/', views.system_check, name='system_check'),
    path('health/', views.health_check, name='health_check'),
    
    # EventStream for real-time updates (disabled for now)
    # path('events/', include(eventstream_urls)),
]
