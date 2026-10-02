#!/usr/bin/env bash
# ==============================================================================
# Dobavlyaet na vhodnuyu nodu otdelnyy inbound, s kotorogo rossiyskiy trafik
# uhodit cepochkoy na RU-nodu, a vse ostalnoe - naprymuyu s etoy zhe nody.
#
#   klient (UA) -> eta noda :6444 -+-> RU-noda -> sberbank.ru
#                                  \-> direct   -> vse ostalnoe
#
# Sushchestvuyushchie inbound'y nody NE trogaet: config.json patchitsya cherez
# jq, a pravila privyazany k inboundTag, tak chto na drugih portah nichego
# ne menyaetsya. Zapuskat mozhno povtorno - staryy blok zamenyaetsya.
#
# Zapusk s lokalnoy mashiny:
#   ssh root@185.23.19.198 "RU_UUID=<uuid> RU_PBK=<pbk> bash -s" < deploy_ru_chain.sh
#
#   RU_UUID - UUID klienta, sozdannogo na RU-paneli v inbound 3 (email lyuboy,
#             naprimer tgpay-chain). Eto sluzhebnyy klient dlya cepochki.
#   RU_PBK  - VPN_NODE_11_PUBLIC_KEY iz .env
# ==============================================================================

set -euo pipefail

RU_HOST="${RU_HOST:-176.109.108.224}"
RU_PORT="${RU_PORT:-6443}"
RU_SNI="${RU_SNI:-ya.ru}"
RU_SID="${RU_SID:-66b601}"
RU_FLOW="${RU_FLOW:-}"
RU_UUID="${RU_UUID:?nuzhen RU_UUID - klient na RU-paneli, inbound 3}"
RU_PBK="${RU_PBK:?nuzhen RU_PBK - VPN_NODE_11_PUBLIC_KEY}"

PORT="${PORT:-6444}"
UUID="${UUID:-69d4388f-ae2d-45cb-acc3-775c6c6dbcea}"   # tot zhe fiksirovannyy UUID TgPay
DEST="${DEST:-www.cloudflare.com}"
CONFIG="${CONFIG:-/usr/local/etc/xray/config.json}"

echo "=== [1/6] Proverka okruzheniya ==="
command -v jq >/dev/null || { apt-get update -y && apt-get install -y jq; }
command -v /usr/local/bin/xray >/dev/null || { echo "[ERROR] Xray ne nayden - eto ne nasha noda"; exit 1; }
[ -f "$CONFIG" ] || { echo "[ERROR] net $CONFIG"; exit 1; }

echo "=== [2/6] Proverka svyazi s RU-nodoy ==="
if timeout 6 bash -c "cat < /dev/null > /dev/tcp/${RU_HOST}/${RU_PORT}" 2>/dev/null; then
    echo "    ${RU_HOST}:${RU_PORT} dostupen"
else
    echo "[ERROR] s etoy nody ne vidno ${RU_HOST}:${RU_PORT} - cepochka ne zarabotaet."
    echo "        Poprobuyte druguyu vhodnuyu nodu. Nichego ne izmeneno."
    exit 1
fi

echo "=== [3/6] Geodata (geoip.dat / geosite.dat) ==="
if [ ! -f /usr/local/share/xray/geosite.dat ]; then
    bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install-geodata
fi

echo "=== [4/6] Generaciya klyuchey Reality dlya novogo inbound ==="
KEYPAIR=$(/usr/local/bin/xray x25519)
PRIVATE_KEY=$(echo "$KEYPAIR" | grep "PrivateKey" | awk '{print $2}')
PUBLIC_KEY=$(echo "$KEYPAIR" | grep "Password (PublicKey)" | awk '{print $3}')
SHORT_ID=$(openssl rand -hex 4)
IP=$(curl -s -4 ifconfig.me || curl -s -4 icanhazip.com)

echo "=== [5/6] Patch $CONFIG ==="
BLOCK=$(jq -n \
  --arg uuid "$UUID" --arg pk "$PRIVATE_KEY" --arg sid "$SHORT_ID" --arg dest "$DEST" \
  --argjson port "$PORT" \
  --arg ruhost "$RU_HOST" --argjson ruport "$RU_PORT" --arg ruuuid "$RU_UUID" \
  --arg rupbk "$RU_PBK" --arg rusni "$RU_SNI" --arg rusid "$RU_SID" --arg ruflow "$RU_FLOW" '
{
  inbound: {
    tag: "vless-ru-chain",
    listen: "0.0.0.0",
    port: $port,
    protocol: "vless",
    settings: {
      clients: [{ id: $uuid, flow: "xtls-rprx-vision", email: "tgpay-ru-chain" }],
      decryption: "none"
    },
    streamSettings: {
      network: "tcp",
      security: "reality",
      realitySettings: {
        show: false,
        dest: ($dest + ":443"),
        xver: 0,
        serverNames: [$dest],
        privateKey: $pk,
        shortIds: [$sid]
      }
    },
    sniffing: { enabled: true, destOverride: ["http", "tls", "quic"] }
  },
  outbound: {
    protocol: "vless",
    tag: "ru-out",
    settings: {
      vnext: [{
        address: $ruhost,
        port: $ruport,
        users: [{ id: $ruuuid, encryption: "none", flow: $ruflow }]
      }]
    },
    streamSettings: {
      network: "tcp",
      security: "reality",
      realitySettings: {
        serverName: $rusni,
        publicKey: $rupbk,
        shortId: $rusid,
        fingerprint: "chrome",
        spiderX: "/"
      }
    }
  },
  rules: [
    { type: "field", inboundTag: ["vless-ru-chain"], domain: ["geosite:category-ru"], outboundTag: "ru-out" },
    { type: "field", inboundTag: ["vless-ru-chain"], ip: ["geoip:ru"], outboundTag: "ru-out" },
    { type: "field", inboundTag: ["vless-ru-chain"], outboundTag: "direct" }
  ]
}')

# Idempotentno: snachala vykidyvaem svoi staryye kuski, potom dobavlyaem zanovo.
# Nash inbound polnostyu opisan tremya svoimi pravilami (RU -> ru-out, ostalnoe
# -> direct), poetomu ne zavisim ot togo, kakoy outbound na etoy mashine pervyy.
# Chuzhie pravila stoyat posle nashih i nashego inbound'a ne kasayutsya.
# Vazhno: xray opredelyaet format konfiga PO RASSHIRENIYU, poetomu vremennyy
# fayl dolzhen byt *.json. Derzhim ego v /tmp, chtoby on ne popal v katalog
# konfigov (na sluchay, esli xray zapuschen s -confdir).
TMPDIR_NEW=$(mktemp -d)
NEWCFG="${TMPDIR_NEW}/config.json"
trap 'rm -rf "$TMPDIR_NEW"' EXIT

jq --argjson n "$BLOCK" '
    .inbounds  = ((.inbounds  // []) | map(select(.tag != "vless-ru-chain"))) + [$n.inbound]
  | .outbounds = ((.outbounds // []) | map(select(.tag != "ru-out")))         + [$n.outbound]
  | .routing.domainStrategy = (.routing.domainStrategy // "IPIfNonMatch")
  | .routing.rules = $n.rules
      + ((.routing.rules // []) | map(select((.inboundTag // []) | index("vless-ru-chain") | not)))
' "$CONFIG" > "$NEWCFG"

/usr/local/bin/xray run -test -c "$NEWCFG" >/dev/null || {
    echo "[ERROR] novyy config ne proshel proverku - staryy ostalsya na meste"
    exit 1
}

cp -a "$CONFIG" "${CONFIG}.bak_$(date +%Y%m%d_%H%M%S)"
cat "$NEWCFG" > "$CONFIG"

if ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow ${PORT}/tcp
fi

echo "=== [6/6] Perezapusk Xray ==="
systemctl restart xray
sleep 1
systemctl is-active --quiet xray || { echo "[ERROR] Xray ne podnyalsya: journalctl -u xray -n 30"; exit 1; }

echo "=========================================================="
echo "  [SUCCESS] Cepochka podnyata na ${IP}:${PORT}"
echo "=========================================================="
echo ""
echo "Skopiruyte v .env bota:"
echo ""
echo "VPN_NODE_12_KEY=ru_chain"
echo "VPN_NODE_12_FLAG=🇷🇺"
echo "VPN_NODE_12_NAME=RU Access (chain)"
echo "VPN_NODE_12_PROFILE_NAME=\"Доступ к РФ Банкам\""
echo "VPN_NODE_12_HOST=${IP}"
echo "VPN_NODE_12_PORT=${PORT}"
echo "VPN_NODE_12_NETWORK=tcp"
echo "VPN_NODE_12_SECURITY=reality"
echo "VPN_NODE_12_PUBLIC_KEY=${PUBLIC_KEY}"
echo "VPN_NODE_12_SHORT_ID=${SHORT_ID}"
echo "VPN_NODE_12_SNI=${DEST}"
echo "VPN_NODE_12_FINGERPRINT=chrome"
echo "VPN_NODE_12_FLOW=xtls-rprx-vision"
echo "VPN_NODE_12_SPIDER_X=/"
echo "VPN_NODE_12_FIXED_UUID=${UUID}"
echo "VPN_NODE_12_ROUTING=ru_proxy"
echo "=========================================================="
