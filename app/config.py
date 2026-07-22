from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    GCP_PROJECT: str = "webeye-internal-test"
    CA_AGENT_ID: str = "ecommerce-analyst-cn"
    GCS_BUCKET: str = "bqca-results"
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    FEISHU_VERIFICATION_TOKEN: str = ""
    FEISHU_ENCRYPT_KEY: str = ""
    CA_LOCATION: str = "global"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
