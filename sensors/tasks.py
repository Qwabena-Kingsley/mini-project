import json
import logging
from datetime import date, timedelta

import requests
from celery import shared_task
from django.conf import settings
from django.db import models as db_models
from django.utils import timezone

from .models import Device, SensorReading, Anomaly, DailySummary, AiInsight

logger = logging.getLogger(__name__)


# =========================================
# HELPER — LLM call (same pattern as views.py)
# =========================================

def call_openrouter_summary(prompt):
    """
    Calls OpenRouter for a narrative daily summary.
    Returns the raw string response — no JSON parsing needed here,
    we just want a plain paragraph for summary_text.
    """
    headers = {
        "Authorization": f"Bearer {settings.OPENROUTER_API_KEY}",
        "Content-Type":  "application/json",
    }
    payload = {
        "model":    settings.OPENROUTER_MODEL,
        "messages": [
            {
                "role":    "user",
                "content": prompt,
            }
        ],
    }
    response = requests.post(
        f"{settings.OPENROUTER_BASE_URL}/chat/completions",
        headers = headers,
        json    = payload,
        timeout = 20,
    )
    response.raise_for_status()
    return response.json()['choices'][0]['message']['content'].strip()


def build_summary_prompt(device, summary):
    """
    Builds the LLM prompt for the daily narrative summary.
    Passes all aggregates so the AI has full context.
    """
    return f"""
You are an intelligent environmental monitoring assistant.

Device   : {device.room_label}
Room type: {device.room_type or 'Not specified'}
Date     : {summary.summary_date}

Daily sensor aggregates:
- Temperature : min {summary.min_temperature}°C  /  avg {summary.avg_temperature}°C  /  max {summary.max_temperature}°C
- Humidity    : min {summary.min_humidity}%      /  avg {summary.avg_humidity}%      /  max {summary.max_humidity}%
- Light       : min {summary.min_light} lux      /  avg {summary.avg_light} lux      /  max {summary.max_light} lux
- Anomalies   : {summary.anomaly_count} detected

Write a short 2-3 sentence daily summary of this room's environmental conditions.
Mention anything notable — high variance, anomalies, comfort issues.
Be clear and concise. No bullet points. Plain paragraph only.
""".strip()


# =========================================
# TASK 1 — DAILY SUMMARY
# =========================================

@shared_task(bind=True, max_retries=3)
def generate_daily_summary(self):
    """
    Runs every day at midnight UTC via Celery Beat.

    For each device:
    1. Aggregate yesterday's sensor readings (avg/min/max)
    2. Count anomalies from yesterday
    3. Save or update the daily_summary row
    4. Call LLM to generate a narrative summary_text
    5. Update the row with the narrative

    bind=True and max_retries=3 means if it fails
    (e.g. DB hiccup) it will retry up to 3 times.
    """
    yesterday = date.today() - timedelta(days=1)
    devices   = Device.objects.all()

    logger.info(f"[generate_daily_summary] Running for {yesterday} across {devices.count()} device(s).")

    for device in devices:
        try:
            # Aggregate yesterday's readings
            aggregates = (
                SensorReading.objects
                .filter(device=device, recorded_at__date=yesterday)
                .aggregate(
                    avg_temperature = db_models.Avg('temperature'),
                    min_temperature = db_models.Min('temperature'),
                    max_temperature = db_models.Max('temperature'),
                    avg_humidity    = db_models.Avg('humidity'),
                    min_humidity    = db_models.Min('humidity'),
                    max_humidity    = db_models.Max('humidity'),
                    avg_light       = db_models.Avg('light'),
                    min_light       = db_models.Min('light'),
                    max_light       = db_models.Max('light'),
                )
            )

            # Skip device if no readings yesterday
            if aggregates['avg_temperature'] is None:
                logger.info(f"[generate_daily_summary] No readings for {device.room_label} on {yesterday}. Skipping.")
                continue

            # Count anomalies
            anomaly_count = (
                Anomaly.objects
                .filter(device=device, detected_at__date=yesterday)
                .count()
            )

            # Round all floats to 2 decimal places
            def r(val):
                return round(val, 2) if val is not None else None

            # Save or update the summary row
            summary, created = DailySummary.objects.update_or_create(
                device       = device,
                summary_date = yesterday,
                defaults     = {
                    'avg_temperature': r(aggregates['avg_temperature']),
                    'min_temperature': r(aggregates['min_temperature']),
                    'max_temperature': r(aggregates['max_temperature']),
                    'avg_humidity':    r(aggregates['avg_humidity']),
                    'min_humidity':    r(aggregates['min_humidity']),
                    'max_humidity':    r(aggregates['max_humidity']),
                    'avg_light':       r(aggregates['avg_light']),
                    'min_light':       r(aggregates['min_light']),
                    'max_light':       r(aggregates['max_light']),
                    'anomaly_count':   anomaly_count,
                }
            )

            action = 'Created' if created else 'Updated'
            logger.info(f"[generate_daily_summary] {action} summary for {device.room_label} on {yesterday}.")

            # LLM narrative
            try:
                prompt       = build_summary_prompt(device, summary)
                narrative    = call_openrouter_summary(prompt)
                summary.summary_text = narrative
                summary.save(update_fields=['summary_text'])
                logger.info(f"[generate_daily_summary] LLM narrative saved for {device.room_label}.")
            except Exception as llm_err:
                # LLM failure should not block the summary row from being saved
                logger.error(f"[generate_daily_summary] LLM failed for {device.room_label}: {llm_err}")

        except Exception as e:
            logger.error(f"[generate_daily_summary] Failed for device {device.room_label}: {e}")
            raise self.retry(exc=e, countdown=60)  # retry after 60 seconds


# =========================================
# TASK 2 — SENSOR HEALTH CHECK
# =========================================

@shared_task(bind=True, max_retries=3)
def check_sensor_health(self):
    """
    Runs every 30 minutes via Celery Beat.

    For each device, checks whether a reading has been
    received in the last 30 minutes.
    If not — the sensor is likely offline or flatlined.
    Logs a 'sensor_flatline' anomaly so it shows up
    in the dashboard alert history.
    """
    cutoff  = timezone.now() - timedelta(minutes=30)
    devices = Device.objects.all()

    logger.info(f"[check_sensor_health] Checking {devices.count()} device(s).")

    for device in devices:
        try:
            latest = (
                SensorReading.objects
                .filter(device=device)
                .order_by('-recorded_at')
                .first()
            )

            if not latest:
                logger.info(f"[check_sensor_health] {device.room_label} has no readings at all. Skipping.")
                continue

            if latest.recorded_at < cutoff:
                # Check we haven't already logged a flatline anomaly
                # in the last 30 minutes to avoid duplicate alerts
                already_flagged = Anomaly.objects.filter(
                    device       = device,
                    anomaly_type = 'sensor_flatline',
                    detected_at__gte = cutoff,
                ).exists()

                if not already_flagged:
                    Anomaly.objects.create(
                        device       = device,
                        reading      = None,
                        anomaly_type = 'sensor_flatline',
                        description  = (
                            f"No readings received from {device.room_label} "
                            f"since {latest.recorded_at.strftime('%Y-%m-%d %H:%M UTC')}. "
                            f"Sensor may be offline."
                        ),
                    )
                    logger.warning(f"[check_sensor_health] Flatline detected for {device.room_label}.")
                else:
                    logger.info(f"[check_sensor_health] {device.room_label} already flagged. Skipping duplicate.")

        except Exception as e:
            logger.error(f"[check_sensor_health] Failed for {device.room_label}: {e}")
            raise self.retry(exc=e, countdown=60)