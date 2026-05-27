# AI Code Review Copilot Backend

Production-ready FastAPI backend scaffold for AI-assisted code review using static analysis and LangChain.

## Quickstart

1. Create a virtual environment and install dependencies.
2. Copy `.env.example` to `.env` and fill required keys.
3. Run:

```bash
uvicorn app.main:app --reload
```

## API

- `GET /api/v1/health`
- `POST /api/v1/review`
