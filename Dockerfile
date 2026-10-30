FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
CMD ["sh", "-c", "python -m inhouse.data.fetch --dir /data/raw && python -m inhouse.data.load --raw /data/raw"]
