# apex-bot-delay-relay

Mirrors trading-signal alert messages from a set of premium Discord channels into matching free/delayed
channels, ~15 minutes later, verbatim

This repo exists only to get a free, unlimited GitHub Actions runner (public repos aren't billed for
Actions minutes; the private repo this was developed in is). `delay_relay.py` is a synced copy of the
tested module — no unit tests live here, no proprietary trading logic lives here, only the relay/dedup
mechanics and a list of Discord channel IDs.

Runs on a schedule (`.github/workflows/discord-delay-relay.yml`, every 5 minutes) via `workflow_dispatch`
or the `schedule` trigger. Needs a `DISCORD_BOT_TOKEN` repo secret with "View Channel" + "Read Message
History" + "Send Messages" on all listed channels.
