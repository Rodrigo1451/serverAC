#!/usr/bin/env bash
# Instalacao no Ubuntu Server.
# Rode da raiz do projeto:  sudo bash deploy/instalar.sh
set -euo pipefail

BASE=/opt/serverac
APP=$BASE/app

if [ ! -f ./site/app.py ] || [ ! -f ./processador/pipeline.py ]; then
  echo "Rode este script da raiz do projeto (onde estao as pastas site/ e processador/)." >&2
  exit 1
fi

echo "==> Pacotes do sistema"
apt-get update
apt-get install -y python3-venv python3-pip unzip

echo "==> Usuario de servico"
id -u serverac &>/dev/null || useradd --system --home "$BASE" --shell /usr/sbin/nologin serverac

echo "==> Pastas"
#   $APP/site           -> lado 1, a web
#   $APP/processador    -> lado 2, pastas/zip/execucao
#   $BASE/ACServerFiles -> a pasta modelo, duplicada a cada envio
#   $BASE/servidores    -> um servidor por pasta
mkdir -p "$APP" "$BASE/ACServerFiles" "$BASE/servidores"

echo "==> Copiando o app"
rm -rf "$APP/site" "$APP/processador"
cp -r ./site ./processador "$APP/"
[ -f "$APP/.env" ] || cp ./.env.example "$APP/.env"

echo "==> Virtualenv (so o site precisa de dependencias; o processador e stdlib puro)"
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --upgrade pip
"$APP/.venv/bin/pip" install -r ./site/requirements.txt

echo "==> Token aleatorio"
if grep -q 'troque-isto' "$APP/.env"; then
  TOKEN=$(openssl rand -hex 32)
  sed -i "s|^UPLOAD_TOKEN=.*|UPLOAD_TOKEN=$TOKEN|" "$APP/.env"
  echo
  echo "    ############################################################"
  echo "    #  SENHA DE ACESSO GERADA - ANOTE AGORA:"
  echo "    #  $TOKEN"
  echo "    ############################################################"
  echo "    (tambem fica em $APP/.env)"
  echo
fi

echo "==> Permissoes"
chown -R serverac:serverac "$BASE"
chmod 600 "$APP/.env"

echo "==> systemd"
cp ./deploy/serverac.service /etc/systemd/system/serverac.service
systemctl daemon-reload
systemctl enable --now serverac
systemctl restart serverac
sleep 1
systemctl --no-pager --lines=15 status serverac || true

echo
echo "Pronto. Agora:"
echo "  1. Coloque sua ACServerFiles em $BASE/ACServerFiles"
echo "       sudo cp -r ~/ACServerFiles/. $BASE/ACServerFiles/"
echo "       sudo chown -R serverac:serverac $BASE/ACServerFiles"
echo "       sudo chmod +x $BASE/ACServerFiles/AssettoServer"
echo
echo "     ATENCAO: no Ubuntu use o build LINUX do AssettoServer - o arquivo"
echo "     'AssettoServer', SEM extensao. O 'AssettoServer.exe' e binario Windows"
echo "     e nao roda direto no Linux (se precisar dele, instale o wine e ponha"
echo "     EXE_RUNNER=wine no .env)."
echo
echo "  2. Teste:  curl -s localhost:8000/health"
echo "  3. Acesso: sudo bash deploy/tailscale.sh  (painel privado, com HTTPS)"
echo "  4. Se o seu amigo NAO usar Tailscale, libere so as portas do jogo:"
echo "       sudo ufw allow 9600/tcp && sudo ufw allow 9600/udp"
echo "       sudo ufw allow 8081/tcp && sudo ufw enable"
