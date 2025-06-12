import os
import json
import re
import requests
from datetime import datetime
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta, timezone

#coments

# Carrega variáveis do arquivo .env
load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CHANNEL_ID = os.getenv("DISCORD_CHANNEL_ID")
SHEET_ID = os.getenv("GOOGLE_SHEET_ID")

if not TOKEN or not CHANNEL_ID or not SHEET_ID:
    print("❌ Erro: Variáveis de ambiente DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID e/ou GOOGLE_SHEET_ID não estão definidas!")
    exit(1)

CREDENTIALS_FILE = "cabal-462702-23e95cf075a6.json"

def remove_emoji_prefix(line):
    # Remove prefixos emoji tipo :inbox_tray:
    line = re.sub(r"^:\w+?:\s*", "", line)
    # Remove símbolos não alfanuméricos do início (básico)
    line = re.sub(r"^[^\w\s]+", "", line)
    return line.strip()

def fetch_messages():
    url = f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages"
    headers = {
        "Authorization": f"Bot {TOKEN}"
    }

    print(f"⏳ Buscando mensagens no canal {CHANNEL_ID}...")

    try:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            print(f"Erro ao buscar mensagens: {response.status_code} {response.text}")
            return []

        messages = response.json()
        print(f"🔍 {len(messages)} mensagens encontradas.")

        # Filtrar só mensagens dos últimos 60 minutos
        now = datetime.now(timezone.utc)
        limit_time = now - timedelta(minutes=70)

        filtered_messages = []
        for msg in messages:
            msg_time = datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00"))
            if msg_time >= limit_time:
                filtered_messages.append(msg)

        print(f"⏳ {len(filtered_messages)} mensagens dentro dos últimos 60 minutos.")
        return filtered_messages

    except Exception as e:
        print(f"Erro na requisição: {e}")
        return []

def parse_item_line(line, entrada_saida):
    item = None
    raridade = None
    quantidade = 1
    nivel_aprimoramento = None
    classe = None
    slots = None

    line = line.strip()

    qtd_match = re.search(r"x(\d+)$", line)
    if qtd_match:
        quantidade = int(qtd_match.group(1))
        line = line[:qtd_match.start()].strip()

    slots_match = re.search(r"\[(.*?)\]", line)
    if slots_match:
        slots = slots_match.group(1)
        line = (line[:slots_match.start()] + line[slots_match.end():]).strip()

    classe_match = re.search(r"\(([A-Z]{2,3})\)", line)
    if classe_match:
        classe = classe_match.group(1)
        line = (line[:classe_match.start()] + line[classe_match.end():]).strip()

    raridade_match = re.search(r"\((Alto|Médio|Baixo|Altíssimo|Alto-médio)\)", line, re.IGNORECASE)
    if raridade_match:
        raridade = raridade_match.group(1)
        line = (line[:raridade_match.start()] + line[raridade_match.end():]).strip()

    nivel_match = re.search(r"\+(\d+)$", line)
    if nivel_match:
        nivel_aprimoramento = int(nivel_match.group(1))
        line = line[:nivel_match.start()].strip()

    item = line

    return {
        "item": item,
        "raridade": raridade,
        "quantidade": quantidade,
        "nivel_aprimoramento": nivel_aprimoramento,
        "classe": classe,
        "slots": slots,
        "entrada_saida": entrada_saida
    }

def extract_loot(messages):
    loot_list = []

    for msg in messages:
        embeds = msg.get("embeds", [])
        if not embeds:
            continue

        for embed in embeds:
            title = embed.get("title", "")
            description = embed.get("description", "")

            if title not in ["📦 Loot", "⚒️ Inventory Cleaner"]:
                continue

            tipo_extracao = "Loot" if title == "📦 Loot" else "Inventory Cleaner"
            lines = description.splitlines()

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Define entrada_saida e remove prefixos conforme regras
                if tipo_extracao == "Loot":
                    entrada_saida = "Entrada"
                    # No Loot remove prefixo emoji tipo :inbox_tray: se houver, mas não considera prefixo para entrada_saida
                    item_line = remove_emoji_prefix(line)
                else:
                    # Inventory Cleaner
                    if line.startswith(":inbox_tray:"):
                        entrada_saida = "Entrada"
                        item_line = remove_emoji_prefix(line)
                    elif line.startswith(":x:") or line.startswith("❌"):
                        entrada_saida = "Saida"
                        item_line = remove_emoji_prefix(line)
                    elif line.startswith("📤"):
                        # "Out" prefix (pode ser em negrito)
                        entrada_saida = "Saida"
                        item_line = line.lstrip("📤").strip()
                    elif line.startswith("📦"):
                        # "In" prefix (pode ser em negrito)
                        entrada_saida = "Entrada"
                        item_line = line.lstrip("📦").strip()
                    else:
                        entrada_saida = "Entrada"
                        item_line = remove_emoji_prefix(line)

                parsed = parse_item_line(item_line, entrada_saida)
                parsed["tipo_extracao"] = tipo_extracao
                parsed["data"] = msg.get("timestamp") or datetime.utcnow().isoformat()

                # Ignora linhas que sejam só indicadores do tipo In/Out, geralmente em negrito ou isolados
                if parsed["item"].lower() in ["**out**", "**in**", "out", "in"]:
                    continue

                loot_list.append(parsed)

    return loot_list

def save_data(data, filename="data/loot_log.json"):
    os.makedirs(os.path.dirname(filename), exist_ok=True)
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"💾 Dados salvos em {filename}")

def append_to_google_sheets(data):
    print("📄 Enviando dados para o Google Sheets...")
    try:
        scope = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID).worksheet("Dados")

        rows = []
        for item in data:
            rows.append([
                item["data"],
                item["tipo_extracao"],
                item["entrada_saida"],
                item["item"],
                item.get("raridade") or "",
                item.get("quantidade") or 1,
                item.get("nivel_aprimoramento") or "",
                item.get("classe") or "",
                item.get("slots") or ""
            ])

        sheet.append_rows(rows, value_input_option="RAW")
        print(f"✅ {len(rows)} linhas adicionadas ao Google Sheets.")
    except Exception as e:
        print(f"❌ Erro ao atualizar Google Sheets: {e}")

def main():
    messages = fetch_messages()
    if not messages:
        print("🔍 0 mensagens encontradas.")
        return

    print("⏳ Extraindo itens...")
    loot = extract_loot(messages)
    print(f"🔍 {len(loot)} itens extraídos.")

    if loot:
        save_data(loot)
        append_to_google_sheets(loot)

if __name__ == "__main__":
    main()
