from fastapi import FastAPI

app = FastAPI(
    title="Nextrace",
    description="Production incident intelligence platform",
    version="0.1.0",
)


@app.get("/health")
def health_check():
    return {
        "status": "healthy"
    }
