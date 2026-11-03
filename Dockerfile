FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
ARG INSTALL_DEV=0
RUN pip install --no-cache-dir -r $([ "$INSTALL_DEV" = "1" ] && echo requirements-dev.txt || echo requirements.txt)
COPY . .
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
