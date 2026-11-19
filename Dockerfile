FROM python:3.12-slim
WORKDIR /app
# Zeitzonen-Daten, damit der Verlauf nach Ortszeit (TZ) in Tage eingeteilt wird
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.txt backend/
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend backend
COPY frontend frontend
EXPOSE 8000
CMD ["uvicorn", "--app-dir", "backend", "main:app", "--host", "0.0.0.0", "--port", "8000"]
