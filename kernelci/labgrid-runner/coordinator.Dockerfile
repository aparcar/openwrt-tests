FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    "labgrid @ git+https://github.com/aparcar/labgrid.git@aparcar/staging"

EXPOSE 20408

CMD ["labgrid-coordinator"]
