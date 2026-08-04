from rest_framework import serializers
from .models import Device, SensorReading, AiInsight, Anomaly, DailySummary


# =========================================
# DEVICE
# =========================================
class DeviceSerializer(serializers.ModelSerializer):

    class Meta:
        model  = Device
        fields = [
            'id',
            'mac_address',
            'room_label',
            'room_type',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']


# =========================================
# SENSOR READING
# =========================================
class SensorReadingSerializer(serializers.ModelSerializer):

    # Computed properties from the model — sent to frontend, never written to DB
    dew_point      = serializers.ReadOnlyField()
    heat_index     = serializers.ReadOnlyField()
    comfort_level  = serializers.ReadOnlyField()
    light_level    = serializers.ReadOnlyField()
    occupancy_hint = serializers.ReadOnlyField()

    class Meta:
        model  = SensorReading
        fields = [
            'id',
            'device',
            'temperature',
            'humidity',
            'light',
            'recorded_at',
            # computed
            'dew_point',
            'heat_index',
            'comfort_level',
            'light_level',
            'occupancy_hint',
        ]
        read_only_fields = ['id', 'recorded_at']


class SensorReadingCreateSerializer(serializers.ModelSerializer):
    """
    Used only for ESP32 POST requests.
    ESP32 sends mac_address instead of device UUID —
    we resolve the device in the view, not here.
    Keeps the serializer clean and single-purpose.
    """
    mac_address = serializers.CharField(write_only=True)

    class Meta:
        model  = SensorReading
        fields = [
            'mac_address',
            'temperature',
            'humidity',
            'light',
        ]

    def validate_temperature(self, value):
        if not -50 <= value <= 100:
            raise serializers.ValidationError(
                "Temperature out of realistic range (-50 to 100°C)."
            )
        return value

    def validate_humidity(self, value):
        if not 0 <= value <= 100:
            raise serializers.ValidationError(
                "Humidity must be between 0 and 100%."
            )
        return value

    def validate_light(self, value):
        if value < 0:
            raise serializers.ValidationError(
                "Light (lux) cannot be negative."
            )
        return value


# =========================================
# AI INSIGHT
# =========================================
class AiInsightSerializer(serializers.ModelSerializer):

    class Meta:
        model  = AiInsight
        fields = [
            'id',
            'device',
            'insight_text',
            'suggestion',
            'generated_at',
        ]
        read_only_fields = ['id', 'generated_at']


# =========================================
# ANOMALY
# =========================================
class AnomalySerializer(serializers.ModelSerializer):

    # Surface the human-readable label instead of the raw key
    anomaly_type_display = serializers.CharField(
        source='get_anomaly_type_display',
        read_only=True
    )

    class Meta:
        model  = Anomaly
        fields = [
            'id',
            'device',
            'reading',
            'anomaly_type',
            'anomaly_type_display',
            'description',
            'detected_at',
        ]
        read_only_fields = ['id', 'detected_at']


# =========================================
# DAILY SUMMARY
# =========================================
class DailySummarySerializer(serializers.ModelSerializer):

    # Computed properties from the model
    temperature_range = serializers.ReadOnlyField()
    humidity_range    = serializers.ReadOnlyField()

    class Meta:
        model  = DailySummary
        fields = [
            'id',
            'device',
            'summary_date',
            'avg_temperature',
            'min_temperature',
            'max_temperature',
            'avg_humidity',
            'min_humidity',
            'max_humidity',
            'avg_light',
            'min_light',
            'max_light',
            'anomaly_count',
            'summary_text',
            'created_at',
            # computed
            'temperature_range',
            'humidity_range',
        ]
        read_only_fields = ['id', 'created_at']


# =========================================
# DASHBOARD SERIALIZER
# =========================================
class DashboardSerializer(serializers.Serializer):
    """
    Single serializer that bundles everything the dashboard
    needs in one API call instead of four separate requests.
    """
    device          = DeviceSerializer()
    latest_reading  = SensorReadingSerializer()
    latest_insight  = AiInsightSerializer()
    recent_anomalies = AnomalySerializer(many=True)
    weekly_summary  = DailySummarySerializer(many=True)