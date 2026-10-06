# Runs discovery without a local Python install.
#
#   docker build -t jobsearch .
#   docker run --rm -v "$(pwd)/data:/data" jobsearch verify
#
# The tracker lives on a mounted volume so your job search survives the
# container. Your profile and .env are mounted, never baked into the image.
FROM python:3.12-slim

# Fail fast and log straight through, rather than buffering into a lost stream.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    JSA_DB=/data/jobsearch.db \
    JSA_HOME=/app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# jsa/ carries everything that ships (plan 27): the schema, the companies
# list, the example profile and .env template, and the public-domain map data
# (town and ZIP points, outlines, the basemap) under jsa/resources/.
COPY jsa/ ./jsa/
COPY tools/ ./tools/

# Run as a non-root user; nothing here needs privileges.
RUN useradd --create-home --uid 1000 jsa \
    && mkdir -p /data /app/output \
    && chown -R jsa:jsa /data /app
USER jsa

VOLUME ["/data"]
EXPOSE 8765

ENTRYPOINT ["python", "-m", "jsa"]
CMD ["matches", "--limit", "20"]
