FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && mkdir -p /app/data
COPY games_platform /app/games_platform
COPY static /app/static
COPY app.py /app/app.py
COPY start.sh /app/start.sh
RUN chmod 755 /app/start.sh
RUN useradd -r -u 10001 games && chown -R games:games /app/data
USER games
EXPOSE 8080
CMD ["/app/start.sh"]
