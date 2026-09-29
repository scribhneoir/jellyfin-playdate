FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY tools/ tools/
USER 65534:65534
EXPOSE 8000
ENTRYPOINT ["python", "-u", "tools/bridge.py"]
CMD ["--host", "0.0.0.0"]
