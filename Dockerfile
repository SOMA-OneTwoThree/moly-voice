FROM python:3.12-slim

WORKDIR /app

# ffmpeg: 오디오 디코딩/리샘플(PCM16)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8001
# 컨테이너(항상 켜짐) — 영속 WebSocket 보유. 서버리스 불가.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
