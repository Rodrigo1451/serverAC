"""
LADO 2 - o trabalho com as pastas.

Duplica a pasta modelo (ACServerFiles) com o nome que voce escolher, coloca o
.zip dentro da copia, extrai e sobe o AssettoServer em background.

Nao sabe nada sobre HTTP, upload ou token: recebe um .zip que ja esta em disco.
So biblioteca padrao do Python - nao precisa instalar nada.

Da pra usar de tres jeitos:
  - importando:  from processador.pipeline import Config, processar
  - pela linha de comando:  python pipeline.py criar pacote.zip --nome meu-server ...
  - e e isso que o site/ chama quando um upload chega.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MAX_LOG = 20000       # quanto de stdout/stderr guardar no modo "aguardar"
LINHAS_PREVIA = 40    # linhas do log devolvidas logo apos subir o servidor


# ---------------------------------------------------------------- config e erros


@dataclass
class Config:
    template_dir: Path                # pasta modelo (ACServerFiles), duplicada a cada envio
    runs_dir: Path                    # onde cada copia e criada
    entry_file: str = "AssettoServer"  # arquivo executado dentro da copia
    entry_alternativos: list[str] = field(default_factory=lambda: ["AssettoServer.exe"])
    modo: str = "background"          # "background" (servidor fica no ar) ou "aguardar"
    run_timeout_s: int = 120          # so no modo "aguardar"
    startup_wait_s: float = 3.0       # quanto esperar pra ver se o servidor subiu mesmo
    exe_runner: str = ""              # ex: "wine" ou "mono", pra rodar .exe no Linux
    keep_runs: int = 0                # 0 = nunca apagar (padrao aqui: pastas sao servidores)
    log_nome: str = "servidor.log"
    pid_nome: str = ".servidor.pid"
    info_nome: str = ".servidor.json"   # nome, data do upload, zip de origem

    def __post_init__(self) -> None:
        self.template_dir = Path(self.template_dir).resolve()
        self.runs_dir = Path(self.runs_dir).resolve()
        if self.modo not in ("background", "aguardar"):
            raise ValueError("modo deve ser 'background' ou 'aguardar'")


class ErroPipeline(Exception):
    """Erro previsto. O `codigo` deixa quem chamou decidir como reportar."""

    def __init__(self, codigo: str, mensagem: str):
        super().__init__(mensagem)
        self.codigo = codigo
        self.mensagem = mensagem


# ---------------------------------------------------------------- nomes


RE_NOME = re.compile(r"[^A-Za-z0-9._-]+")


def nome_pasta_seguro(nome: str | None) -> str:
    """
    Normaliza o nome que veio do site para algo seguro como nome de pasta.
    Vazio vira um nome com data/hora.
    """
    bruto = (nome or "").strip()
    if not bruto:
        return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]

    limpo = RE_NOME.sub("-", bruto).strip("-.")
    limpo = re.sub(r"-{2,}", "-", limpo)[:64]
    if not limpo or limpo in (".", ".."):
        raise ErroPipeline("nome_invalido", f"Nome de pasta invalido: {nome!r}")
    return limpo


def nome_seguro(nome: str | None) -> str:
    """So o nome do arquivo .zip, sem diretorio e sem caractere estranho."""
    base = Path(nome or "upload.zip").name
    base = RE_NOME.sub("_", base).lstrip(".")
    if not base.lower().endswith(".zip"):
        raise ErroPipeline("zip_invalido", "O arquivo precisa ser um .zip")
    return base


# ---------------------------------------------------------------- processos


def _vivo(pid: int) -> bool:
    """Esse PID ainda esta rodando? Funciona em Linux e Windows."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        STILL_ACTIVE = 259
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _e_nosso(pid: int, pasta: Path) -> bool:
    """
    O PID e mesmo o nosso servidor?

    O sistema recicla PID: um servidor parado deixa o .servidor.pid pra tras e,
    se aquele numero for reaproveitado por outro processo qualquer, a gente
    acharia que ele esta no ar - e pior, `parar_instancia` mataria um processo
    alheio. No Linux da pra confirmar pela linha de comando em /proc.
    """
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
    except OSError:
        return True  # sem /proc (Windows/macOS): confia no PID
    return str(pasta) in cmdline


def _ler_pid(pasta: Path, cfg: Config) -> int | None:
    """PID do servidor desta pasta, ou None. Limpa arquivo de PID obsoleto."""
    arq = pasta / cfg.pid_nome
    try:
        pid = int(arq.read_text().strip())
    except (OSError, ValueError):
        return None
    if not _vivo(pid) or not _e_nosso(pid, pasta):
        arq.unlink(missing_ok=True)
        return None
    return pid


def _tail(arquivo: Path, linhas: int) -> str:
    try:
        conteudo = arquivo.read_text(errors="replace")
    except OSError:
        return ""
    return "\n".join(conteudo.splitlines()[-linhas:])


# ---------------------------------------------------------------- passos


def duplicar_pasta(cfg: Config, nome: str) -> Path:
    """Passo 1: copia a pasta modelo para uma pasta nova com o nome escolhido."""
    if not cfg.template_dir.is_dir():
        raise ErroPipeline(
            "template_ausente", f"Pasta modelo nao existe: {cfg.template_dir}"
        )
    cfg.runs_dir.mkdir(parents=True, exist_ok=True)
    destino = cfg.runs_dir / nome
    if destino.exists():
        raise ErroPipeline(
            "nome_em_uso",
            f"Ja existe uma pasta chamada '{nome}'. Escolha outro nome.",
        )
    shutil.copytree(cfg.template_dir, destino, symlinks=False)
    return destino


def extrair_zip(zip_path: Path, destino: Path) -> list[str]:
    """Passo 3: extrai protegendo contra Zip Slip (../../etc/passwd) e caminho absoluto."""
    destino = destino.resolve()
    extraidos: list[str] = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.infolist():
                nome = member.filename
                if nome.startswith("/") or ".." in Path(nome).parts:
                    raise ErroPipeline("caminho_suspeito", f"Caminho suspeito no zip: {nome}")
                alvo = (destino / nome).resolve()
                if not alvo.is_relative_to(destino):
                    raise ErroPipeline("caminho_suspeito", f"Caminho suspeito no zip: {nome}")
                zf.extract(member, destino)
                extraidos.append(nome)
    except zipfile.BadZipFile:
        raise ErroPipeline("zip_invalido", "Arquivo .zip invalido ou corrompido.")
    return extraidos


def _eh_pe(arquivo: Path) -> bool:
    """Binario Windows (.exe/.dll comecam com 'MZ')."""
    try:
        with arquivo.open("rb") as f:
            return f.read(2) == b"MZ"
    except OSError:
        return False


def montar_comando(pasta: Path, cfg: Config) -> tuple[Path, list[str]]:
    """
    Acha o arquivo a executar e monta a linha de comando.

    Tenta `entry_file` e depois os `entry_alternativos` - assim da pra apontar
    pro binario Linux `AssettoServer` e ainda aceitar um pacote que so tenha
    o `AssettoServer.exe`.
    """
    candidatos = [cfg.entry_file, *cfg.entry_alternativos]
    alvo = next((pasta / c for c in candidatos if (pasta / c).is_file()), None)
    if alvo is None:
        raise ErroPipeline(
            "entry_ausente",
            f"Nenhum executavel encontrado apos extrair o zip. Procurei por: "
            f"{', '.join(candidatos)}",
        )

    if alvo.suffix == ".py":
        return alvo, [sys.executable, str(alvo)]

    if alvo.suffix in (".sh", ".bash"):
        alvo.chmod(alvo.stat().st_mode | stat.S_IXUSR)
        return alvo, ["bash", str(alvo)]

    # binario. no Linux, um .exe do Windows so roda com wine/mono.
    if os.name == "posix" and _eh_pe(alvo):
        if not cfg.exe_runner:
            raise ErroPipeline(
                "exe_windows",
                f"'{alvo.name}' e um executavel do Windows e nao roda direto no Linux. "
                f"Use o build Linux do AssettoServer (o arquivo 'AssettoServer', sem "
                f"extensao) na pasta modelo, ou defina EXE_RUNNER=wine no .env.",
            )
        return alvo, [*cfg.exe_runner.split(), str(alvo)]

    alvo.chmod(alvo.stat().st_mode | stat.S_IXUSR)
    return alvo, [str(alvo)]


def _msg_exec(alvo: Path, erro: OSError) -> str:
    """Mensagem util quando o sistema recusa executar o binario."""
    return (
        f"Nao consegui executar '{alvo.name}': {erro}. "
        f"Confira se o binario e do sistema e da arquitetura certos "
        f"(no Ubuntu x64, o build linux-x64 do AssettoServer) e se tem permissao "
        f"de execucao."
    )


def _corta(valor) -> str:
    return valor[-MAX_LOG:] if isinstance(valor, str) else ""


def executar_aguardando(pasta: Path, cfg: Config) -> dict:
    """Modo 'aguardar': roda e espera terminar. So serve pra script que termina sozinho."""
    alvo, cmd = montar_comando(pasta, cfg)
    inicio = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=pasta,
            capture_output=True,
            text=True,
            timeout=cfg.run_timeout_s,
            env={**os.environ, "RUN_DIR": str(pasta)},
        )
        return {
            "modo": "aguardar",
            "executavel": alvo.name,
            "comando": " ".join(cmd),
            "exit_code": proc.returncode,
            "stdout": _corta(proc.stdout),
            "stderr": _corta(proc.stderr),
            "duracao_s": round(time.monotonic() - inicio, 2),
            "timeout": False,
        }
    except OSError as e:
        raise ErroPipeline("falha_ao_iniciar", _msg_exec(alvo, e))
    except subprocess.TimeoutExpired as e:
        return {
            "modo": "aguardar",
            "executavel": alvo.name,
            "comando": " ".join(cmd),
            "exit_code": None,
            "stdout": _corta(e.stdout),
            "stderr": _corta(e.stderr),
            "duracao_s": cfg.run_timeout_s,
            "timeout": True,
        }


def executar_background(pasta: Path, cfg: Config) -> dict:
    """
    Modo 'background': sobe o servidor destacado e volta.

    O processo continua vivo depois que a resposta HTTP e enviada. stdout e
    stderr vao pro log dentro da pasta; o PID fica no arquivo de pid.
    """
    alvo, cmd = montar_comando(pasta, cfg)
    log = pasta / cfg.log_nome

    extra: dict = {}
    if os.name == "nt":
        extra["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        )
    else:
        extra["start_new_session"] = True  # sai do grupo de processos do site

    # append, nao truncate: reiniciar o mesmo servidor preserva o log anterior
    with log.open("ab") as saida:
        saida.write(f"\n===== iniciado em {datetime.now():%Y-%m-%d %H:%M:%S} =====\n".encode())
        saida.flush()
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=pasta,
                stdout=saida,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env={**os.environ, "RUN_DIR": str(pasta)},
                **extra,
            )
        except OSError as e:
            raise ErroPipeline("falha_ao_iniciar", _msg_exec(alvo, e))

    (pasta / cfg.pid_nome).write_text(str(proc.pid))

    # espera um pouco: se o servidor morre na largada, e melhor avisar agora
    limite = time.monotonic() + cfg.startup_wait_s
    while time.monotonic() < limite:
        if proc.poll() is not None:
            break
        time.sleep(0.2)

    exit_code = proc.poll()
    subiu = exit_code is None

    return {
        "modo": "background",
        "executavel": alvo.name,
        "comando": " ".join(cmd),
        "pid": proc.pid,
        "subiu": subiu,
        "exit_code": exit_code,
        "log": str(log),
        "log_previa": _tail(log, LINHAS_PREVIA),
    }


def limpar_antigas(cfg: Config) -> int:
    """
    Apaga execucoes antigas, mantendo as `keep_runs` mais recentes.
    Nunca apaga uma pasta cujo servidor esta rodando.
    """
    if cfg.keep_runs <= 0 or not cfg.runs_dir.is_dir():
        return 0
    pastas = sorted(
        (p for p in cfg.runs_dir.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    apagadas = 0
    for velha in pastas[cfg.keep_runs:]:
        pid = _ler_pid(velha, cfg)
        if pid and _vivo(pid):
            continue
        shutil.rmtree(velha, ignore_errors=True)
        apagadas += 1
    return apagadas


# ---------------------------------------------------------------- instancias


def _escrever_info(pasta: Path, cfg: Config, dados: dict) -> None:
    (pasta / cfg.info_nome).write_text(json.dumps(dados, ensure_ascii=False, indent=2))


def _ler_info(pasta: Path, cfg: Config) -> dict:
    try:
        return json.loads((pasta / cfg.info_nome).read_text())
    except (OSError, ValueError):
        return {}


def status_instancia(pasta: Path, cfg: Config) -> dict:
    pid = _ler_pid(pasta, cfg)
    rodando = bool(pid and _vivo(pid))
    log = pasta / cfg.log_nome
    info = _ler_info(pasta, cfg)
    # a data do upload vem do .servidor.json; se ele sumir, cai no mtime da pasta
    enviado = info.get("enviado_em") or datetime.fromtimestamp(
        pasta.stat().st_mtime
    ).isoformat(timespec="milliseconds")
    return {
        "nome": pasta.name,
        "pasta": str(pasta),
        "pid": pid if rodando else None,
        "rodando": rodando,
        "enviado_em": enviado,
        "zip": info.get("zip"),
        "arquivos_extraidos": info.get("arquivos_extraidos"),
        "log_existe": log.is_file(),
        "log_bytes": log.stat().st_size if log.is_file() else 0,
    }


def listar_instancias(cfg: Config) -> list[dict]:
    """Todos os servidores que existem no disco, mais recentes primeiro."""
    if not cfg.runs_dir.is_dir():
        return []
    itens = [status_instancia(p, cfg) for p in cfg.runs_dir.iterdir() if p.is_dir()]
    return sorted(itens, key=lambda i: i["enviado_em"], reverse=True)


def rodando_agora(cfg: Config, exceto: str | None = None) -> list[str]:
    """Nomes dos servidores no ar (menos `exceto`)."""
    return [
        i["nome"] for i in listar_instancias(cfg) if i["rodando"] and i["nome"] != exceto
    ]


def parar_rodando(cfg: Config, exceto: str | None = None) -> list[str]:
    """Para todos os servidores no ar. Devolve os nomes que foram parados."""
    parados = []
    for nome in rodando_agora(cfg, exceto=exceto):
        if parar_instancia(cfg, nome).get("parado"):
            parados.append(nome)
    return parados


def iniciar_instancia(cfg: Config, nome: str, substituir: bool = False) -> dict:
    """
    Sobe um servidor que ja existe no disco (sem upload novo).

    Como so pode haver um no ar por vez (mesmas portas), recusa se outro estiver
    rodando - a menos que `substituir` seja True, aí para o outro antes.
    """
    pasta = _pasta_da_instancia(cfg, nome)
    pid = _ler_pid(pasta, cfg)
    if pid and _vivo(pid):
        raise ErroPipeline("ja_no_ar", f"'{pasta.name}' ja esta rodando (pid {pid}).")

    montar_comando(pasta, cfg)  # valida o executavel antes de mexer no que esta no ar

    outros = rodando_agora(cfg, exceto=pasta.name)
    if outros and not substituir:
        raise ErroPipeline(
            "ja_rodando",
            f"'{outros[0]}' esta no ar e usa as mesmas portas. "
            f"Pare ele antes, ou peca para substituir.",
        )

    parados = parar_rodando(cfg, exceto=pasta.name) if outros else []
    resultado = executar_background(pasta, cfg)
    resultado["substituiu"] = parados
    return resultado


def _pasta_da_instancia(cfg: Config, nome: str) -> Path:
    """Resolve o nome pra uma pasta dentro de runs_dir, sem deixar escapar."""
    limpo = Path(nome).name
    pasta = (cfg.runs_dir / limpo).resolve()
    if not pasta.is_relative_to(cfg.runs_dir) or not pasta.is_dir():
        raise ErroPipeline("instancia_ausente", f"Nao achei a instancia '{nome}'.")
    return pasta


def ler_log(cfg: Config, nome: str, linhas: int = 200) -> dict:
    pasta = _pasta_da_instancia(cfg, nome)
    return {**status_instancia(pasta, cfg), "log": _tail(pasta / cfg.log_nome, linhas)}


def parar_instancia(cfg: Config, nome: str, espera_s: float = 10.0) -> dict:
    """Manda o servidor parar (SIGTERM), e insiste (SIGKILL) se ele nao sair."""
    pasta = _pasta_da_instancia(cfg, nome)
    pid = _ler_pid(pasta, cfg)
    if not pid or not _vivo(pid):
        return {"nome": pasta.name, "parado": False, "motivo": "nao estava rodando"}

    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                os.kill(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass

    limite = time.monotonic() + espera_s
    while time.monotonic() < limite and _vivo(pid):
        time.sleep(0.2)

    forcado = False
    if _vivo(pid) and os.name != "nt":
        forcado = True
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                os.kill(pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        time.sleep(0.3)

    parado = not _vivo(pid)
    if parado:
        # some com o arquivo de PID: se o numero for reciclado por outro
        # processo, ninguem confunde com este servidor
        (pasta / cfg.pid_nome).unlink(missing_ok=True)

    return {
        "nome": pasta.name,
        "parado": parado,
        "pid": pid,
        "forcado": forcado,
    }


# ---------------------------------------------------------------- pipeline completo


def processar(
    zip_path: Path,
    cfg: Config,
    nome_zip: str | None = None,
    nome_pasta: str | None = None,
    substituir: bool = False,
) -> dict:
    """
    O fluxo inteiro, a partir de um .zip que ja esta em disco.

    1. duplica a pasta modelo com o nome escolhido
    2. coloca o .zip dentro da copia
    3. extrai
    4. sobe o AssettoServer (background) ou roda e espera (aguardar)

    Como todos os servidores usam as mesmas portas, so um pode estar no ar:
      - substituir=True  -> para o que estiver rodando e sobe o novo
      - substituir=False -> cria a pasta mas NAO sobe, e explica o motivo
        (a pasta fica no disco pra voce iniciar depois pelo site)

    Se um passo previsto falhar, a pasta da execucao e apagada e sobe um ErroPipeline.
    """
    zip_path = Path(zip_path)
    nome_arquivo = nome_seguro(nome_zip or zip_path.name)
    nome = nome_pasta_seguro(nome_pasta)

    pasta = duplicar_pasta(cfg, nome)
    try:
        destino_zip = pasta / nome_arquivo
        if not zip_path.is_file():
            raise ErroPipeline("zip_invalido", f"Arquivo nao encontrado: {zip_path}")
        if zip_path.resolve() != destino_zip.resolve():
            shutil.copy2(zip_path, destino_zip)

        arquivos = extrair_zip(destino_zip, pasta)
        _escrever_info(
            pasta,
            cfg,
            {
                "nome": nome,
                # milissegundos: dois envios no mesmo segundo nao empatam na ordenacao
                "enviado_em": datetime.now().isoformat(timespec="milliseconds"),
                "zip": nome_arquivo,
                "tamanho_bytes": destino_zip.stat().st_size,
                "arquivos_extraidos": len(arquivos),
            },
        )

        if cfg.modo != "background":
            resultado = executar_aguardando(pasta, cfg)
        else:
            # valida o executavel antes de encostar em quem esta no ar
            montar_comando(pasta, cfg)
            outros = rodando_agora(cfg, exceto=nome)
            if outros and not substituir:
                resultado = {
                    "modo": "background",
                    "executavel": None,
                    "pid": None,
                    "subiu": False,
                    "exit_code": None,
                    "log": str(pasta / cfg.log_nome),
                    "log_previa": "",
                    "substituiu": [],
                    "motivo": f"'{outros[0]}' esta no ar e usa as mesmas portas. "
                    f"O servidor '{nome}' foi criado mas nao iniciado - "
                    f"inicie pela lista quando quiser.",
                }
            else:
                parados = parar_rodando(cfg, exceto=nome) if outros else []
                resultado = executar_background(pasta, cfg)
                resultado["substituiu"] = parados
    except ErroPipeline:
        shutil.rmtree(pasta, ignore_errors=True)
        raise

    limpar_antigas(cfg)

    return {
        "nome": nome,
        "pasta": str(pasta),
        "zip": nome_arquivo,
        "tamanho_bytes": destino_zip.stat().st_size,
        "arquivos_extraidos": len(arquivos),
        "execucao": resultado,
    }


# ---------------------------------------------------------------- linha de comando



def config_de_leitura(runs_dir: Path) -> Config:
    """
    Config para os comandos que so olham/param instancias.
    Eles nao duplicam nada, entao template_dir nunca e usado - repetir runs_dir
    aqui e so para satisfazer o dataclass.
    """
    return Config(template_dir=runs_dir, runs_dir=runs_dir)


def _saida(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="pipeline.py",
        description="Duplica a ACServerFiles, extrai o zip na copia e sobe o AssettoServer.",
    )
    sub = p.add_subparsers(dest="cmd", required=True, metavar="comando")

    c = sub.add_parser("criar", help="cria um servidor a partir de um .zip")
    c.add_argument("zip", type=Path, help="caminho do .zip")
    c.add_argument("--nome", help="nome da pasta a criar (default: data/hora)")
    c.add_argument("--template", type=Path, required=True, help="a ACServerFiles")
    c.add_argument("--runs", type=Path, required=True, help="onde criar a copia")
    c.add_argument("--entry", default="AssettoServer", help="executavel (default: AssettoServer)")
    c.add_argument("--alternativos", default="AssettoServer.exe",
                   help="executaveis alternativos, separados por virgula")
    c.add_argument("--modo", default="background", choices=["background", "aguardar"])
    c.add_argument("--timeout", type=int, default=120, help="modo aguardar: tempo maximo")
    c.add_argument("--espera", type=float, default=3.0, help="segundos para conferir se subiu")
    c.add_argument("--exe-runner", default="", help="ex: wine, para rodar .exe no Linux")
    c.add_argument("--keep", type=int, default=0, help="pastas a manter (0 = todas)")
    c.add_argument("--substituir", action="store_true",
                   help="para o servidor que estiver no ar antes de subir o novo")

    li = sub.add_parser("listar", help="lista os servidores e o estado de cada um")
    li.add_argument("--runs", type=Path, required=True)

    it = sub.add_parser("iniciar", help="sobe um servidor que ja existe")
    it.add_argument("nome")
    it.add_argument("--runs", type=Path, required=True)
    it.add_argument("--entry", default="AssettoServer")
    it.add_argument("--espera", type=float, default=3.0)
    it.add_argument("--substituir", action="store_true",
                    help="para o servidor que estiver no ar antes")

    lo = sub.add_parser("log", help="mostra o fim do log de um servidor")
    lo.add_argument("nome")
    lo.add_argument("--runs", type=Path, required=True)
    lo.add_argument("--linhas", type=int, default=200)

    pa = sub.add_parser("parar", help="para um servidor")
    pa.add_argument("nome")
    pa.add_argument("--runs", type=Path, required=True)
    pa.add_argument("--espera", type=float, default=10.0)

    a = p.parse_args(argv)

    try:
        if a.cmd == "listar":
            _saida(listar_instancias(config_de_leitura(a.runs)))
            return 0

        if a.cmd == "log":
            _saida(ler_log(config_de_leitura(a.runs), a.nome, linhas=a.linhas))
            return 0

        if a.cmd == "parar":
            r = parar_instancia(config_de_leitura(a.runs), a.nome, espera_s=a.espera)
            _saida(r)
            return 0 if r["parado"] else 1

        if a.cmd == "iniciar":
            cfg_i = config_de_leitura(a.runs)
            cfg_i.entry_file = a.entry
            cfg_i.startup_wait_s = a.espera
            r = iniciar_instancia(cfg_i, a.nome, substituir=a.substituir)
            _saida(r)
            return 0 if r["subiu"] else 1

        cfg = Config(
            template_dir=a.template,
            runs_dir=a.runs,
            entry_file=a.entry,
            entry_alternativos=[s.strip() for s in a.alternativos.split(",") if s.strip()],
            modo=a.modo,
            run_timeout_s=a.timeout,
            startup_wait_s=a.espera,
            exe_runner=a.exe_runner,
            keep_runs=a.keep,
        )
        r = processar(a.zip, cfg, nome_pasta=a.nome, substituir=a.substituir)
    except ErroPipeline as e:
        print(f"erro [{e.codigo}]: {e.mensagem}", file=sys.stderr)
        return 2

    _saida(r)
    ex = r["execucao"]
    if ex["modo"] == "background":
        return 0 if ex["subiu"] else 1
    return 0 if ex["exit_code"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
