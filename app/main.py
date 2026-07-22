from fastapi import FastAPI

app = FastAPI(title="BQCA Feishu Bot")


@app.get("/health")
async def health():
    return {"status": "ok"}
