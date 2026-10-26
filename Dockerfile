FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
COPY proof-of-concept/requirements.txt proof-of-concept/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY supervisor.py clothing_descriptions.py frame_retention.py polling.py polling_integration_check.py presenter_sessions.py benchmark_frame_intervals.py compare_current_tester.py comparison_integration_check.py official_data.py integration_check.py session_integration_check.py official_data_integration_check.py night_stop_integration_check.py ./
COPY proof-of-concept/*.py proof-of-concept/
COPY break_scene_integration_check.py ./
COPY refine_break_endpoint.py ./
COPY proof-of-concept/test-data/2026-10-25-night-break/ proof-of-concept/test-data/2026-10-25-night-break/
COPY proof-of-concept/camera-profiles/ proof-of-concept/camera-profiles/
COPY proof-of-concept/live-frame.jpg proof-of-concept/live-frame.jpg
COPY proof-of-concept/latest/live-frame.jpg proof-of-concept/latest/live-frame.jpg
COPY proof-of-concept/camera-check/live-frame.jpg proof-of-concept/camera-check/live-frame.jpg
COPY proof-of-concept/test-data/2026-10-25-official-site/ proof-of-concept/test-data/2026-10-25-official-site/
COPY proof-of-concept/test-data/2026-10-25-brief-appearances/ proof-of-concept/test-data/2026-10-25-brief-appearances/
COPY proof-of-concept/test-data/2026-10-25-rate-range/ proof-of-concept/test-data/2026-10-25-rate-range/
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
CMD ["python", "supervisor.py"]
