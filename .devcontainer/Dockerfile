FROM mcr.microsoft.com/devcontainers/python:1-3.11-bookworm

# Same system deps as the production image (poppler-utils for pdftoppm, libpq for psycopg2),
# plus psql for poking at the database. The base image's Yarn apt repo has an expired key
# and isn't needed here.
RUN rm -f /etc/apt/sources.list.d/yarn.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    poppler-utils \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1
# postCreateCommand installs the project as the vscode user (user site-packages).
ENV PATH=/home/vscode/.local/bin:${PATH}
