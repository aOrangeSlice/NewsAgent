FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY newsagent ./newsagent
COPY config ./config
COPY README.md LICENSE ./

RUN mkdir -p /app/data

VOLUME ["/app/data"]

ENTRYPOINT ["python", "-m", "newsagent"]
CMD ["doctor"]
