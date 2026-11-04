# Arquitetura do `cftv_sim` (Etapa 0)

Simulador de equipamentos CFTV (Hikvision e Intelbras) a partir da webcam do notebook.
**Uso exclusivo em laboratório/estudo.** Nenhuma credencial real, nenhuma engenharia reversa
de protocolo proprietário (SDK 8000 Hikvision e 37777 Intelbras são apenas listeners que registram conexões).

## 1. Diagrama de fluxo

```
                        ┌──────────────────────────────────────────────────────────┐
                        │                     PROCESSO ÚNICO (Python)              │
                        │                                                          │
 Webcam ─► CaptureService (thread)  ──frame base──┐                                │
 (ou padrão de teste)    cv2.VideoCapture(0)      │                                │
                                                  ▼                                │
                              ┌──────────── VirtualCamera x N (1..5) ───────────┐  │
                              │ espelho/zoom/matiz/ruído → PTZ digital →        │  │
                              │ movimento (MOG2) → OSD da marca → frame main/sub│  │
                              └───────┬───────────────┬──────────────┬──────────┘  │
                                      │               │              │             │
                                      ▼               ▼              ▼             │
                           FFmpegPublisher      snapshot JPEG     EventBus  ◄──────┼── GUI (PyQt6)
                           (rawvideo→libx264)   (API/GUI/web)   (thread-safe)      │   sinais Qt
                                      │                              │             │
                                      ▼                              ├──► SQLite   │
                       ┌────── MediaMTX (RTSP interno :18554) ──┐    │   (eventos, │
                       │ paths internos: <dev>_chN_sM           │    │    logs,    │
                       │ WebRTC :8889 · HLS :8888 · API :9997   │    │    segmentos)│
                       └───────┬─────────────────┬──────────────┘    │             │
                               │                 │                   ▼             │
                               ▼                 ▼             WebSocket /ws        │
                       RtspRouter (1 porta   FastAPI /live/*  (portal web)          │
                       por equipamento)      (proxy autenticado)                    │
                       :8554 :8555 ...             │                                │
                               │                   │                                │
   VLC / ffprobe / iVMS ◄──────┘                   │                                │
                                                   │                                │
   FastAPI (uvicorn em thread, asyncio) ◄──────────┘                                │
     ├─ ISAPI Hikvision     /ISAPI/...  (Digest)      porta HTTP própria por device │
     ├─ CGI Intelbras       /cgi-bin/*.cgi (Digest)                                 │
     ├─ ONVIF Profile S     /onvif/*  + WS-Discovery UDP 3702                       │
     ├─ API interna         /api/v1/*  (sessão + CSRF)                              │
     └─ Portal web VMS      /  (Jinja2 + Alpine + hls.js/WebRTC)                    │
                                                                                    │
   Recording: FFmpeg -c copy segment 60 s → storage/hdd1/<marca>/chNN/AAAA-MM-DD/   │
              ring buffer 2 s (pré-evento 10 s) · índice SQLite · overwrite por cota│
                                                                                    │
   Túnel (cloudflared / ngrok / tailscale funnel) ──► porta do portal web ──────────┘
```

## 2. Camadas e responsabilidades

| Camada | Módulos | Observação |
|---|---|---|
| Núcleo | `core/` | Sem dependência de Qt nem de FastAPI. Testável em isolamento. |
| Marcas | `brands/` | `BrandDevice` abstrato; Hikvision (ISAPI/PSIA) e Intelbras (CGI). |
| Streaming | `streaming/` | MediaMTX, publishers FFmpeg, `RtspRouter`, playback. |
| Gravação | `recording/` | Contínua, por evento (ring buffer), manual, índice, retenção. |
| API | `api/` | Servidor uvicorn em thread, Digest, ONVIF, API interna, WebSocket. |
| Web | `web/` | Portal VMS servido pelo mesmo FastAPI. |
| GUI | `gui/` | PyQt6. Nenhuma operação pesada na thread da UI. |
| Túnel | `tunnel/` | `TunnelProvider` + Cloudflare/ngrok/Tailscale. |

### Threads

- **Thread principal**: Qt (UI).
- **CaptureService**: 1 thread lendo a webcam.
- **VirtualCamera**: 1 thread por câmera (processamento de frame, OSD, movimento).
- **FFmpegPublisher**: 1 thread escritora por stream + o processo `ffmpeg` filho.
- **uvicorn**: 1 thread dedicada com loop `asyncio` próprio.
- **DeviceManager**: 1 thread "escritor" que grava eventos/logs no SQLite (fora das threads de vídeo).
- **Tarefas pesadas da GUI** (playback, exportação MP4, formatação simulada): `QThreadPool`.

### EventBus (thread-safe)

`core/event_bus.py` recebe `publish()` de qualquer thread e entrega para três tipos de assinante:

1. **callback síncrono** (usado pelo DeviceManager e pela ponte Qt, que apenas emite um sinal);
2. **`queue.Queue`** (threads comuns);
3. **`asyncio.Queue`** vinculada a um loop, entregue por `loop.call_soon_threadsafe`
   (usada pelo `alertStream`, `eventManager attach` e pelo WebSocket).

A GUI converte eventos em sinais Qt (`QtEventBridge`, Etapa 5), garantindo entrega na thread de UI.

## 3. Decisões técnicas

1. **`opencv-python-headless` em vez de `opencv-python`.** O pacote completo embarca plugins Qt
   que conflitam com o PyQt6 (erro `xcb`/`cocoa`). Não precisamos de `cv2.imshow`.
2. **Uma única captura da webcam** compartilhada. As N câmeras derivam do mesmo frame base com
   variações leves (espelho, zoom, matiz, ruído) e OSD próprio. Sem webcam, padrão de teste animado.
3. **Frames imutáveis após publicados.** O `CaptureService` nunca altera um array já publicado;
   consumidores leem sem cópia e **não devem modificar** o frame recebido.
4. **Reboot simulado dentro do laço da câmera**, sem timers soltos: o estado vira `REBOOTING`,
   publishers param, a API responde 503 (ou recusa conexão) e, vencido o prazo (20 a 40 s, configurável),
   o estado volta a `ONLINE` e o uptime zera.
5. **Digest com HA1 por realm.** Senhas nunca são gravadas em texto. O perfil guarda hash PBKDF2
   (portal web) e `HA1 = MD5(user:realm:senha)` por realm do equipamento (Digest, RFC 7616).
   MD5 é exigido pelo protocolo, não é usado para armazenar senha.
6. **Portas.** Padrão 8080/8554 (portas < 1024 exigem privilégio no Linux/macOS e costumam estar
   ocupadas no Windows). Cada equipamento ganha a própria porta HTTP e a própria porta RTSP.
   O `core/ports.py` detecta portas ocupadas e sugere um plano.
7. **Sem build no front-end.** Jinja2 + Tailwind (CDN) + Alpine/HTMX + JS puro.

## 4. Solução de roteamento RTSP por query string

**Problema.** O MediaMTX roteia só pelo *path* do URL RTSP. A Intelbras usa
`/cam/realmonitor?channel=1&subtype=0` (o canal e o stream vão na **query**), e o playback usa
`/cam/playback?channel=1&starttime=...&endtime=...`. O MediaMTX não distingue esses URLs.

**Segunda armadilha.** Reescrever só a URL e repassar o `Authorization` ao MediaMTX **não funciona**
com Digest: o `response` do Digest é um hash que inclui o `uri` da requisição, então qualquer
reescrita invalida a autenticação.

**Solução escolhida: `RtspRouter` (proxy RTSP leve, uma porta por equipamento).**

```
cliente ──RTSP──► RtspRouter (porta do equipamento) ──RTSP──► MediaMTX interno (127.0.0.1:18554)
                  1. termina a autenticação (Digest/Basic) com o usuário do equipamento
                  2. reescreve a URL de CADA requisição (OPTIONS/DESCRIBE/SETUP/PLAY/TEARDOWN)
                     e o Content-Base / Content-Location das respostas
                  3. após autenticar, fala com o MediaMTX sem credenciais (somente loopback)
                  4. depois do PLAY, repassa bytes cegamente (RTP/TCP intercalado `$`)
```

Tabela de reescrita (resolvida por marca/equipamento):

| URL pública | Path interno no MediaMTX |
|---|---|
| Hik `/Streaming/Channels/101`, `/ISAPI/Streaming/Channels/101`, `/h264/ch1/main/av_stream` | `<dev>_ch1_s0` |
| Hik `/Streaming/Channels/102`, `/h264/ch1/sub/av_stream` | `<dev>_ch1_s1` |
| Intelbras `/cam/realmonitor?channel=2&subtype=1` | `<dev>_ch2_s1` |
| Hik `/Streaming/tracks/101?starttime=...&endtime=...` | `<dev>_pb_<token>` (temporário) |
| Intelbras `/cam/playback?channel=1&starttime=...&endtime=...` | `<dev>_pb_<token>` (temporário) |

- Os paths internos são `publish` do FFmpeg; MediaMTX só escuta em loopback (`127.0.0.1:18554`),
  então o proxy é a única porta RTSP exposta.
- **Playback:** o `PlaybackService` resolve o intervalo no índice SQLite, concatena os segmentos MP4
  (FFmpeg concat + `-ss`) e publica em um path temporário que expira ao final do trecho.
- **WebRTC/HLS** continuam direto no MediaMTX, mas o navegador só os alcança pelas rotas
  `/live/...` do FastAPI (proxy autenticado), nunca pela porta crua.
- Detalhe de protocolo incerto: variações de clientes (iVMS, SIM Next) ao enviar `Require`/`Session`
  são tratadas de forma configurável e sinalizadas em comentário no código (Etapa 2).

## 5. Estrutura do repositório

Ver a árvore completa no prompt de origem. Estado por etapa:

| Etapa | Conteúdo | Status |
|---|---|---|
| 0 | Arquitetura | feita |
| 1 | requirements, instaladores, config, `core/` | feita |
| 2 | `streaming/` + `recording/` | pendente |
| 3 | `api/` + `brands/hikvision/` | pendente |
| 4 | `brands/intelbras/` | pendente |
| 5 | `gui/` (tema, splash, wizard) | pendente |
| 6 | `gui/pages` e widgets | pendente |
| 7 | `web/` | pendente |
| 8 | `tunnel/` + segurança | pendente |
| 9 | `tests/` + README completo | pendente |
