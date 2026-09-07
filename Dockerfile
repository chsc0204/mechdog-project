FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mqtt_db_bridge.py .

CMD ["python", "mqtt_db_bridge.py"]