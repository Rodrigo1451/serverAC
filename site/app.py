"""
LADO 1 - a web.

Serve a pagina, confere a senha, recebe o .zip + o nome da pasta e entrega
pro processador. Tudo que e "duplicar pasta / extrair / subir servidor" mora
em processador/pipeline.py - aqui so tem HTTP.
"""

from __future__ import annotations

import hmac
import os
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

# deixa `processador` importavel a partir da raiz do projeto.
# append (e nao insert) de proposito: nao queremos que a pasta `site/`
# sombreie o modulo `site` da stdlib.
RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.append(str(RAIZ))

from processador.pipeline import (  # noqa: E402
    Config,
    ErroPipeline,
    iniciar_instancia,
    ler_log,
    listar_instancias,
    parar_instancia,
    processar,
)

# ---------------------------------------------------------------- config


def _env(nome: str, default: str | None = None) -> str:
    valor = os.environ.get(nome, default)
    if valor is None:
        raise RuntimeError(f"Variavel de ambiente obrigatoria nao definida: {nome}")
    return valor


CFG = Config(
    template_dir=Path(_env("TEMPLATE_DIR")),
    runs_dir=Path(_env("RUNS_DIR")),
    entry_file=_env("ENTRY_FILE", "AssettoServer"),
    entry_alternativos=[
        s.strip() for s in _env("ENTRY_ALTERNATIVOS", "AssettoServer.exe").split(",") if s.strip()
    ],
    modo=_env("MODO", "background"),
    run_timeout_s=int(_env("RUN_TIMEOUT_S", "120")),
    startup_wait_s=float(_env("STARTUP_WAIT_S", "3")),
    exe_runner=_env("EXE_RUNNER", ""),
    keep_runs=int(_env("KEEP_RUNS", "0")),
)

UPLOAD_TOKEN = _env("UPLOAD_TOKEN")
MAX_UPLOAD_MB = int(_env("MAX_UPLOAD_MB", "200"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
CHUNK = 1024 * 1024

# de que codigo do pipeline vira qual status HTTP
STATUS = {
    "zip_invalido": 400,
    "caminho_suspeito": 400,
    "nome_invalido": 400,
    "nome_em_uso": 409,
    "entry_ausente": 422,
    "exe_windows": 422,
    "falha_ao_iniciar": 422,
    "instancia_ausente": 404,
    "ja_no_ar": 409,
    "ja_rodando": 409,
    "template_ausente": 500,
}

app = FastAPI(title="serverAC")
STATIC = Path(__file__).parent / "static"


# ---------------------------------------------------------------- helpers


def conferir_token(token: str | None) -> None:
    if not token or not hmac.compare_digest(token, UPLOAD_TOKEN):
        raise HTTPException(status_code=401, detail="Token invalido.")


def http(e: ErroPipeline) -> HTTPException:
    return HTTPException(status_code=STATUS.get(e.codigo, 400), detail=e.mensagem)


async def gravar_upload(upload: UploadFile, destino: Path) -> None:
    """Grava em disco em pedacos, abortando se passar do limite."""
    total = 0
    with destino.open("wb") as out:
        while chunk := await upload.read(CHUNK):
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Arquivo maior que o limite de {MAX_UPLOAD_MB} MB.",
                )
            out.write(chunk)


# ---------------------------------------------------------------- rotas


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health():
    return {
        "ok": True,
        "template_existe": CFG.template_dir.is_dir(),
        "template": str(CFG.template_dir),
        "runs": str(CFG.runs_dir),
        "entry_file": CFG.entry_file,
        "entry_alternativos": CFG.entry_alternativos,
        "modo": CFG.modo,
        "max_upload_mb": MAX_UPLOAD_MB,
    }


@app.post("/upload")
async def upload(
    arquivo: UploadFile,
    nome: str | None = Form(default=None),
    substituir: bool = Form(default=False),
    token: str | None = Form(default=None),
    x_upload_token: str | None = Header(default=None),
):
    conferir_token(token or x_upload_token)

    with tempfile.TemporaryDirectory(prefix="serverac-") as tmp:
        temporario = Path(tmp) / "recebido.zip"
        await gravar_upload(arquivo, temporario)

        try:
            resultado = processar(
                temporario,
                CFG,
                nome_zip=arquivo.filename,
                nome_pasta=nome,
                substituir=substituir,
            )
        except ErroPipeline as e:
            raise http(e)

    return JSONResponse(resultado)


@app.get("/instancias")
def instancias(
    token: str | None = None,
    x_upload_token: str | None = Header(default=None),
):
    conferir_token(token or x_upload_token)
    return {"instancias": listar_instancias(CFG)}


@app.get("/instancias/{nome}/log")
def log(
    nome: str,
    linhas: int = 200,
    token: str | None = None,
    x_upload_token: str | None = Header(default=None),
):
    conferir_token(token or x_upload_token)
    try:
        return ler_log(CFG, nome, linhas=max(1, min(linhas, 2000)))
    except ErroPipeline as e:
        raise http(e)


@app.post("/instancias/{nome}/iniciar")
def iniciar(
    nome: str,
    substituir: bool = Form(default=False),
    token: str | None = Form(default=None),
    x_upload_token: str | None = Header(default=None),
):
    conferir_token(token or x_upload_token)
    try:
        return iniciar_instancia(CFG, nome, substituir=substituir)
    except ErroPipeline as e:
        raise http(e)


@app.post("/instancias/{nome}/parar")
def parar(
    nome: str,
    token: str | None = Form(default=None),
    x_upload_token: str | None = Header(default=None),
):
    conferir_token(token or x_upload_token)
    try:
        return parar_instancia(CFG, nome)
    except ErroPipeline as e:
        raise http(e)
