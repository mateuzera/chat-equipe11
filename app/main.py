import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import List

import redis.asyncio as redis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from pydantic import ValidationError

from app.schemas import ChatMessage, ClientPayload, ServerEvent, USERNAME_REGEX

# configurações do Redis e da Instância
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
PUBSUB_CHANNEL = "chat:global"
HISTORY_KEY = "chat:historico"
MAX_HISTORY_SIZE = 50  # O valor de N (últimas 50 mensagens) pedido pelo professor
INSTANCE_ID = os.getenv("INSTANCE_ID", "Instancia-Indefinida")

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast_local(self, message_json: str):
        """Manda a mensagem para quem está conectado NESTA porta/instância"""
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message_json)
            except Exception:
                self.disconnect(connection)

manager = ConnectionManager()
redis_client: redis.Redis = None

async def redis_listener():
    """Fica escutando o Redis. Se chegar mensagem de outra instância, repassa pros clientes locais"""
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(PUBSUB_CHANNEL)
    print(f"[{INSTANCE_ID}] Escutando o canal Pub/Sub: {PUBSUB_CHANNEL}")
    
    async for message in pubsub.listen():
        if message["type"] == "message":
            await manager.broadcast_local(message["data"])

@asynccontextmanager
async def lifespan(app: FastAPI):
    # liga no Redis quando a API sobe
    global redis_client
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    await redis_client.ping()
    print(f"[{INSTANCE_ID}] Conectado ao Redis!")
    
    # inicia a tarefa que fica escutando as mensagens em background
    task = asyncio.create_task(redis_listener())
    yield
    # desliga tudo bonitinho quando a API cai
    task.cancel()
    await redis_client.close()

app = FastAPI(title=f"Chat Distribuído - {INSTANCE_ID}", lifespan=lifespan)

@app.get("/health")
async def health_check():
    """Rota de teste que o professor pediu no esqueleto base"""
    return {"status": "ok", "instancia": INSTANCE_ID}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, usuario: str = Query(...)):
    # valida o nome de usuário (rejeita se for inválido)
    usuario_limpo = usuario.strip()
    if not USERNAME_REGEX.match(usuario_limpo):
        await websocket.accept()
        erro = ServerEvent(evento="erro", dados={"mensagem": "Usuário inválido (use 3-20 letras/números)."})
        await websocket.send_text(erro.model_dump_json())
        await websocket.close(code=1008)
        return

    await manager.connect(websocket)

    # puxa o histórico do Redis e manda pro usuário que acabou de entrar
    mensagens_salvas = await redis_client.lrange(HISTORY_KEY, 0, -1)
    historico = [json.loads(m) for m in mensagens_salvas]
    
    evento_hist = ServerEvent(evento="historico", dados=historico)
    await websocket.send_text(evento_hist.model_dump_json())

    # avisa no Redis que o usuário entrou
    evento_entrou = ServerEvent(evento="usuario_entrou", dados={"usuario": usuario_limpo, "instancia": INSTANCE_ID})
    await redis_client.publish(PUBSUB_CHANNEL, evento_entrou.model_dump_json())

    try:
        while True:
            # fica esperando o usuário digitar algo
            raw_text = await websocket.receive_text()
            try:
                # valida o que o usuário mandou usando nossos Schemas
                payload = ClientPayload(**json.loads(raw_text))
                nova_msg = ChatMessage(usuario=usuario_limpo, texto=payload.texto, instancia_origem=INSTANCE_ID)

                # salva no Redis (RPUSH) e corta pra manter só as últimas 50 (LTRIM)
                await redis_client.rpush(HISTORY_KEY, nova_msg.model_dump_json())
                await redis_client.ltrim(HISTORY_KEY, -MAX_HISTORY_SIZE, -1)
                
                # publica no Pub/Sub pro Brasil todo ver (Instância A e B)
                evento_msg = ServerEvent(evento="nova_mensagem", dados=nova_msg.model_dump())
                await redis_client.publish(PUBSUB_CHANNEL, evento_msg.model_dump_json())

            except (json.JSONDecodeError, ValidationError) as e:
                erro = ServerEvent(evento="erro", dados={"mensagem": "Mensagem inválida."})
                await websocket.send_text(erro.model_dump_json())

    except WebSocketDisconnect:
        # avisa no Redis quando o usuário fechar a aba
        manager.disconnect(websocket)
        evento_saiu = ServerEvent(evento="usuario_saiu", dados={"usuario": usuario_limpo, "instancia": INSTANCE_ID})
        await redis_client.publish(PUBSUB_CHANNEL, evento_saiu.model_dump_json())