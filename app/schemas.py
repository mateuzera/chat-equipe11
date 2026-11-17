from datetime import datetime, timezone
from typing import Literal, Optional, Any
from pydantic import BaseModel, Field, field_validator
import re

# regra do nome de usuário: letras, números, _ ou -, de 3 a 20 caracteres
USERNAME_REGEX = re.compile(r"^[a-zA-Z0-9_-]{3,20}$")

class ChatMessage(BaseModel):
    usuario: str = Field(..., min_length=3, max_length=20)
    # vai gerar a data/hora automaticamente no formato ISO UTC quando a mensagem for criada
    timestamp_iso: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    texto: str = Field(..., min_length=1, max_length=500)
    instancia_origem: Optional[str] = None

    @field_validator("usuario")
    @classmethod
    def validar_usuario(cls, v: str) -> str:
        v = v.strip() # Remove espaços em branco no começo e fim
        if not USERNAME_REGEX.match(v):
            raise ValueError(
                "Nome de usuário deve ter entre 3 e 20 caracteres (letras, números, _ ou -)."
            )
        return v

    @field_validator("texto")
    @classmethod
    def validar_texto(cls, v: str) -> str:
        v = v.strip() # Remove espaços em branco no começo e fim
        if not v:
            raise ValueError("O texto da mensagem não pode ser vazio.")
        return v

# molde do que o cliente (navegador) envia para o servidor
class ClientPayload(BaseModel):
    evento: Literal["enviar_mensagem"]
    texto: str = Field(..., min_length=1, max_length=500)

# molde do que o servidor envia de volta para o cliente (navegador)
class ServerEvent(BaseModel):
    evento: Literal[
        "historico", "nova_mensagem", "usuario_entrou", "usuario_saiu", "erro"
    ]
    dados: Any