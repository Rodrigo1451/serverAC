"""
Bateria de verificacao do projeto inteiro - roda no Windows.

    python verificar.py

Junta quatro coisas:
  A. estatico       - tudo compila, os scripts de deploy tem sintaxe valida
  B. consistencia   - .env <-> app.py, HTML <-> rotas, codigos de erro <-> HTTP
  C. fim a fim real - usa a SUA ACServerFiles e o ACServerFiles_montado
  D. suites         - processador/teste_pipeline.py e site/teste_site.py

O que NAO da pra testar no Windows esta listado no fim do relatorio.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))

falhas: list[str] = []
avisos: list[str] = []
total = 0


def secao(t: str) -> None:
    print(f"\n\033[1m{t}\033[0m" if os.environ.get("TERM") else f"\n{t}")


def check(nome: str, cond: bool, extra="") -> bool:
    global total
    total += 1
    print(("  OK    " if cond else "FALHA   ") + nome + (f"  -> {extra}" if not cond else ""))
    if not cond:
        falhas.append(nome)
    return cond


def aviso(texto: str) -> None:
    avisos.append(texto)
    print("  AVISO  " + texto)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ==================================================================== A. estatico


def a_estatico() -> None:
    secao("A. ESTATICO")

    pys = sorted(p for p in RAIZ.rglob("*.py") if ".venv" not in p.parts)
    ruins = []
    for f in pys:
        try:
            ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError as e:
            ruins.append(f"{f.relative_to(RAIZ)}:{e.lineno}")
    check(f"os {len(pys)} arquivos .py compilam", not ruins, ruins)

    # imports resolvem de verdade
    try:
        import processador.pipeline as _pl  # noqa: F401

        check("processador importa sem erro", True)
    except Exception as e:  # noqa: BLE001
        check("processador importa sem erro", False, e)

    for sh in sorted((RAIZ / "deploy").glob("*.sh")):
        r = subprocess.run(["bash", "-n", str(sh)], capture_output=True, text=True)
        check(f"deploy/{sh.name}: sintaxe bash valida", r.returncode == 0, r.stderr.strip()[:160])

    # o pipeline nao pode depender de nada fora da stdlib
    fonte = (RAIZ / "processador" / "pipeline.py").read_text(encoding="utf-8")
    arvore = ast.parse(fonte)
    externos = set()
    for n in ast.walk(arvore):
        if isinstance(n, ast.Import):
            externos |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            externos.add(n.module.split(".")[0])
    fora = externos - set(sys.stdlib_module_names) - {"processador"}
    check("processador usa so a stdlib (zero dependencias)", not fora, fora)


# ============================================================== B. consistencia


def b_consistencia() -> None:
    secao("B. CONSISTENCIA ENTRE AS PARTES")

    app_src = (RAIZ / "site" / "app.py").read_text(encoding="utf-8")
    env_src = (RAIZ / ".env.example").read_text(encoding="utf-8")
    html = (RAIZ / "site" / "static" / "index.html").read_text(encoding="utf-8")
    pipe_src = (RAIZ / "processador" / "pipeline.py").read_text(encoding="utf-8")
    unit = (RAIZ / "deploy" / "serverac.service").read_text(encoding="utf-8")
    inst = (RAIZ / "deploy" / "instalar.sh").read_text(encoding="utf-8")

    # 1. variaveis de ambiente
    lidas = set(re.findall(r'_env\("([A-Z_]+)"', app_src))
    no_exemplo = set(re.findall(r"^([A-Z_]+)=", env_src, re.M))
    check("toda variavel lida pelo app esta no .env.example", lidas <= no_exemplo, lidas - no_exemplo)
    check("o .env.example nao tem variavel morta", no_exemplo <= lidas, no_exemplo - lidas)

    # 2. rotas: o que o HTML chama tem que existir no app
    rotas_app = set(re.findall(r'@app\.(?:get|post)\("([^"]+)"\)', app_src))
    padroes = {r.replace("{nome}", "*") for r in rotas_app}
    chamadas = set()
    for m in re.findall(r"fetch\(\s*[`'\"]([^`'\"]+)", html):
        u = m.split("?")[0]
        u = re.sub(r"\$\{[^}]+\}", "*", u)
        chamadas.add(u)
    faltando = {c for c in chamadas if c not in padroes}
    check("toda rota chamada pelo HTML existe no app.py", not faltando, faltando or padroes)

    # 3. ids que o JS usa existem no HTML
    ids_html = set(re.findall(r'id="([^"]+)"', html))
    ids_js = set(re.findall(r"\$\('([^']+)'\)", html)) | set(
        re.findall(r"getElementById\('([^']+)'\)", html)
    )
    check("todo id usado pelo JS existe no HTML", ids_js <= ids_html, ids_js - ids_html)

    # 4. nada que trave o navegador
    travam = [w for w in ("window.confirm", "window.alert", "window.prompt") if w in html]
    travam += [w for w in ("alert(", "confirm(", "prompt(") if re.search(rf"[^.\w]{re.escape(w)}", html)]
    check("HTML nao usa alert/confirm/prompt", not travam, travam)

    # 5. codigos de erro do pipeline mapeados no app
    codigos = set(re.findall(r'ErroPipeline\(\s*"([a-z_]+)"', pipe_src))
    mapeados = set(re.findall(r'^\s*"([a-z_]+)": \d+,', app_src, re.M))
    check("todo codigo de ErroPipeline vira um status HTTP", codigos <= mapeados, codigos - mapeados)
    check("o mapa de status nao tem codigo inexistente", mapeados <= codigos, mapeados - codigos)

    # 6. systemd
    check("unit tem KillMode=process (nao derruba os servidores)", "KillMode=process" in unit)
    check("unit roda pelo venv", "/opt/serverac/app/.venv/bin/uvicorn" in unit)
    check("unit escuta so em 127.0.0.1", "--host 127.0.0.1" in unit)
    check("unit permite escrever em /opt/serverac", "ReadWritePaths=/opt/serverac" in unit)
    check("unit nao roda como root", re.search(r"^User=serverac", unit, re.M) is not None)
    wd = re.search(r"^WorkingDirectory=(.+)$", unit, re.M)
    check("WorkingDirectory e a pasta site/ (evita colidir com o modulo `site`)",
          bool(wd) and wd.group(1).strip().endswith("/site"), wd.group(1) if wd else None)

    # 7. instalador copia as duas metades e nao referencia mais o Caddy
    check("instalar.sh copia site/ e processador/", "cp -r ./site ./processador" in inst)
    check("instalar.sh instala as deps do site", "site/requirements.txt" in inst)
    check("nada mais referencia o Caddy", "Caddyfile" not in inst and not (RAIZ / "deploy" / "Caddyfile").exists())
    check("deploy/tailscale.sh existe", (RAIZ / "deploy" / "tailscale.sh").is_file())

    # 8. .gitignore protege o que nao pode vazar
    gi = (RAIZ / ".gitignore").read_text(encoding="utf-8")
    check(".gitignore ignora o .env (tem a senha)", ".env" in gi)


# ============================================================ C. fim a fim real


def c_fim_a_fim() -> None:
    secao("C. FIM A FIM COM A SUA ACServerFiles")

    tpl, mont = RAIZ / "ACServerFiles", RAIZ / "ACServerFiles_montado"
    if not tpl.is_dir() or not mont.is_dir():
        aviso("ACServerFiles / ACServerFiles_montado nao encontrados - pulando a secao C")
        return

    # o template tem que ser subconjunto do montado
    t = {p.relative_to(tpl).as_posix(): sha(p) for p in tpl.rglob("*") if p.is_file()}
    m = {p.relative_to(mont).as_posix(): sha(p) for p in mont.rglob("*") if p.is_file()}
    check("o template e subconjunto do montado", set(t) <= set(m), sorted(set(t) - set(m))[:5])
    iguais = all(m.get(k) == v for k, v in t.items())
    check("os arquivos comuns sao identicos", iguais)

    delta = sorted(set(m) - set(t))
    check("o delta (o que o zip leva) nao esta vazio", bool(delta), len(delta))

    # o executavel do template
    binario = tpl / "AssettoServer"
    if check("o build Linux 'AssettoServer' esta no template", binario.is_file()):
        magic = binario.read_bytes()[:4]
        check("o binario e ELF (Linux), nao PE (Windows)", magic == b"\x7fELF", magic)

    # monta o zip como voce faria
    tmp = Path(tempfile.mkdtemp(prefix="verificar-"))
    try:
        z = tmp / "pacote.zip"
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED, strict_timestamps=False) as zf:
            for r in delta:
                zf.write(mont / r, r)
        mb = z.stat().st_size / 1048576
        check(f"zip montado com {len(delta)} arquivos ({mb:.1f} MB)", z.stat().st_size > 0)

        # sobe o app apontando pra ACServerFiles de verdade
        runs = tmp / "servidores"
        runs.mkdir()
        os.environ.update(
            TEMPLATE_DIR=str(tpl), RUNS_DIR=str(runs), ENTRY_FILE="AssettoServer",
            ENTRY_ALTERNATIVOS="AssettoServer.exe", MODO="background",
            STARTUP_WAIT_S="1", UPLOAD_TOKEN="verificacao", MAX_UPLOAD_MB="500",
            KEEP_RUNS="0", EXE_RUNNER="",
        )
        from fastapi.testclient import TestClient

        spec = importlib.util.spec_from_file_location("verif_app", RAIZ / "site" / "app.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        c = TestClient(mod.app)

        h = c.get("/health").json()
        check("health enxerga a ACServerFiles real", h["ok"] and h["template_existe"], h)

        r = c.post("/upload",
                   files={"arquivo": ("pacote.zip", z.read_bytes(), "application/zip")},
                   data={"nome": "interlagos-gtm", "token": "verificacao", "substituir": "1"})

        # no Windows o ELF nao roda: o esperado e um 422 explicando, nao um 500
        if r.status_code == 200:
            d = r.json()
            pasta = Path(d["pasta"])
            check("upload -> 200", True)
        elif r.status_code == 422:
            check("ELF no Windows -> 422 com explicacao (nao 500)", True)
            check("a mensagem orienta", "arquitetura" in r.json().get("detail", "").lower()
                  or "executar" in r.json().get("detail", "").lower(), r.json().get("detail"))
            # a pasta foi apagada no erro; refaz so os passos de arquivo pra comparar
            from processador.pipeline import Config, duplicar_pasta, extrair_zip

            cfg = Config(template_dir=tpl, runs_dir=runs, entry_file="AssettoServer")
            pasta = duplicar_pasta(cfg, "interlagos-gtm")
            shutil.copy2(z, pasta / "pacote.zip")
            extrair_zip(pasta / "pacote.zip", pasta)
        else:
            check("upload -> 200 ou 422", False, f"{r.status_code}: {r.text[:200]}")
            return

        # o teste que importa: a copia bate com o montado?
        obtido = {p.relative_to(pasta).as_posix(): sha(p) for p in pasta.rglob("*") if p.is_file()}
        for extra in ("pacote.zip", ".servidor.json", ".servidor.pid", "servidor.log"):
            obtido.pop(extra, None)
        faltando = sorted(set(m) - set(obtido))
        sobrando = sorted(set(obtido) - set(m))
        difere = sorted(k for k in set(m) & set(obtido) if m[k] != obtido[k])
        check(f"a copia tem os mesmos {len(m)} arquivos do montado", not faltando and not sobrando,
              {"faltando": faltando[:5], "sobrando": sobrando[:5]})
        check("todos os arquivos batem byte a byte", not difere, difere[:5])
        check("o modelo original ficou intacto",
              {p.relative_to(tpl).as_posix(): sha(p) for p in tpl.rglob("*") if p.is_file()} == t)

        # config critica que voce precisa revisar
        cfgf = pasta / "cfg" / "server_cfg.ini"
        if check("o cfg/server_cfg.ini chegou na copia", cfgf.is_file()):
            txt = cfgf.read_text(errors="replace")
            if "ADMIN_PASSWORD=password123" in txt:
                aviso("ADMIN_PASSWORD ainda e 'password123' no zip - troque antes de subir")
            portas = dict(re.findall(r"^(UDP_PORT|TCP_PORT|HTTP_PORT)=(\d+)", txt, re.M))
            check("as portas estao definidas", len(portas) == 3, portas)
            print(f"         portas: {portas}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# =================================================================== D. suites


def d_suites() -> None:
    secao("D. SUITES DE TESTE")
    for nome, script in (("processador", "processador/teste_pipeline.py"),
                         ("site", "site/teste_site.py")):
        r = subprocess.run([sys.executable, script], capture_output=True, text=True, cwd=RAIZ)
        oks = len(re.findall(r"^  OK", r.stdout, re.M))
        check(f"suite do {nome} passou ({oks} checks)", r.returncode == 0,
              [l for l in r.stdout.splitlines() if l.startswith("FALHA")][:5] or r.stderr[-300:])


# ===================================================================== relatorio

NAO_TESTAVEL = """
  - subir o AssettoServer de verdade    (binario ELF; so roda no Linux)
  - systemd: KillMode, restart, hardening  (conferido so por leitura do unit)
  - Tailscale: `tailscale serve` e o certificado
  - SIGTERM/SIGKILL e killpg              (no Windows o parar usa taskkill)
  - a checagem de dono do PID via /proc   (nao existe /proc no Windows)
  - ufw / portas do jogo abertas
  - permissoes de arquivo (chmod +x, chown serverac)
"""


def main() -> int:
    print("=" * 66)
    print("  VERIFICACAO DO serverAC  -  " + sys.platform)
    print("=" * 66)

    a_estatico()
    b_consistencia()
    c_fim_a_fim()
    d_suites()

    print("\n" + "=" * 66)
    print(f"  {total - len(falhas)}/{total} verificacoes passaram")
    if falhas:
        print(f"\n  FALHAS ({len(falhas)}):")
        for f in falhas:
            print("    - " + f)
    if avisos:
        print(f"\n  AVISOS ({len(avisos)}):")
        for a in avisos:
            print("    - " + a)
    print("\n  Nao da pra verificar no Windows:")
    print(NAO_TESTAVEL.rstrip())
    print("=" * 66)
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
