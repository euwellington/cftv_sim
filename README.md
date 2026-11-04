# cftv_sim

Simulador de equipamentos CFTV **Hikvision** e **Intelbras** (PyQt6 + portal web VMS) que transforma a
webcam do notebook em câmeras IP e DVR/NVR. **Uso exclusivo em laboratório/estudo.**

> README completo (instalação, testes com curl/VLC, ONVIF, túnel) chega na Etapa 9.

## Status por etapa

| Etapa | Conteúdo | Status |
|---|---|---|
| 0 | Arquitetura (`docs/ARQUITETURA.md`) | feita |
| 1 | requirements, instaladores, config, `core/` | feita |
| 2 | `streaming/` + `recording/` | pendente |
| 3 | `api/` + `brands/hikvision/` | pendente |
| 4 | `brands/intelbras/` | pendente |
| 5 | `gui/` (tema, splash, wizard) | feita |
| 6 | `gui/pages` e widgets | feita |
| 7 | `web/` | pendente |
| 8 | `tunnel/` + segurança | pendente |
| 9 | `tests/` + README completo | pendente |

## Instalar (Etapa 1)

```bash
./install_and_run.sh --no-run          # Linux/macOS
install_and_run.bat --no-run           # Windows
.venv/bin/python -m pytest tests/test_core.py
```

## Logos

O projeto não distribui logos de marcas. Coloque os seus em `assets/logos/` (veja o README da pasta);
sem eles, a interface desenha placeholders.
