FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends texlive-xetex texlive-fonts-recommended && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY researchpilot ./researchpilot
RUN pip install --no-cache-dir ".[api,literature,math]"
ENV RESEARCHPILOT_WORKSPACE=/data
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "researchpilot.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
