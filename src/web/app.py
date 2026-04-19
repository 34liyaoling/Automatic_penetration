from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import logging
import sys

app = FastAPI(
    title="智能渗透测试系统",
    version="1.0.0",
    description="基于大模型的自动化渗透测试系统"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
static_dir = BASE_DIR / "static"
templates_dir = BASE_DIR / "templates"
index_html_path = templates_dir / "index.html"

if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

print(f"BASE_DIR: {BASE_DIR}")
print(f"Static directory: {static_dir}")
print(f"Static mount: /static -> {static_dir}")
print(f"Templates directory: {templates_dir}")
print(f"Index HTML: {index_html_path}")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    try:
        logger.info("Reading index.html directly")
        with open(index_html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except Exception as e:
        logger.error(f"Error reading index.html: {type(e).__name__}: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return HTMLResponse(content=f"""
        <html>
            <head><title>Error</title></head>
            <body>
                <h1>Error loading page</h1>
                <pre>{type(e).__name__}: {e}</pre>
                <pre>{traceback.format_exc()}</pre>
            </body>
        </html>
        """, status_code=500)

from src.api.routes import router as api_router
app.include_router(api_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
