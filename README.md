# serverAC

Sobe servidores **Assetto Corsa** pela web. Voce manda um `.zip` e escolhe um nome;
o Ubuntu Server duplica a `ACServerFiles` com esse nome, extrai o zip dentro da copia
e inicia o `AssettoServer` ali — que fica no ar.

## Antes de tudo: a `ACServerFiles` nao vem no repo

O codigo esta todo aqui, mas a pasta **`ACServerFiles/`** — o AssettoServer em si —
**ficou de fora e voce precisa adicionar**. Dois motivos: o executavel tem 112 MB, acima
do limite de 100 MB por arquivo do GitHub, e `content/` traz carros e pistas do jogo.
Sem essa pasta o painel sobe normalmente, mas todo envio falha ao duplicar o modelo.

Baixe o **build Linux** do AssettoServer (o arquivo `AssettoServer`, sem extensao):

**https://github.com/compujuckel/AssettoServer/releases/tag/v0.0.54**

Na pagina, pegue **`assetto-server-linux-x64.tar.gz`** (ou
`assetto-server-linux-arm64.tar.gz`, se o servidor for ARM). O `.zip` que aparece na
lista e o build **win-x64** — esse nao roda no Ubuntu, veja
[`AssettoServer.exe` nao roda no Ubuntu](#assettoserverexe-nao-roda-no-ubuntu).

```bash
mkdir -p ACServerFiles
tar -xzf assetto-server-linux-x64.tar.gz -C ACServerFiles/
chmod +x ACServerFiles/AssettoServer
```

Ela precisa ficar com o executavel na raiz, mais o que o seu servidor usa
(`cfg/`, `content/`, `plugins/`, `system/`):

```
ACServerFiles/
├── AssettoServer      <- build Linux, sem extensao, executavel
├── cfg/
├── content/           <- cars/ e tracks/
├── plugins/
└── system/
```

No Ubuntu ela vai para `/opt/serverac/ACServerFiles` — veja
[Instalacao no Ubuntu Server](#instalacao-no-ubuntu-server). Em
`processador/exemplo-ACServerFiles/` tem um esqueleto so pra mostrar o formato;
nao e um servidor funcional.

O `.gitignore` ja ignora `ACServerFiles/`, `ACServerFiles_montado/` e `servidores/`,
entao a pasta que voce montar nao vai ser commitada por acidente.

## As duas pastas

```
serverAC/
├── site/                     LADO 1 — a web
│   ├── app.py                senha, upload, rotas de instancia, erro -> HTTP
│   ├── static/index.html     pagina: criar servidor + lista com log/parar
│   ├── requirements.txt      fastapi, uvicorn, python-multipart
│   └── teste_site.py
│
├── processador/              LADO 2 — pastas, zip, processos
│   ├── pipeline.py           duplicar → zip → extrair → subir → listar/log/parar
│   ├── exemplo-ACServerFiles/
│   └── teste_pipeline.py
│
├── ACServerFiles/            NAO VEM NO REPO — voce monta (veja a secao acima)
├── deploy/                   instalar.sh, serverac.service, tailscale.sh
├── verificar.py              bateria completa: estatico + consistencia + fim a fim
└── .env.example
```

A divisao e real, nao so de pasta:

- **`processador/` nao sabe o que e HTTP.** Recebe um `.zip` que ja esta em disco e um
  `Config`. Roda com **zero dependencias** — so a stdlib — e funciona sozinho pela CLI.
- **`site/` nao sabe o que e ACServerFiles.** Confere a senha, grava o upload num
  temporario, chama `processar()` e traduz `ErroPipeline` em status HTTP.

## O que acontece quando um zip chega

```
site/app.py                          processador/pipeline.py
─────────────                        ───────────────────────
POST /upload  (zip + nome + senha)
  confere a senha
  grava o zip num temporario
  chama processar()  ───────────────▶ 1. copytree(ACServerFiles → servidores/<nome>)
                                         409 se o nome ja existir
                                      2. coloca o .zip dentro da copia
                                      3. extrai por cima (protegido contra Zip Slip)
                                      4. Popen(AssettoServer) destacado,
                                         log -> servidor.log, pid -> .servidor.pid
                                         espera 3s pra ver se subiu mesmo
  devolve JSON  ◀──────────────────── pid, subiu, primeiras linhas do log
```

O servidor **continua rodando** depois que a pagina responde. Nao existe "vigiar pasta":
o proprio POST e o gatilho.

## O nome da pasta

O campo **Nome do servidor** na pagina vira o nome da pasta em `servidores/`.
`Drift Noturno` vira `Drift-Noturno`. So passa `A-Za-z0-9._-`, o resto vira `-`,
maximo 64 caracteres, e nao tem como escapar da pasta (`../..` nao funciona).

Se deixar vazio, cai num nome automatico com data/hora.
Se o nome ja existir, a resposta e **409** e **nada e sobrescrito** — a pasta antiga
(e o servidor que talvez esteja rodando nela) fica intacta.

## Um servidor no ar por vez

Todos os servidores sao copias da mesma `ACServerFiles`, entao herdam as mesmas portas do
`cfg/server_cfg.ini` (9600 TCP/UDP, 8081 HTTP). Dois no ar ao mesmo tempo = o segundo
morre na largada. O app trata isso explicitamente:

- **Enviando um zip** com **"Substituir o servidor no ar"** marcado (o padrao na pagina),
  o servidor atual e parado e o novo sobe no lugar. A resposta diz quem foi substituido.
- **Sem marcar**, a pasta e criada normalmente mas o servidor **nao** e iniciado, e a
  resposta explica quem esta ocupando as portas. Nada se perde: e so clicar em
  **iniciar** na lista depois.
- **Clicando em iniciar** com outro no ar, a rota devolve **409** dizendo qual. O botao
  entao vira "substituir o que esta no ar?" e um segundo clique confirma.

A lista mostra todos os servidores que existem no disco, o mais recente primeiro, com
nome, data de envio, zip de origem e estado (no ar / parado) - e os botoes de iniciar,
parar e ver log.

A data de envio fica gravada em `.servidor.json` dentro da pasta, junto com o nome e o
zip de origem. Se esse arquivo sumir, a data cai no mtime da pasta.

Reiniciar o mesmo servidor **nao apaga o log**: cada subida entra no `servidor.log` com
uma linha `===== iniciado em ... =====`.

## Rotas

| Rota | O que faz |
|---|---|
| `GET /` | a pagina |
| `GET /health` | config e se a ACServerFiles esta no lugar |
| `POST /upload` | `arquivo` (.zip) + `nome` + `substituir` + `token` → cria e sobe |
| `GET /instancias` | todos os servidores: `nome`, `enviado_em`, `rodando`, `pid`, `zip` |
| `GET /instancias/{nome}/log` | ultimas linhas do `servidor.log` |
| `POST /instancias/{nome}/iniciar` | sobe um servidor que ja existe (`substituir` opcional) |
| `POST /instancias/{nome}/parar` | SIGTERM, e SIGKILL se insistir |

Todas pedem a senha (campo `token` ou header `X-Upload-Token`).

## Instalacao no Ubuntu Server

```bash
# na sua maquina
scp -r serverAC/ usuario@ip-do-servidor:~/

# no servidor
cd ~/serverAC
sudo bash deploy/instalar.sh
```

O script cria o usuario `serverac`, o venv, as pastas em `/opt/serverac`,
**gera uma senha aleatoria e imprime na tela — anote** — e sobe o servico. Depois:

Se ainda nao montou a `ACServerFiles`, faca isso antes — ela nao vem no repo
([como montar](#antes-de-tudo-a-acserverfiles-nao-vem-no-repo)).

```bash
sudo cp -r ~/ACServerFiles/. /opt/serverac/ACServerFiles/
sudo chown -R serverac:serverac /opt/serverac/ACServerFiles
sudo chmod +x /opt/serverac/ACServerFiles/AssettoServer

curl -s localhost:8000/health
```

Fica assim no servidor:

```
/opt/serverac/
├── app/              site/ + processador/ + .venv + .env
├── ACServerFiles/    sua pasta modelo (duplicada a cada envio)
└── servidores/
    ├── drift-noturno/     ACServerFiles + o zip extraido
    │   ├── AssettoServer
    │   ├── cfg/ content/ ...
    │   ├── servidor.log        <- stdout/stderr do servidor
    │   └── .servidor.pid
    └── gt3-sprint/
```

### `AssettoServer.exe` nao roda no Ubuntu

O `.exe` e binario Windows (PE). No Linux use o **build Linux** do AssettoServer:
o arquivo `AssettoServer`, **sem extensao**. E o que o `ENTRY_FILE` aponta por padrao.
Baixe em https://github.com/compujuckel/AssettoServer/releases/tag/v0.0.54
(`assetto-server-linux-x64.tar.gz`).

O app detecta isso: se so achar um `.exe`, devolve **422** com a explicacao em vez de
um erro cru de "Exec format error". Se voce realmente precisa do `.exe`, instale o wine
e ponha `EXE_RUNNER=wine` no `.env`.

### Acesso ao painel: Tailscale (sem dominio, sem porta aberta)

```bash
sudo bash deploy/tailscale.sh
```

Antes de rodar, ligue **MagicDNS** e **HTTPS Certificates** em
<https://login.tailscale.com/admin/dns> — sem isso o `tailscale serve` nao consegue
emitir o certificado.

O script instala o Tailscale, conecta a maquina na sua tailnet e publica o painel com
`tailscale serve`. O uvicorn continua escutando **so em `127.0.0.1:8000`**; quem atende
na tailnet e termina o TLS e o proprio Tailscale, com certificado de verdade. O endereco
fica tipo `https://sua-maquina.sua-tailnet.ts.net`.

Resultado: **nenhuma porta do painel aberta na internet**, e nada de dominio, DNS ou
Let's Encrypt pra manter.

### E o seu amigo?

Para o painel, ele precisa estar na tailnet — compartilhe a maquina pelo admin do
Tailscale. Para **entrar no servidor de AC**, duas opcoes:

- **Ele tambem no Tailscale**: conecta no AC pelo IP `100.x` da maquina. Nada precisa
  ser aberto na internet. E o mais seguro.
- **Ele pela internet**: libere so as portas do jogo.

  ```bash
  sudo ufw allow 9600/tcp && sudo ufw allow 9600/udp
  sudo ufw allow 8081/tcp
  sudo ufw enable
  ```

  O painel continua fechado: o `ufw` nao filtra a interface `tailscale0`.

## Configuracao

Tudo em `/opt/serverac/app/.env` (modelo em `.env.example`):

| Variavel | O que e | Quem usa |
|---|---|---|
| `TEMPLATE_DIR` | a ACServerFiles duplicada a cada envio | processador |
| `RUNS_DIR` | onde as copias sao criadas | processador |
| `ENTRY_FILE` | executavel a iniciar (`AssettoServer`) | processador |
| `ENTRY_ALTERNATIVOS` | tentados se o principal faltar | processador |
| `MODO` | `background` (servidor no ar) ou `aguardar` | processador |
| `STARTUP_WAIT_S` | segundos ate confirmar que subiu | processador |
| `EXE_RUNNER` | `wine`/`mono` pra rodar `.exe` no Linux | processador |
| `KEEP_RUNS` | pastas a manter (`0` = nunca apagar) | processador |
| `UPLOAD_TOKEN` | senha da pagina — `openssl rand -hex 32` | site |
| `MAX_UPLOAD_MB` | limite do zip (default 200) | site |

Depois de editar: `sudo systemctl restart serverac`

O `AssettoServer` roda com `cwd` na pasta do servidor e recebe `RUN_DIR` no ambiente.

## Usando o processador sozinho

Util pra testar sem passar pela web:

```bash
python processador/pipeline.py criar pacote.zip --nome drift-noturno \
    --template /opt/serverac/ACServerFiles \
    --runs /opt/serverac/servidores --substituir

python processador/pipeline.py listar --runs /opt/serverac/servidores
python processador/pipeline.py iniciar drift-noturno --runs /opt/serverac/servidores --substituir
python processador/pipeline.py log   drift-noturno --runs /opt/serverac/servidores
python processador/pipeline.py parar drift-noturno --runs /opt/serverac/servidores
```

## Enviando por linha de comando

```bash
curl -H "X-Upload-Token: SUA_SENHA" \
     -F "arquivo=@pacote.zip" -F "nome=drift-noturno" -F "substituir=1" \
     https://seu-dominio.com.br/upload
```

## Operacao

```bash
sudo systemctl status serverac                     # o site esta no ar?
sudo journalctl -u serverac -f                     # log do site
tail -f /opt/serverac/servidores/<nome>/servidor.log   # log de um servidor
```

**Reiniciar o site nao derruba os servidores.** O unit usa `KillMode=process` justamente
pra isso: `systemctl restart serverac` mata so o uvicorn. Com o padrao do systemd
(`control-group`), todos os AssettoServer morreriam junto.

O reverso tambem vale: os servidores **nao voltam sozinhos** se a maquina reiniciar —
eles nao sao services do systemd, sao filhos soltos. Depois de um boot voce precisa
recriar/reiniciar cada um.

## Testes

```bash
python -m venv .venv
.venv/bin/pip install -r site/requirements.txt httpx

.venv/bin/python verificar.py                    # tudo: 39 verificacoes + as 2 suites
.venv/bin/python processador/teste_pipeline.py   # 98 checks, sem rede
.venv/bin/python site/teste_site.py              # 57 checks, via HTTP
```

O `verificar.py` roda tambem no Windows e cobre quatro frentes: estatico (tudo compila,
o processador nao tem dependencia externa, os .sh tem sintaxe valida), consistencia
(`.env.example` <-> `app.py`, rotas que o HTML chama <-> rotas que existem, codigos de
erro <-> status HTTP, o systemd unit), fim a fim usando a sua `ACServerFiles` de verdade
(monta o zip, sobe pelo HTTP e compara a copia com o `ACServerFiles_montado` byte a byte)
e por fim as duas suites. Ele imprime no fim o que **nao** da pra verificar fora do
Linux.

Usam um "AssettoServer" de mentira (um `.py` que fica no ar), entao rodam em qualquer
maquina sem ter o servidor de verdade instalado.

Cobrem: nome normalizado/limitado/sem escapar, colisao de nome, servidor subindo em
background com pid e log, listar/log/parar, servidor que cai na largada, executavel
alternativo, deteccao de `.exe` PE no Linux, `EXE_RUNNER=wine`, Zip Slip, caminho
absoluto, zip corrompido, entry ausente, limite de tamanho e cada erro virando o
status HTTP certo. Os subcomandos da CLI (criar/listar/log/parar) tambem sao testados.

## Limites conhecidos

- **Portas**: cada servidor de AC precisa das suas proprias portas em
  `cfg/server_cfg.ini`. Se dois zips vierem com as mesmas portas, o segundo sobe e
  morre — vai aparecer no log. O app nao checa isso.
- **Nao sobrevive a reboot**: veja acima.
- **Sem limite de quantos servidores**: cada um consome CPU/RAM; nada impede criar 50.
- **Senha unica compartilhada**: quem tem a senha sobe e para qualquer servidor.
- **Quem tem a senha executa codigo no servidor** — e literalmente o que a ferramenta
  faz. Por isso roda como o usuario `serverac`, sem sudo, com endurecimento no systemd.
  Guarde a senha como guardaria uma chave SSH.
