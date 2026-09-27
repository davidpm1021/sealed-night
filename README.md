# Prerelease Night

Private prerelease-style sealed events: invite friends, open private kits, save decks, play rounds, and track standings. GitHub branch `mvp-tournament` is the source of truth.

## Windows: one command

Install Python 3.12+ first. From the repository folder run:

```powershell
.\start.cmd
```

This creates the local Python environment, installs missing/changed dependencies, opens the browser, and starts one server at http://localhost:8000. Keep that terminal open. Run the same command next time; existing events and kits are preserved.

```powershell
.\start.cmd -Demo                         # fictional cards; manual results, no XMage
.\start.cmd -WithEngine                   # also build the pinned XMage integration
.\start.cmd -ListenAddress 0.0.0.0         # allow friends on your LAN/private VPN
.\start.cmd -NoBrowser -Port 8080          # optional startup settings
```

XMage setup additionally requires Git, Java 21+ and Maven on PATH. `scripts/setup-xmage.ps1 -InstallDependencies` can request Java/Maven via winget; open a new terminal afterward. The first engine build and first MTGJSON synchronization can take time. Setup stops on failures instead of claiming success. Demo cards cannot be used by the rules engine.

LAN friends use the host PC's LAN hostname/IP and port, not `localhost`. Allow that port through Windows Firewall on your private network when prompted. For friends elsewhere, use a private VPN or an authenticated HTTPS reverse proxy with WebSocket support. This app does not provision internet hosting, accounts, TLS or public-service protection. Keep the Java/bridge ports private. Run exactly one web worker without auto-reload: the in-memory event store and engine ownership are single-process.

## Run a night

1. Host an event with a supported set code, then share its invitation. Generate kits after everyone joins.
2. Each player opens six packs, builds a 40+ card deck and saves it. Packs already opened and unsaved deck edits survive a browser refresh on that browser.
3. Save your **Private recovery code** from the lobby. It restores your authenticated seat in another browser through **Enter recovery code**. Keep it secret; the invite code alone cannot reclaim a seat. Forgetting a seat only removes it from that browser, not the event.
4. The host starts Round 1 once decks are legal. Open your game from pairings. Refresh/reconnect resumes that table without launching another one.
5. Games run one at a time through the engine adapter. Successful concessions count once automatically. For other game endings, both players confirm the same winner/draw; the host can resolve a disagreement. The pinned bridge has a `game_over` flag but no structured winner field, so life totals and chat messages are never used to guess a result.
6. After a recorded game, save sideboard changes from **Deck / sideboard**, then select **Start next game**. Two wins complete the match and update standings. The next table exports the latest saved decks.
7. Manual match reporting is available for tabletop play or recovery. An active engine game must first finish or be marked interrupted by the host. Identical reports are harmless; only the host can correct a completed match before the next round/final standings.
8. The host advances completed rounds, then selects **Finish event**.

## Recovery and limits

- Pools, decks, partial kit-generation progress, rounds and recorded game results persist in `data/events`. Back up that folder privately; files contain player session secrets.
- Browser/network loss does not clear a saved seat. Lobby sockets reconnect; battlefield polling has one loop and pauses during actions. Rejected and stale decisions show an error.
- Private adapter manifests in `data/xmage/sessions` allow a restarted web server to reattach to surviving local player bridges, after checking their unique seat names. Stored terminal outcomes can be recovered even after those bridges exit. Manifests and bridge endpoints never appear in public event data.
- XMage does **not** support restoring an in-progress board after its Java server/bridge dies. Recovery fails visibly rather than silently replacing the game. The host can mark that game interrupted (no score awarded), then start a replacement or report a manual match result. Completed game scores and pools remain intact. Old events with engine IDs but no adapter manifests also use this recovery path.
- Sideboarding is locked while a game is starting/active. There is no joint ready check: both players should save their changes before either launches the next game.
- Actual XMage gameplay remains an integration boundary: automated tests use bridge-contract fixtures and isolated adapters; they do not claim a complete Java-engine match has been played. Card support depends on the pinned engine revision.

## Product handling

MTGJSON SDK 0.1.3 supplies booster collation. Only Play, Draft or default configurations are offered; collector/theme-only configurations fail clearly. Booster foil metadata is retained.

Promos prefer paper, foil, rare/mythic cards marked `prerelease` in the set or its associated `P<SET>` promo set. Back faces and non-playable pieces are excluded, and names are deduplicated. When no marked pool exists, a filtered main-set foil rare/mythic fallback is explicitly labeled as simulated. Candidate searches paginate the whole set.

Every kit displays its product note. Six packs plus one promo is a simulation: physical odds, seeded packs, bonus cards, special promo distributions and other product contents are not claimed as exact. Metadata definitions: [MTGJSON card model](https://mtgjson.com/data-models/card/card-set/) and [sealed products](https://mtgjson.com/data-models/sealed-product/).

## Development and verification

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
npm ci
npx playwright install chromium
npm run test:browser
```

Run browser tests with the Python environment containing the app dependencies on PATH, or set `BROWSER_SERVER_COMMAND` to its uvicorn command. CI runs Python and two-browser flows on Linux and Windows, plus the real Windows launcher smoke test. Adapter tests cover resume identity checks, dead engines, stale/duplicate actions, terminal results and score persistence. Browser tests cover a complete two-player manual tournament, saved-seat failure/retry, and battlefield polling/decision controls.

All XMage wire formats, local manifests and runtime processes stay in `app/engine`. The integration is pinned to mage-bench commit `78e18d68688ff6496f2506e4cae54629b5ba994f`; it is not downloaded or required for ordinary event/demo tests.
