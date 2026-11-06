# Moving pc3-server to the newest middleman

Target: `ravenfall-middleman` at `ea300e8`, which adds the client connection fixes on top of `7e5d659`. The bot needs its `main` with the middleman changes merged.

The bot works with both the old and new middleman, so it goes first and each step can be undone by itself. Both were run locally against real builds: the pre-March build (`9b077db~1`) and the fixed build. pc3-server's real config has not been seen, so check the values below against it.

## Before you start

1. Save copies of the old `middleman.exe` and its `config.json`.
2. Save the output of `curl http://pc3-server:7101/api/config`, and note each town's `middleman_connection_id` from the bot's `channels.json`.
3. Build the new exe with `go build` from `ea300e8`. Don't reuse `middleman/ravenfall-middleman.exe` from the repo: it is dated before that commit and isn't tracked.

## New config.json

Start from the old file. A wrong key name here gives no error: Go ignores unknown keys, so each mistake below just looks like a feature that stopped working.

| Old | New | If it's missed |
| --- | --- | --- |
| `proxy_mappings` | `proxyMappings` | No ports open, so RavenBot can't connect |
| `identifier_timeouts` | `identifierTimeouts` | Every identifier gets the default timeout |
| `messageProcessor.url` | `messageProcessor.urls` (a list) | Processor never connects: messages pass through unmodified, so nothing is hidden or translated |
| Not present | `connectionId` on every mapping | Mappings overwrite each other, so only one town is proxied |
| Not present | `clientHost` on every mapping | Old build listened on `localhost` only. Leaving it out listens on all interfaces |
| Not present | `maxWriteRetries: 3` | Defaults to 1 attempt, so the first message after a game restart can be lost |

- Set each `connectionId` to that town's current `middleman_connection_id`. Those IDs used to be generated as `<serverHost>_<clientPort>_<serverPort>`, for example `127.0.0.1_8040_4040`. If you keep them, `channels.json` doesn't change.
- Use `127.0.0.1` for `clientHost`. Use `0.0.0.0` only if a RavenBot connects from another machine.
- Keep `disableTimeout`, `apiPort` and the ports as they are. Any path in the processor URL works, because the bot ignores it, so use the same host and port as before.

```json
"proxyMappings": [
    {
        "connectionId": "127.0.0.1_8040_4040",
        "clientHost": "127.0.0.1",
        "clientPort": 8040,
        "serverHost": "127.0.0.1",
        "serverPort": 4040
    }
],
"messageProcessor": { "enabled": true, "urls": ["ws://<bot host>:<RF_MIDDLEMAN_PROCESSOR_PORT>/process"] },
"maxWriteRetries": 3
```

## Deploy order

1. **Merge and deploy the bot, then restart it.** It still runs on the old middleman. Check that messages still get translated and blocked.
2. **Pick a quiet moment.** RavenBot messages stop while the middleman is down.
3. **Stop the old middleman**, then swap in the new exe and `config.json`.
4. **Start the new middleman.** It should log `Proxy listening on port ...` once per town and `Successfully connected to message processor`.
5. **Check that each RavenBot reconnected:** look for `[<id>] Client ... connected to port ...`. Restart any that didn't.
6. **Run the checks below.**

To roll back, restore the old exe and config. The bot doesn't need to change.

## Checks

```bash
curl http://pc3-server:7101/api/config
```

```bash
curl "http://pc3-server:7101/api/connection-status?connectionId=127.0.0.1_8040_4040"
```

- `/api/config` shows every town under `proxyMappings`, and `urls` is filled in.
- The status call returns `success: true` for every town. A 404 means the ID doesn't match the mapping.
- In chat, `!inspect` answers, and a long message is translated or split.
- `Unknown connection id` in the bot's log means the IDs don't match.
- `Timeout waiting for response from processor` in the middleman log means replies aren't being matched. That's the symptom `b7d6d32` fixes.
- Restart one game and confirm RavenBot commands work afterwards (see below).

## Changes in behaviour

- **RavenBot stays connected when the game goes down.** The old build dropped RavenBot and it reconnected. The new one keeps it and reconnects to the game on the next write. Not tested with a real game and RavenBot, hence the restart check.
- **`clientConnected` is always `true` for a known ID.** Nothing in the bot reads it.
- **Calls made with no RavenBot connected.** The old build answered 404 to all of them. Now `send-and-wait-response` answers 503 `No client connected` without contacting the game, which the bot reads the same way as the old 404. `send-to-server`, `ensure-connected` and `reconnect` do contact the game.
- **A connecting RavenBot closes any game connection the API opened while it was away.** The game briefly sees a disconnect, and RavenBot's next message reconnects it.
- **`send_to_client` reports success with no RavenBot connected**, and the message is dropped.

## Middleman notes

- With no RavenBot connected and `disableTimeout: true`, or when the game never replies, a game connection opened through the API stays open until RavenBot connects, because nothing sets an expiry for it.
- `forwardServerToClient` reads `pc.serverConn` without the lock, so a disconnect at the wrong moment can hit a nil pointer outside `net/http`'s recovery and stop the whole process. It's already in `7e5d659`, comes from reading the code only, and isn't fixed.
- `API.md` shows `responses` as strings, but they are JSON objects. The bot already reads them as objects.

The rewrite's client on `modularize-compartment` posts to `/api/send-to-server-and-wait`, which doesn't exist in the Go server. The correct path is `/api/send-and-wait-response`, and its `MessageProcessorConfig` still has the old `url` key.
