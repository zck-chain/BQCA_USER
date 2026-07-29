from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Mandatory environment configurations (loaded from .env or OS environment)
    GCP_PROJECT: str
    GCS_BUCKET: str
    BQCA_SUPPORT_SERVICE_ACCOUNT: str

    # Ecommerce Bot & BQCA Agent Configuration
    CA_AGENT_ID: str
    CA_LOCATION: str = "global"
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    FEISHU_VERIFICATION_TOKEN: str = ""
    FEISHU_ENCRYPT_KEY: str = ""

    # Game Bot & BQCA Agent Configuration
    GAME_CA_AGENT_ID: str = ""
    GAME_CA_LOCATION: str = "global"
    GAME_FEISHU_APP_ID: str = ""
    GAME_FEISHU_APP_SECRET: str = ""
    GAME_FEISHU_VERIFICATION_TOKEN: str = ""
    GAME_FEISHU_ENCRYPT_KEY: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
