FROM docker.m.daocloud.io/library/python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       python3 python3-pytest tmux asciinema util-linux procps \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 worker \
    && useradd --create-home --uid 10001 --gid 10001 worker \
    && groupadd --gid 10002 testrunner \
    && useradd --uid 10002 --gid 10002 --no-create-home --shell /usr/sbin/nologin testrunner

RUN mkdir -p /app /logs/verifier

WORKDIR /app
COPY app/ /app/
RUN chown -R worker:worker /app /logs/verifier

USER worker
CMD ["sleep", "infinity"]
