"""Read server port from the same .env settings as storage."""

import uvicorn

from .config import Settings

if __name__ == "__main__":
    uvicorn.run("kbagent.main:app", host="127.0.0.1", port=Settings().port)
