"""Serve the exact release assets; never install packages or build at startup."""
from pathlib import Path
import uvicorn
from headlong_web.server import create_app
from headlong_web.push import PushWatcher
root=Path('/var/lib/custos-harness/dashboard')
static=Path('/opt/custos/current/runtime/headlong/web/viewer/build/client')
if not (static/'index.html').is_file():raise RuntimeError('qualified dashboard assets are missing')
PushWatcher(root).start()
print('headlong-web serving selected release at http://0.0.0.0:8080',flush=True)
uvicorn.run(create_app(root,static,read_only=False),host='0.0.0.0',port=8080,log_level='info')
