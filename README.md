# mpesa-processor

Fresh start for CelliPay's M-Pesa statement processor. Skeleton only.

## Run locally
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env
    uvicorn app.main:app --reload --port 8200
    pytest

## Layout
    app/api/        routes (only /health for now)
    app/core/       config, logging, AWS clients
    app/ingestion/  getting statements in
    app/parsing/    decrypt + read
    app/scoring/    score logic
    app/workers/    background consumers
    app/models/     DB models
    app/schemas/    request/response models
