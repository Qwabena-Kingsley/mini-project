from django.urls import path
from . import views

urlpatterns = [

    # Devices
    path('devices/',                views.device_list,   name='device-list'),
    path('devices/<uuid:device_id>/', views.device_detail, name='device-detail'),

    # Readings
    path('readings/',                               views.ingest_reading,  name='ingest-reading'),
    path('readings/<uuid:device_id>/latest/',       views.latest_reading,  name='latest-reading'),
    path('readings/<uuid:device_id>/history/',      views.reading_history, name='reading-history'),

    # Insights
    path('insights/<uuid:device_id>/latest/',       views.latest_insight,  name='latest-insight'),
    path('insights/<uuid:device_id>/history/',      views.insight_history, name='insight-history'),

    # Anomalies
    path('anomalies/<uuid:device_id>/',             views.anomaly_list,    name='anomaly-list'),

    # Daily summary
    path('summary/<uuid:device_id>/',               views.daily_summary_list, name='daily-summary'),

    # Dashboard — one call for everything
    path('dashboard/<uuid:device_id>/',             views.dashboard,       name='dashboard'),

    #qr code
     path('devices/<uuid:device_id>/qrcode/', views.device_qrcode, name='device-qrcode'),  # ← new

]