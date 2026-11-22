import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, List

import redis.asyncio as redis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from pydantic import ValidationError

from app.schemas import ChatMessage, ClientPayload, ServerEvent, USERNAME_REGEX

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
PUBSUB_CHANNEL = "chat:global"
HISTORY_KEY = "chat:historico"
MAX_HISTORY_SIZE = 50
INSTANCE_ID = os.getenv("INSTANCE_ID", "Instancia-A")

# Configuração de logs estruturados no console
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("chat_distribuido")


def log_evento(evento: str, **detalhes):
    """Imprime logs operacionais estruturados no formato chave=valor."""
    campos = " | ".join(f"{k}={v}" for k, v in detalhes.items())
    logger.info(f"instancia={INSTANCE_ID} | evento={evento}" + (f" | {campos}" if campos else ""))


class ConnectionManager:
    def __init__(self):
        # Mapeia cada socket ativo na memória desta instância ao nome do usuário
        self.active_connections: Dict[WebSocket, str] = {}

    async def connect(self, websocket: WebSocket, usuario: str):
        await websocket.accept()
        self.active_connections[websocket] = usuario

    def disconnect(self, websocket: WebSocket):
        self.active_connections.pop(websocket, None)

    async def broadcast_local(self, message_json: str) -> int:
        """Envia o evento para todos os clientes conectados NESTA instância."""
        enviados = 0
        for connection in list(self.active_connections.keys()):
            try:
                await connection.send_text(message_json)
                enviados += 1
            except Exception:
                self.disconnect(connection)
        return enviados


manager = ConnectionManager()
redis_client: redis.Redis = None


async def redis_listener():
    """Escuta continuamente o canal Pub/Sub do Redis e repassa aos clientes locais."""
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(PUBSUB_CHANNEL)
    log_evento("PUBSUB_INSCRITO", canal=PUBSUB_CHANNEL)

    async for message in pubsub.listen():
        if message["type"] == "message":
            data_str = message["data"]
            try:
                tipo_evento = json.loads(data_str).get("evento", "desconhecido")
            except json.JSONDecodeError:
                tipo_evento = "invalido"

            entregues = await manager.broadcast_local(data_str)
            log_evento(
                "PUBSUB_RECEBIDO_E_BROADCAST",
                canal=PUBSUB_CHANNEL,
                tipo=tipo_evento,
                clientes_locais_notificados=entregues,
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global redis_client
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    await redis_client.ping()
    log_evento("SERVIDOR_INICIADO", redis_url=REDIS_URL, historico_n=MAX_HISTORY_SIZE)

    listener_task = asyncio.create_task(redis_listener())
    yield

    listener_task.cancel()
    await redis_client.aclose()
    log_evento("SERVIDOR_ENCERRADO")


app = FastAPI(title=f"Chat Distribuído - {INSTANCE_ID}", lifespan=lifespan)


@app.get("/health")
async def health_check():
    """Rota HTTP para verificar a saúde da instância e os clientes conectados nela."""
    redis_ok = await redis_client.ping()
    usuarios_locais = list(manager.active_connections.values())
    log_evento("HEALTH_CHECK", redis_ok=redis_ok, clientes_locais=len(usuarios_locais))
    return {
        "status": "online",
        "instancia": INSTANCE_ID,
        "redis_conectado": redis_ok,
        "clientes_conectados_localmente": len(usuarios_locais),
        "usuarios_locais": usuarios_locais,
    }


async def salvar_no_historico(msg: ChatMessage):
    """Salva a mensagem na lista do Redis e mantém apenas as últimas N mensagens."""
    await redis_client.rpush(HISTORY_KEY, msg.model_dump_json())
    await redis_client.ltrim(HISTORY_KEY, -MAX_HISTORY_SIZE, -1)


async def obter_historico() -> List[dict]:
    """Recupera as últimas N mensagens armazenadas no Redis."""
    raw_messages = await redis_client.lrange(HISTORY_KEY, 0, -1)
    return [json.loads(m) for m in raw_messages]


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket, usuario: str = Query(..., description="Nome de usuário")
):
    usuario_limpo = usuario.strip()

    # 1. Validação do nome de usuário
    if not USERNAME_REGEX.match(usuario_limpo):
        await websocket.accept()
        log_evento("CONEXAO_REJEITADA", usuario_tentado=usuario_limpo, motivo="formato_invalido")
        erro = ServerEvent(
            evento="erro",
            dados={"mensagem": "Usuário inválido. Use 3-20 caracteres (letras, números, _ ou -)."},
        )
        await websocket.send_text(erro.model_dump_json())
        await websocket.close(code=1008)
        return

    # 2. Aceita a conexão WebSocket
    await manager.connect(websocket, usuario_limpo)
    log_evento(
        "USUARIO_ENTROU",
        usuario=usuario_limpo,
        clientes_locais=len(manager.active_connections),
    )

    # 3. Envia histórico das últimas N mensagens apenas para o novo cliente
    historico = await obter_historico()
    evento_hist = ServerEvent(evento="historico", dados=historico)
    await websocket.send_text(evento_hist.model_dump_json())
    log_evento("HISTORICO_ENVIADO", usuario=usuario_limpo, quantidade=len(historico))

    # 4. Publica entrada do usuário no canal Pub/Sub para todas as instâncias
    evento_entrou = ServerEvent(
        evento="usuario_entrou",
        dados={
            "usuario": usuario_limpo,
            "instancia": INSTANCE_ID,
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
        },
    )
    await redis_client.publish(PUBSUB_CHANNEL, evento_entrou.model_dump_json())

    # 5. Loop principal de recebimento de mensagens do cliente
    try:
        while True:
            raw_text = await websocket.receive_text()
            try:
                payload_dict = json.loads(raw_text)
                payload = ClientPayload(**payload_dict)

                nova_msg = ChatMessage(
                    usuario=usuario_limpo,
                    texto=payload.texto,
                    instancia_origem=INSTANCE_ID,
                )

                await salvar_no_historico(nova_msg)
                evento_msg = ServerEvent(
                    evento="nova_mensagem", dados=nova_msg.model_dump()
                )
                await redis_client.publish(PUBSUB_CHANNEL, evento_msg.model_dump_json())

                log_evento(
                    "MENSAGEM_PUBLICADA",
                    usuario=usuario_limpo,
                    tamanho_texto=len(nova_msg.texto),
                    canal=PUBSUB_CHANNEL,
                )

            except (json.JSONDecodeError, ValidationError) as e:
                log_evento("ERRO_VALIDACAO", usuario=usuario_limpo, motivo="payload_invalido")
                erro_validacao = ServerEvent(
                    evento="erro",
                    dados={"mensagem": "Mensagem inválida ou fora do limite (1 a 500 caracteres)."},
                )
                await websocket.send_text(erro_validacao.model_dump_json())

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        log_evento(
            "USUARIO_SAIU",
            usuario=usuario_limpo,
            clientes_locais=len(manager.active_connections),
        )
        evento_saiu = ServerEvent(
            evento="usuario_saiu",
            dados={
                "usuario": usuario_limpo,
                "instancia": INSTANCE_ID,
                "timestamp_iso": datetime.now(timezone.utc).isoformat(),
            },
        )
        await redis_client.publish(PUBSUB_CHANNEL, evento_saiu.model_dump_json())