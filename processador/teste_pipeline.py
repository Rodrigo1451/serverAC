"""
Testa o processador sozinho - sem web, sem HTTP, so biblioteca padrao.

    python processador/teste_pipeline.py

Usa um "AssettoServer" de mentira (um .py que fica no ar) para nao depender
de ter o servidor de verdade instalado.
"""

import contextlib
import io
import json
import shutil
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).resolve().parent.parent))
from processador import pipeline  # noqa: E402
from processador.pipeline import (  # noqa: E402
    Config,
    ErroPipeline,
    _eh_pe,
    _vivo,
    extrair_zip,
    iniciar_instancia,
    limpar_antigas,
    ler_log,
    listar_instancias,
    montar_comando,
    rodando_agora,
    nome_pasta_seguro,
    nome_seguro,
    parar_instancia,
    processar,
)

# um servidor de mentira: anuncia que subiu e fica no ar
SERVIDOR = (
    "import sys, time\n"
    "print('SERVIDOR NO AR', flush=True)\n"
    "time.sleep(60)\n"
)
MORRE = "import sys\nprint('faltou config', flush=True)\nsys.exit(1)\n"

falhas: list[str] = []


def check(nome, cond, extra=""):
    print(("  OK   " if cond else "FALHA  ") + nome + (f"  -> {extra}" if not cond else ""))
    if not cond:
        falhas.append(nome)


def espera_erro(nome, codigo, fn):
    try:
        fn()
    except ErroPipeline as e:
        check(nome, e.codigo == codigo, f"codigo veio '{e.codigo}'")
    except Exception as e:  # noqa: BLE001
        check(nome, False, f"excecao inesperada {type(e).__name__}: {e}")
    else:
        check(nome, False, "nao levantou erro nenhum")


def zipar(entradas: dict, destino: Path) -> Path:
    with zipfile.ZipFile(destino, "w") as z:
        for n, v in entradas.items():
            z.writestr(n, v)
    return destino


def montar(tmp: Path, entry="AssettoServer.py", corpo=SERVIDOR, **kw) -> Config:
    """Recria uma ACServerFiles de mentira e zera a pasta de servidores."""
    tpl, runs = tmp / "ACServerFiles", tmp / "servidores"
    for p in (tpl, runs):
        shutil.rmtree(p, ignore_errors=True)
    (tpl / "cfg").mkdir(parents=True)
    runs.mkdir(parents=True)
    (tpl / "cfg" / "server_cfg.ini").write_text("HTTP_PORT=8081\n")
    if corpo is not None:
        (tpl / entry).write_text(corpo)
    return Config(
        template_dir=tpl, runs_dir=runs, entry_file=entry, startup_wait_s=2.0, **kw
    )


def rodar_cli(args: list[str]) -> tuple[int, str]:
    """Chama a CLI de verdade e devolve (exit code, stdout)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = pipeline.main(args)
    return code, buf.getvalue()


def parar_tudo(cfg: Config) -> None:
    for i in listar_instancias(cfg):
        if i["rodando"]:
            parar_instancia(cfg, i["nome"], espera_s=5)


def main() -> int:  # noqa: C901
    tmp = Path(tempfile.mkdtemp(prefix="teste-pipeline-"))
    cfg = None
    try:
        z = zipar({"cfg/entry_list.ini": "[CAR_0]\n", "content/tracks/ks.txt": "x"}, tmp / "pacote.zip")

        print("\n-- nome da pasta")
        check("nome simples passa", nome_pasta_seguro("drift-noturno") == "drift-noturno")
        check("espaco vira traco", nome_pasta_seguro("Drift Noturno") == "Drift-Noturno")
        check("limpa caractere estranho", nome_pasta_seguro("gt3 / sprint!") == "gt3-sprint")
        check("nao deixa escapar de pasta", "/" not in nome_pasta_seguro("../../etc/passwd"))
        check("vazio vira data-hora", len(nome_pasta_seguro("")) >= 15)
        check("corta em 64", len(nome_pasta_seguro("a" * 200)) == 64)
        espera_erro("recusa nome so de simbolos", "nome_invalido", lambda: nome_pasta_seguro("..."))
        check("zip continua validado", nome_seguro("pacote(1).zip") == "pacote_1_.zip")

        print("\n-- criar servidor com nome escolhido")
        cfg = montar(tmp)
        r = processar(z, cfg, nome_pasta="drift-noturno")
        pasta = Path(r["pasta"])
        ex = r["execucao"]
        check("pasta tem o nome pedido", pasta.name == "drift-noturno", pasta.name)
        check("pasta fica dentro de servidores/", pasta.parent == cfg.runs_dir)
        check("ACServerFiles foi duplicada", (pasta / "cfg" / "server_cfg.ini").is_file())
        check("zip ficou dentro da copia", (pasta / "pacote.zip").is_file())
        check("zip foi extraido por cima", (pasta / "cfg" / "entry_list.ini").is_file())
        check("modelo original intacto", not (cfg.template_dir / "cfg" / "entry_list.ini").exists())

        print("\n-- servidor sobe em background")
        check("modo background", ex["modo"] == "background", ex["modo"])
        check("diz que subiu", ex["subiu"] is True, ex)
        check("processo esta vivo", _vivo(ex["pid"]), ex["pid"])
        check("log foi criado", Path(ex["log"]).is_file())
        check("previa do log tem a saida", "SERVIDOR NO AR" in ex["log_previa"], ex["log_previa"])
        check("pid gravado no arquivo", (pasta / cfg.pid_nome).read_text().strip() == str(ex["pid"]))

        print("\n-- listar")
        insts = listar_instancias(cfg)
        check("lista tem 1 servidor", len(insts) == 1, len(insts))
        check("aparece como rodando", insts[0]["rodando"] and insts[0]["nome"] == "drift-noturno", insts)

        print("\n-- ler log")
        d = ler_log(cfg, "drift-noturno")
        check("log vem pelo nome", "SERVIDOR NO AR" in d["log"], d["log"][:120])
        espera_erro("instancia inexistente", "instancia_ausente", lambda: ler_log(cfg, "nao-existe"))
        espera_erro("nao le fora da pasta", "instancia_ausente", lambda: ler_log(cfg, "../../etc"))

        print("\n-- nome repetido")
        espera_erro("recusa nome em uso", "nome_em_uso", lambda: processar(z, cfg, nome_pasta="drift-noturno"))
        check("servidor original segue vivo", _vivo(ex["pid"]))
        check("continua so 1 pasta", len(listar_instancias(cfg)) == 1)

        print("\n-- limpar_antigas nao mata quem esta rodando")
        processar(z, cfg, nome_pasta="segundo")
        cfg.keep_runs = 1
        parar_instancia(cfg, "segundo", espera_s=5)
        time.sleep(0.3)
        limpar_antigas(cfg)
        nomes = {i["nome"] for i in listar_instancias(cfg)}
        check("pasta com servidor no ar sobreviveu", "drift-noturno" in nomes, nomes)
        cfg.keep_runs = 0

        print("\n-- parar")
        p = parar_instancia(cfg, "drift-noturno", espera_s=8)
        check("reporta parado", p["parado"] is True, p)
        check("processo morreu mesmo", not _vivo(ex["pid"]))
        check("agora aparece parado", not listar_instancias(cfg)[0]["rodando"] if listar_instancias(cfg) else False)
        p2 = parar_instancia(cfg, "drift-noturno")
        check("parar de novo e inofensivo", p2["parado"] is False and "nao estava" in p2["motivo"], p2)
        check("pasta continua no disco", (cfg.runs_dir / "drift-noturno").is_dir())

        print("\n-- servidor que cai na largada")
        parar_tudo(cfg)
        cfg = montar(tmp, corpo=MORRE)
        r2 = processar(z, cfg, nome_pasta="quebrado")
        ex2 = r2["execucao"]
        check("diz que NAO subiu", ex2["subiu"] is False, ex2)
        check("traz o exit code", ex2["exit_code"] == 1, ex2["exit_code"])
        check("log explica o motivo", "faltou config" in ex2["log_previa"], ex2["log_previa"])
        check("pasta mantida pra investigar", Path(r2["pasta"]).is_dir())

        print("\n-- qual executavel rodar")
        cfg = montar(tmp)
        (cfg.template_dir / "AssettoServer.py").rename(cfg.template_dir / "alternativo.py")
        cfg.entry_file = "AssettoServer"
        cfg.entry_alternativos = ["alternativo.py"]
        r3 = processar(z, cfg, nome_pasta="alt")
        check("cai no alternativo quando o principal falta", r3["execucao"]["executavel"] == "alternativo.py", r3["execucao"])
        parar_tudo(cfg)

        cfg_sem = montar(tmp, corpo=None)
        espera_erro("erra se nao acha executavel", "entry_ausente", lambda: processar(z, cfg_sem, nome_pasta="vazio"))
        check("pasta apagada quando falta o executavel", not (cfg_sem.runs_dir / "vazio").exists())

        print("\n-- binario que o sistema recusa executar")
        # arquivo sem extensao com bytes que nao sao nem ELF, nem PE, nem script:
        # o SO recusa (ENOEXEC / WinError 193) e isso tem que virar erro tratado
        cfg_ruim = montar(tmp, entry="AssettoServer", corpo=None)
        (cfg_ruim.template_dir / "AssettoServer").write_bytes(b"\x00\x01\x02isto nao e executavel")
        cfg_ruim.entry_alternativos = []
        espera_erro("OSError vira falha_ao_iniciar (nao estoura 500)", "falha_ao_iniciar",
                    lambda: processar(z, cfg_ruim, nome_pasta="binario-ruim"))
        check("pasta apagada apos a falha", not (cfg_ruim.runs_dir / "binario-ruim").exists())

        cfg_ruim2 = montar(tmp, entry="AssettoServer", corpo=None, modo="aguardar")
        (cfg_ruim2.template_dir / "AssettoServer").write_bytes(b"\x00\x01\x02isto nao e executavel")
        cfg_ruim2.entry_alternativos = []
        espera_erro("modo aguardar tambem trata", "falha_ao_iniciar",
                    lambda: processar(z, cfg_ruim2, nome_pasta="ruim2"))

        print("\n-- .exe do Windows no Linux")
        alvo = tmp / "AssettoServer.exe"
        alvo.write_bytes(b"MZ\x90\x00" + b"\x00" * 60)
        check("detecta binario PE", _eh_pe(alvo) is True)
        check("nao confunde script com PE", _eh_pe(cfg_sem.template_dir / "cfg" / "server_cfg.ini") is False)

        pe_cfg = montar(tmp, entry="AssettoServer.exe", corpo=None)
        (pe_cfg.template_dir / "AssettoServer.exe").write_bytes(b"MZ\x90\x00" + b"\x00" * 60)
        pe_cfg.entry_alternativos = []
        pasta_pe = pe_cfg.runs_dir / "pe"
        shutil.copytree(pe_cfg.template_dir, pasta_pe)
        with patch.object(pipeline.os, "name", "posix"):
            espera_erro("no Linux, .exe sem runner -> erro claro", "exe_windows",
                        lambda: montar_comando(pasta_pe, pe_cfg))
            pe_cfg.exe_runner = "wine"
            _, cmd = montar_comando(pasta_pe, pe_cfg)
            check("com EXE_RUNNER=wine, prefixa o comando", cmd[0] == "wine" and cmd[1].endswith(".exe"), cmd)

        print("\n-- protecoes do zip")
        cfg = montar(tmp)
        mau = zipar({"../../fugiu.txt": "hack"}, tmp / "mau.zip")
        espera_erro("recusa caminho ../", "caminho_suspeito", lambda: processar(mau, cfg, nome_pasta="slip"))
        check("nada escapou", not (tmp / "fugiu.txt").exists() and not (tmp.parent / "fugiu.txt").exists())
        check("pasta da tentativa apagada", not (cfg.runs_dir / "slip").exists())

        abs_zip = zipar({"/etc/passwd": "hack"}, tmp / "abs.zip")
        espera_erro("recusa caminho absoluto", "caminho_suspeito", lambda: processar(abs_zip, cfg, nome_pasta="abs"))

        ruim = tmp / "ruim.zip"
        ruim.write_bytes(b"nao sou um zip de verdade")
        espera_erro("recusa zip corrompido", "zip_invalido", lambda: processar(ruim, cfg, nome_pasta="ruim"))

        espera_erro("erra se a ACServerFiles sumiu", "template_ausente",
                    lambda: processar(z, Config(template_dir=tmp / "nao-existe", runs_dir=tmp / "servidores"),
                                      nome_pasta="x"))

        print("\n-- modo aguardar (script que termina)")
        cfg_ag = montar(tmp, entry="run.py", corpo="print('TERMINEI')\n", modo="aguardar")
        r4 = processar(z, cfg_ag, nome_pasta="script")
        check("roda e espera terminar", r4["execucao"]["exit_code"] == 0, r4["execucao"])
        check("captura o stdout", "TERMINEI" in r4["execucao"]["stdout"], r4["execucao"]["stdout"])

        print("\n-- so um servidor no ar por vez")
        parar_tudo(cfg)
        cfg = montar(tmp)
        a = processar(z, cfg, nome_pasta="primeiro", substituir=False)
        check("primeiro sobe normal", a["execucao"]["subiu"] is True, a["execucao"])

        b = processar(z, cfg, nome_pasta="segundo", substituir=False)
        check("segundo NAO sobe sozinho", b["execucao"]["subiu"] is False, b["execucao"])
        check("explica o motivo", "primeiro" in (b["execucao"].get("motivo") or ""), b["execucao"].get("motivo"))
        check("mas a pasta foi criada", Path(b["pasta"]).is_dir())
        check("primeiro continua no ar", rodando_agora(cfg) == ["primeiro"], rodando_agora(cfg))

        c = processar(z, cfg, nome_pasta="terceiro", substituir=True)
        check("com substituir=True, sobe", c["execucao"]["subiu"] is True, c["execucao"])
        check("e derruba o anterior", c["execucao"]["substituiu"] == ["primeiro"], c["execucao"].get("substituiu"))
        check("so o terceiro no ar", rodando_agora(cfg) == ["terceiro"], rodando_agora(cfg))

        print("\n-- iniciar um servidor que ja existe")
        espera_erro("iniciar com outro no ar -> ja_rodando", "ja_rodando",
                    lambda: iniciar_instancia(cfg, "segundo"))
        check("nada mudou", rodando_agora(cfg) == ["terceiro"], rodando_agora(cfg))

        r_i = iniciar_instancia(cfg, "segundo", substituir=True)
        check("com substituir, o segundo sobe", r_i["subiu"] is True, r_i)
        check("derrubou o terceiro", r_i["substituiu"] == ["terceiro"], r_i.get("substituiu"))
        check("so o segundo no ar", rodando_agora(cfg) == ["segundo"], rodando_agora(cfg))
        espera_erro("iniciar o que ja esta no ar -> ja_no_ar", "ja_no_ar",
                    lambda: iniciar_instancia(cfg, "segundo"))

        parar_instancia(cfg, "segundo", espera_s=8)
        time.sleep(0.3)
        r_i2 = iniciar_instancia(cfg, "primeiro")
        check("sem ninguem no ar, inicia sem substituir", r_i2["subiu"] is True, r_i2)
        check("nao derrubou ninguem", r_i2["substituiu"] == [], r_i2.get("substituiu"))

        print("\n-- reiniciar preserva o log")
        antes = (Path(a["pasta"]) / cfg.log_nome).read_text()
        check("log tem as duas subidas", antes.count("SERVIDOR NO AR") == 2, antes.count("SERVIDOR NO AR"))
        check("marca cada inicio", antes.count("===== iniciado em") == 2, antes.count("===== iniciado em"))

        print("\n-- data de upload e metadados")
        insts = listar_instancias(cfg)
        check("todos os 3 aparecem", len(insts) == 3, [i["nome"] for i in insts])
        check("mais recente primeiro", insts[0]["nome"] == "terceiro", [i["nome"] for i in insts])
        um = next(i for i in insts if i["nome"] == "primeiro")
        check("tem data de envio", bool(um["enviado_em"]) and um["enviado_em"][:2] == "20", um["enviado_em"])
        check("guarda o zip de origem", um["zip"] == "pacote.zip", um["zip"])
        check("guarda quantos arquivos", um["arquivos_extraidos"] == 2, um["arquivos_extraidos"])
        info = json.loads((Path(a["pasta"]) / cfg.info_nome).read_text())
        check("info gravada no disco", info["nome"] == "primeiro" and "enviado_em" in info, info)
        (Path(a["pasta"]) / cfg.info_nome).unlink()
        um2 = next(i for i in listar_instancias(cfg) if i["nome"] == "primeiro")
        check("sem o .json, cai no mtime", bool(um2["enviado_em"]), um2)
        parar_tudo(cfg)

        print("\n-- linha de comando")
        parar_tudo(cfg_ag)
        cli = montar(tmp)
        runs = str(cli.runs_dir)

        code, saida = rodar_cli(["criar", str(z), "--nome", "cli-teste",
                                 "--template", str(cli.template_dir), "--runs", runs,
                                 "--entry", "AssettoServer.py", "--espera", "2"])
        d = json.loads(saida) if saida.strip().startswith("{") else {}
        check("criar -> exit 0", code == 0, code)
        check("criar imprime JSON com o nome", d.get("nome") == "cli-teste", saida[:200])
        check("criar subiu o servidor", d.get("execucao", {}).get("subiu") is True, d.get("execucao"))

        code, saida = rodar_cli(["listar", "--runs", runs])
        insts_cli = json.loads(saida)
        check("listar -> exit 0", code == 0, code)
        check("listar mostra o servidor rodando",
              any(i["nome"] == "cli-teste" and i["rodando"] for i in insts_cli), insts_cli)

        code, saida = rodar_cli(["log", "cli-teste", "--runs", runs])
        check("log -> exit 0", code == 0, code)
        check("log traz a saida do servidor", "SERVIDOR NO AR" in json.loads(saida)["log"], saida[:200])

        code, saida = rodar_cli(["criar", str(z), "--nome", "cli-teste",
                                 "--template", str(cli.template_dir), "--runs", runs,
                                 "--entry", "AssettoServer.py", "--espera", "2"])
        check("nome repetido -> exit 2", code == 2, code)

        code, saida = rodar_cli(["parar", "cli-teste", "--runs", runs, "--espera", "8"])
        check("parar -> exit 0", code == 0, code)
        check("parar reporta parado", json.loads(saida)["parado"] is True, saida[:200])
        code, _ = rodar_cli(["parar", "cli-teste", "--runs", runs])
        check("parar o que ja parou -> exit 1", code == 1, code)

        code, saida = rodar_cli(["iniciar", "cli-teste", "--runs", runs,
                                 "--entry", "AssettoServer.py", "--espera", "2"])
        check("iniciar -> exit 0", code == 0, code)
        check("iniciar reporta que subiu", json.loads(saida)["subiu"] is True, saida[:200])
        code, _ = rodar_cli(["iniciar", "cli-teste", "--runs", runs, "--entry", "AssettoServer.py"])
        check("iniciar o que ja esta no ar -> exit 2", code == 2, code)
        rodar_cli(["parar", "cli-teste", "--runs", runs, "--espera", "8"])

        code, _ = rodar_cli(["log", "nao-existe", "--runs", runs])
        check("log de instancia inexistente -> exit 2", code == 2, code)

        try:
            rodar_cli(["comando-que-nao-existe"])
        except SystemExit as e:
            check("subcomando invalido -> argparse recusa", e.code == 2, e.code)
        else:
            check("subcomando invalido -> argparse recusa", False, "nao recusou")

        print("\n-- extrair_zip direto")
        alvo2 = tmp / "solto"
        alvo2.mkdir(exist_ok=True)
        nomes2 = extrair_zip(zipar({"x/y.txt": "z"}, tmp / "s.zip"), alvo2)
        check("extrai e lista os membros", nomes2 == ["x/y.txt"] and (alvo2 / "x" / "y.txt").read_text() == "z", nomes2)
    finally:
        for c in (cfg,):
            if c:
                try:
                    parar_tudo(c)
                except Exception:  # noqa: BLE001
                    pass
        time.sleep(0.5)
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + ("TODOS OS TESTES DO PROCESSADOR PASSARAM" if not falhas else f"{len(falhas)} FALHA(S): {falhas}"))
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
