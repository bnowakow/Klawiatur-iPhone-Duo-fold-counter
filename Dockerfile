FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
COPY proof-of-concept/requirements.txt proof-of-concept/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY supervisor.py appearance_descriptions.py clothing_descriptions.py clothing_description_integration_check.py frame_retention.py polling.py polling_integration_check.py presenter_sessions.py benchmark_frame_intervals.py compare_current_tester.py comparison_integration_check.py ipduotest_pl.py integration_check.py session_integration_check.py ipduotest_pl_integration_check.py night_stop_integration_check.py ./
COPY proof-of-concept/*.py proof-of-concept/
COPY break_scene_integration_check.py ./
COPY youtube_rate_limits.py youtube_rate_limits_integration_check.py youtube_quota.py youtube_quota_integration_check.py ./
COPY youtube_auth.py youtube_chat.py youtube_chat_integration_check.py ./
COPY youtube_commands.py ./
COPY youtube_posting_pause_integration_check.py ./
COPY youtube_bot.py youtube_stream.py youtube_bot_integration_check.py youtube_stream.proto ./
COPY youtube_stint_notifications.py youtube_stint_notifications_integration_check.py ./
RUN python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. youtube_stream.proto
COPY refine_break_endpoint.py ./
COPY proof-of-concept/test-data/2026-10-25-night-break/ proof-of-concept/test-data/2026-10-25-night-break/
COPY proof-of-concept/camera-profiles/ proof-of-concept/camera-profiles/
COPY proof-of-concept/live-frame.jpg proof-of-concept/live-frame.jpg
COPY proof-of-concept/latest/live-frame.jpg proof-of-concept/latest/live-frame.jpg
COPY proof-of-concept/camera-check/live-frame.jpg proof-of-concept/camera-check/live-frame.jpg
COPY proof-of-concept/test-data/2026-10-25-ipduotest-pl/ proof-of-concept/test-data/2026-10-25-ipduotest-pl/
COPY proof-of-concept/test-data/2026-10-25-brief-appearances/ proof-of-concept/test-data/2026-10-25-brief-appearances/
COPY proof-of-concept/test-data/2026-10-25-rate-range/ proof-of-concept/test-data/2026-10-25-rate-range/
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
COPY presenter_video_events.py video_events_integration_check.py ./
COPY repair_presenter_handover.py handover_repair_integration_check.py ./
COPY proof-of-concept/test-data/2026-10-26-dark-clothing-handover/ proof-of-concept/test-data/2026-10-26-dark-clothing-handover/
COPY utilities/current_person_message.py utilities/current_person_message.py
COPY proof-of-concept/test-data/2026-10-26-morning-resumption/ proof-of-concept/test-data/2026-10-26-morning-resumption/
CMD ["python", "supervisor.py"]
