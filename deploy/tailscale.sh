#!/usr/bin/env bash
# Acesso privado ao painel via Tailscale - sem dominio, sem porta aberta na internet.
# Rode depois do instalar.sh:  sudo bash deploy/tailscale.sh
#
# ANTES: no admin do Tailscale (https://login.tailscale.com/admin/dns) ligue
#        "MagicDNS" e "HTTPS Certificates". Sem isso o `tailscale serve` nao
#        consegue emitir o certificado.
set -euo pipefail

echo "==> Instalando o Tailscale"
if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi

echo
echo "==> Conectando esta maquina a sua tailnet"
echo "    (vai imprimir um link; abra no navegador e autorize)"
tailscale up

echo
echo "==> Publicando o painel na tailnet, com HTTPS"
# o uvicorn continua escutando so em 127.0.0.1:8000; o tailscale serve e quem
# atende na tailnet e termina o TLS com um certificado de verdade.
if tailscale serve --bg 8000 2>/dev/null; then
  :
else
  # sintaxe antiga (Tailscale < 1.50)
  tailscale serve https / http://127.0.0.1:8000
fi

echo
tailscale serve status || true
echo
echo "==> Endereco do painel:"
tailscale status --json 2>/dev/null | grep -o '"DNSName":"[^"]*"' | head -1 | cut -d'"' -f4 \
  | sed 's|\.$||; s|^|    https://|' || echo "    veja em: tailscale status"
echo
echo "Pronto. O painel so responde para dispositivos da sua tailnet."
echo
echo "Para o seu amigo entrar no servidor de AC, duas opcoes:"
echo "  a) ele tambem instala o Tailscale e voce compartilha a maquina"
echo "     (nada precisa ser aberto na internet - o AC conecta pelo IP 100.x)"
echo "  b) ele conecta pela internet: ai libere so as portas do jogo"
echo "       sudo ufw allow 9600/tcp && sudo ufw allow 9600/udp"
echo "       sudo ufw allow 8081/tcp"
echo "       sudo ufw enable"
echo "     (o painel continua fechado - o ufw nao filtra a interface tailscale0)"
