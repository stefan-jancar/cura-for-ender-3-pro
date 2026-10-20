"""Lokálna simulácia Vercelu: API fronty + statické súbory z public/ (len na vývoj)."""
from fastapi.staticfiles import StaticFiles

from api.index import app

app.mount("/", StaticFiles(directory="public", html=True), name="ui")
