from fastapi import FastAPI
from datetime import datetime

from pydantic import BaseModel
from typing import List

# Create FastAPI app
app = FastAPI(title="Minimal API", version="1.0.0")


class NumbersList(BaseModel):
    numbers: List[float]


@app.get("/")
def root():
    return {
        "message": "Minimal FastAPI server is running",
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/health")
def health_check():
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


@app.post("/forecast")
def calculate_sum(data: NumbersList):
    return {"sum": sum(data.numbers)}


if __name__ == "__main__":
    import uvicorn

    print("🚀 Starting minimal FastAPI server...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
