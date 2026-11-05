# Gerenciamento de câmeras — contrato dos simuladores

O worker-monitoring (`IpCameraManagementService`) gerencia câmeras Hikvision e Intelbras pelos mesmos
endpoints que um equipamento real expõe. Este documento lista o que o simulador atende para isso.
Todos os endpoints exigem autenticação **Digest** (usuário `admin`/`operator` para as operações de controle).

## Hikvision (ISAPI, XML)

| Ação do worker | Requisição | Observações |
|---|---|---|
| Saúde do HD | `GET /ISAPI/ContentMgmt/Storage/hdd` | `hddList/hdd` com `status` (`ok`/`error`), `capacity` e `freeSpace` em **MB**. Sem `hdd` = "HD não encontrado". |
| Está gravando? | `POST /ISAPI/ContentMgmt/search` | Corpo `CMSearchDescription` com `trackID` 101, `startTime`/`endTime` (UTC, `...Z`), `maxResults`, `searchResultPosition`. Só aparecem segmentos **fechados**; o worker usa o maior `endTime` e considera "gravando" se for mais novo que `IP_CAMERA_RECORDING_STALE_SECONDS` (180 s). |
| Reiniciar | `PUT /ISAPI/System/reboot` | Já existia. |
| Gravação manual | `PUT /ISAPI/ContentMgmt/record/control/manual/start\|stop/tracks/{id}` | **Novo.** Canal = `id // 100` (101 = canal 1). Usa `AppContext.set_recording`. 400 para canal/ação inválidos. |

## Intelbras / Dahua (CGI, `chave=valor`)

| Ação do worker | Requisição | Observações |
|---|---|---|
| Saúde do HD | `GET /cgi-bin/storageDevice.cgi?action=getDeviceAllInfo` | `list.info[i].State` (`Success`/`Error`), `Detail[0].TotalBytes`, `Detail[0].UsedBytes`. |
| Relógio do equipamento | `GET /cgi-bin/magicBox.cgi?action=getCurrentTime` | Hora **local** do equipamento; o worker compara o `EndTime` dos arquivos com ela. |
| Está gravando? | `mediaFileFind.cgi`: `factory.create` → `findFile` (`condition.Channel`, `condition.StartTime/EndTime` em `yyyy-MM-dd HH:mm:ss`) → `findNextFile&count=200` → `close` → `destroy` | Lê `items[i].EndTime`. |
| Reiniciar | `GET /cgi-bin/magicBox.cgi?action=reboot` | Já existia. |
| Gravação manual | `GET /cgi-bin/configManager.cgi?action=setConfig&RecordMode[i].Mode=1\|2` | **Novo.** `i` = canal − 1. `1` liga a gravação manual; `0`/`2` desliga. `getConfig&name=RecordMode` devolve `table.RecordMode[i].Mode` (1 = manual gravando). |

## Como testar de ponta a ponta

1. Suba o simulador com os dois fabricantes e anote as portas HTTP.
2. No Atlas, cadastre uma câmera de cada marca (IP `127.0.0.1` só funciona se o worker rodar na mesma máquina;
   o worker bloqueia loopback por segurança — use o IP da rede local) com o usuário/senha do simulador.
3. Em `api/ip-camera-management/{id}/health/check` confira HD e "gravando".
4. Dispare `device-recording/start` e veja novos segmentos em `storage/hdd1/<marca>/chNN/AAAA-MM-DD/`.

Testes automatizados: `tests/test_web_api.py::test_hikvision_management` e `::test_intelbras_management`.
