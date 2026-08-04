import uuid
from django.db import models


# =========================================
# DEVICES
# =========================================
class Device(models.Model):

    ROOM_TYPE_CHOICES = [
        ('bedroom',     'Bedroom'),
        ('living_room', 'Living Room'),
        ('kitchen',     'Kitchen'),
        ('bathroom',    'Bathroom'),
        ('office',      'Office'),
        ('server_room', 'Server Room'),
        ('other',       'Other'),
    ]

    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mac_address = models.CharField(max_length=50, unique=True)
    room_label  = models.CharField(max_length=100, default='Unnamed device')
    room_type   = models.CharField(max_length=50, choices=ROOM_TYPE_CHOICES, null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'devices'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.room_label} ({self.mac_address})"


# =========================================
# SENSOR READINGS
# =========================================
class SensorReading(models.Model):

    device      = models.ForeignKey(Device, on_delete=models.CASCADE, related_name='readings')
    temperature = models.FloatField()    # Celsius
    humidity    = models.FloatField()    # % relative humidity
    light       = models.FloatField()    # lux
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table  = 'sensor_readings'
        ordering  = ['-recorded_at']
        indexes   = [
            models.Index(fields=['device', '-recorded_at'], name='idx_readings_device_time')
        ]

    def __str__(self):
        return f"{self.device.room_label} | {self.recorded_at:%Y-%m-%d %H:%M} | {self.temperature}°C"

    # -----------------------------------------
    # Computed properties (no extra DB columns)
    # -----------------------------------------

    @property
    def dew_point(self):
        """
        Magnus formula — reasonably accurate between 0°C and 60°C.
        Returns dew point in Celsius.
        """
        import math
        a, b = 17.27, 237.7
        alpha = (a * self.temperature / (b + self.temperature)) + math.log(self.humidity / 100.0)
        return round((b * alpha) / (a - alpha), 2)

    @property
    def heat_index(self):
        """
        Simplified Steadman heat index.
        Only meaningful when temperature >= 27°C and humidity >= 40%.
        Returns feels-like temperature in Celsius.
        """
        T = self.temperature
        H = self.humidity
        if T < 27 or H < 40:
            return round(T, 2)
        hi = (-8.78469475556
              + 1.61139411    * T
              + 2.33854883889 * H
              - 0.14611605    * T  * H
              - 0.012308094   * T  * T
              - 0.016424828   * H  * H
              + 0.002211732   * T  * T * H
              + 0.00072546    * T  * H * H
              - 0.000003582   * T  * T * H * H)
        return round(hi, 2)

    @property
    def comfort_level(self):
        """
        Returns a human-readable comfort label based on
        temperature and humidity together.
        """
        T = self.temperature
        H = self.humidity

        if 20 <= T <= 26 and 30 <= H <= 60:
            return 'Comfortable'
        elif T > 26 and H > 60:
            return 'Hot and Humid'
        elif T > 26 and H < 30:
            return 'Hot and Dry'
        elif T < 20 and H > 60:
            return 'Cold and Damp'
        elif T < 20 and H < 30:
            return 'Cold and Dry'
        else:
            return 'Moderate'

    @property
    def light_level(self):
        """
        Maps raw lux value to a human-readable label.
        """
        lux = self.light
        if lux < 10:
            return 'Dark'
        elif lux < 100:
            return 'Dim'
        elif lux < 500:
            return 'Moderate'
        elif lux < 1000:
            return 'Bright'
        else:
            return 'Very Bright'

    @property
    def occupancy_hint(self):
        """
        Rough occupancy inference from light level alone.
        Not a guarantee — just a hint for the AI context.
        """
        if self.light > 100:
            return 'Likely Occupied'
        else:
            return 'Likely Unoccupied'


# =========================================
# AI INSIGHTS
# =========================================
class AiInsight(models.Model):

    device       = models.ForeignKey(Device, on_delete=models.CASCADE, related_name='insights')
    insight_text = models.TextField()
    suggestion   = models.TextField(null=True, blank=True)
    generated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'ai_insights'
        ordering = ['-generated_at']
        indexes  = [
            models.Index(fields=['device', '-generated_at'], name='idx_insights_device_time')
        ]

    def __str__(self):
        return f"Insight for {self.device.room_label} at {self.generated_at:%Y-%m-%d %H:%M}"


# =========================================
# ANOMALIES
# =========================================
class Anomaly(models.Model):

    ANOMALY_TYPE_CHOICES = [
        ('temp_spike',      'Temperature Spike'),
        ('temp_drop',       'Temperature Drop'),
        ('humidity_jump',   'Humidity Jump'),
        ('humidity_drop',   'Humidity Drop'),
        ('light_spike',     'Light Spike'),
        ('sensor_flatline', 'Sensor Flatline'),
        ('other',           'Other'),
    ]

    device       = models.ForeignKey(Device,        on_delete=models.CASCADE,  related_name='anomalies')
    reading      = models.ForeignKey(SensorReading, on_delete=models.SET_NULL, related_name='anomalies', null=True, blank=True)
    anomaly_type = models.CharField(max_length=50,  choices=ANOMALY_TYPE_CHOICES)
    description  = models.TextField(null=True, blank=True)
    detected_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table  = 'anomalies'
        ordering  = ['-detected_at']
        indexes   = [
            models.Index(fields=['device', '-detected_at'], name='idx_anomalies_device_time')
        ]

    def __str__(self):
        return f"{self.anomaly_type} on {self.device.room_label} at {self.detected_at:%Y-%m-%d %H:%M}"


# =========================================
# DAILY SUMMARY
# =========================================
class DailySummary(models.Model):

    device          = models.ForeignKey(Device, on_delete=models.CASCADE, related_name='daily_summaries')
    summary_date    = models.DateField()

    # Temperature aggregates
    avg_temperature = models.FloatField(null=True, blank=True)
    min_temperature = models.FloatField(null=True, blank=True)
    max_temperature = models.FloatField(null=True, blank=True)

    # Humidity aggregates
    avg_humidity    = models.FloatField(null=True, blank=True)
    min_humidity    = models.FloatField(null=True, blank=True)
    max_humidity    = models.FloatField(null=True, blank=True)

    # Light aggregates
    avg_light       = models.FloatField(null=True, blank=True)
    min_light       = models.FloatField(null=True, blank=True)
    max_light       = models.FloatField(null=True, blank=True)

    anomaly_count   = models.IntegerField(default=0)
    summary_text    = models.TextField(null=True, blank=True)   # LLM-generated daily narrative
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table        = 'daily_summary'
        ordering        = ['-summary_date']
        unique_together = [('device', 'summary_date')]          # one row per device per day
        indexes         = [
            models.Index(fields=['device', '-summary_date'], name='idx_summary_device_date')
        ]

    def __str__(self):
        return f"Summary for {self.device.room_label} on {self.summary_date}"

    @property
    def temperature_range(self):
        """Convenience: how much did temperature swing today."""
        if self.min_temperature is not None and self.max_temperature is not None:
            return round(self.max_temperature - self.min_temperature, 2)
        return None

    @property
    def humidity_range(self):
        if self.min_humidity is not None and self.max_humidity is not None:
            return round(self.max_humidity - self.min_humidity, 2)
        return None