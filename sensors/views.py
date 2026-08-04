import math
import logging
from datetime import timedelta, date
from django.http import HttpResponse
import requests
from django.conf import settings
from django.db import models as db_models
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Device, SensorReading, AiInsight, Anomaly, DailySummary
from .serializers import (
    DeviceSerializer,
    SensorReadingSerializer,
    SensorReadingCreateSerializer,
    AiInsightSerializer,
    AnomalySerializer,
    DailySummarySerializer,
    DashboardSerializer,
)

logger = logging.getLogger(__name__)


# =========================================
# HELPERS
# =========================================

def get_device_or_404(device_id):
    """
    Reusable helper — returns (device, None) on success
    or (None, Response) when device is not found.
    Use it in every view that needs a device lookup.
    """
    try:
        device = Device.objects.get(id=device_id)
        return device, None
    except Device.DoesNotExist:
        return None, Response(
            {"error": f"Device {device_id} not found."},
            status=status.HTTP_404_NOT_FOUND
        )


def run_anomaly_detection(reading):
    """
    Compare the latest reading against the rolling average
    of the last 10 readings for the same device.
    Flags spikes and drops that exceed defined thresholds.
    Saves an Anomaly row if one is detected.
    Returns the Anomaly instance or None.
    """
    TEMP_THRESHOLD     = 5.0   # °C  — flag if delta > this
    HUMIDITY_THRESHOLD = 15.0  # %   — flag if delta > this
    LIGHT_THRESHOLD    = 300.0 # lux — flag if delta > this

    # Get the last 10 readings before this one for a baseline
    recent = (
        SensorReading.objects
        .filter(device=reading.device)
        .exclude(id=reading.id)
        .order_by('-recorded_at')[:10]
    )

    if not recent.exists():
        return None  # not enough history to compare

    avg = recent.aggregate(
        avg_temp     = db_models.Avg('temperature'),
        avg_humidity = db_models.Avg('humidity'),
        avg_light    = db_models.Avg('light'),
    )

    avg_temp     = avg['avg_temp']
    avg_humidity = avg['avg_humidity']
    avg_light    = avg['avg_light']

    anomaly_type = None
    description  = None

    # Check temperature
    if abs(reading.temperature - avg_temp) > TEMP_THRESHOLD:
        if reading.temperature > avg_temp:
            anomaly_type = 'temp_spike'
            description  = (
                f"Temperature spiked to {reading.temperature}°C "
                f"(average was {avg_temp:.1f}°C)."
            )
        else:
            anomaly_type = 'temp_drop'
            description  = (
                f"Temperature dropped to {reading.temperature}°C "
                f"(average was {avg_temp:.1f}°C)."
            )

    # Check humidity
    elif abs(reading.humidity - avg_humidity) > HUMIDITY_THRESHOLD:
        if reading.humidity > avg_humidity:
            anomaly_type = 'humidity_jump'
            description  = (
                f"Humidity jumped to {reading.humidity}% "
                f"(average was {avg_humidity:.1f}%)."
            )
        else:
            anomaly_type = 'humidity_drop'
            description  = (
                f"Humidity dropped to {reading.humidity}% "
                f"(average was {avg_humidity:.1f}%)."
            )

    # Check light
    elif abs(reading.light - avg_light) > LIGHT_THRESHOLD:
        anomaly_type = 'light_spike'
        description  = (
            f"Light jumped to {reading.light} lux "
            f"(average was {avg_light:.1f} lux)."
        )

    if anomaly_type:
        anomaly = Anomaly.objects.create(
            device       = reading.device,
            reading      = reading,
            anomaly_type = anomaly_type,
            description  = description,
        )
        logger.info(f"Anomaly detected: {anomaly_type} on {reading.device.room_label}")
        return anomaly

    return None


def build_ai_prompt(device, reading):
    """
    Builds the prompt string sent to the LLM.
    Includes all computed properties so the AI has full context.
    """
    return f"""
You are an intelligent environmental monitoring assistant.

Device     : {device.room_label}
Room type  : {device.room_type or 'Not specified'}

Current sensor readings:
- Temperature  : {reading.temperature}°C
- Humidity     : {reading.humidity}%
- Light        : {reading.light} lux

Derived metrics:
- Dew point    : {reading.dew_point}°C
- Heat index   : {reading.heat_index}°C
- Comfort level: {reading.comfort_level}
- Light level  : {reading.light_level}
- Occupancy    : {reading.occupancy_hint}

Based on these readings:
1. Give a short, clear insight about the current room conditions (2-3 sentences).
2. Give one specific actionable suggestion to improve comfort or flag a concern.

Respond in this exact JSON format:
{{
  "insight": "...",
  "suggestion": "..."
}}
""".strip()


def call_openrouter(prompt):
    """
    Calls the OpenRouter API with the given prompt.
    Returns (insight_text, suggestion) on success
    or raises an exception on failure.
    """
    headers = {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type":  "application/json",
    }
    payload = {
        "model": settings.OPENROUTER_MODEL,
        "messages": [
            {
                "role":    "user",
                "content": prompt,
            }
        ],
        "response_format": {"type": "json_object"},
    }

    response = requests.post(
        f"{settings.OPENROUTER_BASE_URL}/chat/completions",
        headers = headers,
        json    = payload,
        timeout = 15,
    )
    response.raise_for_status()

    content = response.json()
    message = content['choices'][0]['message']['content']

    import json
    parsed     = json.loads(message)
    insight    = parsed.get('insight',    '')
    suggestion = parsed.get('suggestion', '')

    return insight, suggestion


def should_generate_insight(device):
    """
    Rate-limit LLM calls — only generate a new insight
    if the last one was more than 10 minutes ago.
    This prevents hammering the API on every reading.
    """
    last = (
        AiInsight.objects
        .filter(device=device)
        .order_by('-generated_at')
        .first()
    )
    if not last:
        return True  # no insights yet — generate one

    cutoff = timezone.now() - timedelta(minutes=10)
    return last.generated_at < cutoff


# =========================================
# DEVICE VIEWS
# =========================================

@api_view(['GET', 'POST'])
def device_list(request):
    """
    GET  /api/devices/       — list all devices
    POST /api/devices/       — register a new device
    """
    if request.method == 'GET':
        devices    = Device.objects.all()
        serializer = DeviceSerializer(devices, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    if request.method == 'POST':
        serializer = DeviceSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'PATCH', 'DELETE'])
def device_detail(request, device_id):
    """
    GET    /api/devices/<device_id>/   — get one device
    PATCH  /api/devices/<device_id>/   — update room_label or room_type
    DELETE /api/devices/<device_id>/   — delete device and all its data
    """
    device, err = get_device_or_404(device_id)
    if err:
        return err

    if request.method == 'GET':
        serializer = DeviceSerializer(device)
        return Response(serializer.data, status=status.HTTP_200_OK)

    if request.method == 'PATCH':
        serializer = DeviceSerializer(device, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    if request.method == 'DELETE':
        device.delete()
        return Response(
            {"message": "Device and all associated data deleted."},
            status=status.HTTP_204_NO_CONTENT
        )


# =========================================
# SENSOR READING VIEWS
# =========================================

@api_view(['POST'])
def ingest_reading(request):
    """
    POST /api/readings/

    Called by the ESP32 every N seconds.
    Expects: { mac_address, temperature, humidity, light }

    Pipeline:
    1. Validate the incoming data
    2. Resolve mac_address → Device
    3. Save the reading
    4. Run anomaly detection
    5. If rate-limit allows, call LLM and cache insight
    6. Return the saved reading with all computed properties
    """
    serializer = SensorReadingCreateSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    mac_address = serializer.validated_data.pop('mac_address')

    # Resolve device
    try:
        device = Device.objects.get(mac_address=mac_address)
    except Device.DoesNotExist:
        return Response(
            {"error": f"No device registered with MAC address {mac_address}."},
            status=status.HTTP_404_NOT_FOUND
        )

    # Save reading
    reading = SensorReading.objects.create(
        device      = device,
        temperature = serializer.validated_data['temperature'],
        humidity    = serializer.validated_data['humidity'],
        light       = serializer.validated_data['light'],
    )

    # Anomaly detection
    anomaly = run_anomaly_detection(reading)

    # LLM insight (rate-limited)
    insight = None
    if should_generate_insight(device):
        try:
            prompt          = build_ai_prompt(device, reading)
            insight_text, suggestion = call_openrouter(prompt)
            insight = AiInsight.objects.create(
                device       = device,
                insight_text = insight_text,
                suggestion   = suggestion,
            )
        except Exception as e:
            logger.error(f"OpenRouter call failed: {e}")
            # Do not crash the whole request if LLM fails
            # The reading is already saved — that is what matters

    # Build response
    response_data = {
        "reading": SensorReadingSerializer(reading).data,
        "anomaly_detected": anomaly is not None,
        "anomaly": AnomalySerializer(anomaly).data if anomaly else None,
        "insight_generated": insight is not None,
        "insight": AiInsightSerializer(insight).data if insight else None,
    }

    return Response(response_data, status=status.HTTP_201_CREATED)


@api_view(['GET'])
def reading_history(request, device_id):
    """
    GET /api/readings/<device_id>/history/?limit=50

    Returns the last N readings for a device.
    Default limit is 50, max is 500.
    """
    device, err = get_device_or_404(device_id)
    if err:
        return err

    limit = min(int(request.query_params.get('limit', 50)), 500)

    readings   = SensorReading.objects.filter(device=device).order_by('-recorded_at')[:limit]
    serializer = SensorReadingSerializer(readings, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['GET'])
def latest_reading(request, device_id):
    """
    GET /api/readings/<device_id>/latest/

    Returns only the most recent reading.
    Used for the live dashboard card.
    """
    device, err = get_device_or_404(device_id)
    if err:
        return err

    reading = (
        SensorReading.objects
        .filter(device=device)
        .order_by('-recorded_at')
        .first()
    )

    if not reading:
        return Response(
            {"error": "No readings found for this device."},
            status=status.HTTP_404_NOT_FOUND
        )

    serializer = SensorReadingSerializer(reading)
    return Response(serializer.data, status=status.HTTP_200_OK)


# =========================================
# AI INSIGHT VIEWS
# =========================================

@api_view(['GET'])
def latest_insight(request, device_id):
    """
    GET /api/insights/<device_id>/latest/

    Returns the most recently cached LLM insight.
    Frontend calls this on dashboard load — no LLM call happens here.
    """
    device, err = get_device_or_404(device_id)
    if err:
        return err

    insight = (
        AiInsight.objects
        .filter(device=device)
        .order_by('-generated_at')
        .first()
    )

    if not insight:
        return Response(
            {"error": "No insights generated yet for this device."},
            status=status.HTTP_404_NOT_FOUND
        )

    serializer = AiInsightSerializer(insight)
    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(['GET'])
def insight_history(request, device_id):
    """
    GET /api/insights/<device_id>/history/?limit=20

    Returns past LLM insights for a device.
    Useful for an insight timeline on the dashboard.
    """
    device, err = get_device_or_404(device_id)
    if err:
        return err

    limit    = min(int(request.query_params.get('limit', 20)), 100)
    insights = AiInsight.objects.filter(device=device).order_by('-generated_at')[:limit]
    serializer = AiInsightSerializer(insights, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


# =========================================
# ANOMALY VIEWS
# =========================================

@api_view(['GET'])
def anomaly_list(request, device_id):
    """
    GET /api/anomalies/<device_id>/?limit=20

    Returns recent anomalies for a device.
    """
    device, err = get_device_or_404(device_id)
    if err:
        return err

    limit     = min(int(request.query_params.get('limit', 20)), 200)
    anomalies = Anomaly.objects.filter(device=device).order_by('-detected_at')[:limit]
    serializer = AnomalySerializer(anomalies, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


# =========================================
# DAILY SUMMARY VIEWS
# =========================================

@api_view(['GET'])
def daily_summary_list(request, device_id):
    """
    GET /api/summary/<device_id>/?days=7

    Returns daily summaries for the last N days.
    Default is 7 days (weekly trend view).
    """
    device, err = get_device_or_404(device_id)
    if err:
        return err

    days      = min(int(request.query_params.get('days', 7)), 90)
    from_date = date.today() - timedelta(days=days)

    summaries = (
        DailySummary.objects
        .filter(device=device, summary_date__gte=from_date)
        .order_by('-summary_date')
    )
    serializer = DailySummarySerializer(summaries, many=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


# =========================================
# DASHBOARD VIEW
# =========================================

@api_view(['GET'])
def dashboard(request, device_id):
    """
    GET /api/dashboard/<device_id>/

    Single endpoint that returns everything the dashboard needs:
    - Device info
    - Latest reading with all computed properties
    - Latest AI insight
    - Last 5 anomalies
    - Last 7 daily summaries

    Frontend makes ONE request instead of five.
    """
    device, err = get_device_or_404(device_id)
    if err:
        return err

    # Latest reading
    latest = (
        SensorReading.objects
        .filter(device=device)
        .order_by('-recorded_at')
        .first()
    )

    # Latest insight
    insight = (
        AiInsight.objects
        .filter(device=device)
        .order_by('-generated_at')
        .first()
    )

    # Last 5 anomalies
    anomalies = (
        Anomaly.objects
        .filter(device=device)
        .order_by('-detected_at')[:5]
    )

    # Last 7 daily summaries
    from_date = date.today() - timedelta(days=7)
    summaries = (
        DailySummary.objects
        .filter(device=device, summary_date__gte=from_date)
        .order_by('-summary_date')
    )

    data = {
        "device":           DeviceSerializer(device).data,
        "latest_reading":   SensorReadingSerializer(latest).data if latest else None,
        "latest_insight":   AiInsightSerializer(insight).data if insight else None,
        "recent_anomalies": AnomalySerializer(anomalies, many=True).data,
        "weekly_summary":   DailySummarySerializer(summaries, many=True).data,
    }

    return Response(data, status=status.HTTP_200_OK)


# =========================================
# QR CODE VIEW
# =========================================

@api_view(['GET'])
def device_qrcode(request, device_id):
    """
    GET /api/devices/<device_id>/qrcode/

    Returns a PNG image of a QR code that encodes
    the frontend dashboard URL for this device.

    The frontend dev can display this image directly:
    <img src="/api/devices/<device_id>/qrcode/" />

    Or the user can scan it with their phone to open
    the dashboard for that specific device.
    """
    device, err = get_device_or_404(device_id)
    if err:
        return err

    # Build the frontend URL this QR code points to
    # e.g. http://localhost:3000/dashboard/49650ed7-d8ff-4b65-916f-409909750053
    frontend_url = f"{settings.FRONTEND_BASE_URL}/dashboard/{device.id}"

    # Generate the QR code PNG bytes
    from .utils import generate_qr_png
    png_bytes = generate_qr_png(frontend_url)

    # Return as a raw PNG HTTP response — not JSON
    return HttpResponse(png_bytes, content_type='image/png')