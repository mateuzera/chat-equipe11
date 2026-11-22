import asyncio
import json
import sys
from datetime import datetime
import websockets


def formatar_hora(timestamp_iso: str) -> str:
    """Converte timestamp ISO UTC para hora local (HH:MM:SS)."""
    try:
        dt = datetime.fromisoformat(timestamp_iso).astimezone()
        return dt.strftime("%H:%M:%S")
    except Exception:
        return "--:--:--"


def exibir_evento(dados_json: str) -> str:
    """Formata e imprime no console os eventos recebidos do servidor."""
    pacote = json.loads(dados_json)
    evento = pacote.get("evento")
    dados = pacote.get("dados")

    if evento == "historico":
        print(f"\n=== Histórico Recente ({len(dados)} mensagens) ===")
        if not dados:
            print("  (Nenhuma mensagem anterior na sala)")
        for msg in dados:
            hora = formatar_hora(msg.get("timestamp_iso", ""))
            usuario = msg.get("usuario")
            instancia = msg.get("instancia_origem", "?")
            texto = msg.get("texto")
            print(f"[{hora}] {usuario} ({instancia}): {texto}")
        print("==========================================\n")

    elif evento == "nova_mensagem":
        hora = formatar_hora(dados.get("timestamp_iso", ""))
        usuario = dados.get("usuario")
        instancia = dados.get("instancia_origem", "?")
        texto = dados.get("texto")
        print(f"\r[{hora}] {usuario} ({instancia}): {texto}")

    elif evento == "usuario_entrou":
        hora = formatar_hora(dados.get("timestamp_iso", ""))
        print(f"\r>>> [{hora}] {dados.get('usuario')} entrou na sala (via {dados.get('instancia')})")

    elif evento == "usuario_saiu":
        hora = formatar_hora(dados.get("timestamp_iso", ""))
        print(f"\r<<< [{hora}] {dados.get('usuario')} saiu da sala (via {dados.get('instancia')})")

    elif evento == "erro":
        print(f"\r[ERRO] {dados.get('mensagem')}")

    return evento


async def receber_mensagens(ws):
    """Tarefa concorrente que escuta continuamente eventos vindos do servidor."""
    try:
        async for mensagem in ws:
            exibir_evento(mensagem)
    except websockets.exceptions.ConnectionClosed:
        print("\n[Sistema] Conexão encerrada pelo servidor.")


async def enviar_mensagens(ws):
    """Tarefa concorrente que lê mensagens do teclado sem bloquear o recebimento."""
    print("Chat pronto! Digite sua mensagem e pressione Enter (use /ajuda ou /sair).\n")
    while True:
        texto = await asyncio.to_thread(input)
        texto = texto.strip()

        if not texto:
            continue

        if texto.lower() == "/sair":
            print("[Sistema] Saindo do chat...")
            await ws.close()
            break

        if texto.lower() == "/ajuda":
            print("[Ajuda] Digite qualquer texto para enviar | /sair para desconectar.")
            continue

        payload = {"evento": "enviar_mensagem", "texto": texto}
        await ws.send(json.dumps(payload))


async def main():
    # Permite passar porta e usuário por argumento ou digitar interativamente
    # Exemplo: python client_cli.py 8001 Mateus
    if len(sys.argv) >= 3:
        porta = sys.argv[1]
        usuario = sys.argv[2]
    else:
        print("=== Cliente de Chat Distribuído (Equipe 11) ===")
        porta = input("Porta da instância (8001 para Instância A / 8002 para Instância B) [8001]: ").strip() or "8001"
        usuario = input("Digite seu nome de usuário (3-20 caracteres): ").strip()

    uri = f"ws://localhost:{porta}/ws?usuario={usuario}"
    print(f"Conectando em {uri}...")

    try:
        async with websockets.connect(uri) as ws:
            # Lê a primeira resposta do servidor (histórico ou erro de validação)
            primeira_msg = await ws.recv()
            evento_inicial = exibir_evento(primeira_msg)

            if evento_inicial == "erro":
                return

            # Inicia recebimento e envio de forma concorrente
            tarefa_receber = asyncio.create_task(receber_mensagens(ws))
            tarefa_enviar = asyncio.create_task(enviar_mensagens(ws))

            done, pending = await asyncio.wait(
                [tarefa_receber, tarefa_enviar],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

    except ConnectionRefusedError:
        print(f"[ERRO] Não foi possível conectar na porta {porta}. Verifique se a instância está rodando.")
    except Exception as e:
        print(f"[ERRO] Falha na conexão: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[Sistema] Cliente finalizado.")