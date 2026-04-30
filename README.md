# API Monitoramento

API em `FastAPI` para monitorar servidores Linux e containers Docker via SSH. Ela salva snapshots de carga, memoria, disco, uptime e estado dos containers, e executa coletas automaticas em background.

## Stack escolhida

- `FastAPI`: API rapida, tipada e com documentacao automatica em `/docs`.
- `AsyncSSH`: coleta paralela via SSH sem precisar instalar agente nos servidores.
- `SQLAlchemy + SQLite`: persistencia simples para comecar, pronta para trocar para PostgreSQL depois.
- `cryptography`: criptografa senha SSH e chave privada em repouso.

## O que esta versao monitora

- `load average` e `load por core`
- uso de memoria
- uso de disco de um caminho configuravel
- uptime
- containers Docker ativos ou parados
- containers esperados ausentes
- healthcheck Docker quando estiver disponivel no `Status`
- erros encontrados nos logs dos containers monitorados
- automacao de remediacao por palavra-chave em log com historico persistido

## Limitacoes assumidas

- alvo principal: servidores Linux com acesso SSH
- monitoramento de containers usa a CLI `docker` instalada no servidor remoto
- o projeto nao usa agente local no host remoto

## Como rodar

1. Crie um ambiente virtual e instale as dependencias:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

2. Copie o arquivo de exemplo:

```powershell
Copy-Item .env.example .env
```

3. Suba a API:

```powershell
uvicorn app.main:app --reload
```

4. Abra:

- `http://localhost:8000/docs`
- `http://localhost:8000/health`

## Exemplo de cadastro de servidor

```json
{
  "name": "producao-app-01",
  "host": "10.0.0.15",
  "port": 22,
  "username": "ubuntu",
  "ssh_auth_mode": "private_key",
  "ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\n...\n-----END OPENSSH PRIVATE KEY-----",
  "monitor_docker": true,
  "watch_all_containers": false,
  "expected_containers": ["api", "worker", "nginx"],
  "monitor_container_logs": true,
  "log_monitored_containers": ["api", "worker"],
  "log_tail_lines": 200,
  "log_error_patterns": ["error", "exception", "traceback", "fatal"],
  "automation_enabled": true,
  "automation_target_container": "worker",
  "automation_trigger_pattern": "untrusted",
  "automation_command": "sh reiniciar_certificado.sh",
  "automation_cooldown_seconds": 600,
  "root_disk_path": "/",
  "warning_disk_percent": 80,
  "critical_disk_percent": 90,
  "warning_memory_percent": 80,
  "critical_memory_percent": 90,
  "warning_load_per_core": 0.7,
  "critical_load_per_core": 1.0
}
```

Se preferir senha SSH, envie `ssh_auth_mode: "password"` com `ssh_password`.

## Monitoramento de logs

Para alertar erros de aplicacao vindos dos logs dos containers, configure estes campos no servidor:

- `monitor_container_logs=true`
- `log_monitored_containers`: nomes dos containers que devem ter logs inspecionados
- `log_tail_lines`: quantidade de linhas recentes que a API vai ler em cada coleta
- `log_error_patterns`: palavras ou regex usadas para detectar erros

Quando houver matches, o snapshot passa a retornar:

- `log_alerts_total`
- `log_alerts[]` com o nome do container, quantidade de ocorrencias, padroes encontrados, trechos de log e falhas de coleta
- `automation_events[]` quando uma automacao de remediacao for disparada no mesmo snapshot

Observacao:

- o monitoramento de logs depende de `monitor_docker=true`
- se `monitor_container_logs=true`, e obrigatorio informar ao menos um item em `log_monitored_containers`

## Automacao por log

Se quiser reagir automaticamente a um erro especifico de log, configure estes campos no servidor:

- `automation_enabled=true`
- `automation_target_container`: container que dispara a remediacao
- `automation_trigger_pattern`: palavra ou regex que deve aparecer no log, por exemplo `untrusted`
- `automation_command`: comando remoto a executar no mesmo host monitorado
- `automation_cooldown_seconds`: janela minima entre tentativas novas com assinaturas diferentes

Comportamento desta versao:

- a API executa a automacao no mesmo servidor monitorado via SSH
- o mesmo trecho de log nao dispara a automacao repetidamente
- toda tentativa fica salva em `automation_events`, com horario, comando, status, erro e trecho do log que causou a acao
- o servidor tambem passa a expor `last_automation_at` e `last_automation_status`

## Endpoints principais

- `GET /api/dashboard`: resumo geral
- `POST /api/servers`: cadastra um servidor
- `GET /api/servers`: lista servidores cadastrados
- `PATCH /api/servers/{id}`: atualiza thresholds ou credenciais
- `POST /api/servers/{id}/collect`: forca uma coleta imediata
- `GET /api/servers/{id}/snapshots`: historico recente
- `GET /api/automation-events`: historico global das automacoes
- `GET /api/servers/{id}/automation-events`: historico da automacao de um servidor
- `GET /api/servers/{id}/automation-status`: mostra se a automacao esta `active`, `paused` ou `misconfigured`
- `POST /api/servers/{id}/automation/activate`: ativa a automacao configurada para o servidor
- `POST /api/servers/{id}/automation/pause`: pausa a automacao sem apagar a configuracao

Observacao:

- `GET /api/servers` e `GET /api/dashboard` agora tambem retornam `automation_status`, `automation_active`, `automation_configured` e `automation_status_reason` em cada servidor

Se `API_KEY` estiver definida no `.env`, envie o header `X-API-Key`.

Se algum host tiver muitos containers ou Docker mais lento, voce pode aumentar so o timeout da leitura de containers:

```env
DOCKER_COMMAND_TIMEOUT_SECONDS=45
DOCKER_LOGS_COMMAND_TIMEOUT_SECONDS=120
DOCKER_LOGS_FALLBACK_TAIL_LINES=50
AUTOMATION_COMMAND_TIMEOUT_SECONDS=120
AUTOMATION_HISTORY_LIMIT_PER_SERVER=500
```

`DOCKER_LOGS_COMMAND_TIMEOUT_SECONDS` controla apenas a leitura de logs.
`DOCKER_LOGS_FALLBACK_TAIL_LINES` define quantas linhas tentar no retry automatico quando `docker logs` estourar tempo.
`AUTOMATION_COMMAND_TIMEOUT_SECONDS` controla o tempo maximo do comando corretivo remoto.
`AUTOMATION_HISTORY_LIMIT_PER_SERVER` limita quantos eventos de automacao ficam salvos por servidor.

## CORS

O backend ja vem com CORS habilitado para desenvolvimento local, incluindo estas origens:

- `http://localhost:3000`
- `http://localhost:5173`
- `http://localhost:4173`
- `http://127.0.0.1:3000`
- `http://127.0.0.1:5173`
- `http://127.0.0.1:4173`

Tambem existe suporte a front remoto em dominio Lovable via `CORS_ALLOWED_ORIGIN_REGEX`.

Se precisar customizar, ajuste no `.env`:

```env
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:5173
CORS_ALLOWED_ORIGIN_REGEX=https://.*\.lovable\.app
CORS_ALLOW_CREDENTIALS=false
```

Depois de mudar essas variaveis no Docker, reinicie o container.

## Docker

```powershell
docker compose up --build
```

No `docker-compose.yml`, o projeto ja esta configurado para desenvolvimento com reload automatico:

- alteracoes em `app/` reiniciam a API automaticamente
- a pasta local `data/` continua persistindo banco e chave
- em Docker Desktop no Windows, `WATCHFILES_FORCE_POLLING=true` ajuda o reload a detectar mudancas

Fluxo recomendado:

```powershell
Copy-Item .env.example .env
docker compose up --build
```

Depois disso, sempre que voce mudar arquivos em `app/`, o container recarrega sozinho. So precisa rebuildar se mudar dependencias, `Dockerfile` ou `pyproject.toml`:

```powershell
docker compose up --build
```

## Evolucao recomendada

Para producao em maior escala, eu recomendo manter esta API como camada de cadastro/consulta e depois acoplar:

- `PostgreSQL` no lugar do SQLite
- filas ou workers dedicados para alta volumetria
- Prometheus e Grafana para series temporais e dashboards mais ricos
