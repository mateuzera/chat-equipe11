import asyncio
import json
import websockets

async def testar_chat():
    # simulando dois usuários em instâncias diferentes
    uri_a = "ws://localhost:8001/ws?usuario=UserDaInstanciaA"
    uri_b = "ws://localhost:8002/ws?usuario=UserDaInstanciaB"

    print("Conectando clientes...")
    async with websockets.connect(uri_a) as ws_a, websockets.connect(uri_b) as ws_b:
        
        # ignora as mensagens iniciais (histórico)
        await ws_b.recv() 
        
        print("Enviando mensagem pela porta 8001...")
        payload = {"evento": "enviar_mensagem", "texto": "Fala grupo 11! Mensagem via Pub/Sub funcionando!"}
        await ws_a.send(json.dumps(payload))

        # fica escutando a porta 8002 pra ver se chega
        while True:
            resposta = await ws_b.recv()
            dados = json.loads(resposta)
            if dados.get("evento") == "nova_mensagem":
                print("\n[SUCESSO!] Mensagem chegou na porta 8002:")
                print(json.dumps(dados["dados"], indent=2, ensure_ascii=False))
                break

if __name__ == "__main__":
    asyncio.run(testar_chat())