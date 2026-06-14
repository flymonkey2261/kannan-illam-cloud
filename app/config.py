from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    app_secret: str = os.getenv("APP_SECRET", "development-secret-change-me")
    admin_email: str = os.getenv("ADMIN_EMAIL", "admin@kannan-illam.local")
    admin_password: str = os.getenv("ADMIN_PASSWORD", "kannan-illam")
    device_id: str = os.getenv("DEVICE_ID", "kannan-illam-esp32-01")
    mqtt_host: str = os.getenv("MQTT_HOST", "")
    mqtt_port: int = int(os.getenv("MQTT_PORT", "8883"))
    mqtt_username: str = os.getenv("MQTT_USERNAME", "")
    mqtt_password: str = os.getenv("MQTT_PASSWORD", "")
    mqtt_topic_root: str = os.getenv("MQTT_TOPIC_ROOT", "kannan-illam")
    command_timeout_seconds: int = int(os.getenv("COMMAND_TIMEOUT_SECONDS", "10"))
    device_stale_seconds: int = int(os.getenv("DEVICE_STALE_SECONDS", "30"))
    database_path: str = os.getenv("DATABASE_PATH", "data/kannan_illam.db")
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8080")
    voice_webhook_secret: str = os.getenv(
        "VOICE_WEBHOOK_SECRET", "development-voice-secret-change-me"
    )

    @property
    def mqtt_enabled(self) -> bool:
        return bool(self.mqtt_host and self.mqtt_username and self.mqtt_password)


settings = Settings()
