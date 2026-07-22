from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    BQ_PROJECT: str = "webeye-internal-test"
    BQ_DATASET: str = "thelook_ecommerce"
    GCS_BUCKET: str = "bqca-results"
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    FEISHU_VERIFICATION_TOKEN: str = ""
    FEISHU_ENCRYPT_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.0-flash"
    VERTEX_LOCATION: str = "asia-east1"
    MAX_RESULT_ROWS: int = 1000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
