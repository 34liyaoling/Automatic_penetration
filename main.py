import uvicorn
import platform
from dotenv import load_dotenv

load_dotenv()

from src.web.app import app
from src.config.settings import settings

if __name__ == "__main__":
    print(f"🚀 启动 {settings.APP_NAME} v{settings.VERSION}")
    
    display_host = "127.0.0.1" if settings.WEB_CONFIG["host"] == "0.0.0.0" else settings.WEB_CONFIG["host"]
    
    print(f"📊 Web界面: http://{display_host}:{settings.WEB_CONFIG['port']}")
    print(f"📚 API文档: http://{display_host}:{settings.WEB_CONFIG['port']}/docs")
    print(f"💡 提示: 如果无法访问，请尝试使用 http://localhost:{settings.WEB_CONFIG['port']}")
    print("=" * 60)
    
    uvicorn.run(
        "src.web.app:app",
        host=settings.WEB_CONFIG["host"],
        port=settings.WEB_CONFIG["port"],
        reload=settings.WEB_CONFIG["debug"]
    )
