#!/usr/bin/env bash
# ==============================================================================
# Skript dlya bystrogo razvertyvaniya / vosstanovleniya Ukrainskogo VPN-servera (Xray VLESS Reality)
# Podderzhivaet: Ubuntu 20.04 / 22.04 / 24.04, Debian 11 / 12
# ==============================================================================

set -euo pipefail

echo "=== [1/5] Obnovlenie paketov i ustanovka zavisimostey ==="
apt-get update -y
apt-get install -y curl socat jq openssl ufw

echo "=== [2/5] Ustanovka oficialnogo Xray-core ==="
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install

# Generaciya klyuchey Reality
echo "=== [3/5] Generaciya klyuchey Reality ==="
KEYPAIR=$(/usr/local/bin/xray x25519)
PRIVATE_KEY=$(echo "$KEYPAIR" | grep "PrivateKey" | awk '{print $2}')
PUBLIC_KEY=$(echo "$KEYPAIR" | grep "Password (PublicKey)" | awk '{print $3}')
SHORT_ID=$(openssl rand -hex 4)
UUID="69d4388f-ae2d-45cb-acc3-775c6c6dbcea" # Fiksirovannyy UUID dlya TgPay
IP=$(curl -s -4 ifconfig.me || curl -s -4 icanhazip.com)

echo "=== [4/5] Sozdanie konfiguracii /usr/local/etc/xray/config.json ==="
cat << EOF > /usr/local/etc/xray/config.json
{
  "log": {
    "loglevel": "warning"
  },
  "dns": {
    "servers": [
      "8.8.8.8",
      "1.1.1.1"
    ],
    "queryStrategy": "UseIPv4"
  },
  "inbounds": [
    {
      "tag": "vless-tcp-6443",
      "listen": "0.0.0.0",
      "port": 6443,
      "protocol": "vless",
      "settings": {
        "clients": [
          {
            "id": "${UUID}",
            "flow": "xtls-rprx-vision",
            "email": "tgpay-ua"
          }
        ],
        "decryption": "none"
      },
      "streamSettings": {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
          "show": false,
          "dest": "www.cloudflare.com:443",
          "xver": 0,
          "serverNames": [
            "www.cloudflare.com"
          ],
          "privateKey": "${PRIVATE_KEY}",
          "shortIds": [
            "${SHORT_ID}"
          ]
        },
        "tcpSettings": {
          "acceptProxyProtocol": false
        }
      },
      "sniffing": {
        "enabled": true,
        "destOverride": ["http", "tls", "quic"]
      }
    },
    {
      "tag": "vless-grpc-6445",
      "listen": "0.0.0.0",
      "port": 6445,
      "protocol": "vless",
      "settings": {
        "clients": [
          {
            "id": "${UUID}",
            "email": "tgpay-ua-grpc"
          }
        ],
        "decryption": "none"
      },
      "streamSettings": {
        "network": "grpc",
        "security": "reality",
        "realitySettings": {
          "show": false,
          "dest": "www.cloudflare.com:443",
          "xver": 0,
          "serverNames": [
            "www.cloudflare.com"
          ],
          "privateKey": "${PRIVATE_KEY}",
          "shortIds": [
            "${SHORT_ID}"
          ]
        },
        "grpcSettings": {
          "serviceName": "grpc-ua",
          "multiMode": true
        }
      },
      "sniffing": {
        "enabled": true,
        "destOverride": ["http", "tls", "quic"]
      }
    }
  ],
  "outbounds": [
    {
      "protocol": "freedom",
      "tag": "direct",
      "settings": {
        "domainStrategy": "UseIPv4"
      }
    }
  ]
}
EOF

echo "=== [5/5] Nastroyka fayrvola i perezapusk Xray ==="
if ufw status | grep -q "Status: active"; then
    ufw allow 6443/tcp
    ufw allow 6445/tcp
fi

systemctl restart xray
systemctl enable xray
sleep 1

if systemctl is-active --quiet xray; then
    echo "=========================================================="
    echo "  [SUCCESS] Xray uspeshno zapuschen i rabotaet!"
    echo "=========================================================="
    echo ""
    echo "Skopiruyte sleduyuschie stroki v .env vashego bota:"
    echo ""
    echo "# --- UKRAINE TCP ---"
    echo "VPN_NODE_5_KEY=ua_tcp"
    echo "VPN_NODE_5_FLAG=🇺🇦"
    echo "VPN_NODE_5_NAME=UA Direct (TCP)"
    echo "VPN_NODE_5_PROFILE_NAME=\"Ukraine #2\""
    echo "VPN_NODE_5_HOST=${IP}"
    echo "VPN_NODE_5_PORT=6443"
    echo "VPN_NODE_5_NETWORK=tcp"
    echo "VPN_NODE_5_SECURITY=reality"
    echo "VPN_NODE_5_PUBLIC_KEY=${PUBLIC_KEY}"
    echo "VPN_NODE_5_SHORT_ID=${SHORT_ID}"
    echo "VPN_NODE_5_SNI=www.cloudflare.com"
    echo "VPN_NODE_5_FINGERPRINT=chrome"
    echo "VPN_NODE_5_FLOW=xtls-rprx-vision"
    echo "VPN_NODE_5_SPIDER_X=/"
    echo "VPN_NODE_5_FIXED_UUID=${UUID}"
    echo ""
    echo "# --- UKRAINE gRPC ---"
    echo "VPN_NODE_8_KEY=ua_grpc"
    echo "VPN_NODE_8_FLAG=🇺🇦"
    echo "VPN_NODE_8_NAME=UA Bypass (gRPC)"
    echo "VPN_NODE_8_PROFILE_NAME=\"Ukraine #5 (gRPC)\""
    echo "VPN_NODE_8_HOST=${IP}"
    echo "VPN_NODE_8_PORT=6445"
    echo "VPN_NODE_8_NETWORK=grpc"
    echo "VPN_NODE_8_SECURITY=reality"
    echo "VPN_NODE_8_PUBLIC_KEY=${PUBLIC_KEY}"
    echo "VPN_NODE_8_SHORT_ID=${SHORT_ID}"
    echo "VPN_NODE_8_SNI=www.cloudflare.com"
    echo "VPN_NODE_8_FINGERPRINT=chrome"
    echo "VPN_NODE_8_FLOW="
    echo "VPN_NODE_8_PATH=grpc-ua"
    echo "VPN_NODE_8_FIXED_UUID=${UUID}"
    echo "=========================================================="
else
    echo "[ERROR] Xray ne smog zapustitsya. Proverte journalctl -u xray -n 30"
    exit 1
fi
