"""Local launcher. Optional .env is data only; never evaluated as code."""
import os
from pathlib import Path

ROOT=Path(__file__).parent
if __name__=='__main__':
    env=ROOT/'.env'
    if env.exists():
        for line in env.read_text(encoding='utf-8-sig').splitlines():
            if not line.strip() or line.lstrip().startswith('#') or '=' not in line:
                continue
            name,value=line.split('=',1)
            name=name.strip()
            if name=='GEMINI_API_KEY' or name=='GEMINI_MODEL' or name.startswith('COCONUTRAY_'):
                os.environ.setdefault(name,value.strip().strip('"').strip("'"))
    if not (ROOT/'frontend/dist/index.html').exists():
        raise SystemExit('Compile o frontend primeiro: na pasta frontend, execute pnpm install e pnpm run build.')
    import uvicorn
    uvicorn.run('coconutray.api:app',host='127.0.0.1',port=8000)
