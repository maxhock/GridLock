FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir helics[cli] 
# Install dependencies
RUN pip install --no-cache-dir \
    fastapi==0.104.1 \
    uvicorn[standard]==0.24.0 \
    pydantic==2.5.1 \
    numpy==1.24.3 \
    python-multipart

# Copy application files
COPY main.py .


# Expose the port
EXPOSE 8000


# Run the FastAPI server
#CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
CMD ["helics", "run", "--path=/config/tmp/fastapi_server_runner.json", "--no-log-files"]