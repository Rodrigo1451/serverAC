"""
Testa a camada web - senha, nome da pasta, limite de tamanho, rotas de
instancia e traducao de erro pra HTTP.

    python site/teste_site.py

Precisa de: fastapi, python-multipart, httpx
"""

import importlib.util
import io
import os
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.append(str(RAIZ))

SERVIDOR = "import time\nprint('SERVIDOR NO AR', flush=True)\ntime.sleep(60)\n"

TMP = Path(tempfile.mkdtemp(prefix="teste-site-"))
TPL, RUNS = TMP / "ACServerFiles", TMP / "servidores"
(TPL / "cfg").mkdir(parents=True)
RUNS.mkdir()
(TPL / "cfg" / "server_cfg.ini").write_text("HTTP_PORT=8081\n")
(TPL / "AssettoServer.py").write_text(SERVIDOR)

os.environ.update(
    TEMPLATE_DIR=str(TPL),
    RUNS_DIR=str(RUNS),
    ENTRY_FILE="AssettoServer.py",
    ENTRY_ALTERNATIVOS="AssettoServer.exe",
    MODO="background",
    STARTUP_WAIT_S="2",
    UPLOAD_TOKEN="senha-de-teste",
    MAX_UPLOAD_MB="5",
    KEEP_RUNS="0",
)

from fastapi.testclient import TestClient  # noqa: E402

# carrega site/app.py por caminho. `import site.app` colidiria com o modulo
# `site` da stdlib - e e assim que o uvicorn carrega em producao tambem
# (WorkingDirectory=.../site, `uvicorn app:app`).
_spec = importlib.util.spec_from_file_location("serverac_app", Path(__file__).parent / "app.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

c = TestClient(_mod.app)
SENHA = "senha-de-teste"
falhas: list[str] = []


def check(nome, cond, extra=""):
    print(("  OK   " if cond else "FALHA  ") + nome + (f"  -> {extra}" if not cond else ""))
    if not cond:
        falhas.append(nome)


def zipbytes(entradas: dict) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n, v in entradas.items():
            z.writestr(n, v)
    return b.getvalue()


def enviar(arq, conteudo, nome=None, token=SENHA, header=False, substituir=False):
    files = {"arquivo": (arq, conteudo, "application/zip")}
    dados = {} if nome is None else {"nome": nome}
    if substituir:
        dados["substituir"] = "1"
    if header:
        return c.post("/upload", files=files, data=dados, headers={"X-Upload-Token": token})
    return c.post("/upload", files=files, data={**dados, "token": token})


def iniciar(nome, substituir=False):
    dados = {"token": SENHA}
    if substituir:
        dados["substituir"] = "1"
    return c.post(f"/instancias/{nome}/iniciar", data=dados)


PACOTE = None  # preenchido no main


def parar_tudo():
    r = c.get("/instancias", params={"token": SENHA})
    if r.status_code != 200:
        return
    for i in r.json()["instancias"]:
        if i["rodando"]:
            c.post(f"/instancias/{i['nome']}/parar", data={"token": SENHA})


def main() -> int:  # noqa: C901
    global PACOTE
    PACOTE = zipbytes({"cfg/entry_list.ini": "[CAR_0]\n", "content/x.txt": "y"})
    try:
        print("\n-- pagina e health")
        r = c.get("/")
        check("serve a pagina", r.status_code == 200 and 'id="nome"' in r.text, r.status_code)
        check("pagina tem o campo de nome", 'Nome do servidor' in r.text)
        h = c.get("/health").json()
        check("health enxerga a ACServerFiles", h["ok"] and h["template_existe"], h)
        check("health mostra o modo", h["modo"] == "background", h)

        print("\n-- senha")
        check("senha errada -> 401", enviar("p.zip", PACOTE, "x", token="errado").status_code == 401)
        check("sem senha -> 401", c.post("/upload", files={"arquivo": ("p.zip", PACOTE, "application/zip")}).status_code == 401)
        check("lista exige senha", c.get("/instancias").status_code == 401)
        check("parar exige senha", c.post("/instancias/x/parar").status_code == 401)
        check("nada foi criado", len([p for p in RUNS.iterdir() if p.is_dir()]) == 0)

        print("\n-- criar servidor com nome")
        r = enviar("pacote.zip", PACOTE, "Drift Noturno")
        check("upload -> 200", r.status_code == 200, r.text[:300])
        if r.status_code != 200:
            return 1
        d = r.json()
        ex = d["execucao"]
        pasta = Path(d["pasta"])
        check("nome normalizado", d["nome"] == "Drift-Noturno", d["nome"])
        check("pasta com esse nome", pasta.name == "Drift-Noturno" and pasta.is_dir())
        check("ACServerFiles duplicada", (pasta / "cfg" / "server_cfg.ini").is_file())
        check("zip guardado e extraido", (pasta / "pacote.zip").is_file() and (pasta / "cfg" / "entry_list.ini").is_file())
        check("servidor subiu", ex["subiu"] is True and ex["pid"] > 0, ex)
        check("previa do log veio", "SERVIDOR NO AR" in ex["log_previa"], ex["log_previa"])
        check("nao vazou o temporario", not (pasta / "recebido.zip").exists())

        print("\n-- nome repetido -> 409")
        r = enviar("pacote.zip", PACOTE, "Drift Noturno")
        check("conflito -> 409", r.status_code == 409, r.status_code)
        check("mensagem cita o nome", "Drift-Noturno" in r.json().get("detail", ""), r.json())

        print("\n-- sem nome: cai no automatico, e nao derruba quem esta no ar")
        r = enviar("pacote.zip", PACOTE)
        check("sem nome -> 200", r.status_code == 200, r.text[:200])
        auto = r.json()["nome"]
        check("nome automatico com data", len(auto) >= 15 and auto[:4].isdigit(), auto)
        check("criado mas NAO iniciado", r.json()["execucao"]["subiu"] is False, r.json()["execucao"])
        check("explica que ja tem um no ar",
              "Drift-Noturno" in (r.json()["execucao"].get("motivo") or ""),
              r.json()["execucao"].get("motivo"))

        print("\n-- listar com data de upload")
        insts = c.get("/instancias", params={"token": SENHA}).json()["instancias"]
        check("lista os 2 servidores", len(insts) == 2, len(insts))
        check("so 1 no ar", [i["rodando"] for i in insts].count(True) == 1, insts)
        check("todos tem data de envio", all(i["enviado_em"] for i in insts), insts)
        check("guarda o zip de origem", all(i["zip"] for i in insts), insts)
        check("mais recente primeiro", insts[0]["nome"] == auto, [i["nome"] for i in insts])

        print("\n-- iniciar pela lista")
        r = iniciar(auto)
        check("iniciar com outro no ar -> 409", r.status_code == 409, r.status_code)
        check("mensagem cita quem esta no ar", "Drift-Noturno" in r.json().get("detail", ""), r.json())
        r = iniciar(auto, substituir=True)
        check("com substituir -> 200", r.status_code == 200, r.text[:200])
        check("subiu", r.json()["subiu"] is True, r.json())
        check("derrubou o anterior", r.json()["substituiu"] == ["Drift-Noturno"], r.json().get("substituiu"))
        check("iniciar o que ja esta no ar -> 409", iniciar(auto).status_code == 409)
        check("iniciar instancia inexistente -> 404", iniciar("nao-existe").status_code == 404)

        print("\n-- upload com substituir derruba o que esta no ar")
        r = enviar("pacote.zip", PACOTE, "com-substituicao", substituir=True)
        check("upload substituindo -> 200", r.status_code == 200, r.text[:200])
        ex2 = r.json()["execucao"]
        check("novo subiu", ex2["subiu"] is True, ex2)
        check("derrubou o anterior", ex2["substituiu"] == [auto], ex2.get("substituiu"))
        insts = c.get("/instancias", params={"token": SENHA}).json()["instancias"]
        check("continua so 1 no ar", [i["rodando"] for i in insts].count(True) == 1, insts)

        # devolve o Drift-Noturno pro ar, que o resto do teste conta com ele
        iniciar("Drift-Noturno", substituir=True)

        print("\n-- log")
        r = c.get("/instancias/Drift-Noturno/log", params={"token": SENHA})
        check("log -> 200", r.status_code == 200, r.status_code)
        check("log tem a saida do servidor", "SERVIDOR NO AR" in r.json()["log"])
        check("instancia inexistente -> 404", c.get("/instancias/nao-existe/log", params={"token": SENHA}).status_code == 404)

        print("\n-- parar")
        r = c.post("/instancias/Drift-Noturno/parar", data={"token": SENHA})
        check("parar -> 200", r.status_code == 200, r.status_code)
        check("reporta parado", r.json()["parado"] is True, r.json())
        time.sleep(0.3)
        insts = c.get("/instancias", params={"token": SENHA}).json()["instancias"]
        parado = next(i for i in insts if i["nome"] == "Drift-Noturno")
        check("agora aparece parado", not parado["rodando"], parado)
        check("pasta continua no disco", (RUNS / "Drift-Noturno").is_dir())
        check("parar de novo nao explode", c.post("/instancias/Drift-Noturno/parar", data={"token": SENHA}).json()["parado"] is False)

        print("\n-- senha pelo header (curl)")
        r = enviar("via-header.zip", PACOTE, "via-header", header=True)
        check("X-Upload-Token funciona -> 200", r.status_code == 200, r.status_code)

        print("\n-- erros do processador viram status HTTP")
        parar_tudo()
        check("extensao errada -> 400", enviar("foto.png", b"\x89PNG", "a").status_code == 400)
        check("zip slip -> 400", enviar("mau.zip", zipbytes({"../../fugiu.txt": "h"}), "b").status_code == 400)
        check("nada escapou", not (TMP / "fugiu.txt").exists())
        check("zip corrompido -> 400", enviar("ruim.zip", b"nao sou zip", "c").status_code == 400)
        check("nome so de simbolos -> 400", enviar("p.zip", PACOTE, "...").status_code == 400)

        (TPL / "AssettoServer.py").unlink()
        r = enviar("s.zip", PACOTE, "sem-executavel")
        check("sem executavel -> 422", r.status_code == 422, r.status_code)
        check("mensagem diz o que procurou", "AssettoServer" in r.json().get("detail", ""), r.json())
        (TPL / "AssettoServer.py").write_text(SERVIDOR)

        print("\n-- limite de tamanho (5 MB)")
        grande = zipbytes({"g.bin": "0" * 200}) + b"\x00" * (6 * 1024 * 1024)
        r = enviar("grande.zip", grande, "grandao")
        check("acima do limite -> 413", r.status_code == 413, r.status_code)
        check("mensagem cita o limite", "5 MB" in r.json().get("detail", ""), r.json())
    finally:
        try:
            parar_tudo()
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.5)
        shutil.rmtree(TMP, ignore_errors=True)

    print("\n" + ("TODOS OS TESTES DO SITE PASSARAM" if not falhas else f"{len(falhas)} FALHA(S): {falhas}"))
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
