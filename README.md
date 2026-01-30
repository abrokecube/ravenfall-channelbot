# ravenfall-channelbot
Runs under the username `CubedHelperBot` in my Ravenfall towns. It is not very configurable (you will have to edit the code directly). Code written with AI assistance.

Bot is configured in `channels.json` and `.env`. These files must exist.  
Examples of these files are in [`channels_example.json`](/channels_example.json) and [`.env.example`](/.env.example)
If all dependencies are set up, you can run the bot by executing `uv run main.py` in a terminal window, or by running `run.ps1`

## Dependencies
- Ravenfall
    - Bot sends requests to the game's query server to get information
    - Bot will automatically restart the Ravenfall process
- RavenBot
    - Bot will automatically restart the RavenBot process
- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
    - its pip but better
- [Sandboxie-Plus](https://sandboxie-plus.com/)
    - Allows multiple instances of Ravenfall to run. 
    - Instances must already be set up with Ravenfall and RavenBot.
- [Prometheus](https://prometheus.io/)
    - Must be fetching data from [ravenfall-prometheus-exporter](https://github.com/abrokecube/ravenfall-prometheus-exporter)
    - Must be fetching machine metrics from a [node_exporter](https://github.com/prometheus-community/windows_exporter) (see config below)
- [ravenfall-middleman](https://github.com/abrokecube/ravenfall-middleman)
    - Allows for customized Ravenfall messages and hidden gift commands for redeems. (i like my chat clean)
    - Bot hosts a message processor the middleman connects to.
- [ravenfall-multichat-server](https://github.com/abrokecube/ravenfall-multichat-server)
    - Fetches data of my other characters for use with item redeems, shared scrolls, determining game sync status
    - Allows the bot to chat through my other accounts
- [ravenfall-webops](https://github.com/abrokecube/ravenfall-webops) (not required for operation)
    - `restockscrolls` and `countloyaltypoints` commands
- [watchdog](https://github.com/abrokecube/watchdog) (not required for operation)
    - for `startproc`, `stopproc`, `restartproc`, `listproc`, and `pull`

### Node exporter config
The prometheus node_exporter should have these collectors configured:
```
collectors:
  enabled: cpu,logical_disk,memory,net,os,physical_disk,system,process,thermalzone

collector:
  process:
    include: "(Ravenfall).*"
```

### channels.json fields
See [`channels_example.json`](/channels_example.json) for an example file. This is the configuration I currently use.  

| Property                   | Description | 
|----------------------------|-------------|
`channel_id`                 | Twitch channel id of the town
`channel_name`               | Channel login name
`rf_query_url`               | Ravenfall query server url. Determined by `queryEngineApiPrefix` in Ravenfall's `game-settings.json`. By default `queryEngineApiPrefix` should be `localhost:8888/ravenfall/`, so `rf_query_url` should be set to `http://127.0.0.1:8888/ravenfall`
`custom_town_msg`            | Text appended to town info in the `towns` command
`ravenbot_prefix`            | Prefix used by Ravenbot
`welcome_message`            | Message sent when a first time chatter sends a `join` command
`sandboxie_box`              | Sandboxie box name that contains Ravenfall and Ravenbot
`ravenfall_start_script`     | Command to run Ravenfall. In the example config its used to limit Ravenfall to certain CPU cores.
`auto_restart`               | Bot will auto restart the game periodically. Period is configured in `restart_period`
`restart_period`             | If `auto_restart` is enabled, this determines how long the game should be alive before it needs a restart. Accepts a value in seconds.
`event_notifications`        | Sends dungeon/raid information when a dungeon/raid starts. (ex. "`DUNGEON – Boss HP: 175,662 – Enemies: 49 – Players: 13`")
`middleman_connection_id`    | ID used to identify message sources from the middleman. In the format `<client ip>_<client port>_<server port>`
`ravenfall_loc_strings_path` | Path to a `.yaml` containing translation strings. See [definitions.yaml](data/definitions.yaml) for a list of keys, and [example_translation_strings.yaml](data/example_translation_strings.yaml) for example translations.
`auto_restore_raids`         | Do not use this. Sends `!auto raid on` on behalf of users in the town that have already enabled auto raids. Was used when auto raid status was broken and didn't get restored on game restart.
`channel_points_redeems`     | Listen for channel point redeems. Setting this to `false` disables redeems.
`pause_monitoring`           | Stops Ravenfall monitoring
